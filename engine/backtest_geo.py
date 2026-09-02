"""Backtest the NEWS-driven leans - geopolitics, oil supply, Fed independence, squeezes.

backtest_decode.py could only score the calendar categories, because economic releases
come with dates and numbers attached. News does not. This builds the missing labelled
history: month-by-month Google News queries with `after:`/`before:` operators, every
headline run through commodity_watch.classify() - the REAL classifier - so a story only
enters the sample if the live watcher would genuinely have fired on it.

Why not Bloomberg directly: there is no free API and the archive is paywalled. Google
News indexes Bloomberg, Reuters and the rest, so their headlines are in here - the run
prints the outlet mix so you can see whose reporting the sample rests on.

Event definition
  A (category, date) pair counts as ONE event, and only if at least MIN_OUTLETS distinct
  outlets carried it that day. A real geopolitical event is on every wire; a single
  stray headline is noise, and without this the sample fills up with the latter.

Reaction is close-to-close, same rule as the calendar backtest (news after 20:00 UTC is
scored against the next session), and everything is measured as EXCESS over the
instrument's own baseline drift - gold roughly tripled across the sample, so raw hit
rates flatter every "up" call and punish every "down" call.

    python backtest_geo.py            # 2023-01 to now
    python backtest_geo.py 2024-06
"""
import json, sys, time, re, urllib.request
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict, Counter

import commodity_watch as cw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

CACHE = Path(__file__).parent / "data" / "backtest_geo_news.json"
INSTR = [("gold", "GC=F"), ("silver", "SI=F"), ("oil", "CL=F")]
MIN_OUTLETS = 2          # distinct outlets needed before a day counts as an event

# Broad enough to surface the events; classify() decides what actually counts.
QUERIES = [
    '("Strait of Hormuz" OR Iran OR Israel OR Russia OR Ukraine) '
    '(oil OR crude OR strike OR attack OR ceasefire OR truce OR sanctions)',
    '(oil OR crude OR OPEC OR Brent) ("production cut" OR "output cut" OR supply OR '
    'embargo OR ceasefire OR "peace deal" OR "output increase")',
    '(gold OR silver OR bullion) (Fed OR war OR "central bank" OR squeeze OR shortage '
    'OR Powell OR Warsh)',
]
GN = ("https://news.google.com/rss/search?q={q}+after:{a}+before:{b}"
      "&hl=en-US&gl=US&ceid=US:en")


def months(start):
    y, m = (int(x) for x in start.split("-"))
    now = dt.datetime.now(dt.timezone.utc)
    while (y, m) <= (now.year, now.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_news(start):
    if CACHE.exists():
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        print(f"  using cached corpus ({len(c)} headlines) - delete {CACHE.name} to refetch")
        return c
    from urllib.parse import quote
    out = []
    for y, m in months(start):
        a = dt.date(y, m, 1)
        b = (a + dt.timedelta(days=32)).replace(day=1)
        got = 0
        for q in QUERIES:
            url = GN.format(q=quote(q), a=a.isoformat(), b=b.isoformat())
            raw = cw._get(url, timeout=30)
            if not raw:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                pub = cw._parse_date(item.findtext("pubDate") or "")
                if not title or pub is None:
                    continue
                src = item.find("source")
                out.append({"title": title, "when": pub.isoformat(),
                            "src": (src.text or "").strip() if src is not None else ""})
                got += 1
            time.sleep(0.3)
        print(f"  {y}-{m:02d}  {got:>4} headlines")
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(out), encoding="utf-8")
    return out


def closes(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=5y&interval=1d")
    req = urllib.request.Request(url, headers=cw.UA)
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())["chart"]["result"][0]
    return {dt.datetime.fromtimestamp(ts, dt.timezone.utc).date(): c
            for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"])
            if c is not None}


