"""Breaking-news watcher for the FX desk.

Runs every ~15 min on GitHub Actions. Pulls a handful of free news feeds + the
TradingView economic calendar, keeps only items that are BOTH fresh (published in
the last MAX_AGE_MIN minutes) and material (a keyword classifier), works out the
likely push on the dollar / metals / oil / majors from a fixed playbook, and pings
an ntfy.sh topic so it lands on the phone. Weekend hits get an expected-gap line.

No API keys, stdlib only. Dedup state lives in data/news_seen.json (cached between
runs by the workflow). Set the ntfy topic via the NTFY_TOPIC env var (a GitHub
Actions secret) or data/ntfy_topic.txt for a local test run.

    python news_watch.py            # normal run
    python news_watch.py --dry-run  # classify + print, do not push
    python news_watch.py --test     # push one test alert and exit
"""
import json, os, sys, re, hashlib, time, urllib.request, urllib.error
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

try:                                    # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                             # noqa: BLE001
    pass

DATA = Path(__file__).parent / "data"
SEEN_FILE = DATA / "news_seen.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MAX_AGE_MIN = 45          # only alert on items published this recently
SEEN_TTL_DAYS = 4         # forget dedup hashes older than this
NTFY_BASE = "https://ntfy.sh"

# ---------------------------------------------------------------- news sources
from urllib.parse import quote as _q

_GN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_QUERIES = [
    '"Federal Reserve" OR Powell OR Bessent OR "rate cut" OR "rate hike" OR tariffs',
    'Trump (dollar OR tariff OR "Federal Reserve" OR Powell OR sanctions OR rates)',
    '(Iran OR Israel OR "Middle East" OR Russia OR Ukraine OR Taiwan OR "North Korea") '
    '(strike OR attack OR war OR sanctions OR missile OR invasion)',
    '("US dollar" OR DXY OR gold OR "safe haven" OR "risk off" OR "oil prices") '
    '(surges OR plunges OR jumps OR spikes OR tumbles OR soars)',
    '(OPEC OR "oil output" OR "production cut" OR "Strait of Hormuz" OR embargo)',
]
FEEDS = [_GN.format(q=_q(x)) for x in _QUERIES] + [
    # direct FX news feeds (best-effort - skipped silently if they block us)
    "https://www.forexlive.com/feed/news/",
    "https://www.fxstreet.com/rss/news",
]

# ---------------------------------------------------------------- classifier
# (category, severity 1-3, [keyword...]).  First match wins, order = priority.
RULES = [
    ("geo_escalation", 3, [
        "airstrike", "air strike", "missile strike", "attacked iran", "strikes iran",
        "strike on iran", "bombing", "invasion", "invaded", "declares war", "act of war",
        "military strike", "military action", "retaliatory strike", "nuclear strike",
        "ballistic missile", "troops enter", "ground offensive", "attacks israel",
        "strikes on", "bombed", "shot down", "warships", "blockade",
    ]),
    ("geo_deescalation", 3, [
        "ceasefire", "cease-fire", "ceasefire agreed", "ceasefire deal", "ceasefire holds",
        "peace deal", "peace agreement", "truce agreed", "truce holds", "agrees to a truce",
        "de-escalat", "deescalat", "stand down", "halt strikes", "halts strikes",
        "pause in fighting", "stops attacks", "ends strikes", "end to hostilities",
        "sanctions lifted", "sanctions eased", "prisoner swap", "hostage deal",
        "withdraw troops", "pull back forces", "diplomatic breakthrough", "reopen the strait",
        "strait of hormuz reopen", "iran talks resume", "back to the table",
    ]),
    ("trump_fed", 3, [
        "fire powell", "remove powell", "replace powell", "oust powell", "powell resign",
        "fed independence", "attack on the fed", "shadow fed chair", "shadow chair",
        "trump wants lower rates", "pressure the fed", "trump slams powell",
        "trump blasts powell", "demands rate cut",
    ]),
    ("tariff", 2, [
        "new tariff", "tariffs on", "tariff hike", "raise tariffs", "impose tariffs",
        "trade war", "import duties", "section 301", "section 232", "retaliatory tariff",
        "tariff threat", "100% tariff", "sweeping tariffs", "blanket tariff",
        "export ban", "export controls", "chip ban",
    ]),
    ("fed_hawkish", 2, [
        "hawkish", "higher for longer", "not ready to cut", "premature to cut",
        "inflation too high", "more work to do on inflation", "rate hike on the table",
        "open to a hike", "dissented in favor of a hike", "restrictive for longer",
        "pushes back on rate cut", "no rush to cut",
    ]),
    ("fed_dovish", 2, [
        "dovish", "rate cut is coming", "ready to cut", "time to cut", "cut is warranted",
        "close to cutting", "labor market weakening", "cooling labor market",
        "open the door to a cut", "signals rate cut", "50 basis point cut",
        "emergency cut", "intermeeting cut",
    ]),
    ("energy_supply", 2, [
        "opec+ cut", "opec cut", "production cut", "output cut", "supply disruption",
        "pipeline attack", "refinery fire", "oil embargo", "crude supply",
        "strait of hormuz", "shut the strait", "tanker seized", "oil facility",
    ]),
    ("risk_off_move", 2, [
        "safe haven rush", "risk-off", "risk off", "flight to safety", "market rout",
        "stocks plunge", "stocks tumble", "sell-off deepens", "vix spikes",
        "dollar surges", "gold hits record", "gold surges", "yen surges",
    ]),
    ("cb_surprise", 2, [
        "surprise rate", "unexpected rate", "emergency meeting", "snap decision",
        "intervenes in fx", "fx intervention", "currency intervention",
        "unscheduled meeting", "boj hikes", "snb cuts", "ecb emergency",
    ]),
]

