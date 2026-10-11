"""Publish the watcher's own state files to main - without ever rebasing.

THE FAILURE THIS FIXES
----------------------
The news-watch loop used to publish like this, on every pass that added a feed entry:

    git add data/<state files>; git commit
    git pull --rebase -q || git rebase --abort || true
    git push || echo "push failed - the cache still carries the feed"

and that can wedge for an entire 5.5-hour run. A scheduled run is created when the cron
fires but can sit queued behind the previous loop for over an hour, and actions/checkout
gives it the commit main pointed at when it was CREATED. If the previous loop pushes a
feed commit in that gap, the new run starts from a stale base while its restored cache
already holds the newer files. Its first commit then conflicts with main on exactly those
files, `rebase --abort` leaves it diverged, and every later pull re-applies the same
doomed commit. Measured on run 38081932047 (2026-10-10): triggered 19:59Z, started
21:18Z, the previous loop had pushed at 20:46Z - three push failures and no feed update
reached main for the whole run. All the while the run itself was green.

HOW THIS PUBLISHES INSTEAD
--------------------------
It never moves HEAD, never touches the index, never rebases and never touches code:

  1. fetch main's CURRENT tip
  2. merge each watcher-owned file field by field - remote copy + local copy
  3. build a tree from the tip with ONLY those files replaced, commit it on top of the tip
  4. push that commit; if main moved in the meantime, start again from 1

Because the commit is built from the tip, it can never conflict and can never revert
anything else on main: cloud_pulse.json, the manual override files and the CODE are all
taken from the tip as they are. And because HEAD and the working tree's code are left
alone, the watcher keeps running exactly the commit its test gate passed.

THE MERGE RULES (why nothing is lost and no alert is sent twice)
----------------------------------------------------------------
The watcher's cache can be OLDER than main as well as newer: a run cancelled by a deploy
never saves its cache, but may already have published. So neither side simply wins.

  commodity_feed.json     union of alerts by (iso, cat, title), newest first, capped
  *_seen.json             union of story hashes; the newer timestamp wins on a clash -
                          a story either side has alerted on stays seen, so it is not
                          re-sent
  theme_claims.json       per theme, the newer claim wins
  alert_scores.json       union of scored calls by id
  override_history.json   union of decisions, newest first, capped
  decode_overrides.json   per lean, the side with the newer decision wins, and a newer
                          logged removal beats an older override still on the other side
  alert_queue.json        union of pending retries, minus any instance either side has
                          already delivered (tombstones) or given up on - so a retry is
                          never resurrected and sent twice

The merged result is also written back to the working tree, so the next pass carries on
from it rather than from a stale copy.

WHEN THIS RUNS
--------------
  --sync-in   once, before the loop: adopt anything main has that the cache lacks
  (default)   every pass: publish if the feed changed since the last publish, or if a
              previous publish is still pending; otherwise exit at once, no network
  --final     after the loop: one last attempt, and exit 1 if the feed still has not
              reached main - a red run is visible, a green run with a stale feed was not

Failures are recorded in data/publish_status.json (cached, never committed), surfaced as
Actions annotations, and reported by selfcheck.py once they persist.
"""
import hashlib, json, os, subprocess, sys, tempfile, time
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STATUS_NAME = "publish_status.json"

REMOTE = "origin"
BRANCH = "main"
TRACKING_REF = "refs/watcher/remote-main"
COMMIT_MSG = "feed: new commodity alert(s) [skip refresh]"

PUSH_ATTEMPTS = 4                # per publish: refetch + remerge + retry
BACKOFF_S = (2, 5, 10)           # between attempts that failed for a non-race reason
FAIL_ERROR_AFTER = 3             # consecutive failed publishes before ::error::
STALE_ERROR_MIN = 30             # pending and failing this long = persistent failure

try:                                                   # keep the cap in one place
    from commodity_watch import FEED_KEEP
except Exception:                                      # noqa: BLE001
    FEED_KEEP = 60
DEAD_KEEP = 30
DELIVERED_TTL_H = 48
HISTORY_KEEP = 40


