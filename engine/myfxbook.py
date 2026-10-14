"""Retail sentiment from Myfxbook's Community Outlook - display only.

WHAT IT IS. Myfxbook aggregates the open positions of the trading accounts linked to it and
publishes, per symbol, how many of those traders are long vs short, their volume and their
average entry price. It is RETAIL positioning from one community of accounts. It is shown
on its own ("Retail" tab, and a small card in each 50-week COT report) next to the CFTC
data so the two can be compared by eye. Nothing scores off it, it is not merged with COT
and no buy/sell signal is derived from it.

Not to be confused with sentiment.py, which reads the CFTC's weekly NON-REPORTABLE column.

SOURCE. The official API, never the web page:
    login.json?email=&password=          -> session
    get-community-outlook.json?session=  -> symbols[] + general{}
    logout.json?session=                 -> revokes the session
Sessions are bound to the IP that logged in, so all three calls happen in ONE process on
ONE machine, back to back (a GitHub runner's IP changes every job). The session id lives in
a local variable for those few seconds and is never written, printed or put in an error.

CREDENTIALS come only from the environment (MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD - GitHub
Secrets in the workflows). Neither they nor the session ever reach a file, the page or a
log: every message that leaves fetch() is scrubbed of all three, and the stored snapshot is
built field by field from validated numbers, so nothing else from the response can ride
along.

BUDGET. The free API allows 100 outlook requests per 24 hours. The pipeline runs about every
15 minutes, so a fetch is made only when the last ATTEMPT is >= MIN_GAP_MIN old, and never
more than DAILY_BUDGET attempts in a rolling 24 h (the rest of the 100 is headroom for the
pre-merge build check and manual tests). A login refused for bad credentials backs off
AUTH_BACKOFF_H, so a wrong password cannot hammer the account.

SAFEGUARDS (all in myfxbook_state.json, carried in the cache):
  * failure back-off - after FAIL_BACKOFF_AFTER failed fetches in a row (Myfxbook down,
    malformed response, invalid session) the next attempt waits 1 h, then 2 h, 4 h, and at
    most 6 h; one successful fetch resets it.
  * LOGIN LOCK - an explicit credential rejection waits AUTH_BACKOFF_H; a second one in a row
    stops logging in altogether, so a wrong password can never lock the Myfxbook account.
    Network errors do not count. To reset (after fixing MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD):
    set the repository variable MYFXBOOK_LOGIN_RESET to the lock time the refresh log prints
    ("... set MYFXBOOK_LOGIN_RESET to 2026-10-14T09:30:00Z"). The next refresh with that
    exact value tries again; a later lock has a new time, so an old value never unlocks it.
  * archive retry spacing - a failed archive attempt waits ARCHIVE_EVERY_H like a successful
    one; unarchived day files stay in the cache and are never pruned until archived.
  * HIDE_AFTER_H - a snapshot older than 24 h is not shown: the tab and the cards say
    "Myfxbook retail sentiment unavailable - last successful update <time>" (attribution
    stays). A small script applies the same cut-off in the browser if the page itself is
    not rebuilt for a day.

FAILURE NEVER BLANKS THE DATA. The last valid snapshot is kept and shown, marked "Last known
Myfxbook data - stale" with the reason; values are never replaced by zeros.

STORAGE (engine/data, carried between runs by the refresh workflow's cache, gitignored):
    myfxbook.json             latest valid snapshot + status - what the page is built from
    myfxbook_state.json       attempt times (budget), last error kind
    myfxbook_history/YYYY-MM-DD.csv   one row per market per successful fetch, append-only:
        timestamp,symbol,longPercentage,shortPercentage,longVolume,shortVolume,
        longPositions,shortPositions,avgLongPrice,avgShortPrice,market
    `symbol` is the Myfxbook symbol the reading came from, `market` the dashboard market it
    was used for. Rows are archived whatever the sample size - the MIN_POSITIONS rule only
    decides what the PAGE shows. Files written before `market` existed (10 columns) are read
    and upgraded in place; their market is looked up from the symbol.
History is never loaded into the page. It is there for later views (24 h change, 7-day
history, charts, extremes, retail-vs-COT divergence). The cache copy is only the working
set (the last LOCAL_KEEP_DAYS days); the DURABLE copy is a private GitHub repo written every
ARCHIVE_EVERY_H hours - see archive() below. Read it back with archive_load().
"""
import base64, csv, datetime as dt, gzip, hashlib, io, json, math, os, urllib.error, urllib.parse, urllib.request
from html import escape as _esc
from pathlib import Path

from config import DATA

API = "https://www.myfxbook.com/api"
SOURCE_NAME = "Myfxbook Community Outlook"
SOURCE_URL = "https://www.myfxbook.com/community/outlook"
OUT_NAME = "myfxbook.json"
STATE_NAME = "myfxbook_state.json"
HIST_DIR = "myfxbook_history"
HIST_FIELDS_V1 = ["timestamp", "symbol", "longPercentage", "shortPercentage", "longVolume",
                  "shortVolume", "longPositions", "shortPositions", "avgLongPrice", "avgShortPrice"]
HIST_FIELDS = HIST_FIELDS_V1 + ["market"]

MIN_GAP_MIN = 25          # minutes between attempts (20-30 asked; the pipeline ticks every ~15)
DAILY_BUDGET = 60         # attempts per rolling 24 h, of the free API's 100
AUTH_BACKOFF_H = 6        # after the first refused login
AUTH_LOCK_AFTER = 2       # explicit credential rejections in a row -> no more logins (lock)
RESET_VAR = "MYFXBOOK_LOGIN_RESET"   # repository variable that clears the lock (see LOGIN LOCK)
FAIL_BACKOFF_AFTER = 3    # consecutive failed fetches before the retry delay grows
FAIL_BACKOFF_H = (1, 2, 4, 6)        # hours after failure 3, 4, 5, 6+ (capped at 6 h)
HIDE_AFTER_H = 24         # a snapshot older than this is not shown at all
TIMEOUT_S = 25
DELAYED_MIN = 60          # page labels: older than this -> "Delayed"
STALE_MIN = 180           # older than this -> "Stale"
MIN_POSITIONS = 100       # long + short positions needed before a reading is SHOWN

