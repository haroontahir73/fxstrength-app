"""Historical Performance (Step 3 UI) - presentation of the frozen backtest engine's output.

The engine (divergence_backtest*.py, frozen at 5fceee5) is authoritative. This module only
READS its latest published run from the private archive repo, keeps a small public view of
it, and renders HTML. It does not recalculate a signal, an episode, a COT availability or a
statistic; there is no JavaScript.

    load(...)          -> the public view (dict) or {"state": "missing" / "malformed" / ...}
    panel(view, now)   -> the HTML section for the COT tab
    jump_link(view)    -> a one-line link to the section

Request safety: the only network access is GitHub's API (api.github.com) for the private
repo, through _opener(), which refuses every other host - so this module cannot contact
Myfxbook. No Myfxbook credential is read. Fails visibly: a missing, unreadable or malformed
result file shows an explicit "unavailable" state and no statistic.
"""
import datetime as dt, json, os, urllib.parse, urllib.request
from html import escape as _e
from pathlib import Path

import cot_table as ct
import divergence_backtest_config as bc
import divergence_config as dcfg
from config import DATA

VIEW_SCHEMA = 2                              # 2: retail positioning as bands only (no exact %)
VIEW_NAME = "backtest_view.json"           # cache of the public view (engine/data, gitignored)
RUNS_PATH = "backtest/runs"
GH_API = "https://api.github.com"
STALE_AFTER_H = 36                         # the engine runs daily; older than this -> "stale"
HORIZONS = [str(h) for h in bc.HORIZONS]   # "1", "3", "5", "10" (as the engine writes them)
CLASS_LABEL = {"strong_bull": "Strong Bullish", "bull": "Bullish", "bear": "Bearish", "strong_bear": "Strong Bearish"}
UTC = dt.timezone.utc


def _labels():
    return {r[3]: r[0] for r in ct.ALL_ROWS}


# ---------------------------------------------------------------- network (GitHub only)
class NotAllowed(Exception):
    pass


def _opener(opener=None):
    real = opener or urllib.request.urlopen

    def guarded(req, *a, **k):
        url = req if isinstance(req, str) else getattr(req, "full_url", "")
        if urllib.parse.urlparse(url).netloc != "api.github.com":
            raise NotAllowed(f"history_panel may only contact api.github.com, not {urllib.parse.urlparse(url).netloc}")
        return real(req, *a, **k)
    return guarded


def _get(opener, path, token, raw=False):
    req = urllib.request.Request(GH_API + path, headers={
        "Authorization": f"Bearer {token}", "User-Agent": "fxstrength-desk", "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"})
    with opener(req, timeout=40) as r:
        body = r.read()
    return body if raw else json.loads(body.decode("utf-8"))


def fetch_latest(env=None, opener=None):
    """(run name, manifest, results) of the newest published run (by generated_at)."""
    env = os.environ if env is None else env
    token = env.get("MYFXBOOK_HISTORY_TOKEN", "")
    repo = env.get("MYFXBOOK_HISTORY_REPO") or "haroontahir73/fxstrength-data"
    if not token:
        return None
    op = _opener(opener)
    runs = sorted(x["name"] for x in _get(op, f"/repos/{repo}/contents/{RUNS_PATH}", token) if x.get("type") == "dir")
    best = None
    for name in runs[-4:]:                                   # names start with the run date
        m = json.loads(_get(op, f"/repos/{repo}/contents/{RUNS_PATH}/{name}/manifest.json", token, raw=True))
        if best is None or m.get("generated_at", "") > best[1].get("generated_at", ""):
            best = (name, m)
    if not best:
        return None
    res = json.loads(_get(op, f"/repos/{repo}/contents/{RUNS_PATH}/{best[0]}/results.json", token, raw=True))
    return best[0], best[1], res


# ---------------------------------------------------------------- the public view
class Malformed(Exception):
    pass


def _need(cond, what):
    if not cond:
        raise Malformed(what)