# playbook: what each category typically does. one tight line per instrument.
PLAYBOOK = {
    "geo_escalation": {
        "emoji": "\U0001f6a8", "risk": "RISK-OFF",
        "dxy": "UP", "gold": "UP (haven + inflation hedge)", "silver": "UP",
        "wti": "UP hard if Middle East / Hormuz", "jpy": "UP (haven)", "chf": "UP",
        "eur": "DOWN", "gbp": "DOWN", "aud": "DOWN", "nzd": "DOWN", "cad": "mixed (petro cushions)",
        "equities": "DOWN", "note": "Bigger and faster if oil supply is in play. Fades if contained.",
    },
    "geo_deescalation": {
        "emoji": "\U0001f54a️", "risk": "RISK-ON",
        # gold/silver were "DOWN" here until the backtest: on 121 ceasefire event-days
        # gold fell only 36% of the time against a 45% baseline (-9pp), silver -10pp.
        # The fear premium leaving fights oil/yields falling, and neither wins.
        "dxy": "DOWN", "gold": "no clean read (measured both ways)",
        "silver": "no clean read", "wti": "DOWN (risk premium out)",
        "jpy": "DOWN", "chf": "DOWN", "eur": "UP", "gbp": "UP", "aud": "UP", "nzd": "UP",
        "cad": "mixed", "equities": "UP", "note": "Unwind of any prior risk-off spike.",
    },
    "trump_fed": {
        "emoji": "\U0001f3db️", "risk": "USD-NEGATIVE",
        "dxy": "DOWN", "gold": "UP (Fed-credibility hedge)", "silver": "UP",
        "wti": "mild UP", "jpy": "UP vs USD", "chf": "UP vs USD", "eur": "UP", "gbp": "UP",
        "aud": "UP vs USD", "nzd": "UP vs USD", "cad": "UP vs USD", "equities": "mixed / down",
        "note": "Short-end yields DOWN, long-end UP (term premium) - curve steepens. Sharp if Powell is actually removed.",
    },
    "tariff": {
        "emoji": "\U0001f6a2", "risk": "RISK-OFF / USD-firm",
        "dxy": "UP (near-term)", "gold": "UP", "silver": "mixed", "wti": "DOWN (demand fear)",
        "jpy": "UP", "chf": "UP", "eur": "DOWN if EU targeted", "gbp": "mild DOWN",
        "aud": "DOWN (China proxy)", "nzd": "DOWN", "cad": "DOWN if Canada targeted; CNH/MXN DOWN",
        "equities": "DOWN", "note": "Currency of the targeted country takes the hit; USD and havens bid.",
    },
    "fed_hawkish": {
        "emoji": "\U0001f985", "risk": "USD-POSITIVE",
        "dxy": "UP", "gold": "DOWN", "silver": "DOWN", "wti": "mild DOWN",
        "jpy": "DOWN (USDJPY up)", "chf": "DOWN vs USD", "eur": "DOWN", "gbp": "DOWN",
        "aud": "DOWN", "nzd": "DOWN", "cad": "DOWN", "equities": "DOWN",
        "note": "US yields UP across the curve. Strongest reaction at the front end.",
    },
    "fed_dovish": {
        "emoji": "\U0001f54a️", "risk": "USD-NEGATIVE",
        "dxy": "DOWN", "gold": "UP", "silver": "UP", "wti": "mild UP",
        "jpy": "UP (USDJPY down)", "chf": "UP vs USD", "eur": "UP", "gbp": "UP",
        "aud": "UP", "nzd": "UP", "cad": "UP", "equities": "UP",
        "note": "US yields DOWN. Gold and rate-sensitive FX lead.",
    },
    "energy_supply": {
        "emoji": "\U0001f6e2️", "risk": "INFLATION / petro-FX",
        # gold/silver were "UP" on the inflation-hedge argument; measured -7pp and -8pp.
        # Dearer oil lifts yields as readily as it lifts inflation hedges.
        "dxy": "mild UP", "gold": "no clean read (the hedge step does not measure)",
        "silver": "no clean read", "wti": "UP (supply) / DOWN (if a cut is cancelled)",
        "jpy": "DOWN (import cost)", "chf": "flat", "eur": "DOWN (energy importer)", "gbp": "mild DOWN",
        "aud": "mixed", "nzd": "DOWN", "cad": "UP (petro)", "equities": "DOWN (energy up = margin squeeze)",
        "note": "NOK and CAD are the cleanest petro-FX plays. Direction of WTI depends on the headline.",
    },
    "risk_off_move": {
        "emoji": "\U0001f4c9", "risk": "RISK-OFF (already moving)",
        "dxy": "UP", "gold": "UP", "silver": "mixed", "wti": "DOWN",
        "jpy": "UP", "chf": "UP", "eur": "DOWN", "gbp": "DOWN", "aud": "DOWN", "nzd": "DOWN",
        "cad": "DOWN", "equities": "DOWN", "note": "Confirmation, not a fresh catalyst - move may be mature.",
    },
    "cb_surprise": {
        "emoji": "⚡", "risk": "FX-SPECIFIC",
        "dxy": "depends", "gold": "UP on any policy shock", "silver": "UP",
        "wti": "flat", "jpy": "big move if BoJ / MoF", "chf": "big move if SNB",
        "eur": "big move if ECB", "gbp": "big move if BoE", "aud": "-", "nzd": "-", "cad": "-",
        "equities": "DOWN on a rate-hike shock", "note": "The acting central bank's currency moves most; read the direction from the headline.",
    },
    "data_surprise_hot": {
        "emoji": "\U0001f4c8", "risk": "USD-POSITIVE",
        # measured: gold -6pp vs baseline = no edge, not a short. Oil is the real leg (+9pp).
        "dxy": "UP", "gold": "mild DOWN at most (measures weak)", "silver": "mild DOWN",
        "wti": "mild UP (+9pp measured)",
        "jpy": "DOWN (USDJPY up)", "chf": "DOWN vs USD", "eur": "DOWN", "gbp": "DOWN",
        "aud": "DOWN", "nzd": "DOWN", "cad": "DOWN", "equities": "DOWN if it kills rate-cut hopes",
        "note": "Strong US data - pushes the Fed toward keeping rates high / hiking.",
    },
    "data_surprise_cold": {
        "emoji": "\U0001f4c9", "risk": "USD-NEGATIVE",
        "dxy": "DOWN", "gold": "UP", "silver": "UP", "wti": "DOWN (demand)",
        "jpy": "UP (USDJPY down)", "chf": "UP vs USD", "eur": "UP", "gbp": "UP",
        "aud": "UP", "nzd": "UP", "cad": "DOWN if its own data", "equities": "UP (cut hopes)",
        "note": "Weak US data - pushes the Fed toward cutting rates.",
    },
}

