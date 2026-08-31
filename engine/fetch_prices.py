"""Daily close history for the commodity track.

The FX side needs no price feed of its own - validate.py pulls ECB reference rates from
Frankfurter. Metals and oil are not in that feed, and every free daily bar source the
README lists for open interest is equally walled here (Stooq's proof-of-work page included).

Yahoo Finance's chart endpoint does answer, with a browser UA, for the front-month futures
GC=F / SI=F / CL=F. That is enough for a 50-day moving average, a 20-day slope, and for
validate.py to check the commodity score against realised price.

Writes data/prices_commodity.json:
  {"fetched_at": iso, "symbols": {sym: {"dates": [...], "closes": [...], "last": float}}}
oldest first, business days only (Yahoo already drops weekends). On a fetch failure the last
good series for that symbol is kept and marked "stale": true rather than wiped.
"""
import json, urllib.request, datetime as dt
from config import DATA, COMMODITIES

# query1 sometimes 429s or drops the connection; query2 is the same API on another host.
HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
CHART = "{host}/v8/finance/chart/{sym}?range=6mo&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch(yahoo):
    last_err = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(CHART.format(host=host, sym=yahoo), headers=UA)
            return urllib.request.urlopen(req, timeout=40).read()
        except Exception as e:                      # noqa: BLE001 - try the next host
            last_err = e
    raise last_err


def _one(yahoo):
    payload = json.loads(_fetch(yahoo).decode("utf-8", "replace"))
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("Yahoo returned no result block")
    res = results[0]
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    pairs = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), c)
             for t, c in zip(ts, q) if c is not None]
    if not pairs:
        raise RuntimeError("Yahoo returned no usable closes")
    return {"dates": [d for d, _ in pairs], "closes": [round(c, 4) for _, c in pairs],
            "last": round(pairs[-1][1], 4)}


def main():
    cache_file = DATA / "prices_commodity.json"
    prev = {}
    if cache_file.exists():
        try:
            prev = json.loads(cache_file.read_text(encoding="utf-8")).get("symbols", {})
        except Exception:
            prev = {}

    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "symbols": {}}
    for sym, meta in COMMODITIES.items():
        try:
            d = _one(meta["yahoo"])
            out["symbols"][sym] = d
            print(f"  {sym} {meta['yahoo']:5} last {d['last']:>10}  ({len(d['closes'])} bars, "
                  f"to {d['dates'][-1]})")
        except Exception as e:
            # keep the last good series rather than wiping it on a transient Yahoo failure
            if prev.get(sym, {}).get("closes"):
                kept = dict(prev[sym])
                kept["stale"] = True
                out["symbols"][sym] = kept
                print(f"  {sym} fetch failed ({type(e).__name__}); kept cached "
                      f"{len(kept['closes'])} bars to {kept['dates'][-1]}")
            else:
                out["symbols"][sym] = {"error": str(e)}
                print(f"  {sym} FAILED: {type(e).__name__}: {e}")
    cache_file.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