def day_return(series, days, when):
    d = when.date()
    if when.hour >= 20:
        d += dt.timedelta(days=1)
    for _ in range(5):
        if d in series:
            break
        d += dt.timedelta(days=1)
    else:
        return None
    i = days.index(d)
    return None if i == 0 else (series[d] - series[days[i - 1]]) / series[days[i - 1]] * 100


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2023-01"
    print(f"Building a dated news corpus from {start}...")
    news = fetch_news(start)
    print(f"  {len(news)} headlines\n")

    # classify with the live classifier, then collapse to one event per category-day
    events = defaultdict(lambda: defaultdict(list))       # cat -> date -> [outlets]
    first_seen, outlets = {}, Counter()
    for n in news:
        hit = cw.classify(n["title"])
        if not hit:
            continue
        cat = hit[0]
        if cw.IMPACT.get(cat, 2) < 3:
            continue
        when = dt.datetime.fromisoformat(n["when"])
        d = when.date()
        events[cat][d].append(n["src"])
        outlets[n["src"]] += 1
        first_seen.setdefault((cat, d), when)

    print("Outlets carrying the classified stories (top 12):")
    for src, c in outlets.most_common(12):
        print(f"  {c:>4}  {src or '(unattributed)'}")
    print()

    px, dys = {}, {}
    for name, sym in INSTR:
        px[name] = closes(sym)
        dys[name] = sorted(px[name])

    base = {}
    for name, _ in INSTR:
        d = dys[name]
        rets = [(px[name][d[i]] - px[name][d[i - 1]]) / px[name][d[i - 1]] * 100
                for i in range(1, len(d))]
        base[name] = (sum(1 for r in rets if r > 0) / len(rets) * 100,
                      sum(rets) / len(rets))
    print("BASELINE (all sessions):")
    for name, _ in INSTR:
        print(f"  {name:<7} up {base[name][0]:.0f}% of days, avg {base[name][1]:+.3f}%/day")
    print()

    res = defaultdict(lambda: defaultdict(list))
    counts = {}
    for cat, byday in events.items():
        days = [d for d, srcs in byday.items() if len(set(srcs)) >= MIN_OUTLETS]
        counts[cat] = (len(byday), len(days))
        for d in days:
            when = first_seen[(cat, d)]
            dec = cw.DECODE[cat]
            for name, _ in INSTR:
                direction, s, _ = dec[name]
                if direction == "flat" or s == 0:
                    continue
                r = day_return(px[name], dys[name], when)
                if r is not None:
                    res[cat][name].append((r, direction))

    print(f"Events (>= {MIN_OUTLETS} distinct outlets on the day):")
    for cat, (raw, kept) in sorted(counts.items()):
        print(f"  {cat:<18} {kept:>4} event-days kept of {raw:>4} candidate days")
    print()

    print("=" * 78)
    print(f"{'category / instrument':<28}{'n':>5}{'hit%':>7}{'base':>7}"
          f"{'EXCESS':>9}{'move vs base':>14}{'verdict':>12}")
    print("=" * 78)
    verdicts = []
    for cat in sorted(res):
        print(cat)
        for name in ("gold", "silver", "oil"):
            rows = res[cat].get(name)
            if not rows:
                continue
            n = len(rows)
            want = rows[0][1]
            rate = sum(1 for r, d in rows if (r > 0) == (d == "up")) / n * 100
            avg = sum(r for r, _ in rows) / n
            b_up, b_avg = base[name]
            b_rate = b_up if want == "up" else 100 - b_up
            excess = rate - b_rate
            move = (avg - b_avg) if want == "up" else -(avg - b_avg)
            v = ("too few" if n < 20 else "WRONG WAY" if excess <= -8 else
                 "no edge" if excess < 3 else "slight" if excess < 8 else "CONFIRMED")
            verdicts.append((cat, name, n, rate, b_rate, excess, move, v))
            print(f"  -> {name:<22}{n:>5}{rate:>6.0f}%{b_rate:>6.0f}%"
                  f"{excess:>+8.0f}pp{move:>+13.2f}%{v:>12}")
    print("=" * 78)

    bad = [v for v in verdicts if v[7] == "WRONG WAY"]
    if bad:
        print("\nLEANS THE DATA DISAGREES WITH:")
        for cat, name, n, rate, b, ex, move, _ in bad:
            print(f"  {cat}.{name}: {rate:.0f}% vs {b:.0f}% baseline ({ex:+.0f}pp), "
                  f"{move:+.2f}% vs drift, n={n}")
    else:
        print("\nNo news lean is measurably backwards once baseline drift is removed.")
    print("\nCAVEAT: Google News caps results per query, so this is a SAMPLE of coverage,")
    print("not the full population, and the classifier's own blind spots are inherited by")
    print("the sample it builds. Treat these as weaker evidence than the calendar test.")


if __name__ == "__main__":
    main()
