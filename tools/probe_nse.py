"""Probe round 2: integrated-filing results, results comparison, and quote API via curl_cffi."""
import json
import os
import time

OUT = "probe2"
os.makedirs(OUT, exist_ok=True)
log = []

try:
    from curl_cffi import requests as creq
    S = creq.Session(impersonate="chrome")
    engine = "curl_cffi"
except Exception:
    import requests as creq
    S = creq.Session()
    engine = "requests"
S.headers.update({"Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
                  "Referer": "https://www.nseindia.com/get-quotes/equity?symbol=SAILIFE"})


def hit(name, url):
    try:
        r = S.get(url, timeout=30)
        ct = r.headers.get("content-type", "")
        ext = "xml" if "xml" in ct else "json" if "json" in ct else "txt"
        open(f"{OUT}/{name}.{ext}", "wb").write(r.content[:3_000_000])
        log.append(dict(name=name, url=url, status=r.status_code, bytes=len(r.content), ctype=ct))
        return r if r.status_code == 200 else None
    except Exception as e:
        log.append(dict(name=name, url=url, error=str(e)[:200]))
    finally:
        time.sleep(1.2)


S.get("https://www.nseindia.com/", timeout=30)
time.sleep(1)
S.get("https://www.nseindia.com/get-quotes/equity?symbol=SAILIFE", timeout=30)
time.sleep(1)
sym = "SAILIFE"
hit("quote", f"https://www.nseindia.com/api/quote-equity?symbol={sym}")
hit("results_comparison", f"https://www.nseindia.com/api/results-comparision?symbol={sym}")
hit("if_results_1", f"https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol={sym}")
hit("if_results_2", "https://www.nseindia.com/api/integrated-filing-results?index=equities"
                    f"&type=Integrated%20Filing-%20Financials&symbol={sym}")
hit("if_results_3", f"https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol={sym}&period_ended=all")
hit("fin_results_new", "https://www.nseindia.com/api/corporates-financial-results?index=equities"
                       f"&symbol={sym}&period=Quarterly&from_date=01-01-2025&to_date=11-11-2026")
hit("fin_results_all", f"https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={sym}")
hit("annual_reports", f"https://www.nseindia.com/api/annual-reports?index=equities&symbol={sym}")
hit("event_calendar", f"https://www.nseindia.com/api/event-calendar?index=equities&symbol={sym}")
# follow any XBRL links found in integrated-filing responses
for n in ("if_results_1", "if_results_2", "if_results_3"):
    p = f"{OUT}/{n}.json"
    if os.path.exists(p):
        try:
            d = json.load(open(p))
            rows = d.get("data", d) if isinstance(d, dict) else d
            log.append(dict(name=n + "_shape", type=type(d).__name__,
                            keys=list(d.keys())[:20] if isinstance(d, dict) else None,
                            n=len(rows) if isinstance(rows, list) else None,
                            first=rows[0] if isinstance(rows, list) and rows else None))
            if isinstance(rows, list):
                for i, row in enumerate(rows[:2]):
                    for k, v in row.items():
                        if isinstance(v, str) and v.endswith(".xml"):
                            hit(f"{n}_xbrl{i}", v if v.startswith("http") else "https://nsearchives.nseindia.com" + v)
                            break
        except Exception as e:
            log.append(dict(name=n + "_parse", error=str(e)[:200]))
log.insert(0, dict(engine=engine))
json.dump(log, open(f"{OUT}/_log.json", "w"), indent=1, default=str)
for x in log:
    print(json.dumps(x, default=str)[:400])
