"""Lagged backtest of the COT positioning signal against FORWARD returns.

WHY THIS EXISTS
---------------
validate.py compares today's score against price that has already happened. That is
backward-looking: a score built from an 18 Aug COT report cannot explain a move that
started on 17 Jul. It can flag a broken sign, but it cannot show the signal works.

This does it properly:
    score from the COT report dated Tuesday T
    -> entered at the Friday RELEASE (T+3), because that is when you could actually know it
    -> measured over the FOLLOWING week's return
and repeats it over as many weeks of history as tradingster will serve.

Also settles which trader category to use on out-of-sample weeks rather than on one snapshot.

    python backtest.py [weeks]      default 16

Results cache to data/cot_history.json so re-runs are cheap.
"""
import json, math, sys, time, urllib.request, datetime as dt
from config import DATA, ORDER, CURRENCIES
from fetch_cot import parse

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
HIST = "https://www.tradingster.com/cot/futures/fin/{code}/{date}"
FX_API = "https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols={syms}"
XCCY = [c for c in ORDER if c != "USD"]
CACHE = DATA / "cot_history.json"


def tuesdays(n):
    """The last n COT report dates (Tuesdays), skipping the current unreleased one."""
    d = dt.date.today()
    d -= dt.timedelta(days=(d.weekday() - 1) % 7)      # back to Tuesday
    if (dt.date.today() - d).days < 3:                  # not yet released (Friday)
        d -= dt.timedelta(days=7)
    return [d - dt.timedelta(days=7 * i) for i in range(n)][::-1]


def load_history(dates):
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    fetched = 0
    for d in dates:
        key = d.isoformat()
        cache.setdefault(key, {})
        for ccy, meta in CURRENCIES.items():
            if ccy in cache[key]:
                continue
            url = HIST.format(code=meta["cot"], date=key)
            try:
                html = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=40).read().decode("utf-8", "replace")
                rec = parse(html)
                # tradingster serves the nearest report; only keep an exact date match
                cache[key][ccy] = rec if rec.get("report_date") == key else None
            except Exception:
                cache[key][ccy] = None
            fetched += 1
            time.sleep(0.35)
        print(f"  {key}: {sum(1 for v in cache[key].values() if v)}/{len(CURRENCIES)} ok", flush=True)
    if fetched:
        CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return cache


def cot_score(rec, cat):
    g, oi = rec.get(cat), rec.get("open_interest")
    if not g or not oi:
        return None
    return 0.6 * 100 * math.tanh(g["net"] / oi * 3.0) + \
           0.4 * 100 * math.tanh(g["net_chg"] / oi * 20.0)


def prices(start, end):
    url = FX_API.format(start=start, end=end, syms=",".join(XCCY))
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read()
    return json.loads(raw.decode("utf-8", "replace"))["rates"]


def on_or_before(days, target):
    c = [d for d in days if d <= target]
    return c[-1] if c else None


def fwd(fx, days, a, b):
    da, db = on_or_before(days, a.isoformat()), on_or_before(days, b.isoformat())
    if not da or not db or da == db:
        return None
    ra, rb = fx[da], fx[db]
    m = {c: -(rb[c] - ra[c]) / ra[c] * 100 for c in XCCY if c in ra and c in rb}
    if len(m) < len(XCCY):
        return None
    m["USD"] = -sum(m.values()) / len(m)
    return m


def spearman(sa, sb, keys):
    keys = [k for k in keys if k in sa and k in sb and sa[k] is not None]
    n = len(keys)
    if n < 3:
        return None
    a = sorted(keys, key=lambda c: -sa[c])
    b = sorted(keys, key=lambda c: -sb[c])
    d2 = sum((a.index(c) - b.index(c)) ** 2 for c in keys)
    return 1 - 6 * d2 / (n * (n * n - 1))


def main():
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    dates = tuesdays(weeks)
    print(f"COT reports {dates[0]} .. {dates[-1]} ({len(dates)} weeks)")
    hist = load_history(dates)

    fx = prices(dates[0], dt.date.today())
    days = sorted(fx)

    results = {"asset_manager": [], "leveraged": []}
    per_week = []
    for d in dates:
        recs = hist.get(d.isoformat(), {})
        entry = d + dt.timedelta(days=3)               # Friday release
        exit_ = entry + dt.timedelta(days=7)
        m = fwd(fx, days, entry, exit_)
        if not m:
            continue
        row = {"date": d.isoformat()}
        for cat in results:
            sc = {c: cot_score(r, cat) for c, r in recs.items() if r}
            sc = {c: v for c, v in sc.items() if v is not None}
            rho = spearman(sc, m, ORDER)
            if rho is not None:
                results[cat].append(rho)
                row[cat] = rho
        if len(row) > 1:
            per_week.append(row)

    print(f"\nFORWARD-WEEK rank correlation, COT score at Friday release -> next week's move")
    print(f"{'report':12}{'asset_manager':>15}{'leveraged':>12}")
    for r in per_week:
        am = f"{r['asset_manager']:+.2f}" if "asset_manager" in r else "n/a"
        lf = f"{r['leveraged']:+.2f}" if "leveraged" in r else "n/a"
        print(f"{r['date']:12}{am:>15}{lf:>12}")

    print()
    for cat, vals in results.items():
        if not vals:
            print(f"  {cat:14} no usable weeks")
            continue
        mean = sum(vals) / len(vals)
        pos = sum(1 for v in vals if v > 0)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        se = sd / math.sqrt(len(vals)) if len(vals) > 1 else float("nan")
        print(f"  {cat:14} mean rho {mean:+.3f}  |  positive {pos}/{len(vals)} weeks"
              f"  |  std err {se:.3f}  ->  t {mean/se:+.2f}" if se else "")
    print("\n  A mean rho near 0 means the COT leg carries little forward information at this")
    print("  horizon. Negative means it is contrarian. Judge the category choice on THIS,")
    print("  not on a single snapshot against past price.")


if __name__ == "__main__":
    main()
