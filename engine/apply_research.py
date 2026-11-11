"""
Merge the research step's output into the engine report — enforcing the boundary.

  python apply_research.py --report out/report.json --research out/research.json --out out/final.json

research.json (written by Claude in the daily run) may contain ONLY:
  regime_explanation : str
  commentary         : {stance, sections:[{title, facts:[str|{text,source}], interpretation}]}
  global_context     : [{topic, fact, relevance: High|Medium|Low, note, source:{name,url}}]
  news               : [{announced, event_date, symbol, company, category, desc,
                         status: Confirmed|Unconfirmed, impact, priced_in, source:{name,url}}]
  research           : {SYMBOL: {summary, fundamentals{...}, valuation{...}, governance{flags[],...},
                         catalysts[], upcoming[], risks[], sources[], veto: null | {reason}}}
  assessments        : {SYMBOL: str}   (final-assessment text for actionable cards)

Anything else is rejected. A veto moves an actionable setup to the watchlist with the reason
shown; nothing can add a setup, upgrade a status or change a number the engine produced.
"""
import argparse
import copy
import json
import sys

ALLOWED = {"regime_explanation", "commentary", "global_context", "news", "research", "assessments"}


def fail(msg):
    print(json.dumps(dict(status="rejected", error=msg)))
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--research", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rep = json.load(open(a.report))
    res = json.load(open(a.research))
    extra = set(res) - ALLOWED
    if extra:
        fail(f"research.json has fields it may not set: {sorted(extra)}")
    known = {o["symbol"] for o in rep["opportunities"] + rep["watchlist"]} | {r["symbol"] for r in rep["rejected"]}
    for s in (res.get("research") or {}):
        if s not in known:
            fail(f"research for {s}, which the engine did not shortlist")
    for n in res.get("news") or []:
        if not (n.get("source") and (n["source"].get("url") or n["source"].get("name"))):
            fail(f"news item without a source: {n.get('desc', '')[:60]}")
        if n.get("status") not in ("Confirmed", "Unconfirmed"):
            fail("news status must be Confirmed or Unconfirmed")
    for g in res.get("global_context") or []:
        if g.get("relevance") not in ("High", "Medium", "Low"):
            fail("global_context relevance must be High, Medium or Low")

    out = copy.deepcopy(rep)
    out["regime"]["explanation"] = res.get("regime_explanation")
    out["commentary"] = res.get("commentary")
    out["global_context"] = res.get("global_context") or []
    out["news"] = res.get("news") or []
    out["research"] = res.get("research") or {}
    vetoed = []
    keep = []
    for o in out["opportunities"]:
        v = (out["research"].get(o["symbol"]) or {}).get("veto")
        if v and v.get("reason"):
            o["blocked_by"] = [f"Research veto: {v['reason']}"]
            o["watch_status"] = "Confirmation pending"
            o["carried_forward"] = False
            o["rank"] = None
            out["watchlist"].insert(0, o)
            vetoed.append(o["symbol"])
        else:
            keep.append(o)
    for i, o in enumerate(keep):
        o["rank"] = i + 1
        o["assessment"] = (res.get("assessments") or {}).get(o["symbol"])
    out["opportunities"] = keep
    out["no_trade"] = len(keep) == 0
    out["research_applied"] = dict(vetoed=vetoed)

    # boundary check: every engine number for surviving setups is unchanged
    before = {o["symbol"]: o for o in rep["opportunities"]}
    for o in keep:
        b = before[o["symbol"]]
        for k in ("plan", "quality", "status", "strategy", "close", "support", "resistance", "evidence"):
            if o[k] != b[k]:
                fail(f"{o['symbol']}.{k} changed during research merge")
    json.dump(out, open(a.out, "w"), separators=(",", ":"))
    print(json.dumps(dict(status="ok", actionable=[o["symbol"] for o in keep], vetoed=vetoed,
                          news=len(out["news"]), researched=len(out["research"]))))


if __name__ == "__main__":
    main()