# ------------------------------------------------------------------ merge rules
def _ts(v):
    """Timestamp of a seen / claim entry: a bare number, or a list led by one."""
    if isinstance(v, bool):
        return float("-inf")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list) and v and isinstance(v[0], (int, float)) \
            and not isinstance(v[0], bool):
        return float(v[0])
    return float("-inf")


def _iso(s):
    """Sortable form of an ISO timestamp; unparseable sorts oldest."""
    try:
        t = dt.datetime.fromisoformat(str(s))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t
    except Exception:                                  # noqa: BLE001
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def merge_seen(local, remote):
    """Dedup hashes and cooldowns. Union; on a clash the newer timestamp wins.

    Union is what prevents a duplicate phone alert: a story either side has already
    alerted on must stay seen. Local wins a tie so an unchanged file stays byte-equal."""
    out = dict(remote) if isinstance(remote, dict) else {}
    for k, v in (local.items() if isinstance(local, dict) else ()):
        if k not in out or _ts(v) >= _ts(out[k]):
            out[k] = v
    return out


merge_claims = merge_seen          # theme -> [ts, who]: same rule, newer claim wins


def merge_feed(local, remote, keep=None):
    """Feed entries are never edited after creation, so identity is enough."""
    keep = keep or FEED_KEEP

    def key(e):
        return (e.get("iso") or "", e.get("cat") or "", e.get("title") or "")
    by = {}
    for src in (remote, local):                        # local copy wins on the same alert
        for e in (src if isinstance(src, list) else ()):
            if isinstance(e, dict):
                by[key(e)] = e
    return sorted(by.values(), key=lambda e: _iso(e.get("iso")), reverse=True)[:keep]


def merge_scores(local, remote):
    """Union by id, remote order first so the published file only ever grows at the end."""
    out, seen = [], set()
    for src in (remote, local):
        for r in (src if isinstance(src, list) else ()):
            if not isinstance(r, dict):
                continue
            k = r.get("id") or json.dumps(r, sort_keys=True)
            if k not in seen:
                seen.add(k)
                out.append(r)
    return out


def merge_history(local, remote):
    out = {}
    for src in (remote, local):
        for k, rows in (src.items() if isinstance(src, dict) else ()):
            bucket = out.setdefault(k, {})
            for r in (rows if isinstance(rows, list) else ()):
                if isinstance(r, dict):
                    ident = (r.get("at"), r.get("action"), r.get("drop_before"),
                             r.get("drop_after"))
                    bucket[ident] = r
    return {k: sorted(v.values(), key=lambda r: _iso(r.get("at")), reverse=True)[:HISTORY_KEEP]
            for k, v in out.items()}


def merge_overrides(local, remote, history):
    """Per lean, the newer decision wins - including a newer logged REMOVAL.

    A plain union would resurrect an override that selfcheck deliberately removed on one
    side while the other still holds the older copy, i.e. re-weaken a lean that had
    earned its notch back. override_history.json records the removal, so it decides."""
    local = local if isinstance(local, dict) else {}
    remote = remote if isinstance(remote, dict) else {}
    history = history if isinstance(history, dict) else {}
    out = {}
    for k in list(remote) + [k for k in local if k not in remote] + \
            [k for k in history if k not in remote and k not in local]:
        cands = [r for r in (remote.get(k), local.get(k)) if isinstance(r, dict)]
        best = None
        for r in cands:                                # local wins a tie (listed last)
            if best is None or _iso(r.get("at")) >= _iso(best.get("at")):
                best = r
        events = [e for e in history.get(k, []) if isinstance(e, dict)]
        last = max(events, key=lambda e: _iso(e.get("at")), default=None)
        if last is not None and (last.get("drop_after") is not None
                                 and last.get("drop_after") <= 0) \
                and (best is None or _iso(last.get("at")) >= _iso(best.get("at"))):
            continue                                   # most recent decision removed it
        if best is not None:
            out[k] = best
    return out


def _instance(e):
    """One queued alert INSTANCE. The key alone is not enough: some senders reuse a key
    (selfcheck titles), and a new failure must not be mistaken for a delivered old one."""
    return f"{e.get('key')}|{e.get('first_seen_iso')}"


