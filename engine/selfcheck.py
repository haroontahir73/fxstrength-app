"""Watch the watcher: score every call against the tape, fix what is measurably wrong.

The standing instruction behind this file: "if you decoded wrong and the price went
against your decoding then you learn the issue and solve it yourself rather than me
telling you every time - always keep an eye on the price as well."

So this does three jobs, on its own, every 30 minutes:

  1. SCORE   - every alert older than the horizon is marked against what the instrument
               actually did. Results accumulate in data/alert_scores.json and never
               expire, so the evidence gets stronger over time rather than resetting.
  2. CORRECT - a lean that is measurably worse than doing nothing gets DOWNGRADED,
               automatically, via data/decode_overrides.json which commodity_watch reads.
  3. REPORT  - health problems (alerts that never reached the phone, alerts sent with no
               price data, the watcher going quiet) are pushed to the phone once, rather
               than sitting unnoticed in a log nobody reads.

DELIBERATE LIMIT: it can only ever WEAKEN a lean, never flip or strengthen one. A run of
losses can be luck; reversing a direction on a small sample is how a system talks itself
into nonsense. Flipping stays a human decision, and this file flags the candidates.

    python selfcheck.py            # score, correct, report
    python selfcheck.py --report   # print the scorecard, change nothing
    python selfcheck.py --history  # every automatic change to a lean, with its evidence
    python selfcheck.py --rollback rates_up.silver   # undo one notch by hand (or `all`)
"""
import json, os, sys, time
import datetime as dt
from pathlib import Path
from collections import defaultdict

import commodity_watch as cw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

DATA = Path(__file__).parent / "data"
SCORES = DATA / "alert_scores.json"
OVERRIDES = DATA / "decode_overrides.json"
HEALTH = DATA / "selfcheck_health.json"
HISTORY = DATA / "override_history.json"

# 4h is MEASURED, not guessed - backtest_horizon.py, 673 events, hourly bars, every
# horizon compared against the unconditional move over a window of the same length:
#   oil     4h +7pp   (1h -1, 2h -4, 8h -5, 24h -7)  <- the one real signal
#   gold    best 1h +1pp   silver best 2h +0pp   DXY best 8h +3pp   GBPUSD negative at all
# Only oil shows an edge that survives the baseline. For the metals and FX every horizon
# lands inside +/-3pp, which is noise at these sample sizes - so tuning each instrument
# to its own "best" would be curve-fitting to randomness. One horizon, set by the only
# instrument with a measurable signal.
# NOTE: this is the AGGREGATE across categories. The daily backtests found strong
# per-category edges (inflation_cold -> gold +15pp) that a blended figure hides, and
# selfcheck scores per category+instrument, which is the level where they exist.
HORIZON_H = 4.0          # how long to give a call before marking it
MIN_N = 20               # never act on fewer than this many scored calls
BAD_EXCESS_PP = -10.0    # this far below the baseline = the lean is not working
MIN_MOVE_PCT = 0.05      # smaller than this is noise, scored as flat and ignored
ALERT_GAP_H = 6          # once per issue per this many hours

# --- what it takes to downgrade the SAME lean a second time ---------------------------
# A downgrade used to need only "excess is still bad", which the previous downgrade had
# no way of changing: the scored calls behind the verdict were already on file, so the
# next run read the same numbers and cut the lean again. Two notches - the floor - off a
# single body of evidence, in two runs 30 minutes apart, with nothing recorded about
# either decision and no way back.
#
# A second notch now has to be earned by evidence the first notch did not see:
MIN_NEW_N = 15           # at least this many NEWLY scored calls since the last notch
MIN_HOURS_BETWEEN = 24   # and at least this long, so a busy hour cannot stack notches
# Rollback: a lean that has recovered gets its notch back one at a time rather than
# having the override deleted outright, so the climb back is as gradual as the descent.
RECOVER_EXCESS_PP = -2.0   # above this it is performing again
MAX_DROP = 2               # the floor, unchanged


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return default


def _save(path, obj):
    try:
        Path(path).parent.mkdir(exist_ok=True)
        Path(path).write_text(json.dumps(obj, indent=1), encoding="utf-8")
    except Exception as e:                                     # noqa: BLE001
        print(f"  could not write {Path(path).name}: {type(e).__name__}")


