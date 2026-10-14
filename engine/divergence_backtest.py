"""Step 3 - baseline historical replay of the COT x Myfxbook retail divergence.

What the dashboard WOULD HAVE SHOWN at each genuine Myfxbook snapshot, and what the market
did afterwards. Pure functions: every input is passed in (no network, no clock), so the same
inputs always give the same output (divergence_backtest_run.py does the I/O).

    replay(history_rows, cot_store, prices, ...) -> {"observations", "events", "stats", ...}

NO LOOK-AHEAD
  * Myfxbook: only real archived snapshots (timestamped rows of our production feed). Nothing
    is reconstructed, interpolated or carried between snapshots.
  * COT: a report is usable only from its AVAILABILITY time - never from its Tuesday
    positions date. cot_available_at(): the scheduled Friday 15:30 US-Eastern release, pushed
    one business day for every US federal holiday from the Monday of the report week to the
    release day (CFTC delays releases in holiday weeks); if production recorded when it first
    saw that report, the later of the two. A snapshot sees only reports available at or
    before its own timestamp, and the 50-week percentile window ends at that report.
  * Classification: divergence.evaluate() itself - the frozen Step 2 code and configuration -
    with "now" = the snapshot time. Nothing is re-implemented.
  * Prices: entry is the OPEN of the first daily bar dated after the snapshot's UTC date; a
    horizon of h trading days exits at the CLOSE of the h-th bar. Later bars are never read
    for an earlier horizon, and a horizon whose bars do not exist yet is "pending".
"""
import datetime as dt
import hashlib
import json
import math
import statistics as stx

import divergence_backtest_config as bc
import cot_table as ct
import divergence as dv
import divergence_config as dcfg

UTC = dt.timezone.utc
BULL = ("strong_bull", "bull")
BEAR = ("strong_bear", "bear")
ACTIONABLE = BULL + BEAR
STRONG = ("strong_bull", "strong_bear")


def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- strategy fingerprint
def fingerprint():
    """Hash of everything that defines the Step 2 classification. Must equal
    divergence_backtest_config.BASELINE_FINGERPRINT for a run to be called the baseline."""
    spec = {"rules": dcfg.RULES, "none": dcfg.NONE_LABEL, "orientation": dcfg.ORIENTATION,
            "cot_weeks": dcfg.COT_WEEKS, "cot_max_age": dcfg.COT_MAX_AGE_DAYS,
            "retail_max_age": dcfg.RETAIL_MAX_AGE_MIN, "min_positions": dv.mf.MIN_POSITIONS}
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=list).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- COT availability
def _nth(year, month, weekday, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last(year, month, weekday):
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d):
    return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d


def us_federal_holidays(year):
    """Observed US federal holidays (the days CFTC does not publish)."""
    return {_observed(dt.date(year, 1, 1)), _nth(year, 1, 0, 3), _nth(year, 2, 0, 3),
            _last(year, 5, 0), _observed(dt.date(year, 6, 19)), _observed(dt.date(year, 7, 4)),
            _nth(year, 9, 0, 1), _nth(year, 10, 0, 2), _observed(dt.date(year, 11, 11)),
            _nth(year, 11, 3, 4), _observed(dt.date(year, 12, 25))} | (
        {_observed(dt.date(year + 1, 1, 1))} if _observed(dt.date(year + 1, 1, 1)).year == year else set())


def _business_days_after(d, n, hol):
    while n > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5 and d not in hol:
            n -= 1
    return d


def cot_available_at(report_date, first_seen=None):
    """When a COT report (positions date, ISO) could first have been on the dashboard."""
    rd = dt.date.fromisoformat(report_date)
    sched = ct.release_at(rd)                                   # Friday 15:30 ET, UTC
    fri = sched.date()
    monday = rd - dt.timedelta(days=rd.weekday())
    hol = us_federal_holidays(rd.year) | us_federal_holidays(fri.year)
    n = sum(1 for k in range((fri - monday).days + 1) if (monday + dt.timedelta(days=k)) in hol)
    if n:
        day = _business_days_after(fri, n, hol)
        t = dt.datetime.combine(day, dt.time(15, 30), UTC)
        sched = t - dt.timedelta(hours=ct.eastern_offset_h(t))
    if first_seen:
        # production recorded when it first had this report: that IS what the dashboard knew
        # (never before the scheduled Friday release)
        return max(ct.release_at(rd), _ts(first_seen))
    return sched


