"""Tests for the Historical Performance UI (history_panel.py) - presentation of the frozen
Step 3 engine output.

    python test_history_panel.py

Stdlib only, offline. Fixtures are produced by the real engine on synthetic inputs
(history_fixtures.py); urlopen is replaced by a spy, and a fake GitHub serves the result
files, so nothing real is contacted and the live data/ directory is never touched.
"""
import copy, datetime as dt, json, re, sys, tempfile, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import history_fixtures as hf                                     # noqa: E402
import history_panel as hp                                        # noqa: E402

PASS, FAIL, CALLS = [], [], []
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 15, 6, 0, tzinfo=UTC)


def _spy(req, *a, **k):
    CALLS.append(getattr(req, "full_url", str(req)))
    raise OSError("network blocked in test_history_panel")


urllib.request.urlopen = _spy


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&mdash;", "—").replace("&middot;", "·")


class _R:
    def __init__(self, b):
        self.b = b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.b


class FakeGitHub:
    def __init__(self, runs):
        self.runs, self.calls = runs, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        path = url.split("/contents/", 1)[1]
        if path == "backtest/runs":
            return _R(json.dumps([{"name": n, "type": "dir"} for n in self.runs]).encode())
        name, f = path[len("backtest/runs/"):].split("/")
        return _R(json.dumps(self.runs[name][0 if f == "manifest.json" else 1]).encode())


ENV = {"MYFXBOOK_HISTORY_TOKEN": "t0ken", "MYFXBOOK_HISTORY_REPO": "me/data"}
M_P, R_P = hf.results("pending")
M_C, R_C = hf.results("completed")


def view_of(m, r, run="2026-10-15_5fceee5"):
    return hp.build_view(run, m, r)


def t_reads_engine():
    g = "engine-output"
    v = view_of(M_P, R_P)
    check(g, "view episodes/state events are copied from the engine result",
          [e["id"] for e in v["episodes"]] == [e["id"] for e in R_P["episodes"]]
          and v["accounting"]["signals"] == R_P["accounting"]["signals"])
    src = (Path(__file__).parent / "history_panel.py").read_text()
    check(g, "no strategy code in the UI (no divergence / replay / classification call)",
          not re.search(r"import divergence\b|import divergence_backtest\b|evaluate\(|classify\(|replay\(|make_episodes|make_events|cot_available", src))
    check(g, "no JavaScript at all (nothing recalculated in the browser)", "<script" not in hp.panel(v, NOW) and "<script" not in src)
    r2 = copy.deepcopy(R_P)
    r2["stats"]["episodes"]["all"]["All markets"]["1"]["sample"] = "ENGINE-SAYS"
    check(g, "the sample label is the engine's, not recomputed", "ENGINE-SAYS" in hp.panel(view_of(M_P, r2), NOW))
    check(g, "fingerprint renders from the result metadata (abbreviated, full on hover)",
          f'title="fingerprint {M_P["strategy"]["fingerprint"]}"' in hp.panel(v, NOW)
          and M_P["strategy"]["fingerprint"][:7] in hp.panel(v, NOW))
    m2 = copy.deepcopy(M_P)
    m2["strategy"]["fingerprint"] = "ffffffffffffffff"
    check(g, "a run that is not the frozen baseline says so", "Not the frozen baseline strategy" in hp.panel(view_of(m2, R_P), NOW))


def t_units():
    g = "units"
    v = view_of(M_P, R_P)
    h = hp.panel(v, NOW)
    t = text(h)
    acc = R_P["accounting"]["signals"]
    check(g, "fixture: 5 directional episodes, 6 state events (as on 14 Oct)", acc["directional_episodes"] == 5 and acc["state_events"] == 6, acc)
    check(g, "5 render as 'Independent episodes'", "Independent episodes 5" in t, t[:400])
    check(g, "6 state events shown separately, as a secondary diagnostic", "State events 6" in t
          and "are diagnostics: repeated snapshots of the same signal are not independent trades" in t)
    check(g, "observations shown as 'Signal observations', never as trades / signals count",
          f"Signal observations {acc['observations']}" in t and not re.search(rf"\b{acc['observations']} (signals|trades)\b", t))
    check(g, "headline (from the data)", "Insufficient sample — 5 independent directional episodes, all forward horizons currently pending." in t, t[:300])
    check(g, "headline is not hard-coded", "5 independent" not in (Path(__file__).parent / "history_panel.py").read_text())
    aud = next(e for e in v["episodes"] if e["market"] == "AUD")
    check(g, "AUDUSD: ONE directional episode with two state events Bearish -> Strong Bearish",
          sum(e["market"] == "AUD" for e in v["episodes"]) == 1 and [s["cls"] for s in aud["states"]] == ["bear", "strong_bear"])
    row = re.search(r"<summary>AUDUSD &middot; Bearish</summary>.*?</tr>", h, re.S).group(0)
    check(g, "the AUDUSD audit row shows the transition and both state events",
          "Bearish <span class=\"mut\">(7)</span> &rarr; Strong Bearish" in row and row.count("snapshot") >= 2
          and "<td>Bearish</td><td>Strong Bearish</td>" in row, row[:500])


