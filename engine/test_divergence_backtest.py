"""Tests for the Step 3 baseline backtest (divergence_backtest.py, divergence_backtest_run.py, fetch_bt_prices.py).

    python test_divergence_backtest.py

Stdlib only, offline, fixtures only: synthetic CFTC stores, synthetic Myfxbook history rows
and synthetic daily bars. No network is touched (urlopen is replaced by a spy that fails the
suite on any call) and nothing is read from or written to the live data/ directory.
"""
import contextlib, datetime as dt, io, json, random, sys, tempfile, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import divergence_backtest as bt                                             # noqa: E402
import divergence_backtest_config as bc                                      # noqa: E402
import cot_table as ct                                            # noqa: E402
import divergence as dv                                           # noqa: E402
import divergence_config as dcfg                                  # noqa: E402

PASS, FAIL, CALLS = [], [], []
UTC = dt.timezone.utc
CODE = {r[3]: r[1] for r in ct.ALL_ROWS}


def _spy(req, *a, **k):
    CALLS.append(getattr(req, "full_url", str(req)))
    raise OSError("network blocked in test_backtest")


urllib.request.urlopen = _spy


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def T(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- fixtures
def tuesdays(last, n):
    last = dt.date.fromisoformat(last)
    return [(last - dt.timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


def store_with(nets, last="2026-10-06", n=60):
    """nets: key -> list of NORMALIZED nets (oldest first, len n); stored as the future's NC
    long/short (orientation applied in reverse, so the engine must apply it)."""
    s = ct.empty_store()
    ds = tuesdays(last, n)
    for k, code in CODE.items():
        seq = nets.get(k) or [i * 100 for i in range(n)]
        weeks = {}
        for d, v in zip(ds, seq[-n:]):
            fut = v * dv.sign(k)
            weeks[d] = [2000000, 600000 + max(fut, 0), 600000 + max(-fut, 0), 0, 0, 0, 0, 0]
        s["contracts"][code] = {"name": k, "weeks": weeks}
    return s


def pct_series(rank, n=60, step=1000):
    """n values; the LAST 50 have the latest at `rank` (0..49) of those 50."""
    tail = [i * step for i in range(50) if i != rank] + [rank * step]
    return [0] * (n - 50) + tail


def row(ts, k, sym, lp, lpos=900, spos=600):
    return {"timestamp": ts, "symbol": sym, "longPercentage": str(lp), "shortPercentage": str(100 - lp),
            "longVolume": "1", "shortVolume": "1", "longPositions": str(lpos), "shortPositions": str(spos),
            "avgLongPrice": "1", "avgShortPrice": "1", "market": k}


def bars(start, closes, opens=None, skip=()):
    """Daily bars on weekdays from `start`, skipping dates in `skip`."""
    d = dt.date.fromisoformat(start)
    out = {"dates": [], "open": [], "high": [], "low": [], "close": []}
    i = 0
    while i < len(closes):
        if d.weekday() < 5 and d.isoformat() not in skip:
            c = closes[i]
            o = opens[i] if opens else c
            out["dates"].append(d.isoformat())
            out["open"].append(o)
            out["close"].append(c)
            out["high"].append(max(o, c) + 0.5)
            out["low"].append(min(o, c) - 0.5)
            i += 1
        d += dt.timedelta(days=1)
    return out


SYM = {"EUR": "EURUSD", "AUD": "AUDUSD", "JPY": "USDJPY", "XAG": "XAGUSD", "XAU": "XAUUSD", "GBP": "GBPUSD"}


# ---------------------------------------------------------------- tests
def t_frozen_strategy():
    g = "frozen"
    check(g, "the Step 2 configuration is the frozen baseline (fingerprint at 79b834a)",
          bt.fingerprint() == bc.BASELINE_FINGERPRINT, bt.fingerprint())
    check(g, "thresholds as deployed: Strong 85/15 + 70%, ordinary 70/30 + 60%",
          [(r[3], r[5]) for r in dcfg.RULES] == [(85, 70), (15, 70), (70, 60), (30, 60)])
    check(g, "event gap = the Step 2 retail age limit", bc.EVENT_MAX_GAP_MIN == dcfg.RETAIL_MAX_AGE_MIN)
    src = (Path(__file__).parent / "divergence_backtest.py").read_text()
    check(g, "the replay calls divergence.evaluate (no re-implemented rules)",
          "dv.evaluate(" in src and ">= 85" not in src.split("def _cot_band")[0])
    check(g, "one fixed price instrument per market, ZB none", all(bc.PRICES[k] for k in CODE if k != "ZB")
          and bc.PRICES["ZB"] is None and len({v["symbol"] for v in bc.PRICES.values() if v}) == 15)


def t_cot_timing():
    g = "cot-timing"
    rel = bt.cot_available_at("2026-10-06")
    check(g, "6 Oct 2026 report: available Friday 9 Oct 15:30 ET (19:30 UTC), not Tuesday",
          rel == T("2026-10-09T19:30:00Z"), rel)
    check(g, "winter time: 15:30 EST = 20:30 UTC (13 Jan 2026 -> Fri 16 Jan)",
          bt.cot_available_at("2026-01-13") == T("2026-01-16T20:30:00Z"))
    check(g, "Thanksgiving week (24 Nov 2026) -> Monday 30 Nov", bt.cot_available_at("2026-11-24") == T("2026-11-30T20:30:00Z"))
    check(g, "Christmas week (22 Dec 2026, holiday Fri 25) -> Monday 28 Dec", bt.cot_available_at("2026-12-22") == T("2026-12-28T20:30:00Z"))
    check(g, "Columbus Day week (13 Oct 2026) -> conservatively Monday 19 Oct", bt.cot_available_at("2026-10-13") == T("2026-10-19T19:30:00Z"))
    check(g, "a recorded first-seen time is used (never earlier than the scheduled release)",
          bt.cot_available_at("2026-10-06", "2026-10-09T20:05:00+00:00") == T("2026-10-09T20:05:00Z")
          and bt.cot_available_at("2026-10-06", "2026-10-09T10:00:00+00:00") == T("2026-10-09T19:30:00Z"))
    check(g, "a recorded late first-seen (holiday week) is what the dashboard knew",
          bt.cot_available_at("2026-10-13", "2026-10-16T19:45:00+00:00") == T("2026-10-16T19:45:00Z"))
    store = store_with({}, last="2026-10-13", n=60)
    for when, want in (("2026-10-09T19:29:59Z", "2026-09-29"), ("2026-10-09T19:30:00Z", "2026-10-06"),
                       ("2026-10-14T09:00:00Z", "2026-10-06"), ("2026-10-16T21:00:00Z", "2026-10-06"),
                       ("2026-10-19T19:30:00Z", "2026-10-13")):
        known = bt.cot_as_known(store, T(when))
        latest, _ = ct.pick_weeks(known)
        check(g, f"snapshot {when}: latest usable report {want}", latest == want, latest)
    known = bt.cot_as_known(store, T("2026-10-14T09:00:00Z"))
    check(g, "later reports are absent from the point-in-time store (not just ignored)",
          all("2026-10-13" not in c["weeks"] for c in known["contracts"].values()))
    check(g, "the full store is untouched", all("2026-10-13" in c["weeks"] for c in store["contracts"].values()))


def t_reproduces_step2():
    g = "reproduce"
    nets = {"EUR": pct_series(0), "AUD": pct_series(5), "XAG": pct_series(46), "JPY": pct_series(49), "GBP": pct_series(25)}
    store = store_with(nets, last="2026-10-06")
    ts = "2026-10-14T20:11:53Z"
    hist = [row(ts, "EUR", "EURUSD", 87), row(ts, "AUD", "AUDUSD", 64), row(ts, "XAG", "XAGUSD", 25),
            row(ts, "JPY", "USDJPY", 50), row(ts, "GBP", "GBPUSD", 80), row(ts, "XAU", "XAUUSD", 30, 20, 10)]
    res = bt.replay(hist, store, {})
    got = {o["market"]: o["cls"] for o in res["observations"]}
    # the dashboard's own evaluation with the same inputs
    snap = bt.snapshots(hist)[0][1]
    want = {k: dv.evaluate(k, store, snap, T(ts), True)["cls"] for k in dcfg.ORIENTATION}
    check(g, "every market's class equals divergence.evaluate at that moment", got == want, (got, want))
    check(g, "expected classes (strong bear, bear, strong bull, none, none, unavailable...)",
          (got["EUR"], got["AUD"], got["XAG"], got["JPY"], got["GBP"], got["XAU"], got["ZB"]) ==
          ("strong_bear", "bear", "strong_bull", "none", "none", "unavailable", "unavailable"), got)
    for k, lp, rank, want_cls in (("EUR", 70, 7, "strong_bear"), ("EUR", 69, 7, "bear"), ("EUR", 60, 14, "bear"),
                                  ("EUR", 59, 14, "none"), ("EUR", 30, 42, "strong_bull"), ("EUR", 40, 35, "bull"),
                                  ("EUR", 41, 35, "none"), ("EUR", 50, 49, "none")):
        r = bt.replay([row(ts, k, SYM[k], lp)], store_with({k: pct_series(rank)}), {})
        o = next(x for x in r["observations"] if x["market"] == k)
        check(g, f"boundary: COT rank {rank} ({(rank + .5) * 2:g} pct), retail {lp}% long -> {want_cls}",
              o["cls"] == want_cls, o["cls"])
    o = next(x for x in res["observations"] if x["market"] == "XAU")
    check(g, "below 100 positions -> unavailable with Step 2's reason", o["cls"] == "unavailable"
          and "sample too small" in o["reasons"][0])
    o = next(x for x in res["observations"] if x["market"] == "CAD")
    check(g, "market missing from the snapshot -> unavailable (no data invented)", o["cls"] == "unavailable"
          and "unavailable for this market" in o["reasons"][0])
    check(g, "every unavailable observation is listed as excluded with its reason",
          len(res["excluded"]) == sum(o["cls"] == "unavailable" for o in res["observations"])
          and all(x["why"] for x in res["excluded"]))
    bad = row(ts, "EUR", "EURUSD", 87)
    bad["longPercentage"] = "n/a"
    r = bt.replay([bad], store, {})
    check(g, "malformed history row -> unavailable (malformed), not repaired",
          next(x for x in r["observations"] if x["market"] == "EUR")["cls"] == "unavailable")


def t_returns():
    g = "returns"
    b = bt._bars(bars("2026-10-12", [100 + i for i in range(14)], opens=[99.5 + i for i in range(14)]))
    t = T("2026-10-14T20:00:00Z")                                  # Wednesday
    up = bt.forward(b, t, +1)
    check(g, "entry = OPEN of the first bar after the observation's date (Thu 15 Oct)",
          up[1]["entry_date"] == "2026-10-15" and up[1]["entry"] == 102.5, up[1])
    check(g, "1 day: exit = close of the entry bar", up[1]["exit_date"] == "2026-10-15" and up[1]["exit"] == 103)
    check(g, "3 / 5 / 10 trading days -> the 3rd / 5th / 10th bar (weekend skipped)",
          [up[h]["exit_date"] for h in (3, 5, 10)] == ["2026-10-19", "2026-10-21", "2026-10-28"], [up[h].get("exit_date") for h in (3, 5, 10)])
    r1 = (103 / 102.5 - 1) * 100
    check(g, "bullish: rising price -> positive directional return, correct", abs(up[1]["ret"] - r1) < 1e-6
          and abs(up[1]["dir_ret"] - r1) < 1e-6 and up[1]["correct"])
    dn = bt.forward(b, t, -1)
    check(g, "bearish: the same rise is a negative directional return, not correct",
          abs(dn[1]["dir_ret"] + r1) < 1e-6 and not dn[1]["correct"] and dn[1]["ret"] == up[1]["ret"])
    fall = bt._bars(bars("2026-10-12", [100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89],
                         opens=[100.5, 99.5, 98.5, 97.5, 96.5, 95.5, 94.5, 93.5, 92.5, 91.5, 90.5, 89.5]))
    dn = bt.forward(fall, t, -1)
    check(g, "bearish: falling price -> positive directional return", dn[5]["dir_ret"] > 0 and dn[5]["correct"])
    w = [x for x in bt._bars(bars("2026-10-12", list(range(100, 112)))) if "2026-10-15" <= x[0] <= "2026-10-21"]
    hi, lo, e = max(x[2] for x in w), min(x[3] for x in w), 103
    f = bt.forward(bt._bars(bars("2026-10-12", list(range(100, 112)))), t, +1)
    check(g, "MFE / MAE over the horizon's highs and lows (bullish)",
          abs(f[5]["mfe"] - (hi / e - 1) * 100) < 1e-6 and abs(f[5]["mae"] - (lo / e - 1) * 100) < 1e-6, f[5])
    f2 = bt.forward(bt._bars(bars("2026-10-12", list(range(100, 112)))), t, -1)
    check(g, "MFE / MAE mirrored for a bearish signal",
          abs(f2[5]["mfe"] + (lo / e - 1) * 100) < 1e-6 and abs(f2[5]["mae"] + (hi / e - 1) * 100) < 1e-6)


def t_calendar_and_missing():
    g = "calendar"
    b = bt._bars(bars("2026-11-23", list(range(100, 115)), skip={"2026-11-26"}))   # Thanksgiving closed
    f = bt.forward(b, T("2026-11-25T15:00:00Z"), +1)
    check(g, "holiday: entry skips the closed day (Wed obs -> Fri 27 Nov bar)", f[1]["entry_date"] == "2026-11-27")
    check(g, "trading days, not calendar days: 3 days -> Tue 1 Dec", f[3]["exit_date"] == "2026-12-01", f[3])
    f = bt.forward(bt._bars(bars("2026-10-12", list(range(100, 115)))), T("2026-10-16T21:00:00Z"), +1)
    check(g, "Friday evening observation -> Monday's bar", f[1]["entry_date"] == "2026-10-19")
    f = bt.forward(bt._bars(bars("2026-10-12", list(range(100, 115)))), T("2026-10-17T12:00:00Z"), +1)
    check(g, "weekend observation -> Monday's bar", f[1]["entry_date"] == "2026-10-19")
    g = "missing-price"
    check(g, "no series -> every horizon 'missing'", bt.replay([row("2026-10-14T20:00:00Z", "EUR", "EURUSD", 87)],
          store_with({"EUR": pct_series(0)}), {})["events"][0]["fwd"][1]["state"] == "missing")
    gap = bars("2026-10-12", list(range(100, 115)), skip={f"2026-10-{d}" for d in range(16, 24)})
    f = bt.forward(bt._bars(gap), T("2026-10-14T20:00:00Z"), +1)
    check(g, "a 9-day hole in the bars -> horizons across it 'missing', not bridged",
          f[1]["state"] == "ok" and f[3]["state"] == "missing" and "gap" in f[3]["why"], f)
    late = bars("2026-10-26", list(range(100, 115)))
    f = bt.forward(bt._bars(late), T("2026-10-14T20:00:00Z"), +1)
    check(g, "first bar 12 days after the observation -> 'missing' (no other period substituted)",
          all(x["state"] == "missing" for x in f.values()))
    nul = bars("2026-10-12", list(range(100, 115)))
    nul["close"][5] = None
    f = bt.forward(bt._bars(nul), T("2026-10-14T20:00:00Z"), +1)
    check(g, "a null close inside the window -> 'missing'", f[1]["state"] == "ok" and f[5]["state"] == "missing")
    short = bars("2026-10-12", [100, 101, 102, 103, 104])
    f = bt.forward(bt._bars(short), T("2026-10-14T20:00:00Z"), +1)
    check(g, "not enough later bars yet -> 'pending' (not missing, not partial)",
          f[1]["state"] == "ok" and f[3]["state"] == "pending" and f[10]["state"] == "pending", f)
    f = bt.forward(bt._bars(bars("2026-10-12", list(range(100, 130)))), T("2026-10-14T20:00:00Z"), +1, asof="2026-10-20")
    check(g, "asof caps the bars that may be used (incomplete days count as pending)",
          f[3]["state"] == "ok" and f[5]["state"] == "pending")


def _seq(classes, start="2026-10-14T09:00:00Z", step=30, k="EUR"):
    """Observations for one market from a class sequence (None = no divergence)."""
    t0 = T(start)
    out = []
    for i, c in enumerate(classes):
        ts = bt._iso(t0 + dt.timedelta(minutes=step * i))
        o = {"ts": ts, "market": k, "cls": c or "none", "label": c or "none", "pct": 5.0, "long": 70, "short": 30,
             "positions": 500, "symbol": "EURUSD", "cot_report": "2026-10-06", "cot_available_at": "x"}
        if c in bt.ACTIONABLE:
            o["dir"] = 1 if c in bt.BULL else -1
            o["fwd"] = {}
        out.append(o)
    return out


def t_events():
    g = "events"
    ev = bt.make_events(_seq(["bear"] * 5))
    check(g, "5 consecutive Bearish snapshots = ONE event with 5 observations", len(ev) == 1 and ev[0]["n_obs"] == 5)
    ev = bt.make_events(_seq(["bear", "bear", "strong_bear", "strong_bear", "bear"]))
    check(g, "strengthening (Bearish -> Strong Bearish) and weakening start new events",
          [(e["cls"], e["n_obs"]) for e in ev] == [("bear", 2), ("strong_bear", 2), ("bear", 1)], ev)
    check(g, "each event says what started it", [e["started_by"] for e in ev] ==
          ["first snapshot", "bear -> strong_bear", "strong_bear -> bear"], [e["started_by"] for e in ev])
    ev = bt.make_events(_seq(["bear", "bear", None, None, "bear"]))
    check(g, "signal disappears and reappears -> two events", len(ev) == 2 and ev[1]["started_by"] == "none -> bear")
    ev = bt.make_events(_seq(["bear", "unavailable", "bear"]))
    check(g, "an unavailable snapshot in between also ends the event", len(ev) == 2)
    ev = bt.make_events(_seq(["bear", "bull"]))
    check(g, "bearish -> bullish: two events, opposite directions", [e["dir"] for e in ev] == [-1, 1])
    s = _seq(["bear", "bear"], step=181)
    ev = bt.make_events(s)
    check(g, f"more than {bc.EVENT_MAX_GAP_MIN} min between snapshots -> a new event", len(ev) == 2
          and "gap" in ev[1]["started_by"])
    ev = bt.make_events(_seq(["bear", "bear"], step=180))
    check(g, "exactly the limit apart -> still one event", len(ev) == 1)
    both = _seq(["bear", "bear"]) + _seq(["bull", "bull"], k="AUD")
    ev = bt.make_events(both)
    check(g, "markets are deduplicated separately", len(ev) == 2 and {e["market"] for e in ev} == {"EUR", "AUD"})
    ev = bt.make_events(_seq([None, None]))
    check(g, "no actionable class -> no event", ev == [])


def t_no_leakage():
    """The synthetic test asked for: a future COT report would make a big winner if it leaked."""
    g = "no-leakage"
    # old report (6 Oct): EUR mid-range -> no signal. New report (13 Oct, available Mon 19 Oct
    # by the holiday rule, or Fri 16 Oct if production saw it then): EUR at its 50-week LOW.
    n = 61
    seq = [i * 1000 for i in range(n)]
    seq[-2] = 30000                                   # 6 Oct: middle of the range
    seq[-1] = -5000                                   # 13 Oct: a new low (bearish extreme)
    store = store_with({"EUR": seq}, last="2026-10-13", n=n)
    hist = [row("2026-10-15T12:00:00Z", "EUR", "EURUSD", 85)]   # Thursday, retail heavily long
    prices = {"EUR": bars("2026-10-12", [100, 100, 100, 100, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 40])}
    r = bt.replay(hist, store, prices)
    o = next(x for x in r["observations"] if x["market"] == "EUR")
    check(g, "Thursday snapshot uses the 6 Oct report (the 13 Oct report is not released yet)",
          o["cot_report"] == "2026-10-06", o["cot_report"])
    check(g, "... so NO signal - the leaked report would have produced a profitable Strong Bearish",
          o["cls"] == "none" and r["events"] == [], (o["cls"], o.get("pct")))
    later = [row("2026-10-19T21:00:00Z", "EUR", "EURUSD", 85)]
    leaked = dv.evaluate("EUR", store, bt.snapshots(later)[0][1], T("2026-10-19T21:00:00Z"), True)
    check(g, "(control: with the 13 Oct report the same snapshot WOULD be Strong Bearish)", leaked["cls"] == "strong_bear")
    r2 = bt.replay([row("2026-10-19T20:00:00Z", "EUR", "EURUSD", 85)], store, prices)
    check(g, "after the release the new report is used", r2["observations"][[x["market"] for x in r2["observations"]].index("EUR")]["cls"]
          == "strong_bear")
    # prices: the observation day's own close would flatter the result - it must not be used
    p = {"EUR": bars("2026-10-12", [100, 100, 80, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
                     opens=[100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100])}
    store2 = store_with({"EUR": pct_series(0)})
    r3 = bt.replay([row("2026-10-14T09:00:00Z", "EUR", "EURUSD", 85)], store2, p)
    f = r3["events"][0]["fwd"]
    check(g, "entry is the next day's open, not the observation day's close (which fell 20%)",
          f[1]["entry_date"] == "2026-10-15" and f[1]["entry"] == 100 and abs(f[1]["ret"]) < 1e-9, f[1])
    # later snapshots never change earlier observations
    base = [row("2026-10-14T09:00:00Z", "EUR", "EURUSD", 85), row("2026-10-14T09:30:00Z", "EUR", "EURUSD", 85)]
    more = base + [row("2026-10-14T10:00:00Z", "EUR", "EURUSD", 20), row("2026-10-15T09:00:00Z", "EUR", "EURUSD", 85)]
    a = [o for o in bt.replay(base, store2, p)["observations"]]
    b = [o for o in bt.replay(more, store2, p)["observations"] if o["ts"] <= "2026-10-14T09:30:00Z"]
    check(g, "adding later snapshots leaves every earlier observation identical", a == b)


def t_determinism():
    g = "determinism"
    store = store_with({"EUR": pct_series(0), "XAG": pct_series(46)})
    hist = [row(f"2026-10-14T{h:02d}:00:00Z", k, SYM[k], lp) for h in range(9, 20) for k, lp in (("EUR", 87), ("XAG", 25))]
    prices = {"EUR": bars("2026-10-12", list(range(100, 120))), "XAG": bars("2026-10-12", list(range(50, 70)))}
    r1 = bt.replay(hist, store, prices)
    shuffled = hist[:]
    random.Random(7).shuffle(shuffled)
    r2 = bt.replay(shuffled, store, prices)
    check(g, "same inputs -> identical result hash (input order irrelevant)", bt.result_hash(r1) == bt.result_hash(r2))
    check(g, "byte-identical JSON", json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True))
    check(g, "11 hourly snapshots of two signals -> 2 events, 22 signal observations",
          len(r1["events"]) == 2 and sum(o["cls"] in bt.ACTIONABLE for o in r1["observations"]) == 22)
    # whole CLI twice
    d = Path(tempfile.mkdtemp(prefix="fxbt-"))
    (d / "data" / "myfxbook_history").mkdir(parents=True)
    import myfxbook as mf
    (d / "data" / "myfxbook_history" / "2026-10-14.csv").write_text(mf._day_text([[r[f] for f in mf.HIST_FIELDS] for r in hist]))
    (d / "data" / ct.OUT_NAME).write_text(json.dumps(store))
    (d / "data" / "bt_prices.json").write_text(json.dumps({"markets": {k: {**v, "symbol": bc.PRICES[k]["symbol"]} for k, v in prices.items()}}))
    import divergence_backtest_run as br
    prod = sorted({r["timestamp"] for r in hist})
    (d / "ledger.json").write_text(json.dumps({"production": {"as_of": prod[-1], "attempts": prod}, "manual": []}))
    outs = []
    for i in range(2):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = br.main(["--data", str(d / "data"), "--out", str(d / f"o{i}"), "--generated-at", "2026-11-02T06:40:00Z",
                          "--no-archive", "--provenance", str(d / "ledger.json")])
        outs.append(((d / f"o{i}" / "results.json").read_bytes(), json.loads((d / f"o{i}" / "manifest.json").read_text()), buf.getvalue(), rc))
    check(g, "CLI rerun -> byte-identical results.json", outs[0][0] == outs[1][0] and outs[0][3] == 0)
    m = outs[0][1]
    check(g, "manifest: strategy version, baseline flag, commit, generation time, asof",
          m["strategy"]["version"] == "step2-v1" and m["strategy"]["is_baseline"] and m["generated_at"] == "2026-11-02T06:40:00Z"
          and m["prices_asof"] == "2026-11-01" and "code_commit" in m)
    check(g, "manifest: first / last snapshot, counts, COT reports, price coverage, exclusions",
          m["myfxbook"]["first"] == "2026-10-14T09:00:00Z" and m["myfxbook"]["last"] == "2026-10-14T19:00:00Z"
          and m["myfxbook"]["snapshots"] == 11 and m["cot"]["reports_used"] == ["2026-10-06"]
          and m["price_coverage"]["EUR"]["bars"] == 20 and m["price_coverage"]["CAD"]["bars"] == 0
          and m["excluded"]["observations"] == 11 * 14, m["excluded"])
    log = outs[0][2]
    check(g, "public console: counts and hashes only - no retail percentage or price",
          "87" not in log.replace("2026", "") and "Myfxbook requests made: 0" in log, log)


def t_provenance():
    g = "provenance"
    store = store_with({"EUR": pct_series(0)})
    hist = [row(t, "EUR", "EURUSD", 87) for t in ("2026-10-14T09:00:00Z", "2026-10-14T09:10:00Z", "2026-10-14T09:30:00Z")]
    r = bt.replay(hist, store, {}, production={"2026-10-14T09:00:00Z", "2026-10-14T09:30:00Z"})
    check(g, "only production snapshots are replayed", r["snapshots"] == ["2026-10-14T09:00:00Z", "2026-10-14T09:30:00Z"])
    check(g, "a verification (CI / manual) snapshot is excluded with that reason",
          {"ts": "2026-10-14T09:10:00Z", "market": "*", "why": ["not a production snapshot (verification fetch)"]} in r["excluded"])
    r = bt.replay(hist, store, {}, production=set())
    check(g, "no production record -> nothing replayed (never assumes)", r["snapshots"] == [] and r["events"] == [])
    d = Path(tempfile.mkdtemp(prefix="fxbt-"))
    (d / "data" / "myfxbook_history").mkdir(parents=True)
    import myfxbook as mf
    import divergence_backtest_run as br
    (d / "data" / "myfxbook_history" / "2026-10-14.csv").write_text(mf._day_text([[x[f] for f in mf.HIST_FIELDS] for x in hist]))
    (d / "data" / ct.OUT_NAME).write_text(json.dumps(store))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        br.main(["--data", str(d / "data"), "--out", str(d / "o"), "--generated-at", "2026-10-20T06:40:00Z", "--no-archive"])
        man0 = json.loads((d / "o" / "manifest.json").read_text())
        br.main(["--data", str(d / "data"), "--out", str(d / "a"), "--generated-at", "2026-10-20T06:40:00Z", "--no-archive",
                 "--all-snapshots"])
        man1 = json.loads((d / "a" / "manifest.json").read_text())
    check(g, "CLI without any production record replays nothing and says so",
          man0["myfxbook"]["provenance"]["production_snapshots"] == 0 and "refusing" in buf.getvalue())
    check(g, "--all-snapshots is never labelled the baseline", man1["strategy"]["is_baseline"] is False
          and man1["myfxbook"]["provenance"]["production_snapshots"] == 3)


def t_stats():
    g = "stats"
    check(g, "Wilson 95% interval", bt.wilson(7, 10) == [39.68, 89.22] and bt.wilson(0, 0) is None, bt.wilson(7, 10))
    check(g, "sample states (events)", [bt.sample_state(n) for n in (0, 9, 10, 29, 30, 99, 100)] ==
          ["Insufficient sample", "Insufficient sample", "Early evidence", "Early evidence", "Developing sample",
           "Developing sample", "Meaningful sample"])
    store = store_with({"EUR": pct_series(0), "XAG": pct_series(46)})
    hist = [row("2026-10-14T09:00:00Z", "EUR", "EURUSD", 87), row("2026-10-14T09:00:00Z", "XAG", "XAGUSD", 25),
            row("2026-10-14T09:30:00Z", "EUR", "EURUSD", 87)]
    prices = {"EUR": bars("2026-10-12", [100 - i for i in range(20)], opens=[100.5 - i for i in range(20)]),
              "XAG": bars("2026-10-12", [50 - i for i in range(20)], opens=[50.5 - i for i in range(20)])}
    r = bt.replay(hist, store, prices)
    s = r["stats"]["events"]["all"]["All markets"]["1"]
    check(g, "events view: 2 events, 1 correct (EUR bearish, price fell) and 1 wrong (silver bullish, fell)",
          s["n"] == 2 and s["n_complete"] == 2 and s["wins"] == 1 and s["win_rate"] == 50.0 and s["sample"] == "Insufficient sample", s)
    o = r["stats"]["observations"]["all"]["All markets"]["1"]
    check(g, "observation view counts every snapshot (3) - events view does not", o["n"] == 3)
    check(g, "every summary carries its sample size and state", all("n" in x and "sample" in x for lv in r["stats"].values()
          for v in lv.values() for gr in v.values() for x in gr.values()))
    v = r["stats"]["events"]
    check(g, "views: market, class, group, direction, strength, COT band, retail band",
          set(v) == {"all", "market", "group", "direction", "strength", "class", "market_class", "cot_band", "retail_band"}
          and set(v["direction"]) == {"Bullish", "Bearish"} and set(v["group"]) == {"Currencies", "Commodities & indexes"}
          and set(v["cot_band"]) == {"<= 15", ">= 85"} and set(v["retail_band"]) == {">= 70% against"}, {k: list(x) for k, x in v.items()})


def t_cot_first_seen():
    g = "first-seen"
    d = Path(tempfile.mkdtemp(prefix="fxcot-"))

    def fetch_for(dates):
        def f(since):
            return [{"report_date_as_yyyy_mm_dd": f"{dd}T00:00:00.000", "cftc_contract_market_code": c,
                     "market_and_exchange_names": c, "futonly_or_combined": "FutOnly",
                     "open_interest_all": "1000", "noncomm_positions_long_all": "300", "noncomm_positions_short_all": "200",
                     "noncomm_postions_spread_all": "100", "comm_positions_long_all": "400", "comm_positions_short_all": "500",
                     "nonrept_positions_long_all": "200", "nonrept_positions_short_all": "200"}
                    for dd in dates for c in ct.ALL_CODES]
        return f
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ct.build(force=True, data=d, fetch=fetch_for(["2026-09-29", "2026-10-06"]), now=T("2026-10-09T20:00:00Z"))
        ct.build(force=True, data=d, fetch=fetch_for(["2026-09-29", "2026-10-06"]), now=T("2026-10-10T08:00:00Z"))
        ct.build(force=True, data=d, fetch=fetch_for(["2026-10-06", "2026-10-13"]), now=T("2026-10-16T20:10:00Z"))
    st = ct.load(d / ct.OUT_NAME)["status"]
    check(g, "the COT pipeline keeps each report's first-seen time (re-fetch does not move it)",
          st.get("first_seen") == {"2026-10-06": "2026-10-09T20:00:00+00:00", "2026-10-13": "2026-10-16T20:10:00+00:00"}, st.get("first_seen"))
    import divergence_backtest_run as br
    check(g, "the backtest reads them as availability times", br.first_seen_of({"status": st})["2026-10-13"] == "2026-10-16T20:10:00+00:00")


def t_price_parse():
    g = "price-parse"
    import fetch_bt_prices as fp
    # Yahoo: bar timestamps at exchange open; gmtoffset -14400 (New York, EDT); a null row
    # (placeholder) and a partial null value
    ts = [int(dt.datetime(2026, 10, d, 13, 30, tzinfo=UTC).timestamp()) for d in (12, 13, 14, 15)]
    payload = {"chart": {"result": [{"meta": {"gmtoffset": -14400, "exchangeTimezoneName": "America/New_York"},
                                     "timestamp": ts, "indicators": {"quote": [{
                                         "open": [1.0, None, 3.0, 4.0], "high": [1.5, None, 3.5, 4.5],
                                         "low": [0.5, None, 2.5, 3.5], "close": [1.2, None, 3.2, None]}]}}]}}
    s2 = fp.parse(payload)
    check(g, "bars keyed by exchange-local trading date; empty placeholder rows dropped",
          s2["dates"] == ["2026-10-12", "2026-10-14", "2026-10-15"] and s2["timezone"] == "America/New_York", s2["dates"])
    check(g, "a missing value stays null (the backtest marks it missing - nothing filled)",
          s2["close"] == [1.2, 3.2, None] and s2["open"] == [1.0, 3.0, 4.0])
    try:
        fp.parse({"chart": {"result": None}})
        check(g, "no result block raises", False)
    except RuntimeError:
        check(g, "no result block raises (market recorded as failed, nothing substituted)", True)


def t_zero_requests():
    g = "requests"
    check(g, "no network call in the whole suite (urlopen spy)", CALLS == [], CALLS)
    import divergence_backtest_run as br
    br._no_myfxbook()
    try:
        urllib.request.urlopen("https://www.myfxbook.com/api/login.json")
        check(g, "backtest_run blocks Myfxbook", False)
    except OSError:
        check(g, "backtest_run blocks any Myfxbook request (and counts it as a failure)", br.BLOCKED != [])
    br.BLOCKED.clear()
    urllib.request.urlopen = _spy
    src = "".join((Path(__file__).parent / f).read_text() for f in ("divergence_backtest.py", "divergence_backtest_run.py", "fetch_bt_prices.py"))
    check(g, "the backtest code never logs in or fetches the outlook", "mf.fetch(" not in src and "mf.build(" not in src
          and "login.json" not in src)
    root = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    wf = (root / "divergence-backtest.yml").read_text()
    check(g, "backtest workflow: no Myfxbook login secret, archive read token only",
          "MYFXBOOK_EMAIL" not in wf and "MYFXBOOK_PASSWORD" not in wf and "MYFXBOOK_HISTORY_TOKEN" in wf)
    check(g, "backtest workflow: production cache restored read-only (never saved)",
          "actions/cache/restore" in wf and "actions/cache/save" not in wf)


if __name__ == "__main__":
    for fn in (t_frozen_strategy, t_cot_timing, t_reproduces_step2, t_returns, t_calendar_and_missing, t_events,
               t_no_leakage, t_determinism, t_provenance, t_stats, t_cot_first_seen, t_price_parse, t_zero_requests):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised", f"{type(e).__name__}: {e}"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    groups = {}
    for g, n, _ in PASS:
        groups.setdefault(g, [0, 0])[0] += 1
    for g, n, _ in FAIL:
        groups.setdefault(g, [0, 0])[1] += 1
    print()
    for g, (ok, bad) in groups.items():
        print(f"{g}: {ok}/{ok + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
