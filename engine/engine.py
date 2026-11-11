"""
Nifty 500 swing engine — deterministic rules only.

Every status, level and ranking decision in a report comes from this file.
The LLM step (Claude's daily run) may only add narrative, news context and
red-flag VETOES (downgrade actionable -> watchlist). It never sets a level,
never upgrades a status, never adds a symbol.

Inputs are plain pandas DataFrames indexed by date with columns
open, high, low, close, volume, and optionally deliv_pct.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd

ENGINE_VERSION = "1.1.1"

# ---------------------------------------------------------------- parameters
P = dict(
    min_bars=210,               # need 200 DMA + slope
    liq_high_cr=10.0,           # avg 20d traded value, Rs crore
    liq_ok_cr=3.0,
    rvol_entry=1.3,             # renewed-buying volume on entry day
    rvol_breakout=1.5,
    close_near_high=0.70,       # close in top 30% of the day's range
    max_ext_atr=3.0,            # (close - SMA20) / ATR above this = extended
    min_rr_t1=1.5,
    t1_r=1.5, t2_r=3.0,         # targets as multiples of risk
    stop_buffer_atr=0.25,
    entry_buffer=0.001,         # trigger = high * (1 + 0.1%)
    min_quality=65,
    max_actionable=5,
    corp_action_gap=0.40,       # |overnight move| above this = suspect split/bonus
)

PRIORITY = {"A": 1, "B": 2, "D": 3, "C": 3, "E": 4}
STRATEGY_NAMES = {
    "A": "Breakout & retest",
    "B": "Shallow pullback",
    "C": "Tight consolidation",
    "D": "VCP",
    "E": "Momentum breakout",
}


# ---------------------------------------------------------------- indicators
def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def add_indicators(df: pd.DataFrame, idx_close: Optional[pd.Series] = None) -> pd.DataFrame:
    d = df.copy()
    c = d["close"]
    for n in (5, 10, 20, 50, 100, 150, 200):
        d[f"sma{n}"] = c.rolling(n).mean()
    delta = c.diff()
    up, dn = delta.clip(lower=0), -delta.clip(upper=0)
    rs = wilder(up, 14) / wilder(dn, 14)
    d["rsi14"] = 100 - 100 / (1 + rs)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["macd_sig"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_sig"]
    tr = pd.concat([d["high"] - d["low"],
                    (d["high"] - c.shift()).abs(),
                    (d["low"] - c.shift()).abs()], axis=1).max(axis=1)
    d["atr14"] = wilder(tr, 14)
    d["atr10"] = tr.rolling(10).mean()
    d["atr50"] = tr.rolling(50).mean()
    d["vol20"] = d["volume"].rolling(20).mean()
    d["vol50"] = d["volume"].rolling(50).mean()
    d["rvol"] = d["volume"] / d["vol20"].shift()
    d["value_cr"] = c * d["volume"] / 1e7
    d["avg_value20_cr"] = d["value_cr"].rolling(20).mean()
    d["hi52"] = d["high"].rolling(250, min_periods=120).max()
    d["lo52"] = d["low"].rolling(250, min_periods=120).min()
    d["chg_pct"] = c.pct_change() * 100
    d["ext_atr"] = (c - d["sma20"]) / d["atr14"]
    if idx_close is not None:
        ic = idx_close.reindex(d.index).ffill()
        # 63-session (≈3 month) relative strength vs Nifty 500; >1 = outperforming
        d["rs63"] = (c / c.shift(63)) / (ic / ic.shift(63))
        d["rs20"] = (c / c.shift(20)) / (ic / ic.shift(20))
    else:
        d["rs63"] = np.nan
        d["rs20"] = np.nan
    return d


def swing_points(d: pd.DataFrame, k: int = 5):
    """Confirmed swing highs/lows (a bar is a swing high if it is the max of k bars each side)."""
    h, l = d["high"].values, d["low"].values
    highs, lows = [], []
    for i in range(k, len(d) - k):
        if h[i] == h[i - k:i + k + 1].max():
            highs.append(i)
        if l[i] == l[i - k:i + k + 1].min():
            lows.append(i)
    return highs, lows


def liquidity_class(avg_value_cr: float) -> str:
    if not np.isfinite(avg_value_cr):
        return "Unknown"
    if avg_value_cr >= P["liq_high_cr"]:
        return "High"
    if avg_value_cr >= P["liq_ok_cr"]:
        return "Adequate"
    return "Thin"


ACTION_RATIOS = [0.5, 1 / 3, 2 / 3, 0.25, 0.75, 0.2, 0.1, 0.4, 0.8, 0.6, 1 / 1.5]


def adjust_corporate_actions(df: pd.DataFrame):
    """Back-adjust unadjusted bhavcopy prices for splits/bonuses.

    Large opening gaps are snapped to a standard split/bonus ratio (both the open and the close must
    sit near the same ratio, so a genuine crash is not mistaken for a split). NSE's PREV_CLOSE field is
    NOT adjusted on ex-dates (checked: KOTAKBANK 1:5 split, 14 Jan 2026), so it is not used.
    Gaps beyond the threshold that match no ratio are returned as unresolved, and the stock is kept
    out of setups. Returns (adjusted_df, events, unresolved)."""
    d = df.copy()
    events, unresolved = [], False
    prior_close = d["close"].shift()
    gap = d["open"] / prior_close
    for t, g in gap[(gap < 0.78)].dropna().items():
        snap = min(ACTION_RATIOS, key=lambda r: abs(g / r - 1))
        c = d.at[t, "close"] / prior_close[t]
        if abs(g / snap - 1) <= 0.04 and abs(c / snap - 1) <= 0.12:
            events.append((t, snap, "gap matched split/bonus ratio"))
        elif g < 1 - P["corp_action_gap"]:
            unresolved = True
    for t, fac, _ in events:
        before = d.index < t
        for col in ("open", "high", "low", "close"):
            d.loc[before, col] = d.loc[before, col] * fac
        d.loc[before, "volume"] = d.loc[before, "volume"] / fac
    ev = [dict(date=str(t.date()), factor=round(f, 4), method=m) for t, f, m in events]
    return d, ev, unresolved


def suspect_corporate_action(d: pd.DataFrame, lookback: int = 260) -> bool:
    x = d.tail(lookback)
    gap = (x["open"] / x["close"].shift() - 1).abs()
    return bool((gap > P["corp_action_gap"]).any())


# ---------------------------------------------------------------- candle reads
def candle(d: pd.DataFrame, i: int = -1) -> dict:
    r = d.iloc[i]
    rng = max(r.high - r.low, 1e-9)
    body = abs(r.close - r.open)
    lower = min(r.open, r.close) - r.low
    return dict(
        bull=r.close > r.open,
        close_pos=(r.close - r.low) / rng,
        lower_wick=lower / rng,
        body=body / rng,
    )


def is_reversal(d: pd.DataFrame, i: int = -1) -> Optional[str]:
    c, p = candle(d, i), d.iloc[i - 1]
    r = d.iloc[i]
    if c["lower_wick"] >= 0.5 and c["close_pos"] >= 0.6:
        return "hammer / lower-wick rejection"
    if c["bull"] and r.close >= p.open and r.open <= p.close and p.close < p.open:
        return "bullish engulfing"
    return None


def renewed_buying(d: pd.DataFrame) -> bool:
    r, p = d.iloc[-1], d.iloc[-2]
    c = candle(d)
    return bool(c["bull"] and r.close > p.high and c["close_pos"] >= P["close_near_high"]
                and r.rvol >= P["rvol_entry"])


# ---------------------------------------------------------------- setups
@dataclass
class Setup:
    symbol: str
    strategy: str
    status: str                 # see STATUS_* below
    actionable_candidate: bool  # rules say entry is confirmed today
    evidence: list = field(default_factory=list)
    support: Optional[float] = None
    resistance: Optional[float] = None
    breakout_level: Optional[float] = None
    pullback_low: Optional[float] = None
    trigger: Optional[float] = None
    stop: Optional[float] = None
    need: str = ""
    invalidation: str = ""
    clarity: float = 0.5        # 0..1, used in quality score

    def to_dict(self):
        return asdict(self)


def _trigger_from_today(d):
    return round(float(d.iloc[-1].high) * (1 + P["entry_buffer"]), 2)


def strat_a_breakout_retest(sym, d) -> Optional[Setup]:
    """Breakout above a 20+ bar base, then a retest of the breakout zone."""
    n = len(d)
    atr = float(d.atr14.iloc[-1])
    for b in range(n - 3, n - 26, -1):           # breakout bar 3..25 sessions ago
        base = d.iloc[b - 60:b]
        if len(base) < 40:
            continue
        R = float(base.high.max())
        bars_below = (base.close < R).sum()
        depth = 1 - base.low.min() / R
        br = d.iloc[b]
        if not (bars_below >= 20 and depth <= 0.30):
            continue
        if not (br.close > R * 1.005 and br.rvol >= P["rvol_breakout"] and candle(d, b)["close_pos"] >= 0.6):
            continue
        # found a qualifying breakout bar b
        after = d.iloc[b + 1:]
        post_high = float(d.iloc[b:].high.max())
        zone_lo, zone_hi = R - 0.5 * atr, R + 0.5 * atr
        pb_low = float(after.low.min()) if len(after) else float(br.low)
        last = d.iloc[-1]
        ev = [f"Breakout {d.index[b].date()} above {R:.2f} on {br.rvol:.1f}x volume",
              f"Base: {int(bars_below)} sessions below resistance, depth {depth*100:.0f}%"]
        common = dict(symbol=sym, strategy="A", support=round(R, 2), resistance=round(post_high, 2),
                      breakout_level=round(R, 2), pullback_low=round(pb_low, 2))
        if (after.close < R - atr).any():
            return Setup(**common, status="Retest failure", actionable_candidate=False,
                         evidence=ev + ["Closed more than 1 ATR below the breakout level"],
                         invalidation="Already invalidated", clarity=0.2)
        touched = pb_low <= zone_hi
        if not touched:
            return Setup(**common, status="Breakout awaiting retest", actionable_candidate=False,
                         evidence=ev, need=f"Pull back toward {zone_lo:.2f}–{zone_hi:.2f} on lighter volume",
                         invalidation=f"Close below {R - atr:.2f}", clarity=0.5)
        pb = after[after.index <= after.low.idxmin()]
        pb_vol_ok = pb.volume.mean() < float(br.volume) and pb.volume.mean() <= float(d.vol20.iloc[b])
        if pb_vol_ok:
            ev.append("Pullback volume below breakout-day and 20-day average")
        stop = round(min(pb_low, R) - P["stop_buffer_atr"] * atr, 2)
        if renewed_buying(d) and last.close > R:
            return Setup(**common, status="Confirmed continuation entry", actionable_candidate=True,
                         evidence=ev + ([] if pb_vol_ok else ["Pullback volume was NOT lighter than average (weaker)"])
                         + [f"Renewed buying today: close above prior high, {last.rvol:.1f}x volume"],
                         trigger=_trigger_from_today(d), stop=stop,
                         invalidation=f"Daily close below {stop:.2f}", clarity=0.9 if pb_vol_ok else 0.6)
        rev = is_reversal(d)
        if rev and last.close > R - 0.25 * atr:
            return Setup(**common, status="Successful retest", actionable_candidate=False,
                         evidence=ev + [f"Reversal candle at support: {rev}"],
                         trigger=_trigger_from_today(d), stop=stop,
                         need=f"Break above {float(last.high):.2f} with volume ≥ {P['rvol_entry']}x",
                         invalidation=f"Close below {stop:.2f}", clarity=0.75)
        return Setup(**common, status="Retest in progress", actionable_candidate=False, evidence=ev,
                     stop=stop, need="Reversal candle in the breakout zone, then a break of its high",
                     invalidation=f"Close below {R - atr:.2f}", clarity=0.55)
    return None


def _uptrend(d) -> bool:
    r = d.iloc[-1]
    return bool(r.close > r.sma50 > r.sma200 and r.sma20 > d.sma20.iloc[-6] and r.sma50 > d.sma50.iloc[-11])


def _hh_hl(d) -> bool:
    hs, ls = swing_points(d.tail(120))
    if len(hs) < 2 or len(ls) < 2:
        return False
    x = d.tail(120)
    return bool(x.high.iloc[hs[-1]] > x.high.iloc[hs[-2]] and x.low.iloc[ls[-1]] > x.low.iloc[ls[-2]])


def strat_b_shallow_pullback(sym, d) -> Optional[Setup]:
    r = d.iloc[-1]
    if not (_uptrend(d) and _hh_hl(d) and (r.rs63 or 0) > 1.0):
        return None
    hi20_pos = int(np.argmax(d.high.values[-20:]))
    bars_since = 19 - hi20_pos
    hi20 = float(d.high.iloc[-20:].max())
    pb = d.iloc[-(bars_since):] if bars_since > 0 else d.iloc[0:0]
    if not (2 <= bars_since <= 10):
        return None
    pb_low = float(pb.low.min())
    depth = 1 - pb_low / hi20
    atr = float(r.atr14)
    if not (0.025 <= depth <= 0.10):
        return None
    near_ma = min(abs(pb_low - float(r.sma10)), abs(pb_low - float(r.sma20))) <= atr
    if not near_ma or r.close < r.sma50:
        return None
    vol_ok = pb.volume.mean() < float(r.vol20)
    ev = [f"Uptrend: close > 50 DMA > 200 DMA, both rising; higher highs & higher lows",
          f"Pullback {depth*100:.1f}% over {bars_since} sessions into the 10/20 DMA",
          f"RS vs Nifty 500 (63d): {r.rs63:.2f}"]
    if vol_ok:
        ev.append("Pullback volume below 20-day average")
    stop = round(pb_low - P["stop_buffer_atr"] * atr, 2)
    common = dict(symbol=sym, strategy="B", support=round(pb_low, 2), resistance=round(hi20, 2),
                  pullback_low=round(pb_low, 2), stop=stop)
    if renewed_buying(d):
        return Setup(**common, status="Confirmed continuation entry", actionable_candidate=True,
                     evidence=ev + ([] if vol_ok else ["Pullback volume was NOT lighter than average (weaker)"])
                     + [f"Bullish close above prior high on {r.rvol:.1f}x volume"],
                     trigger=_trigger_from_today(d), invalidation=f"Close below {stop:.2f}",
                     clarity=0.85 if vol_ok else 0.6)
    return Setup(**common, status="Pullback in progress", actionable_candidate=False, evidence=ev,
                 trigger=_trigger_from_today(d),
                 need="Bullish close above the prior day's high with volume",
                 invalidation=f"Close below {stop:.2f} or below 50 DMA", clarity=0.6)


def strat_c_tight_consolidation(sym, d) -> Optional[Setup]:
    r = d.iloc[-1]
    if not _uptrend(d):
        return None
    box = d.iloc[-16:-1]                       # 15 sessions before today
    top, bot = float(box.high.max()), float(box.low.min())
    width = top / bot - 1
    contraction = float(r.atr10 / r.atr50) if r.atr50 else 9
    if not (width <= 0.08 and contraction < 0.8 and r.close >= 0.75 * r.hi52):
        return None
    ev = [f"15-session range {width*100:.1f}% wide ({bot:.2f}–{top:.2f})",
          f"ATR(10)/ATR(50) = {contraction:.2f} (volatility contracting)"]
    stop = round(bot - P["stop_buffer_atr"] * float(r.atr14), 2)
    common = dict(symbol=sym, strategy="C", support=round(bot, 2), resistance=round(top, 2),
                  breakout_level=round(top, 2), stop=stop)
    if r.close > top and r.rvol >= P["rvol_breakout"] and candle(d)["close_pos"] >= P["close_near_high"]:
        return Setup(**common, status="Confirmed continuation entry", actionable_candidate=True,
                     evidence=ev + [f"Closed above range on {r.rvol:.1f}x volume"],
                     trigger=_trigger_from_today(d), invalidation=f"Close back inside range below {(top+bot)/2:.2f}",
                     clarity=0.75)
    return Setup(**common, status="Watching", actionable_candidate=False, evidence=ev,
                 trigger=round(top * (1 + P["entry_buffer"]), 2),
                 need=f"Close above {top:.2f} on ≥ {P['rvol_breakout']}x volume",
                 invalidation=f"Close below {bot:.2f}", clarity=0.6)


def strat_d_vcp(sym, d) -> Optional[Setup]:
    r = d.iloc[-1]
    tt = (r.close > r.sma150 > r.sma200 and r.sma200 > d.sma200.iloc[-22] and r.close >= 1.25 * r.lo52
          and r.close >= 0.75 * r.hi52 and (r.rs63 or 0) > 1.0)
    if not tt:
        return None
    x = d.tail(90)
    hs, ls = swing_points(x, k=3)
    contractions = []
    for h in hs:
        nxt = [l for l in ls if l > h]
        if not nxt:
            continue
        l = nxt[0]
        contractions.append((h, l, 1 - x.low.iloc[l] / x.high.iloc[h]))
    if len(contractions) < 2:
        return None
    depths = [c[2] for c in contractions[-3:]]
    shrinking = all(depths[i + 1] <= depths[i] * 0.8 for i in range(len(depths) - 1))
    last_h, last_l, last_d = contractions[-1]
    vol_dry = x.volume.iloc[last_h:last_l + 1].mean() < float(r.vol50)
    if not (shrinking and last_d <= 0.10 and vol_dry):
        return None
    pivot = float(x.high.iloc[last_h])
    ev = ["Trend template: price > 150 > 200 DMA, 200 DMA rising, within 25% of 52w high",
          "Contractions: " + " → ".join(f"{v*100:.0f}%" for v in depths),
          "Volume dried up in the final contraction"]
    stop = round(float(x.low.iloc[last_l]) - P["stop_buffer_atr"] * float(r.atr14), 2)
    common = dict(symbol=sym, strategy="D", support=round(float(x.low.iloc[last_l]), 2),
                  resistance=round(pivot, 2), breakout_level=round(pivot, 2), stop=stop)
    if r.close > pivot and r.rvol >= P["rvol_breakout"]:
        return Setup(**common, status="Confirmed continuation entry", actionable_candidate=True,
                     evidence=ev + [f"Pivot {pivot:.2f} cleared on {r.rvol:.1f}x volume"],
                     trigger=_trigger_from_today(d), invalidation=f"Close below {stop:.2f}", clarity=0.8)
    status = "Approaching entry" if r.close >= pivot * 0.97 else "Watching"
    return Setup(**common, status=status, actionable_candidate=False, evidence=ev,
                 trigger=round(pivot * (1 + P["entry_buffer"]), 2),
                 need=f"Close above pivot {pivot:.2f} on ≥ {P['rvol_breakout']}x volume",
                 invalidation=f"Close below {stop:.2f}", clarity=0.65)


def strat_e_momentum(sym, d) -> Optional[Setup]:
    r = d.iloc[-1]
    hi60 = float(d.high.iloc[-61:-1].max())
    if not (r.close > hi60 and r.close > r.sma50 and r.rvol >= P["rvol_breakout"]
            and candle(d)["close_pos"] >= 0.75 and (r.rs63 or 0) > 1.0):
        return None
    ev = [f"Close above 60-session high {hi60:.2f}", f"Volume {r.rvol:.1f}x 20-day average"]
    if r.ext_atr > P["max_ext_atr"]:
        return Setup(symbol=sym, strategy="E", status="Rejected — extended", actionable_candidate=False,
                     evidence=ev + [f"{r.ext_atr:.1f} ATR above 20 DMA (limit {P['max_ext_atr']})"],
                     breakout_level=round(hi60, 2), clarity=0.3)
    stop = round(min(hi60, float(r.low)) - 0.5 * float(r.atr14), 2)
    return Setup(symbol=sym, strategy="E", status="Confirmed continuation entry", actionable_candidate=True,
                 evidence=ev, support=round(hi60, 2), breakout_level=round(hi60, 2),
                 trigger=_trigger_from_today(d), stop=stop, invalidation=f"Close back below {hi60:.2f}",
                 clarity=0.6)


STRATEGIES = [strat_a_breakout_retest, strat_b_shallow_pullback, strat_d_vcp,
              strat_c_tight_consolidation, strat_e_momentum]


# ---------------------------------------------------------------- trade plan + quality
def trade_plan(s: Setup, d: pd.DataFrame) -> Optional[dict]:
    if s.trigger is None or s.stop is None:
        return None
    entry = s.trigger
    risk = round(entry - s.stop, 2)
    if risk <= 0:
        return None
    atr = float(d.atr14.iloc[-1])
    t1 = round(entry + P["t1_r"] * risk, 2)
    t2 = round(entry + P["t2_r"] * risk, 2)
    # nearest overhead supply: swing highs above entry within last 250 sessions
    hs, _ = swing_points(d.tail(250))
    x = d.tail(250)
    overhead = sorted(float(x.high.iloc[i]) for i in hs if x.high.iloc[i] > entry * 1.002)
    blocker = overhead[0] if overhead else None
    rr1 = round((t1 - entry) / risk, 2)
    rr_to_blocker = round((blocker - entry) / risk, 2) if blocker else None
    return dict(
        entry_zone=[entry, round(entry + 0.5 * atr, 2)],
        trigger=entry,
        stop=s.stop,
        t1=t1, t2=t2,
        risk=risk, risk_pct=round(risk / entry * 100, 2),
        reward1=round(t1 - entry, 2), reward2=round(t2 - entry, 2),
        rr1=rr1, rr2=round((t2 - entry) / risk, 2),
        overhead_resistance=round(blocker, 2) if blocker else None,
        rr_to_overhead=rr_to_blocker,
        max_chase=round(entry + 0.5 * atr, 2),
        hold="2–10 sessions",
        invalidation=s.invalidation,
        gap_rule=f"Skip if next session opens above {round(entry + 0.5 * atr, 2)} (R:R degrades)",
    )


REGIME_FIT = {  # which strategies tolerate which regime
    "Strong bullish": {"A", "B", "C", "D", "E"},
    "Bullish": {"A", "B", "C", "D", "E"},
    "Neutral": {"A", "B", "C", "D"},
    "Bearish": {"A", "B"},
    "Strong bearish": set(),
}


def quality(s: Setup, d: pd.DataFrame, plan: Optional[dict], regime: str, liq: str, data_ok: bool) -> dict:
    r = d.iloc[-1]
    comp = {}
    comp["Trend quality"] = 1.0 if _uptrend(d) else (0.5 if r.close > r.sma50 else 0.1)
    comp["Setup clarity"] = s.clarity
    rs = r.rs63 if np.isfinite(r.rs63) else 1.0
    comp["Relative strength"] = float(np.clip((rs - 0.9) / 0.4, 0, 1))
    comp["Volume confirmation"] = float(np.clip((r.rvol - 0.8) / 1.2, 0, 1)) if np.isfinite(r.rvol) else 0
    if plan:
        eff = plan["rr_to_overhead"] if plan["rr_to_overhead"] is not None else plan["rr2"]
        comp["Risk/reward"] = float(np.clip((min(eff, plan["rr2"]) - 1) / 2, 0, 1))
    else:
        comp["Risk/reward"] = 0.0
    comp["Regime alignment"] = 1.0 if s.strategy in REGIME_FIT.get(regime, set()) else 0.0
    comp["Liquidity"] = {"High": 1.0, "Adequate": 0.7}.get(liq, 0.0)
    comp["Data completeness"] = 1.0 if data_ok else 0.3
    w = {"Trend quality": 15, "Setup clarity": 20, "Relative strength": 15, "Volume confirmation": 15,
         "Risk/reward": 15, "Regime alignment": 10, "Liquidity": 5, "Data completeness": 5}
    score = round(sum(comp[k] * w[k] for k in w))
    ordered = sorted(comp.items(), key=lambda kv: kv[1])
    grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
    return dict(score=score, grade=grade,
                components=[dict(name=k, value=round(v, 2), weight=w[k]) for k, v in comp.items()],
                weakest=[k for k, _ in ordered[:2]], strongest=[k for k, _ in ordered[-2:]],
                note="Rule-based score, not a calibrated probability. Fundamentals/catalysts are "
                     "reviewed by the research step and can only veto, never raise, a setup.")


# ---------------------------------------------------------------- market level
def classify_regime(idx: pd.DataFrame, breadth: dict, vix: Optional[float], vix_chg: Optional[float],
                    sector_pos_share: Optional[float], fii_5d_cr: Optional[float]) -> dict:
    """idx = Nifty 500 OHLCV with indicators. Returns label, score, per-factor reads."""
    r = idx.iloc[-1]
    f = []

    def add(name, val, pts, read):
        f.append(dict(name=name, value=val, points=pts, read=read))

    trend_pts = (1 if r.close > r.sma50 else -1) + (1 if r.close > r.sma200 else -1) + \
                (1 if r.sma50 > idx.sma50.iloc[-11] else -1)
    add("Index trend", f"Nifty 500 {'above' if r.close > r.sma50 else 'below'} 50 DMA, "
        f"{'above' if r.close > r.sma200 else 'below'} 200 DMA", trend_pts,
        "50 DMA rising" if r.sma50 > idx.sma50.iloc[-11] else "50 DMA falling")
    a50 = breadth.get("above50")
    if a50 is not None:
        pts = 2 if a50 >= 65 else 1 if a50 >= 50 else -1 if a50 >= 35 else -2
        add("Breadth", f"{a50:.0f}% of Nifty 500 above 50 DMA", pts, "broad" if pts > 0 else "narrow")
    adr = breadth.get("ad_ratio_10d")
    if adr is not None:
        pts = 1 if adr >= 1.2 else -1 if adr <= 0.8 else 0
        add("Advance/decline (10d avg)", f"{adr:.2f}", pts, "")
    if vix is not None:
        pts = 1 if vix < 13 else 0 if vix < 17 else -1 if vix < 22 else -2
        if vix_chg is not None and vix_chg > 10:
            pts -= 1
        add("India VIX", f"{vix:.2f}" + (f" ({vix_chg:+.1f}%)" if vix_chg is not None else ""), pts, "")
    if sector_pos_share is not None:
        pts = 1 if sector_pos_share >= 0.6 else -1 if sector_pos_share <= 0.35 else 0
        add("Sector participation", f"{sector_pos_share*100:.0f}% of sectors up on the month with most members above 50 DMA", pts, "")
    if r.get("rvol") is not None and np.isfinite(r.rvol):
        up_day = r.chg_pct > 0
        pts = (1 if up_day else -1) if r.rvol >= 1.2 else 0
        add("Index volume", f"{r.rvol:.2f}x 20-day avg on a {'up' if up_day else 'down'} day", pts, "")
    if fii_5d_cr is not None:
        pts = 1 if fii_5d_cr > 2000 else -1 if fii_5d_cr < -2000 else 0
        add("FII cash (5d net)", f"₹{fii_5d_cr:,.0f} cr", pts, "")
    total = sum(x["points"] for x in f)
    label = ("Strong bullish" if total >= 6 else "Bullish" if total >= 2 else
             "Neutral" if total > -2 else "Bearish" if total > -6 else "Strong bearish")
    return dict(label=label, score=total, factors=f,
                rule="Sum of factor points: ≥6 strong bullish, 2..5 bullish, -1..1 neutral, "
                     "-5..-2 bearish, ≤-6 strong bearish. Global context is narrative only.")


def breadth_stats(frames: dict) -> dict:
    last = [d.iloc[-1] for d in frames.values() if len(d) > 1]
    adv = sum(1 for r in last if r.chg_pct > 0)
    dec = sum(1 for r in last if r.chg_pct < 0)
    unch = len(last) - adv - dec

    def pct(col):
        v = [r.close > r[col] for r in last if np.isfinite(r[col])]
        return round(100 * sum(v) / len(v), 1) if v else None

    hi = sum(1 for r in last if np.isfinite(r.hi52) and r.high >= r.hi52)
    lo = sum(1 for r in last if np.isfinite(r.lo52) and r.low <= r.lo52)
    # 10-day A/D ratio
    ad = []
    for k in range(1, 11):
        a = sum(1 for d in frames.values() if len(d) > k and d.chg_pct.iloc[-k] > 0)
        b = sum(1 for d in frames.values() if len(d) > k and d.chg_pct.iloc[-k] < 0)
        ad.append(a / max(b, 1))
    return dict(adv=adv, dec=dec, unch=unch, ad_ratio=round(adv / max(dec, 1), 2),
                ad_ratio_10d=round(float(np.mean(ad)), 2),
                above20=pct("sma20"), above50=pct("sma50"), above200=pct("sma200"),
                highs52=hi, lows52=lo, counted=len(last))


def sector_stats(frames: dict, sector_of: dict, idx_close: pd.Series) -> list:
    out = []
    by = {}
    for s, d in frames.items():
        by.setdefault(sector_of.get(s, "Other"), []).append((s, d))
    i_m = float(idx_close.iloc[-1] / idx_close.iloc[-22] - 1) * 100
    u_m = float(np.nanmedian([float(d.close.iloc[-1] / d.close.iloc[-22] - 1) * 100
                              for d in frames.values() if len(d) > 22]))
    for sec, items in sorted(by.items()):
        if len(items) < 3:
            continue
        def ret(d, n):
            return float(d.close.iloc[-1] / d.close.iloc[-1 - n] - 1) * 100 if len(d) > n else np.nan
        d1 = np.nanmedian([ret(d, 1) for _, d in items])
        w1 = np.nanmedian([ret(d, 5) for _, d in items])
        m1 = np.nanmedian([ret(d, 21) for _, d in items])
        rs_m = m1 - u_m          # vs the median Nifty 500 stock (like-for-like)
        rs_idx = m1 - i_m        # vs the cap-weighted index
        above20 = np.mean([d.close.iloc[-1] > d.sma20.iloc[-1] for _, d in items]) * 100
        above50 = np.mean([d.close.iloc[-1] > d.sma50.iloc[-1] for _, d in items]) * 100
        rv = np.nanmedian([d.rvol.iloc[-1] for _, d in items])
        rank = sorted(items, key=lambda it: ret(it[1], 21), reverse=True)
        trend = ("Leading" if rs_m > 2 and above50 >= 60 else "Improving" if rs_m > 0 and w1 > 0 else
                 "Weakening" if rs_m < 0 and w1 < 0 else "Lagging" if rs_m < -2 else "Mixed")
        out.append(dict(name=sec, members=len(items), d1=round(d1, 2), w1=round(w1, 2), m1=round(m1, 2),
                        rs_m=round(rs_m, 2), rs_idx=round(rs_idx, 2), above20=round(above20), above50=round(above50),
                        rvol=round(float(rv), 2), trend=trend,
                        strongest=[s for s, _ in rank[:3]], weakest=[s for s, _ in rank[-3:]]))
    return sorted(out, key=lambda x: x["rs_m"], reverse=True)


# ---------------------------------------------------------------- full scan
def scan(raw: dict, idx: pd.DataFrame, sector_of: dict, names: dict, vix=None, vix_chg=None,
         fii_5d_cr=None, prev_watch: Optional[set] = None, universe_size: int = 500) -> dict:
    """raw: {symbol: OHLCV DataFrame}. idx: Nifty 500 OHLCV. Returns a report body (no narrative)."""
    idx_i = add_indicators(idx)
    frames, skipped, corp, adjusted = {}, [], [], []
    for s, df in raw.items():
        if len(df) < P["min_bars"]:
            skipped.append(dict(symbol=s, reason=f"only {len(df)} sessions of history"))
            continue
        df, ev, unresolved = adjust_corporate_actions(df)
        if ev:
            adjusted.append(dict(symbol=s, events=ev))
        if unresolved:
            corp.append(s)
        frames[s] = add_indicators(df, idx["close"])
    breadth = breadth_stats(frames)
    sectors = sector_stats(frames, sector_of, idx["close"])
    # participation: share of sectors with a positive month AND most members above their 50 DMA
    sec_pos = np.mean([x["m1"] > 0 and x["above50"] >= 50 for x in sectors]) if sectors else None
    regime = classify_regime(idx_i, breadth, vix, vix_chg, sec_pos, fii_5d_cr)

    rows, setups = [], []
    for s, d in frames.items():
        r = d.iloc[-1]
        liq = liquidity_class(float(r.avg_value20_cr))
        rows.append([s, sector_of.get(s, "Other"), round(r.close, 2), round(r.chg_pct, 2),
                     *(round(float(r[f"sma{n}"]), 2) for n in (5, 10, 20, 50, 100, 200)),
                     round(r.rsi14, 1), round(r.macd_hist, 2), round(r.atr14, 2),
                     round(r.rs63, 3) if np.isfinite(r.rs63) else None,
                     round(r.rvol, 2) if np.isfinite(r.rvol) else None,
                     round(float(r.deliv_pct), 1) if "deliv_pct" in d and np.isfinite(r.deliv_pct) else None,
                     round(r.avg_value20_cr, 1), liq, round(r.ext_atr, 2)])
        if s in corp:
            continue
        found = []
        for fn in STRATEGIES:
            try:
                st = fn(s, d)
            except Exception as e:      # a rule failing on odd data must not kill the scan
                st = None
            if st:
                found.append(st)
        if not found:
            continue
        found.sort(key=lambda x: (not x.actionable_candidate, PRIORITY[x.strategy]))
        best = found[0]
        plan = trade_plan(best, d)
        q = quality(best, d, plan, regime["label"], liq, True)
        setups.append(dict(setup=best, also=[STRATEGY_NAMES[x.strategy] for x in found[1:]],
                           plan=plan, quality=q, liq=liq, d=d))

    actionable, watch, rejected = [], [], []
    for x in setups:
        s, plan, q = x["setup"], x["plan"], x["quality"]
        reasons = []
        if s.status.startswith("Rejected") or s.status == "Retest failure":
            reasons.append(s.status)
        if x["liq"] in ("Thin", "Unknown"):
            reasons.append(f"Liquidity {x['liq']}")
        if s.status == "Confirmed continuation entry" and not s.actionable_candidate:
            reasons.append("Volume pattern did not confirm (pullback volume not lighter than average)")
        if s.actionable_candidate:
            if not plan:
                reasons.append("Levels could not be computed")
            else:
                if plan["rr1"] < P["min_rr_t1"]:
                    reasons.append(f"R:R to T1 {plan['rr1']} < {P['min_rr_t1']}")
                if plan["rr_to_overhead"] is not None and plan["rr_to_overhead"] < P["min_rr_t1"]:
                    reasons.append(f"Overhead resistance {plan['overhead_resistance']} before T1-equivalent "
                                   f"(R:R {plan['rr_to_overhead']})")
            if q["score"] < P["min_quality"]:
                reasons.append(f"Quality {q['score']} < {P['min_quality']}")
            if s.strategy not in REGIME_FIT[regime["label"]]:
                reasons.append(f"Strategy not permitted in a {regime['label']} regime")
            if float(x["d"].ext_atr.iloc[-1]) > P["max_ext_atr"]:
                reasons.append("Extended from 20 DMA")
        hard_reject = s.status.startswith("Rejected") or s.status == "Retest failure" or \
            x["liq"] in ("Thin", "Unknown")
        if hard_reject:
            rejected.append((x, reasons))
        elif s.actionable_candidate and not reasons:
            actionable.append(x)
        else:
            x["blocked_by"] = reasons
            watch.append(x)

    actionable.sort(key=lambda x: (PRIORITY[x["setup"].strategy], -x["quality"]["score"]))
    overflow = actionable[P["max_actionable"]:]
    actionable = actionable[:P["max_actionable"]]
    for x in overflow:
        x["blocked_by"] = ["Ranked below the top 5"]
        watch.append(x)
    watch.sort(key=lambda x: -x["quality"]["score"])

    def opp(x, rank=None, nbars=120):
        s, d = x["setup"], x["d"]
        r = d.iloc[-1]
        bars = [[str(i.date()), round(v.open, 2), round(v.high, 2), round(v.low, 2), round(v.close, 2),
                 int(v.volume)] for i, v in d.tail(nbars).iterrows()]
        return dict(rank=rank, symbol=s.symbol, company=names.get(s.symbol, s.symbol),
                    sector=sector_of.get(s.symbol, "Other"), close=round(float(r.close), 2),
                    close_date=str(d.index[-1].date()), strategy=STRATEGY_NAMES[s.strategy],
                    strategy_code=s.strategy, status=s.status, also=x["also"], evidence=s.evidence,
                    support=s.support, resistance=s.resistance, breakout_level=s.breakout_level,
                    pullback_low=s.pullback_low, need=s.need, invalidation=s.invalidation,
                    plan=x["plan"], quality=x["quality"], liquidity=x["liq"],
                    blocked_by=x.get("blocked_by", []),
                    tech=dict(rsi14=round(float(r.rsi14), 1), rvol=round(float(r.rvol), 2),
                              rs63=round(float(r.rs63), 3) if np.isfinite(r.rs63) else None,
                              ext_atr=round(float(r.ext_atr), 2), atr14=round(float(r.atr14), 2),
                              sma20=round(float(r.sma20), 2), sma50=round(float(r.sma50), 2),
                              sma200=round(float(r.sma200), 2),
                              trend="Uptrend" if _uptrend(d) else "Not a clean uptrend"),
                    bars=bars)

    prev_watch = prev_watch or set()
    watch_out = [opp(x, nbars=60) for x in watch[:30]]
    watch_map = {"Confirmed continuation entry": "Confirmation pending", "Successful retest": "Approaching entry",
                 "Retest in progress": "Support test in progress", "Pullback in progress": "Pullback in progress",
                 "Breakout awaiting retest": "Watching", "Approaching entry": "Approaching entry",
                 "Watching": "Watching"}
    for w in watch_out:
        w["carried_forward"] = w["symbol"] in prev_watch
        w["watch_status"] = watch_map.get(w["status"], "Watching")
    dropped = sorted(prev_watch - {w["symbol"] for w in watch_out} - {x["setup"].symbol for x in actionable})
    return dict(
        engine_version=ENGINE_VERSION, params=P,
        regime=regime, breadth=breadth, sectors=sectors,
        opportunities=[opp(x, i + 1) for i, x in enumerate(actionable)],
        no_trade=len(actionable) == 0,
        watchlist=watch_out,
        removed_from_watchlist=[dict(symbol=s, reason="No longer meets any setup rule on fresh data")
                                for s in dropped],
        rejected=[dict(symbol=x["setup"].symbol, strategy=STRATEGY_NAMES[x["setup"].strategy],
                       status=x["setup"].status, reasons=rs) for x, rs in rejected],
        screener=dict(cols=["symbol", "sector", "close", "chg_pct", "sma5", "sma10", "sma20", "sma50",
                            "sma100", "sma200", "rsi14", "macd_hist", "atr14", "rs63", "rvol",
                            "deliv_pct", "avg_value_cr", "liquidity", "ext_atr"], rows=rows),
        scan=dict(universe=universe_size, received=len(raw), scanned=len(frames),
                  complete=len(frames) >= universe_size, skipped=skipped, corporate_action_suspects=corp,
                  adjusted_for_actions=adjusted),
    )
