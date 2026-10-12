#!/usr/bin/env python3
"""FX Strength Desk - Raspberry Pi COT checker.

THE PI'S ONE JOB
----------------
Notice when the CFTC has published a new weekly Commitments of Traders report, ask GitHub
to run the tested refresh build (refresh.yml) once, and confirm that build succeeded AND
that the published dashboard now shows the new week. GitHub fetches the data, runs the
test gate, builds and publishes the page; the Pi never builds, commits or publishes.

EVERY RUN (a systemd timer starts it every 15 minutes; see pi/README.md)
  1. Takes a lock, so two checks never overlap (a second one exits at once).
  2. Asks the CFTC for the newest COMPLETE report week (all, or all but one, of the 16
     contracts present) - only when one is due: from Friday 15:30 US Eastern after the
     last report, every run until it appears (hourly once a day late: holidays, shutdowns),
     plus one sanity check a day. A tiny query; nothing is downloaded or stored.
  3. If that week is newer than the last week CONFIRMED live: looks at the published page
     first (a scheduled refresh may already have it); if not there, triggers refresh.yml
     once (workflow_dispatch, cot_legacy_fetch=true).
  4. Follows that run to completion and then checks the page shows the week. Only then is
     the week CONFIRMED. A failed, cancelled or vanished run, or a success whose page still
     shows the old week, is retried with back-off (15, 30, 60 min, then every 6 h) - never
     more than once per attempt, so the same report is not triggered over and over.
After downtime nothing special is needed: the first run compares the CFTC's newest week
with the last confirmed one and triggers ONE build, which backfills every missed week
(cot_table.py widens its fetch to cover the gap).

SECRETS
-------
The GitHub token is read from a file (systemd LoadCredential, or --token-file) and sent
only in the Authorization header to api.github.com. It is never printed, logged, stored
in the state file or put in a URL; anything this script prints passes through a scrubber
as well. The token file must not be readable by group/others, or the script refuses.

    cot_pi_check.py                 one check (what the timer runs)
    cot_pi_check.py --dry-run       the same, but never triggers - says what it would do
    cot_pi_check.py --status        print the saved state (no secrets)
    cot_pi_check.py --check-setup   token works / CFTC + page reachable / clock / schedule
Stdlib only. Needs the repo checkout it lives in (it reads engine/cot_table.py for the
release schedule and the 16 contract codes, so the two can never disagree).
"""
import argparse, fcntl, json, os, re, stat, sys, time, urllib.error, urllib.parse, urllib.request
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "engine"))
import cot_table as ct                                     # noqa: E402  schedule + codes only

REPO = os.environ.get("FXCOT_REPO", "haroontahir73/fxstrength-app")
WORKFLOW = "refresh.yml"
REF = "main"
PAGE_URL = os.environ.get("FXCOT_PAGE", "https://haroontahir73.github.io/fxstrength-app/dashboard.html")
API = "https://api.github.com"
UA = "fxstrength-pi-cot-checker/1"

LATE_POLL = dt.timedelta(hours=1)        # a report over a day late: check hourly
SANITY = dt.timedelta(hours=24)          # otherwise at least one CFTC check a day
RUN_FIND_GRACE = dt.timedelta(minutes=15)   # a dispatched run must show up within this
RUN_STUCK = dt.timedelta(minutes=90)        # a run still going after this is reported
PAGE_GRACE = dt.timedelta(minutes=20)       # Pages can lag a successful run a little
BACKOFF = [dt.timedelta(minutes=15), dt.timedelta(minutes=30), dt.timedelta(minutes=60)]
SLOW_RETRY = dt.timedelta(hours=6)          # after the quick retries are used up

UTC = dt.timezone.utc


# ------------------------------------------------------------------ plumbing
class Ctx:
    """Everything with a side effect, so the tests can drive it."""

    def __init__(self, state_dir, token=None, now=None, http=None, out=None):
        self.state_dir = Path(state_dir)
        self.token = token
        self._now = now
        self._http = http or _http
        self._out = out or (lambda s: print(s, flush=True))

    def now(self):
        return self._now() if self._now else dt.datetime.now(UTC)

    def log(self, msg):
        self._out(scrub(str(msg), self.token))

    def http(self, method, url, body=None, auth=False):
        headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
        if auth:
            if not self.token:
                raise RuntimeError("no GitHub token available")
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return self._http(method, url, headers, body)


def _http(method, url, headers, body=None):
    """(status, text). Never raises for an HTTP status; raises only for network errors."""
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers = dict(headers, **{"Content-Type": "application/json"})
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]