def cot_as_known(store, t, first_seen=None, _cache={}):
    """A copy of the CFTC store holding only reports available at time t (point in time)."""
    first_seen = first_seen or {}
    avail = {}
    for c in store.get("contracts", {}).values():
        for d in c.get("weeks", {}):
            if d not in avail:
                avail[d] = cot_available_at(d, first_seen.get(d))
    known = {d for d, a in avail.items() if a <= t}
    key = (id(store), frozenset(known))
    if key not in _cache:
        _cache.clear()
        s = {k: v for k, v in store.items() if k != "contracts"}
        s["contracts"] = {code: {**c, "weeks": {d: v for d, v in c.get("weeks", {}).items() if d in known}}
                          for code, c in store.get("contracts", {}).items()}
        _cache[key] = s
    return _cache[key]


# ---------------------------------------------------------------- Myfxbook snapshots
def _num(v, kind=float):
    try:
        x = kind(float(v))
        return x
    except (TypeError, ValueError):
        return v                                   # left as is -> divergence flags it malformed


def snapshots(rows):
    """History rows (dicts, HIST_FIELDS) -> [(timestamp, snapshot)] oldest first. One snapshot
    per production fetch timestamp; only the rows really recorded at that timestamp."""
    by = {}
    for r in rows:
        ts, mk = r.get("timestamp"), r.get("market")
        if not ts or not mk:
            continue
        by.setdefault(ts, {})[mk] = {
            "symbol": r.get("symbol"), "longPercentage": _num(r.get("longPercentage")),
            "shortPercentage": _num(r.get("shortPercentage")),
            "longPositions": _num(r.get("longPositions"), int), "shortPositions": _num(r.get("shortPositions"), int)}
    return [(ts, {"fetched_at": ts, "markets": by[ts]}) for ts in sorted(by)]


# ---------------------------------------------------------------- prices
def _bars(series):
    """{dates, open, high, low, close} -> [(date, o, h, l, c)] oldest first; None where absent."""
    if not series or not series.get("dates"):
        return []
    out = []
    for i, d in enumerate(series["dates"]):
        vals = [(series.get(k) or [None] * (i + 1))[i] if i < len(series.get(k) or []) else None
                for k in ("open", "high", "low", "close")]
        out.append((d, *vals))
    return sorted(out)


def forward(bars, t, direction, horizons=bc.HORIZONS, asof=None):
    """Forward performance of a signal observed at t. Returns {h: {...}} with state 'ok',
    'pending' (bars not there yet) or 'missing' (a gap / absent value)."""
    out = {}
    d0 = t.date()
    idx = next((i for i, b in enumerate(bars) if b[0] > d0.isoformat()), None)
    if idx is None:
        return {h: {"state": "pending"} for h in horizons}
    entry_d = dt.date.fromisoformat(bars[idx][0])
    if (entry_d - d0).days > bc.MAX_ENTRY_GAP_DAYS:
        return {h: {"state": "missing", "why": f"no price bar within {bc.MAX_ENTRY_GAP_DAYS} days"} for h in horizons}
    entry = bars[idx][1]
    if entry is None:
        return {h: {"state": "missing", "why": f"no open price on {bars[idx][0]}"} for h in horizons}
    for h in horizons:
        win = bars[idx: idx + h]
        if len(win) < h:
            out[h] = {"state": "pending"}
            continue
        if asof and win[-1][0] > asof:
            out[h] = {"state": "pending"}
            continue
        gaps = [(dt.date.fromisoformat(b[0]) - dt.date.fromisoformat(a[0])).days for a, b in zip(win, win[1:])]
        if any(g > bc.MAX_BAR_GAP_DAYS for g in gaps):
            out[h] = {"state": "missing", "why": f"gap of more than {bc.MAX_BAR_GAP_DAYS} days in the price bars"}
            continue
        if any(v is None for b in win for v in b[1:]):
            out[h] = {"state": "missing", "why": "a price value is missing in the window"}
            continue
        exit_ = win[-1][4]
        ret = (exit_ / entry - 1) * 100
        hi, lo = max(b[2] for b in win), min(b[3] for b in win)
        up, down = (hi / entry - 1) * 100, (lo / entry - 1) * 100
        mfe, mae = (up, down) if direction > 0 else (-down, -up)
        out[h] = {"state": "ok", "entry_date": bars[idx][0], "entry": entry, "exit_date": win[-1][0],
                  "exit": exit_, "ret": round(ret, 6), "dir_ret": round(ret * direction, 6),
                  "correct": ret * direction > 0, "mfe": round(mfe, 6), "mae": round(mae, 6)}
    return out


