"""Major-releases-only score card for the dashboard.

A separate per-currency score built from NOTHING but the big scheduled prints - the ones a
trader actually watches: NFP, CPI, PPI, FOMC / rate decisions, GDP, PMI / ISM, jobs, retail
sales. Same actual-vs-forecast surprise the desk's news leg already computes (positive =
bullish for the currency, inversions handled), but a LONG half-life because these land
monthly, and with the checklist / COT / open interest / positioning deliberately left out.

Reads data/calendar.json (generated every run), writes data/flagship.json as a record, and
injects its own card into dashboard.html between <!--FLAGSHIP_START--> / <!--FLAGSHIP_END-->.
Stateless: if flagship.json is missing it just rebuilds from calendar.json.

    python flagship.py [dashboard.html]
"""
import json, math, re, sys
import datetime as dt
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                             # noqa: BLE001
    pass

DATA = Path(__file__).parent / "data"
ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD"]
NAMES = {"USD": "US Dollar", "EUR": "Euro", "GBP": "British Pound", "JPY": "Japanese Yen",
         "AUD": "Australian Dlr", "NZD": "NZ Dollar", "CAD": "Canadian Dlr"}

HALFLIFE_H = 240              # ~10 days - a monthly print still counts for a fortnight
LOOKBACK_H = 24 * 45         # 45 days of releases
IMPACT_W = {"High": 1.0, "Medium": 0.5, "Low": 0.2}

# the only releases that qualify - substring match on the lowered title
FLAGSHIP = (
    "non farm payroll", "nonfarm payroll", "non-farm payroll", "nfp",
    "unemployment rate", "employment change", "jobless claims", "adp employment",
    "average hourly earnings", "labour force", "labor force",
    "cpi", "inflation rate", "core inflation", "ppi", "producer price",
    "gdp growth", "gross domestic product",
    "ism manufacturing", "ism services", "ism non-manufacturing",
    "manufacturing pmi", "services pmi", "composite pmi", "s&p global",
    "retail sales",
    "interest rate decision", "rate decision", "rate statement",
    "fomc", "monetary policy statement", "official cash rate", "bank rate",
)
FLAGSHIP_VETO = ("mba ", "redbook", "chain store", "expectations index",
                 "current conditions", "5-year", "consumer confidence",
                 "annual revision", "benchmark revision", " revision",
                 "brc ", "monitor", "flash pmi expectations")   # private surveys, not the official print


def _is_flagship(title):
    t = (title or "").lower()
    if any(v in t for v in FLAGSHIP_VETO):
        return False
    return any(k in t for k in FLAGSHIP)


def _base(title):
    """Strip only REVISION tags (prel / final / adv / flash ...). NOT q/q vs y/y - those
    are genuinely different series released together and both count, same as the main
    news leg. Collapsing them silently dropped the stronger GDP-QoQ print for the weaker
    GDP-YoY one and knocked AUD from +19 to +3."""
    t = (title or "").lower()
    for tag in (" prel", " final", " adv", " 2nd est", " 3rd est", " flash", " revised",
                " preliminary", " advance", " prelim", " s.a", " sa"):
        t = t.replace(tag, "")
    return " ".join(t.split())


