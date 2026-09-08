"""The market strip: a handful of live quotes for context, across the top of the page.

The board says what the bias IS. It never says what the market is actually DOING right now,
so you had to leave the page to find out whether the dollar was bid or equities were falling.
This is that one line: the dollar, the two biggest crosses, the US 10-year, the two US indices,
the metals, oil, and the VIX.

Deliberately NOT scored and NOT wired into anything. It is context, not signal - nothing here
feeds a score, and no other module reads this file. That is why it can afford to be the one
part of the desk that fails silently: if a quote is missing the strip just drops that chip.

Source is TradingView's scanner symbol endpoint, the same host the calendar and yields already
come from - `close` and `change` (a percentage) in a single call per symbol.

Writes data/ticker.json.
"""
import json, urllib.parse, urllib.request, datetime as dt
from config import DATA

API = "https://scanner.tradingview.com/symbol?symbol={sym}&fields=close,change&no_404=true"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
OUT = DATA / "ticker.json"

# (label, TradingView symbol, decimal places). Order is the order on the strip.
# Every one of these was checked against the endpoint - the obvious-looking TVC:SPX,
# TVC:NDX and TVC:USOIL all return no data, hence the exchange-qualified symbols.
SYMBOLS = [
    ("DXY",     "TVC:DXY",     2),
    ("EUR/USD", "FX:EURUSD",   4),
    ("USD/JPY", "FX:USDJPY",   2),
    ("US 10Y",  "TVC:US10Y",   2),
    ("S&P 500", "SP:SPX",      0),
    ("NASDAQ",  "NASDAQ:NDX",  0),
    ("GOLD",    "TVC:GOLD",    0),
    ("WTI",     "NYMEX:CL1!",  2),
    ("VIX",     "TVC:VIX",     2),
]


def _quote(sym):
    url = API.format(sym=urllib.parse.quote(sym))
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()
    d = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(d, dict) or d.get("close") is None:
        raise RuntimeError("no data")
    return float(d["close"]), (float(d["change"]) if d.get("change") is not None else None)


def build():
    rows, failed = [], []
    for label, sym, dp in SYMBOLS:
        try:
            close, chg = _quote(sym)
            rows.append({"label": label, "symbol": sym, "dp": dp,
                         "last": round(close, dp), "chg_pct": round(chg, 2) if chg is not None else None})
        except Exception as e:                                   # noqa: BLE001
            failed.append(f"{label} ({type(e).__name__})")
    if failed:
        print(f"  dropped: {', '.join(failed)}")
    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "quotes": rows}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    for q in r["quotes"]:
        c = f"{q['chg_pct']:+.2f}%" if q["chg_pct"] is not None else "   n/a"
        print(f"  {q['label']:9} {q['last']:>12,.{q['dp']}f}  {c}")
