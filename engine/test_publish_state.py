"""Failing cases for publishing the watcher's state to main.

Every test here runs against REAL git: a temporary bare repo stands in for GitHub, and
clones stand in for the watcher runner, the previous watcher run and the PC's pulse.
Nothing touches the live engine/data, nothing leaves the machine, and no notification
can be sent - push() is never called.

    python test_publish_state.py

The scenario that started this, reproduced in test_queued_run:
  a scheduled run is created at commit A, sits queued while the previous loop pushes B,
  then starts on A with a cache holding B's state plus more. The OLD loop's
  commit + `pull --rebase` + push conflicts and can never publish again. The new
  publisher must publish on top of B with nothing lost.
"""
import io, json, os, shutil, subprocess, sys, tempfile, time, contextlib
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Isolate git completely from whoever runs this: no global hooks, signing or identity.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
os.environ["GIT_TERMINAL_PROMPT"] = "0"

import publish_state as ps                                     # noqa: E402

PASS, FAIL = [], []
NOSLEEP = lambda s: None                                       # noqa: E731
ps.BACKOFF_S = (0, 0, 0)                                       # keep failure tests fast


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


# ------------------------------------------------------------------ git world
ID = ["-c", "user.name=tester", "-c", "user.email=t@example.invalid"]


def git(cwd, *args, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()[-300:]}")
    return p


# One fixed clock for the whole suite. A feed entry is a single JSON object copied
# verbatim between the cache and main, so "the same alert" must carry the same iso on
# both sides - deriving it from now() on every call would make two copies of one alert
# look like two alerts.
BASE = dt.datetime.now(dt.timezone.utc)


def iso(minutes_ago):
    return (BASE - dt.timedelta(minutes=minutes_ago)).isoformat()


def entry(tag, minutes_ago):
    return {"when": tag, "iso": iso(minutes_ago), "cat": "rates_up",
            "title": f"GOLD DOWN - {tag}", "body": f"body {tag}", "link": "",
            "talk": False, "pushed": True, "queued_for_retry": False, "push_error": "",
            "px": {"Gold": 4000.0}, "parts": {}}


def write_state(data, feed=None, seen=None, extra=None):
    data.mkdir(parents=True, exist_ok=True)
    if feed is not None:
        (data / "commodity_feed.json").write_text(json.dumps(feed, indent=1))
    if seen is not None:
        (data / "commodity_seen.json").write_text(json.dumps(seen, indent=0))
    for name, obj in (extra or {}).items():
        (data / name).write_text(json.dumps(obj, indent=1) if not isinstance(obj, str) else obj)


def read_remote(origin, path, ref="main"):
    p = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=origin,
                       capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 else None


def world():
    """A bare origin with commit A: two feed entries, two seen hashes, a pulse file and a
    code file. Returns (root, origin, A)."""
    root = Path(tempfile.mkdtemp(prefix="fxpub-"))
    origin = root / "origin.git"
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = root / "seed"
    git(root, "clone", "-q", str(origin), str(seed))
    write_state(seed / "engine" / "data",
                feed=[entry("e1", 300)], seen={"h1": time.time() - 18000},
                extra={"cloud_pulse.json": {"at": "A"}})
    (seed / "engine" / "code.py").write_text("VERSION = 1\n")
    git(seed, "add", "-A")
    git(seed, *ID, "commit", "-q", "-m", "A")
    git(seed, "push", "-q", "origin", "HEAD:main")
    return root, origin, git(seed, "rev-parse", "HEAD").stdout.strip()


def other_writer(root, origin, name, files, msg):
    """Another actor (the previous watcher run, the PC pulse) pushes to main."""
    c = root / name
    if not c.exists():
        git(root, "clone", "-q", str(origin), str(c))
    git(c, "pull", "-q", "--ff-only", "origin", "main")
    for rel, obj in files.items():
        p = c / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(obj if isinstance(obj, str) else
                     json.dumps(obj, indent=0 if "seen" in rel else 1))
    git(c, "add", "-A")
    git(c, *ID, "commit", "-q", "-m", msg)
    git(c, "push", "-q", "origin", "HEAD:main")
    return git(c, "rev-parse", "HEAD").stdout.strip()


def runner_at(root, origin, sha, name="runner"):
    """What actions/checkout gives a queued scheduled run: main, at the trigger SHA."""
    r = root / name
    git(root, "clone", "-q", str(origin), str(r))
    git(r, "checkout", "-q", "-B", "main", sha)
    git(r, "branch", "-q", "--set-upstream-to=origin/main", "main")
    return r, r / "engine" / "data"


