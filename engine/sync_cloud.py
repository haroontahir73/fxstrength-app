"""Push the manual override files to the cloud repo that publishes the dashboard.

WHY THIS EXISTS
The desk runs in two places. The pipeline code is byte-identical in both, but the
*judgment* inputs are not:

    local   E:\\VSISA\\tradingview-mcp-main\\fxstrength\\data\\        <- where the daily task writes
    cloud   github.com/haroontahir73/fxstrength-app engine/data/  <- what Pages + the APK read

The GitHub Actions workflow pins the override files with `git checkout --` before every
run, so the ONLY way research reaches the phone is a commit to that repo. Without this
script the phone silently keeps whatever was last committed - on 2026-09-03 an audit
found it had been serving 30 Aug judgment for four days, with gold 7.8 points and
NZD 4.8 points away from the local board.

Committing engine/data/** triggers refresh.yml (a dashboard rebuild) but NOT
news-watch.yml, which excludes engine/data from its push paths. So this is safe to run
as often as you like; it does nothing when the files already match.

    python sync_cloud.py            # sync, commit and push if anything changed
    python sync_cloud.py --dry-run  # say what would change, touch nothing
"""
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CLONE = Path(r"E:\VSISA\fxstrength-app")
DEST = CLONE / "engine" / "data"

# The four files the workflow pins. commodity_feed.json is deliberately NOT here -
# the news-watch loop commits that one itself from the cloud.
FILES = ["fundamentals_manual.json", "commodities_manual.json",
         "rate_expectations_manual.json", "speech_tone.json",
         # Daily open interest. CME only answers a real browser, so the cloud can never
         # fetch this itself - it is captured on this PC and carried up here. Days are
         # frozen once stored (oi.FREEZE_STORED_DAYS), so this file only ever grows.
         "oi_history.json"]

PAGE = "https://haroontahir73.github.io/fxstrength-app/dashboard.html"
PULSE_STATE = HERE / "data" / "cloud_pulse_state.json"
PULSE_AFTER_MIN = 35      # how stale the PUBLISHED page may get before we nudge it
PULSE_MIN_GAP_MIN = 30    # never nudge more often than this
PULSE_GIVE_UP = 3         # consecutive nudges that failed to move it -> stop, say so


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(CLONE), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    return r.stdout.strip()


