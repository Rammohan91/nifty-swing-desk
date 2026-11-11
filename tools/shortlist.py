"""Write the engine's shortlist (actionable + watchlist symbols) for the fundamentals fetcher."""
import json, sys
r = json.load(open(sys.argv[1]))
syms = [o["symbol"] for o in r["opportunities"]] + [o["symbol"] for o in r["watchlist"]]
json.dump(dict(market_date=r["market_date"], symbols=syms), open(sys.argv[2], "w"))
print(f"shortlist {r['market_date']}: {len(syms)} symbols")
