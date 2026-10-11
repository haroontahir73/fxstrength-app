"""Offline tests for the Raspberry Pi COT checker (cot_pi_check.py).

    python pi/test_pi_check.py

A fake world stands in for the CFTC, GitHub and the published page; the clock is moved
by hand. Nothing touches the network, the real state directory or a real token.
"""
import fcntl, json, os, re, sys, tempfile
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cot_pi_check as pc                                  # noqa: E402

UTC = dt.timezone.utc
TOKEN = "ghp_TESTTOKEN1234567890abcdefXYZ0099"
PASS, FAIL = [], []


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


class World:
    """CFTC + GitHub + Pages, with a hand-moved clock."""

    def __init__(self, now, cftc="2026-10-06", page="2026-10-06"):
        self.now = now
        self.cftc = [cftc]                 # complete weeks, newest last
        self.partial = None                # (date, n) for a half-published week
        self.page = page
        self.runs, self.dispatches, self.calls = [], 0, []
        self.next_conclusion = "success"   # what the next run will end with
        self.publish_on_success = True
        self.echo_token = False            # make GitHub error bodies echo the token

    def http(self, method, url, headers, body=None):
        self.calls.append((method, url.split("?")[0], "Authorization" in headers))
        if "api.github.com" not in url and "Authorization" in headers:
            raise AssertionError(f"token sent to a non-GitHub host: {url}")
        if url.startswith(pc.ct.SOCRATA_LEGACY):
            rows = [{"report_date_as_yyyy_mm_dd": f"{d}T00:00:00.000", "n": "16"} for d in self.cftc]
            if self.partial:
                rows.append({"report_date_as_yyyy_mm_dd": f"{self.partial[0]}T00:00:00.000", "n": str(self.partial[1])})
            return 200, json.dumps(sorted(rows, key=lambda r: r["report_date_as_yyyy_mm_dd"], reverse=True))
        if url.startswith(pc.PAGE_URL):
            return 200, f'<html>... <section class="crsec" data-cot-report="{self.page}"> ...'
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return 401, '{"message": "Bad credentials"}'
        if self.echo_token:
            return 500, json.dumps({"message": f"boom {TOKEN} Bearer {TOKEN}"})
        if method == "POST" and url.endswith("/dispatches"):
            assert body == {"ref": "main", "inputs": {"cot_legacy_fetch": "true"}}, body
            self.dispatches += 1
            self.runs.append({"id": 1000 + len(self.runs), "status": "queued", "conclusion": None,
                              "created_at": (self.now + dt.timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "html_url": "https://github.com/x/y/actions/runs/1"})
            return 204, ""
        m = re.search(r"/actions/runs/(\d+)$", url)
        if m:
            r = next(r for r in self.runs if r["id"] == int(m.group(1)))
            return 200, json.dumps(r)
        if "/workflows/refresh.yml/runs" in url:
            since = re.search(r"created=%3E%3D([0-9T:%A-Z-]+)", url).group(1).replace("%3A", ":")
            return 200, json.dumps({"workflow_runs": [r for r in self.runs if r["created_at"] >= since]})
        if url.endswith("/workflows/refresh.yml"):
            return 200, '{"state": "active"}'
        return 404, '{"message": "Not Found"}'

    def finish_runs(self):
        for r in self.runs:
            if r["status"] != "completed":
                r["status"], r["conclusion"] = "completed", self.next_conclusion
                if self.next_conclusion == "success" and self.publish_on_success:
                    self.page = self.cftc[-1]

    def tick(self, minutes=15):
        self.now += dt.timedelta(minutes=minutes)


def setup(**kw):
    w = World(dt.datetime(2026, 10, 11, 12, 0, tzinfo=UTC), **kw)
    out = []
    ctx = pc.Ctx(tempfile.mkdtemp(prefix="fxpi-"), token=TOKEN, now=lambda: w.now,
                 http=w.http, out=out.append)
    return w, ctx, out


def run(ctx, **kw):
    try:
        return pc.step(ctx, **kw)
    except Exception as e:                                 # noqa: BLE001
        return f"RAISED {type(e).__name__}: {e}"


def state(ctx):
    return pc.load_state(ctx)


# ======================================================================================
def test_first_run_and_schedule():
    g = "first run + schedule"
    w, ctx, out = setup()
    r = run(ctx)
    check(g, "first run, page already current -> confirmed, no trigger",
          r.startswith("CONFIRMED") and w.dispatches == 0 and state(ctx)["confirmed"] == "2026-10-06", r)
    n = len(w.calls); w.tick(60)
    r = run(ctx)
    check(g, "before the next release: no CFTC query, no page fetch", len(w.calls) == n and "due" in r, r)
    w.now = dt.datetime(2026, 10, 12, 12, 30, tzinfo=UTC)
    run(ctx)
    check(g, "one sanity CFTC check a day", any("cftc.gov" in c[1] for c in w.calls[n:]))
    w.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC)      # Fri 15:45 ET, report not out
    n = len(w.calls)
    r = run(ctx); w.tick(); r2 = run(ctx)
    check(g, "once due: checks the CFTC every run; nothing new -> no trigger",
          sum("cftc.gov" in c[1] for c in w.calls[n:]) == 2 and w.dispatches == 0, (r, r2))
    w.now = dt.datetime(2026, 10, 18, 9, 0, tzinfo=UTC)        # holiday: > 1 day late
    run(ctx); n = len(w.calls); w.tick(15); run(ctx)
    check(g, "over a day late: back off to hourly", len(w.calls) == n)
    w.tick(50); run(ctx)
    check(g, "...and check again after the hour", len(w.calls) > n)


