"""Does the decode table actually match what gold, silver and oil DO?

The leans in commodity_watch.DECODE were written from macro reasoning. This measures
them. For every US economic release since 2023 that the live watcher would have fired
on, it takes the SAME category the watcher would assign (via
commodity_watch.categorise_release - the real function, not a copy) and compares the
predicted direction against the instrument's actual move that day.

Method
  - Calendar: TradingView's public API, pulled MONTH BY MONTH. The endpoint caps a
    response at ~2000 rows and truncates from the START of the range, so a single
    multi-year request silently loses its earliest data.
  - Prices: Yahoo daily closes for GC=F / SI=F / CL=F.
  - Reaction: close-to-close on the release day. US releases land 12:30-14:00 UTC, well
    before the 21:00 UTC futures close, so the day's move contains the reaction. Releases
    after 20:00 UTC use the next day.
  - A "hit" is the sign of the move matching the sign of the predicted lean. Flat leans
    (strength 0) are not scored - the table makes no claim there.

Read the output as evidence, not proof: one release is never the only thing moving a
market on a given day, so a hit rate near 50% means "no measurable edge", not "backwards".
A hit rate meaningfully BELOW 50% on a large sample is the interesting result - it says
the lean points the wrong way.

    python backtest_decode.py            # 2023-01 to now
    python backtest_decode.py 2024-01    # from a given month
"""
import json, sys, time, urllib.request
import datetime as dt
from pathlib import Path
from collections import defaultdict

import commodity_watch as cw
from fetch_calendar import API, HEADERS

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

CACHE = Path(__file__).parent / "data" / "backtest_calendar.json"
INSTR = [("gold", "GC=F"), ("silver", "SI=F"), ("oil", "CL=F")]


# ------------------------------------------------------------------ data
def months(start: str):
    y, m = (int(x) for x in start.split("-"))
    now = dt.datetime.now(dt.timezone.utc)
    while (y, m) <= (now.year, now.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_calendar(start):
    """Month-by-month so the row cap never truncates us."""
    if CACHE.exists():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        print(f"  using cached calendar ({len(cached)} events) - delete "
              f"{CACHE.name} to refetch")
        return cached
    out = []
    for y, m in months(start):
        a = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
        b = (a + dt.timedelta(days=32)).replace(day=1)
        url = (f"{API}?from={a.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
               f"&to={b.strftime('%Y-%m-%dT%H:%M:%S.000Z')}&countries=US")
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            rows = json.loads(urllib.request.urlopen(req, timeout=40).read()).get("result", [])
        except Exception as e:                                 # noqa: BLE001
            print(f"  {y}-{m:02d} failed ({type(e).__name__})")
            continue
        keep = [{"title": r.get("title", ""), "date": r.get("date", ""),
                 "actual": r.get("actual"), "forecast": r.get("forecast")}
                for r in rows if r.get("actual") is not None
                and r.get("forecast") is not None]
        out += keep
        print(f"  {y}-{m:02d}  {len(keep):>3} usable events")
        time.sleep(0.4)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(out), encoding="utf-8")
    return out


