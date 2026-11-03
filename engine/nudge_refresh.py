"""Keep the GitHub Pages dashboard rebuilding when the scheduler does not.

refresh.yml asks for a cron of every 15 minutes. On this free public repo GitHub
does not honour it: the scheduled runs that did fire all succeeded, and they were
hours apart (2 Nov 2026 17:19 and 23:58 UTC, then 3 Nov 06:28). Pushes made with
the workflow token do not start it either, which is why the news-watch feed
commits never rebuild the page. Until 2 Nov the page stayed fresh only because
sync_cloud.py on the owner's PC pushed a pulse commit whenever it looked stale.

Two clocks that do keep time call this module. Neither needs a new secret.

  refresh.yml, after a deploy on main, arms refresh-nudge.yml
  (--arm). That workflow sleeps WAIT_SEC and then dispatches refresh.yml if it
  is still due. The sleeper is not in the refresh-dashboard concurrency group,
  so the wait cannot block a push or the Pi's COT trigger. A newer deploy
  cancels the sleeper and starts a fresh wait, so rebuilds do not pile up.

  news-watch.yml, on each pass of its loop, dispatches refresh.yml directly
  when the chain has gone quiet. The watcher itself has gaps (a schedule that
  never lands during a 5.5h loop leaves the next one unqueued), so it is the
  backstop, not the clock.

A dispatch is refused while any refresh run is still queued, pending or
running. refresh.yml uses cancel-in-progress: false, and GitHub cancels the
one pending run in a group when a second one arrives — a careless dispatch
would drop the Pi's queued COT build. The dispatch body is only {"ref": "main"}.
Defaults leave cot_legacy_fetch off; the Pi remains the only caller that sets it.

workflow_dispatch is the one event a GITHUB_TOKEN is allowed to start.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

REFRESH_WORKFLOW = "refresh.yml"
NUDGE_WORKFLOW = "refresh-nudge.yml"
REF = "main"

# A rebuild that finished more recently than this is still fresh. 18 min sits
# inside the 15-30 min target. WAIT_SEC is longer on purpose: the waiter is
# armed when a run finishes, and it must wake up AFTER this gap or it will
# skip and the chain will stop until the cron bothers to fire.
MIN_GAP = dt.timedelta(minutes=18)
WAIT_SEC = 20 * 60

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SKIP = 10

_API = "https://api.github.com"


def _parse(raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when


def _activity(run):
    """When this run last mattered. Completion (updated_at) wins over the time
    GitHub created a scheduled run, which can be hours before it actually starts."""
    times = [_parse(run.get(key)) for key in ("updated_at", "run_started_at", "created_at")]
    times = [t for t in times if t is not None]
    return max(times) if times else None


def _busy(run):
    status = str(run.get("status") or "").strip().lower()
    if not status:
        return False
    return status != "completed"


def _on_main(run):
    branch = run.get("head_branch")
    return branch in (None, "", REF)


def plan(runs, now):
    """('dispatch', reason) or ('skip', reason). `runs` is the refresh.yml run list."""
    runs = [r for r in runs if isinstance(r, dict)]
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    for run in runs:
        if _busy(run):
            state = run.get("status") or "not finished"
            return "skip", f"refresh run {run.get('id')} is {state}"
    undated = False
    undated_id = None
    latest = None
    latest_id = None
    for run in runs:
        if not _on_main(run) or _busy(run):
            continue
        when = _activity(run)
        if when is None:
            undated = True
            undated_id = run.get("id")
            continue
        if latest is None or when > latest:
            latest, latest_id = when, run.get("id")
    if latest is not None and now - latest < MIN_GAP:
        age = (now - latest).total_seconds() / 60
        return "skip", f"last refresh was {age:.0f} min ago (run {latest_id})"
    if undated:
        return "skip", f"refresh run {undated_id} has no timestamp"
    return "dispatch", "no refresh is queued or running, and none finished within the gap"


def dispatch_body():
    """No inputs. The workflow defaults leave mode=auto and cot_legacy_fetch false."""
    return {"ref": REF}


def _workflow_url(repo, workflow, suffix):
    owner, name = repo.split("/", 1)
    safe = quote(owner, safe="") + "/" + quote(name, safe="")
    return f"{_API}/repos/{safe}/actions/workflows/{quote(workflow, safe='')}/{suffix}"


def _scrub(text, token):
    text = str(text or "")
    if token:
        text = text.replace(token, "***")
    try:
        msg = json.loads(text).get("message")
        if isinstance(msg, str) and msg:
            text = msg
            if token:
                text = text.replace(token, "***")
    except ValueError:
        pass
    return text.replace("\n", " ")[:200]


def _repo():
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if repo.count("/") != 1 or not all(repo.split("/")) or any(c in repo for c in " \t"):
        return None
    return repo


def _token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def execute(http, repo, token, now):
    """List refresh.yml runs and dispatch one when it is due.

    Returns ('dispatch'|'skip'|'error', message). Does not POST on skip or error.
    """
    status, text = http("GET", _workflow_url(repo, REFRESH_WORKFLOW, "runs?per_page=50"),
                        token, None)
    if status != 200:
        return "error", f"could not list refresh runs (HTTP {status}: {_scrub(text, token)})"
    try:
        payload = json.loads(text or "{}")
    except ValueError:
        return "error", "refresh run list was not json"
    if not isinstance(payload, dict):
        return "error", "refresh run list was not an object"
    runs = payload.get("workflow_runs")
    if runs is None:
        runs = []
    if not isinstance(runs, list):
        return "error", "refresh run list has no workflow_runs"
    action, reason = plan(runs, now)
    if action != "dispatch":
        return action, reason
    posted, body = http("POST", _workflow_url(repo, REFRESH_WORKFLOW, "dispatches"),
                        token, dispatch_body())
    if posted not in (200, 204):
        return "error", f"refresh dispatch refused (HTTP {posted}: {_scrub(body, token)})"
    return "dispatch", reason


def arm(http, repo, token):
    """Start (or restart) the waiter. Returns ('armed'|'error', message)."""
    posted, body = http("POST", _workflow_url(repo, NUDGE_WORKFLOW, "dispatches"),
                        token, dispatch_body())
    if posted not in (200, 204):
        return "error", f"could not arm the waiter (HTTP {posted}: {_scrub(body, token)})"
    return "armed", f"armed {NUDGE_WORKFLOW} on {REF}"


def github_http(method, url, token, body):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fxstrength-nudge",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read() if getattr(e, "fp", None) is not None else b""
        return e.code, raw.decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", action="store_true",
                    help="dispatch the waiter workflow instead of refresh.yml")
    ap.add_argument("--wait", action="store_true",
                    help="sleep WAIT_SEC first (the waiter workflow)")
    args = ap.parse_args(argv)
    token, repo = _token(), _repo()
    if not token or not repo:
        print("nudge: GH_TOKEN (or GITHUB_TOKEN) and GITHUB_REPOSITORY are required",
              flush=True)
        return EXIT_ERROR
    if args.wait and not args.arm:
        print(f"nudge: waiting {WAIT_SEC // 60} min before checking {REFRESH_WORKFLOW}",
              flush=True)
        time.sleep(WAIT_SEC)
    if args.arm:
        kind, msg = arm(github_http, repo, token)
    else:
        kind, msg = execute(github_http, repo, token, dt.datetime.now(dt.timezone.utc))
    line = f"nudge: {msg}"
    print(f"::warning::{line}" if kind == "error" else line, flush=True)
    if kind == "error":
        return EXIT_ERROR
    if kind == "skip":
        return EXIT_SKIP
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
