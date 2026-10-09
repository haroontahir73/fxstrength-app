"""Regression tests for the reliability fixes - freshness, outages, delivery, corrections.

Every case here is a failure that was REPRODUCED against the code before the fix, in the
same spirit as test_commodity_decode.py: the test exists because the behaviour was once
wrong, and it fails if the old behaviour comes back.

    python test_reliability.py

Stdlib only. Nothing here touches the real data/ directory - each group of tests runs
against a temporary one, so running the suite can never alter live pipeline state.
"""
import json, sys, time, tempfile, shutil
import datetime as dt
from pathlib import Path

PASS, FAIL = [], []


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def _tmp():
    d = Path(tempfile.mkdtemp(prefix="fxrel-"))
    (d / "data").mkdir()
    return d / "data"


# ======================================================================================
# 1. FRESHNESS - a failed calendar fetch must not re-assert a decayed news score
# ======================================================================================
def test_calendar_freshness():
    import fetch_calendar as fc
    g = "calendar freshness"
    data = _tmp()
    real_data, real_fetch = fc.DATA, fc.fetch
    try:
        fc.DATA = data
        fc.fetch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("API down"))
        now = dt.datetime.now(dt.timezone.utc)

        # --- a cache with real events must be RE-SCORED against the current clock.
        # Reusing the stored score unchanged told the blend that evidence from hours ago
        # was as live as evidence from this minute, on the leg carrying 40% of the score.
        when = (now - dt.timedelta(hours=fc.NEWS_HALFLIFE_HOURS)).isoformat()
        cached = {"fetched_at": (now - dt.timedelta(hours=1)).isoformat(),
                  "data_as_of": (now - dt.timedelta(hours=1)).isoformat(),
                  "news_score": {"USD": 80.0}, "contributors": {},
                  "released": [{"title": "Nonfarm Payrolls", "ccy": "USD",
                                "impact": "High", "when": when, "surprise": 0.9}],
                  "upcoming": [], "next_release": None, "next_high_impact": None}
        (data / "calendar.json").write_text(json.dumps(cached))
        out = fc.main()
        # one half-life old, so the decayed weight must be well below the stored 80
        check(g, "cached news score is re-decayed, not re-asserted",
              out["news_score"]["USD"] != 80.0,
              f"got {out['news_score']['USD']} - unchanged from the cached value")
        check(g, "reuse is flagged degraded", out.get("degraded") is True)
        check(g, "reuse records why", bool(out.get("degraded_why")))
        check(g, "data age is carried, not restamped as now",
              out.get("data_as_of") == cached["data_as_of"],
              f"data_as_of={out.get('data_as_of')}")

        # --- an old-format cache with no event list still must not hold its score flat
        old_stamp = (now - dt.timedelta(hours=fc.NEWS_HALFLIFE_HOURS)).isoformat()
        (data / "calendar.json").write_text(json.dumps(
            {"fetched_at": old_stamp, "news_score": {"USD": 60.0, "EUR": -30.0}}))
        out = fc.main()
        check(g, "no-events cache gets a blanket decay",
              abs(out["news_score"]["USD"] - 30.0) < 3.0,
              f"one half-life should roughly halve 60 -> got {out['news_score']['USD']}")

        # --- past the cache ceiling the leg is withdrawn rather than carried
        (data / "calendar.json").write_text(json.dumps(
            {"fetched_at": (now - dt.timedelta(hours=fc.CACHE_MAX_AGE_H + 10)).isoformat(),
             "news_score": {"USD": 70.0}, "released": []}))
        out = fc.main()
        check(g, "a cache past the ceiling withdraws the news leg",
              out["news_score"]["USD"] == 0.0 and out.get("news_leg_withdrawn") is True,
              f"got {out['news_score'].get('USD')}")

        # --- cold start with no cache at all is still a valid, self-describing payload
        (data / "calendar.json").unlink()
        out = fc.main()
        check(g, "cold start emits a valid degraded calendar",
              out["degraded"] is True and out["news_score"] and out["event_count"] == 0)
    finally:
        fc.DATA, fc.fetch = real_data, real_fetch
        shutil.rmtree(data.parent, ignore_errors=True)


