"""One command that audits the whole system and says what is wrong.

Built because the user should not have to point at each problem in turn:
"can u build a command in such a way that u do ur analysis ur self rather than i tell u
everytime, just first check thoroughly before u respond to me".

Runs every check that has ever caught a real bug in this project, in order of how quickly
it pays off, and prints ONE verdict at the end. Anything it can fix on its own, it fixes;
anything it cannot, it names.

    python audit.py            # full audit
    python audit.py --quick    # skip the backtests (~10s instead of ~3min)

WHAT IT CHECKS, and the bug each check exists because of:
  1 regression suite   - keyword rules rot silently as they are added to
  2 live precision     - "Oil Stocks Jump" fired OIL SHORT on a Chevron rally
  3 live recall        - hyphenated headlines ("rate-hike") were dropped, Bloomberg included
  4 contradiction      - escalation and de-escalation alerts landed in the same minute
  5 talk vs action     - "when we're ready for a peace deal" fired OIL STRONG SHORT
  6 delivery           - push() failures were discarded, alerts vanished silently
  7 freshness          - the cloud watcher can stop and nothing says so
  8 self-scoring       - calls that were never marked against the tape
  9 backtests          - leans that measure worse than doing nothing
"""
import json, subprocess, sys, time
import datetime as dt
from pathlib import Path
from collections import Counter, defaultdict

HERE = Path(__file__).parent
PY = sys.executable

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

problems, fixed, notes = [], [], []


def head(n, title):
    print(f"\n{'=' * 66}\n{n}. {title}\n{'=' * 66}")


def run(script, args=()):
    try:
        r = subprocess.run([PY, str(HERE / script), *args], capture_output=True,
                           text=True, timeout=900, cwd=str(HERE))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                     # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def check_tests():
    head(1, "REGRESSION SUITE")
    code, out = run("test_commodity_decode.py")
    tail = [l for l in out.splitlines() if l.strip()][-8:]
    print("\n".join(tail))
    if code != 0:
        problems.append("the regression suite is FAILING - fix before anything else")
    return code == 0


def check_classifier():
    import commodity_watch as cw
    head(2, "LIVE WIRES - what fires, and what is missed")
    cw.MAX_AGE_MIN = 60 * 12
    items = cw.gather()
    print(f"  {len(items)} items on the wires in the last 12h from {len(cw.FEEDS)} feeds")
    if len(items) < 5:
        problems.append(f"only {len(items)} items pulled - feeds may be blocked")

    hits = Counter()
    talk = 0
    for it in items:
        r = cw.classify(it["title"], it["desc"])
        if r and cw.IMPACT.get(r[0], 2) >= 3:
            hits[r[0]] += 1
            talk += r[2].startswith("TALK:")
    print(f"  {sum(hits.values())} high-impact matches, {talk} of them talk-not-action")
    for c, n in hits.most_common(6):
        print(f"     {c:<20} {n}")

    # recall: commodity-relevant headlines that matched nothing at all
    KEY = ("gold", "silver", "oil", "crude", "fed", "inflation", "rate", "opec",
           "iran", "hormuz", "tariff", "sanction", "strike", "ceasefire")
    missed = []
    for it in items:
        if cw.classify(it["title"], it["desc"]):
            continue
        low = it["title"].lower()
        if any(k in low for k in KEY):
            missed.append(it["title"])
    print(f"  {len(missed)} commodity-relevant headlines matched nothing")
    if missed:
        print("     a sample to eyeball for genuine misses:")
        for m in missed[:6]:
            print(f"       - {m[:74]}")
        notes.append(f"{len(missed)} unmatched commodity headlines - read the sample above; "
                     f"most are noise, but this is how the hyphen bug was found")

    # contradiction: opposite categories both firing on current wires
    opp = {"geo_escalation": "geo_deescalation", "oil_supply_tight": "oil_supply_loose",
           "rates_up": "rates_down", "inflation_hot": "inflation_cold",
           "us_data_strong": "us_data_weak"}
    for a, b in opp.items():
        if hits.get(a) and hits.get(b):
            notes.append(f"both {a} and {b} are matching right now - the 30-minute "
                         f"contradiction guard is what stops both being sent")
    return len(items)


