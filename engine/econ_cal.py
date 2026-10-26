"""PR #14 — Economic calendar data layer (official sources + TradingView schedule).

Fetches high-impact macro releases + central-bank rates from public-domain / open-licence
official sources, normalises them to one event schema, writes data/econ_calendar.json for
the public page (econ_cal_public.py), and appends point-in-time observations to a history
store for later research (first-release value + revisions + timestamps + source).

Additionally reads upcoming events from data/calendar.json (written by fetch_calendar.py
in the news-watch workflow) to provide a forward-looking schedule — the official sources
only give released data, not future dates. TradingView events carry proper timestamps
(enabling countdown timers) and surprise scores (enabling colour-coded impact borders).
These events are deduplicated against official data so official sources take precedence.

A failure in any single source is caught and recorded (that source marked unavailable);
the build never raises, so it can run as a continue-on-error pipeline step.

Keyless v1 sources (verified live on the verify/econ-endpoints branch):
  USD  BLS (CPI, unemployment, payrolls)          EUR  Eurostat HICP + ECB policy rate
  GBP  ONS CPI (website JSON) + BoE Bank Rate      CAD  StatCan CPI + Bank of Canada rate
  AUD  ABS CPI (best-effort) + RBA cash rate
  ALL  Myfxbook economic calendar (forward schedule, with permission)
Deferred (need a free key or more endpoint work; documented in the PR):
  USD FRED release calendar, JPY e-Stat, NZD Stats NZ, CHF BFS/SNB, WTI EIA.

The parse_* functions are PURE (text/bytes -> events) so they unit-test against fixtures
with no network. fetch()/build() are the thin live wrappers.
"""
import datetime as dt
import html.parser
import json
import re
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
             scheduled=False, source="", source_url="", decimals=1):
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
            "status": status, "scheduled": scheduled,
            "source": source, "source_url": source_url}


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


# ---------------------------------------------------------------- Myfxbook calendar
# With Myfxbook's permission, we scrape their public economic calendar to get a
# forward-looking schedule of upcoming events (the official sources above only
# give released data, not a forward schedule).

_MYFX_CCYS = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
_MYFX_URL = "https://www.myfxbook.com/forex-economic-calendar"


class _MyfxParser(html.parser.HTMLParser):
    """Extracts events from the Myfxbook economic calendar HTML table."""

    def __init__(self):
        super().__init__()
        self.events = []
        self._in_row = False
        self._cells = []
        self._cur = ""
        self._in_td = False
        self._td_class = ""
        self._current_date = None
        self._td_count = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr" and a.get("class", "").startswith("calendar_row"):
            self._in_row = True
            self._cells = []
            self._td_count = 0
        elif tag == "td" and self._in_row:
            self._in_td = True
            self._td_class = a.get("class", "")
            self._cur = ""
            self._td_count += 1
        elif tag == "span" and self._in_row:
            pass  # span contents collected via handle_data

    def handle_data(self, data):
        if self._in_td:
            self._cur += data.strip()

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td:
            self._in_td = False
            self._cells.append((self._td_class, self._cur.strip()))
        elif tag == "tr" and self._in_row:
            self._in_row = False
            self._process_row()

    def _process_row(self):
        cells = self._cells
        if len(cells) < 6:
            return
        # Myfxbook row order: date/time | time-left | (icon) | currency | impact | event | actual | forecast | previous
        # The cell contents vary; we find currency and event by content pattern
        ccy, event_name, impact, actual, forecast, prev = "", "", "Medium", "", "", ""
        time_str = ""
        for cls, val in cells:
            if "calendarDate" in cls:
                # Parse the date string like "Oct 20, 01:00"
                time_str = val
            elif "calendar_impact" in cls or "calendarImpact" in cls:
                if "high" in cls.lower() or "calendar_impact_high" in cls or "redFont" in val:
                    impact = "High"
                elif "medium" in cls.lower() or "calendar_impact_medium" in cls or "orangeFont" in val:
                    impact = "Medium"
                elif "low" in cls.lower() or "calendar_impact_low" in cls:
                    impact = "Low"
            elif len(val) == 3 and val.upper() in _MYFX_CCYS:
                ccy = val.upper()
            elif not ccy and re.match(r"^[A-Z]{3}$", val):
                ccy = val

        # Try to find event name — usually the longest text cell after currency
        text_cells = [v for c, v in cells if len(v) > 3 and v.upper() not in _MYFX_CCYS
                      and not re.match(r"^[\d.,%KMBT€$£¥-]+$", v)
                      and v not in ("High", "Medium", "Low", "No Impact")]
        if text_cells:
            event_name = text_cells[0]

        if not ccy or not event_name:
            return

        # Parse actual/forecast/previous from the last numeric-ish cells
        num_cells = [v for c, v in cells[-4:] if v and v != "\xa0"]

        self.events.append({
            "ccy": ccy, "event": event_name, "impact": impact,
            "time_str": time_str, "actual": num_cells[0] if len(num_cells) > 0 else "",
            "forecast": num_cells[1] if len(num_cells) > 1 else "",
            "previous": num_cells[2] if len(num_cells) > 2 else "",
        })