def _fwd_view(f):
    out = {}
    for h in HORIZONS:
        x = (f or {}).get(h)
        _need(isinstance(x, dict) and x.get("state") in ("ok", "pending", "missing"), f"horizon {h} state")
        if x["state"] == "ok":
            for k in ("entry_date", "entry", "exit_date", "exit", "ret", "dir_ret", "correct", "mfe", "mae"):
                _need(k in x, f"horizon {h} field {k}")
            out[h] = {k: x[k] for k in ("entry_date", "entry", "exit_date", "exit", "ret", "dir_ret", "correct", "mfe", "mae")}
        else:
            out[h] = {"state": x["state"], **({"why": x["why"]} if x.get("why") else {})}
        out[h].setdefault("state", x["state"])
    return out


# The frozen Step 2 retail thresholds (ordinary / strong), read from the frozen config - not
# re-derived. The public view carries only the band an episode started in: the exact archived
# Myfxbook long / short percentages stay in the private results.json.
RETAIL_ORDINARY = min(r[5] for r in dcfg.RULES)
RETAIL_STRONG = max(r[5] for r in dcfg.RULES)


def retail_band(direction, long_pct, short_pct):
    """'Retail long >=70%' etc. - the side against the signal, banded at the frozen thresholds."""
    side, v = ("long", long_pct) if direction == "Bearish" else ("short", short_pct)
    _need(direction in ("Bearish", "Bullish") and isinstance(v, (int, float)), "episode retail positioning unreadable")
    _need(v >= RETAIL_ORDINARY, "episode starts below the retail trigger")
    if v >= RETAIL_STRONG:
        return f"Retail {side} \u2265{RETAIL_STRONG}%"
    return f"Retail {side} {RETAIL_ORDINARY}\u2013{RETAIL_STRONG - 1}%"


def build_view(run, manifest, res):
    """Copy (never compute) what the page shows from the engine's run. Raises Malformed."""
    _need(isinstance(manifest, dict) and isinstance(res, dict), "not JSON objects")
    for k in ("conventions", "accounting", "episodes", "state_events", "snapshots", "stats"):
        _need(k in res, f"results.json has no '{k}'")
    st = res["stats"]
    _need(st.get("primary_level") == "episodes" and isinstance(st.get("episodes"), dict), "no episode-level statistics")
    allm = (st["episodes"].get("all") or {}).get("All markets")
    eps, evs = res["episodes"], res["state_events"]
    _need(isinstance(eps, list) and isinstance(evs, list), "episodes / state events not lists")
    if eps:
        _need(isinstance(allm, dict) and all(h in allm for h in HORIZONS), "all-markets horizon statistics missing")
    acc = res["accounting"]
    for k in ("snapshots", "market_observations", "signals"):
        _need(k in acc, f"accounting has no '{k}'")
    _need(acc["signals"].get("directional_episodes") == len(eps), "episode count disagrees with the accounting")
    _need(acc["signals"].get("state_events") == len(evs), "state-event count disagrees with the accounting")
    ev_by = {e["id"]: e for e in evs}
    snaps = res["snapshots"]
    last = snaps[-1] if snaps else None
    episodes = []
    for e in eps:
        for k in ("id", "market", "direction", "start", "end", "start_cls", "max_cls", "n_obs", "pct", "long", "short",
                  "state_events", "fwd"):
            _need(k in e, f"episode field {k}")
        _need(all(s in ev_by for s in e["state_events"]), f"episode {e['id']} lists an unknown state event")
        episodes.append({
            "id": e["id"], "market": e["market"], "direction": e["direction"], "start": e["start"], "end": e["end"],
            "active": e["end"] == last, "start_cls": e["start_cls"], "max_cls": e["max_cls"], "n_obs": e["n_obs"],
            "pct": e["pct"], "retail_band": retail_band(e["direction"], e["long"], e["short"]), "symbol": e.get("symbol"),
            "cot_report": e.get("cot_report"), "fwd": _fwd_view(e["fwd"]),
            "states": [{"cls": ev_by[s]["cls"], "start": ev_by[s]["start"], "end": ev_by[s]["end"],
                        "n_obs": ev_by[s]["n_obs"], "started_by": ev_by[s].get("started_by")} for s in e["state_events"]]})
    stats = {"all": {h: allm[h] for h in HORIZONS} if allm else {},
             "market": {k: {h: v[h] for h in HORIZONS} for k, v in (st["episodes"].get("market") or {}).items()}}
    for g in [stats["all"]] + list(stats["market"].values()):
        for h, s in g.items():
            _need(isinstance(s.get("n"), int) and isinstance(s.get("n_complete"), int) and "sample" in s,
                  f"horizon {h} summary incomplete")
    strat = manifest.get("strategy") or {}
    return {"state": "ok", "schema": VIEW_SCHEMA, "run": run, "generated_at": manifest.get("generated_at"), "code_commit": manifest.get("code_commit"),
            "prices_asof": manifest.get("prices_asof"),
            "strategy": {"version": strat.get("version"), "fingerprint": strat.get("fingerprint"),
                         "is_baseline": strat.get("is_baseline") is True and strat.get("fingerprint") == bc.BASELINE_FINGERPRINT},
            "coverage": {"first": snaps[0] if snaps else None, "last": last},
            "accounting": {"snapshots": {k: acc["snapshots"].get(k) for k in ("archived", "production", "excluded_before_expansion")},
                           "market_observations": {k: acc["market_observations"].get(k) for k in ("total", "classified", "unavailable", "unavailable_by_reason")},
                           "signals": acc["signals"]},
            "conventions": res["conventions"], "episodes": episodes, "stats": stats}


