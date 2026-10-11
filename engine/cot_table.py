"""Legacy COT report: the classic Non-Commercial vs Commercial tables, and a 50-week report
for every row.

THE DATA
--------
One source for every figure, current and historical: the CFTC Commitments of Traders
**Legacy report, Futures Only** - Socrata dataset `6dca-aqww` on publicreporting.cftc.gov.
Rows CFTC does not mark `FutOnly` are rejected, so a futures-and-options row can never mix
in. This is a separate view: the rest of the COT tab (TFF Leveraged Funds for currencies,
disaggregated Managed Money for commodities) and every score are untouched, and Legacy
"Non-Commercial" is never relabelled as either of those.

The rows are futures used as proxies for the spot pairs. USD is the ICE US Dollar Index
future - the dollar against a six-currency basket - not a pair. Every commodity and index
row is one named CFTC contract (code and CFTC name shown on the page); nothing is
substituted or summed - the Dow is the $5 e-mini, NOT also the micro.

STORAGE  data/cot_legacy.json  (gitignored; carried in the refresh workflow's cache)
-------
ORIGINAL futures positions exactly as CFTC publishes them, per contract and report date:
    [open interest, NC long, NC short, NC spreading, Comm long, Comm short,
     non-reportable long, non-reportable short]
No pair reversal is stored. History accumulates: a fetch adds new weeks and corrects
revised ones but never drops older weeks (capped at KEEP_WEEKS). Every row is validated
before it is stored - FutOnly, non-negative integers, and CFTC's own accounting identity
(NC long + spreading + Comm long + non-reportable long = open interest, same for shorts).

THE TABLES (computed from storage at render time)
----------
Per group (Non-Commercial, Commercial):
    Total   = Long + Short of that group, spreading excluded. NOT open interest.
    % Long  = Long / Total x 100        % Short = Short / Total x 100
    Net     = Long - Short
Net Difference  = Non-Commercial net - Commercial net. NOT the market's combined net.
Change          = this report's difference - the contract's previous report's difference
% Chng          = Change / |previous difference| x 100;  N/A when that is zero.

USD/xxx PAIRS. CAD, CHF and JPY futures are the foreign currency against the dollar, so a
long CAD future is SHORT USD/CAD. For USDCAD, USDCHF and USDJPY the Long and Short of BOTH
groups are swapped before anything is computed - in the tables, in every historical week,
in the comparisons and in the positioning shading - so every figure reads in pair terms.

POSITIONING SHADING (50-week reports)
-------------------
Each of the four position series - NC long, NC short, Comm long, Comm short, in pair terms -
is ranked against ITS OWN history, longs and shorts separately, point in time: a week's
value gets its percentile rank (mid-rank for ties) among the RANK_WEEKS reports ending at
that week, so an old week is judged by what was known then, not with hindsight.
The colour is continuous over the whole range, no thresholds:
    strength = (percentile / 100) ** SHADE_GAMMA
so a low reading is neutral or faint, a middling one light, a high one strong and an
extreme the darkest. Longs shade red, shorts blue. The words on hover / tap are only labels
for the same number: low < 40th, moderate 40-70th, high 70-90th, extreme >= 90th.
Fewer than MIN_RANK_WEEKS reports, a flat range or a missing value -> no colour, and the
hover says why. AGE FADE is applied afterwards and separately:
    shown = strength x max(AGE_FADE_FLOOR, AGE_FADE ** (weeks before the latest report))
so the latest report is full strength and older weeks fade - but never below 15% of their
own strength, so an old extreme stays visible as a faint tint (a low reading, whose own
strength is tiny, still looks plain).
The hover / tap always gives the true, unfaded percentile. These show positioning extremes,
not buy/sell signals.

UPDATING
--------
CFTC publishes Tuesday's positions on Friday at 15:30 US Eastern (DST handled by hand, so a
Windows Python without tzdata is right too); holidays push that to the following Monday or
Tuesday, and a government shutdown can stop it for weeks. build() therefore:
  * backfills BACKFILL_WEEKS once (so even the oldest shown week has a full ranking window);
  * before the next report is due, re-checks once a day (CFTC does revise);
  * once it is due, polls every POLL_MIN minutes, every POLL_LATE_H hours after a day late;
  * fetches the trailing FETCH_WEEKS each time - widened after a long outage - so any week
    missed while nothing was running is recovered;
  * on a failed fetch keeps the last good data and records the error for the page;
  * stamps report_first_seen_at only when a NEW report date appears, so re-fetching
    unchanged data never makes an old report look newly released.
It runs inside the CI-gated refresh build (run.py), which is the only thing that publishes
the page. It is not part of the news watcher's publisher allowlist.
"""
import json, math, urllib.parse
import datetime as dt
from pathlib import Path

