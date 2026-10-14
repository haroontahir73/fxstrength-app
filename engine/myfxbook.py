"""Retail sentiment from Myfxbook's Community Outlook - display only.

WHAT IT IS. Myfxbook aggregates the open positions of the trading accounts linked to it and
publishes, per symbol, how many of those traders are long vs short, their volume and their
average entry price. It is RETAIL positioning from one community of accounts. It is shown
on its own ("Retail" tab, and a small card in each 50-week COT report) next to the CFTC
data so the two can be compared by eye. Nothing scores off it, it is not merged with COT
and no buy/sell signal is derived from it.

Not to be confused with sentiment.py, which reads the CFTC's weekly NON-REPORTABLE column.

SOURCE. The official API, never the web page:
    login.json?email=&password=          -> session
    get-community-outlook.json?session=  -> symbols[] + general{}
    logout.json?session=                 -> revokes the session
Sessions are bound to the IP that logged in, so all three calls happen in ONE process on
ONE machine, back to back (a GitHub runner's IP changes every job). The session id lives in
a local variable for those few seconds and is never written, printed or put in an error.

CREDENTIALS come only from the environment (MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD - GitHub
Secrets in the workflows). Neither they nor the session ever reach a file, the page or a
log: every message that leaves fetch() is scrubbed of all three, and the stored snapshot is
built field by field from validated numbers, so nothing else from the response can ride
along.

BUDGET. The free API allows 100 outlook requests per 24 hours. The pipeline runs about every
15 minutes, so a fetch is made only when the last ATTEMPT is >= MIN_GAP_MIN old, and never
more than DAILY_BUDGET attempts in a rolling 24 h (the rest of the 100 is headroom for the
pre-merge build check and manual tests). A login refused for bad credentials backs off
AUTH_BACKOFF_H, so a wrong password cannot hammer the account.

FAILURE NEVER BLANKS THE DATA. The last valid snapshot is kept and shown, marked "Last known
Myfxbook data - stale" with the reason; values are never replaced by zeros.

STORAGE (engine/data, carried between runs by the refresh workflow's cache, gitignored):
    myfxbook.json             latest valid snapshot + status - what the page is built from
    myfxbook_state.json       attempt times (budget), last error kind
    myfxbook_history/YYYY-MM.csv   one row per symbol per successful fetch, append-only:
        timestamp,symbol,longPercentage,shortPercentage,longVolume,shortVolume,
        longPositions,shortPositions,avgLongPrice,avgShortPrice
History is never loaded into the page. It is there for later views (24 h change, 7-day
history, charts, extremes, retail-vs-COT divergence) - read it with load_history().
"""
import csv, datetime as dt, json, math, os, urllib.error, urllib.parse, urllib.request
from html import escape as _esc
from pathlib import Path

from config import DATA

API = "https://www.myfxbook.com/api"
SOURCE_NAME = "Myfxbook Community Outlook"
SOURCE_URL = "https://www.myfxbook.com/community/outlook"
OUT_NAME = "myfxbook.json"
STATE_NAME = "myfxbook_state.json"
HIST_DIR = "myfxbook_history"
HIST_FIELDS = ["timestamp", "symbol", "longPercentage", "shortPercentage", "longVolume",
               "shortVolume", "longPositions", "shortPositions", "avgLongPrice", "avgShortPrice"]

MIN_GAP_MIN = 25          # minutes between attempts (20-30 asked; the pipeline ticks every ~15)
DAILY_BUDGET = 60         # attempts per rolling 24 h, of the free API's 100
AUTH_BACKOFF_H = 6        # after a refused login
TIMEOUT_S = 25
DELAYED_MIN = 60          # page labels: older than this -> "Delayed"
STALE_MIN = 180           # older than this -> "Stale"

