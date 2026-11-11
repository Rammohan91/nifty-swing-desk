"""
Daily NSE end-of-day fetcher for the Nifty 500 Swing Desk.

Runs in GitHub Actions (or anywhere with Python 3.10+ and `requests`).
Writes normalised CSVs into ./data, which the workflow commits:

  data/bhav/YYYYMMDD.csv     SYMBOL,OPEN,HIGH,LOW,CLOSE,VOLUME,DELIV_PER,PREV_CLOSE   (EQ series)
  data/index/YYYYMMDD.csv    INDEX,OPEN,HIGH,LOW,CLOSE,VOLUME
  data/fiidii/YYYYMMDD.json  provisional FII/DII cash figures, when NSE serves them
  data/nifty500.csv          current constituents (Symbol, Company Name, Industry)
  data/sessions.json         {"YYYY-MM-DD": "traded" | "no_session"} for every weekday checked
  data/last_fetch.json       status of this run

A weekday with no bhavcopy is recorded as "no_session" (holiday) only after the
publish window has passed; earlier it is "pending" so a later retry can fill it.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import sys
import time
import zipfile

import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DATA = os.environ.get("DATA_DIR", "data")
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "420"))   # calendar days; ~290 sessions
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36",
     "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}
S = requests.Session()
S.headers.update(H)


def get(url, tries=3, timeout=30):
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


def write_csv(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def num(x):
    x = (x or "").strip().replace(",", "")
    try:
        return float(x)
    except ValueError:
        return None


def has_prev_close(path) -> bool:
    """Files from the first version lack PREV_CLOSE (NSE's split/bonus-adjusted previous close);
    those are downloaded again once so the engine can adjust prices exactly."""
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return "PREV_CLOSE" in f.readline()


def fetch_bhav(d: dt.date) -> bool:
    """sec_bhavdata_full has OHLC, volume and delivery % in one file."""
    path = f"{DATA}/bhav/{d:%Y%m%d}.csv"
    if has_prev_close(path):
        return True
    r = get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
    if not r:
        return False
    rows = []
    rd = csv.DictReader(io.StringIO(r.text))
    rd.fieldnames = [c.strip() for c in rd.fieldnames]
    for x in rd:
        x = {k.strip(): (v or "").strip() for k, v in x.items() if k}
        if x.get("SERIES") != "EQ":
            continue
        rows.append([x["SYMBOL"], num(x["OPEN_PRICE"]), num(x["HIGH_PRICE"]), num(x["LOW_PRICE"]),
                     num(x["CLOSE_PRICE"]), num(x["TTL_TRD_QNTY"]), num(x.get("DELIV_PER")),
                     num(x.get("PREV_CLOSE"))])
    if len(rows) < 500:          # partial or malformed file: do not store
        return False
    write_csv(path, ["SYMBOL", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "DELIV_PER", "PREV_CLOSE"], rows)
    return True


def fetch_index(d: dt.date) -> bool:
    path = f"{DATA}/index/{d:%Y%m%d}.csv"
    if os.path.exists(path):
        return True
    r = get(f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv")
    if not r:
        return False
    rows = []
    rd = csv.DictReader(io.StringIO(r.text))
    rd.fieldnames = [c.strip() for c in rd.fieldnames]
    for x in rd:
        x = {k.strip(): (v or "").strip() for k, v in x.items() if k}
        rows.append([x["Index Name"], num(x["Open Index Value"]), num(x["High Index Value"]),
                     num(x["Low Index Value"]), num(x["Closing Index Value"]), num(x.get("Volume"))])
    if not rows:
        return False
    write_csv(path, ["INDEX", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"], rows)
    return True


def fetch_constituents():
    r = get("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv")
    if r and "Symbol" in r.text[:300]:
        os.makedirs(DATA, exist_ok=True)
        open(f"{DATA}/nifty500.csv", "w").write(r.text)
        return True
    return False


def fetch_fiidii(d: dt.date):
    """Provisional figures from NSE's site API. Needs a cookie from the home page first."""
    try:
        S.get("https://www.nseindia.com/", timeout=20)
        r = S.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=20)
        data = r.json()
    except Exception:
        return False
    out = dict(status="provisional", retrieved_at=dt.datetime.now(IST).isoformat(timespec="seconds"),
               source="NSE fiidiiTradeReact")
    for row in data:
        cat = (row.get("category") or "").upper()
        date = row.get("date")
        val = num(str(row.get("netValue")))
        if "FII" in cat or "FPI" in cat:
            out["fii_cash_cr"], out["fii_date"] = val, date
        elif "DII" in cat:
            out["dii_cash_cr"], out["dii_date"] = val, date
    want = d.strftime("%d-%b-%Y")
    if out.get("fii_date") != want:
        return False            # NSE has not published today's figures yet; do not mislabel yesterday's
    # rolling 5-session FII net, only once five sessions are actually on file (today included)
    hist = [out.get("fii_cash_cr") or 0]
    p = f"{DATA}/fiidii"
    today_file = f"{d:%Y%m%d}.json"
    if os.path.isdir(p):
        for f in sorted(x for x in os.listdir(p) if x < today_file)[-4:]:
            try:
                hist.append(json.load(open(os.path.join(p, f))).get("fii_cash_cr") or 0)
            except Exception:
                pass
    out["fii_days_on_file"] = len(hist)
    out["fii_5d_net_cr"] = round(sum(hist), 1) if len(hist) == 5 else None
    os.makedirs(p, exist_ok=True)
    json.dump(out, open(f"{p}/{d:%Y%m%d}.json", "w"))
    return True