from config import DATA

DATASET_ID = "6dca-aqww"
SOCRATA_LEGACY = f"https://publicreporting.cftc.gov/resource/{DATASET_ID}.json"
SOURCE = "CFTC Commitments of Traders - Legacy report, Futures Only (dataset 6dca-aqww)"
OUT_NAME = "cot_legacy.json"
SCHEMA = 2

HISTORY_WEEKS = 50                       # weeks shown per report
FETCH_WEEKS = HISTORY_WEEKS + 3          # routine fetch: 50 + the oldest week's previous + slack
RANK_WEEKS = 156                         # shading window: 3 years of reports, point in time
MIN_RANK_WEEKS = 52                      # fewer reports than this -> no shading
BACKFILL_WEEKS = HISTORY_WEEKS + RANK_WEEKS + 6   # one-time: oldest shown week gets a full window
KEEP_WEEKS = 520                         # storage cap per contract (10 years)
ROW_LIMIT = 20000                        # a response this long may be truncated -> rejected

SHADE_GAMMA = 2.0                        # strength = (pct/100)**gamma: faint low, dark extreme
AGE_FADE = 0.75                          # shown strength x this per week of age...
AGE_FADE_FLOOR = 0.15                    # ...but never below this (reached at 7 weeks old)
BANDS = ((90.0, "extreme"), (70.0, "high"), (40.0, "moderate"), (0.0, "low"))

RECHECK_H = 24                           # not due yet: one re-check a day (revisions)
POLL_MIN = 30                            # due: poll at most this often
POLL_LATE_H = 3                          # more than a day late: back off to this
FAIL_RETRY_MIN = 30                      # after a failed fetch
OVERDUE_FLAG_H = 24                      # this late past the scheduled release -> flagged

FX_TITLE = "Commitment of Traders Report for the Major Currency Pairs"
CM_TITLE = "Commitment of Traders Report for the Major Commodities & Indexes"
REVERSED_NOTE = ("CAD, CHF, JPY Long & Short figures have been reversed from original "
                 "futures numbers for ease of use")

# (row label, CFTC contract code, reverse long/short?, report key, what the row is). The
# report key is the instrument's existing 50-week drill-down id where it has one, so the
# Legacy report joins that layer instead of duplicating it.
FX_ROWS = [
    ("USD",    "098662", False, "USD", "ICE US Dollar Index future: the dollar against a "
                                       "six-currency basket, not a pair"),
    ("AUDUSD", "232741", False, "AUD", "CME Australian dollar future, quoted AUD/USD"),
    ("USDCAD", "090741", True,  "CAD", "CME Canadian dollar future (CAD/USD), read inverted "
                                       "as USD/CAD"),
    ("USDCHF", "092741", True,  "CHF", "CME Swiss franc future (CHF/USD), read inverted as "
                                       "USD/CHF"),
    ("EURUSD", "099741", False, "EUR", "CME euro FX future, quoted EUR/USD"),
    ("GBPUSD", "096742", False, "GBP", "CME British pound future, quoted GBP/USD"),
    ("USDJPY", "097741", True,  "JPY", "CME Japanese yen future (JPY/USD), read inverted as "
                                       "USD/JPY"),
    ("NZDUSD", "112741", False, "NZD", "CME New Zealand dollar future, quoted NZD/USD"),
]
CM_ROWS = [
    ("Gold",       "088691", False, "XAU", "COMEX gold future"),
    ("Silver",     "084691", False, "XAG", "COMEX silver future"),
    ("Copper",     "085692", False, "HG",  "COMEX copper grade #1 future"),
    ("WTI Oil",    "067651", False, "WTI", "NYMEX WTI crude oil (physical) future"),
    ("S&P 500",    "13874A", False, "ES",  "CME E-mini S&P 500 future"),
    ("Nasdaq 100", "209742", False, "NQ",  "CME E-mini Nasdaq-100 future"),
    # The $5 e-mini, as the published tables use - NOT the micro (124608) the Indices tab
    # reads, and never the two added together.
    ("Dow Jones",  "124603", False, "YM",  "CBOT E-mini Dow ($5 x DJIA) future"),
    ("UST Bonds",  "020601", False, "ZB",  "CBOT U.S. Treasury bond future"),
]
ALL_ROWS = FX_ROWS + CM_ROWS
ALL_CODES = [r[1] for r in ALL_ROWS]

