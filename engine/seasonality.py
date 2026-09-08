"""Seasonal bias per pair, metal and index - which months have actually paid, and by how much.

THE TRAP THIS MODULE IS BUILT AROUND. A raw average monthly return embeds the instrument's
whole-period drift. The S&P has risen for fifteen years, so on raw numbers every one of its
twelve months looks bullish and the table says nothing. The same thing quietly flatters any
trending pair. So the headline figure here is the EXCESS: a month's average return minus the
instrument's own average monthly return over the same history. Excess is what "seasonality"
is supposed to mean - this month against a normal month for this instrument - and it is what
the score is built on. The raw figure is kept beside it so the drift stays visible.

Second guard: fifteen years gives fifteen observations per month, which is not many. Every
month carries its hit rate, its sample size and a t-statistic on the excess, and `reliable`
is only true at |t| >= 1.8. Months below that are still shown, and scored at half weight.
A month whose mean is strong but whose hit rate is a coin flip is flagged `tail_driven`: it
pays on average, out of a few big years, not in most of them.

Read the whole tab with this in mind: 34 instruments x 12 months is 408 tests, so a handful
of |t| >= 1.8 readings are there by chance alone. A seasonal is a reason to look, never a
reason to trade on its own.

Source: Yahoo daily closes, 15 years, the same endpoint fetch_prices.py already uses.
History barely changes, so this refreshes at most every REFRESH_DAYS days; pass force=True
(or `python seasonality.py force`) to override.

Writes data/seasonality.json - computed statistics only, never the raw bars.
"""
import json, math, sys, urllib.request, datetime as dt
from config import DATA, COMMODITIES, COMMODITY_ORDER, INDICES, INDEX_ORDER, all_pairs

HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
CHART = "{host}/v8/finance/chart/{sym}?range=15y&interval=1d"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = DATA / "seasonality.json"
REFRESH_DAYS = 7
MIN_YEARS = 8              # below this a month is not scored at all
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _fetch(sym):
    last = None
    for host in HOSTS:
        try:
            req = urllib.request.Request(CHART.format(host=host, sym=sym), headers=UA)
            return urllib.request.urlopen(req, timeout=45).read()
        except Exception as e:                                   # noqa: BLE001
            last = e
    raise last


def _bars(sym):
    """[(date, close)] oldest first."""
    res = (json.loads(_fetch(sym).decode("utf-8", "replace")).get("chart") or {}).get("result")
    if not res:
        raise RuntimeError("no result block")
    r = res[0]
    ts = r.get("timestamp") or []
    cl = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    out = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date(), c)
           for t, c in zip(ts, cl) if c]
    if len(out) < 500:
        raise RuntimeError(f"only {len(out)} bars")
    return out


def _monthly(bars):
    """{(year, month): % return} from the last close of each month."""
    ends = {}
    for d, c in bars:
        ends[(d.year, d.month)] = c
    keys = sorted(ends)
    out = {}
    for prev, cur in zip(keys, keys[1:]):
        p = ends[prev]
        if p:
            out[cur] = (ends[cur] - p) / p * 100
    return out


def _stats(vals):
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    se = sd / math.sqrt(n) if n else 0.0
    return {"mean": mean, "sd": sd, "se": se,
            "hit": sum(1 for v in vals if v > 0) / n * 100, "n": n}


def _weekly_curve(bars):
    """Average cumulative path through a calendar year, in %, one point per ISO week. Each
    year is rebased to 0 at its first bar, then the years are averaged - so this is the shape
    of a typical year, drift included (the shape is the point; the score does not use it)."""
    by_year = {}
    for d, c in bars:
        by_year.setdefault(d.year, []).append((d, c))
    acc = {}
    for yr, rows in by_year.items():
        if len(rows) < 200:                     # skip stub years at either end
            continue
        base = rows[0][1]
        for d, c in rows:
            acc.setdefault(min(52, d.isocalendar()[1]), []).append((c - base) / base * 100)
    return [round(sum(acc[w]) / len(acc[w]), 2) if acc.get(w) else None
            for w in range(1, 53)]


