# Nifty 500 Swing Desk

Data feed, rule engine and daily-run instructions behind the Claude dashboard.

| Piece | Where it runs | What it does |
|---|---|---|
| `fetch_nse.py` + `.github/workflows/fetch.yml` | GitHub Actions, 18:35 and 19:35 IST Mon–Fri | Downloads NSE bhavcopy (with delivery %), index closes, Nifty 500 constituents and provisional FII/DII into `data/`. First run backfills ~14 months. |
| `engine/` | Claude's daily scheduled task, 19:45 IST | `build_report.py` scans the universe with fixed rules (strategies A–E, levels, quality score, regime). `evaluate.py` marks past signals and your trades to market. `apply_research.py` merges Claude's research and enforces that research can only add text or veto a setup. |
| `RUNBOOK.md` | — | The exact prompt the scheduled task runs. |

## Setup (one time)

1. Create a **public** repo `Rammohan91/nifty-swing-desk` (public so Claude's cloud session can clone it without credentials; it holds only public NSE data and this code).
2. Push this folder's contents to it.
3. Repo → Actions → enable workflows → run **Fetch NSE end-of-day data** once by hand. The backfill takes 10–20 minutes.
4. Check `data/last_fetch.json`. If `today_status` is `traded` and `data/bhav/` has files, the feed works.

If NSE blocks GitHub's runners (it rate-limits some cloud IPs), the fetch shows `no_session` for days that did trade. Run `fetch_nse.py` from your own machine or the Vultr VPS on a cron instead and push `data/`.

## Rules in one place

`engine/engine.py` → `P` holds every threshold (liquidity, volume, R:R, extension, quality cut-off). Change a number there, push, and the next run uses it; the report records the engine version and parameters it ran with.

Unadjusted prices: bhavcopy is not split/bonus-adjusted. Any stock with an overnight move above 40% in the last year is listed under Data Health and excluded from setups until it ages out.