def test_trigger_once_and_confirm():
    g = "trigger once + confirm"
    w, ctx, out = setup()
    run(ctx)
    w.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC)
    w.cftc.append("2026-10-13")
    r = run(ctx)
    check(g, "new report at the CFTC, page old -> TRIGGERED", r.startswith("TRIGGERED") and w.dispatches == 1, r)
    for _ in range(3):
        w.tick(); r = run(ctx)
    check(g, "while the run is going: no second trigger", w.dispatches == 1 and "is queued" in r, r)
    check(g, "the run was found and recorded", state(ctx)["trigger"]["run_id"] == 1000)
    w.finish_runs(); w.tick(); r = run(ctx)
    check(g, "run succeeded and the page shows the week -> CONFIRMED",
          r.startswith("CONFIRMED") and "run 1000" in r and state(ctx)["confirmed"] == "2026-10-13", r)
    for _ in range(4):
        w.tick(); run(ctx)
    check(g, "after confirmation: never triggered again for that report", w.dispatches == 1)


def test_retries():
    g = "failure + retries"
    w, ctx, out = setup()
    run(ctx)
    w.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC)
    w.cftc.append("2026-10-13")
    w.next_conclusion = "failure"
    run(ctx); w.tick(5); run(ctx); w.finish_runs(); w.tick(5)
    r = run(ctx)
    check(g, "a failed run is reported with a retry time, not re-triggered at once",
          "failed" in r and w.dispatches == 1, r)
    w.tick(5); run(ctx)
    check(g, "no retry before the back-off (15 min)", w.dispatches == 1)
    w.tick(15); r = run(ctx)
    check(g, "retry after the back-off: attempt 2", w.dispatches == 2 and "attempt 2" in r, r)
    waits = []
    for attempt in (2, 3, 4, 5):
        run(ctx); w.tick(1); run(ctx); w.finish_runs(); w.tick(1)
        run(ctx)
        tr = state(ctx)["trigger"]
        waits.append(round((pc.ts(tr["retry_at"]) - w.now).total_seconds() / 60))
        w.now = pc.ts(tr["retry_at"]) + dt.timedelta(minutes=1)
        run(ctx)
    check(g, "back-off grows 30, 60, then every 6 h", waits == [30, 60, 360, 360], waits)
    check(g, "exactly one trigger per attempt", w.dispatches == 6, w.dispatches)
    w.next_conclusion = "success"
    run(ctx); w.tick(1); run(ctx); w.finish_runs(); w.tick(1)
    r = run(ctx)
    check(g, "eventually confirmed when a run succeeds", r.startswith("CONFIRMED"), r)


def test_success_but_page_stale():
    g = "success but page stale"
    w, ctx, out = setup()
    run(ctx)
    w.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC)
    w.cftc.append("2026-10-13")
    w.publish_on_success = False
    run(ctx); w.tick(5); run(ctx); w.finish_runs(); w.tick(5)
    r = run(ctx)
    check(g, "run succeeded: waits for the page (Pages lag)", "waiting for the page" in r, r)
    w.tick(10); r = run(ctx)
    check(g, "still within the grace period: no retry", w.dispatches == 1 and "waiting for the page" in r, r)
    w.tick(15); r = run(ctx)
    check(g, "page still old after the grace -> counted as a failed attempt",
          "page still shows 2026-10-06" in r and w.dispatches == 1, r)
    w.page = "2026-10-13"; w.tick(20); r = run(ctx)
    check(g, "if a later refresh publishes it, it is confirmed without another trigger",
          r.startswith("CONFIRMED") and w.dispatches == 1, r)


def test_no_trigger_when_already_published():
    g = "already published"
    w, ctx, out = setup()
    run(ctx)
    w.now = dt.datetime(2026, 10, 16, 20, 30, tzinfo=UTC)
    w.cftc.append("2026-10-13"); w.page = "2026-10-13"
    r = run(ctx)
    check(g, "a scheduled refresh got there first -> confirmed, no trigger",
          r.startswith("CONFIRMED") and "scheduled refresh" in r and w.dispatches == 0, r)