# Our 16 markets (the COT report keys) -> the Myfxbook symbol names that are the SAME
# instrument (same underlying, same quote currency), exact match only. When more than one
# is in the response, the one with the most open positions (long + short) is used; a tie
# goes to the earlier name. A market with no match shows "Retail sentiment unavailable" -
# nothing is estimated or borrowed from a neighbouring market (no Brent for WTI, no futures
# basket for the dollar index, no crosses for a USD pair). The symbol used is on the page
# and in every history row.
#
# Verification (14 Oct 2026): the nine FX / metals names are on Myfxbook's public Community
# Outlook list. The API returns more symbols than that page (161 in the first real run, which
# matched COPPER for HG); for the remaining names, each CI build check annotates which name
# was chosen per market, so a wrong or new name shows up there. USDOLLAR (an FXCM basket,
# not the ICE dollar index) was dropped as not the same market.
MARKETS = {
    "USD": ["USDX", "DXY"],
    "AUD": ["AUDUSD"], "CAD": ["USDCAD"], "CHF": ["USDCHF"], "EUR": ["EURUSD"],
    "GBP": ["GBPUSD"], "JPY": ["USDJPY"], "NZD": ["NZDUSD"],
    "XAU": ["XAUUSD", "GOLD"], "XAG": ["XAGUSD", "SILVER"],
    "HG": ["XCUUSD", "COPPER"],
    "WTI": ["WTI", "USOIL", "XTIUSD", "WTIUSD"],
    "ES": ["US500", "SPX500", "SP500"], "NQ": ["NAS100", "USTEC", "NDX100"],
    "YM": ["US30", "DJ30", "DOW30"],
    "ZB": [],
}


# THE SWITCH. Myfxbook runs only when MYFXBOOK_ENABLED is exactly "true" (a repository
# variable, passed to every workflow step that runs this code). Anything else - unset,
# empty, "false", "TRUE", "1" - means OFF: no login, no fetch, no archive write, and the page
# shows no Myfxbook figure, not even a cached one. Off is the default; existing private
# history is never touched. Kept off until Myfxbook confirms public display and storage.
ENABLE_VAR = "MYFXBOOK_ENABLED"


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get(ENABLE_VAR) == "true"


class MyfxbookError(Exception):
    """kind: config | auth | session | unavailable | malformed. Message already scrubbed."""
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------- secrets hygiene
def scrub(text, *secrets):
    """Remove every secret (raw and URL-encoded) from a message."""
    out = str(text)
    for s in secrets:
        if s:
            for form in {s, urllib.parse.quote(s, safe=""), urllib.parse.quote_plus(s)}:
                out = out.replace(form, "[redacted]")
    return out


def _get_json(opener, url, secrets):
    """GET url -> parsed JSON. Errors carry no URL and no secret."""
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": "fxstrength-desk"}),
                    timeout=TIMEOUT_S) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise MyfxbookError("unavailable", f"HTTP {e.code}") from None
    except Exception as e:                                       # noqa: BLE001
        raise MyfxbookError("unavailable", scrub(f"{type(e).__name__}", *secrets)) from None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise MyfxbookError("malformed", "response is not JSON") from None


def fetch(email, password, opener=None, leak_probe=None):
    """login -> outlook -> logout, one process, one IP. Returns the outlook payload.

    leak_probe: CI leak-scan hook only (build-check sets MYFXBOOK_LEAKSCAN_FILE to a path
    in the runner's temp dir). AFTER logout - so the session is already revoked - its id is
    written there, mode 600, for the scan step to search every output for, which then
    deletes it. Refused anywhere under the repo or the data dir."""
    if not email or not password:
        raise MyfxbookError("config", "MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD not set")
    opener = opener or urllib.request.urlopen
    q = urllib.parse.urlencode({"email": email, "password": password})
    login = _get_json(opener, f"{API}/login.json?{q}", (email, password))
    if not isinstance(login, dict):
        raise MyfxbookError("malformed", "login response is not an object")
    session = login.get("session")
    if login.get("error") or not session:
        raise MyfxbookError("auth", "login refused: " + scrub(login.get("message") or "no session",
                                                               email, password, session or ""))
    secrets = (email, password, session)
    try:
        sq = urllib.parse.urlencode({"session": session})
        data = _get_json(opener, f"{API}/get-community-outlook.json?{sq}", secrets)
        if isinstance(data, dict) and data.get("error"):
            msg = scrub(data.get("message") or "error", *secrets)
            kind = "session" if "session" in msg.lower() else "unavailable"
            raise MyfxbookError(kind, f"outlook refused: {msg}")
        return data
    except MyfxbookError as e:
        raise MyfxbookError(e.kind, scrub(str(e), *secrets)) from None
    finally:
        try:
            _get_json(opener, f"{API}/logout.json?" + urllib.parse.urlencode({"session": session}),
                      secrets)
        except Exception:                                        # noqa: BLE001 - best effort
            pass
        if leak_probe:
            _write_probe(leak_probe, session)
        session = None


def _write_probe(path, session):
    p = Path(path).resolve()
    repo = Path(__file__).resolve().parent.parent
    if not p.is_absolute() or p == repo or repo in p.parents:
        return                                   # never inside the repo / data dir
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(session)


