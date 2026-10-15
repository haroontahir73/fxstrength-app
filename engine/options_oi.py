"""Options Futures-Equivalent OI pipeline (PR #12, data only - no dashboard UI).

Reads the CFTC Commitments of Traders reports (futures-only and futures-and-options-combined,
same family, same market, same week) and derives the weekly delta-adjusted options exposure:

    options futures-equivalent OI = combined open interest - futures-only open interest

See options_oi_config.py for the full explanation and the market mapping. Everything here is
public CFTC data, so the generated archive is public-safe: no credentials, no Myfxbook, no CME.

    python options_oi.py                 # fetch from CFTC, rebuild the archive, print a summary
    python options_oi.py --render-only   # recompute metrics from the stored archive, no network

NOTHING is fabricated. A market/date is calculated ONLY when a futures-only value and a
combined value exist for the exact same CFTC market code, report date, report family and
contract-unit definition. Any mismatch, missing side, or a combined OI below futures-only
(which cannot happen legitimately - options add exposure) fails that observation visibly
rather than being shown. Metrics over fewer than their window show the real sample length.
"""
import datetime as dt
import json
import urllib.parse
import urllib.request
from pathlib import Path

import options_oi_config as cfg
from config import DATA

ARCHIVE = DATA / "options_fe_oi.json"          # the generated, public-safe series (gitignored)
UA = {"User-Agent": "fxstrength-desk/1.0 (CFTC public reporting)"}


class Mismatch(Exception):
    """A market/date where futures-only and combined cannot be legitimately subtracted."""


# ---------------------------------------------------------------- fetch (injectable opener)
def _opener(opener=None):
    return opener or (lambda req, timeout=60: urllib.request.urlopen(req, timeout=timeout))


def _get(ds, codes, opener, limit=60000):
    """Every weekly row for `codes` from one Socrata dataset, newest first. Only the fields
    the calculation needs, so futures-only and combined are compared like for like."""
    inlist = ",".join(f"'{c}'" for c in codes)
    params = {
        "$select": "cftc_contract_market_code,report_date_as_yyyy_mm_dd,open_interest_all,contract_units",
        "$where": f"cftc_contract_market_code in({inlist})",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": str(limit),
    }
    url = cfg.SOCRATA.format(ds=ds) + "?" + urllib.parse.urlencode(params, safe="(),'")
    raw = _opener(opener)(urllib.request.Request(url, headers=UA)).read()
    return json.loads(raw)


def _index(rows):
    """{code: {date: {"oi": int, "units": str}}} from Socrata rows, ignoring unparseable OI."""
    out = {}
    for r in rows:
        code = r.get("cftc_contract_market_code")
        date = (r.get("report_date_as_yyyy_mm_dd") or "")[:10]
        oi = r.get("open_interest_all")
        if not (code and date and oi not in (None, "")):
            continue
        try:
            out.setdefault(code, {})[date] = {"oi": int(oi), "units": (r.get("contract_units") or "").strip()}
        except (TypeError, ValueError):
            continue
    return out


def fetch_family(family, codes, opener=None):
    """(futures-only index, combined index) for one report family."""
    s = cfg.SOURCES[family]
    return _index(_get(s["futonly"], codes, opener)), _index(_get(s["combined"], codes, opener))


# ---------------------------------------------------------------- the calculation (pure)
def _observation(market, date, fo, comb, now_iso):
    """One validated weekly observation, or raise Mismatch. `fo`/`comb` are the per-date dicts
    {"oi":, "units":} for this market from each report. Both must exist (caller guarantees)."""
    if fo["units"] != comb["units"]:
        raise Mismatch(f"{market['key']} {date}: unit definitions differ "
                       f"({fo['units']!r} vs {comb['units']!r})")
    fe = comb["oi"] - fo["oi"]
    if fe < 0:
        # Combined folds options INTO futures, so it is always >= futures-only. A negative
        # result is a data error, not a tradeable fact - fail rather than display it.
        raise Mismatch(f"{market['key']} {date}: combined OI {comb['oi']} below futures-only "
                       f"{fo['oi']} (options cannot reduce combined OI)")
    return {
        "date": date,
        "cftc_code": market["code"],
        "report_family": market["family"],
        "report": cfg.SOURCES[market["family"]]["report"],
        "futures_only_oi": fo["oi"],
        "combined_oi": comb["oi"],
        "options_fe_oi": fe,
        "options_share": fe / comb["oi"] if comb["oi"] else None,
        "contract_units": fo["units"],
        "source_futonly": cfg.SOURCES[market["family"]]["futonly"],
        "source_combined": cfg.SOURCES[market["family"]]["combined"],
        "schema_version": cfg.SCHEMA_VERSION,
        "retrieved_at": now_iso,
    }


def build_series(market, fo_idx, comb_idx, now_iso):
    """All validated weekly observations for one market, oldest first, plus any exclusions.
    A date is used ONLY when both reports carry it for this exact market code."""
    code = market["code"]
    fo, comb = fo_idx.get(code, {}), comb_idx.get(code, {})
    obs, excluded = [], []
    for date in sorted(set(fo) & set(comb)):
        try:
            obs.append(_observation(market, date, fo[date], comb[date], now_iso))
        except Mismatch as e:
            excluded.append({"date": date, "reason": str(e)})
    for date in sorted(set(fo) ^ set(comb)):               # present in only one report
        side = "combined" if date in fo else "futures-only"
        excluded.append({"date": date, "reason": f"{market['key']} {date}: no {side} report for this week"})
    return obs, excluded