def test_downtime_and_partial():
    g = "downtime + partial week"
    w, ctx, out = setup(cftc="2026-09-22", page="2026-09-22")
    run(ctx)
    w.now = dt.datetime(2026, 10, 19, 8, 0, tzinfo=UTC)         # Pi was off for 3 weeks
    w.cftc += ["2026-09-29", "2026-10-06", "2026-10-13"]
    r = run(ctx)
    check(g, "after downtime: ONE trigger for the newest week (the build backfills the rest)",
          w.dispatches == 1 and "2026-10-13" in r, r)
    w.finish_runs(); w.tick(); run(ctx); w.tick(); r = run(ctx)
    check(g, "...confirmed", state(ctx)["confirmed"] == "2026-10-13", r)
    w2, ctx2, _ = setup()
    run(ctx2)
    w2.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC)
    w2.partial = ("2026-10-13", 8)
    r = run(ctx2)
    check(g, "a half-published week (8 of 16) is not treated as new", w2.dispatches == 0 and "up to date" in r, r)
    w3, ctx3, _ = setup()
    run(ctx3)
    w3.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC)
    w3.cftc.append("2026-10-13"); w3.publish_on_success = False
    run(ctx3); w3.now = dt.datetime(2026, 10, 23, 19, 45, tzinfo=UTC); w3.cftc.append("2026-10-20")
    r = run(ctx3)
    check(g, "a newer report replaces a pending one (one trigger for the newest)",
          state(ctx3)["trigger"]["week"] == "2026-10-20" and w3.dispatches == 2, r)
    w4, ctx4, _ = setup()
    run(ctx4)
    w4.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC); w4.cftc.append("2026-10-13")
    run(ctx4); w4.runs.clear(); w4.tick(16); r = run(ctx4)
    check(g, "a triggered run that never appears counts as a failed attempt",
          "never appeared" in r and w4.dispatches == 1, r)


def test_dry_run_and_lock():
    g = "dry run + lock"
    w, ctx, out = setup()
    run(ctx)
    w.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC); w.cftc.append("2026-10-13")
    r = run(ctx, dry_run=True)
    check(g, "dry run never triggers", r.startswith("DRY RUN") and w.dispatches == 0, r)
    sd = tempfile.mkdtemp(prefix="fxpi-lock-")
    tok = Path(sd) / "tok"; tok.write_text(TOKEN); os.chmod(tok, 0o600)
    calls = []
    real = pc._http
    pc._http = lambda *a, **k: calls.append(a) or (200, "[]")
    lock = open(Path(sd) / "lock", "w"); fcntl.flock(lock, fcntl.LOCK_EX)
    import io, contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = pc.main(["--state-dir", sd, "--token-file", str(tok)])
    finally:
        pc._http = real
        lock.close()
    check(g, "a second check while one runs exits at once and does nothing",
          rc == 0 and not calls and "still running" in buf.getvalue(), buf.getvalue())


def test_token_hygiene():
    g = "token hygiene"
    w, ctx, out = setup()
    run(ctx)
    w.now = dt.datetime(2026, 10, 16, 19, 45, tzinfo=UTC); w.cftc.append("2026-10-13")
    w.echo_token = True
    r = run(ctx)
    blob = "\n".join(out) + r + (ctx.state_dir / "state.json").read_text()
    check(g, "an error that echoes the token: the token never reaches logs or state",
          TOKEN not in blob and "RAISED" in r, blob[-300:])
    check(g, "the token is only sent to api.github.com",
          all(("api.github.com" in u) == auth for _, u, auth in w.calls if auth))
    check(g, "scrubber hides the exact token even if it is not token-shaped",
          pc.scrub("a plainSecret99 b", "plainSecret99") == "a *** b")
    check(g, "scrubber also hides token-shaped strings",
          pc.scrub("x github_pat_11ABCDEFG0123456789abcdefgh y ghp_abcdefghijklmnopqrstuv1234") == "x *** y ***")
    d = Path(tempfile.mkdtemp(prefix="fxpi-tok-")); f = d / "github_token"
    f.write_text(TOKEN); os.chmod(f, 0o644)
    import io, contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = pc.main(["--state-dir", str(d), "--token-file", str(f)])
    check(g, "a token file readable by others is refused, and not printed",
          rc == 2 and "chmod 600" in err.getvalue() and TOKEN not in err.getvalue(), err.getvalue())
    os.chmod(f, 0o600)
    check(g, "a private token file is accepted", pc.read_token(f) == TOKEN)


if __name__ == "__main__":
    for fn in (test_first_run_and_schedule, test_trigger_once_and_confirm, test_retries,
               test_success_but_page_stale, test_no_trigger_when_already_published,
               test_downtime_and_partial, test_dry_run_and_lock, test_token_hygiene):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    groups = {}
    for g, _ in PASS:
        groups.setdefault(g, [0, 0])[0] += 1
    for g, _ in FAIL:
        groups.setdefault(g, [0, 0])[1] += 1
    print()
    for g, (ok, bad) in groups.items():
        print(f"{g}: {ok}/{ok + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
