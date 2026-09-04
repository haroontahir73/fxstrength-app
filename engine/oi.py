"""Daily futures Open Interest — the bias layer, on its own page.

WHAT OI IS AND WHY IT MATTERS
-----------------------------
Open Interest is the number of futures contracts still open. A new buyer matched with a
new seller ADDS one; two existing holders closing REMOVES one. So OI measures whether
money is entering the market or leaving it.

Price alone cannot tell you that. A rally always means buyers were aggressive - but only
OI says whether those were NEW longs being opened (real demand) or old shorts buying
back to get out (a short-covering bounce with nobody new behind it). Same for a selloff.

Combining the day's price change with the day's OI change gives four states:

    price UP   + OI UP    NEW MONEY LONG      real demand      -> look for longs
    price UP   + OI DOWN  SHORT COVERING      fake demand      -> do not chase
    price DOWN + OI UP    NEW MONEY SHORT     real supply      -> look for shorts
    price DOWN + OI DOWN  LONG LIQUIDATION    fake selloff     -> do not chase

Two extra reads from the same series:
  * a BREAKOUT with OI rising is a real breakout (no retest needed);
  * after a long run, a spike day where OI FALLS is money leaving - the exit signal.

SOURCE AND SCHEDULE
-------------------
CME publishes preliminary volume/OI for the previous trade date overnight. This module
tries, in order, the windows the desk asked for:

    pre-London (05:30-07:30 UTC)  ->  mid-London (09:30-11:30 UTC)
    ->  New York (13:30-15:30 UTC)  ->  give up, try again tomorrow

One attempt per window per trade date; the outcome is recorded in oi_state.json so a
5-minute watcher loop does not hammer the source. If every window fails the page keeps
the last good data and says plainly how old it is.

Sources are tried in order and the page names the one that worked:
  1. CME's own voiProductsViewExport endpoint (what the user's Excel workbook calls)
  2. a local copy of that workbook (Open Interest.xlsm) when running on the user's PC

    python oi.py                      # fetch if a window is open, then render
    python oi.py --force              # ignore the window, try right now
    python oi.py --merge <pull.json>  # merge a browser pull (see CME_PRODUCT_ID below)
    python oi.py --seed <workbook>    # import history from the Excel workbook
    python oi.py --render-only        # rebuild the page from stored history
"""
import json, os, re, sys, time, zipfile, urllib.request, urllib.error
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                             # noqa: BLE001
    pass

HERE = Path(__file__).parent
DATA = HERE / "data"
HIST_FILE = DATA / "oi_history.json"
STATE_FILE = DATA / "oi_state.json"
PAGE = HERE / "open-interest.html"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
      "Referer": "https://www.cmegroup.com/tools-information/quikstrike/volume-open-interest.html"}

CME_EXPORT = ("https://www.cmegroup.com/CmeWS/exp/voiProductsViewExport.ctl"
              "?media=xls&tradeDate={date}&assetClassId={ac}&reportType=P&excluded=CEE,CEU,KCB")
ASSET_CLASS = {3: "FX", 8: "Metals", 5: "Energy"}

# The clean JSON behind the same numbers - one call returns ~34 days of daily OI + volume:
#   https://www.cmegroup.com/CmeWS/mvc/Volume/LastTotals/{PRODUCT_ID}?days=30&isProtected
# Verified against the user's Excel workbook: identical values on every overlapping date.
#
# IT ONLY WORKS FROM A REAL BROWSER SESSION. Plain urllib/curl gets HTTP 403 ("This IP
# address is blocked due to suspected web scraping") no matter what UA or cookies you send
# - CME blocks on the request signature, not the IP, which is why the Excel workbook could
# always fetch it and this script cannot. Drive it through the browser tooling instead, and
# SLOWLY: ~150 requests fired in parallel got the session blocked for several minutes, while
# 10 requests at 2.5-3s apart run clean. Ten a day is all this needs.
#
# Pull with (in a browser on cmegroup.com), then feed the result to `oi.py --merge <file>`:
#   for (const [name,id] of Object.entries(CME_PRODUCT_ID)) { ...fetch..., sleep 2500 }
CME_PRODUCT_ID = {"Gold": 437, "Silver": 458, "Oil": 425, "EUR": 58, "GBP": 42,
                  "JPY": 69, "AUD": 37, "NZD": 78, "CAD": 48, "CHF": 86}