SYM = {"GOLD": "Gold", "SILVER": "Silver", "OIL": "WTI"}
YF = {"GOLD": "GC=F", "SILVER": "SI=F", "OIL": "CL=F"}
_BARS = {}


def bars(instr):
    """5-minute bars for the last 60 days, cached for the run."""
    if instr in _BARS:
        return _BARS[instr]
    import urllib.request
    from bisect import bisect_left            # noqa: F401  (used by price_at)
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{YF[instr]}"
               f"?range=60d&interval=5m")
        req = urllib.request.Request(url, headers=cw.UA)
        r = json.loads(urllib.request.urlopen(req, timeout=40).read())["chart"]["result"][0]
        pairs = [(a, b) for a, b in zip(r["timestamp"],
                                        r["indicators"]["quote"][0]["close"]) if b]
        _BARS[instr] = ([p[0] for p in pairs], [p[1] for p in pairs])
    except Exception as e:                                     # noqa: BLE001
        print(f"  bars({instr}) failed: {type(e).__name__}")
        _BARS[instr] = ([], [])
    return _BARS[instr]


def price_at(instr, when):
    """Price at a SPECIFIC moment, not 'now'.

    Scoring against the current price made the horizon depend on when this file happened
    to run: at a 30-minute cadence a call was marked anywhere between 4.0h and 4.5h after
    it fired. backtest_horizon.py shows the edge is horizon-sensitive - 4h reads +0.0pp
    while 8h reads -3.0pp - so a sloppy window quietly corrupts the verdict. Reading the
    bar at exactly alert+HORIZON keeps every call measured the same way, and lets this run
    on a slow clock without cost.
    """
    from bisect import bisect_left
    ts, px = bars(instr)
    if not ts:
        return None
    target = when.timestamp()
    if target > ts[-1] + 900:            # not far enough in the past yet
        return None
    i = bisect_left(ts, target)
    if i >= len(ts):
        return None
    if abs(ts[i] - target) > 3 * 3600:   # nearest bar is hours away - market was shut
        return None
    return px[i]


def score_due(feed, now_px, scores):
    """Mark every alert old enough to judge and not already judged."""
    done = {r["id"] for r in scores}
    now = dt.datetime.now(dt.timezone.utc)
    added = 0
    for e in feed:
        iso = e.get("iso")
        px = e.get("px") or {}
        if not iso or not px or not e.get("parts"):
            continue
        try:
            when = dt.datetime.fromisoformat(iso)
        except Exception:                                      # noqa: BLE001
            continue
        age_h = (now - when).total_seconds() / 3600
        if age_h < HORIZON_H:
            continue
        for ln in e["parts"].get("leans", []):
            if ln["dir"] == "flat" or ln["strength"] == 0:
                continue
            key = f"{iso}|{ln['label']}"
            if key in done:
                continue
            sym = SYM.get(ln["label"])
            a = px.get(sym)
            b = price_at(ln["label"], when + dt.timedelta(hours=HORIZON_H))
            if not a or not b:
                continue
            chg = (b - a) / a * 100
            if abs(chg) < MIN_MOVE_PCT:
                outcome = "flat"
            else:
                outcome = "win" if (chg > 0) == (ln["dir"] == "up") else "loss"
            scores.append({"id": key, "cat": e.get("cat"), "instr": ln["label"],
                           "dir": ln["dir"], "strength": ln["strength"],
                           "chg": round(chg, 3), "outcome": outcome,
                           "talk": bool(e.get("talk")), "at": iso})
            added += 1
    return added


def summarise(scores):
    """Per category+instrument: wins, losses, and how that compares to the baseline.

    The baseline is taken from THIS data - the share of all scored windows in which the
    instrument rose - so it self-calibrates to whatever trend the market is in. Judging
    against a flat 50% would blame a short lean for a bull market.
    """
    # LEAVE-ONE-CATEGORY-OUT. The baseline must not be built from the very calls being
    # judged: when one category supplies most of an instrument's rows, its own results
    # define the baseline, excess comes out at 0 every time, and no correction ever
    # fires. Each category is measured against how the instrument behaved on OTHER
    # categories' windows instead.
    by_instr = defaultdict(list)
    for r in scores:
        if r["outcome"] != "flat":
            by_instr[r["instr"]].append(r)

    def baseline_up(instr, exclude_cat):
        rows = [r for r in by_instr[instr] if r["cat"] != exclude_cat]
        if len(rows) < 10:
            return 50.0            # not enough independent evidence - assume a coin flip
        return sum(1 for r in rows if r["chg"] > 0) / len(rows) * 100

    out = {}
    groups = defaultdict(list)
    for r in scores:
        groups[(r["cat"], r["instr"])].append(r)
    for (cat, instr), rows in groups.items():
        graded = [r for r in rows if r["outcome"] != "flat"]
        if not graded:
            continue
        wins = sum(1 for r in graded if r["outcome"] == "win")
        n = len(graded)
        hit = wins / n * 100
        up_rate = baseline_up(instr, cat)
        want_up = graded[0]["dir"] == "up"
        base = up_rate if want_up else 100 - up_rate
        out[(cat, instr)] = {"n": n, "wins": wins, "hit": hit, "base": base,
                             "excess": hit - base,
                             "avg": sum(r["chg"] for r in graded) / n,
                             "strength": graded[-1]["strength"]}
    return out