def _norm_queue(q):
    if isinstance(q, list):
        q = {"pending": q}
    q = q if isinstance(q, dict) else {}
    return {"pending": [e for e in q.get("pending") or [] if isinstance(e, dict)],
            "dead": [e for e in q.get("dead") or [] if isinstance(e, dict)],
            "delivered": q.get("delivered") if isinstance(q.get("delivered"), dict) else {}}


def merge_queue(local, remote, now=None):
    """Union the retry queues WITHOUT resurrecting anything already finished.

    An instance delivered on one side but still pending in the other's older copy must not
    come back, or the phone gets it twice. Delivered instances leave a tombstone (see
    alert_queue.drain); given-up ones are in `dead`. Both are subtracted from pending."""
    now = now or time.time()
    L, R = _norm_queue(local), _norm_queue(remote)
    delivered = merge_seen(L["delivered"], R["delivered"])
    delivered = {k: v for k, v in delivered.items()
                 if _ts(v) >= now - DELIVERED_TTL_H * 3600}
    dead_by = {}
    for e in R["dead"] + L["dead"]:
        dead_by[(e.get("key"), e.get("first_seen_iso"), e.get("dropped_at"))] = e
    finished = set(delivered) | {f"{e.get('key')}|{e.get('first_seen_iso')}"
                                 for e in dead_by.values()}
    pend = {}
    for e in R["pending"] + L["pending"]:
        inst = _instance(e)
        if inst in finished:
            continue
        cur = pend.get(inst)
        if cur is None or (e.get("attempts", 0), e.get("due_at", 0)) >= \
                (cur.get("attempts", 0), cur.get("due_at", 0)):
            pend[inst] = e
    return {"pending": sorted(pend.values(), key=lambda e: e.get("first_seen", 0)),
            "dead": sorted(dead_by.values(), key=lambda e: _iso(e.get("dropped_at")),
                           reverse=True)[:DEAD_KEEP],
            "delivered": delivered}


# name -> (merge, json indent the watcher itself writes it with)
# decode_overrides is merged last, from the already-merged history.
FILES = {
    "commodity_feed.json":   (merge_feed, 1),
    "commodity_seen.json":   (merge_seen, 0),
    "news_seen.json":        (merge_seen, 0),
    "theme_claims.json":     (merge_claims, 0),
    "alert_scores.json":     (merge_scores, 1),
    "override_history.json": (merge_history, 1),
    "decode_overrides.json": (None, 1),
    "alert_queue.json":      (merge_queue, 1),
}


# ------------------------------------------------------------------ git plumbing
class GitError(RuntimeError):
    pass


def _git(repo, *args, input=None, env=None, check=True):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if env:
        e.update(env)
    p = subprocess.run(["git", *args], cwd=repo, input=input, env=e,
                       capture_output=True)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])}: "
                       f"{p.stderr.decode('utf-8', 'replace').strip()[-300:]}")
    return p


def _out(p):
    return p.stdout.decode("utf-8", "replace").strip()


def _identity(repo):
    """commit-tree needs an author. The workflow sets one; fall back if it has not."""
    have = _out(_git(repo, "config", "user.name", check=False))
    if have:
        return {}
    return {"GIT_AUTHOR_NAME": "github-actions[bot]",
            "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "github-actions[bot]",
            "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"}


def _fetch_tip(repo):
    shallow = _out(_git(repo, "rev-parse", "--is-shallow-repository", check=False)) == "true"
    args = ["fetch", "-q"] + (["--depth=1"] if shallow else []) + \
           [REMOTE, f"+refs/heads/{BRANCH}:{TRACKING_REF}"]
    _git(repo, *args)
    return _out(_git(repo, "rev-parse", TRACKING_REF))


def _remote_bytes(repo, tip, path):
    p = _git(repo, "show", f"{tip}:{path}", check=False)
    return p.stdout if p.returncode == 0 else None


# ------------------------------------------------------------------ merge all files
def _parse(b):
    if b is None:
        return None
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:                                  # noqa: BLE001
        return None


def _dump(obj, indent):
    return json.dumps(obj, indent=indent).encode("utf-8")