def load(data=None, env=None, opener=None, fetch=True):
    """The view to render: refreshed from the newest run when possible, else the cached view
    (marked stale if old), else an explicit missing / malformed state."""
    data = Path(data or DATA)
    cache = data / VIEW_NAME
    cached = None
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
        except ValueError:
            cached = None
        if not (isinstance(cached, dict) and cached.get("schema") == VIEW_SCHEMA):
            cached = None                       # an older view (it may hold exact retail %): never shown, removed
            cache.unlink(missing_ok=True)
    if fetch:
        try:
            got = fetch_latest(env, opener)
        except NotAllowed:
            raise
        except Exception as e:                                   # noqa: BLE001
            got = None
            if cached:
                cached["fetch_error"] = type(e).__name__
        if got:
            run, manifest, res = got
            if cached and cached.get("run") == run and cached.get("state") == "ok":
                return cached
            try:
                view = build_view(run, manifest, res)
            except (Malformed, KeyError, TypeError, ValueError) as e:
                return {"state": "malformed", "run": run, "why": str(e) or type(e).__name__}
            data.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(view, sort_keys=True))
            return view
    if cached and cached.get("state") == "ok":
        return cached
    return {"state": "missing"}


# ---------------------------------------------------------------- headline (from the data)
def headline(view):
    eps = view["episodes"]
    n = len(eps)
    allm = view["stats"].get("all") or {}
    sample = (allm.get("1") or {}).get("sample") or "Insufficient sample"
    if n == 0:
        return "Insufficient sample — no independent directional episodes yet."
    word = "episode" if n == 1 else "episodes"
    done = {h: (allm.get(h) or {}).get("n_complete", 0) for h in HORIZONS}
    if not any(done.values()):
        return f"{sample} — {n} independent directional {word}, all forward horizons currently pending."
    parts = [f"{done['1']} of {n} {'has' if done['1'] == 1 else 'have'} completed the 1-day horizon"]
    if done["10"]:
        parts.append(f"{done['10']} the 10-day")
    return f"{sample} — {n} independent directional {word}; " + ", ".join(parts) + "."


# ---------------------------------------------------------------- rendering
def _ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _when(s):
    return _ts(s).strftime("%d %b %Y %H:%M UTC") if s else "&mdash;"


def _pct(v):
    return f"{v:+.2f}%".replace("-", "&minus;")


def _ord(p):
    n = int(p + 0.5)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


PENDING = '<span class="hppend" title="Not completed yet - not zero">Pending</span>'