@contextlib.contextmanager
def captured():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


# ------------------------------------------------------------------ the incident
OLD_LOOP_BLOCK = r"""
cd engine
for f in commodity_feed.json commodity_seen.json news_seen.json theme_claims.json \
         alert_scores.json decode_overrides.json alert_queue.json override_history.json; do
  [ -f "data/$f" ] && git add "data/$f"
done
git commit -q -m "feed: new commodity alert(s) [skip refresh]" || true
git pull --rebase -q || git rebase --abort || true
git push -q || echo "  push failed - the cache still carries the feed"
"""


def test_queued_run():
    g = "queued run (the incident)"
    root, origin, A = world()
    # The previous loop, still running, pushes a feed commit AFTER our run was created.
    B = other_writer(root, origin, "prev-run", {
        "engine/data/commodity_feed.json": [entry("e2", 120), entry("e1", 300)],
        "engine/data/commodity_seen.json": {"h1": time.time() - 18000,
                                            "h2": time.time() - 7200}}, "feed (prev run)")
    # Its cache, saved when it ended, holds B's state and a hash it saw afterwards.
    cache_feed = [entry("e2", 120), entry("e1", 300)]
    cache_seen = {"h1": time.time() - 18000, "h2": time.time() - 7200, "h3": time.time() - 3600}
    # Our run alerts on a new story on its first pass.
    new_feed = [entry("e4", 1)] + cache_feed
    new_seen = dict(cache_seen, h4=time.time())

    # ---- the OLD loop, verbatim logic: reproduce the failure first
    old, old_data = runner_at(root, origin, A, "old-runner")
    git(old, "config", "user.name", "github-actions[bot]")
    git(old, "config", "user.email", "bot@example.invalid")
    write_state(old_data, feed=new_feed, seen=new_seen)
    p = subprocess.run(["bash", "-c", OLD_LOOP_BLOCK], cwd=old, capture_output=True, text=True)
    out = p.stdout + p.stderr
    tip_after_old = git(origin, "rev-parse", "main").stdout.strip()
    check(g, "OLD loop: the rebase conflicts on the data files", "CONFLICT" in out or
          "could not apply" in out.lower(), out[-300:])
    check(g, "OLD loop: the push is rejected", "push failed" in out, out[-200:])
    check(g, "OLD loop: main never receives the new alert", tip_after_old == B and
          "e4" not in json.dumps(read_remote(origin, "engine/data/commodity_feed.json")))
    # and it stays wedged: a second alert later in the run fails exactly the same way
    write_state(old_data, feed=[entry("e5", 0)] + new_feed)
    p2 = subprocess.run(["bash", "-c", OLD_LOOP_BLOCK], cwd=old, capture_output=True, text=True)
    check(g, "OLD loop: every later pass fails too (wedged for the whole run)",
          "push failed" in p2.stdout + p2.stderr and
          git(origin, "rev-parse", "main").stdout.strip() == B)

    # ---- the NEW publisher, same situation
    r, data = runner_at(root, origin, A)
    write_state(data, feed=new_feed, seen=new_seen)
    head_before = git(r, "rev-parse", "HEAD").stdout.strip()
    with captured():
        rc = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    tip = git(origin, "rev-parse", "main").stdout.strip()
    parent = git(origin, "rev-parse", "main^").stdout.strip()
    feed = read_remote(origin, "engine/data/commodity_feed.json")
    seen = read_remote(origin, "engine/data/commodity_seen.json")
    check(g, "NEW: publish succeeds", rc == 0)
    check(g, "NEW: the commit sits directly on top of the previous run's push",
          parent == B and tip != B)
    check(g, "NEW: the new alert reached main", [e["when"] for e in feed] == ["e4", "e2", "e1"],
          str([e["when"] for e in feed]))
    check(g, "NEW: every seen hash from both sides is on main",
          set(seen) >= {"h1", "h2", "h3", "h4"}, str(sorted(seen)))
    check(g, "NEW: the runner's HEAD did not move (still the tested commit)",
          git(r, "rev-parse", "HEAD").stdout.strip() == head_before == A)
    # a later alert in the same run keeps publishing
    write_state(data, feed=[entry("e5", 0)] + json.loads((data / "commodity_feed.json").read_text()))
    with captured():
        rc2 = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    feed2 = read_remote(origin, "engine/data/commodity_feed.json")
    check(g, "NEW: a second alert later in the run publishes too",
          rc2 == 0 and feed2[0]["when"] == "e5" and len(feed2) == 4)
    shutil.rmtree(root, ignore_errors=True)


