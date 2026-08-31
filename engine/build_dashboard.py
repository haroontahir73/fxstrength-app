"""Render scores.json into a self-contained dashboard HTML file."""
import json, datetime as dt
from pathlib import Path
from config import (DATA, ORDER, CURRENCIES, WEIGHTS,
                    COMMODITIES, COMMODITY_ORDER, COMMODITY_WEIGHTS, ordinal)
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
    """Chip for a speculative-positioning extreme / turn. Nothing when mid-range."""
    if not cx or not cx.get("state"):
        return ""
    st = cx["state"]
    if st in ("long unwinding", "short covering"):
        return f' <span class="chip neg" title="{esc(cx["note"])}">COT&nbsp;turning</span>'
    return f' <span class="chip warn" title="{esc(cx["note"])}">COT&nbsp;extreme</span>'


def cotx_line(cx, adj=0.0):
    """The positioning-extreme sentence for a card. '' when mid-range. `adj` is the
    contrarian pull the flag put on the score (0 when nothing fired)."""
    if not cx:
        return ""
    if not cx.get("note"):
        p1, p3 = cx.get("pctl_1y"), cx.get("pctl_3y")
        if p1 is None:
            return ""
        yrs = round(cx.get("hist_weeks", 0) / 52)
        return (f'<p class="mut">Speculative net: {ordinal(p1)} percentile over 1 year, '
                f'{ordinal(p3)} over 3, {ordinal(cx.get("pctl_max"))} over {yrs} — mid-range.</p>')
    turn = cx["state"] in ("long unwinding", "short covering")
    pull = f' <b>Score pulled {adj:+.1f}</b> toward reversal.' if adj else ""
    return (f'<p class="mnote {"neg" if turn else "warn"}"><b>Positioning:</b> '
            f'{esc(cx["note"])}.{pull}</p>')


def cot_pull_row(r):
    """Breakdown row for the COT-extreme contrarian pull. '' when the flag did not fire.
    It is not a weighted leg - it is an additive shove applied after the blend - so it
    shows no weight, just the points it moved the score."""
    adj = r.get("cot_adj") or 0.0
    if not adj:
        return ""
    cx = r.get("cot_x") or {}
    turn = cx.get("state") in ("long unwinding", "short covering")
    sign = "pos" if adj >= 0 else "neg"
    tip = esc(cx.get("note", "") or "speculative positioning at an extreme")
    return (f'<div class="crow"><div class="clab">COT extreme'
            f'<span class="cw">{"turning" if turn else "flag"}</span></div>'
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


def build():
    d = json.loads((DATA / "scores.json").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    ranked = d["ranked"]
    best, worst = ranked[0], ranked[-1]
    top_pair = d["pairs"][0] if d["pairs"] else None

    cm = load_commodities()

    # ---- meter rows: currencies ranked, then commodities as a labelled sub-group
    meter = []
    for c in ranked:
        r = d["currencies"][c]
        crowd = ' <span class="chip warn" title="Net position is more than 35% of open interest - squeeze risk">crowded</span>' if r["cot"].get("crowded") else ""
        meter.append(f"""
      <div class="mrow">
        <div class="mccy">{c}<span class="mname">{esc(CURRENCIES[c]['name'])}</span></div>
        {bar(r['score'])}
        <div class="mscore {'pos' if r['score']>=0 else 'neg'}">{r['score']:+.1f}</div>
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
    built = dt.datetime.fromisoformat(d["built_at"]).strftime("%d %b %Y %H:%M UTC")

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
        "{{BUILT}}": built, "{{OISRC}}": d.get("oi_cadence", ""),
    }.items():
        html = html.replace(k, str(v))
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    return OUT


if __name__ == "__main__":
    build()
