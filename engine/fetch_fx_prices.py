"""Recent FX price action, for the directional / retracement read on the board.

The score is a medium-term bias. This adds an orthogonal read: is price moving WITH that bias
(directional) or AGAINST it (a possible retracement)? - and, when it is retracing, where a
dip might be bought / a rally sold (the fib zone of the current leg, see config.retracement_zone).

Source: Frankfurter (ECB daily reference rates, free, no key; needs a browser UA or 403), the
same feed validate.py uses. Quotes are USD-base (1 USD = X ccy). Each currency's STRENGTH
INDEX here is that rate rebased to 100 and inverted so it RISES as the currency strengthens,
matching the sign of the score. USD has no cross rate - its index is the basket of USD/ccy
rates rebased to 100 (the DXY analogue).

Writes data/prices_fx.json:
  {"fetched_at": iso, "asof": date,
   "moves":  {ccy: {"d5": %, "d20": %}},
   "series": {ccy: [strength index, oldest first]},
   "series_dates": [ISO date per series point]}
On a fetch failure the last good file is kept.
"""
import json, urllib.request, datetime as dt
from config import DATA, ORDER

API = "https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols={syms}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
XCCY = [c for c in ORDER if c != "USD"]
WINDOW_DAYS = 100          # calendar days -> ~70 trading days, enough for a swing lookback


def _pct_move(rates, days, n):
    """+ = currency strengthened over the last n trading days."""
    if len(days) <= n:
        return None
    a, b = rates[days[-1 - n]], rates[days[-1]]
    m = {c: -(b[c] - a[c]) / a[c] * 100 for c in XCCY if c in a and c in b and a[c]}
    if not m:
        return None
    m["USD"] = -sum(m.values()) / len(m)
    return m


def _series(rates, days):
    """Strength index per currency, base 100 at days[0], rising = stronger. Returns
    (series_by_ccy, dates) - one shared date list, all currencies use the same trading days."""
    valid = [d for d in days if all(rates[d].get(c) for c in XCCY)]
    if len(valid) < 10:
        return {}, []
    base = rates[valid[0]]
    out = {c: [round(100 * base[c] / rates[d][c], 3) for d in valid] for c in XCCY}
    out["USD"] = [round(100 * sum(rates[d][c] / base[c] for c in XCCY) / len(XCCY), 3)
                  for d in valid]
    return out, valid


def main():
    cache = DATA / "prices_fx.json"
    end = dt.date.today()
    url = API.format(start=end - dt.timedelta(days=WINDOW_DAYS), end=end, syms=",".join(XCCY))
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read()
        rates = json.loads(raw.decode("utf-8", "replace"))["rates"]
        days = sorted(rates)
        if len(days) < 6:
            raise RuntimeError(f"only {len(days)} price days returned")
    except Exception as e:
        print(f"  FX prices fetch failed ({type(e).__name__}: {e})"
              + (" - keeping cached" if cache.exists() else ""))
        try:
            return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {"moves": {}}
        except Exception:
            return {"moves": {}}

    d5, d20 = _pct_move(rates, days, 5), _pct_move(rates, days, 20)
    moves = {c: {"d5": round((d5 or {}).get(c, 0.0), 2),
                 "d20": round((d20 or {}).get(c, 0.0), 2)} for c in ORDER}
    series, series_dates = _series(rates, days)
    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "asof": days[-1], "moves": moves,
           "series": series, "series_dates": series_dates}
    cache.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("  5-day vs USD: " + "  ".join(f"{c} {moves[c]['d5']:+.1f}%" for c in ORDER))
    return out


if __name__ == "__main__":
    main()