def _hours_since(iso, now):
    try:
        return (now - dt.datetime.fromisoformat(iso)).total_seconds() / 3600
    except Exception:                                          # noqa: BLE001
        return None


def _log(history, key, action, s, before, after, why):
    """Every automatic change to a lean, on the record.

    Without this the only evidence a correction had happened was the override file's
    current contents, which says what the state is and nothing about how it got there -
    so a lean cut twice off one sample was indistinguishable from one cut twice on two.
    """
    history.setdefault(key, []).insert(0, {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "action": action, "drop_before": before, "drop_after": after,
        "n": s["n"], "hit": round(s["hit"], 1), "base": round(s["base"], 1),
        "excess": round(s["excess"], 1), "why": why})
    del history[key][40:]


def apply_corrections(summary, overrides, history=None, now=None):
    """Downgrade what is measurably not working. Weaken only, never flip.

    Three constraints on top of that, each closing a way the old version could act twice
    on one body of evidence (see the constants above):
      1. a second notch needs MIN_NEW_N calls scored SINCE the first, and a day's gap
      2. every change is written to override_history.json
      3. recovery steps a notch back at a time instead of deleting the override
    """
    changed = []
    history = history if history is not None else {}
    now = now or dt.datetime.now(dt.timezone.utc)
    for (cat, instr), s in summary.items():
        key = f"{cat}.{instr.lower()}"
        if s["n"] < MIN_N:
            continue
        cur_rule = overrides.get(key) or {}
        cur = cur_rule.get("drop", 0)

        # --- recovering: give a notch back, one run at a time ---------------------
        if s["excess"] > RECOVER_EXCESS_PP:
            if not cur:
                continue
            new = cur - 1
            if new <= 0:
                del overrides[key]
            else:
                overrides[key] = {**cur_rule, "drop": new, "n": s["n"],
                                  "hit": round(s["hit"], 1), "base": round(s["base"], 1),
                                  "excess": round(s["excess"], 1),
                                  "at": now.isoformat(), "direction": "restored"}
            _log(history, key, "restore", s, cur, new,
                 f"excess {s['excess']:+.1f}pp is above {RECOVER_EXCESS_PP:+.0f}pp")
            changed.append(f"{key}: RESTORED one notch (now {new}) - it is performing "
                           f"again ({s['hit']:.0f}% vs {s['base']:.0f}% base, n={s['n']})")
            continue

        # --- in the dead band: measurably poor but not bad enough to act on --------
        if s["excess"] > BAD_EXCESS_PP:
            continue

        # --- downgrading --------------------------------------------------------
        if cur >= MAX_DROP:
            continue                     # already at the floor
        if cur:
            # the evidence that earned the LAST notch cannot earn another one
            seen_n = cur_rule.get("n", 0)
            new_n = s["n"] - seen_n
            hrs = _hours_since(cur_rule.get("at", ""), now)
            if new_n < MIN_NEW_N:
                print(f"  [held] {key}: already eased {cur} notch(es) on n={seen_n}; "
                      f"only {new_n} new call(s) since, need {MIN_NEW_N}")
                continue
            if hrs is not None and hrs < MIN_HOURS_BETWEEN:
                print(f"  [held] {key}: last eased {hrs:.1f}h ago, "
                      f"need {MIN_HOURS_BETWEEN}h between notches")
                continue
        overrides[key] = {"drop": cur + 1, "n": s["n"], "hit": round(s["hit"], 1),
                          "base": round(s["base"], 1), "excess": round(s["excess"], 1),
                          "at": now.isoformat(), "direction": "downgraded",
                          "prev_n": cur_rule.get("n") if cur else None}
        _log(history, key, "downgrade", s, cur, cur + 1,
             (f"{s['n'] - cur_rule.get('n', 0)} new calls since the last notch"
              if cur else f"excess {s['excess']:+.1f}pp over {s['n']} calls"))
        changed.append(f"{key}: DOWNGRADED one notch (now {cur + 1}) - {s['hit']:.0f}% vs "
                       f"{s['base']:.0f}% baseline ({s['excess']:+.0f}pp) over "
                       f"{s['n']} scored calls"
                       + (f", {s['n'] - cur_rule.get('n', 0)} of them new" if cur else ""))
    return changed