def test_freshness_report():
    import freshness
    g = "freshness report"
    data = _tmp()
    real = freshness.DATA
    try:
        freshness.DATA = data
        now = dt.datetime.now(dt.timezone.utc)
        # a calendar well past its budget, everything else absent
        (data / "calendar.json").write_text(json.dumps(
            {"data_as_of": (now - dt.timedelta(hours=48)).isoformat()}))
        rows = freshness.collect(now)
        cal = next(r for r in rows if r["file"] == "calendar.json")
        check(g, "an over-budget input is marked stale", cal["stale"] is True,
              f"age {cal['age_h']}h vs budget {cal['budget_h']}h")
        check(g, "a missing input is marked stale and missing",
              all(r["missing"] and r["stale"] for r in rows if r["file"] != "calendar.json"))
        s = freshness.summary(rows)
        check(g, "the verdict is stale when any input is", s["state"] == "stale")
        check(g, "stale inputs are named", "Calendar / news" in s["stale"])

        # a module reporting itself degraded is a freshness fact a timestamp cannot carry
        (data / "calendar.json").write_text(json.dumps(
            {"data_as_of": now.isoformat(), "degraded": True,
             "degraded_why": "reusing cached events"}))
        rows = freshness.collect(now)
        cal = next(r for r in rows if r["file"] == "calendar.json")
        check(g, "a self-reported degraded input is flagged even when fresh",
              cal["degraded"] is True and cal["stale"] is False)

        # a carried value INSIDE an otherwise current file counts as degraded too
        (data / "rates.json").write_text(json.dumps(
            {"fetched_at": now.isoformat(),
             "currencies": {"EUR": {"rate": 2.15, "stale": True}, "USD": {"rate": 4.25}}}))
        rows = freshness.collect(now)
        r = next(r for r in rows if r["file"] == "rates.json")
        check(g, "carried-over entries mark the file degraded",
              r["degraded"] is True and r["carried"] == ["EUR"])
    finally:
        freshness.DATA = real
        shutil.rmtree(data.parent, ignore_errors=True)


# ======================================================================================
# 2. OUTAGES - good data must survive a failed fetch
# ======================================================================================
def test_rates_carry():
    import fetch_rates as fr
    g = "policy rates"
    data = _tmp()
    real_data, real_fetch = fr.DATA, fr.fetch
    try:
        fr.DATA = data
        fr.fetch = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("API down"))
        today = dt.date.today()
        good = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "usd_rate": 4.25,
                "currencies": {c: {"rate": r, "diff_vs_usd": round(r - 4.25, 2),
                                   "last_move": -0.25,
                                   "as_of": (today - dt.timedelta(days=20)).isoformat(),
                                   "title": "Interest Rate Decision"}
                               for c, r in (("USD", 4.25), ("EUR", 2.15), ("GBP", 4.0),
                                            ("JPY", 1.0), ("AUD", 3.6), ("NZD", 2.5),
                                            ("CAD", 2.5), ("CHF", 0.0))}}
        (data / "rates.json").write_text(json.dumps(good))
        out = fr.main()
        # A policy rate is a fact with a date on it, not a decaying reading. Overwriting
        # it with None dropped both interest-rate indicators to unset and moved the board.
        check(g, "a total outage keeps the last confirmed rates",
              out["usd_rate"] == 4.25 and out["currencies"]["EUR"]["rate"] == 2.15,
              f"usd={out['usd_rate']} eur={out['currencies']['EUR']['rate']}")
        check(g, "carried rates are marked stale",
              out["currencies"]["EUR"].get("stale") is True)
        check(g, "the carried USD rate is flagged", out.get("usd_rate_carried") is True)
        check(g, "the file says it is degraded", out.get("degraded") is True)
        check(g, "differentials are still computed off the carried USD rate",
              out["currencies"]["EUR"].get("diff_vs_usd") == -2.1,
              f"got {out['currencies']['EUR'].get('diff_vs_usd')}")
        check(g, "the carried state is what lands on disk",
              json.loads((data / "rates.json").read_text())["usd_rate"] == 4.25)

        # ...but a rate too old to still plausibly be current is withdrawn, not carried
        stale = json.loads(json.dumps(good))
        for c in stale["currencies"]:
            stale["currencies"][c]["as_of"] = (
                today - dt.timedelta(days=fr.CARRY_MAX_DAYS + 30)).isoformat()
        (data / "rates.json").write_text(json.dumps(stale))
        out = fr.main()
        check(g, "a rate past the carry ceiling is withdrawn",
              out["currencies"]["EUR"]["rate"] is None,
              f"got {out['currencies']['EUR']['rate']}")

        # no cache at all behaves as before
        (data / "rates.json").unlink()
        out = fr.main()
        check(g, "no cache still produces a valid file",
              out["currencies"]["EUR"]["rate"] is None and "note" in out["currencies"]["EUR"])
    finally:
        fr.DATA, fr.fetch = real_data, real_fetch
        shutil.rmtree(data.parent, ignore_errors=True)


