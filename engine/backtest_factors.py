"""Do the new factors actually predict price? An honest, out-of-sample answer.

WHAT THIS TESTS, AND WHAT IT REFUSES TO TEST. Five tabs were added on 2026-09-08 and none of
their legs had ever been measured against realised price. This measures the ones there is
genuine history for and says plainly that the rest cannot be tested yet:

  seasonality   TESTABLE - 15 years of daily bars. Walk-forward: the monthly excess is
                recomputed using ONLY years before the one being predicted, so no month is
                ever scored with knowledge of itself. Anything else would be marking its own
                homework.
  retail        TESTABLE - 3 years of weekly CFTC non-reportable positioning. Percentiles are
                expanding-window (prior weeks only), never full-sample.
  cot           TESTABLE - the same 3 years, Leveraged Funds, for comparison with the retail
                leg and with the existing backtest.py result.
  open interest TESTABLE - 5 years of daily OI from oi_history.json.
  trend / mom   TESTABLE - price only.

  carry, real yield, curve, rate expectations, and the BLENDED score
                NOT TESTABLE. yields_history.json and score_history.json both began on
                2026-09-08 and hold one day. There is no way to measure them yet and this
                module will not pretend otherwise - it prints "insufficient history" and the
                number of days still needed.

METHOD. For each factor and horizon, take the sign the factor implies, compare it with the
realised forward return, and report:
    hit%    how often the direction was right
    mean    average forward return in the direction the factor pointed (this is the number
            that pays; a high hit rate on tiny wins and huge losses is worthless)
    t       mean / standard error - roughly, |t| under 2 is not distinguishable from luck
    n       sample size
A hit rate near 50% with |t| under 2 means the factor did nothing. Say so when that happens.

Bars are cached to data/_bt_bars.json (gitignored) so repeated runs do not re-hit Yahoo.

    python backtest_factors.py            # everything
    python backtest_factors.py seasonality # one section
"""
import json, math, statistics, sys, urllib.request, datetime as dt
from config import DATA, ORDER, CURRENCIES, COMMODITIES, COMMODITY_ORDER, all_pairs

HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
CHART = "{host}/v8/finance/chart/{sym}?range=15y&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CACHE = DATA / "_bt_bars.json"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# The USD pair that carries each currency's move, and which way round it reads.
# +1 means the pair rises when the currency strengthens.
CCY_PAIR = {"EUR": ("EURUSD=X", 1), "GBP": ("GBPUSD=X", 1), "AUD": ("AUDUSD=X", 1),
            "NZD": ("NZDUSD=X", 1), "JPY": ("USDJPY=X", -1), "CAD": ("USDCAD=X", -1),
            "CHF": ("USDCHF=X", -1), "USD": ("DX-Y.NYB", 1)}
CM_YAHOO = {s: COMMODITIES[s]["yahoo"] for s in COMMODITY_ORDER}


# ----------------------------------------------------------------------------- price bars
def _fetch(sym):
    last = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(CHART.format(host=host, sym=sym), headers=UA)
            return urllib.request.urlopen(req, timeout=45).read()
        except Exception as e:                                   # noqa: BLE001
            last = e
    raise last


def bars(symbols):
    """{sym: [(iso date, close)]} oldest first, cached on disk."""
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    need = [s for s in symbols if s not in cache]
    for i, s in enumerate(need, 1):
        try:
            res = (json.loads(_fetch(s).decode("utf-8", "replace")).get("chart") or {}).get("result")
            r = res[0]
            ts, cl = r.get("timestamp") or [], (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            cache[s] = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), c)
                        for t, c in zip(ts, cl) if c]
            print(f"    fetched {s} ({len(cache[s])} bars)  [{i}/{len(need)}]")
        except Exception as e:                                   # noqa: BLE001
            print(f"    {s} FAILED: {type(e).__name__}")
            cache[s] = []
    if need:
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return {s: [(d, c) for d, c in cache.get(s, [])] for s in symbols}


def _stats(vals):
    n = len(vals)
    if n < 3:
        return None
    m = statistics.fmean(vals)
    sd = statistics.stdev(vals)
    se = sd / math.sqrt(n) if sd else 0.0
    return {"n": n, "mean": m, "t": (m / se if se else 0.0),
            "hit": sum(1 for v in vals if v > 0) / n * 100}