def test_cache_older_than_main():
    """A deploy cancels a run before it saves its cache, after it published. The next run
    restores an OLDER cache. Nothing published may be lost and no story re-alerted."""
    g = "cache older than main"
    root, origin, A = world()
    other_writer(root, origin, "cancelled-run", {
        "engine/data/commodity_feed.json": [entry("e2", 60), entry("e1", 300)],
        "engine/data/commodity_seen.json": {"h1": time.time() - 18000, "h2": time.time() - 3600}},
        "feed (run later cancelled)")
    r, data = runner_at(root, origin, A)           # cache == A's state: knows only e1/h1
    with captured():
        adopted = ps.sync_in(repo=r, data=data)
    local_seen = json.loads((data / "commodity_seen.json").read_text())
    check(g, "sync-in adopts the hash only main had", "h2" in local_seen, str(adopted))
    # use the watcher's own loader to prove the story would now be skipped
    import commodity_watch as cw
    real = cw.SEEN_FILE
    cw.SEEN_FILE = data / "commodity_seen.json"
    try:
        check(g, "commodity_watch.load_seen() now treats that story as seen - no duplicate",
              "h2" in cw.load_seen())
    finally:
        cw.SEEN_FILE = real
    check(g, "sync-in adopts the feed entry only main had",
          [e["when"] for e in json.loads((data / "commodity_feed.json").read_text())] == ["e2", "e1"])
    check(g, "sync-in never moves HEAD", git(r, "rev-parse", "HEAD").stdout.strip() == A)

    # Even WITHOUT sync-in, publishing an older cache must not drop main's entries.
    r2, data2 = runner_at(root, origin, A, "runner2")
    write_state(data2, feed=[entry("e9", 0), entry("e1", 300)],
                seen={"h1": time.time() - 18000, "h9": time.time()})
    with captured():
        rc = ps.publish(repo=r2, data=data2, sleep=NOSLEEP)
    feed = [e["when"] for e in read_remote(origin, "engine/data/commodity_feed.json")]
    seen = read_remote(origin, "engine/data/commodity_seen.json")
    check(g, "publish from an older cache keeps main's entry and adds the new one",
          rc == 0 and feed == ["e9", "e2", "e1"], str(feed))
    check(g, "...and keeps main's seen hash", {"h1", "h2", "h9"} <= set(seen))
    shutil.rmtree(root, ignore_errors=True)


def test_concurrent_update():
    g = "concurrent update"
    root, origin, A = world()
    r, data = runner_at(root, origin, A)
    write_state(data, feed=[entry("e6", 0), entry("e1", 300)],
                seen={"h1": time.time() - 18000, "h6": time.time()},
                extra={"cloud_pulse.json": {"at": "A"}})     # runner's (old) pulse copy

    # 1. the PC's pulse lands between our fetch and our push
    calls = []

    def pulse(attempt):
        calls.append(attempt)
        if attempt == 1:
            other_writer(root, origin, "pc", {"engine/data/cloud_pulse.json": {"at": "PULSE"}},
                         "pulse: page was 45 min stale")
    with captured():
        rc = ps.publish(repo=r, data=data, before_push=pulse, sleep=NOSLEEP)
    st = ps.load_status(data)
    check(g, "first push loses the race, second succeeds", rc == 0 and calls == [1, 2],
          str(calls))
    check(g, "the pulse commit is preserved, not overwritten by the runner's older copy",
          read_remote(origin, "engine/data/cloud_pulse.json") == {"at": "PULSE"})
    check(g, "our alert is on main", read_remote(origin, "engine/data/commodity_feed.json")[0]["when"] == "e6")
    check(g, "status records the publish", st.get("last_result") == "published" and not st.get("pending"))

    # 2. a foreign write to the watcher's OWN files lands mid-publish: both must survive
    write_state(data, feed=[entry("e7", 0)] + json.loads((data / "commodity_feed.json").read_text()))

    def rival(attempt):
        if attempt == 1:
            other_writer(root, origin, "pc", {
                "engine/data/commodity_feed.json":
                    [entry("x1", 0.5)] + read_remote(origin, "engine/data/commodity_feed.json"),
                "engine/data/commodity_seen.json":
                    dict(read_remote(origin, "engine/data/commodity_seen.json"), hx=time.time())},
                "rival watcher-state write")
    with captured():
        rc = ps.publish(repo=r, data=data, before_push=rival, sleep=NOSLEEP)
    feed = [e["when"] for e in read_remote(origin, "engine/data/commodity_feed.json")]
    local = [e["when"] for e in json.loads((data / "commodity_feed.json").read_text())]
    check(g, "a rival feed entry and ours are both on main", rc == 0 and
          {"e7", "x1", "e6"} <= set(feed), str(feed))
    check(g, "the rival's seen hash is on main and adopted locally",
          "hx" in read_remote(origin, "engine/data/commodity_seen.json") and
          "hx" in json.loads((data / "commodity_seen.json").read_text()))
    check(g, "the runner carries on from the merged feed", local == feed, f"{local} vs {feed}")
    shutil.rmtree(root, ignore_errors=True)