def t_pending():
    g = "pending"
    v = view_of(M_P, R_P)
    h = hp.panel(v, NOW)
    t = text(h)
    tab = re.search(r"All markets &mdash; independent episodes by horizon</h3>(.*?)</table>", h, re.S).group(1)
    rows = re.findall(r'<tr><th scope="row">([^<]+)</th>(.*?)</tr>', tab, re.S)
    check(g, "horizon rows are exactly 1, 3, 5, 10 days", [r[0] for r in rows] == ["1 day", "3 days", "5 days", "10 days"], rows)
    check(g, "every incomplete horizon shows 'Pending' in all six metric columns",
          all(r[1].count("Pending") == 6 and "0 of 5" in r[1] for r in rows))
    check(g, "pending is never shown as 0% / 0.00 / NaN / Infinity",
          not re.search(r"\b0%|0\.00|NaN|Infinity|nan%", t), re.findall(r".{20}(?:0%|0\.00|NaN|Infinity).{20}", t))
    check(g, "audit: horizon cells and entry say Pending (no invented price)",
          h.count(hp.PENDING) >= 5 * 5 and " @ " not in re.search(r'class="hptab hpaudit".*?</table>', h, re.S).group(0))
    check(g, "the fixture is genuinely pending (not missing): 5 pending at every horizon",
          all(R_P["stats"]["episodes"]["all"]["All markets"][h]["n_pending"] == 5 for h in ("1", "3", "5", "10")))
    check(g, "completed horizons: 0 of 20", "Completed horizons 0 of 20" in t)
    check(g, "no chart when nothing has completed", "<svg" not in h and "<canvas" not in h)


def t_completed():
    g = "completed"
    v = view_of(M_C, R_C)
    h = hp.panel(v, NOW)
    t = text(h)
    s1 = R_C["stats"]["episodes"]["all"]["All markets"]["1"]
    check(g, "fixture completes horizons", s1["n_complete"] > 0, s1)
    n = s1["n_complete"]
    check(g, "a win rate is always shown with its n", f'{s1["win_rate"]:.0f}% (n={n})' in t, t[:900])
    check(g, "no bare win-rate percentage without n", not re.search(r"\d+% win rate", t))
    check(g, "headline updates from the data when horizons complete",
          re.search(rf"— {len(v['episodes'])} independent directional episodes; {n} of {len(v['episodes'])} have completed the 1-day horizon", t) is not None,
          hp.headline(v))
    nzd = [e for e in v["episodes"] if e["market"] == "NZD"]
    check(g, "disappearance / reappearance -> two separate NZD episodes", len(nzd) == 2, [e["id"] for e in nzd])
    check(g, "never calls the strategy profitable / validated / proven / reliable / significant",
          not re.search(r"(?i)profitable|validated|proven|reliable|statistically significant", t))
    check(g, "no optimisation, sizing or equity-curve wording",
          not re.search(r"(?i)threshold search|best threshold|position siz|equity curve|annuali[sz]ed|stop[- ]loss|take[- ]profit|transaction cost", t))


