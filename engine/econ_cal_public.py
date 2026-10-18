"""PR #14 — Economic calendar, public view (ForexFactory-style).

Renders a mobile-first economic calendar from the archive that econ_cal.py builds
(data/econ_calendar.json). It makes NO network request and is completely independent
of fetch_calendar.py / calendar.json and of the scoring pipeline — it only reads its
own archive and emits HTML.

Columns: Time | Currency | Impact | Event | Actual | Forecast | Previous.
Forecast is shown as "—" on the official-sources (keyless) path: there is no consensus
forecast, so actual-vs-previous is displayed as a NEUTRAL change (grey arrow), never as a
bullish/bearish trading signal.

Layout: a table on wide screens; stacked cards under 680px so there is no horizontal page
overflow on phones. Day tabs (Today / Tomorrow / This week / Next week / Recent) and
currency / impact / market filters are progressive enhancement — every row is present in
the HTML, so the page is readable with JavaScript off.
"""
import datetime as dt
import json
from html import escape as _e
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:                                                # noqa: BLE001
    UK = dt.timezone.utc

from config import DATA

ARCHIVE = DATA / "econ_calendar.json"
PAGE = Path(__file__).parent / "economic-calendar.html"

CCY_FLAG = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵",
            "CHF": "🇨🇭", "CAD": "🇨🇦", "AUD": "🇦🇺", "NZD": "🇳🇿"}
# market filter: a commodity is "driven by" these currencies' events
MARKET_DRIVERS = {"XAU": ["USD"], "XAG": ["USD"], "HG": ["USD"], "WTI": ["USD"]}
MARKET_LABEL = {"XAU": "Gold", "XAG": "Silver", "HG": "Copper", "WTI": "WTI Oil"}
IMPACT_RANK = {"High": 0, "Medium": 1, "Low": 2}


def _dt(ev):
    try:
        return dt.datetime.fromisoformat(str(ev["dt_utc"]).replace("Z", "+00:00"))
    except Exception:                                            # noqa: BLE001
        return None


def _uk(when):
    return when.astimezone(UK) if when else None


def _val(ev, key):
    v = ev.get(key)
    return _e(str(v)) if v not in (None, "") else "&mdash;"


def _change(ev):
    """Neutral direction of actual vs previous — NOT a good/bad signal."""
    a, p = ev.get("actual_raw"), ev.get("previous_raw")
    if a is None or p is None:
        return ""
    if a > p:
        return '<span class="chg up" title="higher than previous">▲</span>'
    if a < p:
        return '<span class="chg dn" title="lower than previous">▼</span>'
    return '<span class="chg fl" title="unchanged">■</span>'


def _impact_chip(level):
    cls = {"High": "hi", "Medium": "md", "Low": "lo"}.get(level, "lo")
    return f'<span class="imp {cls}">{_e(level or "Low")}</span>'


def _rows(events):
    """One <tr>/card per event, each tagged with data-* for the client-side filters."""
    out = []
    for ev in events:
        when = _dt(ev)
        uk = _uk(when)
        time_s = uk.strftime("%H:%M") if (uk and ev.get("has_time", True)) else "&mdash;"
        day_key = uk.strftime("%Y-%m-%d") if uk else ""
        ccy = ev.get("currency", "")
        markets = [m for m, drv in MARKET_DRIVERS.items() if ccy in drv]
        out.append(
            f'<tr class="ev" data-ccy="{_e(ccy)}" data-impact="{_e(ev.get("impact","Low"))}" '
            f'data-day="{day_key}" data-markets="{_e(",".join(markets))}" '
            f'data-status="{_e(ev.get("status","released"))}">'
            f'<td class="t" data-l="Time">{time_s}</td>'
            f'<td class="c" data-l="Currency">{CCY_FLAG.get(ccy,"")} {_e(ccy)}</td>'
            f'<td class="i" data-l="Impact">{_impact_chip(ev.get("impact"))}</td>'
            f'<td class="e" data-l="Event">{_e(ev.get("event",""))}'
            + (f' <span class="per">{_e(ev.get("period",""))}</span>' if ev.get("period") and ev.get("period") != "latest" else "")
            + (f' <span class="src">{_e(ev.get("source",""))}</span>' if ev.get("source") else "")
            + (' <span class="rev" title="revised">rev</span>' if ev.get("status") == "revised" else "")
            + f'</td>'
            f'<td class="a" data-l="Actual">{_val(ev,"actual")} {_change(ev)}</td>'
            f'<td class="f" data-l="Forecast">{_val(ev,"forecast")}</td>'
            f'<td class="p" data-l="Previous">{_val(ev,"previous")}</td>'
            f'</tr>')
    return "\n".join(out)


def _sorted(events):
    def key(ev):
        w = _dt(ev)
        return (w or dt.datetime.max.replace(tzinfo=dt.timezone.utc),
                IMPACT_RANK.get(ev.get("impact"), 3))
    return sorted(events, key=key)