def test_failed_push_is_visible_and_recovers():
    g = "failed push"
    root, origin, A = world()
    r, data = runner_at(root, origin, A)
    git(r, "remote", "set-url", "--push", "origin", str(root / "nowhere.git"))
    write_state(data, feed=[entry("f1", 0), entry("e1", 300)],
                seen={"h1": time.time() - 18000, "hf1": time.time()})
    with captured() as out:
        rc = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    st = ps.load_status(data)
    check(g, "a failed publish returns 2 and stays pending",
          rc == 2 and st.get("pending") and st.get("consecutive_failures") == 1)
    check(g, "it records why", bool(st.get("last_error")), str(st.get("last_error")))
    check(g, "it emits an Actions warning annotation", "::warning title=Feed publish failed" in out.getvalue())
    check(g, "a single fresh failure is not yet reported as persistent",
          ps.status_problem(data) is None)

    # it keeps trying every pass, and a later alert accumulates rather than being lost
    write_state(data, feed=[entry("f2", 0)] + json.loads((data / "commodity_feed.json").read_text()))
    with captured():
        ps.publish(repo=r, data=data, sleep=NOSLEEP)
    with captured() as out3:
        ps.publish(repo=r, data=data, sleep=NOSLEEP)
    st = ps.load_status(data)
    check(g, "consecutive failures are counted", st.get("consecutive_failures") == 3)
    check(g, "three in a row escalates to an ::error:: annotation",
          "::error title=Feed is not reaching the dashboard" in out3.getvalue())
    problem = ps.status_problem(data)
    check(g, "status_problem() reports it", bool(problem) and "failed 3 time" in problem, str(problem))
    # time alone also escalates: one failure that has lasted 31 minutes
    st1 = dict(st, consecutive_failures=1,
               first_failure_at=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=31)).isoformat())
    (data / ps.STATUS_NAME).write_text(json.dumps(st1))
    check(g, "a 31-minute-old failure is persistent even at 1 attempt", bool(ps.status_problem(data)))
    (data / ps.STATUS_NAME).write_text(json.dumps(st))

    # selfcheck turns it into a health issue (no notification is sent here: health() only
    # lists issues; notify() is what pushes, and it is not called)
    import selfcheck, alert_queue
    real_q, real_data = alert_queue.QUEUE_FILE, ps.DATA
    alert_queue.QUEUE_FILE = data / "alert_queue.json"
    ps.DATA = data
    try:
        issues = selfcheck.health([], {"Gold": (1, 0)})
    finally:
        alert_queue.QUEUE_FILE, ps.DATA = real_q, real_data
    check(g, "selfcheck.health() lists a 'publish' issue", any(k == "publish" for k, _ in issues),
          str(issues))

    # --final exits 1 while it is still failing
    ps.DATA = data
    try:
        with captured():
            final_rc = ps.main(["--final"])
    finally:
        ps.DATA = real_data
    check(g, "--final fails the step (exit 1) while the feed is unpublished", final_rc == 1)
    check(g, "main is untouched while failing", git(origin, "rev-parse", "main").stdout.strip() == A)

    # recovery: push works again
    git(r, "remote", "set-url", "--push", "origin", str(origin))
    with captured():
        rc = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    st = ps.load_status(data)
    feed = [e["when"] for e in read_remote(origin, "engine/data/commodity_feed.json")]
    check(g, "recovers on the next pass", rc == 0 and not st.get("pending")
          and st.get("consecutive_failures") == 0 and st.get("first_failure_at") is None)
    check(g, "nothing accumulated during the outage was lost", feed == ["f2", "f1", "e1"], str(feed))
    check(g, "status_problem() clears", ps.status_problem(data) is None)
    ps.DATA = data
    try:
        with captured():
            final_ok = ps.main(["--final"])
    finally:
        ps.DATA = real_data
    check(g, "--final passes once published", final_ok == 0)
    shutil.rmtree(root, ignore_errors=True)