_TOKEN_SHAPES = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+\S+)")


def scrub(text, token=None):
    """Remove the token (and anything token-shaped) from text that is about to be shown."""
    if token:
        text = text.replace(token, "***")
    return _TOKEN_SHAPES.sub("***", text)


def read_token(path):
    p = Path(path)
    st = p.stat()
    cred = os.environ.get("CREDENTIALS_DIRECTORY")
    managed = bool(cred) and p.parent == Path(cred)      # systemd LoadCredential: its own perms
    if not managed and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{p} is readable by group/others - run: chmod 600 {p}")
    tok = p.read_text(encoding="utf-8").strip()
    if not tok or any(c.isspace() for c in tok):
        raise ValueError(f"{p} does not contain a single token")
    return tok


def token_path(arg):
    if arg:
        return Path(arg)
    cred = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred and (Path(cred) / "github_token").exists():
        return Path(cred) / "github_token"
    return Path.home() / ".config" / "fxstrength-cot" / "github_token"


def iso(t):
    return t.isoformat() if t else None


def ts(s):
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def load_state(ctx):
    try:
        return json.loads((ctx.state_dir / "state.json").read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}


def save_state(ctx, state):
    ctx.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = ctx.state_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(ctx.state_dir / "state.json")          # atomic: a power cut leaves old or new