_CSS = """
:root{--bg:#fff;--fg:#111;--mut:#667;--line:#e3e6ea;--card:#f7f8fa;--hi:#c0392b;--md:#b7791f;--lo:#7a8699;--up:#2e7d32;--dn:#c0392b;--chip:#eef1f5}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0e1116;--fg:#e8eaed;--mut:#9aa4b2;--line:#222832;--card:#161b22;--hi:#ff6b6b;--md:#e3b341;--lo:#8b97a7;--up:#5bd675;--dn:#ff6b6b;--chip:#1b2430}}
:root[data-theme=dark]{--bg:#0e1116;--fg:#e8eaed;--mut:#9aa4b2;--line:#222832;--card:#161b22;--hi:#ff6b6b;--md:#e3b341;--lo:#8b97a7;--up:#5bd675;--dn:#ff6b6b;--chip:#1b2430}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:var(--mut);font-size:13px;margin:0 0 14px}
.bar{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.seg button,.chip{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--chip);color:var(--fg);border-radius:999px;padding:5px 11px;cursor:pointer}
.seg button.on,.chip.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.flt{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:4px 0}
.flt label{color:var(--mut);font-size:12px;margin-right:2px}
table{width:100%;border-collapse:collapse}
thead th{text-align:left;font-size:12px;color:var(--mut);font-weight:600;border-bottom:1px solid var(--line);padding:8px 8px}
td{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}
td.a,td.f,td.p,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.imp{font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;color:#fff}
.imp.hi{background:var(--hi)}.imp.md{background:var(--md)}.imp.lo{background:var(--lo)}
.src{color:var(--mut);font-size:11px}
.per{color:var(--mut);font-size:11px}
.rev{color:var(--md);font-size:11px;border:1px solid var(--md);border-radius:3px;padding:0 3px}
.chg{font-size:11px}.chg.up{color:var(--up)}.chg.dn{color:var(--dn)}.chg.fl{color:var(--mut)}
.dayhead{margin:18px 0 2px;font-size:13px;font-weight:700;color:var(--mut);border-bottom:2px solid var(--line);padding-bottom:4px}
.updated{color:var(--mut);font-size:12px}
.note{color:var(--mut);font-size:12px;margin-top:16px;border-top:1px solid var(--line);padding-top:10px}
.empty{color:var(--mut);padding:24px 0;text-align:center}
@media(max-width:680px){
 thead{display:none}
 table,tbody,tr,td{display:block;width:100%}
 tr.ev{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:6px 10px;margin:8px 0}
 td{border:0;padding:3px 0;display:flex;justify-content:space-between;gap:12px}
 td::before{content:attr(data-l);color:var(--mut);font-size:12px}
 td.e{flex-direction:column;align-items:flex-start}
 td.e::before{margin-bottom:2px}
}
"""

_JS = """
(function(){
 var state={day:'all',ccy:new Set(),impact:new Set(),market:''};
 function dayKey(d){return d.toISOString().slice(0,10);}
 var now=new Date();var today=dayKey(now);
 var tmr=new Date(now.getTime()+864e5);var tomorrow=dayKey(tmr);
 function weekEnd(n){var d=new Date(now);var dow=(d.getDay()+6)%7;d.setDate(d.getDate()+(6-dow)+7*n);return dayKey(d);}
 var thisWeek=weekEnd(0),nextWeek=weekEnd(1);
 function show(tr){
  var d=tr.getAttribute('data-day');
  if(state.day==='today'&&d!==today)return false;
  if(state.day==='tomorrow'&&d!==tomorrow)return false;
  if(state.day==='week'&&!(d>=today&&d<=thisWeek))return false;
  if(state.day==='next'&&!(d>thisWeek&&d<=nextWeek))return false;
  if(state.ccy.size&&!state.ccy.has(tr.getAttribute('data-ccy')))return false;
  if(state.impact.size&&!state.impact.has(tr.getAttribute('data-impact')))return false;
  if(state.market){var m=(tr.getAttribute('data-markets')||'').split(',');if(m.indexOf(state.market)<0)return false;}
  return true;
 }
 function apply(){
  var any=false;
  document.querySelectorAll('tr.ev').forEach(function(tr){var v=show(tr);tr.style.display=v?'':'none';if(v)any=true;});
  document.querySelectorAll('.dayhead').forEach(function(h){
    var sib=h.nextElementSibling,vis=false;
    while(sib&&!sib.classList.contains('dayhead')){if(sib.classList&&sib.classList.contains('ev')&&sib.style.display!=='none')vis=true;sib=sib.nextElementSibling;}
    h.style.display=vis?'':'none';
  });
  var e=document.getElementById('empty');if(e)e.style.display=any?'none':'';
 }
 document.addEventListener('click',function(ev){
  var b=ev.target.closest('[data-seg]');if(b){state.day=b.getAttribute('data-seg');document.querySelectorAll('[data-seg]').forEach(function(x){x.classList.toggle('on',x===b);});apply();return;}
  var c=ev.target.closest('[data-ccy-f]');if(c){var k=c.getAttribute('data-ccy-f');state.ccy.has(k)?state.ccy.delete(k):state.ccy.add(k);c.classList.toggle('on');apply();return;}
  var i=ev.target.closest('[data-imp-f]');if(i){var k=i.getAttribute('data-imp-f');state.impact.has(k)?state.impact.delete(k):state.impact.add(k);i.classList.toggle('on');apply();return;}
 });
 var sel=document.getElementById('market');if(sel)sel.addEventListener('change',function(){state.market=sel.value;apply();});
 apply();
})();
"""