# plain-English display names (the internal keys stay as-is for the classifier)
CAT_LABEL = {
    "geo_escalation": "GEOPOLITICS — escalation",
    "geo_deescalation": "GEOPOLITICS — de-escalation",
    "trump_fed": "TRUMP vs THE FED",
    "tariff": "TARIFFS / TRADE WAR",
    "fed_hawkish": "FED — leaning toward higher rates",
    "fed_dovish": "FED — leaning toward rate cuts",
    "energy_supply": "OIL / ENERGY SUPPLY",
    "risk_off_move": "RISK-OFF move underway",
    "cb_surprise": "CENTRAL-BANK SURPRISE",
    "data_surprise_hot": "US DATA — strong (USD positive)",
    "data_surprise_cold": "US DATA — weak (USD negative)",
}

INSTR_ORDER = [("dxy", "DXY"), ("gold", "Gold"), ("silver", "Silver"), ("wti", "WTI"),
               ("jpy", "JPY"), ("eur", "EUR"), ("gbp", "GBP"),
               ("aud", "AUD"), ("nzd", "NZD"), ("cad", "CAD"), ("equities", "Equities")]

GAP_LINE = ("Gold GAPS UP, WTI GAPS UP, USD GAPS UP, AUD/NZD GAP DOWN, "
            "JPY GAPS UP at the Sunday/Monday open" )
