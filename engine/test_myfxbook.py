"""Tests for the Myfxbook Community Outlook integration (myfxbook.py + its rendering).

    python test_myfxbook.py

Stdlib only, offline. A fake Myfxbook (login / outlook / logout) is injected as the opener,
and every build runs against a temporary data dir, so the suite never reaches Myfxbook and
never writes the live data/ directory. Covers parsing, missing symbols, malformed responses,
refused login, invalid session, API unavailable, the 25-minute / daily budget, keeping the
last good snapshot (never zeros), freshness labels, 0/100 and 50/50, the retail-crowd bands,
the most-populated symbol choice, the 100-position minimum (99/100/101 in list and card),
the 10 -> 11 column history upgrade, the safeguards (failure back-off, login lock and
its manual reset, archive retry spacing, the 24 h hide),
history storage, and that the credentials and session id never reach a file, the page or a
log line.
"""
import base64, contextlib, gzip, hashlib, io, json, os, sys, tempfile, urllib.error, urllib.parse
import datetime as dt
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import myfxbook as mf                                             # noqa: E402

PASS, FAIL = [], []
UTC = dt.timezone.utc
EMAIL, PASSWORD, SESSION = "trader.secret+x@example.com", "Pa$$ w0rd/=?&", "SeSsIoN9qXw7Zk2"
ON = {"MYFXBOOK_ENABLED": "true"}
ENV = {"MYFXBOOK_EMAIL": EMAIL, "MYFXBOOK_PASSWORD": PASSWORD, **ON}
# Render calls without an explicit env read os.environ: the enabled-path tests run with the
# switch on; t_switch passes explicit envs to test the off state.
os.environ["MYFXBOOK_ENABLED"] = "true"
T0 = dt.datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def _tmp():
    return Path(tempfile.mkdtemp(prefix="fxmfx-"))


def sym(name, lp, sp, lv=100.5, sv=80.25, lpos=1200, spos=900, alp=1.1, asp=1.2):
    return {"name": name, "longPercentage": lp, "shortPercentage": sp, "longVolume": lv,
            "shortVolume": sv, "longPositions": lpos, "shortPositions": spos,
            "totalPositions": lpos + spos, "avgLongPrice": alp, "avgShortPrice": asp}


GOOD = {"error": False, "message": "", "general": {"demoAccountsPercentage": 43},
        "symbols": [sym("EURUSD", 44, 56, alp=1.0832, asp=1.0911),
                    sym("XAUUSD", 61, 39, 512.3, 330.1, 4100, 2600, 2389.12, 2411.5),
                    sym("USDJPY", 0, 100, 0, 77.7, 0, 3000, 0, 151.234),
                    sym("GBPUSD", 50, 50),
                    sym("AUDUSD", "62", "38", "1,234.5", "998.75", "1,500", "700", "0.6512", "0.6633"),
                    sym("US500", 70, 30, alp=5612.4, asp=5655.1),
                    sym("SOMETHINGELSE", 55, 45),
                    {"name": "BROKEN1", "longPercentage": 90, "shortPercentage": 60},
                    {"name": "", "longPercentage": 50, "shortPercentage": 50},
                    sym("NANSYM", float("nan"), 50),
                    "not an object"]}


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class FakeMyfxbook:
    """login / outlook / logout. mode: ok | badlogin | badsession | down | outlook_down |
    garbage | notjson."""
    def __init__(self, mode="ok", outlook=None):
        self.mode, self.outlook, self.calls = mode, outlook if outlook is not None else GOOD, []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        path = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        self.calls.append((path, q))
        if self.mode == "down":
            raise urllib.error.URLError(f"connection refused to {url}")
        if path == "login.json":
            if q.get("email") != EMAIL or q.get("password") != PASSWORD:
                return _Resp(json.dumps({"error": True, "message": "bad args"}).encode())
            if self.mode == "badlogin":
                # a server that echoes the email back in its error must not leak it
                return _Resp(json.dumps({"error": True, "message": f"Invalid login for {EMAIL}",
                                         "session": ""}).encode())
            return _Resp(json.dumps({"error": False, "message": "", "session": SESSION}).encode())
        if path == "get-community-outlook.json":
            if q.get("session") != SESSION:
                return _Resp(json.dumps({"error": True, "message": "Invalid session"}).encode())
            if self.mode == "badsession":
                return _Resp(json.dumps({"error": True, "message": f"Invalid session {SESSION}"}).encode())
            if self.mode == "outlook_down":
                raise urllib.error.HTTPError(url, 503, f"unavailable {url}", {}, None)
            if self.mode == "notjson":
                return _Resp(b"<html>maintenance</html>")
            if self.mode == "garbage":
                return _Resp(json.dumps({"error": False, "symbols": "nope"}).encode())
            return _Resp(json.dumps(self.outlook).encode())
        if path == "logout.json":
            return _Resp(json.dumps({"error": False, "message": "Logged out."}).encode())
        raise AssertionError(path)


