"""PR #14 — Economic calendar data layer (official sources, keyless v1).

Fetches high-impact macro releases + central-bank rates from public-domain / open-licence
official sources, normalises them to one event schema, writes data/econ_calendar.json for
the public page (econ_cal_public.py), and appends point-in-time observations to a history
store for later research (first-release value + revisions + timestamps + source).

INDEPENDENCE: this module is self-contained. It does NOT import, read or write
fetch_calendar.py / calendar.json, and nothing here feeds the scoring pipeline. A failure
in any single source is caught and recorded (that source marked unavailable); the build
never raises, so it can run as a continue-on-error pipeline step.

Keyless v1 sources (verified live on the verify/econ-endpoints branch):
  USD  BLS (CPI, unemployment, payrolls)          EUR  Eurostat HICP + ECB policy rate
  GBP  ONS CPI (website JSON) + BoE Bank Rate      CAD  StatCan CPI + Bank of Canada rate
  AUD  ABS CPI (best-effort) + RBA cash rate
Deferred (need a free key or more endpoint work; documented in the PR):
  USD FRED release calendar, JPY e-Stat, NZD Stats NZ, CHF BFS/SNB, WTI EIA.

NOTE ON TIMING: the keyless DATA apis give the reference period + value, not a scheduled
release datetime. Rate series are dated by their observation date; period indicators are
dated by the source's own "updated" stamp when present, else left undated (period shown in
the label). A precise forward release-schedule is the "later" enhancement (FRED calendar).

The parse_* functions are PURE (text/bytes -> events) so they unit-test against fixtures
with no network. fetch()/build() are the thin live wrappers.
"""
import datetime as dt
import json
import urllib.request
import urllib.error
from pathlib import Path

from config import DATA

ARCHIVE = DATA / "econ_calendar.json"
HISTORY = DATA / "econ_history.jsonl"
UA = {"User-Agent": "fxstrength-desk/1.0 (+https://haroontahir73.github.io/fxstrength-app)"}
TIMEOUT = 30
NOW = lambda: dt.datetime.now(dt.timezone.utc)                    # noqa: E731
_Y = dt.datetime.now(dt.timezone.utc).year


def _f(x):
    """Float or None — BLS and SDMX use '-' or '' for a missing/suppressed value."""
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- schema helpers
def mk_event(currency, event, category, impact, *, actual_raw=None, previous_raw=None,
             unit="", period="", when=None, has_time=False, status="released",
             source="", source_url="", decimals=1):
    """Build one normalised calendar event. `actual`/`previous` are formatted for display;
    the *_raw values are kept for research and for the neutral change arrow."""
    def fmt(v):
        if v is None:
            return None
        s = f"{v:,.{decimals}f}" if isinstance(v, float) else f"{v:,}"
        return f"{s}{unit}" if unit else s
    eid = f"{source}:{currency}:{event}:{period}".replace(" ", "_")
    return {"id": eid, "currency": currency, "country": currency[:2],
            "event": event, "category": category, "impact": impact,
            "actual": fmt(actual_raw), "actual_raw": actual_raw,
            "forecast": None, "forecast_raw": None,            # no consensus on this path
            "previous": fmt(previous_raw), "previous_raw": previous_raw,
            "unit": unit, "period": period,
            "dt_utc": when.isoformat() if when else None, "has_time": has_time,
            "status": status, "source": source, "source_url": source_url}


