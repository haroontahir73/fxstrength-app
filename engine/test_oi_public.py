"""Tests for the CFTC-sourced public Open Interest view (oi_public.py, PR #13).

    python test_oi_public.py

Stdlib only, offline. urlopen is replaced by a spy that blocks and records every request; the
archive is built by the PR #12 pipeline (options_oi.py) on synthetic CFTC rows via a fake
opener, so nothing real is contacted. oi_public itself makes NO network call - it only reads
the generated archive.
"""
import datetime as dt
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import options_oi as oo                                            # noqa: E402
import options_oi_config as cfg                                    # noqa: E402
import oi_public as op                                             # noqa: E402

PASS, FAIL, CALLS = [], [], []
NOW = dt.datetime(2026, 10, 15, 6, 0, tzinfo=dt.timezone.utc)


def _spy(req, *a, **k):
    CALLS.append(getattr(req, "full_url", str(req)))
    raise OSError("network blocked in test_oi_public")


urllib.request.urlopen = _spy


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class _R:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b


class FakeCFTC:
    """Serves synthetic weekly rows for every mapped market, combined OI above futures-only."""
    def __init__(self, weeks=60, last="2026-10-06", fo0=500000, step=1000, opt0=20000, optstep=200):
        self.p = dict(weeks=weeks, last=last, fo0=fo0, step=step, opt0=opt0, optstep=optstep)
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        ds = re.search(r"/resource/([^.]+)\.json", url).group(1)
        codes = re.findall(r"'(\w{6})'", urllib.request.unquote(url))
        combined = ds in (cfg.SOURCES["TFF"]["combined"], cfg.SOURCES["DISAGG"]["combined"])
        last = dt.date.fromisoformat(self.p["last"])
        rows = []
        for c in codes:
            for i in range(self.p["weeks"]):
                d = (last - dt.timedelta(weeks=self.p["weeks"] - 1 - i)).isoformat()
                fo = self.p["fo0"] + self.p["step"] * i
                oi = fo + (self.p["opt0"] + self.p["optstep"] * i if combined else 0)
                rows.append({"cftc_contract_market_code": c, "report_date_as_yyyy_mm_dd": d + "T00:00:00.000",
                             "open_interest_all": str(oi), "contract_units": "(U)"})
        return _R(json.dumps(rows).encode())


def archive(tmp, **kw):
    fake = FakeCFTC(**kw)
    view = oo.compute(oo.fetch_all(opener=fake), now=NOW)
    oo.save(view, Path(tmp) / "options_fe_oi.json")
    return fake


# ---- tests -------------------------------------------------------------------------------
def t_breakdown():
    g = "breakdown"
    d = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d)
    html = op.panel(now=NOW, data=d)
    check(g, "the four-part breakdown is shown (not one unexplained number)",
          all(h in html for h in ("Futures OI", "Options FE OI", "Combined OI", "Options share")))
    check(g, "labelled CFTC weekly", "CFTC weekly" in html)
    view = json.loads((d / "options_fe_oi.json").read_text())
    # every market's latest futures-only and combined OI appear verbatim
    miss = []
    for key, m in view["markets"].items():
        last = m["observations"][-1]
        if f'{last["futures_only_oi"]:,}' not in html or f'{last["combined_oi"]:,}' not in html:
            miss.append(key)
    check(g, "each market's futures-only and combined OI come straight from the archive", not miss, miss)
    check(g, "all 11 mapped markets are present", all(cfg.BY_KEY[k]["label"] in html for k in cfg.MARKET_ORDER))
    check(g, "options FE OI = combined - futures-only, verbatim for EURUSD",
          f'{view["markets"]["EURUSD"]["observations"][-1]["options_fe_oi"]:,}' in html)


def t_no_cme():
    g = "no-cme"
    d = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d)
    html = op.panel(now=NOW, data=d) + op.page_body(now=NOW, data=d)
    check(g, "no CME wording or host anywhere on the public OI surfaces",
          not re.search(r"(?i)\bCME\b|cmegroup|CmeWS|preliminary", html), re.findall(r"(?i).{0,10}(cme|preliminary).{0,10}", html)[:3])
    src = (Path(__file__).parent / "oi_public.py").read_text()
    check(g, "oi_public references no CME / Myfxbook endpoint or host",
          not re.search(r"(?i)cmegroup\.com|CmeWS|myfxbook\.com|/api/get-community", src))
    check(g, "oi_public makes no network call (reads the archive only)",
          "urlopen" not in src and "urlretrieve" not in src)