CME_LAST_TOTALS = ("https://www.cmegroup.com/CmeWS/mvc/Volume/LastTotals/{pid}"
                   "?days={days}&isProtected")

# instrument -> (CME product-name match, price symbol for the direction read)
INSTRUMENTS = {
    "Gold":   ("gold",              "GC=F"),
    "Silver": ("silver",            "SI=F"),
    "Oil":    ("crude oil",         "CL=F"),
    "EUR":    ("euro fx",           "6E=F"),
    "GBP":    ("british pound",     "6B=F"),
    "JPY":    ("japanese yen",      "6J=F"),
    "AUD":    ("australian dollar", "6A=F"),
    "NZD":    ("new zealand dollar", "6N=F"),
    "CAD":    ("canadian dollar",   "6C=F"),
    "CHF":    ("swiss franc",       "6S=F"),
}
ORDER = ["Gold", "Silver", "Oil", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]

# UTC windows: (name, start_hour_min, end_hour_min)
WINDOWS = [("pre-London", (5, 30), (7, 30)),
           ("mid-London", (9, 30), (11, 30)),
           ("New York",   (13, 30), (15, 30))]


# ---------------------------------------------------------------- small helpers
def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001
        return default


def _save(p, obj):
    try:
        Path(p).parent.mkdir(exist_ok=True)
        Path(p).write_text(json.dumps(obj, indent=1), encoding="utf-8")
    except Exception as e:                                    # noqa: BLE001
        print(f"  could not write {p}: {type(e).__name__}")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def prev_business_day(d):
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def current_window(now=None):
    """Which retry window we are in, or None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    hm = (now.hour, now.minute)
    for name, a, b in WINDOWS:
        if a <= hm <= b:
            return name
    return None


# ---------------------------------------------------------------- source 1: CME
def _parse_cme_xls(raw):
    """The 'xls' export is really an HTML table. Returns [(product, volume, oi, chg)]."""
    txt = raw.decode("utf-8", "replace")
    if "<" not in txt:
        return []
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) >= 8 and cells[0]:
            rows.append(cells)
    return rows


def _num(s):
    try:
        return int(float(str(s).replace(",", "").replace("(", "-").replace(")", "")))
    except Exception:                                         # noqa: BLE001
        return None


_LAST_REASON = ""


def fetch_cme(trade_date):
    """{instrument: {oi, chg, volume}} for one trade date, or {} if unavailable."""
    global _LAST_REASON
    out, ds = {}, trade_date.strftime("%Y%m%d")
    for ac in (3, 8, 5):
        try:
            raw = _get(CME_EXPORT.format(date=ds, ac=ac))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read()[:200].decode("utf-8", "replace")
            except Exception:                                 # noqa: BLE001
                pass
            _LAST_REASON = (f"CME HTTP {e.code}" +
                            (" - IP blocked as a scraper" if "blocked" in body.lower() else ""))
            print(f"  CME {ASSET_CLASS.get(ac, ac)}: HTTP {e.code} {body[:80]}")
            continue
        except Exception as e:                                # noqa: BLE001
            _LAST_REASON = f"CME {type(e).__name__}"
            print(f"  CME {ASSET_CLASS.get(ac, ac)}: {type(e).__name__}")
            continue
        for cells in _parse_cme_xls(raw):
            name = cells[0].lower()
            for inst, (match, _) in INSTRUMENTS.items():
                if inst in out or match not in name:
                    continue
                # layout: Name | Type | Globex | OpenOutcry | ClearPort | Volume | OI | Change
                vol, oi, chg = _num(cells[5]), _num(cells[6]), _num(cells[7])
                if oi:
                    out[inst] = {"oi": oi, "chg": chg, "volume": vol}
    return out


# ---------------------------------------------------------------- source 2: the workbook
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _sheet_map(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rmap = {r.get("Id"): r.get("Target") for r in rels}
    rid_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    out = {}
    for s in wb.iter(f"{NS}sheet"):
        t = rmap.get(s.get(rid_attr), "")
        out[s.get("name")] = ("xl/" + t.lstrip("/")) if not t.startswith("/") else t.lstrip("/")
    return out


def _cell_rc(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref or "")
    if not m:
        return None, None
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return int(m.group(2)) - 1, n - 1


def read_workbook(path):
    """Import every instrument sheet. Layout: Date | Volume | OI | Change, newest first.
    Returns {'YYYY-MM-DD': {inst: {oi, chg, volume}}}."""
    z = zipfile.ZipFile(path)
    sheets = _sheet_map(z)
    epoch = dt.date(1899, 12, 30)
    hist = {}
    for inst in INSTRUMENTS:
        target = sheets.get(inst)
        if not target:
            continue
        try:
            root = ET.fromstring(z.read(target))
        except KeyError:
            continue
        grid = {}
        for c in root.iter(f"{NS}c"):
            r, col = _cell_rc(c.get("r"))
            if r is None or col > 4:
                continue
            v = c.find(f"{NS}v")
            if v is not None and v.text:
                grid[(r, col)] = v.text
        r = 1
        while (r, 0) in grid:
            try:
                serial = int(float(grid[(r, 0)]))
                # guard: a stray number in the date column becomes 1900-01-xx and would
                # sit in the history forever as a bogus "oldest" day
                if serial < 36526:                            # before 2000-01-01
                    r += 1
                    continue
                day = (epoch + dt.timedelta(days=serial)).isoformat()
                oi = _num(grid.get((r, 2)))
                if oi:
                    hist.setdefault(day, {})[inst] = {
                        "oi": oi, "chg": _num(grid.get((r, 3))),
                        "volume": _num(grid.get((r, 1)))}
            except Exception:                                 # noqa: BLE001
                pass
            r += 1
    return hist


# ---------------------------------------------------------------- price direction
def price_moves(days_back=70):
    """{instrument: [(date, close)]} from Yahoo - the price half of the OI read.

    The window has to be generous: when the OI source has been down for a while the
    newest OI day can be weeks back, and a short price window would leave nothing to
    pair it with (the read would silently go blank rather than say "stale")."""
    out = {}
    for inst, (_, sym) in INSTRUMENTS.items():
        try:
            u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                 f"?range=6mo&interval=1d")
            d = json.loads(_get(u, timeout=15))
            r = d["chart"]["result"][0]
            ts = r["timestamp"]
            cl = r["indicators"]["quote"][0]["close"]
            ser = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), c)
                   for t, c in zip(ts, cl) if c is not None]
            out[inst] = ser[-days_back:]
        except Exception:                                     # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------- the read
STATES = {
    ("up", "up"):     ("NEW MONEY LONG", "pos",
                       "Price rose and new contracts opened - real buying, not a bounce. "
                       "Bias: look for longs only."),
    ("up", "down"):   ("SHORT COVERING", "warn",
                       "Price rose but contracts CLOSED - old shorts buying back, no new "
                       "buyers behind it. Do not chase this rally."),
    ("down", "up"):   ("NEW MONEY SHORT", "neg",
                       "Price fell and new contracts opened - real selling. "
                       "Bias: look for shorts only."),
    ("down", "down"): ("LONG LIQUIDATION", "warn",
                       "Price fell but contracts CLOSED - holders getting out rather than "
                       "new sellers arriving. A weak selloff; do not chase it down."),
}


TABLE_DAYS = 10          # ~2 weeks of trading days - the user reads the RUN, not one day


def day_rows(hist, prices, inst, n=TABLE_DAYS):
    """The last `n` trading days for one instrument, newest first.

    Rule #1 is about the SEQUENCE, not a single print: days that keep closing up with OI
    rising are the ones you hold and add into; a shrinking OI increase after a long run is
    profit-taking starting; OI falling on a spike day is the exit. One row cannot show any
    of that, so the page always carries a fortnight."""
    px = {d: c for d, c in (prices.get(inst) or [])}
    pdates = [d for d, _ in (prices.get(inst) or [])]
    rows = []
    for day in sorted(hist, reverse=True):
        d = hist[day].get(inst)
        if not d or d.get("chg") is None:
            continue
        chg_px = None
        if day in px:
            i = pdates.index(day)
            if i > 0:
                prev = px[pdates[i - 1]]
                chg_px = (px[day] - prev) / prev * 100
        state = cls = None
        if chg_px:
            state, cls, _ = STATES[("up" if chg_px > 0 else "down",
                                    "up" if d["chg"] > 0 else "down")]
        rows.append({"date": day, "oi": d["oi"], "chg": d["chg"],
                     "px_chg_pct": round(chg_px, 2) if chg_px is not None else None,
                     "state": state, "cls": cls})
        if len(rows) >= n:
            break
    return rows


def analyse(hist, prices):
    """Per instrument: the latest day that has BOTH an OI change and a price change."""
    days = sorted(hist)
    out = {}
    for inst in ORDER:
        rec = None
        for day in reversed(days):
            d = hist[day].get(inst)
            if not d or d.get("chg") is None:
                continue
            ser = prices.get(inst) or []
            px = {x[0]: x[1] for x in ser}
            if day not in px:
                continue
            i = [x[0] for x in ser].index(day)
            if i == 0:
                continue
            chg_px = px[day] - ser[i - 1][1]
            if chg_px == 0:
                continue
            key = ("up" if chg_px > 0 else "down", "up" if d["chg"] > 0 else "down")
            label, cls, note = STATES[key]
            # 20-day average |change| gives "is this a big OI day or a nothing day"
            recent = [abs(hist[x][inst]["chg"]) for x in days[-25:]
                      if hist[x].get(inst, {}).get("chg") is not None]
            avg = sum(recent) / len(recent) if recent else 0
            rec = {"date": day, "oi": d["oi"], "chg": d["chg"],
                   "oi_pct": round(d["chg"] / max(d["oi"] - d["chg"], 1) * 100, 2),
                   "px": round(px[day], 4), "px_chg_pct": round(chg_px / ser[i - 1][1] * 100, 2),
                   "state": label, "cls": cls, "note": note,
                   "size": ("big" if avg and abs(d["chg"]) > 1.8 * avg else
                            "small" if avg and abs(d["chg"]) < 0.4 * avg else "normal"),
                   "avg_chg": round(avg)}
            break
        out[inst] = rec
    return out


# ---------------------------------------------------------------- fetch orchestration
def update(force=False):
    """Try the source, honouring the retry windows. Returns (hist, state)."""
    now = dt.datetime.now(dt.timezone.utc)
    hist = _load(HIST_FILE, {})
    state = _load(STATE_FILE, {})
    target = prev_business_day(now.date())
    tkey = target.isoformat()

    if hist.get(tkey) and not force:
        return hist, state
    win = current_window(now)
    if not win and not force:
        nxt = next((n for n, a, _ in WINDOWS if (now.hour, now.minute) < a), "pre-London tomorrow")
        print(f"  no window open - next attempt at {nxt}")
        return hist, state
    win = win or "forced"
    done = state.get("attempts", {}).get(tkey, [])
    if win in done and not force:
        print(f"  {win} already attempted for {tkey}")
        return hist, state

    print(f"  attempting {tkey} in the {win} window")
    got = fetch_cme(target)
    src = "cme"
    if not got:
        wb = os.environ.get("OI_WORKBOOK") or str(Path.home() / "Desktop" / "Open Interest.xlsm")
        if Path(wb).exists():
            try:
                imported = read_workbook(wb)
                for day, vals in imported.items():
                    hist.setdefault(day, {}).update(vals)
                got = imported.get(tkey, {})
                src = "workbook"
                print(f"  CME unavailable - imported {len(imported)} days from the workbook")
            except Exception as e:                            # noqa: BLE001
                print(f"  workbook read failed: {type(e).__name__}: {e}")

    state.setdefault("attempts", {}).setdefault(tkey, []).append(win)
    state["last_attempt"] = now.isoformat()
    state["last_reason"] = _LAST_REASON or ("ok" if got else "no data returned")
    if got:
        hist.setdefault(tkey, {}).update(got)
        state["last_good"] = now.isoformat()
        state["last_good_date"] = tkey
        state["source"] = src
        print(f"  got {len(got)} instruments for {tkey} from {src}")
    else:
        remaining = [n for n, _, _ in WINDOWS if n not in state["attempts"][tkey]]
        print(f"  nothing for {tkey}; windows left today: {remaining or 'none - retry tomorrow'}")
    # keep a year
    for d in sorted(hist)[:-260]:
        hist.pop(d, None)
    state["attempts"] = {k: v for k, v in state["attempts"].items()
                         if k >= (now.date() - dt.timedelta(days=10)).isoformat()}
    _save(HIST_FILE, hist)
    _save(STATE_FILE, state)
    return hist, state


# ---------------------------------------------------------------- page
def _esc(s):
    return ("" if s is None else str(s)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS = """<style>