def _row(label, st, extra=""):
    if not st:
        return f"  {label:26} insufficient sample"
    verdict = "works" if abs(st["t"]) >= 2 and st["hit"] >= 52 else (
        "no edge" if abs(st["t"]) < 2 else "inverted")
    return (f"  {label:26} hit {st['hit']:5.1f}%   mean {st['mean']:+6.3f}%   "
            f"t {st['t']:+5.2f}   n {st['n']:>5}   {verdict}{extra}")


# ------------------------------------------------------------------- 1. seasonality (WF)
def _month_ends(b):
    ends = {}
    for d, c in b:
        y, m = int(d[:4]), int(d[5:7])
        ends[(y, m)] = c
    keys = sorted(ends)
    return {cur: (ends[cur] - ends[prev]) / ends[prev] * 100
            for prev, cur in zip(keys, keys[1:]) if ends[prev]}


def test_seasonality(min_train_years=6):
    """Walk-forward. For each year, build the seasonal profile from EARLIER years only, then
    trade the current month in the direction that profile implies."""
    syms = {}
    for p in all_pairs():
        syms[p] = f"{p}=X"
    syms.update(CM_YAHOO)
    syms["DXY"] = "DX-Y.NYB"
    data = bars(sorted(set(syms.values())))

    # TWO tests, because the obvious one is mis-specified. The dashboard's signal is the
    # EXCESS - "this month beats a normal month for this instrument". Trading that against
    # ABSOLUTE returns is a mismatch: on an instrument that drifts upward, shorting a
    # below-average month loses to the drift even when the seasonal call was right. So:
    #   raw    -> absolute return : "does the month tend to be up or down" - tradeable as-is
    #   excess -> excess return   : "does the month beat a normal month" - the honest test of
    #                               the signal the tab actually shows
    abs_rets, exc_rets = [], []
    per_inst = {}
    for label, sym in syms.items():
        b = data.get(sym) or []
        if len(b) < 1500:
            continue
        m = _month_ends(b)
        years = sorted({y for y, _ in m})
        a_r, e_r = [], []
        for y in years[min_train_years:]:
            train = {k: v for k, v in m.items() if k[0] < y}
            if len(train) < min_train_years * 12 * 0.7:
                continue
            drift = statistics.fmean(train.values())
            for mo in range(1, 13):
                if (y, mo) not in m:
                    continue
                prior = [v for (yy, mm), v in train.items() if mm == mo]
                if len(prior) < min_train_years - 1:
                    continue
                raw = statistics.fmean(prior)
                excess = raw - drift
                actual = m[(y, mo)]
                if abs(raw) >= 0.15:
                    a_r.append(actual if raw > 0 else -actual)
                if abs(excess) >= 0.15:
                    # measured against the EXCESS return, matching the signal
                    ae = actual - drift
                    e_r.append(ae if excess > 0 else -ae)
        if len(a_r) >= 20:
            per_inst[label] = _stats(e_r)
            abs_rets.extend(a_r)
            exc_rets.extend(e_r)
    print("\n1. SEASONALITY  (walk-forward: profile built only from earlier years)")
    print(_row("raw month -> abs return", _stats(abs_rets), "   (tradeable as-is)"))
    print(_row("excess -> excess return", _stats(exc_rets), "   (what the tab shows)"))
    ranked = sorted((k for k in per_inst if per_inst[k]), key=lambda k: -per_inst[k]["t"])
    for k in ranked[:3]:
        print(_row(f"  best: {k}", per_inst[k]))
    for k in ranked[-2:]:
        print(_row(f"  worst: {k}", per_inst[k]))
    return _stats(exc_rets)


# --------------------------------------------------------- 2. retail + 3. COT positioning
def _weekly_series(hist, sym, cat, field="net"):
    out = []
    for wk in sorted(hist):
        row = (hist[wk] or {}).get(sym) or {}
        g, oi = row.get(cat), row.get("open_interest")
        if not g or not oi or g.get(field) is None:
            continue
        out.append((wk, g[field] / oi * 100))
    return out


def _fwd(b, from_date, weeks):
    """% change from the close on/after `from_date` to `weeks` later."""
    idx = next((i for i, (d, _) in enumerate(b) if d >= from_date), None)
    if idx is None:
        return None
    j = idx + weeks * 5
    if j >= len(b):
        return None
    a, z = b[idx][1], b[j][1]
    return (z - a) / a * 100 if a else None