def health(feed, now_px):
    """Problems worth waking someone for."""
    issues = []
    now = dt.datetime.now(dt.timezone.utc)

    recent = []
    for e in feed:
        try:
            recent.append((dt.datetime.fromisoformat(e["iso"]), e))
        except Exception:                                      # noqa: BLE001
            continue
    recent.sort(reverse=True)

    day = [e for w, e in recent if (now - w).total_seconds() < 86400]
    # An alert that failed to send is now retried from a durable queue, so the thing worth
    # waking someone for is one the queue GAVE UP on - a failure still waiting its turn is
    # the system working. Both are reported, with different weight.
    try:
        import alert_queue
        q_pending, q_dead = alert_queue.pending(), alert_queue.dead(24)
    except Exception:                                          # noqa: BLE001
        q_pending, q_dead = [], []

    if q_dead:
        issues.append(("push", f"{len(q_dead)} alert(s) in the last 24h were given up on "
                               f"after retries and never reached the phone. Newest: "
                               f"{q_dead[0].get('title', '')[:60]}"))
    failed = [e for e in day if e.get("pushed") is False and not e.get("queued_for_retry")]
    if failed:
        issues.append(("push_unqueued", f"{len(failed)} alert(s) in the last 24h failed to "
                                        f"send and were not queued for retry. Newest: "
                                        f"{failed[0].get('title','')[:60]}"))
    if len(q_pending) >= 5:
        issues.append(("queue", f"{len(q_pending)} alert(s) are waiting in the retry queue "
                                f"- delivery has been failing for a while."))

    noprice = [e for e in day if not e.get("px")]
    if len(noprice) >= 2:
        issues.append(("prices", f"{len(noprice)} alert(s) in the last 24h went out with "
                                 f"no live prices - the price feed is failing."))

    if not now_px:
        issues.append(("feed", "Live prices are not loading at all right now."))

    if recent:
        gap_h = (now - recent[0][0]).total_seconds() / 3600
        wd = now.weekday()
        market_open = not (wd == 5 or (wd == 4 and now.hour >= 21)
                           or (wd == 6 and now.hour < 22))
        if gap_h > 18 and market_open:
            issues.append(("quiet", f"No alert for {gap_h:.0f}h while markets are open. "
                                    f"The watcher may be stuck."))
    return issues


def notify(issues, topic):
    """Push each issue at most once per ALERT_GAP_H."""
    state = _load(HEALTH, {})
    now = time.time()
    sent = 0
    for kind, msg in issues:
        if now - state.get(kind, 0) < ALERT_GAP_H * 3600:
            continue
        body = (f"{msg}\n\nThis is the watcher checking itself. Nothing you need to do - "
                f"it is logged so the problem is not invisible.")
        if cw.push(topic, f"SELF-CHECK: {kind}", body, "", "default", "wrench"):
            state[kind] = now
            sent += 1
    _save(HEALTH, state)
    return sent


def report(summary):
    if not summary:
        print("  nothing scored yet - calls need to be "
              f"{HORIZON_H:.0f}h old before they can be marked")
        return
    print(f"{'category / instrument':<34}{'n':>4}{'hit':>7}{'base':>7}{'excess':>9}"
          f"{'avg move':>11}")
    print("-" * 72)
    for (cat, instr), s in sorted(summary.items(), key=lambda kv: kv[1]["excess"]):
        flag = "  <-- not working" if s["n"] >= MIN_N and s["excess"] <= BAD_EXCESS_PP else ""
        print(f"{cat + '.' + instr.lower():<34}{s['n']:>4}{s['hit']:>6.0f}%"
              f"{s['base']:>6.0f}%{s['excess']:>+8.0f}pp{s['avg']:>+10.2f}%{flag}")


