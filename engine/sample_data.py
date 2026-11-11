"""Synthetic, clearly fictional market data used only for the labelled SAMPLE report.

Tickers are invented (prefixed 'X') so no sample level can be mistaken for a real stock.
"""
import numpy as np
import pandas as pd

SECTORS = ["Banking", "IT", "Pharmaceuticals", "Capital goods", "Defence", "Auto", "Auto ancillaries",
           "FMCG", "Consumer durables", "Metals", "Chemicals", "Energy", "Power", "Realty",
           "Financial services", "Telecom", "Infrastructure", "Healthcare"]
WORDS = ["ARC", "BRK", "CLV", "DNM", "ELX", "FRN", "GLD", "HLX", "IND", "JVT", "KRN", "LMN", "MRV",
         "NOV", "ORB", "PRX", "QNT", "RVN", "SKY", "TRV", "UMB", "VRT", "WAV", "YLD", "ZEN"]


def make_universe(n=150, days=320, seed=7, end="2026-11-10"):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=end, periods=days)
    # index: gentle uptrend with a mid-period correction
    ir = rng.normal(0.0005, 0.009, days)
    ir[150:185] -= 0.004
    idx_c = 22000 * np.exp(np.cumsum(ir))
    idx = _ohlc(idx_c, rng, 0.006, base_vol=4e8, dates=dates)
    stocks, sector_of, names = {}, {}, {}
    for k in range(n):
        sym = "X" + WORDS[k % len(WORDS)] + ("" if k < len(WORDS) else str(k // len(WORDS)))
        beta = rng.uniform(0.6, 1.5)
        drift = rng.normal(0.0004, 0.0009)
        vol = rng.uniform(0.012, 0.028)
        r = beta * ir + rng.normal(drift, vol, days)
        c = rng.uniform(80, 3500) * np.exp(np.cumsum(r))
        df = _ohlc(c, rng, vol * 0.7, base_vol=rng.uniform(2e5, 6e6), dates=dates)
        stocks[sym] = df
        sector_of[sym] = SECTORS[k % len(SECTORS)]
        names[sym] = f"{sym[1:].title()} Example Industries (sample)"
    # plant a clean breakout-and-retest and a shallow pullback so every section has content
    stocks["XARC"] = _plant_breakout_retest(stocks["XARC"], rng)
    stocks["XBRK"] = _plant_pullback(stocks["XBRK"], rng)
    stocks["XCLV"] = _plant_breakout_retest(stocks["XCLV"], rng, confirm=False)
    return stocks, idx, sector_of, names


def _ohlc(c, rng, intraday, base_vol, dates):
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, intraday / 3, len(c)))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, intraday / 2, len(c))))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, intraday / 2, len(c))))
    v = base_vol * np.exp(rng.normal(0, 0.35, len(c)))
    df = pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=v.round()), index=dates)
    df["deliv_pct"] = np.clip(rng.normal(48, 12, len(c)), 10, 95)
    return df


def _path(df, seg_closes):
    """Overwrite the tail with a hand-shaped close path, keeping OHLC consistent."""
    d = df.copy()
    n = len(seg_closes)
    c = np.array(seg_closes)
    prev = np.r_[d.close.iloc[-n - 1], c[:-1]]
    o = prev * (1 + np.random.default_rng(3).normal(0, 0.002, n))
    h = np.maximum(o, c) * 1.004
    l = np.minimum(o, c) * 0.996
    d.iloc[-n:, d.columns.get_loc("open")] = o
    d.iloc[-n:, d.columns.get_loc("high")] = h
    d.iloc[-n:, d.columns.get_loc("low")] = l
    d.iloc[-n:, d.columns.get_loc("close")] = c
    return d


def _plant_breakout_retest(df, rng, confirm=True):
    d = df.copy()
    base = float(d.close.iloc[-110])
    # steady uptrend into the base
    trend = list(base * np.linspace(0.82, 1.0, 50))
    R = base * 1.0
    cons = list(R * (0.94 + 0.045 * np.abs(np.sin(np.linspace(0, 9, 45)))))   # 45-session base under R
    brk = [R * 1.035, R * 1.06, R * 1.075, R * 1.07]                          # breakout + follow-through
    pull = [R * 1.05, R * 1.03, R * 1.015, R * 1.008]                        # orderly retest
    seg = trend + cons + brk + pull
    last = [R * 1.004, R * 1.03] if confirm else [R * 1.002, R * 1.0]
    seg = seg + last
    d = _path(d, seg)
    n = len(seg)
    vol = float(d.volume.iloc[-200:].mean())
    v = d.volume.values.copy()
    v[-n:] = vol * 0.9
    b = -n + len(trend) + len(cons)
    v[b] = vol * 2.6
    v[b + 1] = vol * 1.8
    v[-len(pull) - len(last):-len(last)] = vol * 0.55
    v[-1] = vol * 1.9 if confirm else vol * 0.7
    d["volume"] = v
    i = len(d) - 1
    if confirm:   # bullish close at high, above prior high
        d.iloc[i, d.columns.get_loc("open")] = R * 1.003
        d.iloc[i, d.columns.get_loc("low")] = R * 0.999
        d.iloc[i, d.columns.get_loc("high")] = R * 1.032
    else:         # hammer at the zone
        d.iloc[i, d.columns.get_loc("open")] = R * 1.001
        d.iloc[i, d.columns.get_loc("low")] = R * 0.975
        d.iloc[i, d.columns.get_loc("high")] = R * 1.003
    return d


def _plant_pullback(df, rng):
    d = df.copy()
    start = float(d.close.iloc[-140])
    up = list(start * np.exp(np.cumsum(np.full(130, 0.0028) + 0.006 * np.sin(np.linspace(0, 14, 130)))))
    top = up[-1]
    pb = [top * 0.99, top * 0.975, top * 0.962, top * 0.955]
    seg = up + pb + [top * 0.972]
    d = _path(d, seg)
    vol = float(d.volume.iloc[-200:].mean())
    v = d.volume.values.copy()
    v[-len(seg):] = vol
    v[-len(pb) - 1:-1] = vol * 0.6
    v[-1] = vol * 1.6
    d["volume"] = v
    i = len(d) - 1
    d.iloc[i, d.columns.get_loc("open")] = top * 0.952
    d.iloc[i, d.columns.get_loc("low")] = top * 0.949
    d.iloc[i, d.columns.get_loc("high")] = top * 0.974
    return d