def run_build(data, now, fake, env=ENV, force=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        snap = mf.build(now=now, data=data, env=env, opener=fake, force=force)
    return snap, buf.getvalue()


# ---------------------------------------------------------------- tests
def t_parse():
    g = "parse"
    syms, rej = mf.parse(GOOD)
    check(g, "valid symbols kept", set(syms) == {"EURUSD", "XAUUSD", "USDJPY", "GBPUSD", "AUDUSD",
                                                "US500", "SOMETHINGELSE"}, sorted(syms))
    check(g, "invalid entries rejected, not repaired", len(rej) == 4, rej)
    a = syms["AUDUSD"]
    check(g, "numeric strings with thousands separators parsed",
          a["longVolume"] == 1234.5 and a["longPositions"] == 1500 and a["avgLongPrice"] == 0.6512, a)
    j = syms["USDJPY"]
    check(g, "0/100 kept exactly; zero average price -> None, not 0",
          j["longPercentage"] == 0 and j["shortPercentage"] == 100 and j["avgLongPrice"] is None, j)
    x = syms["XAUUSD"]
    check(g, "every field carried", (x["longVolume"], x["shortVolume"], x["longPositions"], x["shortPositions"],
                                     x["avgLongPrice"], x["avgShortPrice"]) == (512.3, 330.1, 4100, 2600, 2389.12, 2411.5), x)
    for bad, label in ((None, "null"), ([], "list"), ({"symbols": "x"}, "symbols not a list"),
                       ({"symbols": [{"name": "A", "longPercentage": 150, "shortPercentage": -50}]}, "all invalid")):
        try:
            mf.parse(bad)
            check(g, f"malformed ({label}) raises", False)
        except mf.MyfxbookError as e:
            check(g, f"malformed ({label}) raises 'malformed'", e.kind == "malformed", e.kind)


def t_match():
    g = "match"
    syms, _ = mf.parse(GOOD)
    m = mf.match(syms)
    check(g, "FX pairs and gold mapped by exact symbol",
          {k: v[0] for k, v in m.items() if k in ("EUR", "XAU", "JPY", "GBP", "AUD")} ==
          {"EUR": "EURUSD", "XAU": "XAUUSD", "JPY": "USDJPY", "GBP": "GBPUSD", "AUD": "AUDUSD"}, m.keys())
    check(g, "index CFD mapped to its future (ES <- US500)", m.get("ES", ("",))[0] == "US500")
    check(g, "missing symbols stay missing (no CAD/NZD/HG/WTI/ZB/USD)",
          not {"CAD", "NZD", "HG", "WTI", "ZB", "USD", "XAG"} & set(m), sorted(m))
    check(g, "unrelated symbols ignored", all(v[0] != "SOMETHINGELSE" for v in m.values()))
    check(g, "ZB has no candidate at all", mf.MARKETS["ZB"] == [])


def t_labels():
    g = "labels"
    cases = [((100, 0), "Heavy Long", 100), ((70, 30), "Heavy Long", 40), ((69, 31), "Long", 38),
             ((60, 40), "Long", 20), ((59, 41), "Balanced", 18), ((50, 50), "Balanced", 0),
             ((41, 59), "Balanced", 18), ((40, 60), "Short", 20), ((31, 69), "Short", 38),
             ((30, 70), "Heavy Short", 40), ((0, 100), "Heavy Short", 100), ((44, 55), "Balanced", 11)]
    for (lp, sp), want, imb in cases:
        check(g, f"{lp}/{sp} -> {want}, imbalance {imb}", mf.crowd(lp, sp) == want and mf.imbalance(lp, sp) == imb,
              (mf.crowd(lp, sp), mf.imbalance(lp, sp)))
    check(g, "freshness bands", [mf.freshness(m) for m in (0, 60, 61, 180, 181, None)] ==
          ["fresh", "fresh", "delayed", "delayed", "stale", "none"])
    b50, b100 = mf._bar(50, 50), mf._bar(0, 100)
    check(g, "50/50 bar is near neutral (weakest colour)", "--a:0.22" in b50, b50)
    check(g, "0/100 bar full strength, no negative or NaN width",
          "--a:1.0" in b100 and "width:0%" in b100 and "width:100%" in b100 and "nan" not in b100.lower(), b100)


def t_fetch():
    g = "fetch"
    fake = FakeMyfxbook()
    data = mf.fetch(EMAIL, PASSWORD, fake)
    check(g, "login -> outlook -> logout, in that order",
          [c[0] for c in fake.calls] == ["login.json", "get-community-outlook.json", "logout.json"], fake.calls)
    check(g, "session from login is used for outlook and logout",
          fake.calls[1][1].get("session") == SESSION and fake.calls[2][1].get("session") == SESSION)
    check(g, "returns the outlook payload", isinstance(data, dict) and isinstance(data.get("symbols"), list))
    for mode, kind in (("badlogin", "auth"), ("badsession", "session"), ("down", "unavailable"),
                       ("outlook_down", "unavailable"), ("notjson", "malformed")):
        fake = FakeMyfxbook(mode)
        try:
            mf.fetch(EMAIL, PASSWORD, fake)
            check(g, f"{mode} raises", False)
        except mf.MyfxbookError as e:
            msg = str(e)
            check(g, f"{mode} -> '{kind}'", e.kind == kind, (e.kind, msg))
            check(g, f"{mode}: error carries no email/password/session/URL",
                  all(s not in msg for s in (EMAIL, PASSWORD, SESSION, urllib.parse.quote_plus(EMAIL),
                                              urllib.parse.quote_plus(PASSWORD), "myfxbook.com/api")), msg)
        if mode in ("badsession", "outlook_down", "notjson"):
            check(g, f"{mode}: still logs out", fake.calls[-1][0] == "logout.json", fake.calls)
    try:
        mf.fetch("", "", FakeMyfxbook())
        check(g, "no credentials raises", False)
    except mf.MyfxbookError as e:
        check(g, "no credentials -> 'config', no request made", e.kind == "config")


def t_build_and_budget():
    g = "build"
    data = _tmp()
    snap, out = run_build(data, T0, FakeMyfxbook(), env=dict(ON))
    check(g, "not configured: no fetch, nothing invented", not snap.get("fetched_at") and
          snap["status"]["configured"] is False and "no fetch (not configured)" in out, (snap, out))
    html = mf.retail_panel(now=T0, data=data)
    check(g, "not configured: page says so", "not configured yet" in html)

    fake = FakeMyfxbook()
    snap, out = run_build(data, T0, fake)
    check(g, "first configured build fetches", snap.get("fetched_at") == "2026-10-14T09:00:00Z" and len(fake.calls) == 3, out)
    check(g, "only our markets stored, with the symbol used",
          set(snap["markets"]) == {"EUR", "XAU", "JPY", "GBP", "AUD", "ES"} and snap["markets"]["ES"]["symbol"] == "US500")
    check(g, "rejects recorded", len(snap["rejected"]) == 4)
    hist = mf.load_history(data)
    check(g, "history: one row per matched symbol, all ten fields",
          len(hist) == 6 and list(hist[0]) == mf.HIST_FIELDS and {r["symbol"] for r in hist} ==
          {"EURUSD", "XAUUSD", "USDJPY", "GBPUSD", "AUDUSD", "US500"}, hist[:1])
    check(g, "history stored by UTC day", (data / mf.HIST_DIR / "2026-10-14.csv").exists())

    fake2 = FakeMyfxbook()
    snap, out = run_build(data, T0 + dt.timedelta(minutes=20), fake2)
    check(g, "within 25 min of the last attempt: no request", fake2.calls == [] and "no fetch" in out, out)
    snap, out = run_build(data, T0 + dt.timedelta(minutes=26), fake2)
    check(g, "after 25 min: fetches again", len(fake2.calls) == 3 and snap["fetched_at"].startswith("2026-10-14T09:26"))
    check(g, "history appends", len(mf.load_history(data)) == 12)
    check(g, "history 'since' filter", len(mf.load_history(data, since="2026-10-14T09:10:00Z")) == 6)

    # budget: fill the rolling 24 h with attempts
    st = json.loads((data / mf.STATE_NAME).read_text())
    tb = T0 + dt.timedelta(hours=2)
    st["attempts"] = [mf._iso(tb - dt.timedelta(minutes=30 + 20 * i)) for i in range(mf.DAILY_BUDGET)]
    (data / mf.STATE_NAME).write_text(json.dumps(st))
    fake3 = FakeMyfxbook()
    snap, out = run_build(data, tb, fake3)
    check(g, f"{mf.DAILY_BUDGET} attempts in 24 h: no request", fake3.calls == [] and "daily budget" in out, out)
    snap, out = run_build(data, tb + dt.timedelta(hours=4), fake3)
    check(g, "old attempts age out of the 24 h window", len(fake3.calls) == 3, out)


def t_failures_keep_snapshot():
    g = "failure"
    for mode in ("down", "outlook_down", "garbage", "notjson", "badsession"):
        data = _tmp()
        run_build(data, T0, FakeMyfxbook())
        before = json.loads((data / mf.OUT_NAME).read_text())["markets"]
        snap, out = run_build(data, T0 + dt.timedelta(minutes=30), FakeMyfxbook(mode))
        check(g, f"{mode}: last valid snapshot kept unchanged, never zeros",
              snap["markets"] == before and snap["fetched_at"] == "2026-10-14T09:00:00Z", snap["status"])
        check(g, f"{mode}: error recorded", snap["status"]["last_error"] in ("unavailable", "malformed", "session"),
              snap["status"])
        html = mf.retail_panel(now=T0 + dt.timedelta(minutes=30), data=data)
        check(g, f"{mode}: page marks 'Last known Myfxbook data - stale' and keeps the figures",
              "Last known Myfxbook data &mdash; stale" in html and "LONG 61%" in html, mode)
        card = mf.report_card("XAU", now=T0 + dt.timedelta(minutes=30), data=data)
        check(g, f"{mode}: report card also marked", "Last known Myfxbook data &mdash; stale" in card)
        snap, out = run_build(data, T0 + dt.timedelta(minutes=60), FakeMyfxbook())
        check(g, f"{mode}: recovery clears the warning", snap["status"]["last_error"] is None and
              "Last known" not in mf.retail_panel(now=T0 + dt.timedelta(minutes=60), data=data))
    data = _tmp()
    snap, out = run_build(data, T0, FakeMyfxbook("badlogin"))
    check(g, "refused login: no data, reason shown", not snap.get("fetched_at") and snap["status"]["last_error"] == "auth")
    fake = FakeMyfxbook()
    snap, out = run_build(data, T0 + dt.timedelta(hours=1), fake)
    check(g, "refused login backs off (no login retry for 6 h)", fake.calls == [] and "login refused earlier" in out, out)
    snap, out = run_build(data, T0 + dt.timedelta(hours=6, minutes=1), fake)
    check(g, "after the back-off it tries again", len(fake.calls) == 3 and snap.get("fetched_at"))


def t_render():
    g = "render"
    data = _tmp()
    run_build(data, T0, FakeMyfxbook())
    for age, want in ((8, "8 minutes"), (75, "Delayed"), (200, "Stale")):
        html = mf.retail_panel(now=T0 + dt.timedelta(minutes=age), data=data)
        check(g, f"age {age} min shows '{want}'", want in html and "Updated: 14 Oct 2026 09:00 UTC" in html)
    html = mf.retail_panel(now=T0 + dt.timedelta(minutes=8), data=data)
    check(g, "section title and attribution with link",
          "Retail sentiment &mdash; Myfxbook" in html and "Retail sentiment data:" in html
          and f'href="{mf.SOURCE_URL}"' in html)
    check(g, "all 16 markets listed once", html.count('data-mfx-key="') == 16, html.count('data-mfx-key="'))
    check(g, "markets without data say 'Retail sentiment unavailable' (10 of 16)",
          html.count("Retail sentiment unavailable") == 10, html.count("Retail sentiment unavailable"))
    check(g, "XAUUSD: long/short %, imbalance, crowd",
          "LONG 61%" in html and "SHORT 39%" in html and "Imbalance 22" in html and "Retail crowd: <b>Long</b>" in html)
    check(g, "XAUUSD: volume, positions and avg prices shown",
          "L 512.30" in html and "S 330.10" in html and "L 4,100" in html and "S 2,600" in html
          and "L 2,389.12" in html and "S 2,411.5<" in html)
    check(g, "USDJPY 0/100: Heavy Short, imbalance 100, missing avg price is a dash not 0",
          "LONG 0%" in html and "SHORT 100%" in html and "Imbalance 100" in html and "Heavy Short" in html
          and "L &mdash; &middot; S 151.234" in html)
    check(g, "GBPUSD 50/50: Balanced, imbalance 0", "Imbalance 0<" in html and "Balanced" in html)
    check(g, "each market links to its 50-week report", 'href="#cot50-XAU"' in html and 'href="#cot50-ZB"' in html)
    check(g, "no buy/sell wording", not any(w in html.lower() for w in ("buy signal", "sell signal", ">buy<", ">sell<")))
    check(g, "live-age script included once", html.count("data-mfx-ts") >= 1 and html.count("<script>") == 1)
    card = mf.report_card("XAU", now=T0 + dt.timedelta(minutes=8), data=data)
    check(g, "report card: Long/Short/Imbalance/crowd + source",
          "Long 61% &middot; Short 39% &middot; Imbalance 22" in card and "Retail crowd: <b>Long</b>" in card
          and "XAUUSD" in card and mf.SOURCE_NAME in card)
    check(g, "report card for a market without data", "Retail sentiment unavailable" in mf.report_card("ZB", data=data))
    check(g, "report card before any fetch", "No Myfxbook data yet" in mf.report_card("XAU", data=_tmp()))


def t_secrets():
    g = "secrets"
    data, logs, pages = _tmp(), [], []
    t = T0
    for mode in ("ok", "badsession", "down", "outlook_down", "notjson", "garbage", "ok", "badlogin"):
        t += dt.timedelta(minutes=30)
        if mode == "badlogin":
            data = _tmp()
        snap, out = run_build(data, t, FakeMyfxbook(mode))
        logs.append(out)
        pages.append(mf.retail_panel(now=t, data=data) + "".join(mf.report_card(k, now=t, data=data) for k in mf.MARKETS))
    blobs = logs + pages + [p.read_text(errors="replace") for p in data.rglob("*") if p.is_file()]
    forms = {EMAIL, PASSWORD, SESSION, urllib.parse.quote(EMAIL, safe=""), urllib.parse.quote_plus(EMAIL),
             urllib.parse.quote(PASSWORD, safe=""), urllib.parse.quote_plus(PASSWORD)}
    leaks = [f for f in forms if any(f in b for b in blobs)]
    check(g, "email, password and session never in any log line, page or stored file", not leaks, leaks)
    check(g, "the refused-login message was scrubbed, not dropped", "[redacted]" in "".join(logs))


TOKEN = "github_pat_ARCHIVEtokenXYZ123"
AENV = {**ENV, "MYFXBOOK_HISTORY_TOKEN": TOKEN, "MYFXBOOK_HISTORY_REPO": "me/fx-data"}


class FakeGitHub:
    """GET/PUT /repos/{repo} and /repos/{repo}/contents/{path}. mode: ok | public | missing | down."""
    def __init__(self, mode="ok"):
        self.mode, self.files, self.calls = mode, {}, []

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        path = urllib.parse.urlparse(url).path
        self.calls.append((method, path))
        if req.get_header("Authorization") != f"Bearer {TOKEN}":
            raise urllib.error.HTTPError(url, 401, "bad token", {}, None)
        if self.mode == "down":
            raise urllib.error.URLError("no route")
        if path == "/repos/me/fx-data":
            if self.mode == "missing":
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            return _Resp(json.dumps({"private": self.mode != "public"}).encode())
        fp = path.split("/contents/", 1)[1]
        if method == "GET":
            if fp in self.files:
                c, sha = self.files[fp]
                return _Resp(json.dumps({"content": c, "sha": sha}).encode())
            kids = sorted({k[len(fp) + 1:].split("/")[0] for k in self.files if k.startswith(fp + "/")})
            if kids:
                return _Resp(json.dumps([{"name": k, "type": "file" if k.endswith(".gz") else "dir"} for k in kids]).encode())
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        body = json.loads(req.data)
        if fp in self.files and body.get("sha") != self.files[fp][1]:
            raise urllib.error.HTTPError(url, 409, "sha mismatch", {}, None)
        self.files[fp] = (body["content"], hashlib.sha1(body["content"].encode()).hexdigest())
        return _Resp(b'{"content": {}}')

    def rows(self, day):
        c, _ = self.files[f"myfxbook/daily/{day[:4]}/{day}.csv.gz"]
        return mf._day_rows(gzip.decompress(base64.b64decode(c)).decode())


def run_archive(data, now, gh, env=AENV, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        a = mf.archive(now=now, data=data, env=env, opener=gh, **kw)
    return a, buf.getvalue()


def t_archive():
    g = "archive"
    data, gh = _tmp(), FakeGitHub()
    a, out = run_archive(data, T0, gh, env=ENV)
    check(g, "no archive token: nothing sent, says so", gh.calls == [] and "not configured" in out and a["configured"] is False, out)
    run_build(data, T0, FakeMyfxbook())
    run_build(data, T0 + dt.timedelta(minutes=30), FakeMyfxbook())
    a, out = run_archive(data, T0 + dt.timedelta(minutes=31), gh)
    check(g, "first push writes today's file to the private repo", list(gh.files) == ["myfxbook/daily/2026/2026-10-14.csv.gz"]
          and a["last_error"] is None, (list(gh.files), out))
    check(g, "archived file = the local day rows (12), gzip CSV with the 11 columns",
          len(gh.rows("2026-10-14")) == 12 and len(gh.rows("2026-10-14")[0]) == 11)
    n = len(gh.calls)
    a, out = run_archive(data, T0 + dt.timedelta(hours=2), gh)
    check(g, "within 6 h: no GitHub call", len(gh.calls) == n and "next after 6 h" in out, out)
    run_build(data, T0 + dt.timedelta(hours=3), FakeMyfxbook())
    a, out = run_archive(data, T0 + dt.timedelta(hours=7), gh)
    check(g, "after 6 h: changed day file pushed again with the new rows", len(gh.rows("2026-10-14")) == 18, out)
    n = len(gh.calls)
    a, out = run_archive(data, T0 + dt.timedelta(hours=14), gh)
    check(g, "nothing new: no write (only the repo check)", [c for c in gh.calls[n:] if c[0] == "PUT"] == [] and "nothing new" in out, out)

    # cache eviction: a fresh runner with an empty data dir must not shrink the archive
    data2 = _tmp()
    run_build(data2, T0 + dt.timedelta(hours=14), FakeMyfxbook())
    a, out = run_archive(data2, T0 + dt.timedelta(hours=14), gh)
    check(g, "after cache loss the archive keeps every earlier row and adds the new ones",
          len(gh.rows("2026-10-14")) == 24, len(gh.rows("2026-10-14")))
    rows = mf.archive_load(env=AENV, opener=gh)
    check(g, "archive_load reads the history back, oldest first, as dicts",
          len(rows) == 24 and rows[0]["timestamp"] <= rows[-1]["timestamp"] and set(rows[0]) == set(mf.HIST_FIELDS))

    # a new day goes to a new file
    run_build(data2, T0 + dt.timedelta(days=1), FakeMyfxbook())
    a, out = run_archive(data2, T0 + dt.timedelta(days=1, hours=1), gh)
    check(g, "next UTC day -> its own file", "myfxbook/daily/2026/2026-10-15.csv.gz" in gh.files, list(gh.files))

    # old local files are dropped only once archived
    old = data2 / mf.HIST_DIR / "2026-08-01.csv"
    old.write_text(mf._day_text([["2026-08-01T00:00:00Z", "EURUSD"] + ["1"] * 8 + ["EUR"]]))
    run_archive(data2, T0 + dt.timedelta(days=2), gh, force=True)
    check(g, f"local day files older than {mf.LOCAL_KEEP_DAYS} days dropped after archiving",
          not old.exists() and "myfxbook/daily/2026/2026-08-01.csv.gz" in gh.files)

    for mode, kind in (("public", "public"), ("missing", "missing"), ("down", "github")):
        d3, gh3 = _tmp(), FakeGitHub(mode)
        run_build(d3, T0, FakeMyfxbook())
        a, out = run_archive(d3, T0, gh3)
        check(g, f"{mode} repo: nothing written, error '{kind}', local history kept",
              not [c for c in gh3.calls if c[0] == "PUT"] and a["last_error"] == kind
              and len(mf.load_history(d3)) == 6, (a, out))
        check(g, f"{mode}: token not in the message", TOKEN not in out and TOKEN not in json.dumps(a))
    d4, gh4 = _tmp(), FakeGitHub()
    a, out = run_archive(d4, T0, gh4, verify_only=True)
    check(g, "CI verify: proves write access with a no-data file only",
          list(gh4.files) == ["myfxbook/_ci_write_check.json"] and "private and writable" in out, (list(gh4.files), out))
    d5, gh5 = _tmp(), FakeGitHub("public")
    a, out = run_archive(d5, T0, gh5, verify_only=True)
    check(g, "CI verify refuses a public repo", gh5.files == {} and a["last_error"] == "public")
    st = (data2 / mf.STATE_NAME).read_text()
    check(g, "token never stored", TOKEN not in st)


def t_probe():
    g = "probe"
    tmpd = _tmp()
    probe = tmpd / "probe.txt"
    data = _tmp()
    snap, out = run_build(data, T0, FakeMyfxbook(), env={**ENV, "MYFXBOOK_LEAKSCAN_FILE": str(probe)})
    check(g, "leak probe written only when asked, after logout, mode 600",
          probe.read_text() == SESSION and oct(probe.stat().st_mode)[-3:] == "600")
    inrepo = Path(__file__).resolve().parent / "data" / "probe_should_not_exist.txt"
    run_build(_tmp(), T0, FakeMyfxbook(), env={**ENV, "MYFXBOOK_LEAKSCAN_FILE": str(inrepo)})
    check(g, "leak probe refused inside the repo", not inrepo.exists())
    d2 = _tmp()
    run_build(d2, T0, FakeMyfxbook())
    check(g, "no probe file without the CI variable", not any("probe" in p.name for p in d2.rglob("*")))


# ---------------------------------------------------------------- symbol choice
def t_select():
    g = "select"
    cands = {"XAUUSD": 30, "GOLD": 500}
    outlook = {"symbols": [sym(n, 55, 45, lpos=k, spos=0) for n, k in cands.items()]
               + [sym("WTI", 50, 50, lpos=40, spos=0), sym("USOIL", 50, 50, lpos=40, spos=0),
                  sym("XTIUSD", 50, 50, lpos=10, spos=5), sym("BRENT", 50, 50, lpos=9000, spos=0),
                  sym("USDOLLAR", 50, 50, lpos=9000, spos=0)]}
    syms, _ = mf.parse(outlook)
    m = mf.match(syms)
    check(g, "most-populated exact candidate wins (GOLD 500 over XAUUSD 30)",
          m["XAU"][0] == "GOLD" and m["XAU"][1]["candidates"] == ["XAUUSD", "GOLD"], m.get("XAU"))
    check(g, "a tie goes to the earlier candidate (WTI before USOIL)", m["WTI"][0] == "WTI", m.get("WTI"))
    check(g, "candidates present are recorded in MARKETS order",
          m["WTI"][1]["candidates"] == ["WTI", "USOIL", "XTIUSD"], m["WTI"][1]["candidates"])
    check(g, "a bigger but different market is never used (no BRENT for WTI, no USDOLLAR for USD)",
          "USD" not in m and all(v[0] not in ("BRENT", "USDOLLAR") for v in m.values()), sorted(m))
    check(g, "every candidate belongs to exactly one market",
          len({c for cs in mf.MARKETS.values() for c in cs}) == sum(len(cs) for cs in mf.MARKETS.values()))
    check(g, "market_of() reverses the mapping", mf.market_of("GOLD") == "XAU" and mf.market_of("us500") == "ES"
          and mf.market_of("BRENT") == "")
    data = _tmp()
    snap, out = run_build(data, T0, FakeMyfxbook(outlook=outlook))
    check(g, "the snapshot stores the chosen symbol", snap["markets"]["XAU"]["symbol"] == "GOLD", snap["markets"].get("XAU"))
    hist = mf.load_history(data)
    check(g, "history records market AND source symbol",
          {(r["market"], r["symbol"]) for r in hist} == {("XAU", "GOLD"), ("WTI", "WTI")}, hist)
    check(g, "log names the alternatives considered", "XAU<-GOLD (of XAUUSD/GOLD)" in out, out)


# ---------------------------------------------------------------- minimum sample
def _one(n_long, n_short, lp=64, sp=36):
    return {"symbols": [sym("XAUUSD", lp, sp, lpos=n_long, spos=n_short),
                        sym("EURUSD", 44, 56)]}


def t_threshold():
    g = "threshold"
    check(g, "minimum is 100 positions", mf.MIN_POSITIONS == 100)
    # boundaries 99 / 100 / 101, both sides contributing, one side only, and empty
    for n_long, n_short in ((60, 39), (60, 40), (60, 41), (99, 0), (0, 100), (1, 0), (0, 0)):
        n = n_long + n_short
        shown = n >= 100
        lp, sp = (0, 100) if n_short and not n_long else (64, 36)
        data = _tmp()
        run_build(data, T0, FakeMyfxbook(outlook=_one(n_long, n_short, lp, sp)))
        t = T0 + dt.timedelta(minutes=5)
        html = mf.retail_panel(now=t, data=data)
        row = html.split('data-mfx-key="XAU"', 1)[1].split('data-mfx-key="XAG"', 1)[0]
        card = mf.report_card("XAU", now=t, data=data)
        msg = f"Too few Myfxbook positions (n = {n:,}) &mdash; no reading"
        reading = ("LONG", "SHORT", "Imbalance", "Retail crowd", "mfxbar", f"{lp}%")
        if shown:
            check(g, f"n = {n}: reading shown in the market list", f"LONG {lp}%" in row and "Retail crowd" in row
                  and "Too few" not in row, row)
            check(g, f"n = {n}: reading shown in the report card", f"Long {lp}%" in card and "Retail crowd" in card
                  and "Too few" not in card, card)
        else:
            check(g, f"n = {n}: market list says too few, no %, bar, imbalance or crowd",
                  msg in row and not any(w in row for w in reading), row)
            check(g, f"n = {n}: report card says too few, no %, bar, imbalance or crowd",
                  msg in card and not any(w in card for w in reading) and "XAUUSD" in card, card)
        check(g, f"n = {n}: still archived (history row written)",
              [(r["market"], r["symbol"], r["longPositions"], r["shortPositions"])
               for r in mf.load_history(data) if r["market"] == "XAU"] == [("XAU", "XAUUSD", str(n_long), str(n_short))],
              mf.load_history(data))
        check(g, f"n = {n}: other markets unaffected", "LONG 44%" in html)
    data = _tmp()
    run_build(data, T0, FakeMyfxbook(outlook=_one(99, 0)))
    run_build(data, T0 + dt.timedelta(minutes=30), FakeMyfxbook("down"))
    card = mf.report_card("XAU", now=T0 + dt.timedelta(minutes=30), data=data)
    check(g, "below the minimum + failed refresh: still no reading, marked stale",
          "n = 99" in card and "Last known Myfxbook data &mdash; stale" in card and "LONG" not in card, card)
    snap, out = run_build(_tmp(), T0, FakeMyfxbook(outlook=_one(99, 0)))
    check(g, "log lists below-minimum markets by name, no figures",
          "below 100 positions (archived, not shown): XAU" in out and "99" not in out, out)
    html = mf.retail_panel(now=T0, data=data)
    check(g, "legend states the minimum", "at least 100 open Myfxbook positions" in html)


# ---------------------------------------------------------------- history format upgrade
def t_history_upgrade():
    g = "history-upgrade"
    v1 = ",".join(mf.HIST_FIELDS_V1) + "\n" + "2026-10-14T05:00:00Z,XAUUSD,61,39,1,2,3,4,5,6\n"
    data = _tmp()
    d = data / mf.HIST_DIR
    d.mkdir(parents=True)
    (d / "2026-10-14.csv").write_text(v1)
    run_build(data, T0, FakeMyfxbook())
    text = (d / "2026-10-14.csv").read_text()
    rows = mf.load_history(data)
    check(g, "a 10-column local file is upgraded before appending (one header, 11 columns)",
          text.startswith(",".join(mf.HIST_FIELDS) + "\n") and text.count("timestamp") == 1
          and all(len(r) == 11 for r in mf._day_rows(text)), text[:300])
    check(g, "the old row keeps its values and gains its market",
          rows[0]["timestamp"] == "2026-10-14T05:00:00Z" and rows[0]["market"] == "XAU"
          and rows[0]["avgShortPrice"] == "6", rows[0])
    gh = FakeGitHub()
    path = "myfxbook/daily/2026/2026-10-14.csv.gz"
    gh.files[path] = (base64.b64encode(mf._gz(v1 + "2026-10-14T04:00:00Z,EURUSD,44,56,1,2,3,4,5,6\n")).decode(), "sha0")
    a, out = run_archive(data, T0 + dt.timedelta(minutes=1), gh)
    arch = gh.rows("2026-10-14")
    check(g, "a 10-column archive file is merged, not overwritten: every archived row kept",
          a["last_error"] is None and len(arch) == 8
          and any(r[0] == "2026-10-14T04:00:00Z" and r[1] == "EURUSD" and r[10] == "EUR" for r in arch), (a, arch))
    check(g, "the archive file is rewritten with the 11-column header",
          gzip.decompress(base64.b64decode(gh.files[path][0])).decode().startswith(",".join(mf.HIST_FIELDS)))
    gh2 = FakeGitHub()
    gh2.files[path] = (base64.b64encode(mf._gz("something,else\n1,2\n")).decode(), "sha0")
    before = gh2.files[path]
    d2 = _tmp()
    run_build(d2, T0, FakeMyfxbook())
    a, out = run_archive(d2, T0 + dt.timedelta(minutes=1), gh2)
    check(g, "an unreadable archive file is never overwritten (error 'format')",
          a["last_error"] == "format" and gh2.files[path] == before, (a, out))


def t_switch():
    g = "switch"
    creds = {"MYFXBOOK_EMAIL": EMAIL, "MYFXBOOK_PASSWORD": PASSWORD, "MYFXBOOK_HISTORY_TOKEN": TOKEN,
             "MYFXBOOK_HISTORY_REPO": "me/fx-data"}
    for v in (None, "", "false", "False", "TRUE", "True", "1", "yes", "on", " true", "true "):
        env = dict(creds) if v is None else {**creds, "MYFXBOOK_ENABLED": v}
        check(g, f"MYFXBOOK_ENABLED={v!r} -> off", mf.enabled(env) is False)
    check(g, "MYFXBOOK_ENABLED='true' -> on", mf.enabled({**creds, "MYFXBOOK_ENABLED": "true"}) is True)

    # a data dir that already holds a real-looking snapshot, state and history
    data = _tmp()
    run_build(data, T0, FakeMyfxbook())
    run_archive(data, T0, FakeGitHub(), env=AENV, force=True)
    before = {p.relative_to(data): p.read_bytes() for p in data.rglob("*") if p.is_file()}
    for v in (None, "false", "TRUE", "1"):
        env = dict(creds) if v is None else {**creds, "MYFXBOOK_ENABLED": v}
        mfx, gh = FakeMyfxbook(), FakeGitHub()
        probe = _tmp() / "probe"
        snap, out = run_build(data, T0 + dt.timedelta(hours=2), mfx, env={**env, "MYFXBOOK_LEAKSCAN_FILE": str(probe)})
        a, out2 = run_archive(data, T0 + dt.timedelta(hours=8), gh, env=env, force=True)
        a3, out3 = run_archive(data, T0 + dt.timedelta(hours=8), gh, env=env, verify_only=True)
        check(g, f"off ({v!r}): no login, no outlook, no logout", mfx.calls == [], mfx.calls)
        check(g, f"off ({v!r}): no archive read or write, no CI write check", gh.calls == [], gh.calls)
        check(g, f"off ({v!r}): says so in the log", "switched off" in out and "switched off" in out2 and "switched off" in out3)
        check(g, f"off ({v!r}): no session probe", not probe.exists())
        after = {p.relative_to(data): p.read_bytes() for p in data.rglob("*") if p.is_file()}
        check(g, f"off ({v!r}): cached snapshot, state and history files untouched", after == before,
              sorted(set(after) ^ set(before)))
        html = mf.retail_panel(now=T0, data=data, env=env)
        cards = "".join(mf.report_card(k, now=T0, data=data, env=env) for k in mf.MARKETS)
        check(g, f"off ({v!r}): tab says switched off and shows no Myfxbook figure, even cached",
              "switched off" in html and not re.search(r"LONG|SHORT|Imbalance|Retail crowd|mfxbar|data-mfx-key|XAUUSD|data-mfx-ts", html), html[:200])
        check(g, f"off ({v!r}): no card in any report", cards == "")
    # the private archive keeps what it had
    gh = FakeGitHub()
    run_archive(_tmp(), T0, gh, env=AENV, force=True)
    stored = dict(gh.files)
    run_archive(_tmp(), T0 + dt.timedelta(days=1), gh, env={k: v for k, v in AENV.items() if k != "MYFXBOOK_ENABLED"}, force=True)
    check(g, "off: archived history in the private repo unchanged", gh.files == stored)

    # switched back on: the normal path, from the same data dir
    mfx, gh = FakeMyfxbook(), FakeGitHub()
    on = {**creds, "MYFXBOOK_ENABLED": "true"}
    snap, out = run_build(data, T0 + dt.timedelta(hours=3), mfx, env=on)
    a, out2 = run_archive(data, T0 + dt.timedelta(hours=3), gh, env=on, force=True)
    check(g, "on: login -> outlook -> logout", [c[0] for c in mfx.calls] == ["login.json", "get-community-outlook.json", "logout.json"])
    check(g, "on: archive written", a.get("last_error") is None and any(k.endswith(".csv.gz") for k in gh.files), a)
    html = mf.retail_panel(now=T0 + dt.timedelta(hours=3), data=data, env=on)
    check(g, "on: readings shown again", "LONG 61%" in html and "Imbalance 22" in html and "switched off" not in html)
    check(g, "on: report cards back", "Imbalance 22" in mf.report_card("XAU", now=T0 + dt.timedelta(hours=3), data=data, env=on))


def t_attribution():
    g = "attribution"
    link = ('Retail sentiment data: <a href="https://www.myfxbook.com/community/outlook" target="_blank" '
            'rel="noopener">Myfxbook Community Outlook</a>')
    empty = _tmp()
    check(g, "card before any fetch credits Myfxbook with a link", link in mf.report_card("XAU", data=empty))
    data = _tmp()
    run_build(data, T0, FakeMyfxbook())
    html = mf.retail_panel(now=T0, data=data)
    check(g, "tab credits Myfxbook Community Outlook with a link", link in html)
    cards = {k: mf.report_card(k, now=T0, data=data) for k in mf.MARKETS}
    check(g, "every card (reading, unavailable) credits Myfxbook with a link",
          all(link in c for c in cards.values()), [k for k, c in cards.items() if link not in c])
    thin = json.loads((data / mf.OUT_NAME).read_text())
    k0 = next(iter(thin["markets"]))
    thin["markets"][k0].update(longPositions=3, shortPositions=1)
    (data / mf.OUT_NAME).write_text(json.dumps(thin))
    check(g, "below-minimum card credits Myfxbook with a link", link in mf.report_card(k0, now=T0, data=data))


def _st(data):
    return json.loads((data / mf.STATE_NAME).read_text())


def t_fail_backoff():
    g = "fail-backoff"
    check(g, "constants: back-off after 3 failures, 1 h -> 2 h -> 4 h -> 6 h cap",
          mf.FAIL_BACKOFF_AFTER == 3 and mf.FAIL_BACKOFF_H == (1, 2, 4, 6) and
          [mf._fail_delay_h(n) for n in range(3, 10)] == [1, 2, 4, 6, 6, 6, 6])
    data = _tmp()
    run_build(data, T0, FakeMyfxbook())
    t = T0
    for i in (1, 2, 3):                                        # 3 failures, 30 min apart: normal cadence
        t += dt.timedelta(minutes=30)
        fake = FakeMyfxbook("down")
        snap, out = run_build(data, t, fake)
        check(g, f"failure {i}: attempted on the normal 25-min cadence", len(fake.calls) == 1, out)
    check(g, "3 failures counted", _st(data).get("consecutive_failures") == 3, _st(data))
    check(g, "snapshot kept through the failures", snap["fetched_at"] == "2026-10-14T09:00:00Z")
    # 1 h, 2 h, 4 h, 6 h, 6 h: blocked one minute before, attempted one minute after
    for n, h in ((3, 1), (4, 2), (5, 4), (6, 6), (7, 6)):
        fake = FakeMyfxbook("down")
        snap, out = run_build(data, t + dt.timedelta(hours=h) - dt.timedelta(minutes=1), fake)
        check(g, f"after {n} failures: no request before {h} h", fake.calls == [] and "failed fetches in a row" in out
              and f"next attempt {h} h" in out, out)
        snap, out = run_build(data, t + dt.timedelta(hours=h) - dt.timedelta(minutes=1), fake, force=True)
        check(g, f"after {n} failures: force (manual run) does not bypass the back-off", fake.calls == [], out)
        t += dt.timedelta(hours=h, minutes=1)
        snap, out = run_build(data, t, fake)
        check(g, f"after {n} failures: tries again after {h} h", len(fake.calls) == 1 and
              _st(data)["consecutive_failures"] == n + 1, out)
    check(g, "cap: never more than 6 h", mf._fail_delay_h(50) == 6)
    # success resets the counter and the normal 25-min cadence returns
    t += dt.timedelta(hours=6, minutes=1)
    fake = FakeMyfxbook()
    snap, out = run_build(data, t, fake)
    check(g, "success after the back-off fetches and resets the counter",
          len(fake.calls) == 3 and "consecutive_failures" not in _st(data) and snap["status"]["last_error"] is None)
    fake = FakeMyfxbook("down")
    run_build(data, t + dt.timedelta(minutes=26), fake)
    fake2 = FakeMyfxbook("down")
    run_build(data, t + dt.timedelta(minutes=52), fake2)
    check(g, "after the reset, failures 1-2 are retried on the normal 25-min cadence",
          len(fake.calls) == 1 and len(fake2.calls) == 1 and _st(data)["consecutive_failures"] == 2)
    # every non-credential failure kind counts; a refused login does not
    for mode in ("outlook_down", "garbage", "notjson", "badsession"):
        d = _tmp()
        run_build(d, T0, FakeMyfxbook(mode))
        check(g, f"{mode} counts as a failed fetch", _st(d).get("consecutive_failures") == 1, _st(d))
    d = _tmp()
    run_build(d, T0, FakeMyfxbook("badlogin"))
    check(g, "a refused login is not a fetch failure (it has its own rule)", "consecutive_failures" not in _st(d))
    check(g, "daily budget and 25-min gap unchanged", mf.DAILY_BUDGET == 60 and mf.MIN_GAP_MIN == 25)


def t_login_lock():
    g = "login-lock"
    data = _tmp()
    snap, out = run_build(data, T0, FakeMyfxbook("badlogin"))
    st = _st(data)
    check(g, "1st rejection: 6 h wait, not locked", st.get("auth_rejections") == 1 and "auth_locked_at" not in st
          and st.get("auth_backoff_until") == "2026-10-14T15:00:00Z", st)
    fake = FakeMyfxbook("badlogin")
    run_build(data, T0 + dt.timedelta(hours=5, minutes=59), fake)
    check(g, "no login during the 6 h wait", fake.calls == [])
    t_lock = T0 + dt.timedelta(hours=6, minutes=1)
    snap, out = run_build(data, t_lock, fake)
    st = _st(data)
    lock = "2026-10-14T15:01:00Z"
    check(g, "2nd rejection: login LOCKED, lock time recorded", st.get("auth_locked_at") == lock
          and st.get("auth_rejections") == 2 and len(fake.calls) == 1, st)
    check(g, "log says LOCKED and prints exactly how to reset",
          "login LOCKED" in out and f"{mf.RESET_VAR} to {lock}" in out, out)
    check(g, "page shows the stop reason", "login stopped - credentials rejected twice" in
          (snap["status"].get("last_error_msg") or ""), snap["status"])
    calls = []
    for h, kw, label in ((7, {}, "7 h later"), (30, {}, "30 h later"), (200, {}, "8 days later"),
                         (31, {"force": True}, "a forced/manual run")):
        f = FakeMyfxbook()
        _, out = run_build(data, T0 + dt.timedelta(hours=h), f, **kw)
        calls += f.calls
        check(g, f"locked: no login {label}", f.calls == [] and "login stopped" in out, out)
    for bad in ("true", "1", "2026-10-14T09:00:00Z", "", lock[:-1]):
        f = FakeMyfxbook()
        run_build(data, T0 + dt.timedelta(hours=40), f, env={**ENV, mf.RESET_VAR: bad})
        check(g, f"locked: reset value {bad!r} (not the lock time) does not unlock", f.calls == [] and _st(data).get("auth_locked_at") == lock)
    f = FakeMyfxbook()
    snap, out = run_build(data, T0 + dt.timedelta(hours=41), f, env={**ENV, mf.RESET_VAR: f" {lock}\n"})
    st = _st(data)
    check(g, "reset with the printed lock time: lock cleared, one login, data fetched",
          len(f.calls) == 3 and snap.get("fetched_at") and not any(k in st for k in
          ("auth_locked_at", "auth_rejections", "auth_backoff_until")) and "cleared" in out, (st, out))
    # the old reset value left in place cannot clear a later lock
    run_build(data, T0 + dt.timedelta(hours=42), FakeMyfxbook("badlogin"), env={**ENV, mf.RESET_VAR: lock})
    check(g, "after a reset, a new rejection starts at 1 again (wait, not lock)",
          _st(data).get("auth_rejections") == 1 and "auth_locked_at" not in _st(data), _st(data))
    run_build(data, T0 + dt.timedelta(hours=48, minutes=1), FakeMyfxbook("badlogin"), env={**ENV, mf.RESET_VAR: lock})
    lock2 = _st(data).get("auth_locked_at")
    check(g, "a later lock gets a new time; the stale reset value does not unlock it",
          lock2 == "2026-10-16T09:01:00Z" and lock2 != lock)
    f = FakeMyfxbook()
    run_build(data, T0 + dt.timedelta(hours=60), f, env={**ENV, mf.RESET_VAR: lock})
    check(g, "old reset value: still locked, no login", f.calls == [] and _st(data).get("auth_locked_at") == lock2)
    # a success between two rejections resets the count
    d = _tmp()
    run_build(d, T0, FakeMyfxbook("badlogin"))
    run_build(d, T0 + dt.timedelta(hours=6, minutes=1), FakeMyfxbook())
    check(g, "a successful login clears the rejection count", "auth_rejections" not in _st(d), _st(d))
    run_build(d, T0 + dt.timedelta(hours=7), FakeMyfxbook("badlogin"))
    check(g, "... so the next rejection only waits, it does not lock", "auth_locked_at" not in _st(d) and _st(d)["auth_rejections"] == 1)
    # network errors are not credential rejections
    d = _tmp()
    run_build(d, T0, FakeMyfxbook("badlogin"))
    for i, mode in enumerate(("down", "outlook_down", "badsession")):
        run_build(d, T0 + dt.timedelta(hours=6, minutes=1 + 30 * i), FakeMyfxbook(mode))
    check(g, "network / API errors do not count as rejections and do not lock",
          _st(d).get("auth_rejections") == 1 and "auth_locked_at" not in _st(d), _st(d))
    check(g, "constants: lock after 2, 6 h wait after the first", mf.AUTH_LOCK_AFTER == 2 and mf.AUTH_BACKOFF_H == 6)


def t_archive_spacing():
    g = "archive-spacing"
    data, gh = _tmp(), FakeGitHub("down")
    run_build(data, T0, FakeMyfxbook())
    old = data / mf.HIST_DIR / "2026-08-01.csv"           # older than the 35-day local keep
    old.write_text(mf._day_text([["2026-08-01T00:00:00Z", "EURUSD"] + ["1"] * 8 + ["EUR"]]))
    a, out = run_archive(data, T0, gh)
    n = len(gh.calls)
    check(g, "failed push recorded with its time; says history is kept",
          a["last_error"] == "github" and a.get("last_failed_at") == "2026-10-14T09:00:00Z"
          and "unarchived history kept" in out and "next attempt in 6 h" in out, (a, out))
    check(g, "failed push: local history untouched, old unarchived file NOT pruned",
          len(mf.load_history(data, since="2026-10-14")) == 6 and old.exists())
    for h, mins in ((0, 30), (2, 0), (5, 59)):
        run_build(data, T0 + dt.timedelta(hours=h, minutes=mins + 1), FakeMyfxbook())
        a, out = run_archive(data, T0 + dt.timedelta(hours=h, minutes=mins), gh)
        check(g, f"{h} h {mins:02d} min after the failure: no GitHub request", len(gh.calls) == n
              and "last failed attempt" in out, out)
    gh.mode = "ok"                                            # the archive comes back
    a, out = run_archive(data, T0 + dt.timedelta(hours=5, minutes=59), gh)
    check(g, "still waits the full 6 h even once GitHub is back", len(gh.calls) == n, out)
    local = len(mf.load_history(data, since="2026-10-14"))
    a, out = run_archive(data, T0 + dt.timedelta(hours=6, minutes=1), gh)
    check(g, "after 6 h: pushes everything collected meanwhile (no gap)",
          a["last_error"] is None and len(gh.rows("2026-10-14")) == local == 24, (local, out))
    check(g, "the old unarchived day was archived, then pruned locally",
          "myfxbook/daily/2026/2026-08-01.csv.gz" in gh.files and not old.exists())
    check(g, "success clears the failure time", "last_failed_at" not in a)
    n = len(gh.calls)
    run_archive(data, T0 + dt.timedelta(hours=8), gh)
    check(g, "next push again 6 h after the successful one", len(gh.calls) == n)
    # a failure after a success also waits 6 h from the failure, not from the success
    d2, gh2 = _tmp(), FakeGitHub()
    run_build(d2, T0, FakeMyfxbook())
    run_archive(d2, T0, gh2)
    gh2.mode = "down"
    a, _ = run_archive(d2, T0 + dt.timedelta(hours=6, minutes=1), gh2)
    n = len(gh2.calls)
    gh2.mode = "ok"
    run_archive(d2, T0 + dt.timedelta(hours=12), gh2)
    check(g, "failure at 6 h -> next attempt not before 12 h 01 min", len(gh2.calls) == n)
    run_archive(d2, T0 + dt.timedelta(hours=12, minutes=2), gh2)
    check(g, "... and it does try then", len(gh2.calls) > n)
    for mode in ("public", "missing"):
        d3, gh3 = _tmp(), FakeGitHub(mode)
        run_build(d3, T0, FakeMyfxbook())
        run_archive(d3, T0, gh3)
        k = len(gh3.calls)
        run_archive(d3, T0 + dt.timedelta(hours=1), gh3)
        check(g, f"{mode} repo: refused once, then 6 h quiet; nothing written", len(gh3.calls) == k and
              not [c for c in gh3.calls if c[0] == "PUT"])


def t_hide_24h():
    g = "hide-24h"
    link = ('Retail sentiment data: <a href="https://www.myfxbook.com/community/outlook" target="_blank" '
            'rel="noopener">Myfxbook Community Outlook</a>')
    data = _tmp()
    run_build(data, T0, FakeMyfxbook())
    thin = json.loads((data / mf.OUT_NAME).read_text())
    thin["markets"]["GBP"].update(longPositions=40, shortPositions=20)        # one below-minimum market
    (data / mf.OUT_NAME).write_text(json.dumps(thin))
    when = "Last successful update: 14 Oct 2026 09:00 UTC."
    for h, m, shown in ((23, 0, True), (24, 0, True), (24, 1, False), (72, 0, False)):
        now = T0 + dt.timedelta(hours=h, minutes=m)
        html = mf.retail_panel(now=now, data=data)
        cards = {k: mf.report_card(k, now=now, data=data) for k in mf.MARKETS}
        allc = "".join(cards.values())
        tag = f"{h} h {m:02d} min old"
        if shown:
            check(g, f"{tag}: readings still shown, marked Stale", "LONG 61%" in html and "Stale" in html
                  and "Imbalance 22" in cards["XAU"], tag)
            check(g, f"{tag}: browser cut-off attached (tab + reading cards)",
                  'data-mfx-exp="2026-10-14T09:00:00Z" data-mfx-hide="1440"' in html and when in html
                  and all('data-mfx-hide="1440"' in cards[k] and when in cards[k] for k in ("XAU", "EUR", "GBP")))
        else:
            check(g, f"{tag}: no reading in the tab", not re.search(r"LONG \d|SHORT \d|Imbalance \d|Retail crowd|mfxbar|mfxrow|data-mfx", html), tag)
            check(g, f"{tag}: tab says unavailable with the last successful update time",
                  "Myfxbook retail sentiment unavailable" in html and when in html)
            check(g, f"{tag}: no reading on any card", not re.search(r"LONG \d|SHORT \d|Imbalance \d|Retail crowd|mfxbar|Too few", allc))
            check(g, f"{tag}: every card says unavailable + last update time",
                  all("Retail sentiment unavailable" in c and when in c for c in cards.values()))
        check(g, f"{tag}: attribution with link kept on the tab and all 16 cards",
              link in html and all(link in c for c in cards.values()))
    # a failure while expired: still hidden, the failure is not shown as data
    now = T0 + dt.timedelta(hours=25)
    run_build(data, now, FakeMyfxbook("down"))
    html = mf.retail_panel(now=now, data=data)
    check(g, "expired + failing refresh: still hidden, last successful time unchanged",
          "LONG 61%" not in html and when in html)
    # recovery shows readings again
    run_build(data, now + dt.timedelta(minutes=30), FakeMyfxbook())
    html = mf.retail_panel(now=now + dt.timedelta(minutes=31), data=data)
    check(g, "next successful fetch: readings back", "LONG 61%" in html and "unavailable &mdash; no successful" not in html.split("mfxexp")[0])
    check(g, "the 100-position minimum still applies", mf.MIN_POSITIONS == 100)
    check(g, "script applies the cut-off in the browser", "data-mfx-exp" in mf.SCRIPT and "expired" in mf.SCRIPT
          and "data-mfx-hide" in mf.SCRIPT and "DOMContentLoaded" in mf.SCRIPT)
    css = (Path(__file__).parent / "template.py").read_text()
    check(g, "CSS hides the live part and shows the note once expired",
          "[data-mfx-exp].expired .mfxlive{display:none}" in css and "[data-mfx-exp].expired .mfxexp{display:block}" in css
          and ".mfxexp{display:none" in css)
    check(g, "switched off: nothing at all, cut-off or not",
          mf.report_card("XAU", now=T0, data=data, env={}) == "" and "data-mfx" not in mf.retail_panel(now=T0, data=data, env={}))


def _ledger(gh):
    c, _ = gh.files[mf.LEDGER_PATH]
    return json.loads(base64.b64decode(c))


def t_request_budget():
    """One budget for every real Community Outlook request (production + manual), kept under
    the documented 100/24 h; manual real checks reserve first and fail closed."""
    g = "request-budget"
    check(g, "worst case per 24 h (production cap + manual cap) stays under the safety budget and the limit",
          mf.DAILY_BUDGET + mf.MANUAL_MAX_24H <= mf.SAFETY_BUDGET < mf.PROVIDER_LIMIT == 100,
          (mf.DAILY_BUDGET, mf.MANUAL_MAX_24H, mf.SAFETY_BUDGET))
    check(g, "existing limits unchanged (25 min gap, 60/day, 6 h login back-off, lock after 2)",
          (mf.MIN_GAP_MIN, mf.DAILY_BUDGET, mf.AUTH_BACKOFF_H, mf.AUTH_LOCK_AFTER, mf.FAIL_BACKOFF_H) == (25, 60, 6, 2, (1, 2, 4, 6)))
    # production publishes its attempts with the archive push - only when it IS production
    data, gh = _tmp(), FakeGitHub()
    for i in range(3):
        run_build(data, T0 + dt.timedelta(minutes=30 * i), FakeMyfxbook())
    a, out = run_archive(data, T0 + dt.timedelta(hours=1, minutes=1), gh)
    check(g, "a non-production archive push (CI / manual) does not touch the ledger", mf.LEDGER_PATH not in gh.files)
    d2, gh2 = _tmp(), FakeGitHub()
    for i in range(3):
        run_build(d2, T0 + dt.timedelta(minutes=30 * i), FakeMyfxbook())
    a, out = run_archive(d2, T0 + dt.timedelta(hours=1, minutes=1), gh2, env={**AENV, mf.SOURCE_VAR: "production"})
    led = _ledger(gh2)
    check(g, "production push records its 24 h attempts in the shared ledger",
          len(led["production"]["attempts"]) == 3 and led["production"]["as_of"] == "2026-10-14T10:01:00Z"
          and a.get("ledger_synced_at"), led)
    check(g, "the ledger holds timestamps only - no credential, token or figure",
          not any(x in json.dumps(led) for x in (EMAIL, PASSWORD, SESSION, TOKEN, "longPercentage")))
    # manual reservation
    env = AENV
    ok1, est = mf.reserve_manual(now=T0 + dt.timedelta(hours=1, minutes=30), env=env, opener=gh2, run="r1")
    check(g, "manual real check: allowed, recorded, total estimated (production at max rate since its sync)",
          ok1 and est["manual"] == 1 and est["production"] == 3 + 2 and est["total"] == 6
          and len(_ledger(gh2)["manual"]) == 1, est)
    for i in (2, 3):
        mf.reserve_manual(now=T0 + dt.timedelta(hours=1, minutes=30 + i), env=env, opener=gh2, run=f"r{i}")
    ok4, why = mf.reserve_manual(now=T0 + dt.timedelta(hours=2), env=env, opener=gh2, run="r4")
    check(g, f"a {mf.MANUAL_MAX_24H + 1}th manual real check within 24 h is refused", not ok4 and "max 3" in why, why)
    check(g, "a refused check is not recorded", len(_ledger(gh2)["manual"]) == 3)
    ok5, _ = mf.reserve_manual(now=T0 + dt.timedelta(hours=26), env=env, opener=gh2, run="r5")
    check(g, "24 h later manual checks are allowed again", ok5)
    # production learns the manual requests and counts them in its own budget
    run_build(d2, T0 + dt.timedelta(hours=7), FakeMyfxbook())
    a, _ = run_archive(d2, T0 + dt.timedelta(hours=7, minutes=2), gh2, env={**AENV, mf.SOURCE_VAR: "production"})
    st = json.loads((d2 / mf.STATE_NAME).read_text())
    check(g, "production learns the manual requests from the ledger", len(st.get("external_attempts", [])) == 3, st.get("external_attempts"))
    check(g, "... and the production sync keeps the manual entries", len(_ledger(gh2)["manual"]) >= 3)
    st["attempts"] = [mf._iso(T0 + dt.timedelta(hours=8) - dt.timedelta(minutes=26 + 5 * i)) for i in range(mf.SAFETY_BUDGET - 3)]
    st["attempts"] = st["attempts"][:mf.DAILY_BUDGET - 1]
    st["external_attempts"] = [mf._iso(T0 + dt.timedelta(hours=7, minutes=i)) for i in range(mf.SAFETY_BUDGET - mf.DAILY_BUDGET + 1)]
    (d2 / mf.STATE_NAME).write_text(json.dumps(st))
    f = FakeMyfxbook()
    _, out = run_build(d2, T0 + dt.timedelta(hours=8), f)
    check(g, "production stops when its own + manual requests reach the shared safety budget",
          f.calls == [] and "shared request budget" in out, out)
    # fail closed
    ok6, why = mf.reserve_manual(now=T0, env=ENV, opener=FakeGitHub())
    check(g, "no archive token -> no real check (the ledger cannot be checked)", not ok6 and "no archive token" in why)
    ok7, why = mf.reserve_manual(now=T0, env=AENV, opener=FakeGitHub("down"))
    check(g, "ledger unreachable -> no real check", not ok7 and "unavailable" in why, why)
    ok8, why = mf.reserve_manual(now=T0, env=AENV, opener=FakeGitHub("public"))
    check(g, "public ledger repo -> no real check", not ok8, why)
    est = mf.ledger_estimate({}, T0)
    check(g, "no production sync yet -> production assumed at its maximum", est["production"] == mf.DAILY_BUDGET)
    led = {"production": {"as_of": mf._iso(T0 - dt.timedelta(hours=30)), "attempts": []}, "manual": []}
    check(g, "production estimate is capped at DAILY_BUDGET", mf.ledger_estimate(led, T0)["production"] == mf.DAILY_BUDGET)


def t_ci_fixture():
    """PR builds: a synthetic snapshot, no Myfxbook request, and the workflows wired so a real
    request needs a manual real_myfxbook_test run."""
    g = "ci-fixture"
    import myfxbook_ci_check as ci
    data = _tmp()
    old = ci.DATA
    try:
        ci.DATA = data
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ci.seed_fixture()
        snap = json.loads((data / mf.OUT_NAME).read_text())
    finally:
        ci.DATA = old
    check(g, "fixture snapshot is marked synthetic, has 15 markets and a fresh timestamp",
          snap["fixture"] is True and len(snap["markets"]) == 15 and "no real request" in buf.getvalue())
    check(g, "fixture markets use the reviewed symbols", all(m["symbol"] in mf.MARKETS[k] for k, m in snap["markets"].items()))
    f = FakeMyfxbook()
    s2, out = run_build(data, dt.datetime.now(dt.timezone.utc), f, env=dict(ON))
    check(g, "the pipeline without credentials keeps the fixture and makes no request",
          f.calls == [] and s2.get("fixture") is True and s2["markets"] == snap["markets"])
    root = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    bc = (root / "build-check.yml").read_text()
    check(g, "build-check: manual input real_myfxbook_test, default off", "real_myfxbook_test:" in bc and "default: false" in bc)
    secret_lines = [l for l in bc.splitlines() if re.search(r"MYFXBOOK_(EMAIL|PASSWORD|HISTORY_TOKEN):", l)
                    and "Leak scan" not in l]
    scan_at = bc.index("Leak scan")
    gated = [l for l in secret_lines if bc.index(l) < scan_at and "secrets." in l]
    check(g, "build-check: every Myfxbook secret given to a step before the leak scan needs the manual real input",
          gated and all("inputs.real_myfxbook_test" in l for l in gated if "HISTORY_TOKEN: ${{ secrets" not in l)
          and all("inputs.real_myfxbook_test" in l or "secrets.MYFXBOOK_HISTORY_TOKEN }}" in l for l in gated), gated)
    reserve_step = bc[bc.index("reserve the one real request"):bc.index("Full pipeline")]
    check(g, "build-check: the history token is only given to the reserve step in a real run",
          "if: env.MYFXBOOK_REAL == 'true'" in reserve_step)
    check(g, "build-check: pull requests run in fixture mode and any myfxbook.com request fails the job",
          "github.event_name == 'workflow_dispatch' && inputs.real_myfxbook_test" in bc and "if not mfx_real and" in bc
          and "--seed-fixture" in bc)
    rf = (root / "refresh.yml").read_text()
    check(g, "production refresh keeps its credentials and syncs the shared ledger",
          "MYFXBOOK_SOURCE: production" in rf and "secrets.MYFXBOOK_EMAIL" in rf)


if __name__ == "__main__":
    for fn in (t_parse, t_match, t_labels, t_fetch, t_build_and_budget, t_failures_keep_snapshot,
               t_render, t_secrets, t_archive, t_probe, t_switch, t_select, t_threshold, t_history_upgrade, t_attribution,
               t_fail_backoff, t_login_lock, t_archive_spacing, t_hide_24h, t_request_budget, t_ci_fixture):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised", f"{type(e).__name__}: {e}"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
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
