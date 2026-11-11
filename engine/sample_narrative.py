"""Adds ILLUSTRATIVE research text to the sample report so every dashboard section has content.
Nothing here is real news, real fundamentals or a real company."""
import json
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "../out/report.json"
r = json.load(open(p))
S = dict(name="SAMPLE — illustrative text", url=None)
top = r["opportunities"][0]["symbol"] if r["opportunities"] else None

r["regime"]["explanation"] = (
    "Illustrative: the index sits above its 50 DMA with a rising slope and two-thirds of the universe is "
    "above its own 50 DMA, so participation is broad. Against that, the 10-day advance/decline average is "
    "below 1 and FII cash flows were negative over five sessions, which keeps this a moderate rather than "
    "strong bullish read.")

r["commentary"] = dict(
    stance="Selective long — one confirmed setup; most candidates still need confirmation.",
    generated_at=r["created_at"],
    sections=[
        dict(title="Market regime", facts=[f"Rule score {r['regime']['score']} → {r['regime']['label']}"],
             interpretation="Breadth supports longs; flows and the 10-day A/D argue for smaller size and "
                            "waiting for confirmation rather than anticipating."),
        dict(title="Index movements", facts=["Large caps and mid caps both closed above their 20 DMA (sample)."],
             interpretation="No divergence between size segments, so leadership is not narrowly concentrated."),
        dict(title="Breadth", facts=[f"{r['breadth']['adv']} advances vs {r['breadth']['dec']} declines",
                                    f"{r['breadth']['above50']}% above 50 DMA"],
             interpretation="A strong one-day A/D ratio after a weak 10-day average is a first sign of repair, "
                            "not yet a trend."),
        dict(title="Sectors", facts=[f"Leaders by 1-month RS: {', '.join(s['name'] for s in r['sectors'][:3])}"],
             interpretation="Leadership has persisted on the monthly window, which matters more than today's move."),
        dict(title="Institutional activity", facts=["FII net sellers, DII net buyers (sample, provisional)."],
             interpretation="Domestic buying is absorbing foreign selling; watch whether FII selling eases."),
        dict(title="Global and domestic", facts=["Illustrative: no scheduled central-bank decision in the next session."],
             interpretation="Low event risk overnight reduces gap risk for new entries."),
        dict(title="Company news", facts=["See News & Catalysts — all sample items are fictional."],
             interpretation="No headline on its own justifies a trade."),
        dict(title="Best setups", facts=[f"{top}: shallow pullback confirmed on volume"] if top else ["None"],
             interpretation="Only setups whose trigger, stop and R:R pass the rules appear as actionable."),
        dict(title="Risks for next session", facts=["Gap-up above the max-chase price would void the R:R."],
             interpretation="Use the gap rule on each card; skip rather than chase."),
        dict(title="Trading stance", facts=["1 actionable, others on watch"],
             interpretation="Selective participation; let the watchlist come to its triggers."),
    ])

r["global_context"] = [
    dict(topic="US markets", fact="Illustrative: US indices closed mixed.", relevance="Low",
         note="No large overnight move to transmit.", source=S),
    dict(topic="Crude oil", fact="Illustrative: Brent steady week on week.", relevance="Medium",
         note="Stable crude is neutral for OMCs and paints.", source=S),
    dict(topic="USD/INR", fact="Illustrative: rupee range-bound.", relevance="Low", note="", source=S),
    dict(topic="Upcoming events", fact="Illustrative: domestic CPI print later this week.", relevance="Medium",
         note="Could move rate-sensitive sectors.", source=S),
]

r["news"] = [
    dict(announced=r["market_date"], event_date=None, symbol=top or "XBRK", company="Sample company",
         desc="Illustrative: order win announced via exchange filing.", status="Confirmed",
         category="New order", source=S, impact="Moderate positive", priced_in="Partly — stock rose on the day"),
    dict(announced=r["market_date"], event_date="2026-11-14", symbol="XLMN3", company="Sample company",
         desc="Illustrative: board meeting for quarterly results.", status="Confirmed",
         category="Earnings", source=S, impact="Event risk", priced_in="n/a"),
    dict(announced=r["market_date"], event_date=None, symbol="XSKY1", company="Sample company",
         desc="Illustrative: media report of a possible stake sale.", status="Unconfirmed",
         category="M&A", source=S, impact="Uncertain", priced_in="Unknown"),
]

research = {}
for o in r["opportunities"] + r["watchlist"][:5]:
    research[o["symbol"]] = dict(
        summary="Illustrative fundamentals for a fictional company.",
        fundamentals=dict(as_of="Q2 FY27 (sample)", revenue_yoy=14.2, ebitda_margin=18.5, pat_yoy=19.0,
                          roe=17.1, roce=20.4, de=0.3, interest_cover=11.0, ocf_vs_pat="0.9x"),
        valuation=dict(pe=31.0, pb=5.1, ev_ebitda=18.2, note="Premium to peers, supported by growth (sample)."),
        governance=dict(pledge_pct=0.0, promoter_change="+0.2 pp QoQ", flags=[]),
        catalysts=["Illustrative order inflow"], upcoming=["Results date (sample)"],
        risks=["Sample risk: raw-material cost pass-through"],
        veto=None, sources=[S])
r["research"] = research
json.dump(r, open(p, "w"), separators=(",", ":"))
print("narrative added")
