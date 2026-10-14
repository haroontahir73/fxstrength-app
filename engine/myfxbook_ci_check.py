"""Myfxbook checks for build-check.

    python myfxbook_ci_check.py --seed-fixture   before the pipeline, normal PR runs: write a
                                                 SYNTHETIC snapshot so the tab, cards and
                                                 divergence are built and checked without
                                                 any real request
    python myfxbook_ci_check.py --reserve        manual real run only: check the shared request
                                                 budget and record this request first
    python myfxbook_ci_check.py                  after the pipeline: fixture mode proves no
                                                 request was made; real mode verifies the
                                                 real fetch (below)

Real mode (MYFXBOOK_REAL=true - only a manual build-check run with real_myfxbook_test) is the
real-data verification, run AFTER the pipeline has made its one genuine login -> outlook ->
logout:

Prints names, counts and PASS/FAIL only - never a Myfxbook figure (this repo's Actions logs
are public) and never a credential. Exit 1 on any failure.

  1. the real fetch succeeded; returned symbols; our 16 markets mapped / unavailable
  2. cadence: a second build() in the same run makes NO request (25-minute rule)
  3. the real snapshot renders in the Retail tab and in all 16 report cards (markets under
     MIN_POSITIONS positions show the count only), and its
     freshness labels move Fresh -> Delayed -> Stale with age; a failed refresh keeps the
     real figures and marks them "Last known Myfxbook data - stale"
  4. archive: this run's rows are written to the private repo at
     myfxbook/daily/YYYY/YYYY-MM-DD.csv.gz and read back; an immediate second archive()
     is skipped (6-hour rule)
"""
import copy, datetime as dt, json, os, re, shutil, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import myfxbook as mf                                             # noqa: E402
from config import DATA                                           # noqa: E402

OK, BAD = [], []


def ok(cond, what):
    (OK if cond else BAD).append(what)
    print(("  PASS  " if cond else "  FAIL  ") + what)
    return cond


def check_off():
    """MYFXBOOK_ENABLED is not "true": prove nothing reaches Myfxbook or the archive repo -
    every outbound request is recorded and refused - and the page shows no figure."""
    import urllib.request
    calls = []

    def spy(req, *a, **k):
        url = req if isinstance(req, str) else getattr(req, "full_url", str(req))
        calls.append(url.split("?")[0])
        raise OSError("blocked: Myfxbook is switched off")
    real, urllib.request.urlopen = urllib.request.urlopen, spy
    try:
        st_p = DATA / mf.STATE_NAME
        before = st_p.read_bytes() if st_p.exists() else None
        print("Myfxbook switched off (MYFXBOOK_ENABLED is not 'true')")
        mf.build()
        mf.archive(force=True)
        mf.archive(verify_only=True)
        ok(not calls, f"no request to Myfxbook or the archive repo ({len(calls)} attempted)")
        ok((st_p.read_bytes() if st_p.exists() else None) == before, "no fetch attempt recorded")
        html = mf.retail_panel()
        ok("switched off" in html and not re.search(r"LONG|SHORT|Imbalance|data-mfx-key|mfxbar", html),
           "tab shows the switched-off note and no figure")
        ok(all(mf.report_card(k) == "" for k in mf.MARKETS), "no Myfxbook card in any report")
    finally:
        urllib.request.urlopen = real
    return finish()


FIXTURE = {  # market: (symbol, long %, long positions, short positions) - synthetic, not Myfxbook data
    "USD": ("USDX", 40, 30, 20), "AUD": ("AUDUSD", 62, 900, 600), "CAD": ("USDCAD", 45, 800, 900),
    "CHF": ("USDCHF", 25, 500, 1500), "EUR": ("EURUSD", 40, 4000, 6000), "GBP": ("GBPUSD", 72, 2200, 800),
    "JPY": ("USDJPY", 50, 1500, 1500), "NZD": ("NZDUSD", 35, 700, 1300), "XAU": ("XAUUSD", 61, 6000, 3800),
    "XAG": ("XAGUSD", 30, 900, 2100), "HG": ("COPPER", 55, 10, 5), "WTI": ("XTIUSD", 60, 30, 30),
    "ES": ("US500", 30, 20, 40), "NQ": ("NAS100", 35, 20, 20), "YM": ("US30", 80, 30, 2)}