def merge_all(data, read_remote, now=None):
    """Merge every watcher-owned file. Returns {name: (merged_bytes, adopted_from_remote)}
    for files that exist on either side. `read_remote(name)` returns bytes or None."""
    out, parsed = {}, {}
    for name, (fn, indent) in FILES.items():
        lp = data / name
        lb = lp.read_bytes() if lp.exists() else None
        rb = read_remote(name)
        lv, rv = _parse(lb), _parse(rb)
        if lb is None and rb is None:
            continue
        if lv is None and rv is None:
            # both unreadable: never invent content and never overwrite either side -
            # leave main's copy and the local copy exactly as they are
            continue
        if name == "decode_overrides.json":
            mv = merge_overrides(lv, rv, parsed.get("override_history.json"))
        elif name == "alert_queue.json":
            mv = merge_queue(lv, rv, now=now)
        else:
            mv = fn(lv, rv)
        parsed[name] = mv
        if lv is not None and mv == lv:
            mb = lb                                    # unchanged locally: keep its bytes
        elif rv is not None and mv == rv:
            mb = rb                                    # remote already has it all
        else:
            mb = _dump(mv, indent)
        out[name] = (mb, lb != mb)
    return out


def _write_local(data, merged):
    adopted = []
    for name, (b, changed) in merged.items():
        if not changed:
            continue
        p = data / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(b)
        os.replace(tmp, p)
        adopted.append(name)
    return adopted


# ------------------------------------------------------------------ status
def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_status(data=None):
    p = (data or DATA) / STATUS_NAME
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}


def _save_status(data, st):
    try:
        (data / STATUS_NAME).write_text(json.dumps(st, indent=1), encoding="utf-8")
    except Exception as e:                             # noqa: BLE001
        print(f"  could not write {STATUS_NAME}: {type(e).__name__}")


def _feed_sha(data):
    p = data / "commodity_feed.json"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def status_problem(data=None, now=None):
    """A sentence for the health report if publishing has persistently failed, else None."""
    st = load_status(data)
    if not st.get("pending") or not st.get("first_failure_at"):
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    mins = (now - _iso(st["first_failure_at"])).total_seconds() / 60
    if mins < STALE_ERROR_MIN and st.get("consecutive_failures", 0) < FAIL_ERROR_AFTER:
        return None
    last_ok = st.get("last_success_at")
    since = (f"since {last_ok[:16].replace('T', ' ')} UTC" if last_ok else "this run")
    return (f"New alerts have not reached the dashboard feed {since} - publishing to "
            f"main has failed {st.get('consecutive_failures', 0)} time(s) in a row "
            f"({(st.get('last_error') or 'unknown error')[:120]}). Phone alerts are "
            f"unaffected; the feed will catch up once a publish succeeds.")


def _annotate(level, title, msg):
    print(f"::{level} title={title}::{msg}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and level == "error":
        try:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"### {title}\n\n{msg}\n\n")
        except Exception:                              # noqa: BLE001
            pass


# ------------------------------------------------------------------ entry points
def _repo_root(start):
    p = _git(start, "rev-parse", "--show-toplevel", check=False)
    return Path(_out(p)) if p.returncode == 0 else None


def sync_in(repo=None, data=None):
    """Adopt anything main holds that the local (cached) state lacks. No commit, no push."""
    data = data or DATA
    repo = repo or _repo_root(data)
    if repo is None:
        print("  sync-in: not a git checkout - skipped")
        return []
    rel = data.resolve().relative_to(Path(repo).resolve()).as_posix()
    tip = _fetch_tip(repo)
    merged = merge_all(data, lambda n: _remote_bytes(repo, tip, f"{rel}/{n}"))
    adopted = _write_local(data, merged)
    if adopted:
        print(f"  sync-in: merged newer state from main {tip[:9]} into "
              f"{', '.join(adopted)} - the cache was missing some of it")
    else:
        print(f"  sync-in: local state already includes everything on main {tip[:9]}")
    return adopted