GAP_LINE_RISKON = ("Gold GAPS DOWN, WTI GAPS DOWN, AUD/NZD GAP UP, "
                   "risk premium comes out at the open")


# ---------------------------------------------------------------- helpers
def _get(url, timeout=25):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:                                 # noqa: BLE001
            if attempt == 2:
                print(f"  feed failed: {url.split('?')[0]}  ({type(e).__name__})")
                return b""
            time.sleep(1.5 * (attempt + 1))
    return b""


_YSYMS = [("Gold", "GC=F"), ("Silver", "SI=F"), ("WTI", "CL=F"), ("DXY", "DX-Y.NYB"),
          ("US10Y", "%5ETNX"), ("S&P", "%5EGSPC"), ("USDJPY", "JPY=X"), ("AUDUSD", "AUDUSD=X")]


def market_snapshot():
    """{name: (last, pct_today)} from Yahoo, so an alert can lead with the ACTUAL
    reaction rather than only the textbook one. Best-effort - returns {} on failure."""
    out = {}
    for name, sym in _YSYMS:
        try:
            u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                 f"?range=5d&interval=1d")
            d = json.loads(_get(u, timeout=12) or b"{}")
            r = d["chart"]["result"][0]
            closes = [x for x in r["indicators"]["quote"][0]["close"] if x is not None]
            last = r["meta"].get("regularMarketPrice") or closes[-1]
            prev = closes[-2] if len(closes) >= 2 else closes[0]
            out[name] = (last, (last - prev) / prev * 100)
        except Exception:                                     # noqa: BLE001
            continue
    return out


def _snap_line(snap):
    if not snap:
        return ""
    bits = []
    for name, _ in _YSYMS:
        if name in snap:
            v, p = snap[name]
            fmt = f"{v:,.2f}" if v > 5 else f"{v:.4f}"
            bits.append(f"{name} {fmt} ({p:+.1f}%)")
    return "Now: " + "  ".join(bits)


# instruments where a "where was it before the risk premium went in" read is useful
_LVL_SYMS = [("WTI", "CL=F", 1), ("Brent", "BZ=F", 1), ("Gold", "GC=F", 0),
            ("US10Y", "%5ETNX", 2), ("S&P", "%5EGSPC", 0),
            ("NZDUSD", "NZDUSD=X", 4), ("AUDUSD", "AUDUSD=X", 4), ("USDCAD", "USDCAD=X", 4),
            ("USDJPY", "JPY=X", 2)]