def t_weekly_metrics():
    g = "metrics"
    d = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d)
    view = op._load(d)
    mt = view["markets"]["EURUSD"]["metrics"]
    obs = view["markets"]["EURUSD"]["observations"]
    fo = [o["futures_only_oi"] for o in obs]
    check(g, "weekly futures-OI change = latest - previous week", mt["fo_weekly_change"] == fo[-1] - fo[-2])
    check(g, "futures-OI 52-week percentile is the inclusive rank (rising series -> 100)", mt["fo_pctile_52w"] == 100.0)
    check(g, "52-week window capped at 52 weeks", mt["window_weeks"] == 52 and mt["sample_weeks"] == 60)
    # a short history reports the real sample size
    d2 = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d2, weeks=6)
    mt2 = op._load(d2)["markets"]["EURUSD"]["metrics"]
    check(g, "a <52-week history reports its real length, not padded", mt2["window_weeks"] == 6 and mt2["sample_weeks"] == 6)


def t_freshness():
    g = "freshness"
    d = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d, last="2026-10-06")                      # NOW is 2026-10-15 -> 9 days -> fresh
    html = op.panel(now=NOW, data=d)
    check(g, "a recent report shows the 'ok' chip with its date", 'oistale ok' in html and "Latest CFTC report" in html)
    d2 = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d2, last="2026-09-20")                     # ~25 days old -> stale
    html2 = op.panel(now=NOW, data=d2)
    check(g, "an old report shows the stale chip", 'oistale old' in html2 and "Stale" in html2)


def t_unavailable():
    g = "states"
    empty = Path(tempfile.mkdtemp(prefix="fxoip-"))
    check(g, "no archive -> explicit unavailable, no fabricated numbers",
          "No CFTC open-interest data" in op.panel(now=NOW, data=empty) and "Futures OI" not in op.panel(now=NOW, data=empty))
    bad = Path(tempfile.mkdtemp(prefix="fxoip-"))
    (bad / "options_fe_oi.json").write_text("{ not json")
    check(g, "malformed archive -> unavailable, no crash", "No CFTC open-interest data" in op.panel(now=NOW, data=bad))
    # a mixed archive: one market has data (so the tab renders), another has none
    d = Path(tempfile.mkdtemp(prefix="fxoip-"))
    obs = [{"date": "2026-10-06", "futures_only_oi": 800000, "combined_oi": 820000,
            "options_fe_oi": 20000, "options_share": 20000 / 820000}]
    v = {"schema_version": 1, "generated_at": "2026-10-15T00:00:00Z", "order": ["EURUSD", "Copper"],
         "markets": {"EURUSD": {"label": "EUR/USD", "observations": obs},
                     "Copper": {"label": "Copper", "observations": []}}, "excluded": {}}
    (d / "options_fe_oi.json").write_text(json.dumps(v))
    html = op.panel(now=NOW, data=d)
    check(g, "a market with no observations shows 'no matching CFTC report', not a value",
          "no matching CFTC report" in html and "820,000" in html)


def t_standalone_page():
    g = "page"
    d = Path(tempfile.mkdtemp(prefix="fxoip-"))
    archive(d)
    body = op.page_body(now=NOW, data=d)
    check(g, "standalone page carries the breakdown and per-market history",
          "Options FE OI" in body and "By market" in body and "weekly history" in body.lower())
    # write_page writes a self-contained file (own CSS, back link)
    import os
    oldpage = op.PAGE
    op.PAGE = Path(d) / "open-interest.html"
    try:
        p = op.write_page(now=NOW, data=d)
        html = p.read_text()
        check(g, "the file is self-contained (doctype, own styles, back link)",
              html.startswith("<!doctype") and "<style>" in html and "back to the desk" in html)
        check(g, "no CME wording in the standalone file", not re.search(r"(?i)\bCME\b|preliminary", html))
    finally:
        op.PAGE = oldpage
        os.path.exists  # noqa


def t_frozen_untouched():
    g = "frozen"
    import divergence_backtest as bt
    check(g, "frozen strategy fingerprint still 0bcac9b16befcd54", bt.fingerprint() == "0bcac9b16befcd54", bt.fingerprint())
    src = (Path(__file__).parent / "oi_public.py").read_text()
    check(g, "oi_public imports no frozen signal module (divergence / myfxbook / history)",
          not re.search(r"import\s+(divergence\b|divergence_backtest\b|divergence_config\b|myfxbook\b|history_panel\b)", src))
    # oi_public must not WRITE the options-FE archive - it only reads it
    check(g, "oi_public does not write the options-FE archive (read-only consumer)",
          "options_fe_oi.json" in src and ".write_text(json" not in src.split("_PAGE_CSS")[0])


if __name__ == "__main__":
    for fn in (t_breakdown, t_no_cme, t_weekly_metrics, t_freshness, t_unavailable, t_standalone_page, t_frozen_untouched):
        try:
            fn()
        except Exception as e:                             # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    groups = {}
    for gp, _ in PASS:
        groups.setdefault(gp, [0, 0])[0] += 1
    for gp, _ in FAIL:
        groups.setdefault(gp, [0, 0])[1] += 1
    print()
    for gp, (okc, bad) in groups.items():
        print(f"{gp}: {okc}/{okc + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    check("safety", "no real network call escaped the suite", CALLS == [], CALLS)
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