# raw field order in storage
POS = ("open_interest_all", "noncomm_positions_long_all", "noncomm_positions_short_all",
       "noncomm_postions_spread_all",            # sic - CFTC's own spelling
       "comm_positions_long_all", "comm_positions_short_all",
       "nonrept_positions_long_all", "nonrept_positions_short_all")
OI, NCL, NCS, NCSP, CL, CS, NRL, NRS = range(8)
FIELDS = ("report_date_as_yyyy_mm_dd", "cftc_contract_market_code",
          "market_and_exchange_names", "futonly_or_combined") + POS


# ------------------------------------------------------------------ release schedule
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def eastern_offset_h(utc):
    """-4 during US daylight time, -5 otherwise (rules since 2007: 02:00 local on the second
    Sunday of March to 02:00 local on the first Sunday of November)."""
    y = utc.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(7), dt.timezone.utc)
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(6), dt.timezone.utc)
    return -4 if start <= utc < end else -5


def release_at(report_date):
    """Scheduled release of a report, in UTC: the Friday after its positions date (a
    Tuesday, occasionally a Monday), 15:30 US Eastern."""
    fri = report_date + dt.timedelta(days=(4 - report_date.weekday()) % 7 or 7)
    t = None
    for off in (-4, -5):
        t = dt.datetime.combine(fri, dt.time(15, 30), dt.timezone.utc) - dt.timedelta(hours=off)
        if eastern_offset_h(t) == off:
            return t
    return t


def next_report_date(latest):
    """The positions date of the report after `latest` (the next Tuesday)."""
    n = latest + dt.timedelta(days=7)
    return n + dt.timedelta(days=(1 - n.weekday()) % 7)


# ------------------------------------------------------------------ fetch
def _query(codes, since):
    where = (f"cftc_contract_market_code in({','.join(repr(c) for c in codes)}) AND "
             f"report_date_as_yyyy_mm_dd>='{since}T00:00:00'")
    params = {"$select": ",".join(FIELDS), "$where": where,
              "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": str(ROW_LIMIT)}
    return f"{SOCRATA_LEGACY}?{urllib.parse.urlencode(params)}"


def fetch_rows(since):
    """Raw Legacy futures-only rows for every tracked contract since `since` (a date)."""
    from fetch_cot import _get                     # same retry/backoff + UA as the COT pull
    rows = json.loads(_get(_query(ALL_CODES, since.isoformat()), timeout=120)
                      .decode("utf-8", "replace"))
    if not isinstance(rows, list):
        raise RuntimeError(f"unexpected CFTC response: {str(rows)[:120]}")
    return rows


# ------------------------------------------------------------------ storage
def empty_store():
    return {"schema": SCHEMA, "dataset": DATASET_ID, "report": "Legacy",
            "futures_only": True, "source": SOURCE, "contracts": {}, "status": {}}


def load(path):
    """The stored history, or None if there is none (or it is from an older layout)."""
    try:
        s = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None
    if not isinstance(s, dict) or s.get("schema") != SCHEMA or s.get("dataset") != DATASET_ID:
        return None
    s.setdefault("contracts", {})
    s.setdefault("status", {})
    return s


def _write(path, store):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)                              # atomic: a crash leaves old or new, never half


def _int(v):
    if v is None or isinstance(v, bool):
        raise ValueError("missing value")
    f = float(v)
    if f != int(f) or f < 0:
        raise ValueError(f"not a non-negative integer: {v!r}")
    return int(f)


def parse_row(r):
    """(date, code, name, [8 ints]) from one raw CFTC row; ValueError says why not."""
    d = str(r.get("report_date_as_yyyy_mm_dd", ""))[:10]
    dt.date.fromisoformat(d)
    code = r.get("cftc_contract_market_code")
    if r.get("futonly_or_combined") != "FutOnly":
        raise ValueError(f"not futures-only ({r.get('futonly_or_combined')!r})")
    vals = [_int(r.get(f)) for f in POS]
    if vals[NCL] + vals[NCSP] + vals[CL] + vals[NRL] != vals[OI]:
        raise ValueError("long side does not add up to open interest")
    if vals[NCS] + vals[NCSP] + vals[CS] + vals[NRS] != vals[OI]:
        raise ValueError("short side does not add up to open interest")
    return d, code, str(r.get("market_and_exchange_names") or ""), vals


