"""Tests for the Myfxbook Community Outlook integration (myfxbook.py + its rendering).

    python test_myfxbook.py

Stdlib only, offline. A fake Myfxbook (login / outlook / logout) is injected as the opener,
and every build runs against a temporary data dir, so the suite never reaches Myfxbook and
never writes the live data/ directory. Covers parsing, missing symbols, malformed responses,
refused login, invalid session, API unavailable, the 25-minute / daily budget, keeping the
last good snapshot (never zeros), freshness labels, 0/100 and 50/50, the retail-crowd bands,
history storage, and that the credentials and session id never reach a file, the page or a
log line.
"""
import contextlib, io, json, sys, tempfile, urllib.error, urllib.parse
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import myfxbook as mf                                             # noqa: E402

PASS, FAIL = [], []
UTC = dt.timezone.utc
EMAIL, PASSWORD, SESSION = "trader.secret+x@example.com", "Pa$$ w0rd/=?&", "SeSsIoN9qXw7Zk2"
ENV = {"MYFXBOOK_EMAIL": EMAIL, "MYFXBOOK_PASSWORD": PASSWORD}
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
    snap, out = run_build(data, T0, FakeMyfxbook(), env={})
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
    check(g, "history stored by month", (data / mf.HIST_DIR / "2026-10.csv").exists())

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


if __name__ == "__main__":
    for fn in (t_parse, t_match, t_labels, t_fetch, t_build_and_budget, t_failures_keep_snapshot,
               t_render, t_secrets):
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