def _cot_week(currencies, oi=500000, net=10000):
    return {"currencies": {c: {"leveraged": {"net": net, "net_chg": 500},
                               "open_interest": oi, "report_date": "2026-10-07"}
                           for c in currencies}}


def test_score_survives_missing_inputs():
    import score
    from config import ORDER
    g = "scoring with gaps"

    # USD's own contract is the minority 25% leg; the other seven contracts ARE positions
    # on the dollar. Reading cot['currencies']['USD'] unconditionally raised KeyError and
    # took the whole board down for one missing contract.
    no_usd = _cot_week([c for c in ORDER if c != "USD"])
    try:
        out = score.cot_score(no_usd)
        crashed = False
    except Exception as e:                                     # noqa: BLE001
        crashed, out = True, None
        check(g, "missing USD COT does not crash scoring", False, f"{type(e).__name__}: {e}")
    if not crashed:
        check(g, "missing USD COT does not crash scoring", True)
        check(g, "USD falls back to the basket alone",
              out["USD"]["score"] == out["USD"]["basket"]
              and out["USD"]["own_contract"] is None,
              f"score={out['USD']['score']} basket={out['USD'].get('basket')}")
        check(g, "the fallback is flagged and explained",
              out["USD"].get("degraded") is True and "basket" in out["USD"]["note"])

    # the healthy path must be untouched by that change
    full = _cot_week(ORDER)
    out = score.cot_score(full)
    check(g, "a complete report still blends 75/25",
          out["USD"]["own_contract"] is not None and not out["USD"].get("degraded"))

    # a currency missing from a cached checklist/OI file (CHF did this on 2026-09-08)
    # must cost that leg, not the build
    data = _tmp()
    real_score_data = score.DATA
    import freshness
    real_fresh_data = freshness.DATA
    try:
        score.DATA = freshness.DATA = data
        (data / "cot.json").write_text(json.dumps(full))
        (data / "calendar.json").write_text(json.dumps(
            {"news_score": {c: 0.0 for c in ORDER}, "contributors": {}, "upcoming": []}))
        (data / "fundamentals.json").write_text(json.dumps(
            {"currencies": {c: {"score": 0.0, "avg_1_5": 3.0, "categories": {},
                                "coverage": 50, "unset": [], "indicators": {}}
                            for c in ORDER if c != "CHF"}}))
        (data / "oi.json").write_text(json.dumps(
            {"currencies": {c: {"score": 0.0, "note": ""} for c in ORDER if c != "CHF"}}))
        out = score.build()
        check(g, "a currency missing from the checklist does not crash the build",
              "CHF" in out["currencies"])
        check(g, "the missing legs are recorded",
              any("CHF" in m for m in out.get("missing_legs", [])),
              f"missing_legs={out.get('missing_legs')}")
        check(g, "a missing checklist row reads as neutral, not 0.00/5",
              out["currencies"]["CHF"]["fundamentals"]["avg_1_5"] == 3.0)
        check(g, "freshness rows are stored with the scores",
              bool(out.get("inputs")) and "state" in (out.get("freshness") or {}))
    finally:
        score.DATA, freshness.DATA = real_score_data, real_fresh_data
        shutil.rmtree(data.parent, ignore_errors=True)