# Our 16 markets (the COT report keys) -> Myfxbook symbol names that are the SAME market,
# tried in order, exact match only. A market with no match shows "Retail sentiment
# unavailable" - nothing is estimated or borrowed from a neighbouring market. The symbol
# actually used is printed on the page.
MARKETS = {
    "USD": ["USDX", "DXY", "USDOLLAR"],
    "AUD": ["AUDUSD"], "CAD": ["USDCAD"], "CHF": ["USDCHF"], "EUR": ["EURUSD"],
    "GBP": ["GBPUSD"], "JPY": ["USDJPY"], "NZD": ["NZDUSD"],
    "XAU": ["XAUUSD", "GOLD"], "XAG": ["XAGUSD", "SILVER"],
    "HG": ["XCUUSD", "COPPER"],
    "WTI": ["WTI", "USOIL", "XTIUSD", "WTIUSD"],
    "ES": ["US500", "SPX500", "SP500"], "NQ": ["NAS100", "USTEC", "NDX100"],
    "YM": ["US30", "DJ30", "DOW30"],
    "ZB": [],
}


class MyfxbookError(Exception):
    """kind: config | auth | session | unavailable | malformed. Message already scrubbed."""
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------- secrets hygiene
def scrub(text, *secrets):
    """Remove every secret (raw and URL-encoded) from a message."""
    out = str(text)
    for s in secrets:
        if s:
            for form in {s, urllib.parse.quote(s, safe=""), urllib.parse.quote_plus(s)}:
                out = out.replace(form, "[redacted]")
    return out


def _get_json(opener, url, secrets):
    """GET url -> parsed JSON. Errors carry no URL and no secret."""
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": "fxstrength-desk"}),
                    timeout=TIMEOUT_S) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise MyfxbookError("unavailable", f"HTTP {e.code}") from None
    except Exception as e:                                       # noqa: BLE001
        raise MyfxbookError("unavailable", scrub(f"{type(e).__name__}", *secrets)) from None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise MyfxbookError("malformed", "response is not JSON") from None


def fetch(email, password, opener=None):
    """login -> outlook -> logout, one process, one IP. Returns the outlook payload."""
    if not email or not password:
        raise MyfxbookError("config", "MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD not set")
    opener = opener or urllib.request.urlopen
    q = urllib.parse.urlencode({"email": email, "password": password})
    login = _get_json(opener, f"{API}/login.json?{q}", (email, password))
    if not isinstance(login, dict):
        raise MyfxbookError("malformed", "login response is not an object")
    session = login.get("session")
    if login.get("error") or not session:
        raise MyfxbookError("auth", "login refused: " + scrub(login.get("message") or "no session",
                                                               email, password, session or ""))
    secrets = (email, password, session)
    try:
        sq = urllib.parse.urlencode({"session": session})
        data = _get_json(opener, f"{API}/get-community-outlook.json?{sq}", secrets)
        if isinstance(data, dict) and data.get("error"):
            msg = scrub(data.get("message") or "error", *secrets)
            kind = "session" if "session" in msg.lower() else "unavailable"
            raise MyfxbookError(kind, f"outlook refused: {msg}")
        return data
    except MyfxbookError as e:
        raise MyfxbookError(e.kind, scrub(str(e), *secrets)) from None
    finally:
        try:
            _get_json(opener, f"{API}/logout.json?" + urllib.parse.urlencode({"session": session}),
                      secrets)
        except Exception:                                        # noqa: BLE001 - best effort
            pass
        session = None


# ---------------------------------------------------------------- validation
def _num(v, lo=None, hi=None):
    if isinstance(v, bool):
        raise ValueError("boolean")
    if isinstance(v, str):
        v = float(v.replace(",", ""))
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"not a number: {v!r}")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"out of range: {v}")
    return float(v)


