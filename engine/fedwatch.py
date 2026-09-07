"""Fed Watch - market-implied odds for every upcoming FOMC decision.

WHY THIS IS COMPUTED, NOT SCRAPED
---------------------------------
CME's FedWatch page and its backing service both return 403 here and from GitHub
Actions (they block anything that is not a real browser session). So this rebuilds the
same number from its public inputs instead, which are all reachable:

    30-Day Fed Funds futures   Yahoo Finance    ZQ<month><year>.CBT
    Effective fed funds rate   NY Fed API       markets.newyorkfed.org
    FOMC decision dates        federalreserve.gov/json/calendar.json

THE MATHS (this is CME's own published method)
----------------------------------------------
A ZQ contract settles to the AVERAGE effective fed funds rate over its calendar month,
so `100 - price` is the market's expected average rate for that month. When a meeting
falls mid-month that average is a blend of the rate before and after the decision:

    implied_avg * days_in_month = rate_before * days_before + rate_after * days_after

Rearranged for the only unknown, that gives the rate the market expects the Fed to hold
AFTER the meeting. Divide the move by 0.25 and you have the odds of a 25bp change:

    P(hike) = (rate_after - rate_before) / 0.25

Meetings are chained - the rate going into one is the rate coming out of the last.
Verified 2026-09-06 against CME's own published figure: 57.9% here vs their ~58%.

    python fedwatch.py          # fetch, compute, print, write data/fedwatch.json
"""
import json, sys, urllib.request
import calendar as _cal
import datetime as dt
from config import DATA

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

OUT = DATA / "fedwatch.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
STEP = 0.25                       # the Fed moves in 25bp steps
HISTORY_MAX = 400                 # ~4 days of 15-minute snapshots
MONTH_CODE = "FGHJKMNQUVXZ"       # Jan..Dec futures month codes

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
EFFR_API = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json"
FED_CAL = "https://www.federalreserve.gov/json/calendar.json"

# Used only if the Fed's own calendar is unreachable. Decision day = the second day of
# each two-day meeting; the new rate takes effect the morning after.
FALLBACK_MEETINGS = ["2026-09-16", "2026-10-28", "2026-12-09",
                     "2027-01-27", "2027-03-17", "2027-04-28"]


def _get(url, timeout=25):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read()


def _load_prev():
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


# ---------------------------------------------------------------- inputs
def effr():
    """(rate, target_low, target_high, as_of) - the rate the market moves away from."""
    d = json.loads(_get(EFFR_API).decode("utf-8", "replace"))
    r = d["refRates"][0]
    return (float(r["percentRate"]), float(r["targetRateFrom"]),
            float(r["targetRateTo"]), r["effectiveDate"])


def meetings(limit=6):
    """Upcoming FOMC DECISION dates, soonest first. The press-conference entry in the
    Fed's calendar is the decision day; the minutes entries are a different thing."""
    today = dt.date.today()
    out = []
    try:
        raw = _get(FED_CAL, timeout=30).decode("utf-8-sig", "replace")
        for e in json.loads(raw).get("events", []):
            if "Press Conference" not in str(e.get("title", "")):
                continue
            ym, days = str(e.get("month", "")), str(e.get("days", ""))
            day = days.split("-")[-1].strip()
            try:
                d = dt.date(int(ym[:4]), int(ym[5:7]), int(day))
            except Exception:                                  # noqa: BLE001
                continue
            if d >= today:
                out.append(d)
    except Exception as e:                                     # noqa: BLE001
        print(f"  Fed calendar unavailable ({type(e).__name__}) - using the fixed schedule",
              file=sys.stderr)
    if not out:
        out = [dt.date.fromisoformat(s) for s in FALLBACK_MEETINGS
               if dt.date.fromisoformat(s) >= today]
    return sorted(set(out))[:limit]


