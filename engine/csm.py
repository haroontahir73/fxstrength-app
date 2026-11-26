"""Currency Strength Meter (CSM) — scrapes livecharts.co.uk and renders a
numerical strength panel for the dashboard.

    python csm.py                  # print scores to stdout (debugging)
    python csm.py dashboard.html   # inject CSM panel into dashboard HTML

The source page encodes strength as CSS classes on 6 bar segments per
currency (map-innercontainer-strong1/2/3, weak1/2/3).  Bars with
``background-image:none`` are inactive.  Active-bar count is converted
to a 0–10 numerical score: ``active / 6 * 10``, rounded to 1 decimal.

The module exposes ``fetch()`` → dict[str, float] and ``render(scores)``
→ HTML string, following the same pattern as oi.py.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from html import escape as esc
from pathlib import Path

URL = "https://www.livecharts.co.uk/currency-strength.php"
DATA = Path(__file__).parent / "data"
CACHE = DATA / "csm_cache.json"

# The currencies livecharts tracks (the 8 FX majors + NOK, DKK).
# Order matches the source page.  We show the 8 majors first.
MAJORS = ("EUR", "GBP", "USD", "AUD", "JPY", "CHF", "NZD", "CAD")
EXTRAS = ("NOK", "DKK")
ORDER = MAJORS + EXTRAS

# ── Fetch ────────────────────────────────────────────────────────────────

_BAR_RE = re.compile(
    r'class="map-innercontainer[^"]*?((?:strong|weak)\d)[^"]*"'
    r'[^>]*style="([^"]*)"',
    re.DOTALL,
)
_CCY_BLOCK_RE = re.compile(
    r'<div\s+class="[^"]*map-container[^"]*"[^>]*>.*?'
    r'<span[^>]*class="[^"]*map-label[^"]*"[^>]*>(\w+)</span>'
    r'(.*?)</div>\s*</div>',
    re.DOTALL,
)


def _parse_scores(html: str) -> dict[str, float]:
    """Extract active bar counts from the livecharts HTML and convert to 0-10."""
    scores: dict[str, float] = {}
    for m in _CCY_BLOCK_RE.finditer(html):
        ccy = m.group(1).upper().strip()
        block = m.group(2)
        bars = _BAR_RE.findall(block)
        if not bars:
            continue
        active = sum(1 for _cls, style in bars if "none" not in style.lower())
        total = len(bars) if len(bars) > 0 else 6
        scores[ccy] = round(active / total * 10, 1)
    return scores


def fetch() -> dict[str, float]:
    """Fetch the livecharts page and return {ccy: score}."""
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="replace")
    scores = _parse_scores(html)
    if scores:
        DATA.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(scores, indent=2), encoding="utf-8")
    return scores


def load() -> dict[str, float]:
    """Return the latest scores — from cache if the live fetch fails."""
    try:
        return fetch()
    except Exception as e:  # noqa: BLE001
        print(f"  CSM live fetch failed ({type(e).__name__}: {e}), trying cache")
        if CACHE.exists():
            return json.loads(CACHE.read_text(encoding="utf-8"))
        return {}


# ── Render ───────────────────────────────────────────────────────────────

def _bar_color(score: float) -> str:
    """CSS colour for the strength bar — green for strong, red for weak."""
    if score >= 7.5:
        return "#16a34a"      # strong green
    if score >= 5.5:
        return "#65a30d"      # mild green
    if score >= 4.5:
        return "#ca8a04"      # neutral amber
    if score >= 2.5:
        return "#ea580c"      # mild red
    return "#dc2626"          # weak red


def _strength_label(score: float) -> str:
    if score >= 7.5:
        return "Strong"
    if score >= 5.5:
        return "Moderate"
    if score >= 4.5:
        return "Neutral"
    if score >= 2.5:
        return "Moderate"
    return "Weak"


def render(scores: dict[str, float]) -> str:
    """Return the HTML for the CSM panel."""
    if not scores:
        return ('<p style="padding:18px;opacity:.5">'
                'Currency strength data not available yet.</p>')

    # Sort by score descending
    ranked = sorted(
        ((c, scores.get(c, 0.0)) for c in ORDER if c in scores),
        key=lambda x: -x[1],
    )

    rows: list[str] = []
    for ccy, sc in ranked:
        bar_w = max(sc * 10, 2)  # percentage width, min 2% so something shows
        color = _bar_color(sc)
        label = _strength_label(sc)
        rows.append(
            f'<tr>'
            f'<td class="csm-ccy">{esc(ccy)}</td>'
            f'<td class="csm-bar-cell">'
            f'<div class="csm-bar" style="width:{bar_w:.0f}%;background:{color}">'
            f'</div></td>'
            f'<td class="csm-score" style="color:{color}">{sc:.1f}</td>'
            f'<td class="csm-label" style="color:{color}">{label}</td>'
            f'</tr>'
        )

    return (
        '<section class="csm-section">'
        '<h2>Currency Strength Meter</h2>'
        '<p class="sub">Numerical strength scores (0–10) derived from '
        '<a href="https://www.livecharts.co.uk/currency-strength.php" '
        'target="_blank" rel="noopener">livecharts.co.uk</a>. '
        'Higher = stronger currency.</p>'
        '<table class="csm-table">'
        '<thead><tr><th>Currency</th><th>Strength</th>'
        '<th>Score</th><th></th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</section>'
    )


# ── Standalone / inject ─────────────────────────────────────────────────

def csm_panel() -> str:
    """Full panel content for the dashboard (called by build_dashboard)."""
    scores = load()
    return render(scores)


def inject(html_path: str) -> None:
    """Replace {{CSM_PANEL}} in a dashboard file, or inject between markers."""
    path = Path(html_path)
    html = path.read_text(encoding="utf-8")
    if "{{CSM_PANEL}}" in html:
        html = html.replace("{{CSM_PANEL}}", csm_panel())
        path.write_text(html, encoding="utf-8")
        print(f"  CSM: injected into {path}")
    else:
        print(f"  CSM: no {{{{CSM_PANEL}}}} marker in {path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        inject(sys.argv[1])
    else:
        scores = load()
        if scores:
            ranked = sorted(scores.items(), key=lambda x: -x[1])
            for ccy, sc in ranked:
                print(f"  {ccy:>4s}  {sc:5.1f}")
        else:
            print("  no data")
