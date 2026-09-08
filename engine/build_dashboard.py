"""Render scores.json into a self-contained dashboard HTML file."""
import json, datetime as dt
from pathlib import Path
from config import (DATA, ORDER, CURRENCIES, WEIGHTS,
                    COMMODITIES, COMMODITY_ORDER, COMMODITY_WEIGHTS, ordinal)
try:
    from config import COT_EXTRA, COT_EXTRA_ORDER
except ImportError:
    COT_EXTRA, COT_EXTRA_ORDER = {}, []
try:
    from config import INDICES, INDEX_ORDER
except ImportError:                                   # older config - the Indices tab hides itself
    INDICES, INDEX_ORDER = {}, []
from template import TEMPLATE

OUT = Path(__file__).parent / "dashboard.html"
PART_LABEL = {"fundamentals": "Checklist", "expectations": "Rate odds",
              "cot": "COT", "oi": "Open interest", "news": "News"}
CPART_LABEL = {"cot": "COT", "oi": "Open interest",
               "trend": "Trend", "overlay": "Overlay"}


def esc(s):
    if s is None or s == "":
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def dash(s):
    """Escaped value, or an em-dash when there is genuinely no number."""
    return esc(s) or "&mdash;"


def fmt_when(iso, now):
    t = dt.datetime.fromisoformat(iso)
    delta = (t - now).total_seconds()
    if delta > 0:
        h, m = divmod(int(delta // 60), 60)
        rel = f"in {h}h {m:02d}m" if h else f"in {m}m"
    else:
        h, m = divmod(int(-delta // 60), 60)
        rel = f"{h}h ago" if h else f"{m}m ago"
    return t.strftime("%a %d %b %H:%M UTC"), rel


def bar(score, w=100):
    """Diverging bar: half-width track, fill grows left or right of centre."""
    pct = max(-100.0, min(100.0, score)) / 100 * 50
    cls = "pos" if score >= 0 else "neg"
    if score >= 0:
        style = f"left:50%;width:{pct:.2f}%"
    else:
        style = f"left:{50 + pct:.2f}%;width:{-pct:.2f}%"
    return f'<span class="bar"><span class="fill {cls}" style="{style}"></span></span>'


def read_chip(read):
    """Small chip for the directional / retracement read. Nothing for a flat or edgeless read."""
    if not read:
        return ""
    st = read.get("state")
    if st == "retracement":
        return (f' <span class="chip warn" title="{esc(read.get("label",""))}">'
                f'poss. retracement</span>')
    if st == "directional":
        return (f' <span class="chip ok" title="{esc(read.get("label",""))}">on&nbsp;trend</span>')
    return ""


def cot_chip(cx):
    """Chip for a speculative-positioning extreme / turn / proven level. Nothing when mid-range."""
    if not cx or not cx.get("state"):
        return ""
    st = cx["state"]
    if st in ("long unwinding", "short covering"):
        return f' <span class="chip neg" title="{esc(cx["note"])}">COT&nbsp;turning</span>'
    if st in ("at long ceiling", "at short floor"):
        return f' <span class="chip warn" title="{esc(cx["note"])}">COT&nbsp;level</span>'
    return f' <span class="chip warn" title="{esc(cx["note"])}">COT&nbsp;extreme</span>'


def _levels_line(cx):
    """Muted context line listing the proven recurring reversal levels, or '' if none."""
    if not cx:
        return ""
    def fmt(lst, label):
        return ", ".join(f'{label} {L["level"]:+,} ({L["touches"]}&times;)' for L in lst[:2])
    parts = [s for s in (fmt(cx.get("ceilings") or [], "ceiling"),
                         fmt(cx.get("floors") or [], "floor")) if s]
    if not parts:
        return ""
    return f'<p class="mnote mut">Proven reversal levels: {" &middot; ".join(parts)}.</p>'


def cotx_line(cx, adj=0.0):
    """The positioning-extreme sentence for a card, plus the proven-levels context line.
    `adj` is the contrarian pull the flag put on the score (0 when nothing fired)."""
    if not cx:
        return ""
    levels = _levels_line(cx)
    if not cx.get("note"):
        p1, p3 = cx.get("pctl_1y"), cx.get("pctl_3y")
        if p1 is None:
            return levels
        yrs = round(cx.get("hist_weeks", 0) / 52)
        return (f'<p class="mut">Speculative net: {ordinal(p1)} percentile over 1 year, '
                f'{ordinal(p3)} over 3, {ordinal(cx.get("pctl_max"))} over {yrs} — mid-range.</p>'
                + levels)
    turn = cx["state"] in ("long unwinding", "short covering")
    pull = f' <b>Score pulled {adj:+.1f}</b> toward reversal.' if adj else ""
    return (f'<p class="mnote {"neg" if turn else "warn"}"><b>Positioning:</b> '
            f'{esc(cx["note"])}.{pull}</p>' + levels)


def cot_pull_row(r):
    """Breakdown row for the COT-extreme contrarian pull. '' when the flag did not fire.
    It is not a weighted leg - it is an additive shove applied after the blend - so it
    shows no weight, just the points it moved the score."""
    adj = r.get("cot_adj") or 0.0
    if not adj:
        return ""
    cx = r.get("cot_x") or {}
    st = cx.get("state")
    lvl = cx.get("level")
    if st in ("long unwinding", "short covering"):
        tag = "turning"
    elif lvl and st in ("at long ceiling", "at short floor"):
        tag = f"level &times;{lvl['touches']}"
    else:
        tag = "flag"
    sign = "pos" if adj >= 0 else "neg"
    tip = esc(cx.get("note", "") or "speculative positioning at an extreme")
    return (f'<div class="crow"><div class="clab">COT extreme'
            f'<span class="cw">{tag}</span></div>'
            f'{bar(adj)}'
            f'<div class="cval mut" title="{tip}">pull</div>'
            f'<div class="ccon {sign}">{adj:+.1f}</div></div>')


def centring_row(r):
    """FX cards only: the board-centring offset, so the rows + the COT pull + this line add
    up exactly to the headline score. FX strength is relative - the seven scores are shifted
    so the board averages zero; commodities are not centred and get no row."""
    raw = r.get("raw_score")
    if raw is None:
        return ""
    delta = round(r["score"] - raw, 1)
    if delta == 0:
        return ""
    return (f'<div class="crow"><div class="clab">Centring<span class="cw">board avg &rarr; 0</span></div>'
            f'{bar(delta)}<div class="cval mut">&mdash;</div>'
            f'<div class="ccon mut">{delta:+.1f}</div></div>')


def _fmt_d(iso):
    try:
        return dt.datetime.fromisoformat(iso).strftime("%d %b")
    except Exception:
        return ""


def retr_line(retr, kind, name):
    """One line describing the Fibonacci dip / rally zone, or '' when there is no clean leg.
    Commodity levels are real prices; FX levels are shown as % of the currency's strength."""
    if not retr:
        return ""
    dip = retr["word"] == "dip-buy"
    w = "dip-buy zone" if dip else "rally-sell zone"
    n, f = retr["band_near_pct"], retr["band_far_pct"]
    ma_p = retr.get("ma20_pct", 0.0)
    prec = 1 if retr["swing_hi"] < 200 else 0
    if kind == "commodity":
        levels = f"{retr['f382']:.{prec}f}–{retr['f618']:.{prec}f}"
        ma = f"20-day avg {retr['ma20']:.{prec}f} ({ma_p:+.1f}%)"
        lo_d, hi_d = _fmt_d(retr.get("swing_lo_at")), _fmt_d(retr.get("swing_hi_at"))
        leg = (f"{retr['swing_lo']:.{prec}f}"
               + (f" ({lo_d})" if lo_d else "") + f" → {retr['swing_hi']:.{prec}f}"
               + (f" ({hi_d})" if hi_d else "") + " leg")
    else:
        levels = f"{n:+.1f}% to {f:+.1f}% of {name} strength"
        ma = f"20-day mean {ma_p:+.1f}%"
        hi_d = _fmt_d(retr.get("swing_hi_at")) if dip else _fmt_d(retr.get("swing_lo_at"))
        leg = "current up-leg" if dip else "current down-leg"
        if hi_d:
            leg += f" (extreme {hi_d})"

    zone = f"{levels} ({n:+.1f}% to {f:+.1f}%)" if kind == "commodity" else levels
    st = retr["state"]
    if st == "no pullback yet":
        return (f'<p class="mnote"><b>No pullback yet.</b> First support {ma}; a 38.2–61.8% '
                f'retrace of the {leg} sits at {zone}.</p>')
    if st == "in zone":
        return (f'<p class="mnote"><b>{w}: price is in it now</b> ({zone}), '
                f'{retr["retraced_pct"]}% retraced &middot; {ma}.</p>')
    if st == "overshot":
        return (f'<p class="mnote"><b>Retraced {retr["retraced_pct"]}% — past 61.8%.</b> '
                f'The {leg} may be failing; {ma}.</p>')
    return (f'<p class="mnote"><b>{w}:</b> {zone} &middot; {ma} &middot; '
            f'{retr["retraced_pct"]}% retraced of the {leg}.</p>')


def _load_hist(name):
    """A date-keyed COT history file, or {} if missing / unreadable."""
    p = DATA / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sgn(n):
    return f"{n:+,}" if isinstance(n, (int, float)) else "&mdash;"


def _wk(d):
    try:
        return dt.date.fromisoformat(d).strftime("%d %b")
    except Exception:
        return esc(d)


# which trader category is "the speculators" in each report
FX_SPEC, CM_SPEC = "leveraged", "managed_money"


def cot_panel():
    """The COT tab: latest-week speculative net + spreading for every contract, then a
    10-week history card per instrument. FX speculators = CFTC Leveraged Funds; commodity
    speculators = Managed Money (the disaggregated report). Read straight from the weekly
    history files - no scoring, just the raw positioning."""
    fx = _load_hist("cot_history.json")
    cm = _load_hist("cot_history_commodity.json")
    if not fx:
        return ('<section><h2>COT report</h2>'
                '<p class="sub">No positioning history on file yet.</p></section>')

    wfx = sorted(fx)[-10:]
    wcm = sorted(cm)[-10:]
    asof = wfx[-1]

    crypto = [s for s in COT_EXTRA_ORDER if any(s in fx.get(w, {}) for w in wfx)]
    items = ([(c, CURRENCIES[c]["name"], fx, wfx, FX_SPEC) for c in ORDER]
             + [(s, COT_EXTRA[s]["name"], fx, wfx, FX_SPEC) for s in crypto]
             + [(s, COMMODITIES[s]["name"], cm, wcm, CM_SPEC) for s in COMMODITY_ORDER])
    sep_before = {}
    if crypto:
        sep_before[crypto[0]] = "Crypto &mdash; CME futures, Leveraged Funds"
    if COMMODITY_ORDER:
        sep_before[COMMODITY_ORDER[0]] = "Commodities &mdash; Managed Money"

    # ---- latest-week summary table
    srows = []
    for code, name, hist, weeks, cat in items:
        if code in sep_before:
            srows.append(f'<tr><td colspan="8" class="mut" style="font-size:11px;'
                         f'text-transform:uppercase;letter-spacing:.1em">{sep_before[code]}</td></tr>')
        rec = hist.get(weeks[-1], {}).get(code) or {}
        g = rec.get(cat) or {}
        net = g.get("net")
        if net is None:
            continue
        oi = rec.get("open_interest") or 0
        pctoi = f"{net / oi * 100:+.1f}%" if oi else "&mdash;"
        sp = g.get("spread")
        sp_txt = f"{sp:,}" if isinstance(sp, int) else "&mdash;"
        spc_txt = _sgn(g.get("spread_chg")) if isinstance(sp, int) else "&mdash;"
        srows.append(
            f'<tr><td class="pr">{code}<span class="cnm">{esc(name)}</span></td>'
            f'<td class="num mono {"pos" if net >= 0 else "neg"}">{net:+,}</td>'
            f'<td class="num mono {"pos" if (g.get("net_chg") or 0) >= 0 else "neg"}">{_sgn(g.get("net_chg"))}</td>'
            f'<td class="num mono">{g.get("long", 0):,}</td>'
            f'<td class="num mono">{g.get("short", 0):,}</td>'
            f'<td class="num mono">{sp_txt}</td>'
            f'<td class="num mono mut">{spc_txt}</td>'
            f'<td class="num mono">{pctoi}</td></tr>')

    # ---- per-instrument 10-week history
    cards = []
    for code, name, hist, weeks, cat in items:
        rows = []
        for wk in reversed(weeks):
            g = (hist.get(wk, {}).get(code) or {}).get(cat) or {}
            if "net" not in g:
                continue
            sp = g.get("spread")
            rows.append(
                f'<tr><td class="mono">{_wk(wk)}</td>'
                f'<td class="num mono">{g.get("long", 0):,}</td>'
                f'<td class="num mono">{g.get("short", 0):,}</td>'
                f'<td class="num mono {"pos" if g["net"] >= 0 else "neg"}">{g["net"]:+,}</td>'
                f'<td class="num mono mut">{_sgn(g.get("net_chg"))}</td>'
                f'<td class="num mono">{f"{sp:,}" if isinstance(sp, int) else "&mdash;"}</td>'
                '</tr>')
        if not rows:
            continue
        cur = (hist.get(weeks[-1], {}).get(code) or {}).get(cat, {}).get("net")
        cards.append(
            f'<div class="cotcard"><h3>{code} <span class="cnm">{esc(name)}</span>'
            f'<span class="mono {"pos" if (cur or 0) >= 0 else "neg"}">{_sgn(cur)}</span></h3>'
            f'<div class="tw"><table><thead><tr><th>Week</th><th class="num">Long</th>'
            f'<th class="num">Short</th><th class="num">Net</th><th class="num">&Delta;</th>'
            f'<th class="num">Spr</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></div>')

    return f"""<section>
    <h2>COT report <span class="mut" style="font-weight:400;font-size:14px">&mdash; speculative positioning, weekly</span></h2>
    <p class="sub">Straight from the CFTC Commitments of Traders, week ending <b>{asof}</b>.
    <b>Net</b> is speculative long minus short &mdash; Leveraged Funds for currencies, Managed
    Money for gold/silver/crude &mdash; the directional, non-commercial money.
    <b>Spread</b> is the spreading position (contracts held both long and short, i.e.
    calendar / relative-value trades that carry no outright direction). <b>% OI</b> is the net
    as a share of total open interest &mdash; above 35% is a crowded, squeeze-prone book.</p>
    <div class="tw"><table>
      <thead><tr><th>Contract</th><th class="num">Net</th><th class="num">&Delta; wk</th>
      <th class="num">Long</th><th class="num">Short</th><th class="num">Spread</th>
      <th class="num">&Delta; spr</th><th class="num">% OI</th></tr></thead>
      <tbody>{"".join(srows)}</tbody>
    </table></div>
    <h2 style="margin-top:10px">Last 10 weeks</h2>
    <p class="sub">Non-commercial long / short / net and the weekly change, newest first.</p>
    <div class="cotgrid">{"".join(cards)}</div>
  </section>"""


def _oi_weekly_fallback():
    """Weekly OI straight from the COT report - used only when the daily preliminary feed
    (oi_history.json via oi.py) has nothing."""
    fx = _load_hist("cot_history.json")
    cm = _load_hist("cot_history_commodity.json")
    if not fx:
        return ('<section><h2>Open interest</h2>'
                '<p class="sub">No open-interest history on file yet.</p></section>')
    wfx, wcm = sorted(fx)[-10:], sorted(cm)[-10:]
    items = ([(c, CURRENCIES[c]["name"], fx, wfx) for c in ORDER]
             + [(s, COMMODITIES[s]["name"], cm, wcm) for s in COMMODITY_ORDER])
    cards = []
    for code, name, hist, weeks in items:
        cur = (hist.get(weeks[-1], {}).get(code) or {}).get("open_interest")
        if not cur:
            continue
        rows = []
        for w in reversed(weeks):
            r = hist.get(w, {}).get(code) or {}
            v = r.get("open_interest")
            if not v:
                continue
            rows.append(f'<tr><td class="mono">{_wk(w)}</td><td class="num mono">{v:,}</td>'
                        f'<td class="num mono {"pos" if (r.get("oi_change") or 0) >= 0 else "neg"}">'
                        f'{_sgn(r.get("oi_change"))}</td></tr>')
        cards.append(f'<div class="cotcard"><h3>{code} <span class="cnm">{esc(name)}</span>'
                     f'<span class="mono">{cur:,}</span></h3><div class="tw"><table><thead><tr>'
                     f'<th>Week</th><th class="num">OI</th><th class="num">&Delta;</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></div></div>')
    return f"""<section>
    <h2>Open interest <span class="mut" style="font-weight:400;font-size:14px">&mdash; weekly (COT report)</span></h2>
    <p class="sub">The daily preliminary feed has nothing on file, so this is the weekly open
    interest from the CFTC report, last 10 weeks, newest first.</p>
    <div class="cotgrid">{"".join(cards)}</div>
  </section>"""


def oi_panel():
    """The Open Interest tab: CME **preliminary** open interest, daily, last ~10 trading days
    for gold / silver / crude and the FX majors. Built from oi.py's own daily history
    (oi_history.json) - preliminary numbers are frozen once stored and never revised to the
    settled figure, because the preliminary read is the one available in time to set the
    day's bias. Falls back to the weekly COT open interest if the daily feed is empty."""
    try:
        hist = json.loads((DATA / "oi_history.json").read_text(encoding="utf-8"))
    except Exception:
        hist = {}
    if not hist:
        return _oi_weekly_fallback()

    try:
        import oi as _oi
        try:
            prices = _oi.price_moves()
        except Exception:
            prices = {}
        tables = _oi.history_tables(hist, prices)
        # each daily table is a bare <table class='oi-tw'> - wrap so it scrolls on a phone
        tables = (tables.replace("<table class='oi-tw'>", "<div class='tw'><table class='oi-tw'>")
                        .replace("</table>", "</table></div>"))
        css = _oi.CSS
        order = _oi.ORDER
    except Exception:
        tables, css, order = "", "", ["Gold", "Silver", "Oil", "EUR", "GBP", "JPY",
                                      "AUD", "NZD", "CAD", "CHF"]

    days = sorted(hist)
    asof = days[-1]                        # ISO - also the key into `hist`, keep it that way
    try:                                   # name the weekday: a bare date is easy to misread
        asof_label = dt.date.fromisoformat(asof).strftime("%a %d %b %Y")
    except Exception:                                          # noqa: BLE001
        asof_label = asof
    latest = hist[asof]

    srows = []
    for inst in order:
        d = latest.get(inst)
        if not d or d.get("oi") is None:
            continue
        chg = d.get("chg") or 0
        prev = d["oi"] - chg
        pct = f"{chg / prev * 100:+.2f}%" if prev else "&mdash;"
        vol = f'{d["volume"]:,}' if d.get("volume") else "&mdash;"
        srows.append(
            f'<tr><td class="pr">{esc(inst)}</td>'
            f'<td class="num mono">{d["oi"]:,}</td>'
            f'<td class="num mono {"pos" if chg >= 0 else "neg"}">{_sgn(chg)}</td>'
            f'<td class="num mono {"pos" if chg >= 0 else "neg"}">{pct}</td>'
            f'<td class="num mono mut">{vol}</td></tr>')

    if not tables and not srows:
        return _oi_weekly_fallback()

    return f"""<section>
    {css}
    <h2>Open interest <span class="mut" style="font-weight:400;font-size:14px">&mdash; CME preliminary, daily</span></h2>
    <p class="sub">CME <b>preliminary</b> open interest for the previous trade date, published
    overnight &mdash; latest <b>{asof_label}</b>. This is the figure available in time to set the
    day's bias; CME reissues each day later as a settled number, and this desk keeps the
    <b>preliminary</b> read frozen rather than revising it. Contract volume for the day is
    shown alongside.</p>
    <div class="tw"><table>
      <thead><tr><th>Contract</th><th class="num">Open interest</th><th class="num">&Delta; day</th>
      <th class="num">&Delta; %</th><th class="num">Volume</th></tr></thead>
      <tbody>{"".join(srows)}</tbody>
    </table></div>
    {tables}
  </section>"""


def _pct_bar(pct, cls="pos"):
    """Left-anchored 0-100% fill (the diverging `bar()` is centred on 50, wrong here)."""
    w = max(0.0, min(100.0, float(pct)))
    return (f'<span class="pbar"><span class="pfill {cls}" style="width:{w:.1f}%"></span>'
            f'</span>')


def fedwatch_panel():
    """The Fed Watch tab, laid out the way CME's own tool is: a target-rate probability
    chart for the next meeting, then the meeting-by-meeting probability grid. See
    fedwatch.py for the maths and why this is computed rather than scraped."""
    try:
        d = json.loads((DATA / "fedwatch.json").read_text(encoding="utf-8"))
    except Exception:
        return ('<section><h2>Fed Watch</h2><p class="sub">Fed funds futures have not been '
                'read yet - the panel fills in on the next refresh.</p></section>')
    rows = d.get("meetings") or []
    if not rows:
        return ('<section><h2>Fed Watch</h2><p class="sub">No upcoming FOMC meetings on '
                'file right now.</p></section>')

    lines = "".join(f"<p>{esc(l)}</p>" for l in (d.get("explainer") or []))
    moved = [m for m in rows if m.get("hike_delta") is not None and abs(m["hike_delta"]) >= 3]
    flag = ""
    if moved:
        m = moved[0]
        up = m["hike_delta"] > 0
        flag = (f'<p class="mnote {"neg" if up else "pos"}"><b>Changed:</b> '
                f'{esc(m["label"])} odds of a rise moved '
                f'{"up" if up else "down"} {abs(m["hike_delta"]):.0f} points '
                f'(from {m["hike_prev"]:.0f}% to {m["hike"]:.0f}%) since the last check.</p>')

    # ---- chart: target-rate probabilities for the NEXT meeting (CME's headline view)
    nxt = rows[0]
    buckets = nxt.get("buckets") or []
    top = max((b["prob"] for b in buckets), default=0) or 1
    cols = "".join(
        f'<div class="fwcol{" hi" if b["prob"] >= top - 0.01 else ""}">'
        f'<span class="fwpct">{b["prob"]:.1f}%</span>'
        f'<span class="fwtrack"><span class="fwbar" '
        f'style="height:{max(2, b["prob"] / top * 100):.1f}%"></span></span>'
        f'<span class="fwrange">{b["low"]:.2f}&ndash;{b["high"]:.2f}</span></div>'
        for b in buckets)
    chart = (f'<div class="dblock"><h4>Target rate probabilities '
             f'<span class="mut">{esc(nxt["label"])} meeting</span></h4>'
             f'<div class="fwchart">{cols}</div></div>') if buckets else ""

    # ---- grid: every meeting against every target range that has any weight
    steps = sorted({b["step"] for m in rows for b in (m.get("buckets") or [])})
    labels = {}
    for m in rows:
        for b in (m.get("buckets") or []):
            labels[b["step"]] = f'{b["low"]:.2f}&ndash;{b["high"]:.2f}'
    head = "".join(f'<th class="num">{labels[k]}</th>' for k in steps)
    grid = []
    for m in rows:
        by = {b["step"]: b["prob"] for b in (m.get("buckets") or [])}
        best = max(by.values(), default=0)
        cells = "".join(
            (f'<td class="num mono{" fwtop" if by.get(k, 0) >= best - 0.01 and by.get(k, 0) > 0 else ""}">'
             f'{by[k]:.1f}%</td>') if k in by else '<td class="num mut">&mdash;</td>'
            for k in steps)
        dlt = m.get("hike_delta")
        if dlt is None:
            chip = '<span class="mut">&mdash;</span>'
        elif abs(dlt) < 0.5:
            chip = '<span class="mut">no change</span>'
        else:
            chip = f'<span class="chip {"neg" if dlt > 0 else "ok"}">{dlt:+.0f} pts</span>'
        is_next = m is rows[0]
        nxt_chip = ' <span class="chip warn">next</span>' if is_next else ''
        tr_cls = ' class="next"' if is_next else ''
        grid.append(f'<tr{tr_cls}><td class="pr">{esc(m["label"])}{nxt_chip}</td>'
                    f'{cells}<td>{chip}</td></tr>')

    tgt = (f'{d["target_low"]:.2f}&ndash;{d["target_high"]:.2f}%'
           if d.get("target_low") is not None else "&mdash;")
    return f"""<section>
    <h2>Fed Watch <span class="mut" style="font-weight:400;font-size:14px">&mdash; what the market expects the Fed to do</span></h2>
    <div class="fwlead">{lines}{flag}</div>
    <p class="sub">Where the market thinks the fed funds target will sit after each meeting.
    Rate now <b>{d.get('effr', 0):.2f}%</b>, target {tgt}. Read straight out of 30-day fed
    funds futures &mdash; the same contracts and the same arithmetic CME's FedWatch uses.</p>
    {chart}
    <div class="tw"><table>
      <thead><tr><th>Meeting</th>{head}<th>Since last check</th></tr></thead>
      <tbody>{"".join(grid)}</tbody>
    </table></div>
    <p class="mnote mut">Each column is a target range; each row adds to 100%. Computed from
    CME 30-Day Fed Funds futures (via Yahoo) against the New York Fed's effective rate,
    chained meeting by meeting. CME's own page cannot be read automatically &mdash; it
    renders inside a session-bound widget &mdash; so this rebuilds the figure from the same
    public inputs; it matched CME to 0.1 of a point when checked.</p>
  </section>"""


def _pages_mod():
    try:
        import pages
        return pages
    except Exception as e:                                       # noqa: BLE001
        print(f"  pages.py unavailable ({type(e).__name__}: {e}) - macro/micro tabs empty")
        return None


def macro_panel():
    """The Macro tab - pages.py's plain-language 'what is driving markets, which way does it
    lean' read, one line per currency and commodity."""
    p = _pages_mod()
    if not p:
        return '<section><h2>Brief</h2><p class="sub">Macro read unavailable.</p></section>'
    try:
        body = p.macro_block()
    except Exception as e:                                       # noqa: BLE001
        return f'<section><h2>Brief</h2><p class="sub">Macro read failed: {esc(e)}</p></section>'
    return (f'<section>{p.CSS}<h2>Brief <span class="mut" style="font-weight:400;font-size:14px">'
            f'&mdash; the big picture</span></h2>'
            f'<div class="pg-wrap" style="margin-top:4px">{body}</div>{p.JS}</section>')


def micro_panel():
    """The Micro tab - the breaking-news feed, each story decoded to plain words with a lean."""
    p = _pages_mod()
    if not p:
        return '<section><h2>News</h2><p class="sub">News feed unavailable.</p></section>'
    try:
        feed = json.loads((DATA / "commodity_feed.json").read_text(encoding="utf-8"))
    except Exception:
        feed = []
    try:
        body = p.cw.render_block(feed)
    except Exception as e:                                       # noqa: BLE001
        return f'<section><h2>News</h2><p class="sub">News feed failed: {esc(e)}</p></section>'
    # p.JS (the tab switcher + the "X min ago" ticker) is already emitted by macro_panel() and
    # runs page-wide, so it is not repeated here - a second copy just double-registers the
    # setInterval. p.CSS is kept (cheap, and micro must still be styled if macro failed).
    return (f'<section>{p.CSS}<h2>News <span class="mut" style="font-weight:400;font-size:14px">'
            f'&mdash; breaking news, decoded</span></h2>'
            f'<div class="pg-wrap" style="margin-top:4px">{body}</div></section>')


def load_commodities():
    """commodities.json, or {} if it is missing or unreadable. The commodity track is an
    add-on - a problem with its file must never stop the FX dashboard from rebuilding."""
    path = DATA / "commodities.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  commodities.json unreadable ({type(e).__name__}: {e}) - section skipped")
        return {}


def commodities_block(d):
    """Render the commodity meter rows and breakdown cards from the loaded commodities dict.
    Returns ('', '') if there is nothing usable so the section degrades to empty."""
    ranked = [s for s in (d.get("ranked") or []) if s in d.get("commodities", {})]
    if not ranked:
        return "", ""

    meter = []
    for s in ranked:
        r = d["commodities"][s]
        crowd = (' <span class="chip warn" title="Managed Money net is more than 35% of open interest - squeeze risk">crowded</span>'
                 if r.get("crowded") else "")
        meter.append(f"""
      <div class="mrow">
        <div class="mccy">{s}<span class="mname">{esc(COMMODITIES[s]['name'])}</span></div>
        {bar(r['score'])}
        <div class="mscore {'pos' if r['score']>=0 else 'neg'}">{r['score']:+.1f}</div>
        <div class="mrate"><span class="pill {r['cls']}">{esc(r['rating'])}</span>{read_chip(r.get('read'))}{cot_chip(r.get('cot_x'))}{crowd}</div>
      </div>""")

    cards = []
    for s in ranked:
        r = d["commodities"][s]
        comp = "".join(
            f"""<div class="crow"><div class="clab">{CPART_LABEL[k]}<span class="cw">&times;{COMMODITY_WEIGHTS[k]:.2f}</span></div>
            {bar(r['parts'][k])}
            <div class="cval {'pos' if r['parts'][k]>=0 else 'neg'}">{r['parts'][k]:+.0f}</div>
            <div class="ccon">{r['contrib'][k]:+.1f}</div></div>"""
            for k in ("trend", "cot", "oi", "overlay")) + cot_pull_row(r)

        ov = r["legs"]["overlay"]
        onotes = "".join(f"""<p class="mnote"><b>{esc(k)}:</b> {esc(v)}</p>"""
                         for k, v in ov.get("notes", {}).items())
        unset = ov.get("unset", [])
        if unset:
            onotes += f"""<p class="mnote mut">Unset: {esc(', '.join(unset))} &mdash; held neutral.</p>"""
        elif not ov.get("notes"):
            onotes = """<p class="mnote mut">No macro overlay set &mdash; leg held at neutral.</p>"""

        rd = r.get("read") or {}
        cards.append(f"""
      <article class="card">
        <header class="chead">
          <div><span class="cccy">{s}</span><span class="cnm">{esc(COMMODITIES[s]['name'])}</span></div>
          <div class="cbig {'pos' if r['score']>=0 else 'neg'}">{r['score']:+.1f}</div>
        </header>
        <p class="readline {esc(rd.get('cls','neu'))}">{esc(rd.get('label',''))}</p>
        {retr_line(r.get('retr'), 'commodity', s)}
        <div class="comp">{comp}</div>
        <div class="detail">
          <div class="dblock">
            <h4>Trend</h4>
            <p>{esc(r['legs']['trend']['note'])}</p>
          </div>
          <div class="dblock">
            <h4>Positioning</h4>
            <p>{esc(r['legs']['cot']['note'])}</p>
            <p class="mut">{esc(r['legs']['oi']['note'])}</p>
            {cotx_line(r.get('cot_x'), r.get('cot_adj') or 0.0)}
          </div>
          <div class="dblock">
            <h4>Macro overlay <span class="mut">{ov.get('coverage',0)}% set</span></h4>
            {onotes}
          </div>
        </div>
      </article>""")
    return "".join(meter), "".join(cards)


# ======================================================================================
# Add-on tabs. Every one of these follows the same contract: read its own JSON, and if it
# is missing or unreadable return a short "not built yet" section rather than raising, so a
# broken feed costs one tab and never the whole page.
# ======================================================================================

def _load_tab(name):
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                       # noqa: BLE001
        print(f"  {name} unreadable ({type(e).__name__}: {e}) - tab degraded")
        return None


def _empty_tab(title, why):
    return (f'<section><h2>{esc(title)}</h2><p class="sub">{esc(why)}</p></section>')


def score_deltas(target_hours=20):
    """How far each score has moved since roughly this time yesterday.

    The pipeline has always computed this - run.py prints "moved since last run" - and then
    thrown it away into a log nobody reads. It is the first thing worth knowing about a board
    you have already seen once, so it belongs on the page.

    NOT measured against the previous run: on a 15-minute cron that is 15 minutes ago and
    almost always zero, which would put a column of +0.0 next to every score and teach you to
    ignore it. Instead take the most recent snapshot that is at least `target_hours` old.
    When the history is too young for that - a fresh deploy, a lost cache - fall back to the
    OLDEST row available and say honestly how old it actually is, rather than calling two
    hours "yesterday".

    Returns ({ccy: delta}, label). Empty when there is nothing worth comparing against.
    """
    hist = _load_tab("score_history.json")
    if not isinstance(hist, list) or len(hist) < 2:
        return {}, ""
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for row in hist:
        try:
            at = dt.datetime.fromisoformat(row["at"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=dt.timezone.utc)
            rows.append((at, row.get("scores") or {}))
        except Exception:                                        # noqa: BLE001
            continue
    if len(rows) < 2:
        return {}, ""
    rows.sort(key=lambda r: r[0])
    cur = rows[-1][1]
    old = [r for r in rows[:-1] if (now - r[0]).total_seconds() >= target_hours * 3600]
    if old:
        base_at, base = old[-1]
    else:
        base_at, base = rows[0]
    age_h = (now - base_at).total_seconds() / 3600
    if age_h < 0.5:
        return {}, ""
    if age_h >= target_hours:
        label = "vs yesterday"
    elif age_h >= 1:
        label = f"vs {age_h:.0f}h ago"
    else:
        label = f"vs {age_h * 60:.0f}m ago"
    deltas = {c: round(cur[c] - base[c], 1)
              for c in cur if c in base and cur[c] is not None and base[c] is not None}
    return deltas, label


def delta_chip(delta, small=False):
    """The move as a coloured arrow. A move under 0.5 shows as a flat dash - below that the
    board has not really changed and an arrow would overstate it."""
    if delta is None:
        return ""
    cls = "mut" if abs(delta) < 0.5 else ("pos" if delta > 0 else "neg")
    arrow = "&mdash;" if abs(delta) < 0.5 else ("&#9650;" if delta > 0 else "&#9660;")
    txt = "" if abs(delta) < 0.5 else f"{abs(delta):.1f}"
    style = "font-size:10px" if small else "font-size:11px"
    return (f'<span class="dchip {cls}" style="{style}" title="{delta:+.1f} since the '
            f'comparison point">{arrow}{txt}</span>')


def ticker_strip():
    """The live market strip across the top. Returns '' when there is nothing to show, so a
    dead feed costs the strip and not the header."""
    d = _load_tab("ticker.json")
    quotes = (d or {}).get("quotes") or []
    if not quotes:
        return ""
    chips = []
    for q in quotes:
        c = q.get("chg_pct")
        cls = "mut" if c is None else ("pos" if c >= 0 else "neg")
        arrow = "" if c is None else ("&#9650;" if c >= 0 else "&#9660;")
        pct = "" if c is None else f"{abs(c):.2f}%"
        chips.append(
            f'<span class="tq"><span class="tqk">{esc(q["label"])}</span>'
            f'<span class="tqv">{q["last"]:,.{q.get("dp", 2)}f}</span>'
            f'<span class="tqc {cls}">{arrow}{pct}</span></span>')
    return f'<div class="tickerwrap"><div class="ticker">{"".join(chips)}</div></div>'


def howto(sub_html):
    """Wrap a long explanatory paragraph so a phone can fold it away.

    These paragraphs are the point of the desk on a desktop - every number traceable, every
    weight justified - but on a 375px screen they push the actual figures two full screens
    down, which is the opposite of helpful. The CSS keeps them permanently open above 720px,
    so this changes nothing for a mouse. Pass the full `<p class="sub">...</p>`.
    """
    return (f'<details class="howto"><summary>How to read this</summary>{sub_html}</details>')


def _safe_panel(fn, title):
    """Render one add-on panel, turning any exception into a visible note on that tab. The
    strength board is the thing this page exists for; a new tab must never be able to take
    it down, and a silently blank tab would be worse than one that says what broke."""
    try:
        return fn()
    except Exception as e:                                       # noqa: BLE001
        print(f"  {title} panel failed ({type(e).__name__}: {e}) - tab shows the error")
        return (f'<section><h2>{esc(title)}</h2><p class="sub">This tab failed to render: '
                f'{esc(type(e).__name__)}: {esc(e)}. The rest of the page is unaffected.</p>'
                f'</section>')


_BCLS = {2: "b2", 1: "b1", 0: "b0", -1: "bm1", -2: "bm2"}


def matrix_panel():
    """The signal matrix - eight currencies against eighteen factors.

    The headline per row stays the WEIGHTED score; the grid adds what the weighted score
    cannot say, which is whether the factors agree. Rows where they disagree with the score
    are marked, because that is the whole reason to look at a grid rather than a list.
    """
    d = _load_tab("matrix.json")
    if not d or not d.get("ranked"):
        return _empty_tab("Signal matrix", "Not built yet - runs at the end of the next refresh.")

    facs = d["factors"]
    # grouped header: one cell per group spanning its factors
    gspans, seen = [], None
    for f in facs:
        if f["group"] != seen:
            gspans.append([f["group"], 0])
            seen = f["group"]
        gspans[-1][1] += 1
    ghead = "".join(f'<th class="grp mono" colspan="{n}">{esc(g)}</th>' for g, n in gspans)

    fhead, first_of_group, seen = [], set(), None
    for f in facs:
        if f["group"] != seen:
            first_of_group.add(f["key"])
            seen = f["group"]
        sep = " gsep" if f["key"] in first_of_group else ""
        fhead.append(f'<th class="fac mono{sep}">{esc(f["label"])}</th>')

    deltas, dlabel = score_deltas()
    rows = []
    for c in d["ranked"]:
        r = d["currencies"][c]
        cells = []
        for f in facs:
            v = r["cells"].get(f["key"])
            b = r["buckets"].get(f["key"])
            sep = " gsep" if f["key"] in first_of_group else ""
            if b is None:
                cells.append(f'<td class="cell bna{sep}" title="{esc(f["label"])}: '
                             f'no reading">&middot;</td>')
            else:
                # "+0" is odd typography for a flat reading - a bare 0 reads better, and
                # keeps the eye on the cells that actually carry a sign
                cells.append(f'<td class="cell {_BCLS[b]}{sep}" '
                             f'title="{esc(f["label"])}: {v:+.1f} on -100..+100">'
                             f'{b:+d}</td>'.replace(">+0<", ">0<"))
        # does the factor count point the other way from the weighted score?
        lean = 1 if r["bull"] > r["bear"] + 2 else (-1 if r["bear"] > r["bull"] + 2 else 0)
        sc = r["score"] or 0.0
        split = (lean > 0 and sc <= -5) or (lean < 0 and sc >= 5)
        rows.append(
            f'<tr class="{"mxsplit" if split else ""}">'
            f'<td class="mxccy">{esc(c)}</td>'
            f'<td class="mxbias"><span class="pill {esc(r["cls"] or "neu")}">'
            f'{esc(r["rating"] or "n/a")}</span></td>'
            f'<td class="mxsc {"pos" if sc >= 0 else "neg"}">{sc:+.1f}'
            f'{delta_chip(deltas.get(c), small=True)}</td>'
            f'<td class="mxconf" title="{r["bull"]} factors bullish, {r["bear"]} bearish, '
            f'{r["flat"]} flat, of {r["covered"]} with a reading">'
            f'<span class="pos">{r["bull"]}</span>/'
            f'<span class="neg">{r["bear"]}</span>'
            f'<span class="mut mxof"> of {r["covered"]}</span></td>'
            + "".join(cells) + "</tr>")

    con = d.get("consensus") or {}
    stats = f"""
    <div class="stats">
      <div class="stat"><span class="k">Strongest</span>
        <span class="v mono pos">{esc(d.get("strongest") or "&mdash;")}</span>
        <span class="s">{(d["currencies"].get(d.get("strongest"), {}).get("score") or 0):+.1f} composite</span></div>
      <div class="stat"><span class="k">Weakest</span>
        <span class="v mono neg">{esc(d.get("weakest") or "&mdash;")}</span>
        <span class="s">{(d["currencies"].get(d.get("weakest"), {}).get("score") or 0):+.1f} composite</span></div>
      <div class="stat"><span class="k">G8 consensus</span>
        <span class="v mono">{con.get("bull", 0)} bull &middot; {con.get("bear", 0)} bear</span>
        <span class="s">{con.get("neutral", 0)} neutral of {con.get("of", 0)}</span></div>
      <div class="stat"><span class="k">Score spread</span>
        <span class="v mono">{d.get("spread") if d.get("spread") is not None else "&mdash;"} pts</span>
        <span class="s">{esc(d.get("strongest") or "")} over {esc(d.get("weakest") or "")}</span></div>
    </div>"""

    legend = ('<div class="mxlegend">'
              '<span><b class="b2">+2</b>strong</span>'
              '<span><b class="b1">+1</b>mild</span>'
              '<span><b class="b0">0</b>flat</span>'
              '<span><b class="bm1">&minus;1</b>mild</span>'
              '<span><b class="bm2">&minus;2</b>strong</span>'
              '<span><b class="bna">&middot;</b>no reading</span>'
              '<span style="border-left:3px solid var(--warn);padding-left:7px">'
              'factors disagree with the score</span>'
              '</div>')

    return f"""
  <section>
    <h2>Signal matrix <span class="mut" style="font-weight:400;font-size:14px">&mdash; 8
      currencies &times; 18 factors</span></h2>
    {howto("""<p class="sub">Every factor is on the same &minus;100..+100 scale as the rest of the desk,
    bucketed to &plusmn;2 for colour only &mdash; <b>hover any cell for its real value</b>.
    The <b>Score</b> column is the desk's weighted score (news&nbsp;0.40, fundamentals&nbsp;0.25,
    COT&nbsp;0.15 &hellip; the weights the backtests actually established) and is the number to
    trade off. The column beside it counts how many factors lean each way, which is what a grid
    is for and what a single score cannot tell you: <em>agreement</em>. A row marked on the left
    is one where the factors lean one way and the weighted score the other &mdash; usually one
    heavy input outvoting many light ones, and worth opening the breakdown card for.
    A dot is <em>no reading</em>, not a neutral one.</p>""")}
    {stats}
    {legend}
    <div class="mxwrap"><table class="mx">
      <thead>
        <tr><th class="grp" colspan="4"></th>{ghead}</tr>
        <tr><th class="fac" style="text-align:left">Ccy</th>
            <th class="fac mxbias" style="text-align:left">Bias</th>
            <th class="fac" style="text-align:right">Score{f' <span class="dlabel">{esc(dlabel)}</span>' if dlabel else ''}</th>
            <th class="fac" style="text-align:right">Bull/bear</th>{"".join(fhead)}</tr>
      </thead>
      <tbody>{"".join(rows)}</tbody>
    </table></div>
    <p class="mnote">Modelled on the terminal layout, with one deliberate difference: that
    design scores each factor as a discrete &plusmn;2 and takes the plain <em>sum</em>, so every
    factor is weighted equally on a &plusmn;36 scale. It reads well and it is worse &mdash; it
    gives seasonality the same vote as CPI, and it collapses a 0.1% inflation beat and a 1.0%
    beat into one cell. The grid and the colour buckets are kept; the headline stays weighted.</p>
  </section>"""


def yields_panel():
    """Policy-relevant yields: 2y, 10y, the curve, the real yield and the differentials."""
    d = _load_tab("yields.json")
    if not d or not d.get("currencies"):
        return _empty_tab("Yields & spreads", "Not built yet - runs on the next refresh.")
    rows = []
    order = d.get("ranked") or list(d["currencies"])
    for c in order:
        r = d["currencies"][c]
        f = lambda v, p=2: (f"{v:.{p}f}" if v is not None else '<span class="mut">&mdash;</span>')
        inv = ' class="inv"' if r.get("inverted") else ""
        sc = r.get("score")
        curve_cls = "neg" if r.get("inverted") else ""
        rows.append(
            f"<tr{inv}><td class='pr'>{esc(c)}</td>"
            f"<td class='mono num'>{f(r.get('y2'))}</td>"
            f"<td class='mono num'>{f(r.get('y10'))}</td>"
            f"<td class='mono num {curve_cls}'>{f(r.get('curve'))}"
            + (' <span class="chip warn">inverted</span>' if r.get("inverted") else "")
            + f"</td><td class='mono num mut'>{f(r.get('cpi'), 1)}</td>"
            f"<td class='mono num'>{f(r.get('real10'))}</td>"
            f"<td class='mono num'>{f(r.get('y2_vs_usd'))}</td>"
            f"<td class='mono num'>{f(r.get('real10_vs_usd'))}</td>"
            f"<td class='mono num {'pos' if (sc or 0) >= 0 else 'neg'}'>"
            + (f"{sc:+.1f}" if sc is not None else '<span class="mut">&mdash;</span>')
            + "</td></tr>")
    inverted = [c for c in d["currencies"] if d["currencies"][c].get("inverted")]
    warn = (f'<p class="mnote warn"><b>Inverted:</b> {", ".join(inverted)} &mdash; the market '
            f'is pricing a slowdown and eventual cuts in {"these" if len(inverted) > 1 else "this"} '
            f'{"economies" if len(inverted) > 1 else "economy"}.</p>') if inverted else ""
    hist = d.get("hist_days", 0)
    momnote = ("" if hist > d.get("mom_days", 20) else
               f'<p class="mnote">Front-end momentum needs {d.get("mom_days", 20) + 1} days of '
               f'history and has {hist} &mdash; that leg is held at zero until it fills, rather '
               f'than estimated.</p>')
    return f"""
  <section>
    <h2>Yields &amp; spreads <span class="mut" style="font-weight:400;font-size:14px">&mdash;
      carry, curve and real return</span></h2>
    {howto("""<p class="sub">Rate differentials are the most established driver in FX, and the
    <b>2-year</b> is the part that moves spot &mdash; it prices the policy path the market
    actually expects, where the 10-year carries term premium as well. <b>Curve</b> is 10y minus
    2y; negative is an inversion. <b>Real</b> is the 10-year minus headline CPI: a high nominal
    yield with inflation above it is not a reason to own a currency. EUR is the German bund and
    CHF the Swiss confederation bond. Score blends the front-end differential (0.45), the real
    yield (0.35) and front-end momentum (0.20), each measured against the board average rather
    than against the dollar alone, so it stays centred like the rest of the desk.</p>""")}
    {warn}{momnote}
    <div class="tw"><table>
      <thead><tr><th>Ccy</th><th class="num">2y</th><th class="num">10y</th>
        <th class="num">Curve</th><th class="num">CPI</th><th class="num">Real 10y</th>
        <th class="num">2y vs USD</th><th class="num">Real vs USD</th>
        <th class="num">Score</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table></div>
    <p class="mnote">Yields and CPI from TradingView (TVC benchmarks, ECONOMICS inflation
    series), read {esc(d.get("asof") or "n/a")}. The real yield here is nominal minus headline
    CPI &mdash; the ex-post figure. The textbook version is the inflation-linked yield, but that
    lives on FRED, which does not answer from this machine or reliably from CI; the ex-post
    figure needs no second feed and moves with the same signal.</p>
  </section>"""


def sentiment_panel():
    """Retail crowd positioning, read contrarian."""
    d = _load_tab("sentiment.json")
    if not d or not d.get("instruments"):
        return _empty_tab("Retail sentiment", "Not built yet - runs on the next refresh.")
    rows = []
    order = d.get("ranked") or list(d["instruments"])
    for k in order:
        r = d["instruments"][k]
        lp, sp = r.get("long_pct"), r.get("short_pct")
        bar = ""
        if lp is not None:
            bar = (f'<span class="pbar" title="retail {lp:.0f}% long / {sp:.0f}% short">'
                   f'<i class="pfill pos" style="width:{lp:.0f}%"></i></span>')
        state = r.get("state")
        chip = (f' <span class="chip {"neg" if "long" in state else "ok"}">{esc(state)}</span>'
                if state else "")
        sc = r.get("score")
        p = r.get("pctl_3y")
        rows.append(
            f"<tr><td class='pr'>{esc(k)}<span class='cnm'>{esc(r.get('name',''))}</span></td>"
            f"<td>{bar}</td>"
            f"<td class='mono num'>{lp:.0f}%</td><td class='mono num mut'>{sp:.0f}%</td>"
            f"<td class='mono num'>{r['net_pct_oi']:+.2f}%</td>"
            f"<td class='mono num'>"
            + (f"{p}<span class='mut'>th</span>" if p is not None
               else f"<span class='mut'>{r['weeks']}w</span>")
            + f"</td><td class='mono num'>"
            + (f"{r['flow_pp']:+.2f}" if r.get("flow_pp") is not None else "&mdash;")
            + f"</td><td class='mono num {'pos' if (sc or 0) >= 0 else 'neg'}'>"
            + (f"{sc:+.1f}" if sc is not None else "&mdash;") + f"{chip}</td></tr>")
    crowded = d.get("crowded") or []
    lead = ""
    if crowded:
        bits = []
        for k in crowded:
            r = d["instruments"][k]
            side = "long" if "long" in (r.get("state") or "") else "short"
            bits.append(f"<b>{esc(k)}</b> &mdash; retail {r['long_pct']:.0f}% long, "
                        f"{r['pctl_3y']}th percentile of the last 3 years, crowded {side}")
        lead = ('<div class="fwlead"><p>' + "</p><p>".join(bits) + "</p>"
                '<p class="mnote">Read against the crowd, not with it &mdash; that is the whole '
                'point of the column.</p></div>')
    return f"""
  <section>
    <h2>Retail sentiment <span class="mut" style="font-weight:400;font-size:14px">&mdash;
      the crowd, read backwards</span></h2>
    {howto("""<p class="sub">Small traders are, on average and <em>at the extremes</em>, on the wrong side
    &mdash; so this score is <b>inverted</b>: a crowded retail long scores negative for the
    instrument. The number is the CFTC's <b>non-reportable</b> column: every account too small
    to have to file, across the whole regulated futures market, published weekly by the
    regulator. That is a better measure than the usual retail-sentiment sources (Myfxbook,
    DailyFX/IG) on every axis except one &mdash; those describe a single broker's book, this
    describes the market. Its real limitation is cadence: <b>Tuesday data published Friday</b>,
    so this is a weekly picture, not a live one. &ldquo;Crowded&rdquo; means crowded against
    <em>this contract's own</em> 3-year range, not some universal threshold.</p>""")}
    {lead}
    <div class="tw"><table>
      <thead><tr><th>Instrument</th><th>Long / short</th><th class="num">Long</th>
        <th class="num">Short</th><th class="num">Net %OI</th><th class="num">3y pctl</th>
        <th class="num">Wk flow</th><th class="num">Score</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table></div>
    <p class="mnote">Week of {esc(d.get("report_date") or "n/a")}. The score is flat between
    the 20th and 80th percentile and only ramps near the edges &mdash; positioning is
    contrarian at extremes and noise in the middle, which is what the FX backtest already
    found for the speculative side.</p>
  </section>"""


_SEAS_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def seasonality_panel():
    """Monthly seasonal bias, de-meaned so a trending instrument does not read bullish
    in all twelve months."""
    d = _load_tab("seasonality.json")
    if not d or not d.get("instruments"):
        return _empty_tab("Seasonality", "Not built yet - runs on the next weekly refresh.")
    cur = d.get("month")
    inst = d["instruments"]
    # widest excess in the table sets the bar scale, so the column is comparable across rows
    span = max([abs(m.get("excess") or 0) for v in inst.values() for m in v.get("months", [])]
               or [1.0]) or 1.0

    def cell(m):
        ex = m.get("excess")
        if ex is None or not m.get("n"):
            return '<td class="mo mut">&middot;</td>'
        w = max(3, round(abs(ex) / span * 22))
        col = "var(--pos)" if ex >= 0 else "var(--neg)"
        star = ' <span class="starred" title="clears the reliability bar">*</span>' if m.get("reliable") else ""
        klass = "mo cur" if m["month"] == cur else "mo"
        title = (f"{m['month']}: {ex:+.2f}% excess (raw {m.get('raw'):+.2f}%), "
                 f"hit {m.get('hit')}% of {m['n']} years, t={m.get('t'):+.2f}"
                 + (" - tail-driven" if m.get("tail_driven") else ""))
        return (f'<td class="{klass}" title="{esc(title)}">'
                f'<span class="seasbar" style="width:{w}px;background:{col}"></span><br>'
                f'<span class="{"pos" if ex >= 0 else "neg"}">{ex:+.1f}</span>{star}</td>')

    def block(kind, title, note):
        keys = [k for k, v in inst.items() if v.get("kind") == kind]
        keys.sort(key=lambda k: -abs((inst[k].get("this_month") or {}).get("excess") or 0))
        if not keys:
            return ""
        rows = []
        for k in keys:
            v = inst[k]
            tm = v.get("this_month") or {}
            sc = v.get("score")
            flags = ""
            if tm.get("tail_driven"):
                flags = ' <span class="chip warn" title="a strong average produced by a few big years, not most of them">tail-driven</span>'
            if v.get("stale"):
                flags += ' <span class="chip warn">cached</span>'
            rows.append(
                f"<tr><td class='pr'>{esc(k)}{flags}</td>"
                f"<td class='mono num {'pos' if (sc or 0) >= 0 else 'neg'}'>"
                + (f"{sc:+.0f}" if sc is not None else "&mdash;") + "</td>"
                + "".join(cell(m) for m in v.get("months", [])) + "</tr>")
        return f"""
    <h3 style="font-size:15px;margin:18px 0 0">{esc(title)}</h3>
    <p class="mnote">{note}</p>
    <div class="tw"><table class="seas">
      <thead><tr><th>{esc(kind.title())}</th><th class="num">{esc(cur)} score</th>
      {"".join(f'<th class="num">{m}</th>' for m in _SEAS_MONTHS)}</tr></thead>
      <tbody>{"".join(rows)}</tbody></table></div>"""

    return f"""
  <section>
    <h2>Seasonality <span class="mut" style="font-weight:400;font-size:14px">&mdash; 15 years,
      drift removed</span></h2>
    {howto("""<p class="sub">Each cell is that month's <b>excess</b> return: the month's average minus
    the instrument's <em>own</em> average month over the same fifteen years. That subtraction is
    the whole point. On raw numbers the S&amp;P has risen for fifteen years, so every one of its
    twelve months looks bullish and the table says nothing; excess asks the question seasonality
    is actually meant to ask &mdash; <em>this</em> month against a normal month for
    <em>this</em> instrument. <b>Hover any cell</b> for the raw figure, hit rate, sample size
    and t-statistic. A <span class="starred">*</span> marks |t|&nbsp;&ge;&nbsp;1.8 on the excess.
    The current month is outlined.</p>""")}
    <p class="mnote warn"><b>Read the stars with suspicion.</b> This table runs 408 tests
    (34 instruments &times; 12 months), so a handful of them clear that bar by chance alone. A
    seasonal is a reason to look at something, never a reason to trade it on its own &mdash;
    and a <em>tail-driven</em> month pays on average out of a few big years rather than in most
    of them, which changes how you would size it.</p>
    {block("fx", "Currency pairs", "28 crosses of the eight majors, quoted the way the market quotes them.")}
    {block("commodity", "Metals &amp; energy", "Front-month futures &mdash; the same contracts the commodity track scores.")}
    {block("index", "Equity indices", "The clearest seasonal pattern on the desk, which is why it earns a scoring leg on the Indices tab and not on the FX board.")}
  </section>"""


def indices_panel():
    """The equity-index track - same shape as the commodity section, own weights."""
    d = _load_tab("indices.json")
    if not d or not d.get("ranked"):
        return _empty_tab("Indices", "Not built yet - runs on the next refresh.")
    W = d.get("weights") or {}
    meter, cards = [], []
    for s in d["ranked"]:
        r = d["indices"][s]
        chips = ""
        if r.get("crowded"):
            chips += ' <span class="chip warn">crowded</span>'
        if r.get("thin"):
            chips += (' <span class="chip warn" title="micro contract, ~43k open interest - '
                      'the positioning legs are weak evidence here">thin contract</span>')
        read = r.get("read") or {}
        meter.append(f"""
      <div class="mrow">
        <div class="mccy">{esc(s)}<span class="mname">{esc(INDICES[s]['name'])}</span></div>
        {bar(r['score'])}
        <div class="mscore {'pos' if r['score'] >= 0 else 'neg'}">{r['score']:+.1f}</div>
        <div class="mrate"><span class="pill {esc(r['cls'])}">{esc(r['rating'])}</span>{chips}</div>
      </div>
      <p class="readline {esc(read.get('cls', 'neu'))}">{esc(read.get('label', ''))}</p>""")

        crows = []
        for k in ("trend", "cot", "oi", "seasonality", "overlay"):
            v = r["parts"].get(k, 0.0)
            con = r["contrib"].get(k, 0.0)
            crows.append(
                f"""<div class="crow"><div class="clab"><span>{esc(k.title())}</span>
                <span class="cw">{W.get(k, 0)*100:.0f}%</span></div>
                {bar(v)}<div class="cval {'pos' if v >= 0 else 'neg'}">{v:+.0f}</div>
                <div class="ccon">{con:+.1f}</div></div>""")
        if r.get("cot_adj"):
            crows.append(f"""<div class="crow"><div class="clab"><span>COT extreme pull</span>
                <span class="cw">after</span></div>{bar(0)}
                <div class="cval mut">&mdash;</div>
                <div class="ccon">{r['cot_adj']:+.1f}</div></div>""")
        notes = "".join(f"<p>{esc(r['legs'][k].get('note', ''))}</p>"
                        for k in ("trend", "cot", "oi", "seasonality", "overlay")
                        if r["legs"].get(k, {}).get("note"))
        cards.append(f"""
      <div class="card">
        <div class="chead"><div><span class="cccy">{esc(s)}</span>
          <span class="cnm">{esc(INDICES[s]['name'])}</span></div>
          <div class="cbig {'pos' if r['score'] >= 0 else 'neg'}">{r['score']:+.1f}</div></div>
        <div class="comp">{"".join(crows)}</div>
        <div class="detail"><div class="dblock"><h4>Legs</h4>{notes}</div></div>
      </div>""")

    return f"""
  <section>
    <h2>Indices <span class="mut" style="font-weight:400;font-size:14px">&mdash; S&amp;P 500,
      Nasdaq 100, Dow</span></h2>
    {howto("""<p class="sub">A third track, separate from the currency board for the same reason
    commodities are: an index is an outright directional bet with a strong upward drift, not a
    relative call against a peer, so it must not sit in the currency centring or the pair
    ranking. Blend is trend&nbsp;0.40, CFTC Leveraged Funds&nbsp;0.20, open interest&nbsp;0.10,
    <b>seasonality&nbsp;0.15</b> &mdash; equities have the clearest seasonal pattern on this desk,
    which is why it earns a leg here and nowhere else &mdash; and a manual overlay&nbsp;0.15 held
    at neutral until set. <b>Real yields are deliberately not a leg</b>: the link is real, but
    the trend leg already carries most of what a yield shock does to an index, and a second leg
    moving with the same shock would let one macro event hit the score twice.</p>""")}
    <div class="meter">{"".join(meter)}</div>
    <div class="grid">{"".join(cards)}</div>
    <p class="mnote">COT {esc(d.get("cot_report_date") or "n/a")}, prices to
    {esc(d.get("price_asof") or "n/a")}. The Dow trades as the <em>micro</em> e-mini in the
    CFTC report &mdash; about 43k open interest against the S&amp;P's 2m &mdash; so its
    positioning legs are thinner and noisier than the other two, and its row says so.</p>
  </section>"""


def build():
    d = json.loads((DATA / "scores.json").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    ranked = d["ranked"]
    best, worst = ranked[0], ranked[-1]
    top_pair = d["pairs"][0] if d["pairs"] else None

    cm = load_commodities()

    # ---- meter rows: currencies ranked, then commodities as a labelled sub-group
    deltas, dlabel = score_deltas()
    meter = []
    for c in ranked:
        r = d["currencies"][c]
        crowd = ' <span class="chip warn" title="Net position is more than 35% of open interest - squeeze risk">crowded</span>' if r["cot"].get("crowded") else ""
        meter.append(f"""
      <div class="mrow">
        <div class="mccy">{c}<span class="mname">{esc(CURRENCIES[c]['name'])}</span></div>
        {bar(r['score'])}
        <div class="mscore {'pos' if r['score']>=0 else 'neg'}">{r['score']:+.1f}{delta_chip(deltas.get(c), small=True)}</div>
        <div class="mrate"><span class="pill {r['cls']}">{esc(r['rating'])}</span>{read_chip(r.get('read'))}{cot_chip(r.get('cot_x'))}{crowd}</div>
      </div>""")
    cm_rows = [s for s in (cm.get("ranked") or []) if s in cm.get("commodities", {})]
    if cm_rows:
        meter.append("""
      <div class="mrow msep"><span>Commodities &mdash; own model, wider scale, not in the
      currency centring or pair ranking</span></div>""")
        for s in cm_rows:
            cr = cm["commodities"][s]
            crowd = (' <span class="chip warn" title="Managed Money net is more than 35% of open interest - squeeze risk">crowded</span>'
                     if cr.get("crowded") else "")
            meter.append(f"""
      <div class="mrow">
        <div class="mccy">{s}<span class="mname">{esc(COMMODITIES[s]['name'])}</span></div>
        {bar(cr['score'])}
        <div class="mscore {'pos' if cr['score']>=0 else 'neg'}">{cr['score']:+.1f}</div>
        <div class="mrate"><span class="pill {cr['cls']}">{esc(cr['rating'])}</span>{read_chip(cr.get('read'))}{cot_chip(cr.get('cot_x'))}{crowd}</div>
      </div>""")

    # ---- per-currency breakdown
    cards = []
    for c in ranked:
        r = d["currencies"][c]
        comp = "".join(
            f"""<div class="crow"><div class="clab">{PART_LABEL[k]}<span class="cw">&times;{WEIGHTS[k]:.2f}</span></div>
            {bar(r['parts'][k])}
            <div class="cval {'pos' if r['parts'][k]>=0 else 'neg'}">{r['parts'][k]:+.0f}</div>
            <div class="ccon">{r['contrib'][k]:+.1f}</div></div>"""
            for k in ("fundamentals", "expectations", "cot", "oi", "news")) + cot_pull_row(r) + centring_row(r)

        news = r["news_drivers"][:4]
        newshtml = "".join(
            f"""<li><span class="nimp {n['impact'].lower()}">{n['impact'][0]}</span>
            <span class="ntitle">{esc(n['title'])}</span>
            <span class="nnum">{dash(n['actual'])} <span class="mut">vs fc {dash(n['forecast'])}</span></span>
            <span class="npts {'pos' if n['points']>=0 else 'neg'}">{n['points']:+.1f}</span></li>"""
            for n in news) or '<li class="mut">No scored releases in the decay window.</li>'

        notes = "".join(
            f"""<p class="mnote"><b>{esc(k)}:</b> {esc(v['basis'])}</p>"""
            for k, v in r["fundamentals"].get("notes", {}).items())

        cats = "".join(
            f"""<div class="cat"><span>{esc(k)}</span><b class="{'pos' if v>3 else 'neg' if v<3 else 'mut'}">{v:.2f}</b></div>"""
            for k, v in r["fundamentals"]["categories"].items())

        rd = r.get("read") or {}
        cards.append(f"""
      <article class="card">
        <header class="chead">
          <div><span class="cccy">{c}</span><span class="cnm">{esc(CURRENCIES[c]['name'])}</span></div>
          <div class="cbig {'pos' if r['score']>=0 else 'neg'}">{r['score']:+.1f}</div>
        </header>
        <p class="readline {esc(rd.get('cls','neu'))}">{esc(rd.get('label',''))}</p>
        {retr_line(r.get('retr'), 'fx', c)}
        <div class="comp">{comp}</div>
        <div class="detail">
          <div class="dblock">
            <h4>Positioning</h4>
            <p>{esc(r['cot']['note'])}</p>
            <p class="mut">{esc(r['oi']['note'])}</p>
            {cotx_line(r.get('cot_x'), r.get('cot_adj') or 0.0)}
          </div>
          <div class="dblock">
            <h4>Checklist <span class="mut">{r['fundamentals']['avg_1_5']:.2f}/5 &middot; {r['fundamentals']['coverage']}% measured</span></h4>
            <div class="cats">{cats}</div>{notes}
          </div>
          <div class="dblock">
            <h4>News drivers</h4>
            <ul class="news">{newshtml}</ul>
          </div>
        </div>
      </article>""")

    # ---- commodities (separate track)
    cmeter, ccards = commodities_block(cm)

    # ---- pairs
    prows = []
    for p in d["pairs"][:10]:
        warn = esc("; ".join(p["warnings"])) if p["warnings"] else '<span class="mut">&mdash;</span>'
        prows.append(
            f"""<tr><td class="pr">{esc(p['pair'])}</td>"""
            f"""<td class="mono pos">{esc(p['long'])}</td><td class="mono neg">{esc(p['short'])}</td>"""
            f"""<td class="mono num">{p['spread']:+.1f}</td><td class="wn">{warn}</td></tr>""")
    pairs = "".join(prows)

    # ---- central bank commentary
    spath = DATA / "speakers.json"
    spk = json.loads(spath.read_text(encoding="utf-8")) if spath.exists() else {"scored": [], "upcoming": [], "pending": []}
    TIER = {"High": 1.0, "Medium": 0.6, "Low": 0.3}
    srows = []
    for e in (spk.get("scored", [])[:6] + spk.get("upcoming", [])[:10]):
        when, rel = fmt_when(e["when"], now)
        w = e["seniority"]
        # flag where the feed's tier materially understates the speaker
        flag = ('<span class="mismatch">under-tagged</span>'
                if w - TIER.get(e.get("feed_impact", "Low"), 0.3) >= 0.25 else "")
        if "tone" in e:
            tone = (f"""<span class="tone {'pos' if e['points'] >= 0 else 'neg'}">"""
                    f"""{e['tone']:+.1f} &rarr; {e['points']:+.0f}</span>""")
        else:
            tone = '<span class="mut">pending</span>' if e["age_h"] >= 0 else '<span class="mut">&mdash;</span>'
        srows.append(
            f"""<tr><td class="mono">{when}<br><span class="mut">{rel}</span></td>"""
            f"""<td class="mono">{esc(e['ccy'])}</td>"""
            f"""<td class="num"><span class="wbar"><i style="width:{w*100:.0f}%"></i></span>{w:.2f}</td>"""
            f"""<td><span class="nimp {e.get('feed_impact','Low').lower()}">"""
            f"""{e.get('feed_impact','Low')[0]}</span></td>"""
            f"""<td>{esc(e['title'])}{flag}</td><td>{tone}</td></tr>""")
    speakers_html = "".join(srows) or (
        '<tr><td colspan="6" class="mut">No central-bank commentary in the window.</td></tr>')

    # ---- upcoming
    ups = []
    for e in d["upcoming"][:10]:
        when, rel = fmt_when(e["when"], now)
        ups.append(f"""<tr><td class="mono">{when}</td><td class="mono mut">{rel}</td>
        <td class="mono">{esc(e['ccy'])}</td>
        <td><span class="nimp {e['impact'].lower()}">{e['impact'][0]}</span> {esc(e['title'])}</td>
        <td class="mono num mut">{dash(e['forecast'])}</td></tr>""")

    nh_when, nh_rel = fmt_when(d["next_high_impact"], now) if d.get("next_high_impact") else ("none scheduled", "")
    nh_event = d.get("next_high_impact_event") or (nh_when if not d.get("next_high_impact") else "high-impact print")
    _bt = dt.datetime.fromisoformat(d["built_at"])
    built = (_bt.strftime("%d %b %Y %H:%M UTC")
             + f' <span class="cn-when mut" data-ts="{_bt.isoformat()}">'
             f'(<span class="cn-ago">just now</span>)</span>')

    gsratio = cm.get("gold_silver_ratio")
    pxdate = cm.get("price_asof") or "n/a"
    ct = next((s for s in (cm.get("ranked") or []) if s in cm.get("commodities", {})), None)
    if ct:
        ctr = cm["commodities"][ct]
        ctop = f"{ct} {ctr['score']:+.1f}"
        ctoprating = esc(ctr["rating"])
    else:
        ctop, ctoprating = "&mdash;", ""

    html = TEMPLATE
    for k, v in {
        "{{METER}}": "".join(meter), "{{CARDS}}": "".join(cards),
        "{{COMMODITY_METER}}": cmeter, "{{COMMODITY_CARDS}}": ccards,
        "{{GSRATIO}}": gsratio if gsratio is not None else "&mdash;",
        "{{PXDATE}}": pxdate, "{{CTOP}}": ctop, "{{CTOPRATING}}": ctoprating,
        "{{PAIRS}}": pairs, "{{UPCOMING}}": "".join(ups),
        "{{SPEAKERS}}": speakers_html,
        "{{BEST}}": best, "{{WORST}}": worst,
        "{{BEST_SC}}": f"{d['currencies'][best]['score']:+.1f}",
        "{{WORST_SC}}": f"{d['currencies'][worst]['score']:+.1f}",
        "{{TOPPAIR}}": top_pair["pair"] if top_pair else "&mdash;",
        "{{TOPSPREAD}}": f"{top_pair['spread']:+.1f}" if top_pair else "",
        "{{COTDATE}}": d.get("cot_report_date") or "n/a",
        "{{NEXTHIGH}}": nh_when, "{{NEXTHIGH_REL}}": nh_rel,
        "{{NEXTHIGH_EVENT}}": esc(nh_event),
        "{{BUILT}}": built, "{{OISRC}}": d.get("oi_cadence", ""),
        "{{TICKER}}": ticker_strip(),
        "{{COT_PANEL}}": cot_panel(), "{{OI_PANEL}}": oi_panel(),
        "{{MACRO_PANEL}}": macro_panel(), "{{MICRO_PANEL}}": micro_panel(),
        "{{FEDWATCH_PANEL}}": fedwatch_panel(),
        # Add-on tabs. Each builder already degrades to a short "not built yet" section on a
        # missing file, but wrap them anyway: a rendering bug in one new tab must not be able
        # to stop the page that carries the strength board from being written at all.
        **{k: _safe_panel(fn, name) for k, fn, name in (
            ("{{MATRIX_PANEL}}", matrix_panel, "Signal matrix"),
            ("{{YIELDS_PANEL}}", yields_panel, "Yields & spreads"),
            ("{{SENTIMENT_PANEL}}", sentiment_panel, "Retail sentiment"),
            ("{{SEASONALITY_PANEL}}", seasonality_panel, "Seasonality"),
            ("{{INDICES_PANEL}}", indices_panel, "Indices"),
        )},
    }.items():
        html = html.replace(k, str(v))
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    return OUT


if __name__ == "__main__":
    build()
