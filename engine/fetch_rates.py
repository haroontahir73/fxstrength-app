"""Latest policy rate per currency, taken from rate decisions on the economic calendar.

Uses the same TradingView calendar API as fetch_calendar.py but over a much longer
window, because some central banks (RBNZ especially) meet too infrequently to appear
in the 45-day window the news scoring uses.

Feeds two of the checklist's interest-rate indicators with real numbers instead of
leaving them at neutral:
    Policy rate               ranked across the seven currencies (carry)
    Rate differential vs USD  this rate minus the USD policy rate

Writes data/rates.json.
"""
import json, datetime as dt
from config import DATA, ORDER
from fetch_calendar import fetch

LOOKBACK_DAYS = 220
TITLES = ("interest rate decision", "cash rate", "official cash rate", "ocr decision")
COUNTRY = {"US": "USD", "GB": "GBP", "JP": "JPY", "EU": "EUR",
           "AU": "AUD", "NZ": "NZD", "CA": "CAD"}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    # The API caps a response at 2000 rows and truncates from the START of the range,
    # so a long window silently returns the OLDEST events. Page it in slices instead.
    rows, step = [], 40
    start = now - dt.timedelta(days=LOOKBACK_DAYS)
    while start < now:
        end = min(start + dt.timedelta(days=step), now)
        try:
            rows.extend(fetch(start, end))
        except Exception as e:
            print(f"  slice {start:%Y-%m-%d} failed: {type(e).__name__}: {e}")
        start = end
    latest = {}
    for ev in sorted(rows, key=lambda e: e["date"]):
        ccy = COUNTRY.get(ev.get("country"))
        if not ccy or ev.get("actualRaw") is None:
            continue
        if any(t in ev["title"].lower() for t in TITLES):
            latest[ccy] = {"rate": ev["actualRaw"], "title": ev["title"],
                           "when": ev["date"][:10],
                           "previous": ev.get("previousRaw")}

    usd = latest.get("USD", {}).get("rate")
    out = {"fetched_at": now.isoformat(), "usd_rate": usd, "currencies": {}}
    for ccy in ORDER:
        d = latest.get(ccy)
        if not d:
            out["currencies"][ccy] = {"rate": None, "note": "no decision found in window"}
            continue
        diff = (d["rate"] - usd) if usd is not None else None
        moved = (d["rate"] - d["previous"]) if d["previous"] is not None else None
        out["currencies"][ccy] = {
            "rate": d["rate"], "diff_vs_usd": round(diff, 2) if diff is not None else None,
            "last_move": round(moved, 2) if moved is not None else None,
            "as_of": d["when"], "title": d["title"],
        }
    (DATA / "rates.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = main()
    print(f"  USD policy rate {r['usd_rate']}")
    for c in ORDER:
        d = r["currencies"][c]
        if d["rate"] is None:
            print(f"  {c}  -- {d['note']}")
        else:
            print(f"  {c}  {d['rate']:>5.2f}%  vs USD {d['diff_vs_usd']:+5.2f}  "
                  f"last move {d['last_move']:+.2f}  ({d['as_of']}, {d['title']})")
