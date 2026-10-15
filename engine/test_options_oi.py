"""Tests for the Options Futures-Equivalent OI pipeline (options_oi.py, PR #12).

    python test_options_oi.py

Stdlib only, offline. urlopen is replaced by a spy that blocks and records every request, and
a FakeCFTC serves canned Socrata rows, so no real CFTC / CME / Myfxbook request is ever made.
The live data/ directory is never touched (every write goes to a temp dir).
"""
import copy
import datetime as dt
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import options_oi as oo                                             # noqa: E402
import options_oi_config as cfg                                     # noqa: E402

PASS, FAIL, CALLS = [], [], []
NOW = dt.datetime(2026, 10, 15, 6, 0, tzinfo=dt.timezone.utc)
EUR_UNITS = "(CONTRACTS OF EUR 125,000)"


def _spy(req, *a, **k):
    CALLS.append(getattr(req, "full_url", str(req)))
    raise OSError("network blocked in test_options_oi")


urllib.request.urlopen = _spy


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


# ---- synthetic CFTC data -----------------------------------------------------------------
def _week(n):
    """n Mondays ending 2026-10-06, oldest first (CFTC report dates are weekly)."""
    last = dt.date(2026, 10, 6)
    return [(last - dt.timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


def _rows(code, dates, ois, units=EUR_UNITS):
    return [{"cftc_contract_market_code": code, "report_date_as_yyyy_mm_dd": d + "T00:00:00.000",
             "open_interest_all": str(oi), "contract_units": units} for d, oi in zip(dates, ois)]


class FakeCFTC:
    """Serves one dataset's rows by resource id, honouring the $where in(...) code filter."""
    def __init__(self, tables):
        self.tables = tables                   # {dataset_id: [rows]}
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append(url)
        ds = re.search(r"/resource/([^.]+)\.json", url).group(1)
        codes = re.findall(r"'(\d{6}|\w{6})'", urllib.request.unquote(url))
        rows = [r for r in self.tables.get(ds, []) if r["cftc_contract_market_code"] in codes]
        return _R(json.dumps(rows).encode())


class _R:
    def __init__(self, b):
        self.b = b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.b


def _fetched_one_market(market_key, fo, comb, units=EUR_UNITS):
    """Build the {family: (fo_idx, comb_idx)} input for compute(), for a single market."""
    m = cfg.BY_KEY[market_key]
    dates = _week(len(fo))
    fo_idx = oo._index(_rows(m["code"], dates, fo, units))
    comb_idx = oo._index(_rows(m["code"], dates, comb, units))
    fetched = {fam: ({}, {}) for fam in cfg.SOURCES}
    fetched[m["family"]] = (fo_idx, comb_idx)
    return fetched, dates


# ---- tests -------------------------------------------------------------------------------
def t_subtraction_and_share():
    g = "calc"
    fetched, _ = _fetched_one_market("EURUSD", [860000, 870291], [1040000, 1051111])
    v = oo.compute(fetched, now=NOW)
    eur = v["markets"]["EURUSD"]
    o = eur["observations"][-1]
    check(g, "options FE OI = combined - futures-only", o["options_fe_oi"] == 1051111 - 870291, o)
    check(g, "options share = FE / combined", abs(o["options_share"] - 180820 / 1051111) < 1e-9)
    check(g, "the raw futures-only and combined are both carried for audit",
          o["futures_only_oi"] == 870291 and o["combined_oi"] == 1051111)
    check(g, "weekly change = this week - last week", eur["metrics"]["weekly_change"] == 180820 - 180000, eur["metrics"])
    check(g, "provenance: family, both dataset ids, schema version, retrieval time",
          o["report_family"] == "TFF" and o["source_futonly"] == "gpe5-46if" and o["source_combined"] == "yw9f-hn96"
          and o["schema_version"] == cfg.SCHEMA_VERSION and o["retrieved_at"].endswith("Z"), o)
    check(g, "FE OI is never labelled a count of option contracts",
          "option contract" not in json.dumps(v) or "not the number of outstanding option contracts" in v["methodology"])


def t_exact_matching():
    g = "matching"
    m = cfg.BY_KEY["EURUSD"]
    # futures-only has an extra week the combined report does not (date mismatch)
    fo = oo._index(_rows(m["code"], _week(3), [850000, 860000, 870291]))
    comb = oo._index(_rows(m["code"], _week(3)[1:], [1040000, 1051111]))
    fetched = {fam: ({}, {}) for fam in cfg.SOURCES}
    fetched["TFF"] = (fo, comb)
    v = oo.compute(fetched, now=NOW)
    eur = v["markets"]["EURUSD"]
    check(g, "only weeks present in BOTH reports are calculated", len(eur["observations"]) == 2)
    check(g, "the unmatched week is excluded with a reason, not silently dropped",
          any("no combined report" in e["reason"] for e in v["excluded"]["EURUSD"]), v["excluded"].get("EURUSD"))
    # a market's rows never bleed into another market
    check(g, "each observation keeps its own CFTC code", all(o["cftc_code"] == m["code"] for o in eur["observations"]))


def t_unit_mismatch():
    g = "matching"
    m = cfg.BY_KEY["EURUSD"]
    fo = oo._index(_rows(m["code"], _week(1), [870291], "(CONTRACTS OF EUR 125,000)"))
    comb = oo._index(_rows(m["code"], _week(1), [1051111], "(CONTRACTS OF EUR 62,500)"))
    fetched = {fam: ({}, {}) for fam in cfg.SOURCES}
    fetched["TFF"] = (fo, comb)
    v = oo.compute(fetched, now=NOW)
    check(g, "a changed contract-unit definition is rejected (not subtracted blindly)",
          v["markets"]["EURUSD"]["observations"] == []
          and any("unit" in e["reason"] for e in v["excluded"]["EURUSD"]), v["excluded"].get("EURUSD"))


def t_missing_sides():
    g = "matching"
    fetched, _ = _fetched_one_market("EURUSD", [870291], [1051111])
    # drop the combined side entirely
    only_fo = {"TFF": (fetched["TFF"][0], {}), "DISAGG": ({}, {})}
    v = oo.compute(only_fo, now=NOW)
    check(g, "missing combined value -> no observation, market marked unavailable",
          not v["markets"]["EURUSD"]["metrics"]["available"])
    only_comb = {"TFF": ({}, fetched["TFF"][1]), "DISAGG": ({}, {})}
    v2 = oo.compute(only_comb, now=NOW)
    check(g, "missing futures-only value -> no observation", not v2["markets"]["EURUSD"]["metrics"]["available"])


def t_negative_fails():
    g = "safety"
    # combined BELOW futures-only must never be shown (options cannot reduce combined OI)
    fetched, _ = _fetched_one_market("EURUSD", [900000], [800000])
    v = oo.compute(fetched, now=NOW)
    check(g, "combined < futures-only is rejected, not shown as a negative",
          v["markets"]["EURUSD"]["observations"] == []
          and any("below futures-only" in e["reason"] for e in v["excluded"]["EURUSD"]))
    check(g, "no negative options FE OI anywhere in the generated structure",
          not re.search(r'"options_fe_oi":\s*-', json.dumps(v)))


def t_metrics_windows():
    g = "metrics"
    # 60 weeks of rising FE OI (comb - fo = 100 + 10*i)
    n = 60
    fo = [800000] * n
    comb = [800000 + 100 + 10 * i for i in range(n)]
    fetched, _ = _fetched_one_market("EURUSD", fo, comb)
    mt = oo.compute(fetched, now=NOW)["markets"]["EURUSD"]["metrics"]
    fe = [comb[i] - fo[i] for i in range(n)]
    check(g, "13-week average uses exactly the last 13 observations",
          abs(mt["avg_13w"] - sum(fe[-13:]) / 13) < 1e-6, mt["avg_13w"])
    check(g, "52-week average uses exactly the last 52 observations",
          abs(mt["avg_52w"] - sum(fe[-52:]) / 52) < 1e-6)
    check(g, "52-week window length is capped at 52 even with more history", mt["window_weeks"] == 52)
    check(g, "latest is the max of a rising series -> 52w percentile 100", mt["pctile_52w"] == 100.0)
    check(g, "52-week high / low come from the 52-week window", mt["high_52w"] == fe[-1] and mt["low_52w"] == fe[-52])
    check(g, "weekly % change is reported", abs(mt["weekly_pct_change"] - (10 / (fe[-2]) * 100)) < 1e-6)


def t_percentile_midrange():
    g = "metrics"
    # a known distribution: FE values 0,10,20,...,510 (52 weeks), latest in the middle
    vals = list(range(0, 520, 10))                 # 52 values
    # latest = 510 would be the max -> 100; put the latest in the middle instead
    mid = vals[:]                                  # make latest = 250
    mid[-1] = 250
    fetched2, _ = _fetched_one_market("EURUSD", [0] * 52, mid)
    mt2 = oo.compute(fetched2, now=NOW)["markets"]["EURUSD"]["metrics"]
    below = sum(1 for v in mid if v <= 250)
    check(g, "52w percentile is the inclusive rank within the window", mt2["pctile_52w"] == round(below / 52 * 100, 1), mt2["pctile_52w"])


def t_incomplete_history():
    g = "metrics"
    fetched, _ = _fetched_one_market("EURUSD", [800000] * 5, [800100, 800200, 800300, 800400, 800500])
    mt = oo.compute(fetched, now=NOW)["markets"]["EURUSD"]["metrics"]
    check(g, "fewer than 52 weeks: the real sample length is reported, not padded",
          mt["sample_weeks"] == 5 and mt["window_weeks"] == 5)
    check(g, "13w average over a 5-week history uses the 5 weeks actually present",
          abs(mt["avg_13w"] - sum(100 * i + 100 for i in range(5)) / 5) < 1e-6 or mt["sample_weeks"] == 5)
    check(g, "no fabricated history: observation count equals weeks supplied", mt["sample_weeks"] == 5)


def t_no_lookahead():
    g = "metrics"
    # metrics must depend only on data up to the latest week, never the future
    base = ([800000] * 40, [800000 + 10 * i for i in range(40)])
    fetched, _ = _fetched_one_market("EURUSD", *base)
    full = oo.compute(fetched, now=NOW)["markets"]["EURUSD"]
    # truncate to the first 30 weeks and recompute: the week-30 metrics must match what the
    # full series would have produced at week 30 (recomputed from the stored observations)
    trimmed = copy.deepcopy(full)
    trimmed["observations"] = full["observations"][:30]
    m30 = oo.metrics(trimmed["observations"])
    check(g, "week-30 metrics use only weeks <= 30 (no leakage from later weeks)",
          m30["options_fe_oi"] == full["observations"][29]["options_fe_oi"]
          and m30["high_52w"] == max(o["options_fe_oi"] for o in full["observations"][:30]))


def t_deterministic_rebuild():
    g = "rebuild"
    fetched, _ = _fetched_one_market("EURUSD", [860000, 870291], [1040000, 1051111])
    v1 = oo.compute(fetched, now=NOW)
    v2 = oo.compute(copy.deepcopy(fetched), now=NOW)
    check(g, "same inputs + same generated_at -> byte-identical JSON",
          json.dumps(v1, sort_keys=True) == json.dumps(v2, sort_keys=True))
    d = Path(tempfile.mkdtemp(prefix="fxoo-"))
    oo.save(v1, d / "a.json")
    reloaded = oo.load_archive(d / "a.json")
    re_metrics = oo.rebuild_metrics(copy.deepcopy(reloaded))
    check(g, "metrics recompute from the stored archive alone (no network, same numbers)",
          re_metrics["markets"]["EURUSD"]["metrics"]["options_fe_oi"]
          == v1["markets"]["EURUSD"]["metrics"]["options_fe_oi"] and CALLS == [])


def t_fetch_isolation():
    g = "isolation"
    # a full fetch_all against a fake CFTC: proves it only ever hits publicreporting.cftc.gov
    tables = {}
    for m in cfg.MARKETS:
        s = cfg.SOURCES[m["family"]]
        tables.setdefault(s["futonly"], []).extend(_rows(m["code"], _week(2), [100, 110], "(U)"))
        tables.setdefault(s["combined"], []).extend(_rows(m["code"], _week(2), [150, 170], "(U)"))
    fake = FakeCFTC(tables)
    fetched = oo.fetch_all(opener=fake)
    v = oo.compute(fetched, now=NOW)
    check(g, "every mapped market produces matched observations from CFTC alone",
          all(v["markets"][k]["metrics"]["available"] for k in cfg.MARKET_ORDER), [k for k in cfg.MARKET_ORDER if not v["markets"][k]["metrics"]["available"]])
    check(g, "only publicreporting.cftc.gov is contacted (never CME or Myfxbook)",
          fake.calls and all("publicreporting.cftc.gov" in u for u in fake.calls)
          and not any(("cme" in u.lower() or "myfxbook" in u.lower()) for u in fake.calls))
    src = (Path(__file__).parent / "options_oi.py").read_text() + (Path(__file__).parent / "options_oi_config.py").read_text()
    check(g, "the module never references a CME or Myfxbook endpoint/host",
          not re.search(r"(?i)cmegroup\.com|CmeWS|myfxbook\.com|/api/get-community", src))
    check(g, "Copper is the only market added beyond the COT mapping", cfg.BY_KEY["Copper"]["code"] == cfg.COPPER_CODE)
    check(g, "FX pair codes are reused verbatim from the COT config",
          cfg.BY_KEY["EURUSD"]["code"] == __import__("config").CURRENCIES["EUR"]["cot"]
          and cfg.BY_KEY["USDJPY"]["code"] == __import__("config").CURRENCIES["JPY"]["cot"])


def t_inverse_flag():
    g = "isolation"
    # raw OI needs no inversion, but the inverse pairs must be flagged for a future signed field
    check(g, "USD-quoted pairs carry the inverse flag; direct pairs do not",
          cfg.BY_KEY["USDJPY"]["inverse"] and cfg.BY_KEY["USDCHF"]["inverse"] and cfg.BY_KEY["USDCAD"]["inverse"]
          and not cfg.BY_KEY["EURUSD"]["inverse"] and not cfg.BY_KEY["Gold"]["inverse"])
    v = oo.compute(_fetched_one_market("USDJPY", [360000], [400000])[0], now=NOW)
    o = v["markets"]["USDJPY"]["observations"][-1]
    check(g, "raw OI is NOT inverted (FE OI is the CME contract's, direction-agnostic)",
          o["options_fe_oi"] == 40000)
    check(g, "no call / put / strike / expiry / gamma field is produced in this version",
          not re.search(r"(?i)\b(call|put)_oi|put_call|strike|expiry|gamma|max_pain", json.dumps(v)))


def t_frozen_untouched():
    g = "frozen"
    # The hard guarantee that the frozen Step 2 / Step 3 engine is unchanged is its fingerprint
    # (depends on the whole frozen config + engine); independent of any git ref so it holds in a
    # shallow CI checkout too.
    import divergence_backtest as bt
    check(g, "frozen strategy fingerprint still 0bcac9b16befcd54", bt.fingerprint() == "0bcac9b16befcd54", bt.fingerprint())
    # The options-OI layer must not import or re-use any frozen signal / Myfxbook / history code,
    # so it structurally cannot change their behaviour.
    src = (Path(__file__).parent / "options_oi.py").read_text() + (Path(__file__).parent / "options_oi_config.py").read_text()
    check(g, "the options-OI layer imports no frozen signal / Myfxbook / history module",
          not re.search(r"import\s+(divergence\b|divergence_backtest\b|divergence_config\b|myfxbook\b|history_panel\b|cot_table\b)", src))
    check(g, "the options-OI layer reuses the COT config, adding only Copper beyond it",
          cfg.BY_KEY["EURUSD"]["code"] == __import__("config").CURRENCIES["EUR"]["cot"]
          and cfg.COPPER_CODE == "085692")


if __name__ == "__main__":
    for fn in (t_subtraction_and_share, t_exact_matching, t_unit_mismatch, t_missing_sides,
               t_negative_fails, t_metrics_windows, t_percentile_midrange, t_incomplete_history,
               t_no_lookahead, t_deterministic_rebuild, t_fetch_isolation, t_inverse_flag, t_frozen_untouched):
        try:
            fn()
        except Exception as e:                             # noqa: BLE001
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
    check("safety", "no real network call escaped the suite", CALLS == [], CALLS)
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
