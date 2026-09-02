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

HORIZON_H = 4.0          # how long to give a call before marking it
MIN_N = 20               # never act on fewer than this many scored calls
BAD_EXCESS_PP = -10.0    # this far below the baseline = the lean is not working
MIN_MOVE_PCT = 0.05      # smaller than this is noise, scored as flat and ignored
ALERT_GAP_H = 6          # once per issue per this many hours


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
            b = (now_px.get(sym) or [None])[0]
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


def apply_corrections(summary, overrides):
    """Downgrade what is measurably not working. Weaken only, never flip."""
    changed = []
    for (cat, instr), s in summary.items():
        key = f"{cat}.{instr.lower()}"
        if s["n"] < MIN_N:
            continue
        if s["excess"] > BAD_EXCESS_PP:
            # working, or at least not measurably broken - drop any old override
            if key in overrides:
                del overrides[key]
                changed.append(f"{key}: override removed, it is performing again "
                               f"({s['hit']:.0f}% vs {s['base']:.0f}% base, n={s['n']})")
            continue
        cur = overrides.get(key, {}).get("drop", 0)
        if cur >= 2:
            continue                     # already at the floor
        overrides[key] = {"drop": cur + 1, "n": s["n"], "hit": round(s["hit"], 1),
                          "base": round(s["base"], 1), "excess": round(s["excess"], 1),
                          "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        changed.append(f"{key}: DOWNGRADED one notch - {s['hit']:.0f}% vs "
                       f"{s['base']:.0f}% baseline ({s['excess']:+.0f}pp) over "
                       f"{s['n']} scored calls")
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
    failed = [e for e in day if e.get("pushed") is False]
    if failed:
        issues.append(("push", f"{len(failed)} alert(s) in the last 24h never reached "
                               f"the phone. Newest: {failed[0].get('title','')[:60]}"))

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


def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        tf = DATA / "ntfy_topic.txt"
        if tf.exists():
            topic = tf.read_text(encoding="utf-8").strip()

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
    changed = apply_corrections(summary, overrides)
    if changed:
        _save(OVERRIDES, overrides)
        print("\nCORRECTIONS APPLIED:")
        for c in changed:
            print("  " + c)
        cw.push(topic, "SELF-CHECK: decode corrected",
                "The watcher marked its own calls against the tape and changed this:\n\n"
                + "\n".join("- " + c for c in changed)
                + "\n\nOnly ever weakened, never flipped - a losing run can be luck.",
                "", "default", "wrench")

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