def merge(store, rows):
    """Validate and store raw rows. Returns (added, revised, rejected) - rejected is a list
    of 'date code: reason'. Older weeks are never dropped except beyond KEEP_WEEKS."""
    added = revised = 0
    rejected = []
    for r in rows:
        code = r.get("cftc_contract_market_code")
        if code not in ALL_CODES:
            continue
        try:
            d, code, name, vals = parse_row(r)
        except Exception as e:                         # noqa: BLE001
            rejected.append(f"{str(r.get('report_date_as_yyyy_mm_dd', '?'))[:10]} {code}: {e}")
            continue
        c = store["contracts"].setdefault(code, {"name": name, "weeks": {}})
        if name:
            c["name"] = name
        old = c["weeks"].get(d)
        if old is None:
            added += 1
        elif old != vals:
            revised += 1
        c["weeks"][d] = vals
    for c in store["contracts"].values():
        if len(c["weeks"]) > KEEP_WEEKS:
            for d in sorted(c["weeks"])[:-KEEP_WEEKS]:
                del c["weeks"][d]
    return added, revised, rejected


# ------------------------------------------------------------------ compute
def _rnd(x):
    """Round half away from zero, as a spreadsheet does - Python's round() sends 62.5 to
    62 (banker's rounding), which would put a percentage one off the published tables."""
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def _side(v, reverse):
    """(NC long, NC short, Comm long, Comm short) in pair terms."""
    if not v:
        return None
    ncl, ncs, cl, cs = v[NCL], v[NCS], v[CL], v[CS]
    if None in (ncl, ncs, cl, cs):
        return None
    return (ncs, ncl, cs, cl) if reverse else (ncl, ncs, cl, cs)


def _group(long_, short):
    total = long_ + short
    pl = _rnd(long_ / total * 100) if total else None
    ps = _rnd(short / total * 100) if total else None
    return {"long": long_, "short": short, "total": total,
            "pct_long": pl, "pct_short": ps, "net": long_ - short}


def week_figures(cur, prev, reverse):
    """Every column for one week from raw stored rows (prev = that contract's previous
    report, or None). None if `cur` is unusable."""
    s = _side(cur, reverse)
    if s is None:
        return None
    nc, cm = _group(s[0], s[1]), _group(s[2], s[3])
    diff = nc["net"] - cm["net"]
    p = _side(prev, reverse)
    prev_diff = ((p[0] - p[1]) - (p[2] - p[3])) if p else None
    change = diff - prev_diff if prev_diff is not None else None
    pct = (_rnd(change / abs(prev_diff) * 100) if prev_diff not in (None, 0) else None)
    return {"nc": nc, "comm": cm, "diff": diff, "prev_diff": prev_diff,
            "change": change, "pct_change": pct}


def pick_weeks(store, codes=None):
    """(latest, previous) report dates. The latest is the newest date with (nearly) every
    contract present - a report caught mid-publication must not become 'this week' with
    half its rows missing. One missing contract is tolerated."""
    codes = codes or ALL_CODES
    have = {}
    for code in codes:
        for d in (store["contracts"].get(code) or {}).get("weeks", {}):
            have.setdefault(d, set()).add(code)
    need = max(1, len(codes) - 1)
    dates = sorted(have, reverse=True)
    latest = next((d for d in dates if len(have[d]) >= need), None)
    if latest is None:
        return None, None
    return latest, next((d for d in dates if d < latest), None)


def summary(store):
    """The two tables for the latest complete report, or None with no data."""
    latest, prev = pick_weeks(store)
    if latest is None:
        return None

    def row(label, code, rev, key, desc):
        c = store["contracts"].get(code) or {}
        weeks = c.get("weeks", {})
        r = {"label": label, "code": code, "reversed": rev, "key": key, "desc": desc,
             "contract": c.get("name", ""), "has_history": bool(weeks)}
        cur = weeks.get(latest)
        older = [d for d in weeks if d < latest]
        pd = max(older) if older else None
        w = week_figures(cur, weeks.get(pd) if pd else None, rev) if cur else None
        if w is None:
            r["missing"] = True
            return r
        r.update(w, missing=False, prev_date=pd)
        return r

    def group(key, title, label_col, spec, note=None):
        g = {"key": key, "title": title, "label_col": label_col,
             "rows": [row(*s) for s in spec]}
        if note:
            g["note"] = note
        return g
    return {"report_date": latest, "prev_date": prev, "source": SOURCE,
            "groups": [group("fx", FX_TITLE, "Currency Pairs", FX_ROWS, REVERSED_NOTE),
                       group("cm", CM_TITLE, "Futures Contracts", CM_ROWS)]}