def fetch_myfxbook():
    """Fetch upcoming events from Myfxbook economic calendar. Returns list of normalised events."""
    try:
        req = urllib.request.Request(_MYFX_URL, headers={
            "User-Agent": "fxstrength-desk/1.0 (+https://haroontahir73.github.io/fxstrength-app)",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
    except Exception:                                                # noqa: BLE001
        return []

    return parse_myfxbook(raw)


def parse_myfxbook(html_text):
    """Parse Myfxbook HTML calendar into normalised events."""
    events = []
    now = NOW()
    year = now.year

    # Myfxbook renders events in table rows with class "calendar_row*"
    # Try structured parsing first
    parser = _MyfxParser()
    try:
        parser.feed(html_text)
    except Exception:                                                # noqa: BLE001
        pass

    if parser.events:
        for ev in parser.events:
            ccy = ev["ccy"]
            if ccy not in _MYFX_CCYS:
                continue
            # Parse time string like "Oct 20, 01:00" or "Oct 20"
            when = _parse_myfx_time(ev.get("time_str", ""), year, now)
            is_future = when and when > now - dt.timedelta(hours=1)
            status = "scheduled" if is_future else "released"
            actual = ev.get("actual", "").strip()
            has_actual = bool(actual and actual not in ("", "-", "\xa0", "—"))
            if has_actual:
                status = "released"

            events.append(mk_event(
                ccy, ev["event"], "macro", ev["impact"],
                actual_raw=_f(actual) if has_actual else None,
                previous_raw=_f(ev.get("previous", "")) if ev.get("previous") else None,
                when=when, has_time=bool(when),
                status=status, scheduled=True,
                source="Myfxbook", source_url=_MYFX_URL,
            ))
        return events

    # Fallback: regex-based extraction for simpler HTML structures
    # Match rows that contain currency codes and event descriptions
    row_pat = re.compile(
        r'<tr[^>]*class="calendar_row[^"]*"[^>]*>(.*?)</tr>', re.S | re.I
    )
    for m in row_pat.finditer(html_text):
        row_html = m.group(1)
        # Extract currency
        ccy_m = re.search(r'>([A-Z]{3})</(?:td|span)', row_html)
        if not ccy_m or ccy_m.group(1) not in _MYFX_CCYS:
            continue
        ccy = ccy_m.group(1)

        # Extract event name — look for the main text cell
        name_m = re.search(r'calendarEventName[^>]*>([^<]+)', row_html)
        if not name_m:
            # Try any link text that looks like an event name
            name_m = re.search(r'<a[^>]*>([A-Z][^<]{5,})</a>', row_html)
        if not name_m:
            continue
        event_name = name_m.group(1).strip()

        # Impact
        impact = "Medium"
        if re.search(r'calendar_impact_high|impact.*high|redFont', row_html, re.I):
            impact = "High"
        elif re.search(r'calendar_impact_low|impact.*low', row_html, re.I):
            impact = "Low"

        # Time
        time_m = re.search(r'calendarDate[^>]*>([^<]+)', row_html)
        time_str = time_m.group(1).strip() if time_m else ""
        when = _parse_myfx_time(time_str, year, now)
        is_future = when and when > now - dt.timedelta(hours=1)

        events.append(mk_event(
            ccy, event_name, "macro", impact,
            when=when, has_time=bool(when),
            status="scheduled" if is_future else "released",
            scheduled=True,
            source="Myfxbook", source_url=_MYFX_URL,
        ))

    return events


def _parse_myfx_time(s, year, now):
    """Parse Myfxbook time strings like 'Oct 20, 01:00' or 'Oct 20'. Returns UTC datetime."""
    if not s:
        return None
    s = s.strip()
    # Try "Mon DD, HH:MM" format
    m = re.match(r"(\w{3})\s+(\d{1,2}),?\s+(\d{1,2}):(\d{2})", s)
    if m:
        mon, day, hr, mn = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        try:
            month = dt.datetime.strptime(mon, "%b").month
            # Handle year rollover (Dec events when now is Jan)
            y = year if month >= now.month - 1 else year + 1
            return dt.datetime(y, month, day, hr, mn, tzinfo=dt.timezone.utc)
        except ValueError:
            return None
    # Try "Mon DD" without time
    m = re.match(r"(\w{3})\s+(\d{1,2})", s)
    if m:
        mon, day = m.group(1), int(m.group(2))
        try:
            month = dt.datetime.strptime(mon, "%b").month
            y = year if month >= now.month - 1 else year + 1
            return dt.datetime(y, month, day, 12, 0, tzinfo=dt.timezone.utc)
        except ValueError:
            return None
    return None


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


_TV_CCYS = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}

# ---------------------------------------------------------------- ForexFactory upcoming
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_CACHE = DATA / "ff_cal_cache.json"        # separate from speakers.py's ff_cache.json
FF_MAX_AGE_H = 4                             # refresh more often than speakers (6h)
FF_CCY_MAP = {"USD": "USD", "GBP": "GBP", "JPY": "JPY", "EUR": "EUR",
              "AUD": "AUD", "NZD": "NZD", "CAD": "CAD", "CHF": "CHF"}


def _fetch_ff_upcoming(data=None):
    """Fetch ForexFactory this-week feed and return normalised upcoming events.

    The feed only serves the current week (Mon-Fri). There is no next-week endpoint.
    Events are returned with scheduled=True so the calendar page shows them in the
    upcoming tabs with countdown timers."""
    now = NOW()
    cache = (Path(data) / "ff_cal_cache.json") if data else FF_CACHE

    # --- cache layer (the feed rate-limits aggressively)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            age = (now - dt.datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 3600
            if age < FF_MAX_AGE_H:
                rows = cached["rows"]
            else:
                rows = None
        except Exception:
            rows = None
    else:
        rows = None

    if rows is None:
        try:
            req = urllib.request.Request(FF_URL, headers={"User-Agent": "Mozilla/5.0"})
            rows = json.loads(urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace"))
            cache.write_text(json.dumps({"fetched_at": now.isoformat(), "rows": rows}), encoding="utf-8")
        except Exception as e:
            # fall back to stale cache
            if cache.exists():
                try:
                    rows = json.loads(cache.read_text(encoding="utf-8"))["rows"]
                except Exception:
                    return [], f"unavailable ({type(e).__name__})"
            else:
                return [], f"unavailable ({type(e).__name__})"

    out = []
    for ev in rows:
        ccy = FF_CCY_MAP.get(ev.get("country"))
        if not ccy:
            continue
        impact = ev.get("impact", "Low")
        # Skip Holiday entries — they aren't real data events
        if impact == "Holiday":
            continue
        title = ev.get("title", "").strip()
        if not title:
            continue
        try:
            when = dt.datetime.fromisoformat(ev["date"]).astimezone(dt.timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        # Only future events (allow 1h grace for just-released)
        if when <= now - dt.timedelta(hours=1):
            continue
        e = mk_event(
            ccy, title, "macro", impact,
            previous_raw=_f(ev.get("previous")),
            when=when, has_time=True,
            status="scheduled", scheduled=True,
            source="ForexFactory",
            source_url="https://www.forexfactory.com/calendar",
        )
        # FF provides display strings for forecast/previous
        if ev.get("forecast"):
            e["forecast"] = ev["forecast"]
        if ev.get("previous") and not e["previous"]:
            e["previous"] = ev["previous"]
        out.append(e)
    return out, "ok"


def _read_tv_upcoming(data_dir):
    """Read upcoming events from data/calendar.json (written by fetch_calendar.py).

    Returns a list of normalised events (mk_event format) with scheduled=True and
    proper dt_utc timestamps, ready for the day-tabs and countdown timers."""
    p = data_dir / "calendar.json"
    if not p.exists():
        return []
    try:
        cal = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    now = NOW()
    out = []
    for ev in cal.get("upcoming", []):
        ccy = ev.get("ccy", "")
        if ccy not in _TV_CCYS:
            continue
        impact = ev.get("impact", "Low")
        try:
            when = dt.datetime.fromisoformat(ev["when"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        # Only include future events
        if when <= now - dt.timedelta(hours=1):
            continue
        e = mk_event(
            ccy, ev.get("title", ""), "macro", impact,
            actual_raw=ev.get("actual_raw"),
            previous_raw=ev.get("previous_raw"),
            unit=ev.get("unit", ""),
            when=when, has_time=True,
            status="scheduled", scheduled=True,
            source="TradingView", source_url="https://www.tradingview.com/economic-calendar/",
        )
        # TradingView provides pre-formatted display strings for forecast/previous
        if ev.get("forecast"):
            e["forecast"] = ev["forecast"]
        if ev.get("previous") and not e["previous"]:
            e["previous"] = ev["previous"]
        out.append(e)
    return out


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
            # #1: never fabricate a release datetime from the fetch clock. A period
            # indicator with no verified release time keeps dt_utc=None (shown as a
            # historical release by its reference period, not placed on a day/time).
            events.extend(evs)
            status[name] = {"ok": True, "as_of": now.isoformat(), "n": len(evs), "ccy": src_label}
        except Exception as e:                                    # noqa: BLE001
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160], "ccy": src_label}

    # TradingView forward-looking schedule (from data/calendar.json, written by fetch_calendar.py)
    try:
        tv_upcoming = _read_tv_upcoming(data)
        if tv_upcoming:
            # Deduplicate: keep official-source events over TradingView when both exist
            # for the same (currency, event-name-tokens) pair
            official_keys = set()
            for e in events:
                toks = frozenset(w.lower() for w in re.findall(r"[A-Za-z]+", e.get("event", "")) if len(w) >= 3)
                official_keys.add((e["currency"], toks))
            added = 0
            for e in tv_upcoming:
                toks = frozenset(w.lower() for w in re.findall(r"[A-Za-z]+", e.get("event", "")) if len(w) >= 3)
                dominated = False
                for oc, ot in official_keys:
                    if oc == e["currency"] and ot and toks and len(ot & toks) / max(len(ot), len(toks)) > 0.4:
                        dominated = True
                        break
                if not dominated:
                    events.append(e)
                    added += 1
            status["TradingView-schedule"] = {"ok": True, "as_of": now.isoformat(), "n": added, "ccy": "ALL"}
        else:
            status["TradingView-schedule"] = {"ok": False, "error": "no upcoming events in calendar.json", "ccy": "ALL"}
    except Exception as e:                                           # noqa: BLE001
        status["TradingView-schedule"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160], "ccy": "ALL"}

    # ForexFactory forward-looking schedule (this-week feed, replaces TradingView when it fails)
    try:
        ff_upcoming, ff_status_msg = _fetch_ff_upcoming(data)
        if ff_upcoming:
            # Build dedup keys from all events already collected (official + TV)
            existing_keys = set()
            for e in events:
                toks = frozenset(w.lower() for w in re.findall(r"[A-Za-z]+", e.get("event", "")) if len(w) >= 3)
                existing_keys.add((e["currency"], toks))
            added = 0
            for e in ff_upcoming:
                toks = frozenset(w.lower() for w in re.findall(r"[A-Za-z]+", e.get("event", "")) if len(w) >= 3)
                dominated = False
                for oc, ot in existing_keys:
                    if oc == e["currency"] and ot and toks and len(ot & toks) / max(len(ot), len(toks)) > 0.4:
                        dominated = True
                        break
                if not dominated:
                    events.append(e)
                    existing_keys.add((e["currency"], toks))
                    added += 1
            status["ForexFactory-schedule"] = {"ok": True, "as_of": now.isoformat(), "n": added, "ccy": "ALL"}
        else:
            status["ForexFactory-schedule"] = {"ok": False, "error": ff_status_msg, "ccy": "ALL"}
    except Exception as e:                                           # noqa: BLE001
        status["ForexFactory-schedule"] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160], "ccy": "ALL"}

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
