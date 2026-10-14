"""Verify a BUILT dashboard.html against the CFTC Legacy data it was built from.

    python check_build.py dashboard.html [--data data] [--require-history 200]

Run by the build-check workflow after a full pipeline run from an empty state, so the
first full build of a change happens in CI, not in production. It re-derives every figure
it checks from the raw stored CFTC rows with its own arithmetic - deliberately NOT by
calling cot_table.py's functions - so the page is not grading itself.

Checks, for the COT report tables and all 16 50-week reports:
  * summary tables: every cell of every row (long, short, total, %, net, difference,
    previous difference, change, % change - N/A on a zero denominator), the report date,
    USD/xxx reversal, contract shown, link to a report that exists;
  * every internal #link on the page resolves to an element id;
  * each report: one layer per market, 3 tables of the expected weeks (newest first, full
    dates), every value, Max/Min over the weeks shown and the 13-week average, the chart
    (one point per week per series), legend wording;
  * shading: every Long/Short cell's colour strength = (pct/100)^2 x max(0.15, 0.75^age) from an
    independent point-in-time percentile over 156 reports (52 minimum, mid-rank), and its
    hover/tap text carries the unfaded percentile;
  * history: with --require-history N, every market must have at least N weekly reports
    stored (proves the one-time backfill ran).
Exit status 1 on any failure; prints a summary either way.
"""
import argparse, os, html, json, math, re, sys
import datetime as dt
from pathlib import Path

REVERSED = {"090741", "092741", "097741"}              # CAD, CHF, JPY futures
RANK, MIN_RANK, GAMMA, FADE, FLOOR, SHOW, AVG = 156, 52, 2.0, 0.75, 0.15, 50, 13

FAILS, OKS = [], []


def ok(cond, what, detail=""):
    (OKS if cond else FAILS).append(what)
    if not cond:
        print(f"  FAIL {what}" + (f"\n       {detail}" if detail else ""))
    return cond


def half_up(x):
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def fmt(n):
    return f"{n:,}"


def cells(row_html):
    """Text of every <td>/<th> in one <tr>, tags stripped, entities decoded."""
    out = []
    for m in re.finditer(r"<t([dh])\b([^>]*)>(.*?)</t\1>", row_html, re.S):
        txt = re.sub(r'<span class="pp">.*?</span>', "", m.group(3), flags=re.S)
        txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
        out.append((m.group(2), txt))
    return out


def side(v, code):
    ncl, ncs, cl, cs = v[1], v[2], v[4], v[5]
    return (ncs, ncl, cs, cl) if code in REVERSED else (ncl, ncs, cl, cs)


def group(l, s):
    t = l + s
    if not t:
        return [fmt(l), fmt(s), fmt(t), "—", "—", fmt(l - s)]
    return [fmt(l), fmt(s), fmt(t), f"{half_up(l / t * 100)}%", f"{half_up(s / t * 100)}%", fmt(l - s)]