def percentile_rank(window, x):
    """Mid-rank percentile of x within window (0-100)."""
    less = sum(1 for v in window if v < x)
    eq = sum(1 for v in window if v == x)
    return 100.0 * (less + 0.5 * eq) / len(window)


def band(p):
    return next(name for floor, name in BANDS if p >= floor)


def positioning(window, x):
    """Where x sits in its own history: {state, pct, n, strength}. state is a band name
    (low / moderate / high / extreme), or no-data / short-history / flat with strength 0."""
    if x is None:
        return {"state": "no-data", "pct": None, "n": 0, "strength": 0.0}
    vals = [v for v in window if v is not None]
    n = len(vals)
    if n < MIN_RANK_WEEKS:
        return {"state": "short-history", "pct": None, "n": n, "strength": 0.0}
    if max(vals) == min(vals):
        return {"state": "flat", "pct": None, "n": n, "strength": 0.0}
    p = percentile_rank(vals, x)
    return {"state": band(p), "pct": p, "n": n, "strength": (p / 100.0) ** SHADE_GAMMA}


def age_fade(age):
    return max(AGE_FADE_FLOOR, AGE_FADE ** max(0, age))


SERIES = ("nc_long", "nc_short", "comm_long", "comm_short")


def report(store, code, reverse, upto, n=HISTORY_WEEKS):
    """One contract's last `n` reports up to `upto`, newest first: every column, plus where
    each of the four series sits in its own history. Never invents a week: a missing report
    is counted, not filled."""
    weeks = (store["contracts"].get(code) or {}).get("weeks", {})
    dates = sorted(d for d in weeks if d <= upto)
    sides = [_side(weeks[d], reverse) for d in dates]
    series = {k: [s[i] if s else None for s in sides] for i, k in enumerate(SERIES)}
    rows = []
    for idx in range(len(dates) - 1, max(0, len(dates) - n) - 1, -1):
        d = dates[idx]
        pd = dates[idx - 1] if idx > 0 else None
        w = week_figures(weeks[d], weeks[pd] if pd else None, reverse)
        if w is None:
            continue
        w["date"], w["prev_date"] = d, pd
        if pd:
            gap = (dt.date.fromisoformat(d) - dt.date.fromisoformat(pd)).days
            w["missing_before"] = max(0, round(gap / 7) - 1)
        lo = max(0, idx - RANK_WEEKS + 1)
        w["pos"] = {k: positioning(series[k][lo:idx + 1], series[k][idx]) for k in SERIES}
        w["age"] = len(rows)
        rows.append(w)
    missing = sum(r.get("missing_before", 0) for r in rows[:-1]) if rows else 0
    return {"code": code, "rows": rows, "wanted": n, "shortfall": max(0, n - len(rows)),
            "missing_weeks": missing,
            "oldest_has_prev": bool(rows) and rows[-1].get("prev_diff") is not None}


# ------------------------------------------------------------------ build
def _ts(s):
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except Exception:                                  # noqa: BLE001
        return None


def should_fetch(store, now, force=False):
    """(fetch?, why) - see UPDATING in the module docstring."""
    st = store.get("status") or {}
    if force:
        return True, "forced"
    if not store.get("contracts"):
        return True, "no history stored yet"
    if not st.get("backfill_done"):
        return True, "one-time history backfill"
    try:
        latest = dt.date.fromisoformat(st["report_date"])
    except Exception:                                  # noqa: BLE001
        return True, "no report date on file"
    ok, err = _ts(st.get("checked_at")), _ts(st.get("last_error_at"))
    last_try = max([t for t in (ok, err) if t], default=None)
    if last_try is None:
        return True, "never checked"
    due = release_at(next_report_date(latest))
    if now >= due:
        late = now - due
        gap = (dt.timedelta(minutes=POLL_MIN) if late < dt.timedelta(hours=24)
               else dt.timedelta(hours=POLL_LATE_H))
        why = (f"report for {next_report_date(latest)} was due {due:%a %d %b %H:%M} UTC "
               f"({late.total_seconds() / 3600:.1f}h ago)")
    else:
        gap = dt.timedelta(hours=RECHECK_H)
        why = "daily re-check for revisions"
    if err and (ok is None or err > ok):
        gap = min(gap, dt.timedelta(minutes=FAIL_RETRY_MIN))
        why += "; retrying after a failed fetch"
    return (now - last_try >= gap), why