def _one(sym, label):
    bars = _bars(sym)
    m = _monthly(bars)
    drift = sum(m.values()) / len(m) if m else 0.0        # the instrument's normal month
    months = []
    for i, name in enumerate(MONTHS, start=1):
        vals = [v for (y, mo), v in m.items() if mo == i]
        st = _stats(vals)
        if not st:
            months.append({"month": name, "n": 0, "reliable": False})
            continue
        excess = st["mean"] - drift
        # the t-statistic is on the EXCESS, not the raw mean - a month that only looks good
        # because the instrument rose all decade is not a seasonal effect
        ex_t = (excess / st["se"]) if st["se"] else 0.0
        # Reliability is the t-test on the excess, nothing else. An earlier cut also demanded
        # a hit rate decisively off 50% and that was wrong: it deleted the real effects. The
        # September equity swoon is the example - a big negative mean produced by a handful of
        # severe Septembers while the median one is flat, so the hit rate sits near 50% and the
        # AND-gate called the single best-known seasonal in markets unreliable. Hit rate is
        # kept as CONTEXT instead, and a strong mean with a coin-flip hit rate is flagged
        # `tail_driven` - it pays on average but not most years, which changes how it is traded.
        months.append({
            "month": name, "n": st["n"],
            "raw": round(st["mean"], 2), "excess": round(excess, 2),
            "hit": round(st["hit"]), "t": round(ex_t, 2),
            "reliable": bool(st["n"] >= MIN_YEARS and abs(ex_t) >= 1.8),
            "tail_driven": bool(st["n"] >= MIN_YEARS and abs(ex_t) >= 1.8
                                and 45 <= st["hit"] <= 55),
        })
    today = dt.date.today()
    cur = months[today.month - 1]
    nxt = months[today.month % 12]
    # score: this month's excess, scaled so 1.5% of excess is full marks, halved when the
    # month did not clear the reliability bar
    ex = cur.get("excess")
    if ex is None or cur["n"] < MIN_YEARS:
        score = None
    else:
        score = max(-100.0, min(100.0, ex / 1.5 * 100)) * (1.0 if cur["reliable"] else 0.5)
        score = round(score, 1)
    return {"symbol": sym, "label": label, "years": round(len(bars) / 252, 1),
            "drift_per_month": round(drift, 3),
            "months": months, "curve": _weekly_curve(bars),
            "this_month": cur, "next_month": nxt, "score": score,
            "asof": bars[-1][0].isoformat()}


def _targets():
    t = [(f"{p}=X", p, "fx") for p in all_pairs()]
    t += [(COMMODITIES[s]["yahoo"], s, "commodity") for s in COMMODITY_ORDER]
    t += [(INDICES[s]["yahoo"], s, "index") for s in INDEX_ORDER]
    return t


def build(force=False):
    now = dt.datetime.now(dt.timezone.utc)
    prev_inst = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            prev_inst = prev.get("instruments", {})
            age = (now - dt.datetime.fromisoformat(prev["fetched_at"])).days
            if not force and age < REFRESH_DAYS and prev_inst:
                print(f"  cached ({age}d old, {len(prev_inst)} instruments) - "
                      f"refreshes every {REFRESH_DAYS}d")
                return prev
        except Exception:
            pass

    inst, failed = {}, []
    for sym, label, kind in _targets():
        try:
            d = _one(sym, label)
            d["kind"] = kind
            inst[label] = d
        except Exception as e:                                   # noqa: BLE001
            # keep the last good statistics rather than dropping the instrument off the tab
            if prev_inst.get(label):
                inst[label] = {**prev_inst[label], "stale": True}
            failed.append(f"{label} ({type(e).__name__})")
    if failed:
        print(f"  {len(failed)} failed, cached kept where available: {', '.join(failed[:8])}"
              + (" ..." if len(failed) > 8 else ""))

    out = {"fetched_at": now.isoformat(), "month": MONTHS[dt.date.today().month - 1],
           "next_month": MONTHS[dt.date.today().month % 12],
           "min_years": MIN_YEARS, "instruments": inst,
           "ranked": sorted([k for k, v in inst.items() if v.get("score") is not None],
                            key=lambda k: -abs(inst[k]["score"]))}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    reliable = sum(1 for v in inst.values() if (v.get("this_month") or {}).get("reliable"))
    print(f"  {len(inst)} instruments, {reliable} with a reliable {out['month']} bias")
    return out


if __name__ == "__main__":
    r = build(force="force" in sys.argv)
    m = r["month"]
    print(f"\n  Strongest {m} seasonals (excess over a normal month for that instrument):")
    for k in r["ranked"][:14]:
        d = r["instruments"][k]
        t = d["this_month"]
        star = "*" if t.get("reliable") else " "
        print(f"   {star}{k:8} excess {t['excess']:+6.2f}%  raw {t['raw']:+6.2f}%  "
              f"hit {t['hit']:>3}%  n={t['n']:<3} t={t['t']:+5.2f}  score {d['score']:+6.1f}")
    print("   * = clears the reliability bar (|t| >= 1.8 on the excess). 408 tests in this "
          "table - treat a lone star as a prompt to look, not a signal.")