def pctl(window, x):
    return 100.0 * (sum(v < x for v in window) + 0.5 * sum(v == x for v in window)) / len(window)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"))
    ap.add_argument("--require-history", type=int, default=0)
    a = ap.parse_args()
    page = Path(a.page).read_text(encoding="utf-8")
    store = json.loads((Path(a.data) / "cot_legacy.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(Path(__file__).parent))
    from cot_table import ALL_ROWS                      # labels/codes/keys only - no maths
    C = store["contracts"]

    # ---- history depth (the backfill)
    for label, code, *_ in ALL_ROWS:
        n = len((C.get(code) or {}).get("weeks", {}))
        if a.require_history:
            ok(n >= a.require_history, f"history: {label} has {n} weekly reports stored",
               f"need >= {a.require_history}")
    for code, c in C.items():
        bad = [d for d, v in c["weeks"].items()
               if None not in v and (v[1] + v[3] + v[4] + v[6] != v[0] or v[2] + v[3] + v[5] + v[7] != v[0])]
        ok(not bad, f"stored rows of {code} satisfy CFTC's open-interest identity", bad[:3])

    # ---- latest complete week (>= 15 of 16 contracts), independent of the module
    have = {}
    for _, code, *_ in ALL_ROWS:
        for d in (C.get(code) or {}).get("weeks", {}):
            have[d] = have.get(d, 0) + 1
    latest = max(d for d, n in have.items() if n >= len(ALL_ROWS) - 1)
    pretty = lambda d: f"{dt.date.fromisoformat(d):%B} {dt.date.fromisoformat(d).day}, {dt.date.fromisoformat(d).year}"  # noqa: E731
    sec = re.search(r'<section class="crsec" data-cot-report="([0-9-]+)">(.*?)</section>', page, re.S)
    if not ok(sec is not None, "COT report tables section present"):
        return finish()
    S = sec.group(2)
    ok(sec.group(1) == latest, f"page marker data-cot-report={sec.group(1)} is the latest complete week {latest}")
    ok(S.count(pretty(latest)) >= 2, f"summary tables dated {pretty(latest)}")
    ok("Commitment of Traders Report for the Major Currency Pairs" in S
       and "Commitment of Traders Report for the Major Commodities &amp; Indexes" in S, "both table titles")

    # ---- summary rows
    rows = {}
    for m in re.finditer(r'<tr><td class="lbl">(.*?)</tr>', S, re.S):
        tds = cells("<td>" + m.group(1))
        rows[tds[0][1].rstrip("*").strip()] = (m.group(0), tds)
    ok(len(rows) == 16, f"16 summary rows (found {len(rows)})", list(rows))
    for label, code, rev, key, _desc in ALL_ROWS:
        got = rows.get(label)
        if not ok(got is not None, f"summary row {label} present"):
            continue
        raw, tds = got
        ok(f'href="#cot50-{key}"' in raw, f"summary row {label} links to its report")
        ok(f"({code})" in S, f"contract code {code} shown for {label}")
        w = C[code]["weeks"]
        if latest not in w:
            ok("not in this week" in raw, f"{label}: missing week shown as missing")
            continue
        nl, ns, cl, cs = side(w[latest], code)
        older = sorted(d for d in w if d < latest)
        diff = (nl - ns) - (cl - cs)
        exp = group(nl, ns) + group(cl, cs) + [fmt(diff)]
        if older:
            pl_, ps_, pcl, pcs = side(w[older[-1]], code)
            pdiff = (pl_ - ps_) - (pcl - pcs)
            chg = diff - pdiff
            exp += [fmt(pdiff), fmt(chg), "N/A" if pdiff == 0 else f"{half_up(chg / abs(pdiff) * 100)}%"]
        else:
            exp += ["—", "—", "—"]
        got_vals = [t for _, t in tds[1:]]
        ok(got_vals == exp, f"summary {label}: all 16 figures match CFTC", f"page {got_vals}\n       want {exp}")

    # ---- every internal link resolves
    ids = set(re.findall(r'\bid="([^"]+)"', page))
    links = set(re.findall(r'href="#([^"]+)"', page))
    missing = sorted(l for l in links if l not in ids)
    ok(not missing, f"all {len(links)} internal links resolve", missing[:10])

    # ---- reports
    layer_ids = re.findall(r'<div class="cotmodal[^"]*" id="(cot50-[^"]+)"', page)
    ok(len(layer_ids) == len(set(layer_ids)), "no duplicate report layers")
    for label, code, rev, key, _desc in ALL_ROWS:
        m = re.search(rf'<div class="cotmodal[^"]*" id="cot50-{re.escape(key)}">(.*?)(?=<div class="cotmodal|\Z)', page, re.S)
        if not ok(m is not None, f"report layer for {label} (cot50-{key})"):
            continue
        L = m.group(1)
        if not ok("CFTC Legacy report" in L, f"{label}: Legacy report inside its layer"):
            continue
        w = C[code]["weeks"]
        dates = sorted(d for d in w if d <= latest)
        shown = list(reversed(dates[-SHOW:]))
        tables = re.findall(r'<table class="cotrep cr7">(.*?)</table>', L, re.S)
        if not ok(len(tables) == 3, f"{label}: NC, Comm and Net Difference tables ({len(tables)})"):
            continue
        sides = [side(w[d], code) for d in dates]
        idx = {d: i for i, d in enumerate(dates)}
        for ti, (tname, a_, b_) in enumerate((("Non-Commercial", 0, 1), ("Commercial", 2, 3))):
            T = tables[ti]
            body = re.search(r"<tbody>(.*?)</tbody>", T, re.S).group(1)
            trs = re.findall(r"<tr>(.*?)</tr>", body, re.S)
            ok(len(trs) == len(shown), f"{label} {tname}: {len(shown)} weeks shown ({len(trs)})")
            bad_v, bad_c, bad_t = [], [], []
            for age, (d, tr) in enumerate(zip(shown, trs)):
                tds = cells(tr)
                lng, sht = sides[idx[d]][a_], sides[idx[d]][b_]
                exp = [pretty(d)] + group(lng, sht)
                if [re.sub(r"\s*\+\d+w gap$", "", t) for _, t in tds] != exp:
                    bad_v.append((d, [t for _, t in tds], exp))
                for col, k in ((1, a_), (2, b_)):
                    attrs = tds[col][0]
                    lo = max(0, idx[d] - RANK + 1)
                    win = [s[k] for s in sides[lo:idx[d] + 1]]
                    tip = html.unescape(re.search(r'title="([^"]*)"', attrs).group(1))
                    if len(win) < MIN_RANK or max(win) == min(win):
                        if "not ranked" not in tip:
                            bad_t.append((d, col, tip))
                        if "--a:" in attrs:
                            bad_c.append((d, col, "coloured but not ranked"))
                        continue
                    p = pctl(win, sides[idx[d]][k])
                    want = (p / 100) ** GAMMA * max(FLOOR, FADE ** age)
                    got = re.search(r"--a:([0-9.]+)", attrs)
                    got = float(got.group(1)) if got else 0.0
                    if abs(got - want) > 0.0015 and not (want < 0.005 and got == 0.0):
                        bad_c.append((d, col, round(got, 4), round(want, 4)))
                    n = min(99, max(1, half_up(p)))
                    suf = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
                    if f"{n}{suf} percentile of its last {len(win)} reports" not in tip:
                        bad_t.append((d, col, tip))
                    if ("xl" in attrs) != (col == 1) or ("xs" in attrs) != (col == 2):
                        bad_c.append((d, col, "wrong hue"))
            ok(not bad_v, f"{label} {tname}: every weekly figure matches CFTC", bad_v[:2])
            ok(not bad_c, f"{label} {tname}: colour = (pct/100)^2 x max(0.15, 0.75^age) on every Long/Short cell", bad_c[:3])
            ok(not bad_t, f"{label} {tname}: hover/tap text carries the true percentile", bad_t[:2])
            # reference rows
            vals = [group(sides[idx[d]][a_], sides[idx[d]][b_]) for d in shown]
            num = lambda s: int(s.replace(",", ""))                       # noqa: E731
            cols = [[num(v[i]) for v in vals] for i in (0, 1, 2, 5)]
            rec = [[num(v[i]) for v in vals[:AVG]] for i in (0, 1, 2, 5)]
            avg = [half_up(sum(c) / len(c)) for c in rec]
            want_rows = {
                "mx": [fmt(max(c)) for c in cols],
                "mn": [fmt(min(c)) for c in cols],
                "av": [fmt(avg[0]), fmt(avg[1]), fmt(avg[2]), f"{half_up(avg[0] / avg[2] * 100)}%",
                       f"{half_up(avg[1] / avg[2] * 100)}%", fmt(avg[3])],
            }
            for cls, want in want_rows.items():
                r = re.search(rf'<tr class="ref {cls}">(.*?)</tr>', T, re.S)
                got = [t for _, t in cells(r.group(1))][1:] if r else []
                got = [g for g in got if g] if cls != "av" else got
                ok(got == want, f"{label} {tname}: {cls} reference row", f"page {got} want {want}")
            ok(f"Max. ({len(shown)} wks)" in T and f"{min(AVG, len(shown))}-wk Avg" in T,
               f"{label} {tname}: reference rows labelled with their periods")
        # chart
        polys = re.findall(r'<polyline class="(ncl|cml)" points="([^"]*)"', L)
        ok(len(polys) == 2 and all(len(p.split()) == len(shown) for _, p in polys),
           f"{label}: chart has both series, one point per week")
        flat = " ".join(L.split())
        ok("<b>Red</b> = unusually high long positioning" in flat and "<b>not</b> buy/sell signals" in flat
           and "The <b>Max / Min</b> rows cover the 50 reports shown" in flat, f"{label}: legend")
    check_myfxbook(page, Path(a.data))
    return finish()


# ---------------------------------------------------------------- Myfxbook retail sentiment
# Re-derived here from data/myfxbook.json with its own arithmetic, not by calling
# myfxbook.py's helpers: imbalance = |L - S| (half-up), the five crowd bands, and that every
# figure on the page is the stored one - nothing zeroed, estimated or borrowed.
MFX_MIN_POSITIONS = 100      # the page's minimum sample (long + short positions)
MFX_BANDS = [(lambda l, s: l >= 70, "Heavy Long"), (lambda l, s: s >= 70, "Heavy Short"),
             (lambda l, s: l >= 60, "Long"), (lambda l, s: s >= 60, "Short"),
             (lambda l, s: True, "Balanced")]


def _pc(v):
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


def check_myfxbook(page, data_dir):
    m = re.search(r'<div class="panel" id="p-rtl">(.*?)<div class="panel" id="p-oi">', page, re.S)
    if os.environ.get("MYFXBOOK_ENABLED") != "true":
        # the switch is off: the tab says so and NO Myfxbook figure is anywhere on the page,
        # not even from a cached snapshot (MYFXBOOK_ENABLED must be exactly "true")
        ok(m is not None and "switched off" in m.group(1), "Myfxbook switched off: tab says so")
        ok(not re.search(r'class="mfx(row|card|bar|meta)|data-mfx-(key|ts)|Retail crowd:', page),
           "Myfxbook switched off: no reading, card or timestamp anywhere on the page")
        return
    p = data_dir / "myfxbook.json"
    snap = json.loads(p.read_text()) if p.exists() else {}
    m = re.search(r'<div class="panel" id="p-rtl">(.*?)<div class="panel" id="p-oi">', page, re.S)
    ok(m is not None, "Myfxbook retail tab present (after COT)")
    if not m:
        return
    tab = m.group(1)
    ok("Retail sentiment &mdash; Myfxbook" in tab and 'href="https://www.myfxbook.com/community/outlook"' in tab
       and "Retail sentiment data:" in tab, "Myfxbook: section title and source link")
    layers = dict(re.findall(r'<div class="cotmodal[^"]*" id="cot50-([^"]+)">(.*?)(?=<div class="cotmodal|\Z)', page, re.S))
    if not snap.get("fetched_at"):
        ok("not configured yet" in tab or "No Myfxbook data yet" in tab, "Myfxbook: no data yet - page says so",
           snap.get("status"))
        cards = [k for k, L in layers.items() if "Myfxbook retail" in L and "No Myfxbook data yet" in L]
        ok(len(cards) >= 16, f"Myfxbook: every report card says no data yet ({len(cards)})")
        return
    link = 'Retail sentiment data: <a href="https://www.myfxbook.com/community/outlook" target="_blank" rel="noopener">Myfxbook Community Outlook</a>'
    cards = re.findall(r'<div class="mfxcard">.*?</p></div>', page, re.S)
    ok(link in tab, "Myfxbook: tab credits Myfxbook Community Outlook with a link")
    ok(len(cards) >= 16 and all(link in c for c in cards),
       f"Myfxbook: every report card credits Myfxbook Community Outlook with a link ({sum(link in c for c in cards)}/{len(cards)})")
    markets = snap.get("markets") or {}
    rows = dict(re.findall(r'<div class="mfxrow[^"]*" data-mfx-key="([^"]+)">(.*?)(?=<div class="mfxrow|</div>\s*<h3|</div>\s*<p class="mfxkey")', tab, re.S))
    ok(len(rows) == 16, f"Myfxbook: 16 markets listed ({len(rows)})")
    ts = snap["fetched_at"]
    ok(f'data-mfx-ts="{ts}"' in tab, "Myfxbook: tab shows the stored timestamp")
    for key, r in rows.items():
        d = markets.get(key)
        L = layers.get(key, "")
        ok("Myfxbook retail" in L, f"Myfxbook: {key} report has the retail card")
        if not d:
            ok("Retail sentiment unavailable" in r, f"Myfxbook: {key} has no data and says so")
            ok("Retail sentiment unavailable" in L, f"Myfxbook: {key} report card says unavailable")
            continue
        n = d["longPositions"] + d["shortPositions"]
        if n < MFX_MIN_POSITIONS:
            want = f"Too few Myfxbook positions (n = {n:,}) &mdash; no reading"
            # no figure in the message: this log is public
            card = re.search(r'<div class="mfxcard">(.*?)</p></div>', L, re.S)
            ok(want in r and card is not None and want in card.group(1),
               f"Myfxbook: {key} below {MFX_MIN_POSITIONS} positions - says so, tab and report")
            ok(card is not None and not re.search(r"LONG \d|SHORT \d|Imbalance \d|Retail crowd|mfxbar", r + card.group(1)),
               f"Myfxbook: {key} below {MFX_MIN_POSITIONS} - no %, bar, imbalance or crowd")
            continue
        lp, sp = d["longPercentage"], d["shortPercentage"]
        imb = int(abs(lp - sp) + 0.5)
        band = next(n for f, n in MFX_BANDS if f(lp, sp))
        ok(f"LONG {_pc(lp)}%" in r and f"SHORT {_pc(sp)}%" in r, f"Myfxbook: {key} long/short % as stored")
        ok(f"Imbalance {imb}<" in r and f"Retail crowd: <b>{band}</b>" in r, f"Myfxbook: {key} imbalance {imb}, crowd {band}")
        name = re.search(r'class="mfxname">([^<]*)<', r)
        same = name and name.group(1).replace("&amp;", "&").upper() == d["symbol"]
        ok(same or f">{d['symbol']}<" in r,
           f"Myfxbook: {key} names the Myfxbook symbol used ({d['symbol']}{', = market label' if same else ''})")
        ok(f"width:{lp:.4g}%" in r and f"width:{sp:.4g}%" in r, f"Myfxbook: {key} bar widths match the %")
        vols = f"L {d['longVolume']:,.2f} &middot; S {d['shortVolume']:,.2f}"
        pos = f"L {d['longPositions']:,} &middot; S {d['shortPositions']:,}"
        ok(vols in r and pos in r, f"Myfxbook: {key} volume and positions as stored")
        ok(f"Long {_pc(lp)}% &middot; Short {_pc(sp)}% &middot; Imbalance {imb}" in L
           and f"Retail crowd: <b>{band}</b>" in L, f"Myfxbook: {key} report card matches")


def finish():
    print(f"\ncheck_build: {len(OKS)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