def level_map(back_days: int = 12):
    """{name: (last, baseline ~back_days trading days ago, 20d low, 20d high, ndp)} - lets a
    geopolitical alert say concretely where each market sat BEFORE the risk premium and where
    a ceasefire would unwind it to. Best-effort; {} on failure."""
    out = {}
    for name, sym, ndp in _LVL_SYMS:
        try:
            u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                 f"?range=2mo&interval=1d")
            d = json.loads(_get(u, timeout=12) or b"{}")
            r = d["chart"]["result"][0]
            c = [x for x in r["indicators"]["quote"][0]["close"] if x is not None]
            if len(c) < 25:
                continue
            last = r["meta"].get("regularMarketPrice") or c[-1]
            base = c[-(back_days + 1)]
            out[name] = (last, base, min(c[-20:]), max(c[-20:]), ndp)
        except Exception:                                     # noqa: BLE001
            continue
    return out


def _fmt(v, ndp):
    return f"{v:,.{ndp}f}"


def _unwind_section(cat, lmap):
    """For a ceasefire: where the risk premium unwinds to. For an escalation: how much
    premium is already in and where the headline room is."""
    if not lmap:
        return ""
    rows = []
    de = cat == "geo_deescalation"
    hdr = ("UNWIND MAP — a confirmed & holding ceasefire takes the war premium back out:"
           if de else "RISK PREMIUM ALREADY IN (vs ~2-3 weeks ago, before this leg):")
    for name, sym, _ in _LVL_SYMS:
        if name not in lmap:
            continue
        last, base, lo, hi, ndp = lmap[name]
        diff = last - base
        pct = diff / base * 100 if base else 0.0
        if de:
            if name in ("WTI", "Brent"):
                rows.append(f"  {name}: {_fmt(last,ndp)} → toward {_fmt(base,ndp)} (pre-strikes), "
                            f"then {_fmt(lo,ndp)} (the pre-war base)")
            elif name == "Gold":
                rows.append(f"  Gold: {_fmt(last,ndp)} → a relief bounce as yields ease, but the "
                            f"Fed-stays-high story still caps it; {_fmt(hi,ndp)} is the ceiling")
            elif name == "US10Y":
                rows.append(f"  US10Y: {_fmt(last,ndp)}% → back toward {_fmt(base,ndp)}% as the "
                            f"inflation premium comes out")
            elif name == "S&P":
                rows.append(f"  S&P: {_fmt(last,ndp)} → recovers toward {_fmt(hi,ndp)} (recent high)")
            elif name in ("NZDUSD", "AUDUSD"):
                rows.append(f"  {name}: {_fmt(last,ndp)} → risk-FX relief pop toward "
                            f"{_fmt(hi,ndp)} (20-day high); NZD also has the RBNZ decision on top")
            elif name == "USDCAD":
                rows.append(f"  USDCAD: {_fmt(last,ndp)} → CAD loses its petro cushion; drifts "
                            f"back up toward {_fmt(hi,ndp)} (20-day high)")
            elif name == "USDJPY":
                rows.append(f"  USDJPY: {_fmt(last,ndp)} → JPY gives back the small haven bid")
        else:
            arrow = "above" if diff >= 0 else "below"
            rows.append(f"  {name}: {_fmt(last,ndp)}  ({diff:+.{ndp}f} {arrow} the {_fmt(base,ndp)} "
                        f"level, {pct:+.1f}%)")
    return hdr + "\n" + "\n".join(rows) if rows else ""