# ======================================================================================
# 3. DELIVERY - a failed push must be retried, never silently dropped
# ======================================================================================
def test_alert_queue():
    import alert_queue as aq
    g = "retry queue"
    data = _tmp()
    real_data, real_file = aq.DATA, aq.QUEUE_FILE
    try:
        aq.DATA, aq.QUEUE_FILE = data, data / "alert_queue.json"
        aq.enqueue("k1", "fx", "FX: tariff", "body", meta={"error": "HTTPError 429"})
        check(g, "a failed alert is recorded", len(aq.pending()) == 1)
        check(g, "the queue survives the process",
              (data / "alert_queue.json").exists())

        # same alert failing again must not become a second entry
        aq.enqueue("k1", "fx", "FX: tariff", "body", meta={"error": "HTTPError 429"})
        check(g, "re-queueing the same alert is idempotent", len(aq.pending()) == 1)
        check(g, "the attempt count still rises", aq.pending()[0]["attempts"] == 2)

        # backoff: nothing is due immediately after a failure
        sent = []
        d, p, dr = aq.drain(lambda e: sent.append(e) or True)
        check(g, "backoff defers the next attempt", d == 0 and p == 1 and not sent)

        # once due, it is delivered and leaves the queue
        q = aq._load()
        q["pending"][0]["due_at"] = time.time() - 1
        aq._save(q)
        d, p, dr = aq.drain(lambda e: sent.append(e) or True)
        check(g, "a due alert is redelivered", d == 1 and p == 0 and len(sent) == 1)
        check(g, "the redelivered alert carries its body", sent[0]["body"] == "body")

        # a send that fails again stays queued with a wider gap
        aq.enqueue("k2", "commodity", "GOLD", "b2")
        q = aq._load()
        q["pending"][0]["due_at"] = time.time() - 1
        aq._save(q)
        before = aq.pending()[0]["attempts"]
        d, p, dr = aq.drain(lambda e: False)
        check(g, "a repeated failure stays queued", p == 1 and d == 0)
        check(g, "and backs off further", aq.pending()[0]["attempts"] == before + 1)
        check(g, "and the next attempt is in the future",
              aq.pending()[0]["due_at"] > time.time())

        # an alert whose moment has passed is dropped, not delivered hours late
        q = aq._load()
        q["pending"][0]["first_seen"] = time.time() - (aq.MAX_AGE_H + 1) * 3600
        q["pending"][0]["due_at"] = 0
        aq._save(q)
        d, p, dr = aq.drain(lambda e: True)
        check(g, "a stale alert is dropped rather than sent late", dr == 1 and p == 0)
        check(g, "the drop stays visible for the health report",
              len(aq.dead(24)) == 1 and "no longer news" in aq.dead(24)[0]["dropped_why"])

        # giving up is bounded
        aq.enqueue("k3", "fx", "T", "b")
        q = aq._load()
        q["pending"][0]["attempts"] = aq.MAX_ATTEMPTS
        q["pending"][0]["due_at"] = 0
        aq._save(q)
        d, p, dr = aq.drain(lambda e: False)
        check(g, "retries are bounded", dr == 1 and p == 0)

        # a corrupt queue file must not stop a watcher pass
        (data / "alert_queue.json").write_text("{not json")
        check(g, "a corrupt queue reads as empty", aq.pending() == [])
    finally:
        aq.DATA, aq.QUEUE_FILE = real_data, real_file
        shutil.rmtree(data.parent, ignore_errors=True)


