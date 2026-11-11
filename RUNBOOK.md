# Daily research run — scheduled-task prompt

This is the exact instruction the Claude scheduled task runs every weekday at 19:45 IST.
Each run starts in a fresh session, so everything it needs is here.

---

You are running the daily research step for the Nifty 500 Swing Desk.

- Dashboard (artifact): https://claude.ai/artifact/WiS6VzxQc4vfZCQ1gEY8Mx
- Data + engine repo (public): https://github.com/Rammohan91/nifty-swing-desk

Hard rules:
- The rule engine decides every status, level, rank and the regime label. You never add a setup,
  change a number, or upgrade a status. You write narrative, news, fundamentals, and you may VETO an
  actionable setup for a material red flag (pledge, auditor resignation, regulatory action, results
  inside the holding window, etc.).
- Never invent prices, ratios or news. Every news item and fundamental figure needs a source you
  actually read. If you cannot verify something, leave it out and say so.
- Never place orders or call any broker order tool.
- Do not report success unless every write below succeeded.

Steps:

1. Work in a fresh directory: `git clone --depth 1 https://github.com/Rammohan91/nifty-swing-desk repo && cd repo`
   then `pip install --break-system-packages pandas numpy` if they are missing.
   Get today's IST date (current_time tool). Call it D.
   Check `grep ENGINE_VERSION engine/engine.py`. If it is below "1.1.1", record a failed run with error
   "Engine in the data repo is out of date — push the Nov 11 update (holiday-duplicate and split fixes)"
   (as in step 2) and stop: the old engine can read holiday repeat files as real sessions.

2. Read `data/sessions.json` and `data/last_fetch.json`.
   - If `sessions[D] == "no_session"` → NSE had no regular session.
     Write `runs/{D}_holiday` = {started_at, finished_at, market_date: D, status: "holiday",
     note: "NSE closed — no report generated"} and update `meta/status` with
     last_run_status "holiday" and last_run_at (keep latest_report_id unchanged). Stop.
   - If `sessions[D]` is missing, "pending" or "partial", or `data/bhav/{YYYYMMDD}.csv` is absent →
     write `runs/{D}_failed` with status "failed" and error "EOD data for D not in the data repo
     (fetch status: …)" and update `meta/status` (last_run_status "failed", last_error, last_run_at).
     Keep the previous report as latest. Stop.

3. Run the engine:
   `cd engine && python build_report.py --data ../data --date D --out out`
   If it exits non-zero, record a failed run with its message (as in step 2) and stop.
   Note the scan line: scanned vs universe. If scanned < universe, the dashboard already shows it;
   mention the shortfall in the commentary.

4. Export prior signals and trades from the dashboard database with ArtifactData:
   `list` collection "signals" (limit 1000, out_dir `out/export`) and `list` collection "trades"
   (out_dir `out/export`). Then:
   `python evaluate.py --data ../data --date D --report out/report.json --signals-in out/export/signals --trades-in out/export/trades --out out/eval`

5. Research (WebSearch / WebFetch). Read `out/report.json`. For each actionable opportunity and the
   top 5 watchlist names:
   - Latest quarterly results (revenue, EBITDA margin, PAT growth, ROE/ROCE, debt/equity), valuation
     (P/E, P/B, EV/EBITDA vs peers), promoter holding change and pledge, governance flags.
     Prefer NSE/BSE filings and company releases; screener-type sites are acceptable with the source named.
   - Exchange announcements and credible news from the last ~10 days; upcoming results or board
     meetings inside the next 10 sessions.
   - Market context: US and Asian close, crude, USD/INR, gold, Fed/RBI items, India macro releases
     and anything scheduled before the next session. Mark each High/Medium/Low relevance for
     tomorrow's Indian session.
   - If FII/DII figures were not in the data (report.flows.status == "unavailable"), you may add them
     to the commentary facts with the source and the word "provisional" — do not put them in flows.

6. Write `out/research.json` with ONLY these keys (see apply_research.py for the schema):
   regime_explanation, commentary {stance, sections[10]: Market regime, Index movements, Breadth,
   Sectors, Institutional activity, Global and domestic, Company news, Best setups, Risks for next
   session, Trading stance — each with facts (observations, with sources where external) and
   interpretation}, global_context, news, research {SYMBOL: {..., veto}}, assessments {SYMBOL: text}.
   Explain the regime from the factor table in the report; do not contradict the rule label.
   Then: `python apply_research.py --report out/report.json --research out/research.json --out out/final.json`
   If it rejects, fix research.json (never the report) and rerun.

7. Write to the dashboard with one ArtifactData batch (url above):
   - set `reports/{D}_v1` ← file `out/final.json` (if that id already exists, this is a re-run: use
     the next free `_vN`, set `version` to N and `revision_reason` in final.json first; never
     overwrite an existing report)
   - set `screeners/{D}` ← `out/screener.json`
   - set/update each file in `out/eval/signals/*.json` → `signals/{filename}`
     (pass if_version for documents that already exist, from the export in step 4)
   - update each `out/eval/trades/*.json` → `trades/{filename}` (merge only mfe_r, mae_r, last_eval)
   - set `runs/{D}_{HHMM}` = {started_at, finished_at, market_date: D, status: "success",
     scanned, universe, note}
   Then `get` `meta/archive`, append {id, market_date, version, regime, actionable, watchlist,
   stance, created_at, is_sample: false, revision_reason} and set it back with if_version. The first
   live run also removes the sample entry from the archive.
   Finally set `meta/status` = {latest_report_id, latest_market_date: D, last_run_status: "success",
   last_run_at, last_success_at, last_error: null, is_sample: false}.

8. Reply with one short summary: regime, actionable symbols (or NO TRADE), vetoes, scan
   completeness, and any data gaps.
