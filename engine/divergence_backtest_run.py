"""Run the Step 3 baseline backtest on real inputs and write a versioned, private report.

    python divergence_backtest_run.py --out DIR [--prices FILE] [--generated-at ISO] [--asof DATE]
                           [--publish PREFIX]

Inputs (no Myfxbook request is ever made - any attempt fails the run):
  * Myfxbook: our own archived production snapshots - the private archive repo
    (myfxbook.archive_load, a GitHub read) united with the local day files in data/.
  * COT: data/cot_legacy.json (the production store, or a fresh CFTC fetch), with the times
    production first saw each report where it recorded them.
  * Prices: data/bt_prices.json from fetch_bt_prices.py.
Outputs in DIR: results.json (every observation, event and statistic - contains Myfxbook
history, so PRIVATE) and manifest.json (versions, coverage, exclusions, hashes). --publish
copies both to the private archive repo under PREFIX (refused unless the repo is private).
The console gets counts and hashes only (Actions logs are public).

Deterministic: --generated-at and --asof (default: generated date minus one day, so only
completed daily bars count) are the only time inputs; rerunning with the same inputs and the
same two values gives byte-identical results.json.
"""
import argparse, datetime as dt, hashlib, json, os, subprocess, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import divergence_backtest as bt                                             # noqa: E402
import divergence_backtest_config as bc                                      # noqa: E402
import cot_table as ct                                            # noqa: E402
import myfxbook as mf                                             # noqa: E402
from config import DATA                                           # noqa: E402

BLOCKED = []


def _no_myfxbook():
    """Fail closed: the backtest consumes archived data only."""
    real = urllib.request.urlopen

    def guard(req, *a, **k):
        url = req if isinstance(req, str) else getattr(req, "full_url", str(req))
        if "myfxbook.com" in url.lower():
            BLOCKED.append(url.split("?")[0])
            raise OSError("blocked: the backtest never contacts Myfxbook")
        return real(req, *a, **k)
    urllib.request.urlopen = guard