def test_deliver_queues_on_failure():
    import news_watch as nw
    import alert_queue as aq
    g = "deliver()"
    data = _tmp()
    real_data, real_file = aq.DATA, aq.QUEUE_FILE
    real_push = nw.push
    try:
        aq.DATA, aq.QUEUE_FILE = data, data / "alert_queue.json"
        # The FX watcher used to discard push()'s return value entirely, so a lost alert
        # left no trace anywhere and the story was already marked seen.
        nw.push = lambda *a, **k: False
        ok, queued = nw.deliver("topic", "FX: test", "body", "link", key="h1")
        check(g, "a failed delivery is reported as failed", ok is False)
        check(g, "a failed delivery is queued", queued is True and len(aq.pending()) == 1)
        check(g, "the queue entry knows which watcher sent it",
              aq.pending()[0]["who"] == "fx")

        nw.push = lambda *a, **k: True
        ok, queued = nw.deliver("topic", "FX: test2", "body", "link", key="h2")
        check(g, "a successful delivery is not queued",
              ok is True and queued is False and len(aq.pending()) == 1)

        # drain_queue is what the watchers call at the start of a pass
        d, p, dr = nw.drain_queue("topic")
        q = aq._load()
        check(g, "drain_queue is wired to the queue", isinstance(d, int) and p >= 0)
    finally:
        nw.push = real_push
        aq.DATA, aq.QUEUE_FILE = real_data, real_file
        shutil.rmtree(data.parent, ignore_errors=True)


# ======================================================================================
# 4. AUTOMATIC CORRECTIONS - one body of evidence may only act once
# ======================================================================================
def _summary(n, excess=-15.0, hit=40.0, base=55.0, cat="rates_up", instr="SILVER"):
    return {(cat, instr): {"n": n, "wins": int(n * hit / 100), "hit": hit, "base": base,
                           "excess": excess, "avg": -0.01, "strength": 3}}


def test_corrections_need_new_evidence():
    import selfcheck as sc
    g = "auto-corrections"
    now = dt.datetime.now(dt.timezone.utc)
    ov, hist = {}, {}

    changed = sc.apply_corrections(_summary(177), ov, hist, now)
    check(g, "a measurably broken lean is downgraded once",
          ov["rates_up.silver"]["drop"] == 1 and len(changed) == 1)

    # The same scored calls were already on file, so the next run read the same numbers
    # and cut the lean again - two notches, the floor, off one sample, 30 minutes apart.
    changed = sc.apply_corrections(_summary(177), ov, hist, now + dt.timedelta(minutes=30))
    check(g, "the same evidence cannot downgrade a second time",
          ov["rates_up.silver"]["drop"] == 1 and not changed,
          f"drop={ov['rates_up.silver']['drop']}")

    # enough time but no new calls - still held
    changed = sc.apply_corrections(_summary(177), ov, hist, now + dt.timedelta(days=3))
    check(g, "time alone does not earn a second notch",
          ov["rates_up.silver"]["drop"] == 1 and not changed)

    # new calls but too soon - still held
    changed = sc.apply_corrections(_summary(177 + sc.MIN_NEW_N), ov, hist,
                                   now + dt.timedelta(hours=1))
    check(g, "new calls too soon do not earn a second notch",
          ov["rates_up.silver"]["drop"] == 1 and not changed)

    # new evidence AND a day's gap - now it may act again
    later = now + dt.timedelta(hours=sc.MIN_HOURS_BETWEEN + 1)
    changed = sc.apply_corrections(_summary(177 + sc.MIN_NEW_N), ov, hist, later)
    check(g, "fresh evidence plus a day's gap earns the second notch",
          ov["rates_up.silver"]["drop"] == 2 and len(changed) == 1,
          f"drop={ov['rates_up.silver']['drop']}")

    # the floor still holds
    changed = sc.apply_corrections(_summary(177 + 5 * sc.MIN_NEW_N), ov, hist,
                                   later + dt.timedelta(days=5))
    check(g, "the floor is respected", ov["rates_up.silver"]["drop"] == 2 and not changed)

    # every decision is on the record, with the evidence it acted on
    rows = hist.get("rates_up.silver") or []
    check(g, "each change is recorded with its evidence",
          len(rows) == 2 and all(r.get("n") and r.get("excess") is not None for r in rows),
          f"{len(rows)} row(s) recorded")
    check(g, "the record says what changed",
          rows[0]["drop_before"] == 1 and rows[0]["drop_after"] == 2)