def rollback(key):
    """Undo one notch of an automatic downgrade, by hand.

    The corrections were irreversible in practice: nothing recorded what had been changed
    and the only way back was to edit decode_overrides.json and work out from the numbers
    inside it what the lean used to be. `--rollback <key>` (or `--rollback all`) steps a
    notch back and records that it was done by hand, so the history reads as a sequence
    of decisions rather than a current state of unknown origin.
    """
    overrides = _load(OVERRIDES, {})
    history = _load(HISTORY, {})
    keys = list(overrides) if key == "all" else [key]
    done = []
    for k in keys:
        rule = overrides.get(k)
        if not rule:
            print(f"  {k}: no override in force")
            continue
        before = rule.get("drop", 0)
        after = before - 1
        if after <= 0:
            del overrides[k]
        else:
            overrides[k] = {**rule, "drop": after, "direction": "rolled back by hand",
                            "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        history.setdefault(k, []).insert(0, {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "action": "rollback", "drop_before": before, "drop_after": after,
            "why": "manual rollback"})
        done.append(f"{k}: {before} -> {after}")
    if done:
        _save(OVERRIDES, overrides)
        _save(HISTORY, history)
        print("rolled back:")
        for line in done:
            print("  " + line)
    else:
        print("nothing to roll back")
    return done


def show_history():
    history = _load(HISTORY, {})
    overrides = _load(OVERRIDES, {})
    if not history:
        print("  no automatic corrections on record yet")
        return
    for key in sorted(history):
        cur = overrides.get(key, {}).get("drop", 0)
        print(f"\n{key}  (currently eased {cur} notch(es))")
        for row in history[key]:
            n = row.get("n")
            ev = f"n={n}" if n is not None else ""
            print(f"  {row['at'][:16]}  {row['action']:<9} "
                  f"{row.get('drop_before')}->{row.get('drop_after')}  "
                  f"{ev:<8} {row.get('why', '')}")


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        tf = DATA / "ntfy_topic.txt"
        if tf.exists():
            topic = tf.read_text(encoding="utf-8").strip()

    if "--rollback" in sys.argv:
        i = sys.argv.index("--rollback")
        if len(sys.argv) <= i + 1:
            print("usage: selfcheck.py --rollback <cat.instrument | all>")
            return
        rollback(sys.argv[i + 1])
        return

    if "--history" in sys.argv:
        show_history()
        return

    feed = _load(cw.FEED_FILE, [])
    scores = _load(SCORES, [])
    report_only = "--report" in sys.argv

    need_px = any(
        e.get("px") and e.get("iso") and
        (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(e["iso"])
         ).total_seconds() / 3600 >= HORIZON_H
        for e in feed if e.get("iso"))
    now_px = cw.market_snapshot() if (need_px or not report_only) else {}

    added = score_due(feed, now_px, scores)
    if added and not report_only:
        _save(SCORES, scores)
    print(f"  scored {added} new call(s); {len(scores)} total on record")

    summary = summarise(scores)
    report(summary)

    if report_only:
        return

    overrides = _load(OVERRIDES, {})
    history = _load(HISTORY, {})
    changed = apply_corrections(summary, overrides, history)
    if changed:
        _save(OVERRIDES, overrides)
        _save(HISTORY, history)
        print("\nCORRECTIONS APPLIED:")
        for c in changed:
            print("  " + c)
        cw.deliver(topic, "SELF-CHECK: decode corrected",
                   "The watcher marked its own calls against the tape and changed this:\n\n"
                   + "\n".join("- " + c for c in changed)
                   + f"\n\nOnly ever weakened a notch at a time, never flipped - a losing "
                     f"run can be luck. A second notch needs {MIN_NEW_N} newly scored "
                     f"calls and {MIN_HOURS_BETWEEN}h since the last one. Every change is "
                     f"in data/override_history.json, and `selfcheck.py --rollback <key>` "
                     f"undoes one.",
                   "", "default", "wrench", who="selfcheck")

    issues = health(feed, now_px)
    if issues:
        print("\nHEALTH ISSUES:")
        for kind, msg in issues:
            print(f"  [{kind}] {msg}")
        n = notify(issues, topic)
        print(f"  {n} pushed (the rest were already reported recently)")
    else:
        print("\n  health: nothing wrong")


if __name__ == "__main__":
    main()