def t_states():
    g = "states"
    h = hp.panel({"state": "missing"}, NOW)
    check(g, "missing result file -> explicit unavailable state, no statistic",
          "Historical performance unavailable" in h and "Win rate" not in h and "%" not in text(h))
    for mutate, label in ((lambda r: r.pop("episodes"), "no episodes key"),
                          (lambda r: r["accounting"]["signals"].update(directional_episodes=99), "counts disagree"),
                          (lambda r: r["episodes"][0]["fwd"].pop("3"), "horizon missing"),
                          (lambda r: r["episodes"][0]["fwd"]["1"].update(state="weird"), "bad horizon state"),
                          (lambda r: r["stats"].update(primary_level="observations"), "wrong primary level")):
        r2 = copy.deepcopy(R_P)
        mutate(r2)
        try:
            hp.build_view("x", M_P, r2)
            check(g, f"malformed ({label}) is rejected", False)
        except hp.Malformed:
            check(g, f"malformed ({label}) is rejected", True)
    d = Path(tempfile.mkdtemp(prefix="fxhp-"))
    bad = copy.deepcopy(R_P)
    bad.pop("stats")
    v = hp.load(data=d, env=ENV, opener=FakeGitHub({"2026-10-15_x": (M_P, bad)}))
    check(g, "a malformed published file fails visibly (state 'malformed', reason kept)",
          v["state"] == "malformed" and "stats" in v["why"], v)
    h = hp.panel(v, NOW)
    check(g, "... and the page shows that, with no statistic", "failed validation" in h and "Win rate" not in h)
    gh = FakeGitHub({"2026-10-14_a": (dict(M_P, generated_at="2026-10-14T06:41:00Z"), R_P),
                     "2026-10-15_b": (M_P, R_P)})
    v = hp.load(data=d, env=ENV, opener=gh)
    check(g, "loads the newest run by generation time and caches the view", v["state"] == "ok" and v["run"] == "2026-10-15_b"
          and (d / hp.VIEW_NAME).exists())
    check(g, "only api.github.com was contacted", all(u.startswith("https://api.github.com/") for u in gh.calls) and gh.calls)
    gh2 = FakeGitHub({})

    def down(req, timeout=None):
        raise OSError("down")
    v2 = hp.load(data=d, env=ENV, opener=down)
    check(g, "GitHub unreachable -> the cached view, marked with the fetch error", v2["state"] == "ok" and v2.get("fetch_error") == "OSError")
    check(g, "an old result is marked stale with its time",
          "Stale — the latest backtest run is from 15 Oct 2026 00:36 UTC" in text(hp.panel(v2, NOW + dt.timedelta(hours=40))))
    check(g, "a fresh result is not marked stale", "Stale" not in text(hp.panel(v2, NOW)))
    d2 = Path(tempfile.mkdtemp(prefix="fxhp-"))
    check(g, "no token and no cache -> missing (nothing invented)", hp.load(data=d2, env={}, opener=gh2)["state"] == "missing" and gh2.calls == [])
    check(g, "an empty run (0 episodes) renders a clear 'no episodes yet' state",
          "no independent directional episodes yet" in hp.headline({"episodes": [], "stats": {"all": {}}}))


def t_safety():
    g = "safety"
    guarded = hp._opener(lambda req, timeout=None: _R(b"{}"))
    for url in ("https://www.myfxbook.com/api/get-community-outlook.json", "https://evil.example/x", "http://api.github.com.evil.io/x"):
        try:
            guarded(urllib.request.Request(url))
            check(g, f"blocks {url.split('/')[2]}", False)
        except hp.NotAllowed:
            check(g, f"blocks {url.split('/')[2]}", True)
    src = (Path(__file__).parent / "history_panel.py").read_text()
    check(g, "no Myfxbook credential, login or URL in the UI module",
          not re.search(r"MYFXBOOK_EMAIL|MYFXBOOK_PASSWORD|login\.json|myfxbook\.com/api", src))
    h = hp.panel(view_of(M_C, R_C), NOW)
    check(g, "the page section contains no fetch / XHR / script", not re.search(r"(?i)<script|fetch\(|XMLHttpRequest", h))
    check(g, "Myfxbook switched off -> no section, no link", True if _off_check() else False)
    check(g, "no network used by the suite (spy)", CALLS == [], CALLS)


def _off_check():
    import os
    old = os.environ.get("MYFXBOOK_ENABLED")
    os.environ["MYFXBOOK_ENABLED"] = "false"
    hp._VIEW = None
    try:
        return hp.section(NOW) == "" and hp.link() == ""
    finally:
        if old is None:
            os.environ.pop("MYFXBOOK_ENABLED", None)
        else:
            os.environ["MYFXBOOK_ENABLED"] = old
        hp._VIEW = None


def t_markets():
    g = "markets"
    v = view_of(M_P, R_P)
    h = hp.panel(v, NOW)
    t = text(h)
    for name in ("AUDUSD", "EURUSD", "GBPUSD", "USDJPY", "Silver"):
        check(g, f"{name}: drill-down with 1 independent episode", f"{name} · 1 independent episode" in t)
    check(g, "markets without episodes: one compact line, no empty tables",
          "No historical divergence episodes yet:" in t and h.count('class="hpmkt"') == 5)
    check(g, "drill-down shows first signal, latest snapshot, completed / pending horizons",
          re.search(r"First signal 14 Oct 2026 09:24 UTC · latest relevant snapshot .* horizons completed 0, pending 4", t) is not None)
    check(g, "methodology disclosure, collapsed by default (no 'open')",
          '<details class="hpmeth">' in h and "Bearish &rarr; Strong Bearish stays the same episode" in h
          and "next dated daily session" in h and "roll jump" in h and "official CFTC release" in h and "genuine snapshots" in h)
    check(g, "sample status is text, not colour only", "Insufficient sample" in t and 'class="hpsample"' in h)


if __name__ == "__main__":
    for fn in (t_reads_engine, t_units, t_pending, t_completed, t_states, t_markets, t_safety):
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