def _since(store, now):
    today = now.date()
    if not (store.get("status") or {}).get("backfill_done"):
        return today - dt.timedelta(weeks=BACKFILL_WEEKS)
    newest = max((d for c in store["contracts"].values() for d in c.get("weeks", {})),
                 default=None)
    weeks = FETCH_WEEKS
    if newest:              # down longer than the routine window: reach back to cover it
        behind = (today - dt.date.fromisoformat(newest)).days // 7 + 2
        weeks = max(weeks, min(behind, KEEP_WEEKS))
    return today - dt.timedelta(weeks=weeks)


def build(force=False, data=None, fetch=None, now=None):
    """Bring data/cot_legacy.json up to date (when a fetch is due) and return the store.
    `fetch(since)` returns raw CFTC rows; injectable for tests."""
    data = data or DATA
    path = data / OUT_NAME
    now = now or dt.datetime.now(dt.timezone.utc)
    store = load(path) or empty_store()
    st = store["status"]
    do, why = should_fetch(store, now, force)
    if not do:
        print(f"  COT legacy: no fetch ({why}); week of {st.get('report_date')}")
        return store
    since = _since(store, now)
    try:
        rows = (fetch or fetch_rows)(since)
        if len(rows) >= ROW_LIMIT:
            raise RuntimeError(f"CFTC returned {len(rows)} rows - the row limit; refusing a "
                               f"possibly truncated history")
        added, revised, rejected = merge(store, rows)
        latest, prev = pick_weeks(store)
        if latest is None:
            raise RuntimeError("no complete report week in the CFTC data")
    except Exception as e:                             # noqa: BLE001
        if not store["contracts"]:
            raise                                      # nothing to keep and nothing written
        st["last_error"] = f"{type(e).__name__}: {e}"[:240]
        st["last_error_at"] = now.isoformat()
        _write(path, store)
        print(f"  COT legacy: fetch failed ({st['last_error']}) - keeping the week of "
              f"{st.get('report_date')}")
        return store
    st["checked_at"] = now.isoformat()
    st["last_error"] = None
    if added or revised:
        st["updated_at"] = now.isoformat()
    if st.get("report_date") != latest:
        st["report_date"] = latest
        st["report_first_seen_at"] = now.isoformat()
    st["prev_date"] = prev
    st["rejected_rows"] = len(rejected)
    st["rejected_sample"] = rejected[:5]
    if not st.get("backfill_done"):
        st["backfill_done"] = True
        st["backfill_since"] = since.isoformat()
    _write(path, store)
    print(f"  COT legacy: {why} -> week {latest} vs {prev}; +{added} new, {revised} revised"
          + (f", {len(rejected)} rejected" if rejected else ""))
    return store


def status_view(store, now=None):
    """What the page says about freshness: report date, when it was first seen, last check,
    the next report and whether it is overdue, and any error newer than the last success."""
    now = now or dt.datetime.now(dt.timezone.utc)
    st = store.get("status") or {}
    out = {k: st.get(k) for k in ("report_date", "prev_date", "report_first_seen_at",
                                  "checked_at", "updated_at", "last_error", "last_error_at")}
    ok, err = _ts(st.get("checked_at")), _ts(st.get("last_error_at"))
    out["failing"] = bool(st.get("last_error")) and err is not None and (ok is None or err > ok)
    out["overdue"] = False
    try:
        latest = dt.date.fromisoformat(st["report_date"])
        out["released_at"] = release_at(latest).isoformat()
        nxt = next_report_date(latest)
        due = release_at(nxt)
        out["next_report"], out["next_due_at"] = nxt.isoformat(), due.isoformat()
        out["overdue_h"] = max(0.0, (now - due).total_seconds() / 3600)
        out["overdue"] = out["overdue_h"] >= OVERDUE_FLAG_H
    except Exception:                                  # noqa: BLE001
        pass
    return out


if __name__ == "__main__":
    s = build(force=True)
    t = summary(s)
    print(f"COT legacy: week {t['report_date']} vs {t['prev_date']}")
    for g in t["groups"]:
        print(f"\n{g['title']}")
        for r in g["rows"]:
            if r.get("missing"):
                print(f"  {r['label']:10} -- not in this report")
                continue
            print(f"  {r['label']:10} NC net {r['nc']['net']:>9,}  Comm net {r['comm']['net']:>10,}"
                  f"  diff {r['diff']:>10,}  chg {r['change'] if r['change'] is not None else '-':>8}")
