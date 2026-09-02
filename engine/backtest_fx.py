"""Same measurement, run on the FX pairs.

backtest_decode.py and backtest_geo.py scored gold / silver / oil. This scores the
currencies off the SAME events, so the desk's FX reads rest on the same evidence.

Every category gets ONE lean - on the DOLLAR - and the pairs are derived from it:
USD up means EURUSD/GBPUSD/AUDUSD/NZDUSD down and USDJPY/USDCAD/DXY up. That keeps the
table small enough to check by eye, and it is how the dollar actually works: on US news
the dollar leg moves and everything else is the other side of it.

Baseline-corrected exactly as the others are - a raw hit rate on a trending pair flatters
whichever direction the trend ran.

    python backtest_fx.py
"""
import json, sys, urllib.request
import datetime as dt
from pathlib import Path
from collections import defaultdict

import commodity_watch as cw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

CAL_CACHE = Path(__file__).parent / "data" / "backtest_calendar.json"
NEWS_CACHE = Path(__file__).parent / "data" / "backtest_geo_news.json"
MIN_OUTLETS = 2

# (name, yahoo symbol, +1 if the pair RISES when the dollar strengthens)
PAIRS = [("EURUSD", "EURUSD=X", -1), ("GBPUSD", "GBPUSD=X", -1),
         ("USDJPY", "JPY=X", +1), ("AUDUSD", "AUDUSD=X", -1),
         ("NZDUSD", "NZDUSD=X", -1), ("USDCAD", "USDCAD=X", +1),
         ("DXY", "DX-Y.NYB", +1)]

# What each category should do TO THE DOLLAR. (direction, strength 0-3)
USD_LEAN = {
    "rates_up": ("up", 3), "rates_down": ("down", 3),
    "us_data_strong": ("up", 3), "us_data_weak": ("down", 3),
    "inflation_hot": ("up", 2), "inflation_cold": ("down", 2),
    "fed_independence": ("down", 3),        # trust in the dollar is the whole story
    "geo_escalation": ("up", 2),            # haven bid
    "geo_deescalation": ("down", 1),
    "oil_supply_tight": ("up", 1),          # inflation -> rates stay high
    "oil_supply_loose": ("down", 1),
    "tariff": ("up", 2), "risk_off": ("up", 2),
}


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


def events():
    """Every event both other backtests would have fired on, as (when, category)."""
    out = []
    for e in json.loads(CAL_CACHE.read_text(encoding="utf-8")):
        hit = cw.categorise_release(e["title"], e["actual"], e["forecast"])
        if not hit or hit[1] < cw.MIN_IMPACT:
            continue
        when = cw._parse_date(e["date"])
        if when:
            out.append((when, hit[0], "data"))

    byday = defaultdict(lambda: defaultdict(list))
    first = {}
    for n in json.loads(NEWS_CACHE.read_text(encoding="utf-8")):
        hit = cw.classify(n["title"])
        if not hit or cw.IMPACT.get(hit[0], 2) < 3:
            continue
        when = dt.datetime.fromisoformat(n["when"])
        byday[hit[0]][when.date()].append(n["src"])
        first.setdefault((hit[0], when.date()), when)
    for cat, days in byday.items():
        for d, srcs in days.items():
            if len(set(srcs)) >= MIN_OUTLETS:
                out.append((first[(cat, d)], cat, "news"))
    return out


def main():
    evs = events()
    print(f"{len(evs)} events (data + news), high-impact only\n")

    px, dys, base = {}, {}, {}
    for name, sym, _ in PAIRS:
        px[name] = closes(sym)
        dys[name] = sorted(px[name])
        rets = [(px[name][dys[name][i]] - px[name][dys[name][i - 1]])
                / px[name][dys[name][i - 1]] * 100 for i in range(1, len(dys[name]))]
        base[name] = (sum(1 for r in rets if r > 0) / len(rets) * 100,
                      sum(rets) / len(rets))
    print("BASELINE (every session):")
    for name, _, _ in PAIRS:
        print(f"  {name:<7} up {base[name][0]:.0f}% of days, avg {base[name][1]:+.3f}%/day")
    print()

    res = defaultdict(lambda: defaultdict(list))
    for when, cat, _src in evs:
        lean = USD_LEAN.get(cat)
        if not lean or lean[1] == 0:
            continue
        usd_dir = lean[0]
        for name, _sym, sign in PAIRS:
            # the pair's expected direction, given what the dollar should do
            want = usd_dir if sign > 0 else ("down" if usd_dir == "up" else "up")
            r = day_return(px[name], dys[name], when)
            if r is not None:
                res[cat][name].append((r, want))

    print("=" * 76)
    print(f"{'category / pair':<26}{'n':>5}{'hit%':>7}{'base':>7}{'EXCESS':>9}"
          f"{'move vs base':>14}{'verdict':>12}")
    print("=" * 76)
    rows = []
    for cat in sorted(res):
        print(cat)
        for name, _sym, _sign in PAIRS:
            data = res[cat].get(name)
            if not data:
                continue
            n = len(data)
            want = data[0][1]
            rate = sum(1 for r, d in data if (r > 0) == (d == "up")) / n * 100
            avg = sum(r for r, _ in data) / n
            b_up, b_avg = base[name]
            b_rate = b_up if want == "up" else 100 - b_up
            excess = rate - b_rate
            move = (avg - b_avg) if want == "up" else -(avg - b_avg)
            v = ("too few" if n < 20 else "WRONG WAY" if excess <= -8 else
                 "no edge" if excess < 3 else "slight" if excess < 8 else "CONFIRMED")
            rows.append((cat, name, n, rate, b_rate, excess, move, v))
            print(f"  -> {name:<20}{n:>5}{rate:>6.0f}%{b_rate:>6.0f}%{excess:>+8.0f}pp"
                  f"{move:>+13.2f}%{v:>12}")
    print("=" * 76)

    bad = [r for r in rows if r[7] == "WRONG WAY"]
    good = [r for r in rows if r[7] == "CONFIRMED"]
    print(f"\nCONFIRMED: {len(good)}   WRONG WAY: {len(bad)}   of {len(rows)} scored\n")
    for cat, name, n, rate, b, ex, move, _ in bad:
        print(f"  WRONG  {cat}.{name}: {rate:.0f}% vs {b:.0f}% baseline ({ex:+.0f}pp), n={n}")
    for cat, name, n, rate, b, ex, move, _ in good:
        print(f"  GOOD   {cat}.{name}: {rate:.0f}% vs {b:.0f}% ({ex:+.0f}pp), "
              f"{move:+.2f}% vs drift, n={n}")


if __name__ == "__main__":
    main()
