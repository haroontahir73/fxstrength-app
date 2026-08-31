"""Fetch weekly CFTC COT positioning + open interest.

PRIMARY source is CFTC's own public feed (Socrata, publicreporting.cftc.gov) - a government
API with no datacenter-IP / Cloudflare blocking, so it works from CI runners. tradingster.com
is kept as a local fallback (it 403s from many datacenter IPs). Both carry the same underlying
CFTC data; net values match exactly.

Stdlib only. Writes data/cot.json (current week) and appends to the weekly history files
data/cot_history.json (currencies, Leveraged Funds + Asset Manager + Dealer) /
data/cot_history_commodity.json (commodities, Managed Money + Producer + Swap), which feed the
COT-extreme / positioning-turn read (config.cot_extreme).

    python fetch_cot.py                 current week (Socrata, tradingster fallback) + history
    python fetch_cot.py backfill_cftc 10   deep history from CFTC, one query per contract
    python fetch_cot.py backfill 30        older fallback: 30 weeks via tradingster scraping

Also the current open-interest source (fetch_oi.py reads the OI carried in the report).
"""
import json, re, sys, time, urllib.request, datetime
from config import CURRENCIES, COMMODITIES, DATA

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
BASE = "https://www.tradingster.com/cot/futures/fin/{}"
BASE_DISAGG = "https://www.tradingster.com/cot/futures/disagg/{}"   # commodities
NUM = r"([\-\+]?[\d,]+)"
HIST_FX = DATA / "cot_history.json"
HIST_CMDTY = DATA / "cot_history_commodity.json"
HIST_WEEKS = 560          # ~10.7 years - deep enough for a multi-year positioning extreme
SOCRATA_TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"       # TFF report
SOCRATA_DISAGG = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"    # disaggregated
FX_CODES = [m["cot"] for m in CURRENCIES.values()]
CM_CODES = [m["cot"] for m in COMMODITIES.values()]
_FX_BY_CODE = {m["cot"]: c for c, m in CURRENCIES.items()}
_CM_BY_CODE = {m["cot"]: s for s, m in COMMODITIES.items()}


def _get(url, tries=3, timeout=60):
    """GET with a small retry/backoff - CI runners see transient 5xx / resets."""
    last = None
    for n in range(tries):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=timeout).read()
        except Exception as e:                                 # noqa: BLE001
            last = e
            time.sleep(1.5 * (n + 1))
    raise last


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _hist_merge(path, week_rows):
    """week_rows: {report_date: {name: parsed_dict}}. Merge into the date-keyed history,
    drop null / empty entries (backtest.py writes `null` for weeks it could not fetch),
    keep the last HIST_WEEKS dates."""
    try:
        hist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        hist = {}
    for date, rows in week_rows.items():
        if not date:
            continue
        hist.setdefault(date, {}).update({k: v for k, v in rows.items()
                                          if isinstance(v, dict) and "error" not in v})
    # tidy: strip null category rows and any week left with nothing usable
    hist = {d: clean for d, wk in hist.items()
            if (clean := {k: v for k, v in (wk or {}).items() if isinstance(v, dict)})}
    for stale in sorted(hist)[:-HIST_WEEKS]:
        del hist[stale]
    path.write_text(json.dumps(hist, indent=1), encoding="utf-8")


def _clean(v):
    return int(v.replace(",", "").replace("+", ""))


def _text(html):
    t = re.sub(r"<[^>]+>", "|", html)
    t = re.sub(r"\|{2,}", "|", t)
    return re.sub(r"[ \t]+", " ", t)


def parse(html):
    out = {}
    m = re.search(r"AS OF:\s*([\d\-]+)", html)
    out["report_date"] = m.group(1) if m else None

    m = re.search(r"Open Interest:\s*<span class=\"number\">([\d,]+)</span>", html)
    out["open_interest"] = _clean(m.group(1)) if m else None
    m = re.search(r"Total Changes:\s*<span class=\"number\"><span class='[a-z\-]+'>([\-\+][\d,]+)", html)
    out["oi_change"] = _clean(m.group(1)) if m else None

    flat = _text(html)
    tok = re.compile(r"[\-\+]?\d[\d,]*(?:\.\d+)?%?")
    for key, pat in (("asset_manager", r"Asset Manager/\|Institutional"),
                     ("leveraged", r"Leveraged\|Funds"),
                     ("dealer", r"Dealer\|Intermediary")):
        m = re.search(pat, flat)
        if not m:
            out[key] = None
            continue
        # cells run: long, long_chg, long_%, long_traders, short, short_chg, short_%, short_traders
        nums = [x for x in tok.findall(flat[m.end():m.end() + 260]) if not x.endswith("%")]
        if len(nums) < 5:
            out[key] = None
            continue
        lng, lng_chg, sht, sht_chg = (_clean(nums[i]) for i in (0, 1, 3, 4))
        out[key] = {"long": lng, "long_chg": lng_chg, "short": sht, "short_chg": sht_chg,
                    "net": lng - sht, "net_chg": lng_chg - sht_chg}
    return out


