"""How long should a call be given before it is marked right or wrong?

selfcheck.py scores an alert 4 hours after it fires. That number was a guess. This
measures it: the same historical events, scored at 1h / 2h / 4h / 8h / 24h, to see which
horizon shows the clearest edge over doing nothing.

Uses hourly bars (Yahoo gives ~2 years of them) and the SAME categorise_release() the
live watcher uses, so what is measured is what actually runs.

Baseline matters as much as it did in the other backtests: over 24 hours a trending
market drifts, so a raw hit rate at a long horizon flatters whichever way the trend ran.
Each horizon is compared against the unconditional share of up-moves over a window of
THAT SAME length.

    python backtest_horizon.py
"""
import json, sys, urllib.request
import datetime as dt
from pathlib import Path
from collections import defaultdict
from bisect import bisect_left

import commodity_watch as cw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

CAL = Path(__file__).parent / "data" / "backtest_calendar.json"
HORIZONS = [1, 2, 4, 8, 24]
INSTR = [("gold", "Gold", "GC=F"), ("silver", "Silver", "SI=F"), ("oil", "WTI", "CL=F")]


def hourly(symbol):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=730d&interval=1h")
    req = urllib.request.Request(url, headers=cw.UA)
    r = json.loads(urllib.request.urlopen(req, timeout=45).read())["chart"]["result"][0]
    pairs = [(t, c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"])
             if c is not None]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def move(ts, px, when_ts, hours):
    """Return over `hours` starting at the first bar at or after the event."""
    i = bisect_left(ts, when_ts)
    if i >= len(ts) - 1:
        return None
    j = bisect_left(ts, ts[i] + hours * 3600)
    if j >= len(ts) or j == i:
        return None
    # refuse a window stretched by a weekend - it is no longer the horizon being tested
    if ts[j] - ts[i] > hours * 3600 * 2.5:
        return None
    return (px[j] - px[i]) / px[i] * 100


def baseline(ts, px, hours):
    """Unconditional share of up-moves over a window of this length."""
    up = tot = 0
    step = max(1, len(ts) // 4000)
    for i in range(0, len(ts) - 1, step):
        j = bisect_left(ts, ts[i] + hours * 3600)
        if j >= len(ts) or j == i or ts[j] - ts[i] > hours * 3600 * 2.5:
            continue
        tot += 1
        if px[j] > px[i]:
            up += 1
    return (up / tot * 100) if tot else 50.0


def main():
    events = []
    for e in json.loads(CAL.read_text(encoding="utf-8")):
        hit = cw.categorise_release(e["title"], e["actual"], e["forecast"])
        if not hit or hit[1] < cw.MIN_IMPACT:
            continue
        when = cw._parse_date(e["date"])
        if when:
            events.append((when.timestamp(), hit[0]))
    print(f"{len(events)} high-impact releases on record")

    data, base = {}, defaultdict(dict)
    for key, sym, yf in INSTR:
        ts, px = hourly(yf)
        data[key] = (ts, px)
        print(f"  {key:<7} {len(ts)} hourly bars "
              f"({dt.datetime.fromtimestamp(ts[0], dt.timezone.utc).date()} onward)")
        for h in HORIZONS:
            base[key][h] = baseline(ts, px, h)
    print()

    # horizon -> list of (won, instrument)
    res = defaultdict(lambda: defaultdict(list))
    for when_ts, cat in events:
        dec = cw.DECODE.get(cat)
        if not dec:
            continue
        for key, _sym, _yf in INSTR:
            d, s, _ = dec[key]
            if d == "flat" or s == 0:
                continue
            ts, px = data[key]
            for h in HORIZONS:
                m = move(ts, px, when_ts, h)
                if m is None or abs(m) < 0.05:
                    continue
                res[h][key].append(((m > 0) == (d == "up"), d))

    print("=" * 68)
    print(f"{'horizon':<10}{'instrument':<12}{'n':>6}{'hit':>8}{'base':>8}{'EXCESS':>10}")
    print("=" * 68)
    totals = {}
    for h in HORIZONS:
        exc_sum, n_sum = 0.0, 0
        for key, _sym, _yf in INSTR:
            rows = res[h][key]
            if len(rows) < 30:
                continue
            n = len(rows)
            hit = sum(1 for won, _ in rows if won) / n * 100
            up_rate = base[key][h]
            # The baseline depends on the DIRECTION of each call: an "up" call is judged
            # against how often the instrument rose, a "down" call against how often it
            # fell. Comparing a mixed set to the up-rate alone was the same mistake the
            # earlier backtests had - it flatters or punishes purely by trend.
            b = sum(up_rate if d == "up" else 100 - up_rate for _, d in rows) / n
            exc = hit - b
            print(f"{str(h) + 'h':<10}{key:<12}{n:>6}{hit:>7.0f}%{b:>7.0f}%{exc:>+9.0f}pp")
            exc_sum += exc * n
            n_sum += n
        totals[h] = exc_sum / n_sum if n_sum else 0
        print(f"{'':<10}{'WEIGHTED':<12}{n_sum:>6}{'':>8}{'':>8}{totals[h]:>+9.1f}pp")
        print("-" * 68)

    best = max(totals, key=lambda k: totals[k])
    print(f"\nBEST HORIZON: {best}h  (weighted excess {totals[best]:+.1f}pp)")
    print("ranking: " + "  ".join(f"{h}h {totals[h]:+.1f}" for h in
                                  sorted(totals, key=lambda k: -totals[k])))
    print(f"\nselfcheck.py currently uses HORIZON_H = {4.0}")


if __name__ == "__main__":
    main()