def fetch(url, method="GET", body=None, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- pure parsers
def parse_bls_cpi(text, source_url=""):
    """CUUR0000SA0 index -> CPI YoY %. Needs 13 months to compute year-on-year."""
    res = json.loads(text).get("Results", {}).get("series")
    if not res:
        return []            # empty / rate-limited (BLS keyless quota)
    d = res[0]["data"]
    rows = [r for r in d if r.get("period", "").startswith("M")]            # monthly only
    rows.sort(key=lambda r: (r["year"], r["period"]))                       # oldest -> newest
    vals = [(f'{r["year"]}-{r["period"][1:]}', _f(r.get("value"))) for r in rows]
    vals = [(p, v) for p, v in vals if v is not None]
    if len(vals) < 13:
        return []
    def yoy(i):
        return round((vals[i][1] / vals[i - 12][1] - 1) * 100, 1)
    cur, prev = yoy(-1), yoy(-2)
    per = vals[-1][0]
    return [mk_event("USD", "CPI (YoY)", "inflation", "High", actual_raw=cur,
                     previous_raw=prev, unit="%", period=per, source="BLS",
                     source_url=source_url)]


def parse_bls_unrate(text, source_url=""):
    res = json.loads(text).get("Results", {}).get("series")
    if not res:
        return []            # empty / rate-limited (BLS keyless quota)
    d = res[0]["data"]
    rows = sorted((r for r in d if r.get("period", "").startswith("M")),
                  key=lambda r: (r["year"], r["period"]))
    pts = [(f'{r["year"]}-{r["period"][1:]}', _f(r.get("value"))) for r in rows]
    pts = [(p, v) for p, v in pts if v is not None]
    if len(pts) < 2:
        return []
    return [mk_event("USD", "Unemployment rate", "labour", "High",
                     actual_raw=pts[-1][1], previous_raw=pts[-2][1],
                     unit="%", period=pts[-1][0], source="BLS", source_url=source_url)]


def parse_bls_nfp(text, source_url=""):
    """CES0000000001 total nonfarm level (thousands) -> monthly CHANGE = payrolls headline."""
    res = json.loads(text).get("Results", {}).get("series")
    if not res:
        return []            # empty / rate-limited (BLS keyless quota)
    d = res[0]["data"]
    rows = sorted((r for r in d if r.get("period", "").startswith("M")),
                  key=lambda r: (r["year"], r["period"]))
    pts = [(f'{r["year"]}-{r["period"][1:]}', _f(r.get("value"))) for r in rows]
    pts = [(p, v) for p, v in pts if v is not None]
    if len(pts) < 3:
        return []
    lv = [v for _, v in pts]
    cur = round(lv[-1] - lv[-2], 0)                                         # change, thousands
    prev = round(lv[-2] - lv[-3], 0)
    return [mk_event("USD", "Nonfarm payrolls (change, 000s)", "labour", "High",
                     actual_raw=int(cur), previous_raw=int(prev), unit="K", period=pts[-1][0],
                     source="BLS", source_url=source_url)]


def parse_eurostat_hicp(text, source_url=""):
    """JSON-stat 2.0, single geo -> HICP YoY %. value keyed by flattened index; here all
    dimensions are size 1 except time, so the value index == time index."""
    o = json.loads(text)
    tdim = o["dimension"]["time"]["category"]["index"]
    periods = sorted(tdim, key=lambda k: tdim[k])                           # chrono order
    vals = o["value"]
    series = [(p, vals.get(str(tdim[p]))) for p in periods if vals.get(str(tdim[p])) is not None]
    if len(series) < 2:
        return []
    return [mk_event("EUR", "HICP (YoY)", "inflation", "High",
                     actual_raw=float(series[-1][1]), previous_raw=float(series[-2][1]),
                     unit="%", period=series[-1][0], source="Eurostat", source_url=source_url)]


def parse_ecb_rate(text, source_url=""):
    """SDMX-JSON (ECB) single series -> latest main refinancing rate %."""
    o = json.loads(text)
    times = o["structure"]["dimensions"]["observation"][0]["values"]
    series = next(iter(o["dataSets"][0]["series"].values()))
    obs = series["observations"]
    idx = sorted(obs, key=lambda k: int(k))
    pts = [(times[int(i)]["id"], obs[i][0]) for i in idx if obs[i] and obs[i][0] is not None]
    if not pts:
        return []
    cur = pts[-1]
    prev = pts[-2] if len(pts) > 1 else (None, None)
    return [mk_event("EUR", "ECB main refinancing rate", "central bank", "High",
                     actual_raw=float(cur[1]), previous_raw=(float(prev[1]) if prev[1] is not None else None),
                     unit="%", period=cur[0], source="ECB", source_url=source_url, decimals=2)]


def parse_boc_rate(text, source_url=""):
    o = json.loads(text)
    obs = o.get("observations", [])
    pts = [(x["d"], float(x["V39079"]["v"])) for x in obs if x.get("V39079", {}).get("v") not in (None, "")]
    if not pts:
        return []
    when = dt.datetime.fromisoformat(pts[-1][0]).replace(tzinfo=dt.timezone.utc)
    prev = pts[-2][1] if len(pts) > 1 else None
    return [mk_event("CAD", "BoC overnight rate target", "central bank", "High",
                     actual_raw=pts[-1][1], previous_raw=prev, unit="%", period=pts[-1][0],
                     when=when, has_time=False, source="Bank of Canada", source_url=source_url, decimals=2)]


def parse_statcan_cpi(text, source_url=""):
    """CPI all-items index -> YoY % (needs 13 monthly points)."""
    o = json.loads(text)[0]["object"]
    dp = o.get("vectorDataPoint", [])
    pts = [(x["refPer"], float(x["value"])) for x in dp]
    pts.sort(key=lambda t: t[0])
    if len(pts) < 13:                                                       # not enough for YoY
        if len(pts) < 2:
            return []
        cur = round((pts[-1][1] / pts[-2][1] - 1) * 100, 1)                 # fallback: MoM %
        return [mk_event("CAD", "CPI (MoM)", "inflation", "High", actual_raw=cur,
                         previous_raw=None, unit="%", period=pts[-1][0][:7],
                         source="StatCan", source_url=source_url)]
    yoy = lambda i: round((pts[i][1] / pts[i - 12][1] - 1) * 100, 1)        # noqa: E731
    return [mk_event("CAD", "CPI (YoY)", "inflation", "High", actual_raw=yoy(-1),
                     previous_raw=yoy(-2), unit="%", period=pts[-1][0][:7],
                     source="StatCan", source_url=source_url)]


def parse_boe_rate(text, source_url=""):
    """BoE IADB CSV: DATE,IUDBEDR."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    pts = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) >= 2:
            try:
                pts.append((parts[0].strip(), float(parts[1])))
            except ValueError:
                continue
    if not pts:
        return []
    prev = next((v for _, v in reversed(pts[:-1]) if v != pts[-1][1]), pts[-2][1] if len(pts) > 1 else None)
    return [mk_event("GBP", "BoE Bank Rate", "central bank", "High", actual_raw=pts[-1][1],
                     previous_raw=prev, unit="%", period=pts[-1][0], source="Bank of England",
                     source_url=source_url, decimals=2)]


def parse_ons_cpi(text, source_url=""):
    """ONS website timeseries JSON (D7G7/MM23) — CPI annual rate %. Uses the 'months' list."""
    o = json.loads(text)
    months = o.get("months") or []
    pts = []
    for m in months:
        try:
            pts.append((m.get("date", ""), float(m["value"])))
        except (ValueError, KeyError):
            continue
    if len(pts) < 2:
        return []
    return [mk_event("GBP", "CPI (YoY)", "inflation", "High", actual_raw=pts[-1][1],
                     previous_raw=pts[-2][1], unit="%", period=pts[-1][0],
                     source="ONS", source_url=source_url)]


def parse_rba_rate(text, source_url=""):
    """RBA F1 CSV — the 'Cash Rate Target' column, last non-empty value."""
    rows = [ln.split(",") for ln in text.splitlines()]
    col = None
    for r in rows:
        for j, cell in enumerate(r):
            if cell.strip().strip('"').lower() == "cash rate target":
                col = j
                break
        if col is not None:
            break
    if col is None:
        return []
    vals = []
    for r in rows:
        if len(r) > col:
            cell = r[col].strip().strip('"')
            try:
                vals.append(float(cell))
            except ValueError:
                continue
    if not vals:
        return []
    prev = next((v for v in reversed(vals[:-1]) if v != vals[-1]), None)
    return [mk_event("AUD", "RBA cash rate target", "central bank", "High",
                     actual_raw=vals[-1], previous_raw=prev, unit="%", period="latest",
                     source="RBA", source_url=source_url, decimals=2)]


def parse_abs_cpi(text, source_url=""):
    """ABS SDMX-JSON 2.0 (best-effort) — latest CPI observation. ABS CPI is quarterly."""
    o = json.loads(text)
    ds = (o.get("data") or {}).get("dataSets") or []
    structs = (o.get("data") or {}).get("structures") or o.get("structure")
    if not ds:
        return []
    series = ds[0].get("series") or {}
    obs = next(iter(series.values()), {}).get("observations") if series else ds[0].get("observations")
    if not obs:
        return []
    idx = sorted(obs, key=lambda k: int(k.split(":")[-1]) if ":" in k else int(k))
    pts = [obs[i][0] for i in idx if obs[i] and obs[i][0] is not None]
    if len(pts) < 2:
        return []
    return [mk_event("AUD", "CPI (index)", "inflation", "High", actual_raw=float(pts[-1]),
                     previous_raw=float(pts[-2]), period="latest", source="ABS",
                     source_url=source_url)]


# ---------------------------------------------------------------- live sources
SOURCES = [
    ("USD-CPI", "https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0", {}, parse_bls_cpi),
    ("USD-UNRATE", "https://api.bls.gov/publicAPI/v1/timeseries/data/LNS14000000", {}, parse_bls_unrate),
    ("USD-NFP", "https://api.bls.gov/publicAPI/v1/timeseries/data/CES0000000001", {}, parse_bls_nfp),
    ("EUR-HICP", "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
     "prc_hicp_manr?format=JSON&lang=EN&coicop=CP00&geo=EA&lastTimePeriod=6", {}, parse_eurostat_hicp),
    ("EUR-ECB", "https://data-api.ecb.europa.eu/service/data/FM/"
     "B.U2.EUR.4F.KR.MRR_FR.LEV?format=jsondata&lastNObservations=4", {}, parse_ecb_rate),
    ("GBP-CPI", "https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data",
     {}, parse_ons_cpi),
    ("GBP-BOE", "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?"
     "csv.x=yes&Datefrom=01/Jan/2024&Dateto=now&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N",
     {}, parse_boe_rate),
    ("CAD-CPI", "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods",
     {"method": "POST", "body": [{"vectorId": 41690973, "latestN": 14}]}, parse_statcan_cpi),
    ("CAD-BOC", "https://www.bankofcanada.ca/valet/observations/V39079/json?recent=8", {}, parse_boc_rate),
    ("AUD-RBA", "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv", {}, parse_rba_rate),
]


def build(data=None, offline=False):
    """Fetch every source, normalise, write the archive + append history. Never raises."""
    data = Path(data or DATA)
    now = NOW()
    events, status = [], {}
    for name, url, opt, parser in SOURCES:
        src_label = name.split("-")[0]
        try:
            raw = fetch(url, method=opt.get("method", "GET"),
                        body=opt.get("body"), headers=opt.get("headers"))
            evs = parser(raw, source_url=url)
            if not evs:
                raise RuntimeError("parser returned no events")
            for e in evs:
                if e.get("dt_utc") is None:                      # period indicator: stamp as released now
                    e["dt_utc"] = now.replace(microsecond=0).isoformat()
                    e["has_time"] = False
            events.extend(evs)
            status[name] = {"ok": True, "as_of": now.isoformat(), "n": len(evs), "ccy": src_label}
        except Exception as e:                                    # noqa: BLE001
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160], "ccy": src_label}

    _append_history(events, now, data)
    archive = {"built_at": now.isoformat(), "data_as_of": now.isoformat(),
               "tz": "Europe/London", "events": events, "sources": status,
               "degraded": any(not s.get("ok") for s in status.values())}
    (data / ARCHIVE.name).write_text(json.dumps(archive, indent=2), encoding="utf-8")
    return archive


def _append_history(events, now, data):
    """Point-in-time research store: first time a (id) period is seen -> first_release;
    a changed value for an already-seen id -> a revision. Append-only JSONL."""
    hp = data / HISTORY.name
    seen = {}
    if hp.exists():
        try:
            for ln in hp.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    seen[r["id"]] = r.get("value_raw")
        except Exception:                                         # noqa: BLE001
            seen = {}
    lines = []
    for e in events:
        if e.get("actual_raw") is None:
            continue
        prior = seen.get(e["id"], "__none__")
        if prior == "__none__":
            kind = "first_release"
        elif prior != e["actual_raw"]:
            kind = "revision"
        else:
            continue                                             # unchanged, nothing to log
        lines.append(json.dumps({"id": e["id"], "currency": e["currency"], "event": e["event"],
                                 "period": e["period"], "value_raw": e["actual_raw"],
                                 "seen_utc": now.isoformat(), "kind": kind, "source": e["source"]}))
    if lines:
        with hp.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    a = build()
    ok = sum(1 for s in a["sources"].values() if s.get("ok"))
    print(f"econ calendar: {len(a['events'])} events from {ok}/{len(a['sources'])} sources")
    for n, s in a["sources"].items():
        print(f"  {'OK ' if s.get('ok') else 'ERR'} {n}: {s.get('n', s.get('error',''))}")