def seed_fixture():
    """Normal PR runs: a synthetic snapshot (marked fixture) in data/, 5 minutes old, so the
    Retail tab, the report cards and the divergence are built and checked with NO request."""
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    markets = {k: {"symbol": s, "longPercentage": lp, "shortPercentage": 100 - lp, "longVolume": 100.5,
                   "shortVolume": 80.25, "longPositions": lpos, "shortPositions": spos, "totalPositions": lpos + spos,
                   "avgLongPrice": 1.2345, "avgShortPrice": 1.2399, "candidates": [s]}
               for k, (s, lp, lpos, spos) in FIXTURE.items()}
    snap = {"fixture": True, "fetched_at": ts, "markets": markets, "symbols_in_response": len(markets),
            "symbols_returned": sorted(m["symbol"] for m in markets.values()), "rejected": [],
            "status": {"configured": True, "last_attempt_at": ts, "last_ok_at": ts, "last_error": None,
                       "last_error_msg": None},
            "source": {"name": mf.SOURCE_NAME, "url": mf.SOURCE_URL}}
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / mf.OUT_NAME).write_text(json.dumps(snap))
    print(f"Myfxbook FIXTURE snapshot written ({len(markets)} markets, synthetic) - no real request in this run")
    return 0


def check_fixture():
    """Normal PR runs: prove that nothing reached Myfxbook (the pipeline had no credentials)
    and that a further build()/archive() also makes no request."""
    import urllib.request
    calls = []

    def spy(req, *a, **k):
        url = req if isinstance(req, str) else getattr(req, "full_url", str(req))
        calls.append(url.split("?")[0])
        raise OSError("blocked: fixture mode makes no real request")
    real, urllib.request.urlopen = urllib.request.urlopen, spy
    try:
        print("Myfxbook FIXTURE mode (no real request; real data only with a manual real_myfxbook_test run)")
        snap = json.loads((DATA / mf.OUT_NAME).read_text())
        ok(snap.get("fixture") is True, "the page was built from the synthetic fixture snapshot")
        st = json.loads((DATA / mf.STATE_NAME).read_text()) if (DATA / mf.STATE_NAME).exists() else {}
        ok(not st.get("attempts"), "the pipeline recorded no Myfxbook request attempt")
        mf.build()
        mf.archive(force=True)
        ok(not calls, f"a further build()/archive() makes no request ({len(calls)} attempted)")
        html = mf.retail_panel()
        ok(html.count('data-mfx-key="') == 16, "Retail tab renders all 16 markets from the fixture")
    finally:
        urllib.request.urlopen = real
    return finish()


def reserve():
    """Manual real run: book this request in the shared ledger first, or stop the job."""
    run = os.environ.get("GITHUB_RUN_ID", "")
    allowed, info = mf.reserve_manual(run=run)
    if not allowed:
        print(f"::error title=Myfxbook request budget::real verification refused - {info}")
        return 1
    print(f"Myfxbook request budget: reserved 1 real request. Estimated 24 h total {info['total']} "
          f"(production <= {info['production']}, manual {info['manual']}) of the {info['safety_budget']} safety "
          f"budget, under the documented {info['limit']}")
    return 0