def _h(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def load_history(data, use_archive=True):
    rows = {}
    for r in mf.load_history(data):
        rows[(r["timestamp"], r["symbol"])] = r
    n_local = len(rows)
    n_arch = 0
    if use_archive and os.environ.get("MYFXBOOK_HISTORY_TOKEN"):
        for r in mf.archive_load():
            n_arch += 1
            rows.setdefault((r["timestamp"], r["symbol"]), r)
    return [rows[k] for k in sorted(rows)], {"local_rows": n_local, "archive_rows": n_arch}


PROVENANCE = "backtest/provenance.json"


def _repo_json(path):
    token = os.environ.get("MYFXBOOK_HISTORY_TOKEN", "")
    repo = os.environ.get("MYFXBOOK_HISTORY_REPO") or mf.ARCHIVE_REPO
    if not token:
        return None
    cur = mf._gh(urllib.request.urlopen, "GET", f"/repos/{repo}/contents/{path}", token)
    if not cur or not cur.get("content"):
        return None
    import base64
    return json.loads(base64.b64decode(cur["content"]).decode())


def production_timestamps(extra_files=()):
    """Which snapshots the PRODUCTION feed made: the shared request ledger (production attempts,
    rolling 24 h - read only, never written here) united with the accumulated registry
    backtest/provenance.json (kept by this run). Returns (set or None, info)."""
    seen, src = set(), []
    led = _repo_json(mf.LEDGER_PATH)
    if led:
        seen |= set((led.get("production") or {}).get("attempts") or [])
        src.append("request ledger")
    reg = _repo_json(PROVENANCE)
    if reg:
        seen |= set(reg.get("production") or [])
        src.append("provenance registry")
    # production's own attempt list (the restored production cache) - covers the hours since
    # the ledger's last 6-hourly sync
    stp = Path(os.environ.get("BT_DATA") or DATA) / mf.STATE_NAME
    if stp.exists():
        try:
            seen |= set(json.loads(stp.read_text()).get("attempts") or [])
            src.append("production state cache")
        except ValueError:
            pass
    for f in extra_files:
        d = json.loads(Path(f).read_text())
        seen |= set((d.get("production") or {}).get("attempts") or d.get("production") or [])
        src.append(Path(f).name)
    return (seen if src else None), src


def first_seen_of(store):
    st = (store or {}).get("status") or {}
    fs = dict(st.get("first_seen") or {})
    if st.get("report_date") and st.get("report_first_seen_at"):
        fs.setdefault(st["report_date"], st["report_first_seen_at"])
    return fs


def manifest(res, rows, store, prices, fs, generated_at, asof, src):
    obs, ev = res["observations"], res["events"]
    snaps = res["snapshots"]
    reasons = {}
    for x in res["excluded"]:
        for w in x["why"]:
            key = w.split(" - ")[0]
            reasons[key] = reasons.get(key, 0) + 1
    cov = {}
    for k, spec in bc.PRICES.items():
        m = (prices.get("markets") or {}).get(k) or {}
        cov[k] = ({"symbol": spec["symbol"], "what": spec["what"], "timezone": m.get("timezone"),
                   "price_type": m.get("price_type"), "bars": len(m.get("dates") or []),
                   "first": (m.get("dates") or [None])[0], "last": (m.get("dates") or [None])[-1],
                   "error": m.get("error")} if spec else {"symbol": None, "why": "no Myfxbook data for this market"})
    reports = sorted({d for c in (store or {}).get("contracts", {}).values() for d in c.get("weeks", {})})
    used = sorted({o["cot_report"] for o in obs if o.get("cot_report")})
    try:
        commit = os.environ.get("GITHUB_SHA") or subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                                                 text=True, cwd=Path(__file__).parent).stdout.strip()
    except Exception:                                            # noqa: BLE001
        commit = None
    fp = bt.fingerprint()
    return {
        "strategy": {"version": bc.STRATEGY_VERSION, "frozen_at": bc.STRATEGY_COMMIT, "fingerprint": fp,
                     "is_baseline": fp == bc.BASELINE_FINGERPRINT},
        "code_commit": commit, "generated_at": generated_at, "prices_asof": asof,
        "myfxbook": {"snapshots": len(snaps), "first": snaps[0] if snaps else None, "last": snaps[-1] if snaps else None,
                     "rows": len(rows), "sources": src, "requests_made": 0, "blocked_attempts": len(BLOCKED)},
        "cot": {"reports_in_store": len(reports), "first": reports[0] if reports else None,
                "last": reports[-1] if reports else None, "reports_used": used,
                "used_available_at": {d: bt._iso(bt.cot_available_at(d, fs.get(d))) for d in used},
                "first_seen_recorded": fs},
        "observations": {"total": len(obs), "classified": sum(o["cls"] != "unavailable" for o in obs),
                         "signals": sum(o["cls"] in bt.ACTIONABLE for o in obs),
                         "by_class": {c: sum(o["cls"] == c for o in obs) for c in
                                      ("strong_bull", "bull", "none", "bear", "strong_bear", "unavailable")}},
        "events": {"total": len(ev), "by_class": {c: sum(e["cls"] == c for e in ev) for c in bt.ACTIONABLE},
                   "by_market": {k: sum(e["market"] == k for e in ev) for k in sorted({e["market"] for e in ev})}},
        "excluded": {"observations": len(res["excluded"]), "by_reason": dict(sorted(reasons.items()))},
        "price_coverage": cov, "price_source": bc.PRICE_SOURCE,
        "input_hashes": {"myfxbook_rows": _h(rows), "cot_store": _h((store or {}).get("contracts", {})),
                         "prices": _h(prices.get("markets") or {})},
        "result_hash": bt.result_hash(res),
    }