def compute():
    try:
        cal = json.loads((DATA / "calendar.json").read_text(encoding="utf-8"))
    except Exception:                                         # noqa: BLE001
        cal = {"released": []}
    now = dt.datetime.now(dt.timezone.utc)

    latest = {}
    for e in sorted(cal.get("released", []), key=lambda r: r.get("when", "")):
        # need a real forecast - "actual vs what the market expected" is the whole point;
        # an actual-vs-previous move is a different signal and belongs in the checklist
        if e.get("surprise") is None or e.get("forecast") is None:
            continue
        if not _is_flagship(e.get("title", "")) or e.get("ccy") not in ORDER:
            continue
        latest[(e["ccy"], _base(e["title"]))] = e

    num = {c: 0.0 for c in ORDER}
    den = {c: 0.0 for c in ORDER}
    evs = {c: [] for c in ORDER}
    for e in latest.values():
        c = e["ccy"]
        try:
            age_h = (now - dt.datetime.fromisoformat(e["when"])).total_seconds() / 3600
        except Exception:                                     # noqa: BLE001
            continue
        if age_h < 0 or age_h > LOOKBACK_H:
            continue
        w = IMPACT_W.get(e.get("impact"), 0.3) * 0.5 ** (age_h / HALFLIFE_H)
        num[c] += e["surprise"] * w
        den[c] += w
        evs[c].append({"title": e["title"], "actual": e.get("actual"),
                       "forecast": e.get("forecast"), "surprise": round(e["surprise"], 3),
                       "impact": e.get("impact", ""), "when": e["when"],
                       "age_d": round(age_h / 24, 1),
                       "points": round(e["surprise"] * w * 100, 1)})

    out = {}
    for c in ORDER:
        evs[c].sort(key=lambda r: -abs(r["points"]))
        if den[c] <= 0:
            out[c] = {"score": 0.0, "n": 0, "events": []}
            continue
        conf = math.tanh(den[c] / 1.2)
        out[c] = {"score": round((num[c] / den[c]) * conf * 100, 1),
                  "n": len(evs[c]), "events": evs[c][:6]}
    res = {"as_of": now.isoformat(), "currencies": out}
    try:
        (DATA / "flagship.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    except Exception:                                         # noqa: BLE001
        pass
    return res


# ---------------------------------------------------------------- render
def _esc(s):
    return ("" if s is None else str(s)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pill(score):
    if score >= 28:
        return "vsb", "Strong data"
    if score >= 10:
        return "mb", "Beating forecasts"
    if score <= -28:
        return "vwb", "Weak data"
    if score <= -10:
        return "mbr", "Missing forecasts"
    return "neu", "In line"


def _bar(score):
    pct = max(-100.0, min(100.0, score)) / 100 * 50
    if score >= 0:
        style = f"left:50%;width:{pct:.1f}%"
        cls = "pos"
    else:
        style = f"left:{50 + pct:.1f}%;width:{-pct:.1f}%"
        cls = "neg"
    return f'<span class="bar"><span class="fill {cls}" style="{style}"></span></span>'


def _driver(d, score):
    """The release that best explains the score - the biggest contributor IN the score's
    direction when the score is clearly directional, otherwise the biggest overall. Stops
    a beat showing next to a negative number."""
    evs = d.get("events") or []
    if not evs:
        return None
    if abs(score) >= 8:
        same = [e for e in evs if (e["points"] >= 0) == (score >= 0)]
        if same:
            return same[0]
    return evs[0]


def _one_liner(ev):
    if not ev:
        return "no major release in the window"
    a, f = ev.get("actual"), ev.get("forecast")
    vs = f" vs {f} exp" if f is not None else ""
    return f'{_esc(ev["title"])} {_esc(a)}{vs}  ({ev["age_d"]}d ago)'


def render_block(res):
    rows = []
    for c in sorted(ORDER, key=lambda x: -res["currencies"][x]["score"]):
        d = res["currencies"][c]
        s = d["score"]
        cls, word = _pill(s)
        top = _driver(d, s)
        rows.append(
            f'<div class="mrow">'
            f'<div class="mccy">{c}<span class="mname">{NAMES[c]}</span></div>'
            f'{_bar(s)}'
            f'<div class="mscore {"pos" if s >= 0 else "neg"}">{s:+.0f}</div>'
            f'<div class="mrate"><span class="pill {cls}">{word}</span>'
            f'<span class="mname">{_one_liner(top)}</span></div>'
            f'</div>')
    asof = ""
    try:
        t = dt.datetime.fromisoformat(res["as_of"])
        asof = t.strftime("%d %b %H:%M UTC")
    except Exception:                                         # noqa: BLE001
        pass
    return (
        '<style>.fs-card{margin:22px 0;padding-top:18px;border-top:1px solid var(--line,#333)}'
        '.fs-card .sub{max-width:60ch}</style>'
        '<section class="fs-card">'
        '<h2>Major releases only</h2>'
        '<p class="sub">A score built from <b>nothing but the big scheduled data</b> &mdash; '
        'NFP, CPI, PPI, FOMC &amp; other rate decisions, GDP, ISM / PMI, jobs and retail sales. '
        'Actual vs forecast over the last 45 days, newer prints weighted more. No checklist, '
        'no COT, no positioning &mdash; just how the headline numbers have landed. '
        f'<span class="mut">Updated {asof}.</span></p>'
        f'<div class="meter">{"".join(rows)}</div>'
        '</section>'
    )


# ---------------------------------------------------------------- inject
MARK_A, MARK_B = "<!--FLAGSHIP_START-->", "<!--FLAGSHIP_END-->"


def inject(html_path, block):
    p = Path(html_path)
    if not p.exists():
        print(f"  inject: {p} not found, skipped")
        return False
    html = p.read_text(encoding="utf-8")
    wrapped = MARK_A + block + MARK_B
    if MARK_A in html and MARK_B in html:
        html = re.sub(re.escape(MARK_A) + ".*?" + re.escape(MARK_B),
                      lambda _: wrapped, html, flags=re.S)
    else:
        # right after the main "Strength meter" section - the blended board stays first,
        # this supplementary card sits directly below it
        m = re.search(r'<section>\s*<h2>\s*What moved each score', html, flags=re.I)
        if not m:
            m = re.search(r'<section>\s*<h2>\s*Commodities', html, flags=re.I)
        if m:
            html = html[:m.start()] + wrapped + html[m.start():]
        elif "</body>" in html:
            html = html.replace("</body>", wrapped + "\n</body>")
        else:
            html += wrapped
    p.write_text(html, encoding="utf-8")
    print(f"  injected the flagship card into {p.name}")
    return True


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "dashboard.html"
    res = compute()
    scores = "  ".join(f"{c} {res['currencies'][c]['score']:+.0f}" for c in ORDER)
    print(f"  flagship: {scores}")
    inject(target, render_block(res))


if __name__ == "__main__":
    main()