def publish(repo=None, data=None, final=False, before_push=None, sleep=time.sleep,
            force=False):
    """Publish the watcher's state if the feed changed or a publish is pending.

    Returns 0 when published or nothing to do, 2 when the publish failed and is pending.
    `before_push(attempt)` is a test hook, called just before each push."""
    data = data or DATA
    repo = repo or _repo_root(data)
    if repo is None:
        print("  publish: not a git checkout - skipped")
        return 0
    st = load_status(data)
    sha = _feed_sha(data)
    if not force and not st.get("pending") and sha == st.get("published_feed_sha"):
        return 0                                       # nothing new: no network at all

    rel = data.resolve().relative_to(Path(repo).resolve()).as_posix()
    st["last_attempt_at"] = _now_iso()
    errors = []
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            tip = _fetch_tip(repo)
            merged = merge_all(data, lambda n: _remote_bytes(repo, tip, f"{rel}/{n}"))
            _write_local(data, merged)                 # carry on from the merged state
            with tempfile.TemporaryDirectory() as td:
                env = {"GIT_INDEX_FILE": str(Path(td) / "index")}
                _git(repo, "read-tree", tip, env=env)
                for name, (b, _) in merged.items():
                    blob = _out(_git(repo, "hash-object", "-w", "--stdin", input=b))
                    _git(repo, "update-index", "--add", "--cacheinfo",
                         f"100644,{blob},{rel}/{name}", env=env)
                tree = _out(_git(repo, "write-tree", env=env))
            if tree == _out(_git(repo, "rev-parse", f"{tip}^{{tree}}")):
                st.update(pending=False, consecutive_failures=0, first_failure_at=None,
                          last_error="", last_result="no-change",
                          last_success_at=_now_iso(), published_feed_sha=_feed_sha(data),
                          last_published_commit=tip)
                _save_status(data, st)
                return 0
            commit = _out(_git(repo, "commit-tree", tree, "-p", tip, "-m", COMMIT_MSG,
                               env=_identity(repo)))
            if before_push:
                before_push(attempt)
            p = _git(repo, "push", "-q", REMOTE, f"{commit}:refs/heads/{BRANCH}",
                     check=False)
            if p.returncode == 0:
                st.update(pending=False, consecutive_failures=0, first_failure_at=None,
                          last_error="", last_result="published",
                          last_success_at=_now_iso(), published_feed_sha=_feed_sha(data),
                          last_published_commit=commit)
                _save_status(data, st)
                print(f"  published watcher state to {BRANCH} as {commit[:9]}"
                      + (f" (attempt {attempt})" if attempt > 1 else ""))
                return 0
            err = p.stderr.decode("utf-8", "replace")
            # only a lost race is retried at once; "[remote rejected]" (permissions,
            # protection) is a real failure and gets the backoff like any other
            raced = "non-fast-forward" in err or "fetch first" in err
            errors.append("main moved during the push" if raced else
                          (err.strip().splitlines() or ["push failed"])[-1][:160])
            if raced:
                continue                               # refetch + remerge at once
        except GitError as e:
            errors.append(str(e)[:160])
        except Exception as e:                         # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}"[:160])
        if attempt < PUSH_ATTEMPTS:
            sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])

    # every attempt failed: record it, keep it pending, and say so
    st["pending"] = True
    st["consecutive_failures"] = st.get("consecutive_failures", 0) + 1
    st["first_failure_at"] = st.get("first_failure_at") or st["last_attempt_at"]
    st["last_error"] = errors[-1] if errors else "unknown"
    st["last_result"] = "failed"
    _save_status(data, st)
    _annotate("warning", "Feed publish failed",
              f"attempt series {st['consecutive_failures']} failed: {st['last_error']}. "
              f"The state is kept and will be retried on the next pass.")
    problem = status_problem(data)
    if problem or final:
        _annotate("error", "Feed is not reaching the dashboard",
                  problem or f"publishing still failing at the end of the run: "
                             f"{st['last_error']}")
    return 2


def main(argv):
    if "--sync-in" in argv:
        try:
            sync_in()
        except Exception as e:                         # noqa: BLE001
            # not fatal: the first publish merges main in anyway
            _annotate("warning", "Watcher state sync-in failed", f"{type(e).__name__}: {e}")
        return 0
    final = "--final" in argv
    rc = publish(final=final)
    if final:
        st = load_status()
        if st.get("pending"):
            print(f"FEED NOT PUBLISHED: {st.get('consecutive_failures', 0)} failed publish "
                  f"attempt series, last error: {st.get('last_error')}")
            return 1
        return 0
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