def parse(payload):
    """Validated {SYMBOL: record} for EVERY symbol in the response, plus rejects.
    Raises MyfxbookError('malformed') when the response has no usable symbols at all."""
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise MyfxbookError("malformed", "no symbols list in the response")
    out, rejected = {}, []
    for s in payload["symbols"]:
        try:
            if not isinstance(s, dict):
                raise ValueError("entry is not an object")
            name = str(s.get("name") or "").strip().upper()
            if not name or len(name) > 20:
                raise ValueError("missing symbol name")
            lp = _num(s.get("longPercentage"), 0, 100)
            sp = _num(s.get("shortPercentage"), 0, 100)
            if not 97 <= lp + sp <= 103:
                raise ValueError(f"long+short = {lp + sp:g}%")
            rec = {"longPercentage": lp, "shortPercentage": sp,
                   "longVolume": _num(s.get("longVolume"), 0),
                   "shortVolume": _num(s.get("shortVolume"), 0),
                   "longPositions": int(_num(s.get("longPositions"), 0)),
                   "shortPositions": int(_num(s.get("shortPositions"), 0))}
            for k in ("avgLongPrice", "avgShortPrice"):
                v = s.get(k)
                rec[k] = None if v in (None, "", 0, "0") else _num(v, 0)
            out[name] = rec
        except (ValueError, TypeError) as e:
            rejected.append(f"{(s.get('name') if isinstance(s, dict) else '?')!s:.20}: {e}")
    if not out:
        raise MyfxbookError("malformed", f"no valid symbols ({len(rejected)} rejected)")
    return out, rejected


def match(symbols):
    """{market key: (myfxbook symbol, record)} for markets with an exact match."""
    norm = {"".join(ch for ch in k if ch.isalnum()): k for k in symbols}
    out = {}
    for key, cands in MARKETS.items():
        for c in cands:
            if c in norm:
                out[key] = (norm[c], symbols[norm[c]])
                break
    return out


# ---------------------------------------------------------------- derived figures
def imbalance(lp, sp):
    return round(abs(lp - sp))


def crowd(lp, sp):
    """Descriptive label only - not a signal."""
    if lp >= 70:
        return "Heavy Long"
    if sp >= 70:
        return "Heavy Short"
    if lp >= 60:
        return "Long"
    if sp >= 60:
        return "Short"
    return "Balanced"


def freshness(age_min):
    if age_min is None:
        return "none"
    if age_min > STALE_MIN:
        return "stale"
    if age_min > DELAYED_MIN:
        return "delayed"
    return "fresh"


# ---------------------------------------------------------------- storage
def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return default


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _append_history(data, ts, matched):
    d = data / HIST_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ts[:7]}.csv"
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HIST_FIELDS)
        for sym, r in sorted({sym: r for sym, r in matched.values()}.items()):
            w.writerow([ts, sym] + [r[k] if r[k] is not None else "" for k in HIST_FIELDS[2:]])