def zq_price(year, month):
    """Last price for the ZQ contract covering that calendar month."""
    sym = f"ZQ{MONTH_CODE[month - 1]}{year % 100:02d}.CBT"
    d = json.loads(_get(YAHOO.format(sym=sym)).decode("utf-8", "replace"))
    res = (d.get("chart") or {}).get("result") or []
    if not res:
        raise ValueError(f"no data for {sym}")
    px = res[0]["meta"].get("regularMarketPrice")
    if px is None:
        closes = [c for c in res[0]["indicators"]["quote"][0]["close"] if c is not None]
        px = closes[-1] if closes else None
    if px is None:
        raise ValueError(f"no price for {sym}")
    return float(px), sym


# ---------------------------------------------------------------- the calculation
def implied_after(avg_rate, rate_before, meeting_day, year, month):
    """The rate the market expects AFTER the meeting, from that month's futures average.
    The decision takes effect the day after the meeting ends."""
    n = _cal.monthrange(year, month)[1]
    d_before = meeting_day                 # days the OLD rate is in force (1st..meeting day)
    d_after = n - d_before
    if d_after <= 0:                       # meeting on the last day - no signal this month
        return None
    return (avg_rate * n - rate_before * d_before) / d_after


def _step_probs(delta):
    """Split a net expected move of `delta` 25bp steps into per-step probabilities.

    CME assumes the Fed moves in 25bp increments, so a net of +0.58 steps means a 58%
    chance of one 25bp rise and 42% of no change. A net beyond +/-1 means the market is
    pricing some chance of a double move, so the weight spills into the two-step bucket."""
    if abs(delta) < 1e-9:
        return {0: 1.0}
    sign = 1 if delta > 0 else -1
    mag = abs(delta)
    if mag <= 1.0:
        return {0: 1.0 - mag, sign: mag}
    mag = min(mag, 2.0)                       # nothing here prices a triple move
    two = mag - 1.0
    return {sign: 1.0 - two, sign * 2: two}


def distribution(dist, delta):
    """Push a {step: probability} distribution through one meeting."""
    moves = _step_probs(delta)
    out = {}
    for step, p in dist.items():
        for mv, pm in moves.items():
            if pm > 0:
                out[step + mv] = out.get(step + mv, 0.0) + p * pm
    return {k: v for k, v in out.items() if v > 0.0005}


def build():
    now = dt.datetime.now(dt.timezone.utc)
    cur, lo, hi, effr_date = effr()
    mtgs = meetings()

    rows, rate_before = [], cur
    dist = {0: 1.0}                     # steps away from the CURRENT target range
    for m in mtgs:
        try:
            px, sym = zq_price(m.year, m.month)
        except Exception as e:                                 # noqa: BLE001
            print(f"  {m}: {type(e).__name__} - skipped", file=sys.stderr)
            continue
        avg = 100.0 - px
        after = implied_after(avg, rate_before, m.day, m.year, m.month)
        if after is None:
            continue
        move = after - rate_before
        p_move = move / STEP                       # +1.0 = a full 25bp rise priced
        p_hike = max(0.0, min(1.0, p_move)) * 100
        p_cut = max(0.0, min(1.0, -p_move)) * 100

        # Carry a full distribution over TARGET RANGES through the meetings, which is what
        # CME's own table shows. The move at this meeting is measured against the expected
        # rate going in, not against a single path.
        exp_before = cur + STEP * sum(k * v for k, v in dist.items())
        dist = distribution(dist, (after - exp_before) / STEP)
        buckets = sorted(
            ({"step": k,
              "low": round(lo + k * STEP, 2), "high": round(hi + k * STEP, 2),
              "prob": round(v * 100, 1)} for k, v in dist.items()),
            key=lambda b: b["low"])

        rows.append({
            "buckets": buckets,
            "date": m.isoformat(),
            "label": m.strftime("%d %b %Y"),
            "contract": sym, "price": round(px, 4),
            "implied_month_avg": round(avg, 4),
            "rate_before": round(rate_before, 4),
            "implied_after": round(after, 4),
            "move_bp": round(move * 100, 1),
            "hike": round(p_hike, 1), "cut": round(p_cut, 1),
            "hold": round(max(0.0, 100 - p_hike - p_cut), 1),
            "cum_bp_from_now": round((after - cur) * 100, 1),
        })
        rate_before = after

    prev = _load_prev()
    prev_rows = {r["date"]: r for r in prev.get("meetings", [])}
    for r in rows:                                   # movement since the last run
        p = prev_rows.get(r["date"])
        r["hike_prev"] = p.get("hike") if p else None
        r["hike_delta"] = round(r["hike"] - p["hike"], 1) if p else None

    res = {
        "built_at": now.isoformat(),
        "effr": cur, "target_low": lo, "target_high": hi, "effr_as_of": effr_date,
        "source": "computed from CME 30-Day Fed Funds futures (Yahoo) + NY Fed EFFR",
        "meetings": rows,
    }
    res["headline"] = headline(res)
    res["explainer"] = explainer(res)

    hist = prev.get("history", [])
    if rows:
        hist.append({"at": now.isoformat(),
                     "next": rows[0]["date"], "hike": rows[0]["hike"]})
    res["history"] = hist[-HISTORY_MAX:]

    OUT.write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


