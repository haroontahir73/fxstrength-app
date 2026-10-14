"""Daily OHLC bars for the Step 3 backtest (one fixed instrument per market).

    python fetch_bt_prices.py [--out data/bt_prices.json] [--range 2y]

Source: Yahoo Finance chart API (the project's existing daily price source, fetch_prices.py),
with the instrument fixed per market in divergence_backtest_config.PRICES. Writes, oldest first:
  {"fetched_at", "source", "markets": {KEY: {"symbol", "what", "timezone", "price_type",
   "dates", "open", "high", "low", "close"}}}
`dates` are the bar's trading date in the exchange's own time zone (Yahoo gmtoffset). Only
trading days exist (no weekend or holiday bars are invented); a missing value stays null and
the backtest marks any horizon touching it "missing". Never contacts Myfxbook.
"""
import argparse, datetime as dt, json, urllib.request
from pathlib import Path

import divergence_backtest_config as bc
from config import DATA

HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
CHART = "{host}/v8/finance/chart/{sym}?range={rng}&interval=1d&includePrePost=false&events=div%2Csplits"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _get(sym, rng, opener=None):
    opener = opener or urllib.request.urlopen
    err = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(CHART.format(host=host, sym=urllib.request.quote(sym), rng=rng), headers=UA)
            with opener(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:                                   # noqa: BLE001
            err = e
    raise err


def parse(payload):
    res = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not res:
        raise RuntimeError("no result block")
    meta = res.get("meta") or {}
    off = int(meta.get("gmtoffset") or 0)
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    rows = {}
    for i, t in enumerate(res.get("timestamp") or []):
        d = (dt.datetime.fromtimestamp(t + off, dt.timezone.utc)).date().isoformat()
        vals = [(q.get(k) or [None] * (i + 1))[i] for k in ("open", "high", "low", "close")]
        vals = [round(v, 6) if isinstance(v, (int, float)) else None for v in vals]
        if all(v is None for v in vals):
            continue                                   # a placeholder row, not a bar
        rows[d] = vals                                 # one bar per trading date (last wins)
    dates = sorted(rows)
    return {"timezone": meta.get("exchangeTimezoneName"), "price_type": "daily OHLC, unadjusted",
            "dates": dates, **{k: [rows[d][j] for d in dates] for j, k in enumerate(("open", "high", "low", "close"))}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA / "bt_prices.json"))
    ap.add_argument("--range", default="2y")
    a = ap.parse_args(argv)
    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "source": bc.PRICE_SOURCE, "markets": {}}
    bad = 0
    for k, spec in bc.PRICES.items():
        if not spec:
            continue
        try:
            s = parse(_get(spec["symbol"], a.range))
            out["markets"][k] = {"symbol": spec["symbol"], "what": spec["what"], **s}
            print(f"  {k:4} {spec['symbol']:9} {len(s['dates'])} bars {s['dates'][0]} .. {s['dates'][-1]}")
        except Exception as e:                                   # noqa: BLE001
            bad += 1
            out["markets"][k] = {"symbol": spec["symbol"], "what": spec["what"], "error": f"{type(e).__name__}"}
            print(f"  {k:4} {spec['symbol']:9} FAILED ({type(e).__name__}) - marked missing, nothing substituted")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, sort_keys=True))
    print(f"bt prices: {len(out['markets']) - bad} ok, {bad} failed -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