def load_history(data=None, since=None):
    """All stored rows (dicts) at/after `since` (ISO string), oldest first. Not used by the
    page yet - this is the read side for the later history views."""
    d = Path(data or DATA) / HIST_DIR
    rows = []
    for p in sorted(d.glob("*.csv")) if d.exists() else []:
        if since and p.stem < since[:7]:
            continue
        with p.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not since or r["timestamp"] >= since:
                    rows.append(r)
    return rows


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(now=None, data=None, env=None, opener=None, force=False):
    """Fetch when due, keep the last good snapshot, write myfxbook.json. Never raises for a
    Myfxbook problem - the problem is recorded and the page says so."""
    now = now or dt.datetime.now(dt.timezone.utc)
    data = Path(data or DATA)
    env = os.environ if env is None else env
    out_p, st_p = data / OUT_NAME, data / STATE_NAME
    snap = _read(out_p, {})
    st = _read(st_p, {"attempts": []})
    email, password = env.get("MYFXBOOK_EMAIL", ""), env.get("MYFXBOOK_PASSWORD", "")
    configured = bool(email and password)
    cutoff = _iso(now - dt.timedelta(hours=24))
    st["attempts"] = [a for a in st.get("attempts", []) if a >= cutoff]
    last = max(st["attempts"]) if st["attempts"] else None
    why = None
    if not configured:
        why = "not configured"
    elif not force and last and (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))) \
            < dt.timedelta(minutes=MIN_GAP_MIN):
        why = f"last attempt {last[11:16]} UTC, next after {MIN_GAP_MIN} min"
    elif len(st["attempts"]) >= DAILY_BUDGET:
        why = f"daily budget used ({DAILY_BUDGET} attempts in 24 h)"
    elif st.get("auth_backoff_until") and _iso(now) < st["auth_backoff_until"]:
        why = f"login refused earlier - waiting until {st['auth_backoff_until'][11:16]} UTC"
    status = dict(snap.get("status") or {})
    status["configured"] = configured
    if why:
        print(f"  Myfxbook: no fetch ({why})")
        if not configured:
            status.update(last_error="config", last_error_msg="credentials not set")
    else:
        st["attempts"].append(_iso(now))
        status["last_attempt_at"] = _iso(now)
        try:
            symbols, rejected = parse(fetch(email, password, opener))
            matched = match(symbols)
            ts = _iso(now)
            snap = {"fetched_at": ts, "markets": {k: {"symbol": s, **r} for k, (s, r) in matched.items()},
                    "symbols_in_response": len(symbols), "rejected": rejected[:20]}
            _append_history(data, ts, matched)
            status.update(last_ok_at=ts, last_error=None, last_error_msg=None)
            st.pop("auth_backoff_until", None)
            print(f"  Myfxbook: {len(symbols)} symbols, {len(matched)} of our 16 markets matched"
                  + (f", {len(rejected)} rejected" if rejected else ""))
        except MyfxbookError as e:
            status.update(last_error=e.kind, last_error_msg=scrub(str(e), email, password))
            if e.kind == "auth":
                st["auth_backoff_until"] = _iso(now + dt.timedelta(hours=AUTH_BACKOFF_H))
            print(f"  Myfxbook: fetch failed ({e.kind}: {status['last_error_msg']}) - keeping the "
                  + (f"snapshot of {snap['fetched_at']}" if snap.get("fetched_at") else "page without data"))
    st["last_error"] = status.get("last_error")
    snap["status"] = status
    snap["source"] = {"name": SOURCE_NAME, "url": SOURCE_URL}
    _write(out_p, snap)
    _write(st_p, st)
    return snap


# ---------------------------------------------------------------- rendering
_KEY_LABEL = None


def _labels():
    global _KEY_LABEL
    if _KEY_LABEL is None:
        import cot_table as ct
        _KEY_LABEL = [(g, [(r[3], r[0]) for r in rows]) for g, rows in
                      (("Currency pairs", ct.FX_ROWS), ("Commodities & indexes", ct.CM_ROWS))]
    return _KEY_LABEL


def _fmt_ts(ts):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return t.strftime("%d %b %Y %H:%M UTC")