def test_code_is_never_touched():
    """The watcher must keep running the commit its tests passed - and must not revert a
    newer commit on main to that older code when it publishes."""
    g = "tested code"
    root, origin, A = world()
    C = other_writer(root, origin, "dev", {"engine/code.py": "VERSION = 2\n"}, "code change")
    r, data = runner_at(root, origin, A)
    write_state(data, feed=[entry("c1", 0), entry("e1", 300)])
    with captured():
        rc = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    remote_code = git(origin, "show", "main:engine/code.py").stdout
    check(g, "publish succeeds on top of a newer code commit", rc == 0 and
          git(origin, "rev-parse", "main^").stdout.strip() == C)
    check(g, "main keeps the NEWER code - not reverted to the runner's", remote_code == "VERSION = 2\n")
    check(g, "the runner keeps running its tested code", (r / "engine" / "code.py").read_text() == "VERSION = 1\n")
    check(g, "HEAD unchanged", git(r, "rev-parse", "HEAD").stdout.strip() == A)
    check(g, "the index is untouched (nothing staged)", git(r, "diff", "--cached", "--name-only").stdout == "")
    changed = [l[3:] for l in git(r, "status", "--porcelain").stdout.splitlines()]
    check(g, "only the watcher's data files changed in the working tree",
          all(c.startswith("engine/data/") for c in changed), str(changed))
    pushed_files = git(origin, "diff", "--name-only", "main^", "main").stdout.split()
    check(g, "the published commit changes only watcher-owned files",
          pushed_files and all(f.startswith("engine/data/") and f.split("/")[-1] in ps.FILES
                               for f in pushed_files), str(pushed_files))
    shutil.rmtree(root, ignore_errors=True)


def test_idle_pass_is_free():
    g = "idle pass"
    root, origin, A = world()
    r, data = runner_at(root, origin, A)
    write_state(data, feed=[entry("i1", 0), entry("e1", 300)])
    with captured():
        ps.publish(repo=r, data=data, sleep=NOSLEEP)
    tip = git(origin, "rev-parse", "main").stdout.strip()
    git(r, "remote", "set-url", "origin", str(root / "unreachable.git"))   # any fetch would fail
    with captured():
        rc = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    check(g, "no feed change -> returns 0 without touching the network", rc == 0 and
          not ps.load_status(data).get("pending"))
    check(g, "...and creates no commit", git(origin, "rev-parse", "main").stdout.strip() == tip)
    git(r, "remote", "set-url", "origin", str(origin))
    # a feed change that main already has produces no empty commit
    (data / ps.STATUS_NAME).unlink()
    with captured():
        rc = ps.publish(repo=r, data=data, sleep=NOSLEEP)
    check(g, "state identical to main -> no empty commit", rc == 0 and
          git(origin, "rev-parse", "main").stdout.strip() == tip and
          ps.load_status(data).get("last_result") == "no-change")
    shutil.rmtree(root, ignore_errors=True)


