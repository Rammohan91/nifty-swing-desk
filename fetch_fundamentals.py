"""
Fundamentals from NSE filings, for the Nifty 500 universe. Runs in GitHub Actions.

Sources (all public, no login):
  * Integrated Filing – Financials: list per symbol, then the XBRL of each quarterly result
    (consolidated preferred, standalone otherwise). Values come straight from the filed XBRL.
  * Shareholding pattern list (promoter & promoter-group %), quarterly.
  * Pledge / encumbrance disclosure (promoter shares encumbered %, shares outstanding).
  * Event calendar (upcoming board meetings, e.g. results dates).

Writes data/fundamentals/{SYMBOL}.json and data/fundamentals/_index.json. Parsed XBRL is cached
in data/fundamentals/_xbrl/{seq}.json so a filing is downloaded once.

Refresh policy per run (keeps each run under ~30 minutes):
  1. symbols with a new financial-results filing in the last 4 days (one market-wide request),
  2. symbols never fetched,
  3. the stalest files older than 7 days,
  capped at MAX_SYMBOLS per run. The calendar is refreshed market-wide every run.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time

try:
    from curl_cffi import requests as creq
    S = creq.Session(impersonate="chrome")
except Exception:            # plain requests works for these endpoints too
    import requests as creq
    S = creq.Session()
    S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
S.headers.update({"Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
                  "Referer": "https://www.nseindia.com/companies-listing/corporate-integrated-filing"})

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DATA = os.environ.get("DATA_DIR", "data")
OUT = f"{DATA}/fundamentals"
XC = f"{OUT}/_xbrl"
MAX_SYMBOLS = int(os.environ.get("MAX_SYMBOLS", "180"))
PAUSE = float(os.environ.get("PAUSE", "0.9"))
QUARTERS = 5
API = "https://www.nseindia.com/api"
_last_cookie = [0.0]


def warm():
    if time.time() - _last_cookie[0] > 240:          # NSE cookies expire after a few minutes
        try:
            S.get("https://www.nseindia.com/", timeout=30)
            S.get("https://www.nseindia.com/companies-listing/corporate-integrated-filing", timeout=30)
        except Exception:
            pass
        _last_cookie[0] = time.time()
        time.sleep(1)


def get_json(url, tries=3):
    for i in range(tries):
        warm()
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                _last_cookie[0] = 0
        except Exception:
            pass
        time.sleep(2 * (i + 1))
    return None


def get_text(url, tries=3):
    for i in range(tries):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 200 and r.content:
                return r.text
        except Exception:
            pass
        time.sleep(2 * (i + 1))
    return None


def num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def pdate(s):
    for f in ("%d-%b-%Y", "%d-%B-%Y", "%d-%b-%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s.strip().title(), f).date()
        except (ValueError, AttributeError):
            pass
    return None


# ------------------------------------------------------------------ XBRL
TAGS = {
    "revenue": ["RevenueFromOperations", "InterestEarned", "RevenueFromOperationsBanking"],
    "other_income": ["OtherIncome"],
    "total_income": ["Income", "TotalIncome"],
    "expenses": ["Expenses"],
    "pbt": ["ProfitBeforeTax", "ProfitLossBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "tax": ["TaxExpense"],
    "pat": ["ProfitLossForPeriod", "ProfitLossForThePeriod", "NetProfitLossForThePeriod"],
    "pat_owners": ["ProfitLossAttributableToOwnersOfParent", "ProfitOrLossAttributableToOwnersOfParent",
                   "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"],
    "finance_costs": ["FinanceCosts", "InterestExpended"],
    "exceptional": ["ExceptionalItemsBeforeTax", "ExceptionalItems"],
    "eps": ["BasicEarningsLossPerShareFromContinuingOperations",
            "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsPerShareAfterExtraordinaryItems", "BasicEarningsLossPerShare"],
    "paid_up_capital": ["PaidUpValueOfEquityShareCapital", "EquityShareCapital"],
    "face_value": ["FaceValueOfEquityShareCapital"],
    "equity": ["EquityAttributableToOwnersOfParent", "Equity", "TotalEquity"],
    "bank_capital": ["Capital"],
    "bank_reserves": ["ReservesAndSurplus"],
    "deposits": ["Deposits"],
    "borrowings_nc": ["BorrowingsNoncurrent"],
    "borrowings_c": ["BorrowingsCurrent"],
    "borrowings_bank": ["Borrowings"],
    "cash": ["CashAndCashEquivalents"],
    "receivables": ["TradeReceivablesCurrent"],
    "inventories": ["Inventories"],
    "ocf": ["CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"],
    "gross_npa_pct": ["PercentageOfGrossNpa", "PercentageOfGrossNPA"],
}


def parse_xbrl(x: str, qe: dt.date) -> dict:
    ctx = {}
    for cid, body in re.findall(r'<(?:xbrli:)?context\s+id="([^"]+)"\s*>(.*?)</(?:xbrli:)?context>', x, re.S):
        if "explicitMember" in body or "typedMember" in body:
            continue                                  # dimensional (segment) contexts are not totals
        st = re.search(r"startDate>([\d-]+)<", body)
        en = re.search(r"endDate>([\d-]+)<", body)
        ins = re.search(r"instant>([\d-]+)<", body)
        if st and en:
            a, b = dt.date.fromisoformat(st.group(1)), dt.date.fromisoformat(en.group(1))
            ctx[cid] = ("D", a, b, (b - a).days)
        elif ins:
            ctx[cid] = ("I", None, dt.date.fromisoformat(ins.group(1)), 0)
    facts = {}
    for name, attrs, val in re.findall(r"<[\w-]+:(\w+)\b([^>]*?)>([^<]*)</[\w-]+:\1>", x):
        m = re.search(r'contextRef="([^"]+)"', attrs)
        if not m or m.group(1) not in ctx:
            continue
        v = num(val)
        if v is None:
            continue
        facts.setdefault(name, {})[m.group(1)] = v

    def pick(names, kind):
        for n in names:
            for cid, v in facts.get(n, {}).items():
                k, a, b, days = ctx[cid]
                if b != qe:
                    continue
                if kind == "Q" and k == "D" and 80 <= days <= 100:
                    return v
                if kind == "H" and k == "D" and 170 <= days <= 190:
                    return v
                if kind == "Y" and k == "D" and 350 <= days <= 370:
                    return v
                if kind == "I" and k == "I":
                    return v
        return None

    FLOW = ("revenue", "other_income", "total_income", "expenses", "pbt", "tax", "pat", "pat_owners",
            "depreciation", "finance_costs", "exceptional", "eps")
    q = {k: pick(TAGS[k], "Q") for k in FLOW + ("face_value",)}
    q["paid_up_capital"] = pick(TAGS["paid_up_capital"], "Q")
    h = {k: pick(TAGS[k], "H") for k in FLOW}
    for dct in (q, h):
        # owners' share only when it is a plausible part of reported profit; some filings use the tag
        # inside other statements, which produced zeros and sign errors
        t, o = dct.get("pat"), dct.get("pat_owners")
        if t is not None and o is not None and t != 0 and abs(o / t - 1) <= 0.3:
            dct["pat"] = o
        elif t is None and o is not None:
            dct["pat"] = o
    if q.get("paid_up_capital") is None:
        q["paid_up_capital"] = pick(TAGS["paid_up_capital"], "I")
    bs = {k: pick(TAGS[k], "I") for k in ("equity", "borrowings_nc", "borrowings_c", "borrowings_bank", "cash",
                                          "receivables", "inventories", "bank_capital", "bank_reserves",
                                          "deposits")}
    if bs.get("equity") is None and bs.get("bank_capital") is not None and bs.get("bank_reserves") is not None:
        bs["equity"] = bs["bank_capital"] + bs["bank_reserves"]          # banking taxonomy
    npa = pick(TAGS["gross_npa_pct"], "Q")
    if npa is None:
        npa = pick(TAGS["gross_npa_pct"], "I")
    # unit is "pure" (a fraction); consolidated bank filings report 0, meaning not reported
    bs["gross_npa_pct"] = round(npa * 100, 2) if npa else None
    cf = {"ocf_h1": pick(TAGS["ocf"], "H"), "ocf_y": pick(TAGS["ocf"], "Y"),
          "pat_h1": pick(TAGS["pat"], "H"), "pat_y": pick(TAGS["pat"], "Y")}
    return dict(q=q, h=h, bs=bs if any(v is not None for v in bs.values()) else None, cf=cf,
                banking="BANKING" in x[:3000] or "InterestEarned" in facts)


# ------------------------------------------------------------------ per symbol
def filings_list(sym):
    d = get_json(f"{API}/integrated-filing-results?index=equities&symbol={sym}"
                 "&type=Integrated%20Filing-%20Financials")
    rows = (d or {}).get("data") or []
    out = {}
    for r in rows:
        qe = pdate(r.get("qe_Date") or "")
        if not qe or not r.get("xbrl"):
            continue
        cons = (r.get("consolidated") or "").lower().startswith("cons")
        url = r["xbrl"] if r["xbrl"].startswith("http") else "https://nsearchives.nseindia.com/corporate" + r["xbrl"]
        cur = out.get(qe)
        filed = r.get("broadcast_Date") or ""
        # prefer consolidated; among equals prefer the latest filing (revisions)
        rank = (cons, filed)
        if cur is None or rank > cur["rank"]:
            out[qe] = dict(qe=qe, url=url, consolidated=cons, filed=filed, ixbrl=r.get("ixbrl"), rank=rank,
                           seq=str(r.get("seq_Id") or re.sub(r"\W", "", url[-40:])))
    return [out[k] for k in sorted(out, reverse=True)]


def parsed_filing(f):
    os.makedirs(XC, exist_ok=True)
    p = f"{XC}/{f['seq']}.json"
    if os.path.exists(p):
        d = json.load(open(p))
        if d.get("parser") == 3:           # older cache entries are re-parsed with the current rules
            return d
    x = get_text(f["url"])
    if not x:
        return None
    d = parse_xbrl(x, f["qe"])
    d.update(parser=3, qe=f["qe"].isoformat(), consolidated=f["consolidated"], filed=f["filed"], ixbrl=f["ixbrl"], xbrl=f["url"])
    json.dump(d, open(p, "w"))
    time.sleep(PAUSE)
    return d


def cr(v):
    return None if v is None else round(v / 1e7, 2)


def pct(a, b):
    return None if a is None or b in (None, 0) else round((a / b - 1) * 100 * (1 if b > 0 else -1), 1)


def build_symbol(sym, financial=False):
    fl = filings_list(sym)
    time.sleep(PAUSE)
    quarters = []
    for f in fl[:QUARTERS + 1]:
        d = parsed_filing(f)
        if d:
            quarters.append(d)
    shp = get_json(f"{API}/corporate-share-holdings-master?index=equities&symbol={sym}") or []
    time.sleep(PAUSE)
    pl = get_json(f"{API}/corporate-pledgedata?index=equities&symbol={sym}") or {}
    time.sleep(PAUSE)

    rec = dict(symbol=sym, fetched_at=dt.datetime.now(IST).isoformat(timespec="seconds"), quarters=[], missing=[],
               financial=financial or any(d.get("banking") for d in quarters))
    by_qe = {d["qe"]: d for d in quarters}
    for d in quarters:
        q = dict(d["q"])
        d["derived"] = False
        if q.get("pat") is None and (d.get("h") or {}).get("pat") is not None:
            # Some March filings report the half-year (Oct–Mar) instead of the quarter: Q4 = H2 − Q3
            prev_qe = (dt.date.fromisoformat(d["qe"]).replace(day=1) - dt.timedelta(days=1))
            prev_qe = (prev_qe.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
            prev_qe = (prev_qe - dt.timedelta(days=1))
            p3 = by_qe.get(prev_qe.isoformat())
            if p3:
                for k, v in d["h"].items():
                    pv = p3["q"].get(k)
                    q[k] = v - pv if v is not None and pv is not None else None
                d["derived"] = True
        ebitda = None
        if None not in (q.get("pbt"), q.get("finance_costs"), q.get("depreciation")):
            ebitda = q["pbt"] + q["finance_costs"] + q["depreciation"] - (q.get("other_income") or 0) \
                - (q.get("exceptional") or 0)
        rec["quarters"].append(dict(
            qe=d["qe"], consolidated=d["consolidated"], filed=d["filed"], filing=d.get("ixbrl") or d.get("xbrl"),
            revenue_cr=cr(q.get("revenue")), ebitda_cr=cr(ebitda), pat_cr=cr(q.get("pat")), pbt_cr=cr(q.get("pbt")),
            finance_costs_cr=cr(q.get("finance_costs")), exceptional_cr=cr(q.get("exceptional")), eps=q.get("eps"),
            ebitda_margin=round(ebitda / q["revenue"] * 100, 1) if ebitda is not None and q.get("revenue") else None,
            paid_up_capital=q.get("paid_up_capital"), face_value=q.get("face_value"),
            derived_from_half_year=d["derived"]))
    Q = rec["quarters"]
    if not Q:
        rec["missing"].append("no integrated-filing results found")
    else:
        L = Q[0]
        yago = next((x for x in Q if x["qe"][:4] == str(int(L["qe"][:4]) - 1) and x["qe"][5:] == L["qe"][5:]), None)
        prev = Q[1] if len(Q) > 1 else None
        rec["latest"] = dict(
            qe=L["qe"], consolidated=L["consolidated"], filed=L["filed"], filing=L["filing"],
            revenue_cr=L["revenue_cr"], ebitda_cr=L["ebitda_cr"], ebitda_margin=L["ebitda_margin"], pat_cr=L["pat_cr"],
            eps=L["eps"],
            revenue_yoy=pct(L["revenue_cr"], yago and yago["revenue_cr"]),
            pat_yoy=pct(L["pat_cr"], yago and yago["pat_cr"]),
            ebitda_yoy=pct(L["ebitda_cr"], yago and yago["ebitda_cr"]),
            margin_yoy_pts=round(L["ebitda_margin"] - yago["ebitda_margin"], 1)
            if yago and L["ebitda_margin"] is not None and yago["ebitda_margin"] is not None else None,
            revenue_qoq=pct(L["revenue_cr"], prev and prev["revenue_cr"]),
            pat_qoq=pct(L["pat_cr"], prev and prev["pat_cr"]),
            exceptional_cr=L["exceptional_cr"],
            yoy_base_qe=yago and yago["qe"])
        last4 = Q[:4]
        if len(last4) == 4 and all(x["eps"] is not None for x in last4):
            rec["ttm"] = dict(eps=round(sum(x["eps"] for x in last4), 2),
                              pat_cr=round(sum(x["pat_cr"] or 0 for x in last4), 2),
                              ebit_cr=round(sum((x["pbt_cr"] or 0) + (x["finance_costs_cr"] or 0) for x in last4), 2),
                              quarters=[x["qe"] for x in last4])
        else:
            rec["missing"].append("fewer than 4 quarters with EPS: no TTM, P/E, ROE or ROCE")
        bsq = next((d for d in quarters if d.get("bs") and d["bs"].get("equity")), None)
        if bsq:
            b = bsq["bs"]
            debt = sum(v for v in (b.get("borrowings_nc"), b.get("borrowings_c")) if v is not None) \
                if (b.get("borrowings_nc") is not None or b.get("borrowings_c") is not None) else b.get("borrowings_bank")
            rec["balance_sheet"] = dict(as_of=bsq["qe"], equity_cr=cr(b["equity"]), borrowings_cr=cr(debt),
                                        cash_cr=cr(b.get("cash")), receivables_cr=cr(b.get("receivables")),
                                        inventories_cr=cr(b.get("inventories")),
                                        de=round(debt / b["equity"], 2) if debt is not None and b["equity"] else None,
                                        gross_npa_pct=b.get("gross_npa_pct"))
            t = rec.get("ttm")
            if t and b["equity"]:
                rec["ttm"]["roe"] = round(t["pat_cr"] / cr(b["equity"]) * 100, 1)
                if debt is not None:
                    rec["ttm"]["roce"] = round(t["ebit_cr"] / (cr(b["equity"]) + cr(debt)) * 100, 1)
            cf = bsq["cf"]
            if cf.get("ocf_h1") is not None and cf.get("pat_h1"):
                rec["balance_sheet"]["ocf_to_pat_h1"] = round(cf["ocf_h1"] / cf["pat_h1"], 2)
            elif cf.get("ocf_y") is not None and cf.get("pat_y"):
                rec["balance_sheet"]["ocf_to_pat_fy"] = round(cf["ocf_y"] / cf["pat_y"], 2)
        else:
            rec["missing"].append("no balance sheet in recent filings: no ROE, ROCE or debt/equity")
    # shareholding
    sh = [s for s in shp if isinstance(s, dict) and s.get("pr_and_prgrp") not in (None, "-")]
    if sh:
        sh.sort(key=lambda s: pdate(s.get("date") or "") or dt.date.min, reverse=True)
        cur, prv = num(sh[0]["pr_and_prgrp"]), num(sh[1]["pr_and_prgrp"]) if len(sh) > 1 else None
        rec["shareholding"] = dict(as_of=sh[0].get("date"), promoter_pct=cur,
                                   promoter_change_pts=round(cur - prv, 2) if cur is not None and prv is not None else None,
                                   history=[[s.get("date"), num(s["pr_and_prgrp"])] for s in sh[:6]])
    else:
        rec["missing"].append("shareholding pattern")
    pdata = (pl.get("data") or [None])[0] if isinstance(pl, dict) else None
    if pdata:
        rec["pledge"] = dict(as_of=pdata.get("shp"), broadcast=pdata.get("broadcastDt"),
                             promoter_encumbered_pct_of_promoter=num(pdata.get("percPromoterShares")),
                             promoter_encumbered_pct_of_total=num(pdata.get("percTotShares")),
                             shares_outstanding=num(pdata.get("totIssuedShares")))
    else:
        rec["missing"].append("pledge disclosure")
    if rec["financial"]:
        for x in rec["quarters"]:
            x["ebitda_cr"] = x["ebitda_margin"] = None
        if rec.get("latest"):
            for k in ("ebitda_cr", "ebitda_margin", "ebitda_yoy", "margin_yoy_pts"):
                rec["latest"][k] = None
        if rec.get("ttm"):
            rec["ttm"].pop("roce", None)
        if rec.get("balance_sheet"):
            for k in ("ocf_to_pat_h1", "ocf_to_pat_fy", "de", "receivables_cr", "inventories_cr"):
                rec["balance_sheet"].pop(k, None)
        rec["note"] = "Lender: EBITDA, ROCE, debt/equity and cash-flow ratios are not meaningful and are omitted."
    rec["source"] = dict(name="NSE Integrated Filing (XBRL), shareholding and pledge disclosures",
                         url=f"https://www.nseindia.com/get-quotes/equity?symbol={sym}")
    return rec


# ------------------------------------------------------------------ market-wide
def calendar():
    d = get_json(f"{API}/event-calendar?index=equities")
    rows = d if isinstance(d, list) else []
    out = {}
    today = dt.datetime.now(IST).date()
    for r in rows:
        day = pdate(r.get("date") or "")
        if not day or day < today:
            continue
        out.setdefault(r.get("symbol"), []).append(dict(date=day.isoformat(), purpose=r.get("purpose"),
                                                        desc=(r.get("bm_desc") or "")[:200]))
    return out


def recent_filers(days=4):
    today = dt.datetime.now(IST).date()
    f, t = (today - dt.timedelta(days=days)).strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y")
    syms = set()
    for page in range(1, 11):
        d = get_json(f"{API}/integrated-filing-results?index=equities&type=Integrated%20Filing-%20Financials"
                     f"&from_date={f}&to_date={t}&page={page}&size=100")
        rows = (d or {}).get("data") or []
        syms |= {r.get("symbol") for r in rows if r.get("symbol")}
        if len(rows) < 100:
            break
        time.sleep(PAUSE)
    return syms


def main():
    os.makedirs(OUT, exist_ok=True)
    import csv
    rows = list(csv.DictReader(open(f"{DATA}/nifty500.csv")))
    uni = [r["Symbol"] for r in rows]
    fin = {r["Symbol"] for r in rows if r.get("Industry") == "Financial Services"}
    idx_p = f"{OUT}/_index.json"
    index = json.load(open(idx_p)) if os.path.exists(idx_p) else {}
    now = dt.datetime.now(IST)

    cal = calendar()
    json.dump(dict(fetched_at=now.isoformat(timespec="seconds"), events=cal), open(f"{OUT}/_calendar.json", "w"))
    fresh = recent_filers() & set(uni)

    def age(s):
        t = index.get(s, {}).get("fetched_at")
        return (now - dt.datetime.fromisoformat(t)).days if t else 9999

    order = [s for s in uni if s in fresh] + \
            [s for s in uni if s not in index and s not in fresh] + \
            sorted([s for s in uni if s in index and s not in fresh and age(s) >= 7], key=age, reverse=True)
    done, failed = [], []
    started = time.time()
    for s in order[:MAX_SYMBOLS]:
        if time.time() - started > 45 * 60:
            break
        try:
            rec = build_symbol(s, financial=s in fin)
            json.dump(rec, open(f"{OUT}/{s}.json", "w"), separators=(",", ":"))
            index[s] = dict(fetched_at=rec["fetched_at"], latest_qe=(rec.get("latest") or {}).get("qe"),
                            missing=rec["missing"])
            done.append(s)
        except Exception as e:
            failed.append(dict(symbol=s, error=str(e)[:200]))
    json.dump(index, open(idx_p, "w"), separators=(",", ":"))
    status = dict(run_at=now.isoformat(timespec="seconds"), refreshed=len(done), failed=failed,
                  new_filers=sorted(fresh), covered=len(index), universe=len(uni),
                  calendar_symbols=len(cal), remaining_never_fetched=len([s for s in uni if s not in index]))
    json.dump(status, open(f"{OUT}/_last_run.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in status.items() if k != "new_filers"}))


if __name__ == "__main__":
    if len(sys.argv) > 1:                # debug: python fetch_fundamentals.py SAILIFE HDFCBANK
        os.makedirs(OUT, exist_ok=True)
        import csv
        fin = {r["Symbol"] for r in csv.DictReader(open(f"{DATA}/nifty500.csv")) if r.get("Industry") == "Financial Services"}
        for s in sys.argv[1:]:
            rec = build_symbol(s, financial=s in fin)
            json.dump(rec, open(f"{OUT}/{s}.json", "w"), indent=1)
            print(json.dumps({k: rec.get(k) for k in ("latest", "ttm", "balance_sheet", "shareholding", "pledge",
                                                      "missing")}, indent=1)[:2500])
    else:
        main()