def publish(prefix, files):
    token = os.environ.get("MYFXBOOK_HISTORY_TOKEN", "")
    repo = os.environ.get("MYFXBOOK_HISTORY_REPO") or mf.ARCHIVE_REPO
    if not token:
        print("publish: no archive token - results kept in the run only")
        return False
    meta = mf._gh(urllib.request.urlopen, "GET", f"/repos/{repo}", token)
    if not meta or meta.get("private") is not True:
        print("publish: refused - the archive repo is not private")
        return False
    for name, blob in files.items():
        mf._put(urllib.request.urlopen, token, repo, f"{prefix}/{name}", blob, f"backtest: {prefix}/{name}")
    print(f"publish: {len(files)} file(s) written to the private archive repo under {prefix}/")
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--prices", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--generated-at", default=None)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("--publish", default=None)
    ap.add_argument("--provenance", action="append", default=[],
                    help="local ledger / registry JSON with production snapshot times (tests, offline runs)")
    ap.add_argument("--all-snapshots", action="store_true",
                    help="include verification (non-production) snapshots - NOT the baseline")
    a = ap.parse_args(argv)
    _no_myfxbook()
    data = Path(a.data)
    os.environ["BT_DATA"] = str(data)
    gen = a.generated_at or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    asof = a.asof or (bt._ts(gen).date() - dt.timedelta(days=1)).isoformat()
    rows, src = load_history(data, not a.no_archive)
    store = ct.load(data / ct.OUT_NAME) or ct.empty_store()
    pp = Path(a.prices) if a.prices else data / "bt_prices.json"
    prices = json.loads(pp.read_text()) if pp.exists() else {"markets": {}}
    fs = first_seen_of(store)
    prod, prov_src = (None, ["all snapshots (--all-snapshots)"]) if a.all_snapshots else production_timestamps(a.provenance)
    if prod is None and not a.all_snapshots:
        print("provenance: no production record available - refusing to treat every snapshot as production")
        prod = set()
    res = bt.replay(rows, store, {k: v for k, v in (prices.get("markets") or {}).items() if "dates" in v},
                    first_seen=fs, asof=asof, production=prod)
    man = manifest(res, rows, store, prices, fs, gen, asof, src)
    snaps_all = sorted({r["timestamp"] for r in rows})
    man["myfxbook"]["provenance"] = {
        "sources": prov_src, "baseline_rule": "production snapshots only" if not a.all_snapshots else "ALL snapshots (not baseline)",
        "archived_snapshots": len(snaps_all),
        "production_snapshots": len(res["snapshots"]),
        "excluded_non_production": len(snaps_all) - len(res["snapshots"])}
    if a.all_snapshots:
        man["strategy"]["is_baseline"] = False
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = json.dumps(res, sort_keys=True, separators=(",", ":")).encode()
    mani = json.dumps(man, sort_keys=True, indent=1).encode()
    (out / "results.json").write_bytes(results)
    (out / "manifest.json").write_bytes(mani)
    # public log: counts and hashes only
    m = man
    print(f"backtest {m['strategy']['version']} (baseline: {m['strategy']['is_baseline']}, fingerprint {m['strategy']['fingerprint']})")
    print(f"  Myfxbook snapshots: {m['myfxbook']['snapshots']} ({m['myfxbook']['first']} .. {m['myfxbook']['last']}), "
          f"rows {m['myfxbook']['rows']}, Myfxbook requests made: 0, blocked attempts: {m['myfxbook']['blocked_attempts']}")
    print(f"  COT reports in store: {m['cot']['reports_in_store']}, used: {', '.join(m['cot']['reports_used']) or 'none'}")
    print(f"  observations: {m['observations']['total']} ({m['observations']['signals']} signal), "
          f"events: {m['events']['total']}, excluded: {m['excluded']['observations']}")
    print("  price coverage: " + ", ".join(f"{k} {v['bars']}" for k, v in m["price_coverage"].items() if v.get("symbol")))
    print(f"  result hash {m['result_hash']}")
    print(f"  provenance: {', '.join(prov_src) or 'none'}; production snapshots {len(res['snapshots'])} "
          f"of {len(snaps_all)} archived")
    if a.publish:
        publish(a.publish, {"results.json": results, "manifest.json": mani})
        if prod:
            reg = sorted(set(prod) & set(snaps_all) | set((_repo_json(PROVENANCE) or {}).get("production") or []))
            publish("backtest", {"provenance.json": json.dumps({"production": reg}, indent=1).encode()})
    return 1 if BLOCKED else 0


if __name__ == "__main__":
    raise SystemExit(main())