def _closes(path):
    with open(path) as f:
        return {r["SYMBOL"]: (r["CLOSE"], r["VOLUME"]) for r in csv.DictReader(f)}


def repeats_previous_session(d: dt.date) -> bool:
    """On NSE holidays the bhavcopy URL serves the previous session's file again.
    A file whose closes AND volumes match the prior stored file for ≥98% of symbols is that repeat."""
    path = f"{DATA}/bhav/{d:%Y%m%d}.csv"
    prev = sorted(f for f in os.listdir(f"{DATA}/bhav") if f < f"{d:%Y%m%d}.csv")
    if not prev or not os.path.exists(path):
        return False
    a, b = _closes(f"{DATA}/bhav/{prev[-1]}"), _closes(path)
    common = set(a) & set(b)
    return bool(common) and sum(a[s] == b[s] for s in common) / len(common) >= 0.98


def drop_bhav(d: dt.date):
    for sub in ("bhav", "index"):
        p = f"{DATA}/{sub}/{d:%Y%m%d}.csv"
        if os.path.exists(p):
            os.remove(p)


def main():
    now = dt.datetime.now(IST)
    today = now.date()
    sessions_p = f"{DATA}/sessions.json"
    sessions = json.load(open(sessions_p)) if os.path.exists(sessions_p) else {}
    got_const = fetch_constituents() or os.path.exists(f"{DATA}/nifty500.csv")
    results = {}
    # repair: earlier versions stored holiday repeats as "partial"
    for key, st in list(sessions.items()):
        dd = dt.date.fromisoformat(key)
        if st == "partial" and repeats_previous_session(dd):
            drop_bhav(dd)
            sessions[key] = "no_session"
            results[key] = "no_session (removed repeated file)"
    d = today - dt.timedelta(days=BACKFILL_DAYS)
    while d <= today:
        key = d.isoformat()
        # weekends are checked too: special sessions (e.g. Diwali Muhurat trading) happen on them;
        # a weekend with no file, or one that repeats the prior session, is recorded as no_session
        if sessions.get(key) != "no_session":
            if sessions.get(key) == "traded" and has_prev_close(f"{DATA}/bhav/{d:%Y%m%d}.csv") \
                    and os.path.exists(f"{DATA}/index/{d:%Y%m%d}.csv"):
                pass
            else:
                b, i = fetch_bhav(d), fetch_index(d)
                if b and repeats_previous_session(d):
                    drop_bhav(d)
                    b = i = False
                    sessions[key] = "no_session"
                elif b and i:
                    sessions[key] = "traded"
                elif d < today or now.hour >= 20:
                    sessions[key] = "no_session" if not b else "partial"
                else:
                    sessions[key] = "pending"
                results[key] = sessions[key]
                time.sleep(0.4)
        d += dt.timedelta(days=1)
    fii = fetch_fiidii(today) if sessions.get(today.isoformat()) == "traded" else False
    os.makedirs(DATA, exist_ok=True)
    json.dump(dict(sorted(sessions.items())), open(sessions_p, "w"), indent=0)
    status = dict(run_at=now.isoformat(timespec="seconds"), today=today.isoformat(),
                  today_status=sessions.get(today.isoformat(), "weekend"), constituents=got_const,
                  fiidii=fii, fetched=results)
    json.dump(status, open(f"{DATA}/last_fetch.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in status.items() if k != "fetched"}), f"fetched={len(results)}")


if __name__ == "__main__":
    sys.exit(main())