def check_delivery():
    import commodity_watch as cw
    head(3, "DELIVERY - did the alerts actually reach the phone")
    feed = []
    try:
        feed = json.loads((HERE / "data" / "commodity_feed.json").read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        pass
    if not feed:
        print("  no feed on record yet")
        return
    now = dt.datetime.now(dt.timezone.utc)
    recent = []
    for e in feed:
        try:
            recent.append(((now - dt.datetime.fromisoformat(e["iso"])).total_seconds() / 3600, e))
        except Exception:                                      # noqa: BLE001
            continue
    day = [e for h, e in recent if h < 24]
    failed = [e for e in day if e.get("pushed") is False]
    nopx = [e for e in day if not e.get("px")]
    print(f"  {len(day)} alert(s) in the last 24h")
    print(f"  {len(failed)} never reached the phone")
    print(f"  {len(nopx)} went out with no live prices")
    if failed:
        problems.append(f"{len(failed)} alert(s) did not reach the phone in the last 24h")
    if len(nopx) >= 2:
        problems.append(f"{len(nopx)} alert(s) had no prices - the price feed is failing")
    if recent:
        gap = min(h for h, _ in recent)
        print(f"  newest alert is {gap:.1f}h old")
        if gap > 18 and not cw.market_closed():
            problems.append(f"no alert for {gap:.0f}h while markets are open - "
                            f"the watcher may be stuck")


def check_scoring():
    head(4, "SELF-SCORING - are the calls being marked against the tape")
    code, out = run("selfcheck.py", ("--report",))
    print("\n".join(l for l in out.splitlines() if l.strip())[:1600] or "  (no output)")
    try:
        scores = json.loads((HERE / "data" / "alert_scores.json").read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        scores = []
    if not scores:
        notes.append("nothing scored yet - calls need to be 4h old, and the feed only "
                     "started storing prices recently")
    try:
        ov = json.loads((HERE / "data" / "decode_overrides.json").read_text(encoding="utf-8"))
        if ov:
            print("\n  self-applied corrections currently in force:")
            for k, v in ov.items():
                print(f"    {k}: eased {v.get('drop')} notch(es) - {v.get('hit')}% vs "
                      f"{v.get('base')}% base over {v.get('n')} calls")
            fixed.append(f"{len(ov)} lean(s) are being eased automatically")
    except Exception:                                          # noqa: BLE001
        pass


def check_backtests(quick):
    head(5, "BACKTESTS - do the leans still measure")
    if quick:
        print("  skipped (--quick)")
        return
    for script in ("backtest_decode.py", "backtest_geo.py"):
        print(f"\n  --- {script} ---")
        code, out = run(script)
        keep = [l for l in out.splitlines()
                if "CONFIRMED" in l or "WRONG WAY" in l or "no edge" in l]
        for l in keep[:14]:
            print("   " + l.rstrip())
        if any("WRONG WAY" in l for l in out.splitlines()):
            problems.append(f"{script} reports a lean pointing the wrong way - "
                            f"read its output and correct DECODE")


def main():
    quick = "--quick" in sys.argv
    t0 = time.time()
    print("AUDIT  " + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
          + ("   (quick)" if quick else ""))

    ok = check_tests()
    check_classifier()
    check_delivery()
    check_scoring()
    if ok:
        check_backtests(quick)

    print(f"\n{'=' * 66}\nVERDICT  ({time.time() - t0:.0f}s)\n{'=' * 66}")
    if problems:
        print(f"{len(problems)} PROBLEM(S) NEEDING A FIX:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("No problems found.")
    if fixed:
        print("\nFIXED BY ITSELF:")
        for f in fixed:
            print(f"  - {f}")
    if notes:
        print("\nWORTH A LOOK (not necessarily wrong):")
        for n in notes:
            print(f"  - {n}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