def parse_commodity(html):
    """Same layout as parse(), but the DISAGGREGATED report - the speculative category is
    'Managed Money', the FX analogue of Leveraged Funds. Cells run identically:
    long, long_chg, long_%, long_traders, short, short_chg, ..."""
    out = {}
    m = re.search(r"AS OF:\s*([\d\-]+)", html)
    out["report_date"] = m.group(1) if m else None
    m = re.search(r"Open Interest:\s*<span class=\"number\">([\d,]+)</span>", html)
    out["open_interest"] = _clean(m.group(1)) if m else None
    m = re.search(r"Total Changes:\s*<span class=\"number\"><span class='[a-z\-]+'>([\-\+][\d,]+)", html)
    out["oi_change"] = _clean(m.group(1)) if m else None

    flat = _text(html)
    tok = re.compile(r"[\-\+]?\d[\d,]*(?:\.\d+)?%?")
    for key, pat in (("managed_money", r"Managed Money"),
                     ("producer", r"Producer/Merchant"),
                     ("swap", r"Swap Dealers")):
        m = re.search(pat, flat)
        if not m:
            out[key] = None
            continue
        nums = [x for x in tok.findall(flat[m.end():m.end() + 260]) if not x.endswith("%")]
        if len(nums) < 5:
            out[key] = None
            continue
        lng, lng_chg, sht, sht_chg = (_clean(nums[i]) for i in (0, 1, 3, 4))
        # sanity: Managed Money long+short in even the smallest contract runs to thousands.
        # both under 100 means the pattern landed on trader counts, not the position row.
        if lng < 100 and sht < 100:
            out[key] = None
            continue
        out[key] = {"long": lng, "long_chg": lng_chg, "short": sht, "short_chg": sht_chg,
                    "net": lng - sht, "net_chg": lng_chg - sht_chg}
    return out


def fetch(code, base=BASE):
    return _get(base.format(code), timeout=40).decode("utf-8", "replace")


def _cat(r, lf, sf, dlf, dsf):
    lng, sht = _i(r.get(lf)), _i(r.get(sf))
    dl, ds = _i(r.get(dlf)), _i(r.get(dsf))
    return {"long": lng, "short": sht, "long_chg": dl, "short_chg": ds,
            "net": lng - sht, "net_chg": dl - ds}


def _map_tff(r):
    return {"report_date": r["report_date_as_yyyy_mm_dd"][:10],
            "open_interest": _i(r.get("open_interest_all")),
            "oi_change": _i(r.get("change_in_open_interest_all")),
            "leveraged": _cat(r, "lev_money_positions_long", "lev_money_positions_short",
                              "change_in_lev_money_long", "change_in_lev_money_short"),
            "asset_manager": _cat(r, "asset_mgr_positions_long", "asset_mgr_positions_short",
                                  "change_in_asset_mgr_long", "change_in_asset_mgr_short"),
            "dealer": _cat(r, "dealer_positions_long_all", "dealer_positions_short_all",
                           "change_in_dealer_long_all", "change_in_dealer_short_all")}


def _map_disagg(r):
    return {"report_date": r["report_date_as_yyyy_mm_dd"][:10],
            "open_interest": _i(r.get("open_interest_all")),
            "oi_change": _i(r.get("change_in_open_interest_all")),
            "managed_money": _cat(r, "m_money_positions_long_all", "m_money_positions_short_all",
                                  "change_in_m_money_long_all", "change_in_m_money_short_all"),
            "producer": _cat(r, "prod_merc_positions_long", "prod_merc_positions_short",
                             "change_in_prod_merc_long", "change_in_prod_merc_short"),
            "swap": _cat(r, "swap_positions_long_all", "swap__positions_short_all",
                         "change_in_swap_long_all", "change_in_swap_short_all")}


def _socrata_recent(url, codes, days=21):
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    inlist = "%2C".join(f"%27{c}%27" for c in codes)
    q = (f"{url}?%24where=cftc_contract_market_code%20in%28{inlist}%29%20AND%20"
         f"report_date_as_yyyy_mm_dd%3E%27{since}T00%3A00%3A00%27"
         f"&%24order=report_date_as_yyyy_mm_dd%20DESC&%24limit=100")
    return json.loads(_get(q).decode("utf-8", "replace"))