# ---------------------------------------------------------------- validation
def _num(v, lo=None, hi=None):
    if isinstance(v, bool):
        raise ValueError("boolean")
    if isinstance(v, str):
        v = float(v.replace(",", ""))
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"not a number: {v!r}")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"out of range: {v}")
    return float(v)


def parse(payload):
    """Validated {SYMBOL: record} for EVERY symbol in the response, plus rejects.
    Raises MyfxbookError('malformed') when the response has no usable symbols at all."""
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise MyfxbookError("malformed", "no symbols list in the response")
    out, rejected = {}, []
    for s in payload["symbols"]:
        try:
            if not isinstance(s, dict):
                raise ValueError("entry is not an object")
            name = str(s.get("name") or "").strip().upper()
            if not name or len(name) > 20:
                raise ValueError("missing symbol name")
            lp = _num(s.get("longPercentage"), 0, 100)
            sp = _num(s.get("shortPercentage"), 0, 100)
            if not 97 <= lp + sp <= 103:
                raise ValueError(f"long+short = {lp + sp:g}%")
            rec = {"longPercentage": lp, "shortPercentage": sp,
                   "longVolume": _num(s.get("longVolume"), 0),
                   "shortVolume": _num(s.get("shortVolume"), 0),
                   "longPositions": int(_num(s.get("longPositions"), 0)),
                   "shortPositions": int(_num(s.get("shortPositions"), 0))}
            for k in ("avgLongPrice", "avgShortPrice"):
                v = s.get(k)
                rec[k] = None if v in (None, "", 0, "0") else _num(v, 0)
            out[name] = rec
        except (ValueError, TypeError) as e:
            # name + reason TYPE only - no figures (this list reaches the public CI log)
            msg = str(e)
            kind = ("long+short not ~100%" if msg.startswith("long+short") else
                    "value out of range" if msg.startswith("out of range") else
                    "not a number" if msg.startswith("not a number") or msg == "boolean" else
                    "missing name" if "name" in msg else "bad entry")
            rejected.append(f"{(s.get('name') if isinstance(s, dict) else '?')!s:.20}: {kind}")
    if not out:
        raise MyfxbookError("malformed", f"no valid symbols ({len(rejected)} rejected)")
    return out, rejected


def positions(rec):
    return rec["longPositions"] + rec["shortPositions"]


def match(symbols):
    """{market key: (myfxbook symbol, record)} - per market, the most-populated of its exact
    candidates present in the response (tie -> the earlier candidate). The record carries
    `candidates`: every candidate name that was present, in MARKETS order."""
    norm = {"".join(ch for ch in k if ch.isalnum()): k for k in symbols}
    out = {}
    for key, cands in MARKETS.items():
        present = [norm[c] for c in cands if c in norm]
        if not present:
            continue
        best = max(present, key=lambda s: (positions(symbols[s]), -present.index(s)))
        out[key] = (best, {**symbols[best], "candidates": present})
    return out


def market_of(symbol):
    """Market key for a Myfxbook symbol (for history rows written before `market` existed)."""
    s = "".join(ch for ch in str(symbol).upper() if ch.isalnum())
    return next((k for k, cands in MARKETS.items() if s in cands), "")


# ---------------------------------------------------------------- derived figures
def too_few(rec):
    """A reading needs MIN_POSITIONS positions (long + short) before the page shows it."""
    return positions(rec) < MIN_POSITIONS


def too_few_text(rec):
    return (f"Too few Myfxbook positions (n = {positions(rec):,}) &mdash; no reading")



def imbalance(lp, sp):
    return round(abs(lp - sp))


def crowd(lp, sp):
    """Descriptive label only - not a signal."""
    if lp >= 70:
        return "Heavy Long"
    if sp >= 70:
        return "Heavy Short"
    if lp >= 60:
        return "Long"
    if sp >= 60:
        return "Short"
    return "Balanced"


def freshness(age_min):
    if age_min is None:
        return "none"
    if age_min > STALE_MIN:
        return "stale"
    if age_min > DELAYED_MIN:
        return "delayed"
    return "fresh"