def _controls():
    segs = [("all", "All"), ("today", "Today"), ("tomorrow", "Tomorrow"),
            ("week", "This week"), ("next", "Next week")]
    seg = "".join(f'<button data-seg="{k}"{" class=\"on\"" if k=="all" else ""}>{v}</button>'
                  for k, v in segs)
    ccy = "".join(f'<span class="chip" data-ccy-f="{c}">{CCY_FLAG[c]} {c}</span>'
                  for c in ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"])
    imp = "".join(f'<span class="chip" data-imp-f="{k}">{k}</span>'
                  for k in ["High", "Medium", "Low"])
    mkt = '<select id="market"><option value="">All markets</option>' + \
          "".join(f'<option value="{m}">{MARKET_LABEL[m]}</option>' for m in MARKET_LABEL) + \
          "</select>"
    return (f'<div class="bar seg">{seg}</div>'
            f'<div class="flt"><label>Currency</label>{ccy}</div>'
            f'<div class="flt"><label>Impact</label>{imp}</div>'
            f'<div class="flt"><label>Market</label>{mkt}</div>')


def _future(ev):
    w = _dt(ev)
    return bool(w and w >= dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1))


def _body(archive):
    events = _sorted(archive.get("events", []))
    head = ("<thead><tr><th>Time</th><th>Currency</th><th>Impact</th><th>Event</th>"
            "<th class='n'>Actual</th><th class='n'>Forecast</th><th class='n'>Previous</th></tr></thead>")
    # #2: only events carrying a VERIFIED official schedule go on the calendar days.
    upcoming = [e for e in events if e.get("scheduled") and _future(e)]
    recent = [e for e in events if not (e.get("scheduled") and _future(e))]
    sections, groups, order = [], {}, []
    for ev in upcoming:
        uk = _uk(_dt(ev))
        key, label = uk.strftime("%Y-%m-%d"), uk.strftime("%a %d %b %Y")
        if key not in groups:
            groups[key] = {"label": label, "rows": []}
            order.append(key)
        groups[key]["rows"].append(ev)
    for key in order:
        g = groups[key]
        sections.append(f'<div class="dayhead">{_e(g["label"])}</div>'
                        f'<table>{head}<tbody>{_rows(g["rows"])}</tbody></table>')
    if recent:                                                   # historical, clearly labelled
        sections.append('<div class="dayhead">Latest releases '
                        '<span style="font-weight:400;color:var(--mut)">&middot; most recent official '
                        'prints &mdash; not a forward schedule</span></div>'
                        f'<table>{head}<tbody>{_rows(recent)}</tbody></table>')
    if not sections:
        sections.append('<div class="empty">No calendar data available yet.</div>')
    sections.append('<div class="empty" id="empty" style="display:none">No events match these filters.</div>')
    return "\n".join(sections)


def _meta(archive):
    stamp = archive.get("data_as_of") or archive.get("built_at")
    try:
        uk = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(UK)
        when = uk.strftime("%a %d %b %Y, %H:%M UK")
    except Exception:                                            # noqa: BLE001
        when = "unknown"
    srcs = archive.get("sources", {})
    ok = sum(1 for s in srcs.values() if s.get("ok"))
    return when, ok, len(srcs)


def panel(archive=None):
    archive = archive if archive is not None else _load()
    when, ok, n = _meta(archive)
    note = ("Official-source data (public-domain / open-licence agencies). "
            "<b>Latest releases</b> are the most recent published figures, not a forward "
            "schedule; upcoming events appear only when sourced from an official release "
            "calendar. No consensus forecast exists on this path, so Forecast shows "
            "&ldquo;&mdash;&rdquo; and the arrow by Actual marks change vs Previous only "
            "&mdash; not a trading signal.")
    deg = ' &middot; <span style="color:var(--md)">some sources unavailable</span>' if archive.get("degraded") else ""
    return (f'<div class="wrap">'
            f'<h1>Economic calendar</h1>'
            f'<p class="sub"><span class="updated">Last updated {_e(when)} &middot; {ok}/{n} sources OK{deg}</span></p>'
            f'{_controls()}{_body(archive)}'
            f'<p class="note">{note}</p></div>')


def page(archive=None):
    archive = archive if archive is not None else _load()
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Economic calendar — FX Strength Desk</title>"
            f"<style>{_CSS}</style></head><body>{panel(archive)}"
            f"<script>{_JS}</script></body></html>")


def _load(data=None):
    p = Path(data or DATA) / ARCHIVE.name
    if not p.exists():
        return {"events": [], "sources": {}, "degraded": True, "built_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"events": [], "sources": {}, "degraded": True, "built_at": None}


def write_page(archive=None):
    PAGE.write_text(page(archive), encoding="utf-8")
    return PAGE


if __name__ == "__main__":
    p = write_page()
    print(f"  wrote {p.name}")