def _current_from_socrata():
    """Latest complete weekly report for every tracked contract, from CFTC. Returns
    ({ccy: row}, {sym: row}, report_date) or raises."""
    fx_rows = _socrata_recent(SOCRATA_TFF, FX_CODES)
    cm_rows = _socrata_recent(SOCRATA_DISAGG, CM_CODES)
    fx_by = {}
    for r in fx_rows:
        fx_by.setdefault(r["report_date_as_yyyy_mm_dd"][:10], {})[
            _FX_BY_CODE[r["cftc_contract_market_code"]]] = _map_tff(r)
    cm_by = {}
    for r in cm_rows:
        cm_by.setdefault(r["report_date_as_yyyy_mm_dd"][:10], {})[
            _CM_BY_CODE[r["cftc_contract_market_code"]]] = _map_disagg(r)
    # newest date that has all currencies AND all commodities
    for d in sorted(set(fx_by) & set(cm_by), reverse=True):
        if len(fx_by[d]) == len(CURRENCIES) and len(cm_by[d]) == len(COMMODITIES):
            return fx_by[d], cm_by[d], d
    raise RuntimeError("Socrata: no complete recent week for all contracts")


def main(prefer="socrata"):
    result = {"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "currencies": {}, "commodities": {}, "source": None}
    prev_c = {}
    if (DATA / "cot.json").exists():
        try:
            prev_c = json.loads((DATA / "cot.json").read_text(encoding="utf-8")).get("commodities", {})
        except Exception:
            prev_c = {}

    # --- primary: CFTC Socrata (one query per report, works from CI) ---
    if prefer == "socrata":
        try:
            fx, cm, rdate = _current_from_socrata()
            age = (datetime.date.today() - datetime.date.fromisoformat(rdate)).days
            if age > 12:
                raise RuntimeError(f"Socrata latest report {rdate} is {age}d old")
            result["currencies"], result["commodities"], result["source"] = fx, cm, "cftc"
            for c in CURRENCIES:
                g = fx[c]["leveraged"]
                print(f"  {c} {rdate} OI {fx[c]['open_interest']:>9,}  lev net {g['net']:+9,} ({g['net_chg']:+,})")
            for s in COMMODITIES:
                g = cm[s]["managed_money"]
                print(f"  {s} {rdate} OI {cm[s]['open_interest']:>9,}  MM net {g['net']:+9,} ({g['net_chg']:+,})")
        except Exception as e:
            print(f"  Socrata primary failed ({type(e).__name__}: {e}) - falling back to tradingster",
                  file=sys.stderr)

    # --- fallback: tradingster scrape for anything still missing ---
    for ccy, meta in CURRENCIES.items():
        if ccy in result["currencies"]:
            continue
        try:
            d = parse(fetch(meta["cot"]))
            if not d.get("leveraged"):
                raise ValueError("Leveraged Funds row not parsed")
            result["currencies"][ccy] = d
            result["source"] = result["source"] or "tradingster"
            print(f"  {ccy} {d['report_date']} (tradingster)")
        except Exception as e:
            print(f"  {ccy} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            result["currencies"][ccy] = {"error": str(e)}

    for sym, meta in COMMODITIES.items():
        if sym in result["commodities"]:
            continue
        try:
            d = parse_commodity(fetch(meta["cot"], BASE_DISAGG))
            if not d.get("managed_money"):
                raise ValueError("Managed Money row not parsed")
            result["commodities"][sym] = d
            result["source"] = result["source"] or "tradingster"
            print(f"  {sym} {d['report_date']} (tradingster)")
        except Exception as e:
            if prev_c.get(sym, {}).get("managed_money"):
                kept = dict(prev_c[sym]); kept["stale"] = True
                result["commodities"][sym] = kept
                print(f"  {sym} fetch failed ({type(e).__name__}); kept cached "
                      f"{kept.get('report_date')}", file=sys.stderr)
            else:
                result["commodities"][sym] = {"error": str(e)}
                print(f"  {sym} FAILED: {type(e).__name__}: {e}", file=sys.stderr)

    (DATA / "cot.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    # append this week to the rolling history (keyed by the report's own AS-OF date)
    fx_date = next((d.get("report_date") for d in result["currencies"].values()
                    if isinstance(d, dict) and d.get("report_date")), None)
    cm_date = next((d.get("report_date") for d in result["commodities"].values()
                    if isinstance(d, dict) and d.get("report_date")), None)
    _hist_merge(HIST_FX, {fx_date: result["currencies"]})
    _hist_merge(HIST_CMDTY, {cm_date: result["commodities"]})
    print(f"wrote {DATA/'cot.json'}  (+history {fx_date} / {cm_date})")
    return result


def _tuesdays(n):
    d = datetime.date.today()
    d -= datetime.timedelta(days=(d.weekday() - 1) % 7)      # back to the last Tuesday
    if (datetime.date.today() - d).days < 3:                 # Friday release not out yet
        d -= datetime.timedelta(days=7)
    return [(d - datetime.timedelta(days=7 * i)).isoformat() for i in range(n)][::-1]


def backfill(weeks=30):
    """Pull historical weekly reports into the history files. tradingster serves the path
    form /cot/futures/<report>/<code>/<YYYY-MM-DD> back roughly six months."""
    have_fx = set(json.loads(HIST_FX.read_text(encoding="utf-8")) if HIST_FX.exists() else {})
    have_cm = set(json.loads(HIST_CMDTY.read_text(encoding="utf-8")) if HIST_CMDTY.exists() else {})
    for date in _tuesdays(weeks):
        fx_rows, cm_rows = {}, {}
        if date not in have_fx:
            for ccy, meta in CURRENCIES.items():
                try:
                    fx_rows[ccy] = parse(fetch(f"{meta['cot']}/{date}"))
                except Exception as e:
                    print(f"  {date} {ccy}: {type(e).__name__}", file=sys.stderr)
        if date not in have_cm:
            for sym, meta in COMMODITIES.items():
                try:
                    cm_rows[sym] = parse_commodity(fetch(f"{meta['cot']}/{date}", BASE_DISAGG))
                except Exception as e:
                    print(f"  {date} {sym}: {type(e).__name__}", file=sys.stderr)
        if fx_rows:
            _hist_merge(HIST_FX, {date: fx_rows})
        if cm_rows:
            _hist_merge(HIST_CMDTY, {date: cm_rows})
        if fx_rows or cm_rows:
            print(f"  backfilled {date}  fx {len(fx_rows)}  commodity {len(cm_rows)}")
    print("backfill done")


def _socrata_all(url, code, since):
    """Every weekly row for one contract since `since` (ISO date). Deep-history use."""
    q = (f"{url}?cftc_contract_market_code={code}"
         f"&%24where=report_date_as_yyyy_mm_dd%3E%27{since}T00%3A00%3A00%27"
         f"&%24order=report_date_as_yyyy_mm_dd&%24limit=3000")
    return json.loads(_get(q, timeout=90).decode("utf-8", "replace"))


def backfill_cftc(years=7):
    """Deep COT history straight from CFTC's public feed - back to 2006, one query per
    contract. Stores every trader category so backtest.py's asset_manager comparison works."""
    since = (datetime.date.today() - datetime.timedelta(days=365 * years + 14)).isoformat()

    fx_weeks = {}
    for ccy, meta in CURRENCIES.items():
        rows = _socrata_all(SOCRATA_TFF, meta["cot"], since)
        for r in rows:
            fx_weeks.setdefault(r["report_date_as_yyyy_mm_dd"][:10], {})[ccy] = _map_tff(r)
        print(f"  {ccy}: {len(rows)} weeks  ({rows[0]['report_date_as_yyyy_mm_dd'][:10]} .. "
              f"{rows[-1]['report_date_as_yyyy_mm_dd'][:10]})" if rows else f"  {ccy}: none")

    cm_weeks = {}
    for sym, meta in COMMODITIES.items():
        rows = _socrata_all(SOCRATA_DISAGG, meta["cot"], since)
        for r in rows:
            cm_weeks.setdefault(r["report_date_as_yyyy_mm_dd"][:10], {})[sym] = _map_disagg(r)
        print(f"  {sym}: {len(rows)} weeks  ({rows[0]['report_date_as_yyyy_mm_dd'][:10]} .. "
              f"{rows[-1]['report_date_as_yyyy_mm_dd'][:10]})" if rows else f"  {sym}: none")

    _hist_merge(HIST_FX, fx_weeks)
    _hist_merge(HIST_CMDTY, cm_weeks)
    fx = json.loads(HIST_FX.read_text(encoding="utf-8"))
    cm = json.loads(HIST_CMDTY.read_text(encoding="utf-8"))
    print(f"cot_history.json now {len(fx)} weeks ({min(fx)} .. {max(fx)}); "
          f"commodity {len(cm)} weeks ({min(cm)} .. {max(cm)})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        backfill(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif len(sys.argv) > 1 and sys.argv[1] == "backfill_cftc":
        backfill_cftc(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    else:
        main()