def test_merge_rules():
    g = "merge rules"
    now = time.time()
    m = ps.merge_seen({"a": now, "c": [now, 3, 1.0]}, {"a": now - 9, "b": now - 5, "c": [now - 99, 2, 2.0]})
    check(g, "seen: union, newer timestamp wins", m == {"a": now, "b": now - 5, "c": [now, 3, 1.0]}, str(m))
    big = [entry(f"r{i}", 200 + i) for i in range(70)]
    f = ps.merge_feed([entry("new", 0)], big)
    check(g, "feed: newest first and capped at FEED_KEEP",
          len(f) == ps.FEED_KEEP and f[0]["when"] == "new" and
          [e["iso"] for e in f] == sorted([e["iso"] for e in f], reverse=True))
    e1 = entry("same", 5)
    check(g, "feed: the same alert on both sides appears once",
          len(ps.merge_feed([e1], [dict(e1)])) == 1)
    s = ps.merge_scores([{"id": "x"}, {"id": "y"}], [{"id": "w"}, {"id": "x"}])
    check(g, "scores: union by id, remote order first", [r["id"] for r in s] == ["w", "x", "y"])

    # overrides: a newer logged removal beats an older override still on the other side
    t0, t1 = iso(120), iso(10)
    remote_ov = {"rates_up.silver": {"drop": 1, "at": t0}}
    hist = {"rates_up.silver": [{"at": t1, "action": "restore", "drop_before": 1, "drop_after": 0}]}
    check(g, "overrides: a newer removal is not resurrected",
          ps.merge_overrides({}, remote_ov, hist) == {})
    newer = {"rates_up.silver": {"drop": 2, "at": iso(1)}}
    check(g, "overrides: an override newer than the removal stays",
          ps.merge_overrides(newer, remote_ov, hist) == newer)
    legacy = {"geo_deescalation.oil": {"drop": 2, "n": 20}}
    check(g, "overrides: a legacy override with no history is kept",
          ps.merge_overrides(legacy, legacy, {}) == legacy)
    h = ps.merge_history({"k": [{"at": t1, "action": "a"}]}, {"k": [{"at": t0, "action": "b"},
                                                                   {"at": t1, "action": "a"}]})
    check(g, "history: union without duplicates, newest first", [r["action"] for r in h["k"]] == ["a", "b"])

    # unreadable files
    d = Path(tempfile.mkdtemp(prefix="fxpubm-"))
    (d / "news_seen.json").write_text("{not json")
    (d / "commodity_seen.json").write_text("{broken")
    remote = {"news_seen.json": json.dumps({"z": now}).encode(), "commodity_seen.json": b"[[["}
    merged = ps.merge_all(d, lambda n: remote.get(n))
    check(g, "a corrupt local file is healed from main's copy",
          json.loads(merged["news_seen.json"][0]) == {"z": now})
    check(g, "when both copies are unreadable the file is left alone (not invented, not overwritten)",
          "commodity_seen.json" not in merged)
    shutil.rmtree(d, ignore_errors=True)


def test_queue_never_resends():
    g = "retry queue"
    import alert_queue as aq
    d = Path(tempfile.mkdtemp(prefix="fxpubq-"))
    real = aq.QUEUE_FILE
    aq.QUEUE_FILE = d / "alert_queue.json"
    try:
        with captured():
            older = json.loads(json.dumps(aq.enqueue("k1", "fx", "T1", "body", meta={})))
            snapshot = json.loads((d / "alert_queue.json").read_text())   # main's older copy
            q = aq._load(); q["pending"][0]["due_at"] = 0; aq._save(q)
            sent = []
            aq.drain(lambda e: sent.append(e["key"]) or True)
        local = json.loads((d / "alert_queue.json").read_text())
        inst = f"k1|{older['first_seen_iso']}"
        check(g, "drain() leaves a tombstone for a delivered alert", inst in local.get("delivered", {}))
        merged = ps.merge_queue(local, snapshot)
        check(g, "merging main's older copy does NOT resurrect the delivered alert",
              merged["pending"] == [], str(merged["pending"]))
        # end to end: after a sync-in style merge, drain would not send it again
        (d / "alert_queue.json").write_text(json.dumps(merged))
        sent2 = []
        with captured():
            aq.drain(lambda e: sent2.append(e["key"]) or True)
        check(g, "...so the phone gets it exactly once", sent == ["k1"] and sent2 == [])
        # a NEW alert that reuses the key is a different instance and must survive
        fresh = dict(snapshot["pending"][0], first_seen_iso=iso(0), first_seen=time.time())
        m2 = ps.merge_queue(local, {"pending": [fresh], "dead": []})
        check(g, "a later alert reusing the same key is not mistaken for the delivered one",
              len(m2["pending"]) == 1)
        # given up on one side -> not resurrected from the other
        dead = {"pending": [], "dead": [{"key": "k1", "first_seen_iso": older["first_seen_iso"],
                                          "dropped_at": iso(0)}]}
        check(g, "an alert given up on is not resurrected", ps.merge_queue(dead, snapshot)["pending"] == [])
    finally:
        aq.QUEUE_FILE = real
        shutil.rmtree(d, ignore_errors=True)


# ======================================================================================
if __name__ == "__main__":
    live = HERE / "data"
    before = {p.name: p.read_bytes() for p in live.glob("*.json")} if live.exists() else {}
    for fn in (test_queued_run, test_cache_older_than_main, test_concurrent_update,
               test_failed_push_is_visible_and_recovers, test_code_is_never_touched,
               test_idle_pass_is_free, test_merge_rules, test_queue_never_resends):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised", f"{type(e).__name__}: {e}"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    after = {p.name: p.read_bytes() for p in live.glob("*.json")} if live.exists() else {}
    check("isolation", "live engine/data is byte-identical after the suite", before == after,
          str(sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]))

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