def published_built_at():
    """When the page the phone actually reads was last built, from the page itself.

    Measures the thing we care about - how stale the phone is - rather than trusting
    GitHub's scheduler to tell us. A Range request pulls only the last 1800 bytes (the
    footer carries the "Built ... UTC" line), so this costs nothing to call every wake.
    Returns None if the page or the line cannot be read; callers treat that as "unknown,
    do nothing", because nudging on a failed read would nudge forever.
    """
    try:
        req = urllib.request.Request(
            PAGE, headers={"User-Agent": "fxstrength-desk", "Range": "bytes=-1800"})
        with urllib.request.urlopen(req, timeout=30) as f:
            tail = f.read().decode("utf-8", "replace")
    except Exception:                                                  # noqa: BLE001
        return None
    m = re.search(r"Built (\d{2} \w{3} \d{4} \d{2}:\d{2}) UTC", tail)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%d %b %Y %H:%M").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def read_pulse_state():
    try:
        return json.loads(PULSE_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pulse(dry):
    """Nudge the cloud into rebuilding when GitHub's scheduler has dropped the job.

    refresh.yml is set to cron '*/30' but measured over three days it delivered 19
    scheduled runs - median gap 235 minutes, worst 7.8 hours. Discrete crons are not the
    answer either: news-watch.yml uses '7,37' and did no better (median 273 min).
    Scheduled workflows on free/public repos are best-effort and get dropped wholesale.

    But refresh.yml ALSO triggers on any push touching engine/**, and this PC has a
    scheduled task that genuinely runs every 15 minutes. So the reliable clock is here,
    not at GitHub: when the published page has gone stale, commit a one-line heartbeat
    and let the push trigger the rebuild. (engine/data/** is excluded from news-watch's
    push paths, so this never restarts the alert watcher.)

    Deliberately self-limiting - it only commits when the scheduler has ALREADY failed,
    so a healthy week produces no heartbeat commits at all.
    """
    built = published_built_at()
    if built is None:
        print("  pulse: could not read the published page, skipped")
        return
    now = dt.datetime.now(dt.timezone.utc)
    age = (now - built).total_seconds() / 60
    if age < PULSE_AFTER_MIN:
        print(f"  pulse: page is {age:.0f} min old, healthy - no nudge")
        return

    st = read_pulse_state()
    last = st.get("last_pulse_at")
    if last:
        since = (now - dt.datetime.fromisoformat(last)).total_seconds() / 60
        if since < PULSE_MIN_GAP_MIN:
            print(f"  pulse: nudged {since:.0f} min ago, holding off")
            return

    # If our last few nudges did not move the page, the build itself is broken and more
    # commits will not help - stop and make the real problem visible.
    if st.get("failed_streak", 0) >= PULSE_GIVE_UP and st.get("last_seen_built") == built.isoformat():
        print(f"  pulse: {st['failed_streak']} nudges did not move the page "
              f"(stuck at {built:%d %b %H:%M} UTC) - the cloud build is broken, not the cron")
        return

    if dry:
        print(f"  pulse: page is {age:.0f} min stale -> would nudge the cloud")
        return

    stamp = now.replace(microsecond=0).isoformat()
    (DEST / "cloud_pulse.json").write_text(json.dumps({
        "pulse": stamp,
        "reason": f"published page was {age:.0f} min stale; GitHub's cron had not fired",
        "note": "written by sync_cloud.py on the local PC - a push here triggers refresh.yml",
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    git("add", "engine/data/cloud_pulse.json")
    git("commit", "-q", "-m", f"pulse: page was {age:.0f} min stale, triggering a rebuild")
    git("push", "-q", "origin", "main")

    streak = st.get("failed_streak", 0) + 1 if st.get("last_seen_built") == built.isoformat() else 1
    PULSE_STATE.write_text(json.dumps({
        "last_pulse_at": stamp,
        "last_seen_built": built.isoformat(),
        "failed_streak": streak,
    }, indent=2), encoding="utf-8")
    print(f"  pulse: page was {age:.0f} min stale - nudged the cloud into a rebuild")


def main():
    dry = "--dry-run" in sys.argv
    if not CLONE.exists():
        raise SystemExit(f"clone not found: {CLONE}")

    # The clone is a working copy that drifts behind; always start from the remote so a
    # push can never clobber the news-watch loop's own commits.
    git("fetch", "origin", "--quiet")
    dirty = git("status", "--porcelain")
    if dirty and not dry:
        raise SystemExit("clone has uncommitted changes - resolve by hand:\n" + dirty)
    if not dry:
        git("checkout", "-q", "main")
        git("reset", "--hard", "-q", "origin/main")

    changed = []
    for f in FILES:
        src, dst = HERE / "data" / f, DEST / f
        if not src.exists():
            print(f"  {f}: missing locally, skipped")
            continue
        text = src.read_text(encoding="utf-8")
        # Compare against what is actually PUBLISHED (origin/main), not the working tree -
        # the clone is a partial checkout that can be missing engine/ entirely, which
        # would report every file as changed. And compare the PARSED json, not the bytes:
        # local is CRLF and the repo is LF, so a byte compare marks all four as changed
        # on every run and commits noise forever. Only a real change should push.
        published = git("show", f"origin/main:engine/data/{f}", check=False)
        try:
            same = bool(published) and json.loads(published) == json.loads(text)
        except ValueError:
            same = False
        if same:
            print(f"  {f}: in sync")
            continue
        changed.append(f)
        print(f"  {f}: CHANGED -> {'would push' if dry else 'copied'}")
        if not dry:
            # newline="\n" so the committed file stays LF and git sees a clean diff.
            dst.write_text(text, encoding="utf-8", newline="\n")

    if not changed:
        print("nothing to push - the phone already has this.")
        # Overrides being in sync says nothing about the NEWS and PRICES on the page,
        # which the cloud rebuilds on its own (unreliable) schedule. Check that too.
        pulse(dry)
        return 0
    if dry:
        print(f"\n--dry-run: {len(changed)} file(s) would be pushed")
        pulse(dry)
        return 0

    git("add", *[f"engine/data/{f}" for f in changed])
    git("commit", "-q", "-m",
        "overrides: today's tone scores, rate odds and commodity overlay\n\n"
        "Researched by the fx-strength-desk-daily task on the local machine. The\n"
        "workflow pins these files, so without this commit the published dashboard\n"
        "and the phone app keep serving the previous run's judgment.")
    git("push", "-q", "origin", "main")
    print(f"\npushed {len(changed)} file(s) - refresh.yml will rebuild the dashboard.")
    print("live shortly: https://haroontahir73.github.io/fxstrength-app/dashboard.html")
    # No pulse here on purpose: this push has already triggered the rebuild.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
