"""The Pages rebuild clock (nudge_refresh.py). Offline: every GitHub call is fake.

A wrong decision here either leaves the dashboard stale for hours or stacks
refresh runs on top of each other. refresh.yml cancels a pending run when
another one joins its concurrency group, so dispatching while one is queued
would drop the Pi's COT trigger.

    python test_nudge_refresh.py
"""
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import nudge_refresh as nr                                        # noqa: E402

PASS, FAIL = [], []
NOW = dt.datetime(2026, 11, 3, 8, 30, tzinfo=dt.timezone.utc)


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def _stamp(minutes_ago):
    return (NOW - dt.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(rid, status="completed", minutes_ago=30, branch="main", **over):
    when = _stamp(minutes_ago)
    row = {"id": rid, "status": status, "head_branch": branch,
           "created_at": when, "run_started_at": when, "updated_at": when}
    row.update(over)
    return row


# --------------------------------------------------------------------------------------
def test_when_to_dispatch():
    g = "when to dispatch"

    def decision(runs):
        return nr.plan(runs, NOW)

    action, _ = decision([])
    check(g, "nothing has ever run -> dispatch", action == "dispatch")

    action, _ = decision([run(1, minutes_ago=40)])
    check(g, "a refresh that finished well outside the gap -> dispatch", action == "dispatch")

    action, reason = decision([run(2, minutes_ago=10)])
    check(g, "a refresh that finished inside the gap -> skip",
          action == "skip" and "10 min" in reason, reason)

    action, _ = decision([run(3, minutes_ago=18)])
    check(g, "exactly the gap is due again", action == "dispatch")

    action, _ = decision([run(4, minutes_ago=17)])
    check(g, "one minute inside the gap -> skip", action == "skip")

    for status in ("in_progress", "queued", "pending", "waiting",
                   "requested", "action_required", "blocked"):
        action, reason = decision([run(5, status=status, minutes_ago=600)])
        check(g, f"{status} blocks a dispatch even if the timestamps are old",
              action == "skip" and status in reason, reason)

    action, _ = decision([run(6, status="", minutes_ago=600)])
    check(g, "a blank status is not treated as a stuck run", action == "dispatch")

    # Scheduled runs are created hours before the job starts. run_started_at on
    # this repo has been observed equal to created_at even when the job began
    # much later; updated_at is when the run actually finished.
    action, reason = decision([run(
        7, minutes_ago=360,
        updated_at=_stamp(2), created_at=_stamp(360), run_started_at=_stamp(360))])
    check(g, "finished two minutes ago even if it was created six hours ago -> skip",
          action == "skip", reason)

    action, _ = decision([run(
        8, minutes_ago=360,
        updated_at=_stamp(40), created_at=_stamp(360), run_started_at=_stamp(360))])
    check(g, "created six hours ago but finished outside the gap -> dispatch",
          action == "dispatch")

    action, reason = decision([run(9, status="in_progress", branch="experiment", minutes_ago=1)])
    check(g, "an in-progress run on another branch still blocks (shared concurrency group)",
          action == "skip" and "in_progress" in reason, reason)

    action, _ = decision([run(10, branch="experiment", minutes_ago=1)])
    check(g, "a finished branch run did not publish main, so it does not reset the gap",
          action == "dispatch")

    action, _ = decision([
        run(11, branch="experiment", minutes_ago=1),
        run(12, minutes_ago=40),
    ])
    check(g, "main's own finish time is what the gap uses", action == "dispatch")

    action, _ = decision([run(13, minutes_ago=40), run(14, status="queued", minutes_ago=1)])
    check(g, "a queued run hides behind an older success and still blocks", action == "skip")

    action, _ = decision([run(15, minutes_ago=40), "nope", None, 3])
    check(g, "junk entries are ignored", action == "dispatch")

    ahead = (NOW + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    action, _ = decision([run(16, updated_at=ahead)])
    check(g, "a timestamp in the future is not 'old'", action == "skip")

    naive = _stamp(40).replace("Z", "")
    action, _ = decision([run(17, updated_at=naive, created_at=naive, run_started_at=naive)])
    check(g, "a timezone-naive timestamp is UTC", action == "dispatch")

    action, reason = decision([run(18, minutes_ago=40, updated_at="yesterday",
                                  created_at="yesterday", run_started_at="yesterday")])
    check(g, "a finished run with no readable time is not assumed to be old",
          action == "skip" and "no timestamp" in reason, reason)

    action, _ = decision([{"status": "completed", "head_branch": "main"}])
    check(g, "missing timestamp fields are the same as unreadable ones", action == "skip")


def test_what_gets_sent():
    g = "what gets sent"
    body = nr.dispatch_body()
    check(g, "dispatch body is ref=main and nothing else",
          body == {"ref": "main"}, str(body))
    check(g, "cot_legacy_fetch is not sent (the Pi is the only caller that sets it)",
          "cot_legacy_fetch" not in body and "inputs" not in body)
    check(g, "mode is not sent, so the workflow's own auto/daily/cot choice stands",
          "mode" not in json.dumps(body))

    refresh = nr._workflow_url("haroontahir73/fxstrength-app", nr.REFRESH_WORKFLOW, "dispatches")
    nudge = nr._workflow_url("haroontahir73/fxstrength-app", nr.NUDGE_WORKFLOW, "dispatches")
    check(g, "a due check posts to refresh.yml", refresh.endswith("/workflows/refresh.yml/dispatches"))
    check(g, "arming posts to the waiter, not to refresh.yml",
          nudge.endswith("/workflows/refresh-nudge.yml/dispatches") and "refresh.yml" not in nudge)
    check(g, "owner and repo are one path segment each",
          "/repos/haroontahir73/fxstrength-app/" in refresh)

    check(g, "the waiter sleeps longer than the gap, with a minute of slack",
          nr.WAIT_SEC >= nr.MIN_GAP.total_seconds() + 60,
          f"WAIT_SEC={nr.WAIT_SEC} MIN_GAP={nr.MIN_GAP.total_seconds()}")
    check(g, "the gap is inside 15-30 min",
          15 * 60 <= nr.MIN_GAP.total_seconds() <= 30 * 60)
    check(g, "the wait is inside 15-30 min",
          15 * 60 <= nr.WAIT_SEC <= 30 * 60)


class _Http:
    def __init__(self, gets=None, post_status=204, post_body=""):
        self.gets = list(gets if gets is not None else [{"workflow_runs": []}])
        self.post_status = post_status
        self.post_body = post_body
        self.calls = []

    def __call__(self, method, url, token, body):
        self.calls.append((method, url, token, body))
        if method == "GET":
            payload = self.gets.pop(0) if self.gets else {"workflow_runs": []}
            if isinstance(payload, tuple):
                return payload
            return 200, json.dumps(payload)
        return self.post_status, self.post_body


def test_api_calls():
    g = "api calls"
    repo = "haroontahir73/fxstrength-app"

    http = _Http()
    kind, _ = nr.execute(http, repo, "sekret", NOW)
    posts = [c for c in http.calls if c[0] == "POST"]
    check(g, "no runs -> one GET and one POST", kind == "dispatch" and len(posts) == 1
          and len(http.calls) == 2)
    check(g, "the POST body is the bare ref", posts[0][3] == {"ref": "main"})
    check(g, "the POST goes to refresh.yml", posts[0][1].endswith("/refresh.yml/dispatches"))

    http = _Http(gets=[{"workflow_runs": [run(1, minutes_ago=5)]}])
    kind, _ = nr.execute(http, repo, "sekret", NOW)
    check(g, "a fresh run -> no POST",
          kind == "skip" and not any(c[0] == "POST" for c in http.calls))

    http = _Http(gets=[(500, '{"message":"nope sekret"}')])
    kind, msg = nr.execute(http, repo, "sekret", NOW)
    check(g, "a failed list is an error and does not dispatch",
          kind == "error" and not any(c[0] == "POST" for c in http.calls), msg)
    check(g, "the token is scrubbed from a list error", "sekret" not in msg, msg)

    http = _Http(gets=[(200, "not-json")])
    kind, _ = nr.execute(http, repo, "sekret", NOW)
    check(g, "a non-json list is an error and does not dispatch",
          kind == "error" and not any(c[0] == "POST" for c in http.calls))

    http = _Http(gets=[(200, json.dumps({"workflow_runs": "nope"}))])
    kind, _ = nr.execute(http, repo, "sekret", NOW)
    check(g, "a weird run list is an error and does not dispatch", kind == "error")

    http = _Http(post_status=403, post_body='{"message":"refused sekret"}')
    kind, msg = nr.execute(http, repo, "sekret", NOW)
    check(g, "a refused dispatch is an error", kind == "error" and "403" in msg, msg)
    check(g, "the token is scrubbed from a dispatch error", "sekret" not in msg, msg)

    http = _Http(post_status=204)
    kind, msg = nr.arm(http, repo, "sekret")
    check(g, "arming posts once to the waiter",
          kind == "armed" and len(http.calls) == 1
          and http.calls[0][1].endswith("/refresh-nudge.yml/dispatches")
          and http.calls[0][3] == {"ref": "main"}, msg)

    http = _Http(post_status=401, post_body="token=sekret")
    kind, msg = nr.arm(http, repo, "sekret")
    check(g, "a refused arm is an error with the token scrubbed",
          kind == "error" and "sekret" not in msg, msg)


def test_cli():
    g = "cli"
    saved = dict(os.environ)
    real_sleep, real_http = nr.time.sleep, nr.github_http
    try:
        os.environ["GITHUB_REPOSITORY"] = "haroontahir73/fxstrength-app"
        os.environ["GH_TOKEN"] = "sekret"
        slept = []
        nr.time.sleep = slept.append

        def _quiet(argv):
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            try:
                return nr.main(argv), buf.getvalue()
            finally:
                sys.stdout = old

        http = _Http()
        nr.github_http = http
        code, _ = _quiet(["--arm"])
        check(g, "--arm does not sleep", code == 0 and slept == [], str(slept))
        check(g, "--arm hits the waiter only",
              len(http.calls) == 1 and http.calls[0][1].endswith("/refresh-nudge.yml/dispatches"))

        http = _Http()
        nr.github_http = http
        code, _ = _quiet(["--wait"])
        check(g, "--wait sleeps for WAIT_SEC then dispatches refresh",
              code == 0 and slept == [nr.WAIT_SEC]
              and any(c[1].endswith("/refresh.yml/dispatches") for c in http.calls),
              f"slept={slept} calls={[c[1] for c in http.calls]}")

        http = _Http(gets=[{"workflow_runs": [run(1, minutes_ago=1)]}])
        nr.github_http = http
        code, out = _quiet([])
        check(g, "a fresh run exits 10 and does not POST",
              code == nr.EXIT_SKIP and not any(c[0] == "POST" for c in http.calls),
              f"code={code}")
        check(g, "the skip reason is printed", "nudge:" in out)

        os.environ.pop("GH_TOKEN", None)
        os.environ.pop("GITHUB_TOKEN", None)
        code, _ = _quiet([])
        check(g, "no token exits 1 and does not call GitHub", code == nr.EXIT_ERROR)

        os.environ["GH_TOKEN"] = "sekret"
        os.environ["GITHUB_REPOSITORY"] = "not a repo"
        code, _ = _quiet(["--arm"])
        check(g, "a malformed repository exits 1", code == nr.EXIT_ERROR)
    finally:
        os.environ.clear()
        os.environ.update(saved)
        nr.time.sleep = real_sleep
        nr.github_http = real_http


if __name__ == "__main__":
    for fn in (test_when_to_dispatch, test_what_gets_sent, test_api_calls, test_cli):
        try:
            fn()
        except Exception as e:                                     # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised", f"{type(e).__name__}: {e}"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()

    groups = {}
    for grp, _, _ in PASS:
        groups.setdefault(grp, [0, 0])[0] += 1
    for grp, _, _ in FAIL:
        groups.setdefault(grp, [0, 0])[1] += 1
    print()
    for grp, (ok, bad) in groups.items():
        print(f"{grp}: {ok}/{ok + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
