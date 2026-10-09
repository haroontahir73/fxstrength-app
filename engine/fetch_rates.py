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
           "AU": "AUD", "NZ": "NZD", "CA": "CAD", "CH": "CHF"}

# A policy rate is a FACT with a date on it, not a reading that decays: the ECB's 2.15%
# stays 2.15% until the ECB moves it. So when the fetch cannot confirm it, the last
# confirmed value is a far better answer than None - which is what this used to write.
# None here is not neutral: fundamentals.py drops both interest-rate indicators to unset,
# so a transient API outage quietly removed real evidence from the checklist and changed
# the board. Carried-over values are marked `stale` with a day count so freshness.py and
# the page can both say the number is remembered rather than confirmed.
CARRY_MAX_DAYS = 120   # past this a remembered rate is withdrawn rather than carried


def _cached():
    p = DATA / "rates.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None


def main():
    now = dt.datetime.now(dt.timezone.utc)
    # The API caps a response at 2000 rows and truncates from the START of the range,
    # so a long window silently returns the OLDEST events. Page it in slices instead.
    rows, step = [], 40
    slices = failed = 0
    start = now - dt.timedelta(days=LOOKBACK_DAYS)
    while start < now:
        end = min(start + dt.timedelta(days=step), now)
        slices += 1
        try:
            rows.extend(fetch(start, end))
        except Exception as e:
            failed += 1
            print(f"  slice {start:%Y-%m-%d} failed: {type(e).__name__}: {e}")
        start = end
    if failed:
        print(f"  {failed}/{slices} slices failed")
    latest = {}
    for ev in sorted(rows, key=lambda e: e["date"]):
        ccy = COUNTRY.get(ev.get("country"))
        if not ccy or ev.get("actualRaw") is None:
            continue
        if any(t in ev["title"].lower() for t in TITLES):
            latest[ccy] = {"rate": ev["actualRaw"], "title": ev["title"],
                           "when": ev["date"][:10],
                           "previous": ev.get("previousRaw")}

    prev = _cached() or {}
    prev_ccy = prev.get("currencies") or {}

    usd = latest.get("USD", {}).get("rate")
    # Carry the USD rate too - every diff_vs_usd depends on it, so losing it alone would
    # blank the rate-differential indicator for all eight currencies.
    usd_carried = False
    if usd is None and prev_ccy.get("USD", {}).get("rate") is not None:
        usd, usd_carried = prev_ccy["USD"]["rate"], True

    out = {"fetched_at": now.isoformat(), "usd_rate": usd,
           "usd_rate_carried": usd_carried, "currencies": {}}
    carried = []
    for ccy in ORDER:
        d = latest.get(ccy)
        if not d:
            # Nothing confirmed this run. Keep the last confirmed rate rather than
            # overwriting a fact with None - but only while it is recent enough to
            # still plausibly be the current policy rate.
            old = prev_ccy.get(ccy) or {}
            age_d = None
            if old.get("rate") is not None:
                try:
                    age_d = (now.date() - dt.date.fromisoformat(old["as_of"])).days
                except Exception:                              # noqa: BLE001
                    age_d = None
            if old.get("rate") is not None and (age_d is None or age_d <= CARRY_MAX_DAYS):
                kept = dict(old)
                kept.update({"stale": True, "carried_at": now.isoformat(),
                             "stale_days": age_d,
                             "note": f"no decision in window this run - carrying the last "
                                     f"confirmed rate"
                                     + (f" ({age_d}d old)" if age_d is not None else "")})
                # the differential still has to be against whatever USD we ended up with
                if usd is not None and kept.get("rate") is not None:
                    kept["diff_vs_usd"] = round(kept["rate"] - usd, 2)
                out["currencies"][ccy] = kept
                carried.append(ccy)
                continue
            why = ("no decision found in window" if not old.get("rate") else
                   f"last confirmed rate is {age_d}d old (>{CARRY_MAX_DAYS}d) - withdrawn")
            out["currencies"][ccy] = {"rate": None, "note": why}
            continue
        diff = (d["rate"] - usd) if usd is not None else None
        moved = (d["rate"] - d["previous"]) if d["previous"] is not None else None
        out["currencies"][ccy] = {
            "rate": d["rate"], "diff_vs_usd": round(diff, 2) if diff is not None else None,
            "last_move": round(moved, 2) if moved is not None else None,
            "as_of": d["when"], "title": d["title"],
        }
    if carried:
        out["degraded"] = True
        out["degraded_why"] = f"carried last confirmed rate for {', '.join(carried)}"
        print(f"  carried previous rates for {', '.join(carried)}"
              + ("  (USD too)" if usd_carried else ""))
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