# ------------------------------------------------------------------ the three sources
def cftc_latest(ctx):
    """Newest report date with (nearly) all 16 contracts published, futures-only."""
    since = (ctx.now().date() - dt.timedelta(days=42)).isoformat()
    params = {
        "$select": "report_date_as_yyyy_mm_dd, count(cftc_contract_market_code) as n",
        "$where": (f"cftc_contract_market_code in({','.join(repr(c) for c in ct.ALL_CODES)}) "
                   f"AND report_date_as_yyyy_mm_dd>='{since}T00:00:00' "
                   f"AND futonly_or_combined='FutOnly'"),
        "$group": "report_date_as_yyyy_mm_dd",
        "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "8"}
    status, text = ctx.http("GET", f"{ct.SOCRATA_LEGACY}?{urllib.parse.urlencode(params)}")
    if status != 200:
        raise RuntimeError(f"CFTC answered HTTP {status}")
    need = len(ct.ALL_CODES) - 1
    for row in json.loads(text):
        if int(float(row.get("n", 0))) >= need:
            return str(row["report_date_as_yyyy_mm_dd"])[:10]
    raise RuntimeError("no complete report week in the last six weeks")


def page_week(ctx):
    """The report week the PUBLISHED dashboard shows (its data-cot-report marker), or None."""
    status, text = ctx.http("GET", f"{PAGE_URL}?pi={int(time.time())}")
    if status != 200:
        raise RuntimeError(f"dashboard answered HTTP {status}")
    m = re.search(r'data-cot-report="(\d{4}-\d{2}-\d{2})"', text)
    return m.group(1) if m else None


def dispatch(ctx):
    status, text = ctx.http(
        "POST", f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        {"ref": REF, "inputs": {"cot_legacy_fetch": "true"}}, auth=True)
    if status not in (200, 204):
        raise RuntimeError(f"GitHub refused the trigger: HTTP {status} {_gh_msg(text, ctx.token)}")


def find_run(ctx, after):
    """The refresh run our dispatch created: the oldest workflow_dispatch run of refresh.yml
    on main created at/after `after` (less two minutes of clock slack)."""
    since = (after - dt.timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.urlencode({"event": "workflow_dispatch", "branch": REF,
                                "created": f">={since}", "per_page": "20"})
    status, text = ctx.http("GET", f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?{q}", auth=True)
    if status != 200:
        raise RuntimeError(f"GitHub run list: HTTP {status} {_gh_msg(text, ctx.token)}")
    runs = sorted(json.loads(text).get("workflow_runs", []), key=lambda r: r.get("created_at", ""))
    return runs[0] if runs else None


def get_run(ctx, run_id):
    status, text = ctx.http("GET", f"{API}/repos/{REPO}/actions/runs/{int(run_id)}", auth=True)
    if status != 200:
        raise RuntimeError(f"GitHub run {run_id}: HTTP {status} {_gh_msg(text, ctx.token)}")
    return json.loads(text)


def _gh_msg(text, token=None):
    """GitHub's error message, scrubbed - an error body must never carry the token out."""
    try:
        return scrub(str(json.loads(text).get("message", ""))[:160], token)
    except Exception:                                   # noqa: BLE001
        return ""


# ------------------------------------------------------------------ the decision
def cftc_due(state, now):
    """Should this run ask the CFTC? (see 2. above)"""
    last = ts(state.get("last_cftc_check"))
    if not state.get("cftc_latest") or last is None:
        return True, "first check"
    nxt = ct.next_report_date(dt.date.fromisoformat(state["cftc_latest"]))
    due = ct.release_at(nxt)
    if now >= due:
        if now - due > dt.timedelta(hours=24) and now - last < LATE_POLL:
            return False, f"report for {nxt} is over a day late - next check within the hour"
        return True, f"report for {nxt} due since {due:%a %d %b %H:%M} UTC"
    if now - last >= SANITY:
        return True, "daily sanity check"
    return False, f"next report ({nxt}) due {due:%a %d %b %H:%M} UTC"


def step(ctx, dry_run=False):
    """One check. Returns a short outcome string; state is saved before returning."""
    now = ctx.now()
    state = load_state(ctx)
    state["last_run"] = iso(now)
    try:
        ask, why = cftc_due(state, now)
        if ask:
            latest = cftc_latest(ctx)
            state["last_cftc_check"] = iso(now)
            if latest != state.get("cftc_latest"):
                ctx.log(f"CFTC newest complete report: {latest} (was {state.get('cftc_latest')}) [{why}]")
            state["cftc_latest"] = latest
        latest, confirmed = state.get("cftc_latest"), state.get("confirmed")
        if not latest or (confirmed and confirmed >= latest):
            state.pop("trigger", None)
            outcome = f"up to date: {confirmed} is live" + ("" if ask else f" ({why})")
            return _done(ctx, state, outcome)
        return _done(ctx, state, _publish(ctx, state, latest, now, dry_run))
    except Exception as e:                              # noqa: BLE001
        state["last_error"] = scrub(f"{type(e).__name__}: {e}", ctx.token)[:300]
        state["last_error_at"] = iso(now)
        save_state(ctx, state)
        ctx.log(f"ERROR {state['last_error']}")
        raise


def _done(ctx, state, outcome):
    state["last_outcome"] = outcome
    if not outcome.startswith("ERROR"):
        state["last_error"] = None
    save_state(ctx, state)
    ctx.log(outcome)
    return outcome


def _publish(ctx, state, week, now, dry_run):
    """`week` is at the CFTC but not confirmed live. Follow, confirm, wait or trigger -
    at most one trigger per attempt, so the same report is not triggered over and over.
    The ~1.5 MB page is only fetched when it can change the decision: before a first
    trigger, after a run succeeded, and before a retry."""
    tr = state.get("trigger")
    if tr and tr.get("week") != week:
        ctx.log(f"a newer report ({week}) replaces the pending trigger for {tr.get('week')}")
        tr = None
        state.pop("trigger", None)
    if tr and tr.get("status") in ("dispatched", "running"):
        msg = _follow(ctx, tr, now)
        state["trigger"] = tr
        if tr["status"] in ("dispatched", "running", "failed"):
            return msg
    if tr and tr.get("status") == "failed" and now < ts(tr["retry_at"]):
        state["trigger"] = tr
        return (f"attempt {tr['attempt']} for {week} failed ({tr.get('last_failure')}); "
                f"retry at {ts(tr['retry_at']):%a %H:%M} UTC")
    shown = page_week(ctx)
    if shown and shown >= week:
        state["confirmed"] = shown
        state.pop("trigger", None)
        how = (f"run {tr.get('last_run_id')}" if tr and tr.get("last_run_id")
               else "a scheduled refresh")
        return f"CONFIRMED: the dashboard shows {shown} (published by {how})"
    if tr and tr.get("status") == "succeeded":
        if now - ts(tr["succeeded_at"]) < PAGE_GRACE:
            return f"run {tr['last_run_id']} succeeded; waiting for the page to show {week} (shows {shown})"
        msg = _failed(tr, now, f"run {tr['last_run_id']} succeeded but the page still shows {shown}")
        state["trigger"] = tr
        return msg
    attempt = (tr or {}).get("attempt", 0) + 1
    if dry_run:
        return f"DRY RUN: would trigger refresh.yml for {week} (attempt {attempt}); page shows {shown}"
    dispatch(ctx)
    state["trigger"] = {"week": week, "attempt": attempt, "status": "dispatched",
                        "dispatched_at": iso(now), "run_id": None, "retry_at": None,
                        "last_run_id": (tr or {}).get("last_run_id")}
    return f"TRIGGERED refresh.yml for {week} (attempt {attempt}); page shows {shown}"


def _follow(ctx, tr, now):
    """Track a dispatched run without touching the page. Sets tr['status'] to running,
    succeeded or failed (with a retry time) and returns what happened."""
    sent = ts(tr["dispatched_at"])
    if not tr.get("run_id"):
        run = find_run(ctx, sent)
        if run:
            tr["run_id"] = tr["last_run_id"] = run["id"]
            tr["run_url"] = run.get("html_url")
        elif now - sent < RUN_FIND_GRACE:
            return f"triggered {tr['week']}; waiting for GitHub to start the run"
        else:
            return _failed(tr, now, "the triggered run never appeared")
    run = get_run(ctx, tr["run_id"])
    if run.get("status") != "completed":
        tr["status"] = "running"
        mins = int((now - sent).total_seconds() // 60)
        if now - sent > RUN_STUCK:
            return f"WARNING: run {tr['run_id']} for {tr['week']} still {run.get('status')} after {mins} min"
        return f"run {tr['run_id']} for {tr['week']} is {run.get('status')} ({mins} min)"
    if run.get("conclusion") != "success":
        return _failed(tr, now, f"run {tr['run_id']} ended {run.get('conclusion')}")
    tr.update(status="succeeded", succeeded_at=iso(now))
    return f"run {tr['run_id']} for {tr['week']} succeeded"


def _failed(tr, now, why):
    n = tr.get("attempt", 1)
    wait = BACKOFF[n - 1] if n - 1 < len(BACKOFF) else SLOW_RETRY
    tr.update(status="failed", last_failure=why, retry_at=iso(now + wait), run_id=None)
    return f"attempt {n} for {tr['week']} failed: {why}; retry at {(now + wait):%a %H:%M} UTC"


# ------------------------------------------------------------------ commands
def check_setup(ctx):
    ok = True
    now = ctx.now()
    try:
        status, text = ctx.http("GET", f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}", auth=True)
        if status == 200:
            ctx.log(f"token: OK - can see {REPO} {WORKFLOW} ({json.loads(text).get('state')})")
        else:
            ok = False
            ctx.log(f"token: FAILED - HTTP {status} {_gh_msg(text, ctx.token)} (needs Actions: read and write on {REPO})")
    except Exception as e:                              # noqa: BLE001
        ok = False
        ctx.log(f"token: FAILED - {type(e).__name__}: {e}")
    for name, fn in (("CFTC", cftc_latest), ("dashboard", page_week)):
        try:
            ctx.log(f"{name}: OK - {fn(ctx)}")
        except Exception as e:                          # noqa: BLE001
            ok = False
            ctx.log(f"{name}: FAILED - {type(e).__name__}: {e}")
    st = load_state(ctx)
    if st.get("cftc_latest"):
        nxt = ct.next_report_date(dt.date.fromisoformat(st["cftc_latest"]))
        ctx.log(f"schedule: next report {nxt}, due {ct.release_at(nxt):%a %d %b %H:%M} UTC")
    ctx.log(f"clock (UTC): {now:%Y-%m-%d %H:%M:%S} - must be NTP-synced (timedatectl)")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="never trigger; say what would happen")
    ap.add_argument("--status", action="store_true", help="print the saved state")
    ap.add_argument("--check-setup", action="store_true", help="verify token, CFTC, page, clock")
    ap.add_argument("--state-dir", default=os.environ.get("STATE_DIRECTORY")
                    or str(Path.home() / ".local" / "state" / "fxstrength-cot"))
    ap.add_argument("--token-file", default=None)
    a = ap.parse_args(argv)
    if os.environ.get("FXCOT_MODE", "").strip().lower() == "dry-run":
        a.dry_run = True
    if a.status:
        ctx = Ctx(a.state_dir)
        ctx.log(json.dumps(load_state(ctx), indent=1, sort_keys=True))
        return 0
    try:
        token = read_token(token_path(a.token_file))
    except FileNotFoundError:
        token = None
    except Exception as e:                              # noqa: BLE001
        print(f"token file problem: {e}", file=sys.stderr)
        return 2
    ctx = Ctx(a.state_dir, token=token)
    if a.check_setup:
        return 0 if check_setup(ctx) else 1
    if token is None and not a.dry_run:
        ctx.log(f"no GitHub token at {token_path(a.token_file)} - see pi/README.md step 4")
        return 2
    lock_path = Path(a.state_dir) / "lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            ctx.log("another check is still running - skipping this one")
            return 0
        try:
            step(ctx, dry_run=a.dry_run)
        except Exception:                               # noqa: BLE001
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