def test_corrections_recover_and_rollback():
    import selfcheck as sc
    g = "corrections rollback"
    now = dt.datetime.now(dt.timezone.utc)
    ov = {"rates_up.silver": {"drop": 2, "n": 200, "hit": 40.0, "base": 55.0,
                              "excess": -15.0, "at": now.isoformat()}}
    hist = {}

    # recovery steps back a notch at a time rather than deleting the override outright
    changed = sc.apply_corrections(_summary(260, excess=+4.0, hit=59.0), ov, hist,
                                   now + dt.timedelta(days=2))
    check(g, "a recovered lean gets one notch back",
          ov["rates_up.silver"]["drop"] == 1 and len(changed) == 1,
          f"drop={ov.get('rates_up.silver', {}).get('drop')}")
    changed = sc.apply_corrections(_summary(300, excess=+4.0, hit=59.0), ov, hist,
                                   now + dt.timedelta(days=3))
    check(g, "a fully recovered lean loses the override",
          "rates_up.silver" not in ov)

    # the dead band between the two thresholds is hysteresis: poor but not acted on,
    # so a lean cannot flap between downgraded and restored run after run
    ov2 = {"rates_up.silver": {"drop": 1, "n": 200, "at": now.isoformat()}}
    changed = sc.apply_corrections(_summary(400, excess=-6.0), ov2, {},
                                   now + dt.timedelta(days=4))
    check(g, "the dead band holds steady instead of flapping",
          ov2["rates_up.silver"]["drop"] == 1 and not changed)

    # manual rollback
    data = _tmp()
    real_ov, real_hist = sc.OVERRIDES, sc.HISTORY
    try:
        sc.OVERRIDES, sc.HISTORY = data / "decode_overrides.json", data / "override_history.json"
        sc.OVERRIDES.write_text(json.dumps({"rates_up.silver": {"drop": 2, "n": 177},
                                            "geo_deescalation.oil": {"drop": 1, "n": 20}}))
        sc.HISTORY.write_text(json.dumps({}))
        sc.rollback("rates_up.silver")
        ov3 = json.loads(sc.OVERRIDES.read_text())
        check(g, "rollback steps one notch back", ov3["rates_up.silver"]["drop"] == 1)
        sc.rollback("rates_up.silver")
        ov3 = json.loads(sc.OVERRIDES.read_text())
        check(g, "rollback to zero removes the override", "rates_up.silver" not in ov3)
        check(g, "rollback is recorded",
              len(json.loads(sc.HISTORY.read_text())["rates_up.silver"]) == 2)
        sc.rollback("all")
        check(g, "rollback all clears what is left",
              json.loads(sc.OVERRIDES.read_text()) == {})
    finally:
        sc.OVERRIDES, sc.HISTORY = real_ov, real_hist
        shutil.rmtree(data.parent, ignore_errors=True)


def test_override_consumer_unchanged():
    """The override FORMAT must stay readable by commodity_watch - it is the consumer."""
    import commodity_watch as cw
    g = "override consumer"
    dec = {"gold": ("down", 3, "r"), "silver": ("down", 3, "r"), "oil": ("down", 1, "r")}
    out = cw.apply_overrides(dec, "rates_up", {"rates_up.silver": {"drop": 1, "n": 200,
                                                                   "direction": "downgraded"}})
    check(g, "a one-notch downgrade eases the lean", out["silver"][1] == 2)
    out = cw.apply_overrides(dec, "rates_up", {"rates_up.silver": {"drop": 3}})
    check(g, "a downgrade past zero switches the lean off",
          out["silver"][0] == "flat" and out["silver"][1] == 0)
    out = cw.apply_overrides(dec, "rates_up", {})
    check(g, "no override leaves the decode alone", out == dec)


# ======================================================================================
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    for fn in (test_calendar_freshness, test_freshness_report, test_rates_carry,
               test_score_survives_missing_inputs, test_alert_queue,
               test_deliver_queues_on_failure, test_corrections_need_new_evidence,
               test_corrections_recover_and_rollback, test_override_consumer_unchanged):
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