def _age_min(ts, now):
    t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return max(0, int((now - t).total_seconds() // 60))


def _age_txt(m):
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h, mm = divmod(m, 60)
    return f"{h} h {mm:02d} min" if h < 48 else f"{h // 24} days"


def _n(v, dec=2):
    if v is None:
        return "&mdash;"
    if dec == 0:
        return f"{int(round(v)):,}"
    return f"{v:,.{dec}f}"


def _price(v):
    if v is None:
        return "&mdash;"
    return f"{v:,.6f}".rstrip("0").rstrip(".")      # as published, no rounding


def _pc(v):
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def _bar(lp, sp):
    """Two-sided bar; colour strength grows with the imbalance, 50/50 stays near neutral."""
    a = round(0.22 + 0.78 * min(imbalance(lp, sp), 60) / 60, 3)
    return (f'<div class="mfxbar" style="--a:{a}" role="img" aria-label="long {_pc(lp)}%, short {_pc(sp)}%">'
            f'<span class="mfl" style="width:{lp:.4g}%"></span><span class="mfs" style="width:{sp:.4g}%"></span></div>')


def _freshness_line(snap, now, compact=False):
    ts = snap.get("fetched_at")
    st = snap.get("status") or {}
    if not ts:
        return ""
    m = _age_min(ts, now)
    f = freshness(m)
    label = {"fresh": "", "delayed": "Delayed", "stale": "Stale"}[f]
    failed = st.get("last_error") and st.get("last_error") != "config"
    warn = ('<span class="mfxwarn">Last known Myfxbook data &mdash; stale'
            + (f' (latest update failed: {_esc(st.get("last_error_msg") or st["last_error"])})' if not compact else "")
            + "</span>") if failed else ""
    return (f'<span class="mfxfresh" data-mfx-ts="{_esc(ts)}" data-mfx-delayed="{DELAYED_MIN}" data-mfx-stale="{STALE_MIN}">'
            f'Updated: {_fmt_ts(ts)} &middot; Age: <span class="mfxage">{_age_txt(m)}</span> '
            f'<span class="mfxstate {f}">{label}</span></span>{warn}')


ATTRIB = (f'<p class="mfxsrc">Retail sentiment data: <a href="{SOURCE_URL}" target="_blank" '
          f'rel="noopener">{SOURCE_NAME}</a>. Positions of the trading accounts linked to Myfxbook '
          f'&mdash; one community of retail traders, not the whole market. Shown as published; '
          f'this dashboard does not generate or adjust it.</p>')

# Live age without a server: the page is static, so a tiny script turns each stored
# timestamp into its current age and label. Without script the build-time age stays.
SCRIPT = """<script>(function(){function u(){var n=Date.now();document.querySelectorAll('[data-mfx-ts]').forEach(function(e){var t=Date.parse(e.getAttribute('data-mfx-ts'));if(isNaN(t))return;var m=Math.max(0,Math.floor((n-t)/60000)),a=e.querySelector('.mfxage'),s=e.querySelector('.mfxstate');if(a)a.textContent=m<60?m+(m==1?' minute':' minutes'):(m<2880?Math.floor(m/60)+' h '+('0'+m%60).slice(-2)+' min':Math.floor(m/1440)+' days');if(s){var st=+e.getAttribute('data-mfx-stale'),dl=+e.getAttribute('data-mfx-delayed');s.className='mfxstate '+(m>st?'stale':m>dl?'delayed':'fresh');s.textContent=m>st?'Stale':m>dl?'Delayed':'';}})}u();setInterval(u,60000);})();</script>"""


def _row(key, label, m, link=True):
    name = (f'<a href="#cot50-{_esc(key)}" class="mfxname">{_esc(label)}</a>' if link
            else f'<span class="mfxname">{_esc(label)}</span>')
    if not m:
        return (f'<div class="mfxrow na" data-mfx-key="{_esc(key)}">{name}'
                f'<span class="mfxna">Retail sentiment unavailable</span></div>')
    lp, sp = m["longPercentage"], m["shortPercentage"]
    imb = imbalance(lp, sp)
    em = "hi" if imb >= 40 else ("md" if imb >= 20 else "lo")
    return (f'<div class="mfxrow" data-mfx-key="{_esc(key)}">'
            f'<div class="mfxhd">{name}'
            + (f'<span class="mfxsym">{_esc(m["symbol"])}</span>' if m["symbol"] != label.upper() else "")
            + f'<span class="mfximb {em}" title="Imbalance = |Long % - Short %|">Imbalance {imb}</span>'
            f'<span class="mfxcrowd">Retail crowd: <b>{crowd(lp, sp)}</b></span></div>'
            f'<div class="mfxls"><span class="mfxl">LONG {_pc(lp)}%</span>{_bar(lp, sp)}'
            f'<span class="mfxs">SHORT {_pc(sp)}%</span></div>'
            f'<dl class="mfxv"><dt>Volume (lots)</dt><dd>L {_n(m["longVolume"])} &middot; S {_n(m["shortVolume"])}</dd>'
            f'<dt>Positions</dt><dd>L {_n(m["longPositions"], 0)} &middot; S {_n(m["shortPositions"], 0)}</dd>'
            f'<dt>Avg price</dt><dd>L {_price(m["avgLongPrice"])} &middot; S {_price(m["avgShortPrice"])}</dd></dl>'
            f'</div>')


def _load(data):
    return _read(Path(data or DATA) / OUT_NAME, {})


def retail_panel(now=None, data=None):
    """The 'Retail' tab."""
    now = now or dt.datetime.now(dt.timezone.utc)
    snap = _load(data)
    st = snap.get("status") or {}
    markets = snap.get("markets") or {}
    head = ('<section class="mfx"><h2>Retail sentiment &mdash; Myfxbook</h2>'
            '<p class="sub">How Myfxbook&rsquo;s community of retail traders is positioned in each of the '
            'dashboard&rsquo;s markets: share long vs short, volume, number of positions and average entry '
            'price. <b>Separate from the COT data</b> (institutional futures positioning) and from the '
            'CFTC-based &ldquo;Retail sentiment&rdquo; on the COT tab &mdash; nothing is combined or scored. '
            'Long is not &ldquo;bullish&rdquo; and short is not &ldquo;bearish&rdquo;: these are raw '
            'positioning figures, not trade signals. Tap a market for its 50-week COT report, where the '
            'same figures sit beside the institutional data.</p>')
    if not snap.get("fetched_at"):
        msg = ("Myfxbook credentials are not configured yet, so no data has been fetched."
               if not st.get("configured") else
               "No Myfxbook data yet" + (f" &mdash; last attempt failed: {_esc(st.get('last_error_msg') or '')}"
                                          if st.get("last_error") else " &mdash; fetched on the next refresh."))
        return head + f'<p class="mfxempty">{msg}</p>' + ATTRIB + "</section>"
    body = [f'<p class="mfxmeta">{_freshness_line(snap, now)}</p>']
    for g, rows in _labels():
        body.append(f'<h3 class="mfxg">{g}</h3><div class="mfxlist">')
        body.extend(_row(k, lab, markets.get(k)) for k, lab in rows)
        body.append("</div>")
    legend = ('<p class="mfxkey"><span class="mfxl">LONG</span> and <span class="mfxs">SHORT</span> are '
              'two colours that mean nothing more than the side. The bar&rsquo;s colour strengthens with the '
              '<b>imbalance</b> = |Long % &minus; Short %| (50/50 = 0, 60/40 = 20, 80/20 = 60). '
              '<b>Retail crowd</b>: Long &ge; 70% Heavy Long, 60&ndash;69% Long, 41&ndash;59% Balanced, '
              'Short 60&ndash;69% Short, &ge; 70% Heavy Short &mdash; a description, not a signal.</p>')
    return head + "".join(body) + legend + ATTRIB + SCRIPT + "</section>"


def report_card(key, now=None, data=None):
    """The small 'Myfxbook retail' card inside a market's 50-week COT report."""
    now = now or dt.datetime.now(dt.timezone.utc)
    snap = _load(data)
    head = '<div class="mfxcard"><div class="mfxct">Myfxbook retail'
    if not snap.get("fetched_at"):
        return head + '</div><p class="mfxna">No Myfxbook data yet.</p></div>'
    m = (snap.get("markets") or {}).get(key)
    if not m:
        return head + '</div><p class="mfxna">Retail sentiment unavailable</p></div>'
    lp, sp = m["longPercentage"], m["shortPercentage"]
    return (head + f' <span class="mfxsym">{_esc(m["symbol"])}</span></div>'
            f'<div class="mfxls"><span class="mfxl">LONG {_pc(lp)}%</span>{_bar(lp, sp)}'
            f'<span class="mfxs">SHORT {_pc(sp)}%</span></div>'
            f'<p class="mfxcl">Long {_pc(lp)}% &middot; Short {_pc(sp)}% &middot; Imbalance {imbalance(lp, sp)} '
            f'&middot; Retail crowd: <b>{crowd(lp, sp)}</b></p>'
            f'<p class="mfxcl mut">{_freshness_line(snap, now, compact=True)} &middot; '
            f'Retail sentiment data: <a href="{SOURCE_URL}" target="_blank" rel="noopener">{SOURCE_NAME}</a></p>'
            f'</div>')
