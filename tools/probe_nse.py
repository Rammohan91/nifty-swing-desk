"""Probe round 3: save raw XBRL files that the parser could not read, for offline inspection."""
import os, time
try:
    from curl_cffi import requests as creq
    S = creq.Session(impersonate="chrome")
except Exception:
    import requests as creq
    S = creq.Session()
os.makedirs("probe3", exist_ok=True)
for name, url in [
    ("shyam_q4", "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_INDAS_1665720_11052026083814_WEB.xml"),
    ("hdfcbank_q2", "https://nsearchives.nseindia.com/corporate/xbrl/INTEGRATED_FILING_BANKING_1740913_17102026054812_WEB.xml"),
]:
    r = S.get(url, timeout=60)
    print(name, r.status_code, len(r.content))
    open(f"probe3/{name}.xml", "wb").write(r.content)
    time.sleep(1)
