"""One-off probe: which NSE fundamentals endpoints answer from this runner, and what do they return.
Saves raw responses under probe/ so they can be inspected offline."""
import json
import os
import re
import time

import requests

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36",
     "Accept": "application/json,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9",
     "Referer": "https://www.nseindia.com/get-quotes/equity"}
S = requests.Session()
S.headers.update(H)
OUT = "probe"
os.makedirs(OUT, exist_ok=True)
log = []


def hit(name, url, binary=False):
    try:
        r = S.get(url, timeout=30)
        ok = r.status_code == 200
        ext = "xml" if binary or "xml" in r.headers.get("content-type", "") else "json"
        path = f"{OUT}/{name}.{ext}"
        open(path, "wb").write(r.content[:3_000_000])
        log.append(dict(name=name, url=url, status=r.status_code, bytes=len(r.content),
                        ctype=r.headers.get("content-type")))
        return r if ok else None
    except Exception as e:
        log.append(dict(name=name, url=url, error=str(e)))
        return None
    finally:
        time.sleep(1)


S.get("https://www.nseindia.com/", timeout=30)
time.sleep(1)
S.get("https://www.nseindia.com/get-quotes/equity?symbol=SAILIFE", timeout=30)
for sym in ["SAILIFE", "SHYAMMETL"]:
    hit(f"{sym}_quote", f"https://www.nseindia.com/api/quote-equity?symbol={sym}")
    hit(f"{sym}_trade_info", f"https://www.nseindia.com/api/quote-equity?symbol={sym}&section=trade_info")
    r = hit(f"{sym}_results", "https://www.nseindia.com/api/corporates-financial-results?"
                              f"index=equities&symbol={sym}&period=Quarterly")
    if r:
        try:
            rows = r.json()
            log.append(dict(name=f"{sym}_results_count", n=len(rows), keys=list(rows[0].keys()) if rows else []))
            # first consolidated + standalone XBRL links
            got = 0
            for row in rows:
                x = row.get("xbrl") or ""
                if x.startswith("http") and got < 2:
                    hit(f"{sym}_xbrl_{got}_{(row.get('consolidated') or 'na')[:4]}", x, binary=True)
                    got += 1
        except Exception as e:
            log.append(dict(name=f"{sym}_results_parse", error=str(e)))
    hit(f"{sym}_shp", f"https://www.nseindia.com/api/corporate-share-holdings-master?index=equities&symbol={sym}")
    hit(f"{sym}_pledge", f"https://www.nseindia.com/api/corporate-pledgedata?index=equities&symbol={sym}")
    hit(f"{sym}_annc", f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={sym}")

json.dump(log, open(f"{OUT}/_log.json", "w"), indent=1)
for x in log:
    print(json.dumps(x)[:300])