.oi-wrap{font-family:"IBM Plex Sans",system-ui,sans-serif;max-width:900px;margin:0 auto}
.oi-wrap h2{font-size:19px;margin:26px 0 6px}
.oi-sub{color:var(--mut,#8a8a94);font-size:13px;line-height:1.55;margin:0 0 14px;max-width:66ch}
.oi-row{display:grid;grid-template-columns:96px 74px 96px 1fr;gap:14px;align-items:center;
  padding:11px 0;border-bottom:1px solid var(--line,#2a2a31)}
.oi-row:last-child{border-bottom:0}
.oi-nm{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:14px}
.oi-nm span{display:block;font-family:inherit;font-weight:400;font-size:11px;color:var(--mut,#8a8a94)}
.oi-num{font-family:"IBM Plex Mono",monospace;font-size:13px;text-align:right}
.oi-tag{font-family:"IBM Plex Mono",monospace;font-size:10.5px;padding:3px 8px;border-radius:999px;
  white-space:nowrap;display:inline-block}
.oi-tag.pos{color:#3fbe83;background:rgba(63,190,131,.13)}
.oi-tag.neg{color:#ec6a5e;background:rgba(236,106,94,.13)}
.oi-tag.warn{color:#e0a63a;background:rgba(224,166,58,.13)}
.oi-tag.mut{color:var(--mut,#8a8a94);background:var(--surface2,#22222a)}
.oi-note{font-size:12.5px;color:var(--ink2,#c9c9d2);line-height:1.5}
.oi-key{margin:16px 0;border-collapse:collapse;font-size:12.5px;width:100%}
.oi-key td{padding:7px 10px;border-bottom:1px solid var(--line,#2a2a31);vertical-align:top}
.oi-key td:first-child{font-family:"IBM Plex Mono",monospace;white-space:nowrap;color:var(--mut,#8a8a94)}
.oi-stale{padding:9px 12px;border-radius:7px;font-size:12.5px;margin:10px 0}
.oi-stale.ok{background:rgba(63,190,131,.10);color:#3fbe83}
.oi-stale.old{background:rgba(224,166,58,.12);color:#e0a63a}
.oi-hist{margin:26px 0 0}
.oi-hist h3{font-size:15px;margin:22px 0 2px;font-family:"IBM Plex Mono",monospace}
.oi-hist h3 small{font-family:"IBM Plex Sans",sans-serif;font-weight:400;font-size:11.5px;
  color:var(--mut,#8a8a94);margin-left:8px}
.oi-tw{width:100%;border-collapse:collapse;font-size:12.5px;
  font-family:"IBM Plex Mono",monospace;margin-top:6px}
.oi-tw th{text-align:right;font-weight:600;font-size:10.5px;letter-spacing:.06em;
  color:var(--mut,#8a8a94);padding:5px 8px;border-bottom:1px solid var(--line,#2a2a31)}
.oi-tw th:first-child,.oi-tw td:first-child{text-align:left}
.oi-tw th:last-child,.oi-tw td:last-child{text-align:left}
.oi-tw td{text-align:right;padding:5px 8px;border-bottom:1px solid rgba(255,255,255,.045)}
.oi-tw tr:last-child td{border-bottom:0}
.oi-tw .up{color:#3fbe83} .oi-tw .dn{color:#ec6a5e} .oi-tw .na{color:var(--mut,#8a8a94)}
.oi-tw .st{font-size:10.5px;white-space:nowrap}
@media(max-width:560px){.oi-tw .vol{display:none}}
</style>"""


SHORT_STATE = {"NEW MONEY LONG": "new longs", "SHORT COVERING": "short cover",
               "NEW MONEY SHORT": "new shorts", "LONG LIQUIDATION": "long liq"}


def history_tables(hist, prices):
    """A fortnight of daily rows per instrument - the run is the signal, not one print."""
    blocks = []
    for inst in ORDER:
        rows = day_rows(hist, prices, inst)
        if not rows:
            continue
        net = sum(r["chg"] for r in rows)
        up = sum(1 for r in rows if r["chg"] > 0)
        body = []
        for r in rows:
            pc = ("<td class='na'>&mdash;</td>" if r["px_chg_pct"] is None else
                  f"<td class='{'up' if r['px_chg_pct'] > 0 else 'dn'}'>{r['px_chg_pct']:+.2f}%</td>")
            st = (f"<td class='st na'>&mdash;</td>" if not r["state"] else
                  f"<td class='st'><span class='oi-tag {r['cls']}'>"
                  f"{SHORT_STATE.get(r['state'], r['state'])}</span></td>")
            body.append(
                f"<tr><td>{r['date'][5:]}</td>{pc}"
                f"<td class='{'up' if r['chg'] > 0 else 'dn'}'>{r['chg']:+,}</td>"
                f"<td class='vol'>{r['oi']:,}</td>{st}</tr>")
        blocks.append(
            f"<h3>{inst}<small>{len(rows)} days &middot; net "
            f"{net:+,} contracts &middot; {up} of {len(rows)} days up</small></h3>"
            "<table class='oi-tw'><thead><tr><th>Date</th><th>Price</th>"
            "<th>OI change</th><th class='vol'>Open interest</th><th>Read</th></tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")
    if not blocks:
        return ""
    return ('<div class="oi-hist"><h2>Last two weeks, day by day</h2>'
            '<p class="oi-sub">Rule&nbsp;#1 is about the <b>run</b>, not one print. Days that '
            'keep closing up with open interest rising are the ones to hold and add into. '
            'A shrinking OI increase after a long run is profit-taking starting. OI falling '
            'on a spike day is the exit.</p>' + "".join(blocks) + '</div>')


def render(hist, state, reads, prices=None):
    now = dt.datetime.now(dt.timezone.utc)
    last = state.get("last_good_date") or state.get("newest") or (sorted(hist)[-1] if hist else None)
    age_d = None
    if last:
        age_d = (now.date() - dt.date.fromisoformat(last)).days
    if age_d is None:
        banner = '<div class="oi-stale old">No open-interest data yet. The next attempt is in the pre-London window.</div>'
    elif age_d <= 1:
        banner = (f'<div class="oi-stale ok">Current &mdash; latest trade date {last} '
                  f'(source: {_esc(state.get("source", "n/a"))}).</div>')
    else:
        nxt = current_window(now) or "the next window"
        why = state.get("last_reason")
        why = f' Last attempt: {_esc(why)}.' if why else ""
        banner = (f'<div class="oi-stale old">Latest data is {age_d} days old ({last}).'
                  f'{why} Retrying in {nxt}. Treat the reads below as stale.</div>')

    rows = []
    for inst in ORDER:
        r = reads.get(inst)
        if not r:
            rows.append(f'<div class="oi-row"><div class="oi-nm">{inst}</div>'
                        f'<div class="oi-num">&mdash;</div><div></div>'
                        f'<div class="oi-note oi-tag mut">no matching price/OI day</div></div>')
            continue
        size = ("" if r["size"] == "normal" else
                f' <span class="oi-tag mut">{"big move" if r["size"] == "big" else "quiet"}</span>')
        rows.append(
            f'<div class="oi-row">'
            f'<div class="oi-nm">{inst}<span>{r["date"]}</span></div>'
            f'<div class="oi-num" style="color:{"#3fbe83" if r["px_chg_pct"] > 0 else "#ec6a5e"}">'
            f'{r["px_chg_pct"]:+.2f}%</div>'
            f'<div class="oi-num" style="color:{"#3fbe83" if r["chg"] > 0 else "#ec6a5e"}">'
            f'{r["chg"]:+,}<br><span style="font-size:10.5px;color:var(--mut,#8a8a94)">'
            f'{r["oi"]:,} open</span></div>'
            f'<div><span class="oi-tag {r["cls"]}">{r["state"]}</span>{size}'
            f'<div class="oi-note" style="margin-top:5px">{_esc(r["note"])}</div></div>'
            f'</div>')

    return (CSS + '<section class="oi-wrap">'
            '<h2>Open Interest &mdash; the daily bias layer</h2>'
            '<p class="oi-sub">Open interest is how many futures contracts are still open. '
            'It goes UP when new money opens a position and DOWN when someone closes one. '
            'Price on its own cannot tell you which is happening &mdash; pair the day\'s price '
            'move with the day\'s OI move and you can see whether a rally is real buying or '
            'just shorts getting out.</p>'
            + banner +
            '<table class="oi-key">'
            '<tr><td>price UP &middot; OI UP</td><td><b>New money long.</b> Real demand &mdash; '
            'buyers are opening fresh positions. Look for longs.</td></tr>'
            '<tr><td>price UP &middot; OI DOWN</td><td><b>Short covering.</b> The move is old '
            'shorts closing, not new buying. Do not chase.</td></tr>'
            '<tr><td>price DOWN &middot; OI UP</td><td><b>New money short.</b> Real selling. '
            'Look for shorts.</td></tr>'
            '<tr><td>price DOWN &middot; OI DOWN</td><td><b>Long liquidation.</b> Holders '
            'getting out rather than new sellers arriving. A weak selloff.</td></tr>'
            '<tr><td>breakout + OI UP</td><td>A real breakout &mdash; no retest needed.</td></tr>'
            '<tr><td>spike day + OI DOWN</td><td>Money leaving on the spike. The classic '
            'exit signal after a long run.</td></tr>'
            '</table>'
            f'<div class="oi-rows">{"".join(rows)}</div>'
            + history_tables(hist, prices or {}) +
            f'<p class="oi-sub" style="margin-top:16px">Price change is the futures close vs the '
            f'previous close. OI is CME preliminary open interest for that trade date, published '
            f'overnight &mdash; so the reading is about <b>yesterday\'s</b> session and sets '
            f'<b>today\'s</b> bias. Checked before London, again mid-London, again at the New York '
            f'open, then the next day. Page built {now.strftime("%d %b %H:%M UTC")}.</p>'
            '</section>')


def write_page(block):
    PAGE.write_text('<!doctype html><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Open Interest</title>'
                    '<link rel="preconnect" href="https://fonts.googleapis.com">'
                    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
                    'family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600;700'
                    '&display=swap">'
                    '<body style="margin:0;padding:18px 16px 40px;background:#14141a;'
                    'color:#ecedf2;font-family:\'IBM Plex Sans\',system-ui,sans-serif">'
                    '<p style="max-width:900px;margin:0 auto 4px"><a href="./dashboard.html" '
                    'style="color:#8b8df0;font-size:13px;text-decoration:none">&larr; back to the '
                    'desk</a></p>' + block + "</body>", encoding="utf-8")
    print(f"  wrote {PAGE.name}")


# ---------------------------------------------------------------- dashboard strip
MARK_A, MARK_B = "<!--OI_STRIP_START-->", "<!--OI_STRIP_END-->"


def strip_block(state, reads):
    """A one-line summary + link, injected into dashboard.html so the OI page is findable."""
    longs = [i for i, r in reads.items() if r and r["state"] == "NEW MONEY LONG"]
    shorts = [i for i, r in reads.items() if r and r["state"] == "NEW MONEY SHORT"]
    fake = [i for i, r in reads.items()
            if r and r["state"] in ("SHORT COVERING", "LONG LIQUIDATION")]
    last = state.get("last_good_date") or state.get("newest") or "n/a"
    age = ""
    try:
        n = (dt.datetime.now(dt.timezone.utc).date() - dt.date.fromisoformat(last)).days
        age = " &middot; current" if n <= 1 else f' &middot; <b>{n} days old</b>'
    except Exception:                                         # noqa: BLE001
        pass
    def _lst(x):
        return ", ".join(x) if x else "none"
    return (
        '<section style="margin:22px 0;padding:14px 16px;border-radius:9px;'
        'background:var(--surface2,#1c1c23);font-size:13.5px;line-height:1.6">'
        '<b>Open interest</b> &mdash; real money in vs out '
        f'<span style="color:var(--mut,#8a8a94)">(trade date {last}{age})</span><br>'
        f'<span style="color:#3fbe83">New longs:</span> {_lst(longs)} &nbsp; '
        f'<span style="color:#ec6a5e">New shorts:</span> {_lst(shorts)} &nbsp; '
        f'<span style="color:#e0a63a">No new money (do not chase):</span> {_lst(fake)}<br>'
        '<a href="./open-interest.html" style="color:#8b8df0;text-decoration:none">'
        'Full open-interest page &rarr;</a></section>')


def inject_strip(html_path, block):
    p = Path(html_path)
    if not p.exists():
        return False
    html = p.read_text(encoding="utf-8")
    wrapped = MARK_A + block + MARK_B
    if MARK_A in html and MARK_B in html:
        html = re.sub(re.escape(MARK_A) + ".*?" + re.escape(MARK_B),
                      lambda _: wrapped, html, flags=re.S)
    else:
        # directly under the Strength meter. It was below "Central bank commentary" and
        # the user could not find it on a phone - the link to the OI page has to be near
        # the top or the page may as well not exist.
        m = re.search(r'<section>\s*<h2>\s*What moved each score', html, flags=re.I)
        if not m:
            m = re.search(r'<section>\s*<h2>\s*Pair ranking', html, flags=re.I)
        if m:
            html = html[:m.start()] + wrapped + html[m.start():]
        elif "</body>" in html:
            html = html.replace("</body>", wrapped + "\n</body>")
        else:
            html += wrapped
    p.write_text(html, encoding="utf-8")
    print(f"  injected the OI strip into {p.name}")
    return True


def merge_pull(path):
    """Merge a browser pull: {instrument: [[YYYYMMDD, oi, volume], ...]} -> oi_history.json.
    `chg` is derived from consecutive days rather than trusted from the source, so a gap in
    the series can never silently produce a wrong day-change."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    hist = _load(HIST_FILE, {})
    n = 0
    for inst, rows in raw.items():
        if not isinstance(rows, list):
            print(f"  skip {inst}: {rows}")
            continue
        prev = None
        for ds, oi, vol in sorted(rows, key=lambda r: r[0]):
            day = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"
            if hist.get(day, {}).get(inst, {}).get("oi") != oi:
                n += 1
            hist.setdefault(day, {})[inst] = {
                "oi": oi, "chg": (oi - prev) if prev is not None else None, "volume": vol}
            prev = oi
    _save(HIST_FILE, hist)
    state = _load(STATE_FILE, {})
    state["last_good"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state["last_good_date"] = sorted(hist)[-1]
    state["source"] = "cme (browser pull)"
    state["last_reason"] = "ok"
    _save(STATE_FILE, state)
    print(f"  merged {n} cells; history {len(hist)} days, newest {sorted(hist)[-1]}")
    return hist, state


def main():
    args = sys.argv[1:]
    if "--merge" in args:
        hist, state = merge_pull(args[args.index("--merge") + 1])
    elif "--seed" in args:
        wb = args[args.index("--seed") + 1]
        hist = _load(HIST_FILE, {})
        for day, vals in read_workbook(wb).items():
            hist.setdefault(day, {}).update(vals)
        _save(HIST_FILE, hist)
        print(f"  seeded {len(hist)} days from {Path(wb).name}")
        state = _load(STATE_FILE, {})
    elif "--render-only" in args:
        hist, state = _load(HIST_FILE, {}), _load(STATE_FILE, {})
    else:
        hist, state = update(force="--force" in args)

    # the newest day actually in the history, whatever route it arrived by - the banner
    # and the strip both key off this so a seeded-only history still dates itself
    if hist:
        state["newest"] = sorted(hist)[-1]
        _save(STATE_FILE, state)

    prices = price_moves()
    reads = analyse(hist, prices)
    for inst in ORDER:
        r = reads.get(inst)
        if r:
            print(f"  {inst:7} {r['date']}  px {r['px_chg_pct']:+6.2f}%  "
                  f"OI {r['chg']:+8,}  {r['state']}")
    write_page(render(hist, state, reads, prices))
    target = next((a for a in args if a.endswith(".html")), "dashboard.html")
    inject_strip(target, strip_block(state, reads))


if __name__ == "__main__":
    main()