def closes(symbol):
    """{date: close} of daily closes, 5 years."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=5y&interval=1d")
    req = urllib.request.Request(url, headers=cw.UA)
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    r = d["chart"]["result"][0]
    out = {}
    for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c is None:
            continue
        out[dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()] = c
    return out


# ------------------------------------------------------------------ scoring
def day_return(series, days, when):
    """Close-to-close move on the release day (or the next session)."""
    d = when.date()
    if when.hour >= 20:
        d = d + dt.timedelta(days=1)
    for _ in range(5):                       # roll forward over weekends / holidays
        if d in series:
            break
        d = d + dt.timedelta(days=1)
    else:
        return None
    i = days.index(d)
    if i == 0:
        return None
    return (series[d] - series[days[i - 1]]) / series[days[i - 1]] * 100


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2023-01"
    print(f"Pulling US calendar from {start} (month by month)...")
    events = fetch_calendar(start)
    print(f"  {len(events)} releases with actual + forecast\n")

    print("Pulling daily closes...")
    px, dys = {}, {}
    for name, sym in INSTR:
        px[name] = closes(sym)
        dys[name] = sorted(px[name])
        print(f"  {name:<7} {len(px[name])} sessions "
              f"({dys[name][0]} to {dys[name][-1]})")
    print()

    # category -> instrument -> [(return, predicted_dir)]
    res = defaultdict(lambda: defaultdict(list))
    fired = 0
    for e in events:
        hit = cw.categorise_release(e["title"], e["actual"], e["forecast"])
        if hit is None:
            continue
        cat, tier, _ = hit
        if tier < cw.MIN_IMPACT:             # high-impact only, as the watcher runs
            continue
        when = cw._parse_date(e["date"])
        if when is None:
            continue
        fired += 1
        dec = cw.DECODE[cat]
        for name, _sym in INSTR:
            d, s, _ = dec[name]
            if d == "flat" or s == 0:
                continue                     # no claim made, nothing to score
            r = day_return(px[name], dys[name], when)
            if r is not None:
                res[cat][name].append((r, d))

    print(f"{fired} releases would have fired an alert "
          f"(high-impact only, {cw.MIN_IMPACT=})\n")

    # THE BASELINE MATTERS MORE THAN THE HIT RATE. Gold roughly tripled across this
    # sample, so a plain up-day is already better than a coin flip. Judging a lean
    # against 50% would credit an "up" call for nothing more than the bull market, and
    # condemn a "down" call for the same reason. Everything below is scored as EXCESS
    # over the unconditional daily behaviour of that instrument.
    base = {}
    for name, _sym in INSTR:
        d = dys[name]
        rets = [(px[name][d[i]] - px[name][d[i - 1]]) / px[name][d[i - 1]] * 100
                for i in range(1, len(d))]
        up = sum(1 for r in rets if r > 0) / len(rets) * 100
        base[name] = (up, sum(rets) / len(rets))
    print("BASELINE (every session in the sample, no news filter):")
    for name, _ in INSTR:
        print(f"  {name:<7} up on {base[name][0]:.0f}% of days, "
              f"avg {base[name][1]:+.3f}%/day")
    print("  -> a lean only earns its keep by beating its own instrument's baseline\n")

    print("=" * 78)
    print(f"{'category / instrument':<28}{'n':>5}{'hit%':>7}{'base':>7}"
          f"{'EXCESS':>9}{'move vs base':>14}{'verdict':>12}")
    print("=" * 78)
    verdicts = []
    for cat in sorted(res):
        print(f"{cat}")
        for name in ("gold", "silver", "oil"):
            rows = res[cat].get(name)
            if not rows:
                continue
            n = len(rows)
            want = rows[0][1]
            hits = sum(1 for r, d in rows if (r > 0) == (d == "up"))
            rate = hits / n * 100
            avg = sum(r for r, _ in rows) / n
            b_up, b_avg = base[name]
            # the baseline for a DOWN call is the share of down days, i.e. 100 - up%
            b_rate = b_up if want == "up" else 100 - b_up
            excess = rate - b_rate
            move = (avg - b_avg) if want == "up" else -(avg - b_avg)
            if n < 20:
                v = "too few"
            elif excess <= -8:
                v = "WRONG WAY"
            elif excess < 3:
                v = "no edge"
            elif excess < 8:
                v = "slight"
            else:
                v = "CONFIRMED"
            verdicts.append((cat, name, n, rate, b_rate, excess, move, v))
            print(f"  -> {name:<22}{n:>5}{rate:>6.0f}%{b_rate:>6.0f}%"
                  f"{excess:>+8.0f}pp{move:>+13.2f}%{v:>12}")
    print("=" * 78)

    bad = [v for v in verdicts if v[7] == "WRONG WAY"]
    weak = [v for v in verdicts if v[7] == "no edge"]
    if bad:
        print("\nLEANS THE DATA DISAGREES WITH - these point the wrong way:")
        for cat, name, n, rate, b, ex, move, _ in bad:
            print(f"  {cat}.{name}: {rate:.0f}% right vs {b:.0f}% baseline "
                  f"({ex:+.0f}pp), {move:+.2f}% vs baseline drift, n={n}")
    else:
        print("\nNo lean is measurably backwards once the baseline drift is removed.")
    if weak:
        print("\nNO MEASURABLE EDGE (keep, but do not trade them on their own):")
        for cat, name, n, rate, b, ex, move, _ in weak:
            print(f"  {cat}.{name}: {rate:.0f}% vs {b:.0f}% baseline ({ex:+.0f}pp), n={n}")
    print("\nOne release is never the only thing moving a market that day, so a few")
    print("points of excess is a real but small signal. Large negative excess is the")
    print("only result that says a lean is written backwards.")


if __name__ == "__main__":
    main()