def test_positioning(cat, title, contrarian, horizons=(1, 4, 12)):
    fx = json.loads((DATA / "cot_history.json").read_text(encoding="utf-8"))
    cm = json.loads((DATA / "cot_history_commodity.json").read_text(encoding="utf-8"))
    syms = {c: CCY_PAIR[c] for c in ORDER if c in CCY_PAIR}
    data = bars(sorted({s for s, _ in syms.values()} | set(CM_YAHOO.values())))

    print(f"\n{title}")
    for h in horizons:
        rets = []
        for c, (sym, sign) in syms.items():
            b = data.get(sym) or []
            ser = _weekly_series(fx, c, cat)
            vals = [v for _, v in ser]
            for i in range(30, len(ser)):
                past = vals[:i]                    # expanding window - no lookahead
                lo, hi = min(past), max(past)
                if hi <= lo:
                    continue
                p = (vals[i] - lo) / (hi - lo) * 100
                if 20 < p < 80:                    # only act at an extreme
                    continue
                f = _fwd(b, ser[i][0], h)
                if f is None:
                    continue
                view = -1 if p >= 80 else 1        # crowded long -> expect down
                if not contrarian:
                    view = -view
                rets.append(view * sign * f * (1 if sign > 0 else 1))
        # commodities, Managed Money / non-reportable in the disaggregated file
        for s in COMMODITY_ORDER:
            b = data.get(CM_YAHOO[s]) or []
            ccat = "managed_money" if cat == "leveraged" else cat
            ser = _weekly_series(cm, s, ccat)
            vals = [v for _, v in ser]
            for i in range(30, len(ser)):
                past = vals[:i]
                lo, hi = min(past), max(past)
                if hi <= lo:
                    continue
                p = (vals[i] - lo) / (hi - lo) * 100
                if 20 < p < 80:
                    continue
                f = _fwd(b, ser[i][0], h)
                if f is None:
                    continue
                view = -1 if p >= 80 else 1
                if not contrarian:
                    view = -view
                rets.append(view * f)
        print(_row(f"{h:>2}-week horizon", _stats(rets)))


# ------------------------------------------------------------------------ 4. trend / mom
def test_trend():
    syms = {c: CCY_PAIR[c] for c in ORDER if c in CCY_PAIR}
    data = bars(sorted({s for s, _ in syms.values()}))
    print("\n5. TREND / MOMENTUM  (price above its 50-day, hold 4 weeks)")
    for look, hold in ((50, 20), (20, 5)):
        rets = []
        for c, (sym, _) in syms.items():
            b = data.get(sym) or []
            cl = [x for _, x in b]
            for i in range(look, len(cl) - hold):
                ma = statistics.fmean(cl[i - look:i])
                view = 1 if cl[i] > ma else -1
                rets.append(view * (cl[i + hold] - cl[i]) / cl[i] * 100)
        print(_row(f"{look}-day MA, hold {hold}d", _stats(rets)))


# ------------------------------------------------------------------- 5. what cannot be run
def report_untestable():
    print("\n6. NOT TESTABLE YET  (no history - these are NOT results, they are gaps)")
    for f, need, what in (("yields_history.json", 60, "carry / real yield / curve"),
                          ("score_history.json", 90, "the blended score itself"),
                          ("expectations_history.json", 60, "rate expectations")):
        p = DATA / f
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            have = len(d) if isinstance(d, (dict, list)) else 0
        except Exception:
            have = 0
        print(f"  {what:26} {have} day(s) of history, needs ~{need} - "
              f"insufficient, no verdict offered")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("=" * 78)
    print("FACTOR BACKTEST - out-of-sample where the history allows it")
    print("=" * 78)
    if which in ("all", "seasonality"):
        test_seasonality()
    if which in ("all", "retail"):
        test_positioning("nonrept", "2. RETAIL CROWD  (CFTC non-reportable, read CONTRARIAN "
                                    "at the extremes)", contrarian=True)
    if which in ("all", "cot"):
        test_positioning("leveraged", "3. SPECULATIVE COT  (Leveraged Funds, read CONTRARIAN "
                                      "at the extremes)", contrarian=True)
        test_positioning("leveraged", "4. SPECULATIVE COT  (same, read as MOMENTUM instead)",
                         contrarian=False)
    if which in ("all", "trend"):
        test_trend()
    if which in ("all",):
        report_untestable()
    print("\n" + "=" * 78)
    print("hit% = direction right. mean = average forward move in the direction taken.")
    print("t = mean/standard error; |t| under 2 is not distinguishable from luck.")
    print("=" * 78)
