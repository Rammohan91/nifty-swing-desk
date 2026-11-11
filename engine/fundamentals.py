"""
Attach filed fundamentals (data/fundamentals/*.json, from NSE XBRL) to the engine report.

Rule-based, like the rest of the engine:
  * BLOCK (actionable -> watchlist): a results board meeting within RESULTS_WINDOW_DAYS calendar
    days of the session — the holding window would straddle a results surprise.
  * FLAG only (shown on the card, never blocking): promoter encumbrance > 20% of promoter shares,
    promoter holding cut by >= 2 points in a quarter, negative operating cash flow vs profit
    (non-lenders), exceptional items > 25% of PBT, debt/equity > 2 (non-lenders), stale results
    (latest quarter older than ~5 months).
P/E uses trailing-four-quarter EPS from filings and the session close. P/B uses filed equity and
shares outstanding from the pledge disclosure.
"""
from __future__ import annotations

import datetime as dt
import json
import os

RESULTS_WINDOW_DAYS = 14


def load(data_dir):
    base = os.path.join(data_dir, "fundamentals")
    cal_p = os.path.join(base, "_calendar.json")
    cal = json.load(open(cal_p)).get("events", {}) if os.path.exists(cal_p) else {}
    return base, cal


def summarise(sym, close, market_date, base, cal):
    p = os.path.join(base, f"{sym}.json")
    if not os.path.exists(p):
        return None, [], None
    r = json.load(open(p))
    L, t, b = r.get("latest") or {}, r.get("ttm") or {}, r.get("balance_sheet") or {}
    sh, pl = r.get("shareholding") or {}, r.get("pledge") or {}
    shares = pl.get("shares_outstanding")
    mcap_cr = round(close * shares / 1e7, 0) if shares else None
    pe = round(close / t["eps"], 1) if t.get("eps") and t["eps"] > 0 else None
    pb = round(mcap_cr / b["equity_cr"], 1) if mcap_cr and b.get("equity_cr") and b["equity_cr"] > 0 else None
    md = dt.date.fromisoformat(market_date)
    upcoming = [e for e in cal.get(sym, []) if dt.date.fromisoformat(e["date"]) >= md]
    flags, block = [], None
    for e in upcoming:
        d = dt.date.fromisoformat(e["date"])
        if "result" in (e.get("purpose") or "").lower() and (d - md).days <= RESULTS_WINDOW_DAYS:
            block = f"Results board meeting on {e['date']} — inside the holding window"
            flags.append(block)
            break
    enc = pl.get("promoter_encumbered_pct_of_promoter")
    if enc is not None and enc > 20:
        flags.append(f"Promoter encumbrance {enc:.1f}% of promoter shares")
    chg = sh.get("promoter_change_pts")
    if chg is not None and chg <= -2:
        flags.append(f"Promoter holding cut {chg:+.2f} pts last quarter")
    if not r.get("financial"):
        o = b.get("ocf_to_pat_h1", b.get("ocf_to_pat_fy"))
        if o is not None and o < 0:
            flags.append(f"Operating cash flow negative vs profit (OCF/PAT {o:.2f})")
        if b.get("de") is not None and b["de"] > 2:
            flags.append(f"Debt/equity {b['de']:.2f}")
    q0 = (r.get("quarters") or [{}])[0]
    if q0.get("exceptional_cr") and q0.get("pbt_cr") and abs(q0["exceptional_cr"]) > 0.25 * abs(q0["pbt_cr"]):
        flags.append(f"Exceptional item ₹{q0['exceptional_cr']:,.0f} cr in latest quarter")
    if L.get("qe") and (md - dt.date.fromisoformat(L["qe"])).days > 150:
        flags.append(f"Latest filed quarter is {L['qe']} — results may be pending")
    out = dict(
        as_of_quarter=L.get("qe"), consolidated=L.get("consolidated"), filed=L.get("filed"), filing=L.get("filing"),
        financial=r.get("financial", False),
        revenue_cr=L.get("revenue_cr"), revenue_yoy=L.get("revenue_yoy"), revenue_qoq=L.get("revenue_qoq"),
        ebitda_margin=L.get("ebitda_margin"), margin_yoy_pts=L.get("margin_yoy_pts"),
        pat_cr=L.get("pat_cr"), pat_yoy=L.get("pat_yoy"), pat_qoq=L.get("pat_qoq"),
        ttm_eps=t.get("eps"), roe=t.get("roe"), roce=t.get("roce"),
        de=b.get("de"), gross_npa_pct=b.get("gross_npa_pct"), ocf_to_pat=b.get("ocf_to_pat_h1", b.get("ocf_to_pat_fy")),
        balance_sheet_as_of=b.get("as_of"),
        pe=pe, pb=pb, mcap_cr=mcap_cr,
        promoter_pct=sh.get("promoter_pct"), promoter_change_pts=chg, shareholding_as_of=sh.get("as_of"),
        promoter_encumbered_pct=enc,
        upcoming=upcoming[:3], flags=flags, missing=r.get("missing", []), note=r.get("note"),
        trend=[[q["qe"], q.get("revenue_cr"), q.get("pat_cr"), q.get("ebitda_margin")] for q in r.get("quarters", [])[:5]],
        source=r.get("source"), fetched_at=r.get("fetched_at"))
    return out, flags, block


def attach(report, data_dir):
    base, cal = load(data_dir)
    if not os.path.isdir(base):
        report["fundamentals_status"] = dict(available=False, note="No fundamentals data in the repo yet")
        return report
    md = report["market_date"]
    have = 0
    keep = []
    for o in report["opportunities"]:
        f, flags, block = summarise(o["symbol"], o["close"], md, base, cal)
        o["fundamentals"] = f
        have += f is not None
        if block:
            o["blocked_by"] = [block]
            o["watch_status"] = "Confirmation pending"
            o["carried_forward"] = False
            o["rank"] = None
            report["watchlist"].insert(0, o)
        else:
            keep.append(o)
    for i, o in enumerate(keep):
        o["rank"] = i + 1
    report["opportunities"] = keep
    report["no_trade"] = len(keep) == 0
    for o in report["watchlist"]:
        if "fundamentals" in o:
            continue
        f, flags, block = summarise(o["symbol"], o["close"], md, base, cal)
        o["fundamentals"] = f
        have += f is not None
        if block and block not in o.get("blocked_by", []):
            o.setdefault("blocked_by", []).append(block)
    shortlisted = len(report["opportunities"]) + len([o for o in report["watchlist"] if "fundamentals" in o])
    report["fundamentals_status"] = dict(available=True, shortlisted=shortlisted, attached=have,
                                         calendar_symbols=len(cal), results_window_days=RESULTS_WINDOW_DAYS)
    return report
