"""Daily close history for the equity-index track (S&P 500, Nasdaq 100, Dow).

Same Yahoo chart endpoint and the same failure behaviour as fetch_prices.py - on a fetch
error the last good series for that symbol is kept and marked stale rather than wiped - so
the two live side by side and neither can take the other down. Split into its own file only
because the index track has its own config list and its own output file.

Writes data/prices_index.json:
  {"fetched_at": iso, "symbols": {sym: {"dates": [...], "closes": [...], "last": float}}}
"""
import json, datetime as dt
from config import DATA, INDICES
from fetch_prices import _one

OUT = DATA / "prices_index.json"


def main():
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8")).get("symbols", {})
        except Exception:
            prev = {}

    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "symbols": {}}
    for sym, meta in INDICES.items():
        try:
            d = _one(meta["yahoo"])
            out["symbols"][sym] = d
            print(f"  {sym} {meta['yahoo']:6} last {d['last']:>10,.2f}  ({len(d['closes'])} bars, "
                  f"to {d['dates'][-1]})")
        except Exception as e:                                   # noqa: BLE001
            if prev.get(sym, {}).get("closes"):
                kept = dict(prev[sym])
                kept["stale"] = True
                out["symbols"][sym] = kept
                print(f"  {sym} fetch failed ({type(e).__name__}); kept cached "
                      f"{len(kept['closes'])} bars to {kept['dates'][-1]}")
            else:
                out["symbols"][sym] = {"error": str(e)}
                print(f"  {sym} FAILED: {type(e).__name__}: {e}")
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
