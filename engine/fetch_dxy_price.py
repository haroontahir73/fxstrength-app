"""Daily close history for the dollar index (DX-Y.NYB), for dxy.py's trend leg.

Same Yahoo endpoint and the same keep-the-last-good-series-on-failure behaviour as
fetch_prices.py / fetch_index_prices.py. Its own file only because DXY is neither a
commodity nor an equity index and does not belong in either config list.

Writes data/prices_dxy.json.
"""
import json, datetime as dt
from config import DATA
from fetch_prices import _one

OUT = DATA / "prices_dxy.json"
SYMBOL = "DX-Y.NYB"


def main():
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8")).get("symbols", {})
        except Exception:
            prev = {}
    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "symbols": {}}
    try:
        d = _one(SYMBOL)
        out["symbols"]["DXY"] = d
        print(f"  DXY {SYMBOL} last {d['last']:>8.3f}  ({len(d['closes'])} bars, to {d['dates'][-1]})")
    except Exception as e:                                       # noqa: BLE001
        if prev.get("DXY", {}).get("closes"):
            kept = dict(prev["DXY"]); kept["stale"] = True
            out["symbols"]["DXY"] = kept
            print(f"  DXY fetch failed ({type(e).__name__}); kept cached to {kept['dates'][-1]}")
        else:
            out["symbols"]["DXY"] = {"error": str(e)}
            print(f"  DXY FAILED: {type(e).__name__}: {e}")
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