def _parse_date(s):
    # NOTE: the ".%f" variants are not optional - the TradingView calendar API stamps
    # every event "2026-09-02T12:15:00.000Z". Without them every economic release was
    # parsed as None and silently dropped, so data-surprise alerts never fired at all.
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            d = dt.datetime.strptime(s.strip(), fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------- cross-watcher claims
# This file and commodity_watch.py read the SAME wires and run back to back in the same
# job. Measured on one 8-hour sample: 7 stories fired BOTH - so a single Hormuz headline
# buzzed the phone twice, once as an FX playbook and once as a gold/silver/oil decode.
# Neither watcher could see the other's dedup, because each keyed on its own categories.
# So they now claim a shared THEME: whoever alerts first blocks the other on that theme
# for CROSS_COOLDOWN_MIN. Each watcher's own dedup and cooldown are untouched.
THEME_FILE = DATA / "theme_claims.json"
CROSS_COOLDOWN_MIN = 45

THEMES = {
    # FX watcher categories
    "geo_escalation": "geo", "geo_deescalation": "geo", "energy_supply": "energy",
    "fed_hawkish": "macro", "fed_dovish": "macro", "trump_fed": "macro",
    "data_surprise_hot": "macro", "data_surprise_cold": "macro", "cb_surprise": "macro",
    "tariff": "tariff", "risk_off_move": "risk",
    # commodity watcher categories
    "oil_supply_tight": "energy", "oil_supply_loose": "energy",
    "oil_inv_build": "energy", "oil_inv_draw": "energy",
    "rates_up": "macro", "rates_down": "macro", "fed_independence": "macro",
    "inflation_hot": "macro", "inflation_cold": "macro",
    "us_data_strong": "macro", "us_data_weak": "macro",
    "silver_squeeze": "metals", "metal_supply": "metals", "cb_gold_buying": "metals",
    "demand_up": "growth", "demand_down": "growth", "risk_off": "risk",
}


def theme_claim(cat, who):
    """True if `who` may alert on this category's theme now.

    False when the SIBLING watcher covered the same theme inside the cooldown. Returns
    True when the same watcher claimed it - each keeps its own cooldown rules.
    """
    theme = THEMES.get(cat, cat)
    try:
        claims = json.loads(THEME_FILE.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        claims = {}
    prev = claims.get(theme)
    if prev and prev[1] != who and (time.time() - prev[0]) / 60 < CROSS_COOLDOWN_MIN:
        return False
    claims[theme] = [time.time(), who]
    cutoff = time.time() - 86400
    claims = {k: v for k, v in claims.items() if v[0] > cutoff}
    try:
        THEME_FILE.parent.mkdir(exist_ok=True)
        THEME_FILE.write_text(json.dumps(claims, indent=0), encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        pass
    return True


def load_seen():
    try:
        raw = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    return {k: v for k, v in raw.items() if v > cutoff}


def save_seen(seen):
    SEEN_FILE.parent.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=0), encoding="utf-8")


# phrases that flip the meaning - if any is present the match is vetoed
VETO = (
    "worthless piece of water", "bypass it", "bypasses", "irrelevant", "no longer matters",
    "opinion:", "analysis:", "explainer:", "how to", "what to know", "recap",
    "years ago", "last year", "in 2019", "in 2020", "in 2021", "in 2022", "in 2023",
    "anniversary", "documentary", "book review", "movie", "could happen", "what if",
    "hypothetical", "war game", "simulation", "prediction market", "odds of",
)


def classify(text):
    t = text.lower()
    if any(v in t for v in VETO):
        return None
    for cat, sev, keys in RULES:
        for k in keys:
            if k in t:
                return cat, sev, k
    return None


# ---------------------------------------------------------------- feed pull
def gather_items():
    now = dt.datetime.now(dt.timezone.utc)
    fresh = []
    for url in FEEDS:
        raw = _get(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = re.sub("<[^>]+>", " ", item.findtext("description") or "")
            pub = _parse_date(item.findtext("pubDate") or "")
            if not title or not link:
                continue
            if pub is None or (now - pub).total_seconds() > MAX_AGE_MIN * 60:
                continue
            src = ""
            se = item.find("source")
            if se is not None and se.text:
                src = se.text.strip()
            fresh.append({"title": title, "link": link, "desc": desc.strip(),
                          "src": src, "pub": pub})
    return fresh


# ---------------------------------------------------------------- calendar surprise
def calendar_surprises():
    """Big just-released US/major economic beats or misses in the last ~40 min."""
    try:
        from fetch_calendar import API, HEADERS, COUNTRY, base_indicator
    except Exception:                                          # noqa: BLE001
        return []
    now = dt.datetime.now(dt.timezone.utc)
    frm = (now - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to = (now + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    url = (f"{API}?from={frm}&to={to}"
           f"&countries=US,GB,JP,EU,AU,NZ,CA")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        rows = json.loads(urllib.request.urlopen(req, timeout=25).read()).get("result", [])
    except Exception:                                          # noqa: BLE001
        return []
    out = []
    for e in rows:
        if e.get("importance", -1) < 1:            # high impact only
            continue
        a, f = e.get("actual"), e.get("forecast")
        if a is None or f is None:
            continue
        when = _parse_date(e.get("date", ""))
        if when is None or (now - when).total_seconds() > 40 * 60 or when > now:
            continue
        denom = abs(f) if abs(f) > 1e-9 else 1.0
        surp = (a - f) / denom
        if abs(surp) < 0.15:                       # < 15% off forecast = not a surprise
            continue
        ccy = COUNTRY.get(e.get("country"), e.get("country"))
        title = e.get("title", "")
        inverted = any(w in title.lower() for w in ("unemployment", "jobless", "claims"))
        hot = (surp > 0) != inverted              # True = currency-positive print
        out.append({"ccy": ccy, "title": title, "actual": a, "forecast": f,
                    "surp": surp, "hot": hot, "us": ccy == "USD"})
    return out


# ---------------------------------------------------------------- alert build + push
def _regime_note(snap, lmap=None):
    """Which channel is the market trading? Uses the multi-day level_map (structural, not
    intraday noise) when available: gold BELOW its ~2wk baseline while 10y yields and oil
    are ABOVE theirs = the oil->inflation->Fed-stays-high chain is dominating and an
    escalation headline PRESSURES gold rather than lifting it."""
    if lmap and all(k in lmap for k in ("Gold", "US10Y", "WTI")):
        g_dn = lmap["Gold"][0] < lmap["Gold"][1] * 0.995
        y_up = lmap["US10Y"][0] > lmap["US10Y"][1] + 0.03
        oil_up = lmap["WTI"][0] > lmap["WTI"][1] * 1.02
        sp_dn = "S&P" in lmap and lmap["S&P"][0] < lmap["S&P"][1] * 0.985
        if y_up and oil_up and (g_dn or not sp_dn):
            return ("REGIME: this is being traded as oil -> inflation -> Fed-keeps-rates-high, "
                    "NOT flight-to-safety. Gold is near/below where it sat before the flare-up "
                    "while 10y yields and oil are well above - so an escalation headline lifts "
                    "yields and the dollar and PRESSURES gold. The safe-haven bid only takes "
                    "over if the news threatens growth more than inflation (broad war, equity "
                    "crash).")
        if g_dn is False and sp_dn:
            return ("REGIME: closer to classic risk-off - gold firm, equities heavy. The "
                    "textbook reaction below is roughly the one in force.")
        return ""
    if not snap or "Gold" not in snap or "US10Y" not in snap:
        return ""
    g, y = snap["Gold"][1], snap["US10Y"][1]
    if g < -0.5 and y > 0.3:
        return ("REGIME: gold FALLING while US yields RISE - traded as oil -> inflation -> "
                "Fed-stays-high, not flight-to-safety; an escalation headline PRESSURES gold.")
    return ""


def build_alert(cat, headline, link, src, weekend, snap=None, lmap=None):
    pb = PLAYBOOK[cat]
    lines = [f"{pb['emoji']} {CAT_LABEL.get(cat, cat)}  —  {pb['risk']}", ""]
    lines.append(headline + (f"  ({src})" if src else ""))
    sl = _snap_line(snap)
    if sl:
        lines += ["", sl]
    reg = _regime_note(snap, lmap)
    if reg:
        lines += ["", reg]
    if cat in ("geo_escalation", "geo_deescalation", "energy_supply"):
        us = _unwind_section(cat, lmap)
        if us:
            lines += ["", us]
    lines.append("")
    lines.append("Textbook reaction:")
    for key, label in INSTR_ORDER:
        if key in pb:
            lines.append(f"  {label:9} {pb[key]}")
    lines.append("")
    lines.append(pb["note"])
    if weekend and cat in ("geo_escalation", "tariff", "energy_supply", "cb_surprise",
                           "trump_fed", "risk_off_move"):
        lines += ["", "⚠ MARKETS CLOSED → expected open: " + GAP_LINE
                  + " (unless the yields/inflation channel dominates - then gold gaps DOWN)"]
    elif weekend and cat == "geo_deescalation":
        lines += ["", "⚠ MARKETS CLOSED → expected open: " + GAP_LINE_RISKON]
    return "\n".join(lines)


def push(topic, title, body, link, priority="high", tags="rotating_light"):
    if not topic:
        print("  NTFY_TOPIC not set - would have pushed:\n" + body + "\n")
        return False
    hdr = {
        "Title": title.encode("ascii", "replace").decode(),
        "Priority": priority,
        "Tags": tags,
        "Markdown": "yes",
    }
    if link:
        hdr["Click"] = link
    try:
        req = urllib.request.Request(f"{NTFY_BASE}/{topic}",
                                     data=body.encode("utf-8"),
                                     headers={**UA, **hdr}, method="POST")
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:                                     # noqa: BLE001
        print(f"  ntfy push failed: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------- main
def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        tf = DATA / "ntfy_topic.txt"
        if tf.exists():
            topic = tf.read_text(encoding="utf-8").strip()

    if "--test" in sys.argv:
        ok = push(topic, "FX ALERT: test",
                  "This is a test alert from news_watch.py. If you can read this on "
                  "your phone, the pipe works.", "https://haroontahir73.github.io/fxstrength-app/")
        print("test push:", "sent" if ok else "failed")
        return

    if "--audit" in sys.argv:                 # classify every current feed item, ignore age
        global MAX_AGE_MIN
        MAX_AGE_MIN = 60 * 24 * 3
        n = hits = 0
        for it in gather_items():
            n += 1
            c = classify(it["title"] + " " + it["desc"])
            if c:
                hits += 1
                print(f"  [{c[0]}/{c[1]}] {it['title'][:110]}  <{c[2]}>")
        print(f"audit: {hits}/{n} items would alert")
        return

    dry = "--dry-run" in sys.argv
    weekend = dt.datetime.now(dt.timezone.utc).weekday() >= 5
    seen = load_seen()
    fired = 0

    items = gather_items()
    cal = calendar_surprises()
    snap = market_snapshot() if (items or cal) else {}
    # a where-was-it-before-the-premium read, only when there's a geo item to attach it to
    geo = any(classify(i["title"] + " " + i["desc"]) and
              classify(i["title"] + " " + i["desc"])[0]
              in ("geo_escalation", "geo_deescalation", "energy_supply") for i in items)
    lmap = level_map() if geo else {}

    # 1) news feeds
    for it in items:
        h = hashlib.sha1((it["title"][:120]).encode("utf-8", "replace")).hexdigest()[:16]
        if h in seen:
            continue
        hit = classify(it["title"] + " " + it["desc"])
        if not hit:
            continue
        cat, sev, kw = hit
        seen[h] = time.time()
        # `not dry` matters: a --dry-run that claimed themes would silence the real
        # commodity alerts for the next 45 minutes while pushing nothing itself.
        if not dry and not theme_claim(cat, "fx"):
            print(f"  [claimed by the commodity watcher] {cat}: {it['title'][:70]}")
            continue
        body = build_alert(cat, it["title"], it["link"], it["src"], weekend, snap, lmap)
        title = f"FX: {CAT_LABEL.get(cat, cat)}"
        prio = "urgent" if sev >= 3 else "high"
        print(f"\n[{cat}/{sev}] <{kw}>\n" + "-" * 60 + f"\n{body}\n" + "-" * 60)
        if not dry:
            push(topic, title, body, it["link"], prio)
        fired += 1

    # 2) economic-calendar surprises
    for s in cal:
        key = f"cal:{s['ccy']}:{s['title']}:{s['actual']}"
        h = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
        if h in seen:
            continue
        seen[h] = time.time()
        cat = "data_surprise_hot" if s["hot"] else "data_surprise_cold"
        head = (f"{s['ccy']} {s['title']}: {s['actual']} vs {s['forecast']} forecast "
                f"({s['surp']*100:+.0f}% off)")
        body = build_alert(cat, head, "", "economic calendar", weekend, snap)
        if not s["us"]:
            body += f"\n\nNote: this is {s['ccy']} data - the direct hit is on {s['ccy']}, not the broad USD."
        print(f"[{cat}] {head}")
        if not dry:
            push(topic, f"FX ALERT: {s['ccy']} data surprise", body, "", "high", "chart_with_upwards_trend")
        fired += 1

    if not dry:
        save_seen(seen)
    print(f"done - {fired} alert(s), {len(seen)} hashes tracked, weekend={weekend}")


if __name__ == "__main__":
    main()