# ---------------------------------------------------------------- plain words
def headline(res):
    """One line: what the market currently expects at the next meeting."""
    if not res["meetings"]:
        return "No fed funds futures data right now."
    m = res["meetings"][0]
    h, c = m["hike"], m["cut"]
    if h >= 85:
        return f"A rate rise on {m['label']} is fully priced ({h:.0f}%)."
    if h >= 60:
        return f"Markets lean toward a rate rise on {m['label']} ({h:.0f}%)."
    if h >= 40:
        return f"{m['label']} is close to a coin flip - {h:.0f}% for a rate rise."
    if c >= 60:
        return f"Markets lean toward a rate cut on {m['label']} ({c:.0f}%)."
    return f"Markets expect no change on {m['label']} ({m['hold']:.0f}% hold)."


def explainer(res):
    """The 1-3 plain lines beside the numbers: what it means, and what just moved."""
    if not res["meetings"]:
        return ["Fed funds futures did not load, so there is no read right now."]
    m = res["meetings"][0]
    out = [headline(res)]

    h, c = m["hike"], m["cut"]
    if h >= 60:
        out.append("Higher US rates usually mean a firmer dollar and pressure on gold.")
    elif c >= 60:
        out.append("Lower US rates usually mean a softer dollar and support for gold.")
    elif h <= 25 and c <= 25:
        out.append("A steady Fed keeps the dollar range-bound - the next big move comes "
                   "from the data, not the meeting.")
    else:
        out.append("It is near enough 50/50, so expect a sharp dollar and gold move on "
                   "the decision itself.")

    d = m.get("hike_delta")
    if d is not None and abs(d) >= 3:
        out.append(f"Odds moved {'up' if d > 0 else 'down'} {abs(d):.0f} points since the "
                   f"last check - the market has shifted "
                   f"{'toward' if d > 0 else 'away from'} a rise.")
    elif len(res["meetings"]) > 1:
        last = res["meetings"][-1]
        cum = last["cum_bp_from_now"]
        if abs(cum) >= 10:
            out.append(f"Further out, {abs(cum):.0f}bp {'higher' if cum > 0 else 'lower'} "
                       f"is priced by {last['label']}.")
    return out[:3]


if __name__ == "__main__":
    r = build()
    print(f"EFFR {r['effr']}%  (target {r['target_low']}-{r['target_high']}, "
          f"as of {r['effr_as_of']})")
    for m in r["meetings"]:
        d = f"  ({m['hike_delta']:+.0f})" if m.get("hike_delta") is not None else ""
        print(f"  {m['label']}  {m['contract']:12}  hike {m['hike']:5.1f}%  "
              f"hold {m['hold']:5.1f}%  cut {m['cut']:5.1f}%   "
              f"implied {m['implied_after']:.3f}%{d}")
    print()
    for line in r["explainer"]:
        print("  " + line)