# ---------------------------------------------------------------- replay
def replay(history_rows, cot_store, prices, first_seen=None, asof=None, horizons=bc.HORIZONS, production=None):
    """The whole baseline: observations, events and statistics. `prices` = {market: series}
    as written by fetch_bt_prices.py; `asof` (ISO date) caps which price bars may be used.
    `production` (a set of snapshot timestamps, or None for all): only snapshots the
    PRODUCTION feed made count - genuine verification fetches (manual / CI) are excluded with
    that reason, since the dashboard never showed them."""
    first_seen = first_seen or {}
    snaps = snapshots(history_rows)
    bars = {k: _bars(prices.get(k)) for k in dcfg.ORIENTATION}
    obs, excluded = [], []
    if production is not None:
        excluded += [{"ts": ts, "market": "*", "why": ["not a production snapshot (verification fetch)"]}
                     for ts, _ in snaps if ts not in production]
        snaps = [(ts, s) for ts, s in snaps if ts in production]
    for ts, snap in snaps:
        t = _ts(ts)
        store = cot_as_known(cot_store, t, first_seen)
        latest, _ = ct.pick_weeks(store) if store.get("contracts") else (None, None)
        for k in dcfg.ORIENTATION:
            r = dv.evaluate(k, store, snap, t, True)
            o = {"ts": ts, "market": k, "cls": r["cls"], "label": r["label"],
                 "cot_report": latest, "cot_available_at": _iso(cot_available_at(latest, first_seen.get(latest))) if latest else None}
            if r["cls"] == "unavailable":
                o["reasons"] = r["reasons"]
                excluded.append({"ts": ts, "market": k, "why": r["reasons"]})
            else:
                c, rt = r["cot"], r["retail"]
                o.update(pct=round(c["pct"], 4), net=c["net"], momentum=c["momentum"],
                         long=rt["long"], short=rt["short"], positions=rt["positions"], symbol=rt["symbol"])
            if r["cls"] in ACTIONABLE:
                o["dir"] = 1 if r["cls"] in BULL else -1
                o["fwd"] = forward(bars[k], t, o["dir"], horizons, asof) if bars[k] else \
                    {h: {"state": "missing", "why": "no price series for this market"} for h in horizons}
            obs.append(o)
    events = make_events(obs)
    return {"observations": obs, "events": events, "excluded": excluded,
            "snapshots": [ts for ts, _ in snaps], "stats": stats(obs, events, horizons)}


def make_events(obs):
    """Collapse runs of the same actionable class per market (rules in divergence_backtest_config):
    a new event starts at the first snapshot of a class that differs from the previous
    snapshot's class for that market (none / unavailable / another class, incl. Bearish ->
    Strong Bearish), or after a gap of more than EVENT_MAX_GAP_MIN between snapshots."""
    by = {}
    for o in obs:
        by.setdefault(o["market"], []).append(o)
    events = []
    for k, seq in by.items():
        seq = sorted(seq, key=lambda o: o["ts"])
        cur, prev = None, None
        for o in seq:
            t = _ts(o["ts"])
            gap = prev is not None and (t - _ts(prev["ts"])).total_seconds() / 60 > bc.EVENT_MAX_GAP_MIN
            if o["cls"] in ACTIONABLE and (cur is None or gap or prev["cls"] != o["cls"]):
                why = ("first snapshot" if prev is None else
                       f"after a {int((t - _ts(prev['ts'])).total_seconds() // 60)}-minute gap" if gap else
                       f"{prev['cls']} -> {o['cls']}")
                cur = {"id": f"{k}@{o['ts']}", "market": k, "cls": o["cls"], "label": o["label"], "dir": o["dir"],
                       "start": o["ts"], "end": o["ts"], "n_obs": 0, "pct": o["pct"], "long": o["long"],
                       "short": o["short"], "positions": o["positions"], "symbol": o["symbol"],
                       "cot_report": o["cot_report"], "cot_available_at": o["cot_available_at"],
                       "fwd": o["fwd"], "started_by": why}
                events.append(cur)
            elif o["cls"] not in ACTIONABLE:
                cur = None
            if cur is not None and o["cls"] == cur["cls"]:
                cur["end"] = o["ts"]
                cur["n_obs"] += 1
            prev = o
    events.sort(key=lambda e: (e["start"], e["market"]))
    return events