def _hz_rows(stats, n_label="episodes"):
    rows = []
    for h in HORIZONS:
        s = stats.get(h) or {}
        n, c = s.get("n", 0), s.get("n_complete", 0)
        if not c:
            why = f'{s.get("n_pending", n)} pending' + (f', {s["n_missing"]} missing price' if s.get("n_missing") else "")
            rows.append(f'<tr><th scope="row">{h} day{"s" if h != "1" else ""}</th><td>0 of {n} <span class="mut">({why})</span></td>'
                        + f"<td>{PENDING}</td>" * 6 + "</tr>")
            continue
        ci = s.get("win_ci95")
        ci_t = f' title="Wilson 95% interval {ci[0]:.0f}-{ci[1]:.0f}%"' if ci else ""
        rows.append(
            f'<tr><th scope="row">{h} day{"s" if h != "1" else ""}</th><td>{c} of {n}</td>'
            f'<td>{s["wins"]} of {c}</td><td{ci_t}>{s["win_rate"]:.0f}% <span class="mut">(n={c})</span></td>'
            f'<td>{_pct(s["avg_dir"])}</td><td>{_pct(s["med_dir"])}</td><td>{_pct(s["avg_mfe"])}</td><td>{_pct(s["avg_mae"])}</td></tr>')
    return ("<div class=\"hpscroll\"><table class=\"hptab\"><thead><tr><th scope=\"col\">Horizon</th>"
            f"<th scope=\"col\">Independent {n_label} completed</th><th scope=\"col\">Direction correct</th>"
            "<th scope=\"col\">Win rate</th><th scope=\"col\">Avg directional return</th><th scope=\"col\">Median</th>"
            "<th scope=\"col\">Avg MFE</th><th scope=\"col\">Avg MAE</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _hz_cell(f):
    if f.get("state") == "ok":
        mark = "&#10003;" if f["correct"] else "&#10007;"
        return f'<td>{_pct(f["dir_ret"])} <span class="mut" aria-label="{"direction correct" if f["correct"] else "direction wrong"}">{mark}</span></td>'
    if f.get("state") == "missing":
        return f'<td><span class="hppend" title="{_e(f.get("why", ""))}">Missing price</span></td>'
    return f"<td>{PENDING}</td>"


def _episode_rows(view, labels):
    out = []
    for e in view["episodes"]:
        f = e["fwd"]
        entry = next((f[h] for h in HORIZONS if f[h].get("state") == "ok"), None)
        entry_t = f'{entry["entry_date"]} @ {entry["entry"]:g}' if entry else PENDING
        states = " &rarr; ".join(f'{CLASS_LABEL.get(s["cls"], s["cls"])} <span class="mut">({s["n_obs"]})</span>' for s in e["states"])
        out.append(
            f'<tr><th scope="row" class="hpid"><details><summary>{_e(labels.get(e["market"], e["market"]))}'
            f' &middot; {_e(e["direction"])}</summary><p class="hpstates">State transitions: {states}</p>'
            + "".join(f'<p class="mut hpst">{CLASS_LABEL.get(s["cls"], s["cls"])}: {_when(s["start"])} &ndash; {_when(s["end"])}, '
                      f'{s["n_obs"]} snapshot{"s" if s["n_obs"] != 1 else ""} ({_e(s.get("started_by") or "")})</p>' for s in e["states"])
            + f'<p class="mut hpst">Episode ID: <code>{_e(e["id"])}</code></p></details></th>'
            f'<td>{_e(e["direction"])}</td><td>{_when(e["start"])}</td>'
            f'<td>{"Active" if e["active"] else _when(e["end"])}</td>'
            f'<td>{CLASS_LABEL.get(e["start_cls"], e["start_cls"])}</td><td>{CLASS_LABEL.get(e["max_cls"], e["max_cls"])}</td>'
            f'<td>{e["n_obs"]}</td><td>{_ord(e["pct"])}</td>'
            f'<td>{_e(e["retail_band"])}</td><td>{entry_t}</td>'
            + "".join(_hz_cell(f[h]) for h in HORIZONS) + "</tr>")
    return ("<div class=\"hpscroll\"><table class=\"hptab hpaudit\"><thead><tr><th scope=\"col\">Episode</th><th scope=\"col\">Direction</th>"
            "<th scope=\"col\">Start</th><th scope=\"col\">End</th><th scope=\"col\">Starting strength</th>"
            "<th scope=\"col\">Strongest</th><th scope=\"col\">Snapshots</th><th scope=\"col\">COT percentile</th>"
            "<th scope=\"col\">Retail at start (band)</th><th scope=\"col\">Entry</th>"
            + "".join(f'<th scope="col">{h}d</th>' for h in HORIZONS) + "</tr></thead><tbody>" + "".join(out)
            + "</tbody></table></div>")


def _markets(view, labels):
    by = {}
    for e in view["episodes"]:
        by.setdefault(e["market"], []).append(e)
    if not by:
        return '<p class="mut">No historical divergence episodes yet.</p>'
    out = []
    for k in [r[3] for r in ct.ALL_ROWS if r[3] in by]:
        es = by[k]
        rank = {"bull": 1, "bear": 1, "strong_bull": 2, "strong_bear": 2}
        strongest = max((e["max_cls"] for e in es), key=lambda c: rank.get(c, 0))
        dirs = sorted({e["direction"] for e in es})
        done = sum(1 for e in es for h in HORIZONS if e["fwd"][h].get("state") == "ok")
        pend = sum(1 for e in es for h in HORIZONS if e["fwd"][h].get("state") == "pending")
        stats = view["stats"]["market"].get(k) or {}
        out.append(
            f'<details class="hpmkt"><summary><b>{_e(labels.get(k, k))}</b> &middot; {len(es)} independent episode'
            f'{"s" if len(es) != 1 else ""} &middot; {", ".join(dirs)} &middot; strongest {CLASS_LABEL.get(strongest, strongest)}</summary>'
            f'<p class="mut">First signal {_when(min(e["start"] for e in es))} &middot; latest relevant snapshot '
            f'{_when(max(e["end"] for e in es))} &middot; horizons completed {done}, pending {pend}</p>'
            + _hz_rows(stats) + "</details>")
    quiet = [labels.get(k, k) for k in [r[3] for r in ct.ALL_ROWS] if k not in by]
    if quiet:
        out.append(f'<p class="mut hpquiet">No historical divergence episodes yet: {_e(", ".join(quiet))}.</p>')
    return "".join(out)


def _method(view):
    c = view.get("conventions") or {}
    return ("<details class=\"hpmeth\"><summary>How this is measured</summary><ul>"
            "<li>Repeated snapshots are <b>not</b> independent trades: consecutive snapshots with the same direction for a "
            "market are collapsed into one <b>independent directional episode</b> (the sample unit). Bearish &rarr; Strong "
            "Bearish stays the same episode; it is shown as a state transition inside it.</li>"
            "<li>Entry uses the open of the next dated daily session after the signal. 1 day = that entry session's close; "
            "3, 5 and 10 days count trading sessions including the entry session.</li>"
            "<li>COT data is point-in-time: a report is used only from its official CFTC release (15:30 ET) or later.</li>"
            "<li>Myfxbook history contains only genuine snapshots captured by the production feed; nothing is "
            "reconstructed or back-filled.</li>"
            f"<li>Retail positioning is shown as a band at the frozen triggers ({RETAIL_ORDINARY}\u2013{RETAIL_STRONG - 1}% = ordinary, "
            f"\u2265{RETAIL_STRONG}% = strong, on the side against the signal); the exact archived Myfxbook percentages "
            "are kept private.</li>"
            "<li>Metals, copper and oil use Yahoo continuous front-month futures, so a result spanning a contract roll "
            "includes the roll jump.</li>"
            "<li><b>Pending</b> means the horizon has not finished yet &mdash; it is not a zero result.</li>"
            f"</ul><p class=\"mut\">Exact rules from the engine: {_e(c.get('horizon', ''))}. {_e(c.get('directional_episode', ''))}</p></details>")


def _status_chip(view, now):
    gen = view.get("generated_at")
    if not gen:
        return ""
    age_h = (now - _ts(gen)).total_seconds() / 3600
    if age_h > STALE_AFTER_H:
        return (f' <span class="chip warn">Stale &mdash; the latest backtest run is from {_when(gen)}'
                f'{" (refresh failed)" if view.get("fetch_error") else ""}</span>')
    return ""


def panel(view, now=None):
    now = now or dt.datetime.now(UTC)
    head = ('<section class="hpsec" id="hist-perf"><h2>Historical Performance '
            '<span class="mut" style="font-weight:400;font-size:14px">&mdash; COT &times; Myfxbook divergence, baseline backtest</span></h2>')
    if view.get("state") == "missing":
        return head + ('<p class="hpbad"><b>Historical performance unavailable</b> &mdash; no backtest result has been '
                       'published yet (or it cannot be read). Nothing is shown rather than estimated.</p></section>')
    if view.get("state") != "ok":
        return head + (f'<p class="hpbad"><b>Historical performance unavailable</b> &mdash; the latest result file '
                       f'({_e(view.get("run") or "?")}) failed validation: {_e(view.get("why") or "malformed")}. '
                       'No statistic is shown.</p></section>')
    labels = _labels()
    acc = view["accounting"]
    st = view["strategy"]
    fp = st.get("fingerprint") or "?"
    tot_done = sum((view["stats"].get("all", {}).get(h) or {}).get("n_complete", 0) for h in HORIZONS)
    n = len(view["episodes"])
    sample = ((view["stats"].get("all") or {}).get("1") or {}).get("sample") or "Insufficient sample"
    base = "" if st.get("is_baseline") else ' <span class="chip warn">Not the frozen baseline strategy</span>'
    cov = view["coverage"]
    summary = (
        f'<p class="hphead" role="status"><span class="hpsample">{_e(sample)}</span> &mdash; {_e(headline(view).split(" — ", 1)[-1])}</p>'
        '<dl class="hpsum">'
        f'<div><dt>Independent episodes</dt><dd>{n}</dd></div>'
        f'<div><dt>State events</dt><dd>{acc["signals"].get("state_events", 0)}</dd></div>'
        f'<div><dt>Signal observations</dt><dd>{acc["signals"].get("observations", 0)}</dd></div>'
        f'<div><dt>Production snapshots</dt><dd>{acc["snapshots"].get("production", 0)}</dd></div>'
        f'<div><dt>Completed horizons</dt><dd>{tot_done} of {n * len(HORIZONS)}</dd></div>'
        f'<div><dt>Sample status</dt><dd>{_e(sample)}</dd></div>'
        f'<div><dt>Coverage</dt><dd>{_when(cov.get("first"))} &ndash; {_when(cov.get("last"))}</dd></div>'
        f'<div><dt>Strategy</dt><dd><abbr title="fingerprint {_e(fp)}">{_e(st.get("version") or "?")} &middot; {_e(fp[:7])}</abbr></dd></div>'
        '</dl>'
        '<p class="mut hpnote">State events (signal strength changes) and signal observations (every captured snapshot) '
        'are diagnostics: repeated snapshots of the same signal are not independent trades.</p>'
        f'<p class="mut hpprov">Backtest run {_e(view.get("run") or "")} &middot; generated {_when(view.get("generated_at"))} '
        f'&middot; code {_e((view.get("code_commit") or "")[:7])} &middot; prices to {_e(view.get("prices_asof") or "")}'
        f'{_status_chip(view, now)}{base}</p>')
    mo = acc["market_observations"]
    detail = (f'<details class="hpacc"><summary>Data coverage</summary><p class="mut">'
              f'{acc["snapshots"].get("archived")} archived snapshots: {acc["snapshots"].get("production")} from the production '
              f'feed, {acc["snapshots"].get("excluded_before_expansion")} verification fetches excluded. '
              f'{mo.get("total")} market observations: {mo.get("classified")} classified, {mo.get("unavailable")} unavailable ('
              + ", ".join(f"{v} {_e(k.lower())}" for k, v in (mo.get("unavailable_by_reason") or {}).items())
              + ').</p></details>')
    return (head + summary
            + f'<h3 class="hph3">All markets &mdash; independent episodes by horizon</h3>{_hz_rows(view["stats"].get("all") or {})}'
            + '<h3 class="hph3">By market</h3>' + _markets(view, labels)
            + '<h3 class="hph3">Episode audit</h3>' + _episode_rows(view, labels)
            + detail + _method(view) + "</section>")


def jump_link(view):
    return ('<p class="hpjump"><a href="#hist-perf">Historical performance of these divergences &darr;</a></p>'
            if view.get("state") is not None else "")


_VIEW = None


def current():
    """One load per build (the page builder calls this). Nothing while Myfxbook is switched
    off (the Step 2 rule: no Myfxbook-derived figure on the page then)."""
    global _VIEW
    import myfxbook as mf
    if not mf.enabled():
        return {"state": None}
    if _VIEW is None:
        _VIEW = load()
    return _VIEW


def section(now=None):
    v = current()
    return "" if v.get("state") is None else panel(v, now)


def link():
    return jump_link(current())
