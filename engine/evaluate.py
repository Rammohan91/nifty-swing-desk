"""
Mark-to-market of past signals and the user's tracked trades, on official closes only.

  python evaluate.py --data ../data --date 2026-11-12 --report out/report.json \
      --signals-in sig_export/signals --trades-in sig_export/trades --out out/eval

Execution assumptions (deliberately conservative):
  * Entry only if a later session trades at/above the trigger within 3 sessions of the signal.
  * Fill = max(open, trigger). If the open is above the max-chase price, the signal is skipped
    ("not_triggered", gap) — no fill at a price that was never acceptable.
  * Stop gapped through -> fill at the open. Stop and target touched in one bar -> stop first.
  * After T1 trades, the stop moves to entry. Exit at T2, or at the close of session 10.
  * 0.25% round-trip cost deducted (brokerage, STT, slippage), expressed in R.
Writes one JSON per changed document into --out/signals and --out/trades.
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import pandas as pd

COST = 0.0025
ENTRY_WINDOW = 3
MAX_HOLD = 10


def load_prices(data, symbols, upto):
    ymd = upto.replace("-", "")
    files = sorted(f for f in glob.glob(os.path.join(data, "bhav", "*.csv")) if os.path.basename(f)[:8] <= ymd)[-40:]
    out = {s: [] for s in symbols}
    for f in files:
        d = pd.to_datetime(os.path.basename(f)[:8]).strftime("%Y-%m-%d")
        x = pd.read_csv(f)
        x = x[x.SYMBOL.isin(symbols)]
        for r in x.itertuples():
            out[r.SYMBOL].append((d, r.OPEN, r.HIGH, r.LOW, r.CLOSE))
    return {s: sorted(v) for s, v in out.items()}


def evaluate_signal(sig, bars):
    """bars: list of (date, o, h, l, c) after nothing — we filter by date here."""
    after = [b for b in bars if b[0] > sig["rec_date"]]
    trig, stop0, t1, t2 = sig["trigger"], sig["stop"], sig["t1"], sig["t2"]
    chase = sig.get("max_chase") or trig * 1.01
    res = dict(sig)
    if not after:
        res["outcome"] = "pending"
        return res
    entry_i = None
    for i, b in enumerate(after[:ENTRY_WINDOW]):
        if b[2] >= trig:
            if b[1] > chase:
                res.update(outcome="not_triggered", triggered=False, note=f"Gapped open {b[1]} above max-chase {chase}")
                return res
            entry_i, fill = i, max(b[1], trig)
            break
    if entry_i is None:
        res.update(outcome="not_triggered" if len(after) >= ENTRY_WINDOW else "pending", triggered=False)
        return res
    risk = fill - stop0
    res.update(triggered=True, entry_date=after[entry_i][0], entry_price=round(fill, 2))
    stop, hit_t1, mfe, mae = stop0, False, 0.0, 0.0
    held = after[entry_i:]
    for k, (d, o, h, l, c) in enumerate(held):
        mfe, mae = max(mfe, (h - fill) / risk), min(mae, (l - fill) / risk)
        if k > 0 and o <= stop:                                   # gap through stop
            return _close(res, d, o, fill, risk, "stop" if not hit_t1 else "t1", k, hit_t1, mfe, mae)
        if l <= stop:
            return _close(res, d, stop, fill, risk, "stop" if not hit_t1 else "t1", k, hit_t1, mfe, mae)
        if h >= t2:
            return _close(res, d, t2, fill, risk, "t2", k, True, mfe, mae)
        if h >= t1 and not hit_t1:
            hit_t1, stop = True, fill
        if k + 1 >= MAX_HOLD:
            return _close(res, d, c, fill, risk, "t1" if hit_t1 else "expired", k, hit_t1, mfe, mae)
    res.update(outcome="open", hit_t1=hit_t1, mfe_r=round(mfe, 2), mae_r=round(mae, 2), hold_days=len(held))
    return res


def _close(res, d, px, fill, risk, outcome, k, hit_t1, mfe, mae):
    r = (px - fill) / risk - COST * fill / risk
    res.update(outcome=outcome, exit_date=d, exit_price=round(px, 2), r_multiple=round(r, 2), hold_days=k + 1,
               hit_t1=hit_t1, mfe_r=round(mfe, 2), mae_r=round(mae, 2))
    return res


def read_dir(p):
    out = {}
    for f in glob.glob(os.path.join(p, "*.json")) if p and os.path.isdir(p) else []:
        doc = json.load(open(f))
        doc = doc.get("data", doc)            # tolerate {id, version, data} export shapes
        out[os.path.basename(f)[:-5]] = doc
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--signals-in")
    ap.add_argument("--trades-in")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rep = json.load(open(a.report))
    sigs = {k: v for k, v in read_dir(a.signals_in).items() if not v.get("is_sample")}
    trades = read_dir(a.trades_in)
    # new signals from today's actionable list
    for o in rep["opportunities"]:
        sid = f"{rep['market_date']}_{o['symbol']}"
        if sid in sigs:
            continue
        p = o["plan"]
        sigs[sid] = dict(rec_date=rep["market_date"], report_id=f"{rep['market_date']}_v{rep['version']}",
                         symbol=o["symbol"], strategy=o["strategy"], strategy_code=o["strategy_code"],
                         regime=rep["regime"]["label"], sector=o["sector"], quality=o["quality"]["score"],
                         trigger=p["trigger"], stop=p["stop"], t1=p["t1"], t2=p["t2"], rr1=p["rr1"],
                         max_chase=p["max_chase"], outcome="pending", triggered=False, is_sample=False)
    open_sigs = {k: v for k, v in sigs.items() if v.get("outcome") in ("pending", "open")}
    open_trades = {k: v for k, v in trades.items() if v.get("entry_price") and not v.get("exit_price")}
    syms = {v["symbol"] for v in open_sigs.values()} | {v["symbol"] for v in open_trades.values()}
    prices = load_prices(a.data, syms, a.date) if syms else {}
    os.makedirs(os.path.join(a.out, "signals"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "trades"), exist_ok=True)
    changed_s = changed_t = 0
    for k, v in open_sigs.items():
        new = evaluate_signal(v, prices.get(v["symbol"], []))
        new["last_eval"] = a.date
        if new != v:
            json.dump(new, open(os.path.join(a.out, "signals", k + ".json"), "w"))
            changed_s += 1
    for k, t in open_trades.items():
        bars = [b for b in prices.get(t["symbol"], []) if b[0] >= (t.get("entry_date") or "")]
        if not bars or not t.get("stop") or t["entry_price"] <= t["stop"]:
            continue
        risk = t["entry_price"] - t["stop"]
        mfe = round(max((b[2] - t["entry_price"]) / risk for b in bars), 2)
        mae = round(min((b[3] - t["entry_price"]) / risk for b in bars), 2)
        if (mfe, mae) != (t.get("mfe_r"), t.get("mae_r")):
            json.dump(dict(mfe_r=mfe, mae_r=mae, last_eval=a.date),
                      open(os.path.join(a.out, "trades", k + ".json"), "w"))
            changed_t += 1
    print(json.dumps(dict(status="ok", new_or_changed_signals=changed_s, trade_updates=changed_t)))


if __name__ == "__main__":
    main()
