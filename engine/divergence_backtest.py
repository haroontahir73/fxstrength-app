"""Step 3 - baseline historical replay of the COT x Myfxbook retail divergence.

What the dashboard WOULD HAVE SHOWN at each genuine Myfxbook snapshot, and what the market
did afterwards. Pure functions: every input is passed in (no network, no clock), so the same
inputs always give the same output (divergence_backtest_run.py does the I/O).

    replay(history_rows, cot_store, prices, ...) -> {"observations", "events", "stats", ...}

NO LOOK-AHEAD
  * Myfxbook: only real archived snapshots (timestamped rows of our production feed). Nothing
    is reconstructed, interpolated or carried between snapshots.
  * COT: a report is usable only from its AVAILABILITY time - never from its Tuesday
    positions date. cot_availability(): the release on the CFTC's OFFICIAL published
    calendar (15:30 US Eastern; a documented fallback algorithm only for years the calendar
    is not held); if production recorded when it first saw that report, the later of the two. A snapshot sees only reports available at or
    before its own timestamp, and the 50-week percentile window ends at that report.
  * Classification: divergence.evaluate() itself - the frozen Step 2 code and configuration -
    with "now" = the snapshot time. Nothing is re-implemented.
  * Prices: entry is the OPEN of the first daily bar dated after the snapshot's date in the
    exchange's time zone (so the bar opens after the snapshot); a
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


def official_release(report_date):
    """(release datetime UTC) from the CFTC's published calendar, or None if that year is not
    in divergence_backtest_config.CFTC_RELEASES."""
    rd = dt.date.fromisoformat(report_date)
    earliest = rd + dt.timedelta(days=3)
    if earliest.year not in bc.CFTC_RELEASES:
        return None                                # that year's calendar is not held
    cands = []
    for y in (earliest.year, earliest.year + 1):
        days = bc.CFTC_RELEASES.get(y)
        if days:
            cands += [dt.date(y, int(x[:2]), int(x[3:])) for x in days.split()]
    nxt = min((d for d in cands if d >= earliest), default=None)
    if nxt is None or (nxt - rd).days > 14:
        return None
    t = dt.datetime.combine(nxt, dt.time(15, 30), UTC)
    return t - dt.timedelta(hours=ct.eastern_offset_h(t))


def fallback_release(report_date):
    """Documented fallback where no official calendar is held: the Friday 15:30 ET release,
    one business day later for every US federal holiday from the Monday of the report week to
    that Friday (conservative for holidays; it cannot know about a government shutdown, so
    years with a shutdown should get their official calendar added before snapshots from that
    period are replayed - our genuine Myfxbook history starts 14 Oct 2026)."""
    rd = dt.date.fromisoformat(report_date)
    sched = ct.release_at(rd)
    fri = sched.date()
    monday = rd - dt.timedelta(days=rd.weekday())
    hol = us_federal_holidays(rd.year) | us_federal_holidays(fri.year)
    n = sum(1 for k in range((fri - monday).days + 1) if (monday + dt.timedelta(days=k)) in hol)
    if n:
        day = _business_days_after(fri, n, hol)
        t = dt.datetime.combine(day, dt.time(15, 30), UTC)
        sched = t - dt.timedelta(hours=ct.eastern_offset_h(t))
    return sched


def cot_availability(report_date, first_seen=None):
    """(time, basis): when a COT report (positions date, ISO) could first have been on the
    dashboard. The official release (or the fallback); if production recorded when it first
    saw the report, the LATER of the two - the dashboard never had it before the release, and
    had it only once production fetched it."""
    off = official_release(report_date)
    rel, basis = (off, "official CFTC calendar") if off else (fallback_release(report_date), "fallback algorithm")
    if first_seen and _ts(first_seen) > rel:
        return _ts(first_seen), basis + " + production first-seen"
    return rel, basis


def cot_available_at(report_date, first_seen=None):
    return cot_availability(report_date, first_seen)[0]


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


def local_date(t, tz):
    """The observation's calendar date in the price series' exchange time zone (bars are
    dated that way). Unknown zone -> the latest date anywhere (UTC+14), i.e. conservative."""
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return t.astimezone(ZoneInfo(tz)).date()
        except Exception:                                        # noqa: BLE001
            pass
    return (t + dt.timedelta(hours=14)).date() if tz else t.date()


def forward(bars, t, direction, horizons=bc.HORIZONS, asof=None, tz=None):
    """Forward performance of a signal observed at t. Returns {h: {...}} with state 'ok',
    'pending' (bars not there yet) or 'missing' (a gap / absent value). Entry is the first
    bar dated after the observation's date IN THE EXCHANGE'S TIME ZONE, so the entry bar
    always opens after the observation."""
    out = {}
    d0 = local_date(t, tz)
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
    """The whole baseline: observations, state events, directional episodes, accounting and
    statistics. `prices` = {market: series}
    as written by fetch_bt_prices.py; `asof` (ISO date) caps which price bars may be used.
    `production` (a set of snapshot timestamps, or None for all): only snapshots the
    PRODUCTION feed made count - genuine verification fetches (manual / CI) are excluded with
    that reason, since the dashboard never showed them."""
    first_seen = first_seen or {}
    snaps = snapshots(history_rows)
    bars = {k: _bars(prices.get(k)) for k in dcfg.ORIENTATION}
    obs, unavailable, excluded_snaps = [], [], []
    archived = len(snaps)
    if production is not None:
        # exact timestamp match only - a near or ambiguous time is NOT production
        excluded_snaps = [{"ts": ts, "why": "not a production snapshot (verification fetch / unknown provenance)"}
                          for ts, _ in snaps if ts not in production]
        snaps = [(ts, s) for ts, s in snaps if ts in production]
    for ts, snap in snaps:
        t = _ts(ts)
        store = cot_as_known(cot_store, t, first_seen)
        latest, _ = ct.pick_weeks(store) if store.get("contracts") else (None, None)
        avail = cot_availability(latest, first_seen.get(latest)) if latest else (None, None)
        for k in dcfg.ORIENTATION:
            r = dv.evaluate(k, store, snap, t, True)
            o = {"ts": ts, "market": k, "cls": r["cls"], "label": r["label"], "cot_report": latest,
                 "cot_available_at": _iso(avail[0]) if latest else None, "cot_availability_basis": avail[1]}
            if r["cls"] == "unavailable":
                o["reasons"] = r["reasons"]
                unavailable.append({"ts": ts, "market": k, "why": r["reasons"]})
            else:
                c, rt = r["cot"], r["retail"]
                o.update(pct=round(c["pct"], 4), net=c["net"], momentum=c["momentum"],
                         long=rt["long"], short=rt["short"], positions=rt["positions"], symbol=rt["symbol"])
            if r["cls"] in ACTIONABLE:
                o["dir"] = 1 if r["cls"] in BULL else -1
                o["fwd"] = forward(bars[k], t, o["dir"], horizons, asof, (prices.get(k) or {}).get("timezone")) if bars[k] else \
                    {h: {"state": "missing", "why": "no price series for this market"} for h in horizons}
            obs.append(o)
    events = make_events(obs)
    episodes = make_episodes(obs, events)
    reasons = {}
    for u in unavailable:
        key = u["why"][0].split(" - ")[0]
        reasons[key] = reasons.get(key, 0) + 1
    accounting = {
        "snapshots": {"archived": archived, "production": len(snaps), "excluded_before_expansion": len(excluded_snaps)},
        "market_observations": {"total": len(obs), "markets_per_snapshot": len(dcfg.ORIENTATION),
                                "classified": sum(o["cls"] != "unavailable" for o in obs),
                                "unavailable": len(unavailable), "unavailable_by_reason": dict(sorted(reasons.items()))},
        "signals": {"observations": sum(o["cls"] in ACTIONABLE for o in obs), "state_events": len(events),
                    "directional_episodes": len(episodes)},
    }
    return {"conventions": CONVENTIONS, "accounting": accounting, "observations": obs, "state_events": events,
            "episodes": episodes, "excluded_snapshots": excluded_snaps, "unavailable_observations": unavailable,
            "snapshots": [ts for ts, _ in snaps], "stats": stats(obs, events, episodes, horizons)}


CONVENTIONS = {
    "entry": "OPEN of the first daily session whose trading date is strictly after the signal's date in "
             "that exchange's time zone",
    "horizon": "h-day exit = CLOSE of the h-th session, counting the entry session as session 1 "
               "(1d = entry session's close, 3d = 3rd session's close, 5d, 10d likewise)",
    "mfe_mae": "highest high / lowest low from the entry session through the h-th session, from the entry "
               "price, signed in the signal's direction (MFE best move in favour, MAE worst move against)",
    "horizons_trading_sessions": list(bc.HORIZONS),
    "pending_missing": "pending = sessions not completed yet (only bars dated <= asof count); missing = a gap of "
                       f"more than {bc.MAX_BAR_GAP_DAYS} days, no bar within {bc.MAX_ENTRY_GAP_DAYS} days, or a null "
                       "value - nothing is substituted",
    "cot_availability": "official CFTC release calendar 15:30 ET (fallback algorithm for years not held); the later "
                        "production first-seen time where recorded",
    "state_event": "consecutive snapshots of one market with the SAME class; a class change (incl. Bearish <-> "
                   f"Strong Bearish), a no-signal / unavailable snapshot or a gap over {bc.EVENT_MAX_GAP_MIN} min "
                   "starts a new one",
    "directional_episode": "consecutive snapshots of one market with the same DIRECTION (Bearish and Strong Bearish "
                           "are one bearish episode); it ends when the signal disappears / becomes unavailable, flips "
                           f"direction, or after a gap over {bc.EVENT_MAX_GAP_MIN} min. Primary sample unit.",
    "provenance": "production snapshots only, exact timestamp match against production records",
    "prices_warning": "metals, copper and oil are continuous front-month futures (Yahoo GC=F, SI=F, HG=F, CL=F): "
                      "returns across a contract roll include the roll jump",
}


def make_events(obs):
    """State events: runs of the same actionable class per market (CONVENTIONS["state_event"])."""
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
                o["state_event"] = cur["id"]
            prev = o
    events.sort(key=lambda e: (e["start"], e["market"]))
    return events


_RANK = {"bull": 1, "strong_bull": 2, "bear": 1, "strong_bear": 2}


def make_episodes(obs, events):
    """Directional episodes (CONVENTIONS["directional_episode"]): the independent unit. Sets
    `directional_episode_id` on every signal observation and state event."""
    by = {}
    for o in obs:
        by.setdefault(o["market"], []).append(o)
    ev_by_id = {e["id"]: e for e in events}
    eps = []
    for k, seq in by.items():
        seq = sorted(seq, key=lambda o: o["ts"])
        cur, prev = None, None
        for o in seq:
            t = _ts(o["ts"])
            gap = prev is not None and (t - _ts(prev["ts"])).total_seconds() / 60 > bc.EVENT_MAX_GAP_MIN
            if o["cls"] not in ACTIONABLE:
                cur = None
            else:
                if cur is None or gap or prev.get("dir") != o["dir"]:
                    cur = {"id": f"{k}:{'bull' if o['dir'] > 0 else 'bear'}@{o['ts']}", "market": k, "dir": o["dir"],
                           "direction": "Bullish" if o["dir"] > 0 else "Bearish", "start": o["ts"], "end": o["ts"],
                           "n_obs": 0, "cls": o["cls"], "start_cls": o["cls"], "max_cls": o["cls"],
                           "state_events": [], "pct": o["pct"], "long": o["long"], "short": o["short"],
                           "positions": o["positions"], "symbol": o["symbol"], "cot_report": o["cot_report"],
                           "cot_available_at": o["cot_available_at"], "fwd": o["fwd"]}
                    eps.append(cur)
                cur["end"] = o["ts"]
                cur["n_obs"] += 1
                if _RANK[o["cls"]] > _RANK[cur["max_cls"]]:
                    cur["max_cls"] = o["cls"]
                sid = o.get("state_event")
                if sid and sid not in cur["state_events"]:
                    cur["state_events"].append(sid)
                    ev_by_id[sid]["directional_episode_id"] = cur["id"]
                o["directional_episode_id"] = cur["id"]
            prev = o
    eps.sort(key=lambda e: (e["start"], e["market"]))
    return eps


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


def stats(obs, events, episodes, horizons=bc.HORIZONS):
    """Every view x horizon at three levels: directional episodes (PRIMARY - independent),
    state events (strength transitions) and observations. Sample size everywhere. For an
    episode, class / strength are those it STARTED with."""
    sig = [o for o in obs if o["cls"] in ACTIONABLE]
    out = {"primary_level": "episodes"}
    for level, items in (("episodes", episodes), ("state_events", events), ("observations", sig)):
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
    core = {k: res[k] for k in ("conventions", "accounting", "observations", "state_events", "episodes",
                                 "excluded_snapshots", "unavailable_observations", "snapshots", "stats")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()