# ---------------------------------------------------------------- rolling metrics (pure)
def _percentile(series, value):
    """Fraction of the window at or below `value`, as a percent (0-100). Inclusive rank."""
    if not series:
        return None
    at_or_below = sum(1 for v in series if v <= value)
    return round(at_or_below / len(series) * 100, 1)


def metrics(obs):
    """Latest-week headline metrics for one market's observation list (oldest first). Windows
    shorter than SHORT_WIN / LONG_WIN report what sample they actually had - never padded."""
    if not obs:
        return {"available": False}
    fe = [o["options_fe_oi"] for o in obs]
    latest = obs[-1]
    win_long = fe[-cfg.LONG_WIN:]
    win_short = fe[-cfg.SHORT_WIN:]
    prev = obs[-2]["options_fe_oi"] if len(obs) >= 2 else None
    wk_chg = (latest["options_fe_oi"] - prev) if prev is not None else None
    wk_pct = (wk_chg / prev * 100) if prev not in (None, 0) else None
    return {
        "available": True,
        "date": latest["date"],
        "futures_only_oi": latest["futures_only_oi"],
        "combined_oi": latest["combined_oi"],
        "options_fe_oi": latest["options_fe_oi"],
        "options_share": latest["options_share"],
        "weekly_change": wk_chg,
        "weekly_pct_change": wk_pct,
        "avg_13w": sum(win_short) / len(win_short),
        "avg_52w": sum(win_long) / len(win_long),
        "pctile_52w": _percentile(win_long, latest["options_fe_oi"]),
        "high_52w": max(win_long),
        "low_52w": min(win_long),
        "sample_weeks": len(fe),
        "window_weeks": len(win_long),           # actual length behind the 52w stats (<=52)
    }


# ---------------------------------------------------------------- assemble + archive
def compute(fetched, now=None):
    """Turn {family: (fo_idx, comb_idx)} into the full generated structure. Pure and
    deterministic for a fixed `now` and inputs - the same inputs give byte-identical JSON."""
    now = now or dt.datetime.now(dt.timezone.utc)
    now_iso = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    markets, excluded = {}, {}
    for m in cfg.MARKETS:
        fo_idx, comb_idx = fetched[m["family"]]
        obs, exc = build_series(m, fo_idx, comb_idx, now_iso)
        markets[m["key"]] = {"label": m["label"], "cftc_code": m["code"], "report_family": m["family"],
                             "inverse_pair": m["inverse"], "observations": obs, "metrics": metrics(obs)}
        if exc:
            excluded[m["key"]] = exc
    return {
        "schema_version": cfg.SCHEMA_VERSION,
        "generated_at": now_iso,
        "methodology": cfg.METHODOLOGY,
        "sources": cfg.SOURCES,
        "order": cfg.MARKET_ORDER,
        "markets": markets,
        "excluded": excluded,
    }


def fetch_all(opener=None):
    """{family: (fo_idx, comb_idx)} for every family, one pair of CFTC queries per family."""
    out = {}
    for family in cfg.SOURCES:
        codes = sorted({m["code"] for m in cfg.MARKETS if m["family"] == family})
        out[family] = fetch_family(family, codes, opener)
    return out


def save(view, path=None):
    path = Path(path or ARCHIVE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(view, sort_keys=True, indent=1))
    return path


def load_archive(path=None):
    path = Path(path or ARCHIVE)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def rebuild_metrics(view, now=None):
    """Recompute every market's metrics from its stored observations (no network). Used by
    --render-only and to prove the archive alone reproduces the headline numbers."""
    for key, m in view.get("markets", {}).items():
        m["metrics"] = metrics(m.get("observations", []))
    return view


# ---------------------------------------------------------------- CLI
def _fmt(v):
    return "-" if v is None else f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


def main(argv):
    if "--render-only" in argv:
        view = load_archive()
        if not view:
            print("no archive to render"); return 1
        view = rebuild_metrics(view)
    else:
        view = compute(fetch_all())
        save(view)
    print(f"Options futures-equivalent OI - generated {view['generated_at']}")
    for key in view["order"]:
        m = view["markets"][key]
        mt = m["metrics"]
        if not mt.get("available"):
            print(f"  {key:7} no matching observations"); continue
        pct = "-" if mt["pctile_52w"] is None else f"{mt['pctile_52w']:.0f}"
        print(f"  {key:7} {mt['date']}  FE OI {_fmt(mt['options_fe_oi']):>10}  "
              f"share {mt['options_share'] * 100:5.1f}%  52w pct {pct:>4}  n={mt['sample_weeks']}")
    if view["excluded"]:
        print("exclusions:")
        for key, exc in view["excluded"].items():
            print(f"  {key}: {len(exc)} (e.g. {exc[0]['reason']})")
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                      # noqa: BLE001
        pass
    raise SystemExit(main(sys.argv[1:]))