def main():
    if "--seed-fixture" in sys.argv:
        return seed_fixture()
    if "--reserve" in sys.argv:
        return reserve()
    if not mf.enabled():
        return check_off()
    if os.environ.get("MYFXBOOK_REAL") != "true":
        return check_fixture()
    snap = json.loads((DATA / mf.OUT_NAME).read_text())
    st = json.loads((DATA / mf.STATE_NAME).read_text())
    status = snap.get("status") or {}
    print("1. Real fetch")
    if not ok(bool(snap.get("fetched_at")) and status.get("last_error") is None,
              f"login -> Community Outlook -> logout from this GitHub runner "
              f"(last error: {status.get('last_error') or 'none'})"):
        print(f"     reason: {status.get('last_error_msg')}")
        return finish()
    returned = snap.get("symbols_returned") or []
    markets = snap.get("markets") or {}
    print(f"     returned ({len(returned)}): {', '.join(returned)}")
    mapping = ", ".join(f"{k}<-{markets[k]['symbol']}"
                        + (f" (of {'/'.join(markets[k]['candidates'])})" if len(markets[k].get("candidates", [])) > 1 else "")
                        for k in mf.MARKETS if k in markets)
    unavailable = ", ".join(k for k in mf.MARKETS if k not in markets) or "none"
    few = ", ".join(k for k in mf.MARKETS if k in markets and mf.too_few(markets[k])) or "none"
    print("     mapped: " + mapping)
    print("     unavailable: " + unavailable)
    print(f"     below {mf.MIN_POSITIONS} positions (archived, page shows the count only): {few}")
    # Names only, as a check-run annotation: readable from the PR without opening the log,
    # so the symbol chosen for each market can be reviewed after every real fetch.
    print(f"::notice title=Myfxbook mapping::mapped: {mapping} | unavailable: {unavailable}"
          f" | below {mf.MIN_POSITIONS}: {few}")
    ok(len(returned) > 0 and len(markets) > 0, f"{len(markets)} of our 16 markets matched exactly")
    ok(all(markets[k]["symbol"] in mf.MARKETS[k] for k in markets), "every match is one of that market's own candidates")
    ok(all(markets[k]["symbol"] in markets[k].get("candidates", [markets[k]["symbol"]]) for k in markets),
       "every chosen symbol is among the candidates present")
    rej = snap.get("rejected") or []
    print(f"     rejected entries (excluded, never repaired): {len(rej)}"
          + (" - " + ", ".join(rej) if rej else ""))

    print("2. Cadence (25 min live / 6 h archive)")
    n = len(st.get("attempts", []))
    mf.build()
    st2 = json.loads((DATA / mf.STATE_NAME).read_text())
    ok(len(st2.get("attempts", [])) == n, "a second fetch attempt within 25 min made no request")

    print("3. Real snapshot on the page, freshness and stale handling")
    now = dt.datetime.fromisoformat(snap["fetched_at"].replace("Z", "+00:00"))
    html = mf.retail_panel(now=now + dt.timedelta(minutes=5))
    ok(html.count('data-mfx-key="') == 16 and html.count("Retail sentiment unavailable") == 16 - len(markets),
       "Retail tab: 16 markets, real data where mapped, 'unavailable' elsewhere")
    for k, m in markets.items():
        card = mf.report_card(k, now=now + dt.timedelta(minutes=5))
        row = re.search(rf'data-mfx-key="{k}">(.*?)(?=<div class="mfxrow|</div>\s*<h3|</div>\s*<p class="mfxkey")', html, re.S)
        row = row.group(1) if row else ""
        if mf.too_few(m):
            ok(mf.too_few_text(m) in row and mf.too_few_text(m) in card
               and not re.search(r"LONG \d|Imbalance \d|Retail crowd|mfxbar", row + card),
               f"{k}: below {mf.MIN_POSITIONS} positions - count only, no reading (tab and card)")
            continue
        lp, sp = m["longPercentage"], m["shortPercentage"]
        ok(f"LONG {mf._pc(lp)}%" in row and f"Imbalance {mf.imbalance(lp, sp)}" in card
           and mf.crowd(lp, sp) in card, f"{k}: tab row and report card show the real snapshot")
    for k in mf.MARKETS:
        if k not in markets:
            ok("Retail sentiment unavailable" in mf.report_card(k), f"{k}: report card says unavailable")
    for age, want in ((5, ""), (75, "Delayed"), (200, "Stale")):
        h = mf.retail_panel(now=now + dt.timedelta(minutes=age))
        st_label = re.search(r'class="mfxstate (\w+)">([^<]*)<', h)
        ok(st_label and st_label.group(2) == want, f"age {age} min -> '{want or 'fresh'}' label")
    tmp = Path(tempfile.mkdtemp())
    failed = copy.deepcopy(snap)
    failed["status"].update(last_error="unavailable", last_error_msg="HTTP 503")
    (tmp / mf.OUT_NAME).write_text(json.dumps(failed))
    h = mf.retail_panel(now=now + dt.timedelta(minutes=40), data=tmp)
    shown = [k for k in markets if not mf.too_few(markets[k])]
    k0 = shown[0] if shown else next(iter(markets))
    ok("Last known Myfxbook data &mdash; stale" in h
       and (f"LONG {mf._pc(markets[k0]['longPercentage'])}%" in h if shown else mf.too_few_text(markets[k0]) in h),
       "a failed refresh keeps the real figures and marks them 'Last known Myfxbook data - stale'")
    shutil.rmtree(tmp, ignore_errors=True)

    print("4. Durable history archive (private repo)")
    a = mf.archive(force=True)
    if not ok(a.get("configured") and not a.get("last_error"),
              f"archive write to {a.get('repo')} (error: {a.get('last_error') or 'none'})"):
        print(f"     reason: {a.get('last_error_msg')}")
        return finish()
    day = snap["fetched_at"][:10]
    path = mf._archive_path(day)
    ok(re.fullmatch(r"myfxbook/daily/\d{4}/\d{4}-\d{2}-\d{2}\.csv\.gz", path) is not None,
       f"history path {path}")
    rows = mf.archive_load(since_day=day)
    mine = [r for r in rows if r["timestamp"] == snap["fetched_at"]]
    ok(len(mine) == len(markets) and {r["symbol"] for r in mine} == {m["symbol"] for m in markets.values()},
       f"read back from the archive: this run's {len(mine)} row(s), {len(rows)} row(s) for {day} in total")
    ok(all(set(r) == set(mf.HIST_FIELDS) for r in mine), f"archived rows carry all {len(mf.HIST_FIELDS)} history fields")
    ok({(r["market"], r["symbol"]) for r in mine} == {(k, m["symbol"]) for k, m in markets.items()},
       "each archived row names its market and source symbol (below-threshold markets included)")
    b = mf.archive()
    ok(b.get("last_push_at") == a.get("last_push_at"), "an immediate second archive() is skipped (6-hour rule)")
    return finish()


def finish():
    print(f"\nmyfxbook_ci_check: {len(OK)} passed, {len(BAD)} failed")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