# ---------------------------------------------------------------- statistics
def wilson(k, n, z=bc.CI_Z):
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0, c - h) * 100, 2), round(min(1, c + h) * 100, 2)]


def sample_state(n):
    return next(label for floor, label in bc.SAMPLE_STATES if n >= floor)


def _summ(items, h):
    done = [x["fwd"][h] for x in items if x.get("fwd", {}).get(h, {}).get("state") == "ok"]
    n = len(done)
    out = {"n": len(items), "n_complete": n,
           "n_pending": sum(1 for x in items if x.get("fwd", {}).get(h, {}).get("state") == "pending"),
           "n_missing": sum(1 for x in items if x.get("fwd", {}).get(h, {}).get("state") == "missing")}
    if not n:
        return out
    r = [d["ret"] for d in done]
    dr = [d["dir_ret"] for d in done]
    wins = sum(d["correct"] for d in done)
    mfe, mae = [d["mfe"] for d in done], [d["mae"] for d in done]
    rd = lambda x: round(x, 4)                                                   # noqa: E731
    out.update(win_rate=rd(100 * wins / n), wins=wins, win_ci95=wilson(wins, n),
               avg_ret=rd(stx.mean(r)), med_ret=rd(stx.median(r)), avg_dir=rd(stx.mean(dr)),
               med_dir=rd(stx.median(dr)), avg_mfe=rd(stx.mean(mfe)), med_mfe=rd(stx.median(mfe)),
               avg_mae=rd(stx.mean(mae)), med_mae=rd(stx.median(mae)), best=rd(max(dr)), worst=rd(min(dr)))
    return out


def _group_of(k):
    return "Currencies" if k in {r[3] for r in ct.FX_ROWS} else "Commodities & indexes"


def _cot_band(p):
    return ("<= 15" if p <= 15 else "15-30" if p <= 30 else ">= 85" if p >= 85 else "70-85" if p >= 70 else "30-70")


def _retail_band(x):
    against = x["short"] if x["dir"] > 0 else x["long"]
    return ">= 70% against" if against >= 70 else "60-70% against" if against >= 60 else "< 60% against"


VIEWS = {
    "all": lambda x: "All markets",
    "market": lambda x: x["market"],
    "group": lambda x: _group_of(x["market"]),
    "direction": lambda x: "Bullish" if x["dir"] > 0 else "Bearish",
    "strength": lambda x: "Strong" if x["cls"] in STRONG else "Ordinary",
    "class": lambda x: x["cls"],
    "market_class": lambda x: f'{x["market"]}|{x["cls"]}',
    "cot_band": lambda x: _cot_band(x["pct"]),
    "retail_band": _retail_band,
}


def stats(obs, events, horizons=bc.HORIZONS):
    """Every view x horizon, for events (primary) and observations. Sample size everywhere."""
    sig = [o for o in obs if o["cls"] in ACTIONABLE]
    out = {}
    for level, items in (("events", events), ("observations", sig)):
        lv = {}
        for view, fn in VIEWS.items():
            groups = {}
            for x in items:
                groups.setdefault(fn(x), []).append(x)
            lv[view] = {g: {str(h): _summ(xs, h) for h in horizons} for g, xs in sorted(groups.items())}
            for g in lv[view].values():
                for s in g.values():
                    s["sample"] = sample_state(s["n_complete"])
        out[level] = lv
    return out


def result_hash(res):
    """Hash of everything but metadata - identical inputs must give an identical hash."""
    core = {k: res[k] for k in ("observations", "events", "excluded", "snapshots", "stats")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()
