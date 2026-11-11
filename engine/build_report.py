"""
Build one day's report body from data on disk (or from the synthetic sample).

  python build_report.py --data ../data --date 2026-11-12 --out out/
  python build_report.py --sample --out out/

Writes out/report.json (narrative fields left for the research step) and
out/screener.json. Prints a one-line JSON status to stdout.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd

from engine import add_indicators, scan, ENGINE_VERSION

warnings.filterwarnings("ignore")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

INDEX_NAMES = ["Nifty 50", "Nifty Next 50", "Nifty 100", "Nifty 200", "Nifty 500", "Nifty Midcap 100",
               "Nifty Midcap 150", "Nifty Smallcap 100", "Nifty Smallcap 250", "Nifty Bank"]
VEHICLE_MAKERS = {"MARUTI", "M&M", "TATAMOTORS", "TMPV", "TMCV", "BAJAJ-AUTO", "HEROMOTOCO", "EICHERMOT",
                  "TVSMOTOR", "ASHOKLEY", "ESCORTS", "FORCEMOT", "OLECTRA", "ATHERENERG", "OLAELEC", "HYUNDAI"}
DEFENCE = {"HAL", "BEL", "BDL", "MAZDOCK", "COCHINSHIP", "GRSE", "BEML", "SOLARINDS", "DATAPATTNS",
           "ASTRAMICRO", "PARAS", "ZENTEC", "MIDHANI"}
INDUSTRY_TO_SECTOR = {
    "Information Technology": "IT", "Capital Goods": "Capital goods",
    "Fast Moving Consumer Goods": "FMCG", "Consumer Durables": "Consumer durables",
    "Metals & Mining": "Metals", "Chemicals": "Chemicals", "Oil Gas & Consumable Fuels": "Energy",
    "Power": "Power", "Realty": "Realty", "Telecommunication": "Telecom", "Construction": "Infrastructure",
    "Construction Materials": "Infrastructure",
}


def sector_for(sym: str, name: str, industry: str) -> str:
    if sym in DEFENCE:
        return "Defence"
    if industry == "Financial Services":
        return "Banking" if re.search(r"\bbank\b", name, re.I) else "Financial services"
    if industry == "Healthcare":
        return "Pharmaceuticals" if re.search(r"pharma|lab|drug|life ?science|biocon|cipla", name, re.I) \
            else "Healthcare"
    if industry == "Automobile and Auto Components":
        return "Auto" if sym in VEHICLE_MAKERS else "Auto ancillaries"
    return INDUSTRY_TO_SECTOR.get(industry, industry or "Other")


def now_ist():
    return dt.datetime.now(IST).isoformat(timespec="seconds")


def index_rows(idx_frames: dict, n500: pd.Series) -> list:
    out = []
    for name, df in idx_frames.items():
        d = add_indicators(df)
        r = d.iloc[-1]
        rel5 = (float(d.close.iloc[-1] / d.close.iloc[-6]) / float(n500.iloc[-1] / n500.iloc[-6]) - 1) * 100 \
            if len(d) > 6 else None
        trend = ("Uptrend" if r.close > r.sma20 > r.sma50 else
                 "Downtrend" if r.close < r.sma20 < r.sma50 else "Sideways")
        out.append(dict(name=name, close=round(float(r.close), 2), chg_pct=round(float(r.chg_pct), 2),
                        volume=int(r.volume) if np.isfinite(r.volume) and r.volume > 0 else None,
                        sma20=round(float(r.sma20), 2), sma50=round(float(r.sma50), 2),
                        rel5_vs_n500=round(rel5, 2) if rel5 is not None else None, trend=trend,
                        spark=[round(float(x), 2) for x in d.close.tail(60)]))
    return out


# ------------------------------------------------------------------ real data
def load_disk(data: str, date: str):
    ymd = date.replace("-", "")
    sp = os.path.join(data, "sessions.json")
    traded = None
    if os.path.exists(sp):
        traded = {k.replace("-", "") for k, v in json.load(open(sp)).items() if v == "traded"}
        if ymd not in traded:
            raise SystemExit(json.dumps(dict(status="failed",
                                             error=f"{date} is not marked as a traded session in sessions.json")))
    files = sorted(glob.glob(os.path.join(data, "bhav", "*.csv")))
    files = [f for f in files if os.path.basename(f)[:8] <= ymd
             and (traded is None or os.path.basename(f)[:8] in traded)
             and os.path.exists(os.path.join(data, "index", os.path.basename(f)))][-320:]
    if not files or os.path.basename(files[-1])[:8] != ymd:
        raise SystemExit(json.dumps(dict(status="failed", error=f"No bhavcopy for {date} in {data}/bhav")))
    u = pd.read_csv(os.path.join(data, "nifty500.csv"))
    u.columns = [c.strip() for c in u.columns]
    uni = set(u["Symbol"])
    names = dict(zip(u["Symbol"], u["Company Name"]))
    sector_of = {s: sector_for(s, names.get(s, ""), ind) for s, ind in zip(u["Symbol"], u["Industry"])}
    parts, prev_sig = [], None
    for f in files:
        x = pd.read_csv(f)
        x = x[x["SYMBOL"].isin(uni)]
        sig = tuple(zip(x.SYMBOL, x.CLOSE, x.VOLUME))
        if sig == prev_sig:          # holiday repeat of the previous session: never a real bar
            continue
        prev_sig = sig
        x["date"] = pd.to_datetime(os.path.basename(f)[:8])
        parts.append(x)
    allx = pd.concat(parts)
    raw = {}
    for s, g in allx.groupby("SYMBOL"):
        g = g.set_index("date").sort_index()
        raw[s] = pd.DataFrame(dict(open=g.OPEN, high=g.HIGH, low=g.LOW, close=g.CLOSE, volume=g.VOLUME,
                                   deliv_pct=pd.to_numeric(g.get("DELIV_PER"), errors="coerce"),
                                   prev_close=pd.to_numeric(g["PREV_CLOSE"], errors="coerce")
                                   if "PREV_CLOSE" in g else np.nan))
    ifiles = sorted(glob.glob(os.path.join(data, "index", "*.csv")))
    ifiles = [f for f in ifiles if os.path.basename(f)[:8] <= ymd][-320:]
    ip = []
    for f in ifiles:
        x = pd.read_csv(f)
        x["date"] = pd.to_datetime(os.path.basename(f)[:8])
        ip.append(x)
    ix = pd.concat(ip)
    idx_frames = {}
    for name in INDEX_NAMES + ["India VIX"]:
        g = ix[ix["INDEX"].str.lower() == name.lower()].set_index("date").sort_index()
        if len(g):
            idx_frames[name] = pd.DataFrame(dict(open=g.OPEN, high=g.HIGH, low=g.LOW, close=g.CLOSE,
                                                 volume=pd.to_numeric(g.VOLUME, errors="coerce")))
    flows = None
    fp = os.path.join(data, "fiidii", f"{ymd}.json")
    if os.path.exists(fp):
        flows = json.load(open(fp))
    return raw, idx_frames, sector_of, names, len(uni), flows


# ------------------------------------------------------------------ main
def build(raw, idx_frames, sector_of, names, universe, flows, market_date, is_sample, sources):
    n500 = idx_frames["Nifty 500"]
    vix = idx_frames.get("India VIX")
    vix_last = float(vix.close.iloc[-1]) if vix is not None else None
    vix_chg = float((vix.close.iloc[-1] / vix.close.iloc[-2] - 1) * 100) if vix is not None else None
    fii5 = None
    if flows:
        if (flows.get("fii_days_on_file") or 0) >= 5:
            fii5 = flows.get("fii_5d_net_cr")
        else:
            flows["fii_5d_net_cr"] = None
            flows["note"] = (f"5-session FII total starts once 5 sessions are on file "
                             f"({flows.get('fii_days_on_file') or 1} so far); not used in the regime yet")
    body = scan(raw, n500, sector_of, names, vix=vix_last, vix_chg=vix_chg, fii_5d_cr=fii5,
                universe_size=universe)
    screener = body.pop("screener")
    indices = index_rows({k: v for k, v in idx_frames.items() if k != "India VIX"}, n500.close)
    report = dict(
        market_date=market_date, version=1, created_at=now_ist(), is_sample=is_sample,
        revision_reason=None,
        indices=indices,
        vix=dict(close=round(vix_last, 2) if vix_last else None, chg_pct=round(vix_chg, 2) if vix_chg else None),
        flows=flows or dict(status="unavailable", note="FII/DII figures not retrieved by the data step"),
        **body,
        # filled by the research step (Claude); never alters engine fields above
        commentary=None, global_context=[], news=[], research=None,
        sources=sources,
    )
    return report, dict(market_date=market_date, is_sample=is_sample, **screener)


def jdefault(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


def clean(o):
    """NaN/inf -> None so the JSON is valid."""
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--date")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.sample:
        from sample_data import make_universe
        raw, idx, sector_of, names = make_universe()
        scale = {"Nifty 50": 1.1, "Nifty Next 50": 3.0, "Nifty 100": 1.12, "Nifty 200": 0.62,
                 "Nifty 500": 1.0, "Nifty Midcap 100": 2.6, "Nifty Midcap 150": 0.97,
                 "Nifty Smallcap 100": 0.84, "Nifty Smallcap 250": 0.77, "Nifty Bank": 2.5}
        rng = np.random.default_rng(11)
        idx_frames = {}
        for k, s in scale.items():
            noise = np.exp(np.cumsum(rng.normal(0, 0.002, len(idx))))
            f = idx.copy()
            for c in ("open", "high", "low", "close"):
                f[c] = idx[c] * s * noise
            idx_frames[k] = f
        idx_frames["Nifty 500"] = idx
        v = idx.copy()
        v["close"] = 13 + np.cumsum(rng.normal(0, 0.15, len(idx))).clip(-3, 4)
        v["open"] = v["high"] = v["low"] = v["close"]
        idx_frames["India VIX"] = v
        flows = dict(status="provisional", fii_cash_cr=-812.4, dii_cash_cr=1290.7, fii_5d_net_cr=-1450.0,
                     source="SAMPLE — illustrative", note="Sample values, not real flows")
        market_date = str(idx.index[-1].date())
        universe = 500
        sources = [dict(name="Synthetic sample generator", url=None, retrieved_at=now_ist(),
                        final=False, note="Fictional tickers; every number is illustrative")]
    else:
        raw, idx_frames, sector_of, names, universe, flows = load_disk(a.data, a.date)
        market_date = a.date
        sources = [dict(name="NSE CM bhavcopy (with delivery)", url="https://www.nseindia.com/all-reports",
                        retrieved_at=now_ist(), final=True),
                   dict(name="NSE index closing values", url="https://www.nseindia.com/all-reports",
                        retrieved_at=now_ist(), final=True)]
        if flows:
            sources.append(dict(name="NSE FII/DII provisional", url="https://www.nseindia.com/reports/fii-dii",
                                retrieved_at=flows.get("retrieved_at"), final=False))
    report, screener = build(raw, idx_frames, sector_of, names, universe, flows, market_date, a.sample, sources)
    report, screener = clean(json.loads(json.dumps(report, default=jdefault))), \
        clean(json.loads(json.dumps(screener, default=jdefault)))
    if not a.sample:
        from fundamentals import attach
        report = attach(report, a.data)
        if report["fundamentals_status"]["available"]:
            report["sources"].append(dict(name="NSE Integrated Filing XBRL, shareholding and pledge disclosures",
                                          url="https://www.nseindia.com/companies-listing/corporate-integrated-filing",
                                          retrieved_at=now_ist(), final=True,
                                          note="Per-stock fetch dates are on each card"))
    # the dashboard stores each report as one document (256 KB cap); research adds ~40-60 KB later,
    # so keep the engine output under ~180 KB by shortening chart history on lower-ranked watchlist names
    budget = 180_000
    for o in reversed(report["watchlist"]):
        if len(json.dumps(report, separators=(",", ":"))) <= budget:
            break
        o["bars"] = o["bars"][-20:]
    rp, sp = os.path.join(a.out, "report.json"), os.path.join(a.out, "screener.json")
    json.dump(report, open(rp, "w"), separators=(",", ":"))
    json.dump(screener, open(sp, "w"), separators=(",", ":"))
    print(json.dumps(dict(status="ok", market_date=market_date, regime=report["regime"]["label"],
                          actionable=[o["symbol"] for o in report["opportunities"]],
                          fundamentals=report.get("fundamentals_status"),
                          watchlist=len(report["watchlist"]), scanned=report["scan"]["scanned"],
                          universe=report["scan"]["universe"],
                          report_kb=round(os.path.getsize(rp) / 1024, 1),
                          screener_kb=round(os.path.getsize(sp) / 1024, 1))))


if __name__ == "__main__":
    main()