# ---------------------------------------------------------------- storage
def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return default


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _day_rows(text):
    """CSV text (with header) -> list of row lists in HIST_FIELDS order, header dropped.
    A 10-column file from before `market` existed is upgraded (market from the symbol).
    Raises on an unknown header: guessing would let an archive push overwrite good rows."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    if rows[0] == HIST_FIELDS:
        return [r for r in rows[1:] if r]
    if rows[0] == HIST_FIELDS_V1:
        return [r + [market_of(r[1])] for r in rows[1:] if r]
    raise ValueError("unknown Myfxbook history header")


def _day_text(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HIST_FIELDS)
    w.writerows(sorted(rows, key=lambda r: (r[0], r[1])))
    return buf.getvalue()


def _append_history(data, ts, matched):
    """One CSV per UTC day (data/myfxbook_history/YYYY-MM-DD.csv), one row per matched market
    per successful fetch - the same day files the durable archive stores. Every matched
    market is written, including ones below MIN_POSITIONS."""
    d = data / HIST_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ts[:10]}.csv"
    if p.exists() and p.read_text(encoding="utf-8").split("\n", 1)[0] == ",".join(HIST_FIELDS_V1):
        p.write_text(_day_text(_day_rows(p.read_text(encoding="utf-8"))), encoding="utf-8")
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(HIST_FIELDS)
        for key, (sym, r) in sorted(matched.items(), key=lambda kv: (kv[1][0], kv[0])):
            w.writerow([ts, sym] + [r[k] if r[k] is not None else "" for k in HIST_FIELDS_V1[2:]] + [key])


def load_history(data=None, since=None):
    """Locally held rows (dicts) at/after `since` (ISO string), oldest first - the recent
    working set (LOCAL_KEEP_DAYS). The full history is the archive (archive_load())."""
    d = Path(data or DATA) / HIST_DIR
    rows = []
    for p in sorted(d.glob("????-??-??.csv")) if d.exists() else []:
        if since and p.stem < since[:10]:
            continue
        for r in _day_rows(p.read_text(encoding="utf-8")):
            if not since or r[0] >= since:
                rows.append(dict(zip(HIST_FIELDS, r)))
    return rows


# ---------------------------------------------------------------- durable history archive
# The Actions cache is only a working copy. The durable history is a PRIVATE GitHub repo
# (default haroontahir73/fxstrength-data; MYFXBOOK_HISTORY_REPO overrides), written every
# ARCHIVE_EVERY_H hours through the GitHub contents API with MYFXBOOK_HISTORY_TOKEN (a
# fine-grained token for that one repo, Contents: read and write):
#     myfxbook/daily/YYYY/YYYY-MM-DD.csv.gz     same columns as the local day files
# Each push MERGES with the archived copy (union by timestamp + symbol), so a lost cache can
# never delete archived rows. It refuses to write unless the repo is private (the data's
# public-display terms are not settled). Old local day files are dropped only once archived.
ARCHIVE_EVERY_H = 6
ARCHIVE_REPO = "haroontahir73/fxstrength-data"
ARCHIVE_PREFIX = "myfxbook/daily"
LOCAL_KEEP_DAYS = 35
GH_API = "https://api.github.com"


class ArchiveError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _gh(opener, method, path, token, body=None):
    req = urllib.request.Request(
        GH_API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fxstrength-desk"})
    try:
        with opener(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ArchiveError("github", f"GitHub HTTP {e.code} on {method} {path.split('?')[0]}") from None
    except ValueError:
        raise ArchiveError("github", "GitHub answered with non-JSON") from None
    except Exception as e:                                       # noqa: BLE001
        raise ArchiveError("github", f"GitHub unreachable ({type(e).__name__})") from None


def _gz(text):
    return gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)


def _archive_path(day):
    return f"{ARCHIVE_PREFIX}/{day[:4]}/{day}.csv.gz"


def _put(opener, token, repo, path, content, message):
    cur = _gh(opener, "GET", f"/repos/{repo}/contents/{path}", token)
    body = {"message": message, "content": base64.b64encode(content).decode()}
    if cur and cur.get("sha"):
        body["sha"] = cur["sha"]
    _gh(opener, "PUT", f"/repos/{repo}/contents/{path}", token, body)


def archive(now=None, data=None, env=None, opener=None, force=False, verify_only=False):
    """Push changed day files to the private archive repo (every ARCHIVE_EVERY_H hours).
    verify_only: prove the token can write (one small file, no data) - the pre-merge check.
    Never raises; the outcome is recorded in myfxbook_state.json."""
    now = now or dt.datetime.now(dt.timezone.utc)
    data = Path(data or DATA)
    env = os.environ if env is None else env
    opener = opener or urllib.request.urlopen
    token = env.get("MYFXBOOK_HISTORY_TOKEN", "")
    repo = env.get("MYFXBOOK_HISTORY_REPO") or ARCHIVE_REPO
    if not enabled(env):
        print(f"  Myfxbook history archive: switched off ({ENABLE_VAR} is not 'true') - nothing written")
        return {"enabled": False, "repo": repo}
    st_p = data / STATE_NAME
    st = _read(st_p, {"attempts": []})
    a = st.setdefault("archive", {})
    a["repo"] = repo
    if not token:
        a.update(configured=False)
        print("  Myfxbook history archive: not configured (MYFXBOOK_HISTORY_TOKEN) - kept in the cache only")
        _write(st_p, st)
        return a
    a["configured"] = True
    # 6 h between attempts - failed ones included, so a broken archive is not retried on
    # every refresh; nothing is lost meanwhile (unarchived day files stay in the cache)
    last = max(a.get("last_push_at") or "", a.get("last_failed_at") or "") or None
    if not force and not verify_only and last and \
            now - dt.datetime.fromisoformat(last.replace("Z", "+00:00")) < dt.timedelta(hours=ARCHIVE_EVERY_H):
        what = "failed attempt" if last == a.get("last_failed_at") else "push"
        print(f"  Myfxbook history archive: last {what} {last[:16]}Z, next after {ARCHIVE_EVERY_H} h")
        return a
    pushed = []
    try:
        meta = _gh(opener, "GET", f"/repos/{repo}", token)
        if meta is None:
            raise ArchiveError("missing", f"archive repo {repo} not found (or the token cannot see it)")
        if meta.get("private") is not True:
            raise ArchiveError("public", f"archive repo {repo} is not private - refusing to write")
        if verify_only:
            _put(opener, token, repo, "myfxbook/_ci_write_check.json",
                 json.dumps({"checked_at": _iso(now)}).encode(), "myfxbook: CI write check (no data)")
            print(f"  Myfxbook history archive: {repo} is private and writable (CI check, no data written)")
            a.update(last_verify_at=_iso(now), last_error=None)
            _write(st_p, st)
            return a
        d = data / HIST_DIR
        hashes = a.setdefault("hashes", {})
        for p in sorted(d.glob("????-??-??.csv")) if d.exists() else []:
            text = p.read_text(encoding="utf-8")
            h = hashlib.sha256(text.encode()).hexdigest()
            if hashes.get(p.stem) == h:
                continue
            path = _archive_path(p.stem)
            cur = _gh(opener, "GET", f"/repos/{repo}/contents/{path}", token)
            remote, remote_text = [], ""
            try:
                if cur and cur.get("content"):
                    remote_text = gzip.decompress(base64.b64decode(cur["content"])).decode("utf-8")
                    remote = _day_rows(remote_text)
                local = _day_rows(text)
            except (ValueError, OSError, EOFError):
                # never overwrite a file we cannot read - that would drop archived rows
                raise ArchiveError("format", f"history file {p.stem} unreadable - not written") from None
            merged = {(r[0], r[1]): r for r in remote}
            merged.update({(r[0], r[1]): r for r in local})
            out = _day_text(list(merged.values()))
            if not remote or len(merged) != len(remote) or remote_text != _day_text(remote):
                body = {"message": f"myfxbook history {p.stem} ({len(merged)} rows)",
                        "content": base64.b64encode(_gz(out)).decode()}
                if cur and cur.get("sha"):
                    body["sha"] = cur["sha"]
                _gh(opener, "PUT", f"/repos/{repo}/contents/{path}", token, body)
                pushed.append(p.stem)
            hashes[p.stem] = h
        # drop old local day files, but only ones the archive is known to hold
        cutoff = (now - dt.timedelta(days=LOCAL_KEEP_DAYS)).strftime("%Y-%m-%d")
        for p in sorted(d.glob("????-??-??.csv")) if d.exists() else []:
            if p.stem < cutoff and p.stem in hashes:
                p.unlink()
                hashes.pop(p.stem, None)
        a.update(last_push_at=_iso(now), last_error=None, last_pushed_days=pushed)
        a.pop("last_failed_at", None)
        print(f"  Myfxbook history archive: {len(pushed)} day file(s) written to {repo}"
              + (f" ({', '.join(pushed)})" if pushed else " (nothing new)"))
    except ArchiveError as e:
        a.update(last_error=e.kind, last_error_msg=scrub(str(e), token))
        if not verify_only:
            a["last_failed_at"] = _iso(now)
        print(f"  Myfxbook history archive: FAILED ({e.kind}: {a['last_error_msg']}) - unarchived "
              f"history kept in the cache, next attempt in {ARCHIVE_EVERY_H} h")
    _write(st_p, st)
    return a


def archive_load(env=None, opener=None, since_day=None):
    """Read the archived history back (for the later history / divergence / backtest work):
    rows as dicts, oldest first."""
    env = os.environ if env is None else env
    opener = opener or urllib.request.urlopen
    token, repo = env.get("MYFXBOOK_HISTORY_TOKEN", ""), env.get("MYFXBOOK_HISTORY_REPO") or ARCHIVE_REPO
    rows = []
    years = _gh(opener, "GET", f"/repos/{repo}/contents/{ARCHIVE_PREFIX}", token) or []
    for y in sorted(x["name"] for x in years if x.get("type") == "dir"):
        files = _gh(opener, "GET", f"/repos/{repo}/contents/{ARCHIVE_PREFIX}/{y}", token) or []
        for f in sorted(x["name"] for x in files if x["name"].endswith(".csv.gz")):
            if since_day and f[:10] < since_day:
                continue
            cur = _gh(opener, "GET", f"/repos/{repo}/contents/{ARCHIVE_PREFIX}/{y}/{f}", token)
            text = gzip.decompress(base64.b64decode(cur["content"])).decode("utf-8")
            rows.extend(dict(zip(HIST_FIELDS, r)) for r in _day_rows(text))   # 10-col files upgraded
    return rows


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail_delay_h(fails):
    """Retry delay after `fails` consecutive failed fetches (>= FAIL_BACKOFF_AFTER)."""
    return FAIL_BACKOFF_H[min(fails - FAIL_BACKOFF_AFTER, len(FAIL_BACKOFF_H) - 1)]


def build(now=None, data=None, env=None, opener=None, force=False):
    """Fetch when due, keep the last good snapshot, write myfxbook.json. Never raises for a
    Myfxbook problem - the problem is recorded and the page says so."""
    now = now or dt.datetime.now(dt.timezone.utc)
    data = Path(data or DATA)
    env = os.environ if env is None else env
    out_p, st_p = data / OUT_NAME, data / STATE_NAME
    if not enabled(env):
        # no login, no request, no state change; any cached snapshot stays private (the
        # page will not show it) and the history files are left exactly as they are
        print(f"  Myfxbook: switched off ({ENABLE_VAR} is not 'true') - no login, no fetch")
        return {"enabled": False}
    snap = _read(out_p, {})
    st = _read(st_p, {"attempts": []})
    email, password = env.get("MYFXBOOK_EMAIL", ""), env.get("MYFXBOOK_PASSWORD", "")
    configured = bool(email and password)
    cutoff = _iso(now - dt.timedelta(hours=24))
    st["attempts"] = [a for a in st.get("attempts", []) if a >= cutoff]
    last = max(st["attempts"]) if st["attempts"] else None
    why = None
    if st.get("auth_locked_at") and (env.get(RESET_VAR) or "").strip() == st["auth_locked_at"]:
        print(f"  Myfxbook: login lock of {st['auth_locked_at']} cleared by {RESET_VAR} - trying again")
        for k in ("auth_locked_at", "auth_rejections", "auth_backoff_until"):
            st.pop(k, None)
    fails = st.get("consecutive_failures", 0)
    since_last = (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))) if last else None
    if not configured:
        why = "not configured"
    elif st.get("auth_locked_at"):
        why = (f"login stopped after {AUTH_LOCK_AFTER} rejected logins in a row (locked at "
               f"{st['auth_locked_at']}). Fix MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD, then set the "
               f"repository variable {RESET_VAR} to {st['auth_locked_at']}")
    elif not force and since_last is not None and since_last < dt.timedelta(minutes=MIN_GAP_MIN):
        why = f"last attempt {last[11:16]} UTC, next after {MIN_GAP_MIN} min"
    elif fails >= FAIL_BACKOFF_AFTER and since_last is not None and \
            since_last < dt.timedelta(hours=_fail_delay_h(fails)):
        why = (f"{fails} failed fetches in a row - next attempt {_fail_delay_h(fails)} h after "
               f"the last ({last[11:16]} UTC)")
    elif len(st["attempts"]) >= DAILY_BUDGET:
        why = f"daily budget used ({DAILY_BUDGET} attempts in 24 h)"
    elif st.get("auth_backoff_until") and _iso(now) < st["auth_backoff_until"]:
        why = f"login refused earlier - waiting until {st['auth_backoff_until'][11:16]} UTC"
    status = dict(snap.get("status") or {})
    status["configured"] = configured
    if why:
        print(f"  Myfxbook: no fetch ({why})")
        if not configured:
            status.update(last_error="config", last_error_msg="credentials not set")
    else:
        st["attempts"].append(_iso(now))
        status["last_attempt_at"] = _iso(now)
        try:
            symbols, rejected = parse(fetch(email, password, opener,
                                            leak_probe=env.get("MYFXBOOK_LEAKSCAN_FILE")))
            matched = match(symbols)
            ts = _iso(now)
            snap = {"fetched_at": ts, "markets": {k: {"symbol": s, **r} for k, (s, r) in matched.items()},
                    "symbols_in_response": len(symbols), "symbols_returned": sorted(symbols),
                    "rejected": rejected[:60]}
            _append_history(data, ts, matched)
            status.update(last_ok_at=ts, last_error=None, last_error_msg=None)
            for k in ("auth_backoff_until", "auth_rejections", "consecutive_failures"):
                st.pop(k, None)
            # names only - no values in the (public) log
            print(f"  Myfxbook: {len(symbols)} symbols, {len(matched)} of our 16 markets matched"
                  + (f", {len(rejected)} rejected" if rejected else ""))
            print("    returned: " + ", ".join(sorted(symbols)))
            print("    mapped: " + (", ".join(f"{k}<-{s}" + (f" (of {'/'.join(r['candidates'])})"
                                                           if len(r["candidates"]) > 1 else "")
                                            for k, (s, r) in sorted(matched.items())) or "none")
                  + "; unavailable: " + (", ".join(k for k in MARKETS if k not in matched) or "none"))
            few = [k for k, (_, r) in sorted(matched.items()) if too_few(r)]
            print(f"    below {MIN_POSITIONS} positions (archived, not shown): " + (", ".join(few) or "none"))
        except MyfxbookError as e:
            status.update(last_error=e.kind, last_error_msg=scrub(str(e), email, password))
            if e.kind == "auth":
                # an explicit credential rejection: wait, and stop for good on the second
                st["auth_rejections"] = st.get("auth_rejections", 0) + 1
                if st["auth_rejections"] >= AUTH_LOCK_AFTER:
                    st["auth_locked_at"] = _iso(now)
                    st.pop("auth_backoff_until", None)
                    status["last_error_msg"] = "login stopped - credentials rejected twice"
                    print(f"  Myfxbook: login LOCKED after {st['auth_rejections']} rejected logins. Fix the "
                          f"credentials, then set the repository variable {RESET_VAR} to {st['auth_locked_at']}")
                else:
                    st["auth_backoff_until"] = _iso(now + dt.timedelta(hours=AUTH_BACKOFF_H))
            else:
                st["consecutive_failures"] = st.get("consecutive_failures", 0) + 1
            print(f"  Myfxbook: fetch failed ({e.kind}: {status['last_error_msg']}) - keeping the "
                  + (f"snapshot of {snap['fetched_at']}" if snap.get("fetched_at") else "page without data"))
    st["last_error"] = status.get("last_error")
    snap["status"] = status
    snap["source"] = {"name": SOURCE_NAME, "url": SOURCE_URL}
    _write(out_p, snap)
    _write(st_p, st)
    return snap


# ---------------------------------------------------------------- rendering
_KEY_LABEL = None


def _labels():
    global _KEY_LABEL
    if _KEY_LABEL is None:
        import cot_table as ct
        _KEY_LABEL = [(g, [(r[3], r[0]) for r in rows]) for g, rows in
                      (("Currency pairs", ct.FX_ROWS), ("Commodities & indexes", ct.CM_ROWS))]
    return _KEY_LABEL


def _fmt_ts(ts):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t.strftime("%d %b %Y %H:%M UTC")


def _age_min(ts, now):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return max(0, int((now - t).total_seconds() // 60))


def _age_txt(m):
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h, mm = divmod(m, 60)
    return f"{h} h {mm:02d} min" if h < 48 else f"{h // 24} days"


def _n(v, dec=2):
    if v is None:
        return "&mdash;"
    if dec == 0:
        return f"{int(round(v)):,}"
    return f"{v:,.{dec}f}"


def _price(v):
    if v is None:
        return "&mdash;"
    return f"{v:,.6f}".rstrip("0").rstrip(".")      # as published, no rounding


def _pc(v):
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def _bar(lp, sp):
    """Two-sided bar; colour strength grows with the imbalance, 50/50 stays near neutral."""
    a = round(0.22 + 0.78 * min(imbalance(lp, sp), 60) / 60, 3)
    return (f'<div class="mfxbar" style="--a:{a}" role="img" aria-label="long {_pc(lp)}%, short {_pc(sp)}%">'
            f'<span class="mfl" style="width:{lp:.4g}%"></span><span class="mfs" style="width:{sp:.4g}%"></span></div>')


def _freshness_line(snap, now, compact=False):
    ts = snap.get("fetched_at")
    st = snap.get("status") or {}
    if not ts:
        return ""
    m = _age_min(ts, now)
    f = freshness(m)
    label = {"fresh": "", "delayed": "Delayed", "stale": "Stale"}[f]
    failed = st.get("last_error") and st.get("last_error") != "config"
    warn = ('<span class="mfxwarn">Last known Myfxbook data &mdash; stale'
            + (f' (latest update failed: {_esc(st.get("last_error_msg") or st["last_error"])})' if not compact else "")
            + "</span>") if failed else ""
    return (f'<span class="mfxfresh" data-mfx-ts="{_esc(ts)}" data-mfx-delayed="{DELAYED_MIN}" data-mfx-stale="{STALE_MIN}">'
            f'Updated: {_fmt_ts(ts)} &middot; Age: <span class="mfxage">{_age_txt(m)}</span> '
            f'<span class="mfxstate {f}">{label}</span></span>{warn}')


ATTRIB = (f'<p class="mfxsrc">Retail sentiment data: <a href="{SOURCE_URL}" target="_blank" '
          f'rel="noopener">{SOURCE_NAME}</a>. Positions of the trading accounts linked to Myfxbook '
          f'&mdash; one community of retail traders, not the whole market. Shown as published; '
          f'this dashboard does not generate or adjust it.</p>')

# Live age without a server: the page is static, so a tiny script turns each stored
# timestamp into its current age and label, and hides every reading once the snapshot is
# more than HIDE_AFTER_H old (the page may sit unrebuilt for days if refreshes stop).
# Runs at once and again on DOMContentLoaded (the report cards come later in the page).
# Without script the build-time age and state stay.
SCRIPT = """<script>(function(){function u(){var n=Date.now();document.querySelectorAll('[data-mfx-exp]').forEach(function(e){var t=Date.parse(e.getAttribute('data-mfx-exp'));if(!isNaN(t))e.classList.toggle('expired',(n-t)/60000>+e.getAttribute('data-mfx-hide'))});document.querySelectorAll('[data-mfx-ts]').forEach(function(e){var t=Date.parse(e.getAttribute('data-mfx-ts'));if(isNaN(t))return;var m=Math.max(0,Math.floor((n-t)/60000)),a=e.querySelector('.mfxage'),s=e.querySelector('.mfxstate');if(a)a.textContent=m<60?m+(m==1?' minute':' minutes'):(m<2880?Math.floor(m/60)+' h '+('0'+m%60).slice(-2)+' min':Math.floor(m/1440)+' days');if(s){var st=+e.getAttribute('data-mfx-stale'),dl=+e.getAttribute('data-mfx-delayed');s.className='mfxstate '+(m>st?'stale':m>dl?'delayed':'fresh');s.textContent=m>st?'Stale':m>dl?'Delayed':'';}})}u();document.addEventListener('DOMContentLoaded',u);setInterval(u,60000);})();</script>"""


HIDE_MIN = HIDE_AFTER_H * 60


def expired(snap, now):
    """True once the stored snapshot is more than HIDE_AFTER_H old: nothing is shown from it."""
    ts = snap.get("fetched_at")
    return bool(ts) and _age_min(ts, now) > HIDE_MIN


def _exp_text(ts, card=False):
    return (("Retail sentiment unavailable" if card else "Myfxbook retail sentiment unavailable")
            + f" &mdash; no successful update for more than {HIDE_AFTER_H} hours. "
            f"Last successful update: {_fmt_ts(ts)}.")


def _exp_attrs(ts):
    """Client-side expiry: SCRIPT adds 'expired' to this element after HIDE_AFTER_H, and CSS
    then hides its .mfxlive part and shows its .mfxexp message."""
    return f' data-mfx-exp="{_esc(ts)}" data-mfx-hide="{HIDE_MIN}"'


def _row(key, label, m, link=True):
    name = (f'<a href="#cot50-{_esc(key)}" class="mfxname">{_esc(label)}</a>' if link
            else f'<span class="mfxname">{_esc(label)}</span>')
    if not m:
        return (f'<div class="mfxrow na" data-mfx-key="{_esc(key)}">{name}'
                f'<span class="mfxna">Retail sentiment unavailable</span></div>')
    if too_few(m):
        # sample too small: the count and the symbol only - no %, bar, imbalance or crowd
        return (f'<div class="mfxrow na few" data-mfx-key="{_esc(key)}">{name}'
                + (f'<span class="mfxsym">{_esc(m["symbol"])}</span>' if m["symbol"] != label.upper() else "")
                + f'<span class="mfxna">{too_few_text(m)}</span></div>')
    lp, sp = m["longPercentage"], m["shortPercentage"]
    imb = imbalance(lp, sp)
    em = "hi" if imb >= 40 else ("md" if imb >= 20 else "lo")
    return (f'<div class="mfxrow" data-mfx-key="{_esc(key)}">'
            f'<div class="mfxhd">{name}'
            + (f'<span class="mfxsym">{_esc(m["symbol"])}</span>' if m["symbol"] != label.upper() else "")
            + f'<span class="mfximb {em}" title="Imbalance = |Long % - Short %|">Imbalance {imb}</span>'
            f'<span class="mfxcrowd">Retail crowd: <b>{crowd(lp, sp)}</b></span></div>'
            f'<div class="mfxls"><span class="mfxl">LONG {_pc(lp)}%</span>{_bar(lp, sp)}'
            f'<span class="mfxs">SHORT {_pc(sp)}%</span></div>'
            f'<dl class="mfxv"><dt>Volume (lots)</dt><dd>L {_n(m["longVolume"])} &middot; S {_n(m["shortVolume"])}</dd>'
            f'<dt>Positions</dt><dd>L {_n(m["longPositions"], 0)} &middot; S {_n(m["shortPositions"], 0)}</dd>'
            f'<dt>Avg price</dt><dd>L {_price(m["avgLongPrice"])} &middot; S {_price(m["avgShortPrice"])}</dd></dl>'
            f'</div>')


def _load(data):
    return _read(Path(data or DATA) / OUT_NAME, {})


OFF_NOTE = ('<p class="mfxempty mfxoff">Myfxbook retail sentiment is switched off &mdash; no Myfxbook '
            'data is fetched or shown.</p>')


def retail_panel(now=None, data=None, env=None):
    """The 'Retail' tab."""
    if not enabled(env):
        return ('<section class="mfx"><h2>Retail sentiment &mdash; Myfxbook</h2>' + OFF_NOTE + "</section>")
    now = now or dt.datetime.now(dt.timezone.utc)
    snap = _load(data)
    st = snap.get("status") or {}
    markets = snap.get("markets") or {}
    head = ('<section class="mfx"><h2>Retail sentiment &mdash; Myfxbook</h2>'
            '<p class="sub">How Myfxbook&rsquo;s community of retail traders is positioned in each of the '
            'dashboard&rsquo;s markets: share long vs short, volume, number of positions and average entry '
            'price. <b>Separate from the COT data</b> (institutional futures positioning) and from the '
            'CFTC-based &ldquo;Retail sentiment&rdquo; on the COT tab &mdash; nothing is combined or scored. '
            'Long is not &ldquo;bullish&rdquo; and short is not &ldquo;bearish&rdquo;: these are raw '
            'positioning figures, not trade signals. Tap a market for its 50-week COT report, where the '
            'same figures sit beside the institutional data.</p>')
    if not snap.get("fetched_at"):
        msg = ("Myfxbook credentials are not configured yet, so no data has been fetched."
               if not st.get("configured") else
               "No Myfxbook data yet" + (f" &mdash; last attempt failed: {_esc(st.get('last_error_msg') or '')}"
                                          if st.get("last_error") else " &mdash; fetched on the next refresh."))
        return head + f'<p class="mfxempty">{msg}</p>' + ATTRIB + "</section>"
    ts = snap["fetched_at"]
    if expired(snap, now):
        # older than HIDE_AFTER_H: no reading reaches the page at all
        return head + f'<p class="mfxempty mfxexpired">{_exp_text(ts)}</p>' + ATTRIB + "</section>"
    body = [f'<div class="mfxwrap"{_exp_attrs(ts)}><p class="mfxexp">{_exp_text(ts)}</p>'
            f'<div class="mfxlive"><p class="mfxmeta">{_freshness_line(snap, now)}</p>']
    for g, rows in _labels():
        body.append(f'<h3 class="mfxg">{g}</h3><div class="mfxlist">')
        body.extend(_row(k, lab, markets.get(k)) for k, lab in rows)
        body.append("</div>")
    legend = ('<p class="mfxkey"><span class="mfxl">LONG</span> and <span class="mfxs">SHORT</span> are '
              'two colours that mean nothing more than the side. The bar&rsquo;s colour strengthens with the '
              '<b>imbalance</b> = |Long % &minus; Short %| (50/50 = 0, 60/40 = 20, 80/20 = 60). '
              '<b>Retail crowd</b>: Long &ge; 70% Heavy Long, 60&ndash;69% Long, 41&ndash;59% Balanced, '
              'Short 60&ndash;69% Short, &ge; 70% Heavy Short &mdash; a description, not a signal. '
              f'A market needs at least {MIN_POSITIONS} open Myfxbook positions (long + short) for a '
              'reading; below that only the count is shown.</p>')
    return head + "".join(body) + legend + "</div></div>" + ATTRIB + SCRIPT + "</section>"


# Myfxbook permits display of Community Outlook data with clear attribution and a link
# back (support email, 14 Oct 2026): every card carries this line, whatever it shows.
CARD_CREDIT = (f'<p class="mfxcl mut">Retail sentiment data: <a href="{SOURCE_URL}" target="_blank" '
               f'rel="noopener">{SOURCE_NAME}</a></p>')


def report_card(key, now=None, data=None, env=None):
    """The small 'Myfxbook retail' card inside a market's 50-week COT report. Nothing at all
    while the switch is off. Always ends with CARD_CREDIT."""
    if not enabled(env):
        return ""
    now = now or dt.datetime.now(dt.timezone.utc)
    snap = _load(data)
    head = '<div class="mfxcard"><div class="mfxct">Myfxbook retail'
    if not snap.get("fetched_at"):
        return head + f'</div><p class="mfxna">No Myfxbook data yet.</p>{CARD_CREDIT}</div>'
    ts = snap["fetched_at"]
    if expired(snap, now):
        return head + f'</div><p class="mfxna mfxexpired">{_exp_text(ts, card=True)}</p>{CARD_CREDIT}</div>'
    m = (snap.get("markets") or {}).get(key)
    if not m:
        return head + f'</div><p class="mfxna">Retail sentiment unavailable</p>{CARD_CREDIT}</div>'
    head = (f'<div class="mfxcard"{_exp_attrs(ts)}><div class="mfxct">Myfxbook retail '
            f'<span class="mfxsym">{_esc(m["symbol"])}</span></div>'
            f'<p class="mfxna mfxexp">{_exp_text(ts, card=True)}</p><div class="mfxlive">')
    fresh = f'<p class="mfxcl mut">{_freshness_line(snap, now, compact=True)}</p>'
    if too_few(m):
        return head + f'<p class="mfxna">{too_few_text(m)}</p>{fresh}</div>{CARD_CREDIT}</div>'
    lp, sp = m["longPercentage"], m["shortPercentage"]
    return (head + f'<div class="mfxls"><span class="mfxl">LONG {_pc(lp)}%</span>{_bar(lp, sp)}'
            f'<span class="mfxs">SHORT {_pc(sp)}%</span></div>'
            f'<p class="mfxcl">Long {_pc(lp)}% &middot; Short {_pc(sp)}% &middot; Imbalance {imbalance(lp, sp)} '
            f'&middot; Retail crowd: <b>{crowd(lp, sp)}</b></p>'
            f'{fresh}</div>{CARD_CREDIT}</div>')
