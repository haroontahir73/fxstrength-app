"""Two pages for the app: MACRO (the big picture) and MICRO (what just happened).

MACRO answers "what is driving markets this week, and which way does each thing lean".
MICRO is the breaking-news feed - one story, decoded, with a lean.

Both are written for a trader reading on a phone, so: short lines, plain words, no
jargon. The desk's own numbers are already on the page below; this does not repeat them,
it says what they MEAN.

    python pages.py                 # rebuild both and inject into dashboard.html
    python pages.py dashboard.html  # explicit target
"""
import json, sys
import datetime as dt
from pathlib import Path

import commodity_watch as cw

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

DATA = Path(__file__).parent / "data"

# What each score component actually is, in words a trader uses.
PART_WORDS = {
    "fundamentals": "the economy's numbers",
    "cot": "what the big funds are betting",
    "oi": "money moving into futures",
    "news": "recent news surprises",
    "expectations": "what the market thinks the central bank will do",
}

# Plain reading of a score. The desk already prints its own rating; this is the
# "so what do I do" line.
def _stance(score):
    if score >= 25:
        return "strong", "Look for longs. Buy dips rather than chasing."
    if score >= 10:
        return "firm", "Mild long bias. Wait for a pullback."
    if score > -10:
        return "flat", "No clear side. Leave it alone."
    if score > -25:
        return "soft", "Mild short bias. Sell rallies."
    return "weak", "Look for shorts. Sell rallies rather than chasing."


def _why(parts, contrib):
    """One line naming the biggest push and the biggest drag."""
    if not contrib:
        return ""
    items = sorted(contrib.items(), key=lambda kv: kv[1])
    drag, push = items[0], items[-1]
    bits = []
    if push[1] > 0.5:
        bits.append(f"helped most by {PART_WORDS.get(push[0], push[0])}")
    if drag[1] < -0.5:
        bits.append(f"held back by {PART_WORDS.get(drag[0], drag[0])}")
    return ", ".join(bits).capitalize() if bits else ""


def _load(name):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def regime_line():
    """What is driving everything right now, in one or two sentences."""
    lmap = cw.level_map()
    tag, _ = cw.regime(lmap)
    if tag == "yields":
        return ("Oil is driving everything. Dearer oil means more inflation, so the Fed "
                "keeps rates high. That <b>hurts gold</b> and <b>helps the dollar</b> - "
                "so scary news pushes gold DOWN right now, not up.")
    if tag == "haven":
        return ("Fear is driving markets. Money is moving into safety. Gold is working "
                "as a haven again, so bad news lifts it.")
    if not lmap:
        return "Live prices did not load, so there is no regime read right now."
    return ("No single driver. Gold, oil and rates are moving on their own stories, so "
            "treat each one on its own merits.")


def macro_block():
    sc, co = _load("scores.json"), _load("commodities.json")
    if not sc and not co:
        return ('<div class="pg-empty">The desk has not built yet, so there is no macro '
                'read to show.</div>')

    rows = ""
    for ccy in sc.get("ranked", []):
        d = sc["currencies"][ccy]
        score = d.get("score", 0)
        word, action = _stance(score)
        why = _why(d.get("parts"), d.get("contrib"))
        rows += (f'<div class="pg-row"><div class="pg-name">{ccy}</div>'
                 f'<div class="pg-score {"up" if score>0 else "dn"}">{score:+.0f}</div>'
                 f'<div class="pg-txt"><b>{word.capitalize()}.</b> {action}'
                 + (f'<div class="pg-why">{why}.</div>' if why else "")
                 + "</div></div>")

    crows = ""
    NAMES = {"XAU": "Gold", "XAG": "Silver", "WTI": "Oil"}
    for sym in co.get("ranked", []):
        d = co["commodities"][sym]
        score = d.get("score", 0)
        word, action = _stance(score)
        why = _why(d.get("parts"), d.get("contrib"))
        crows += (f'<div class="pg-row"><div class="pg-name">{NAMES.get(sym, sym)}</div>'
                  f'<div class="pg-score {"up" if score>0 else "dn"}">{score:+.0f}</div>'
                  f'<div class="pg-txt"><b>{word.capitalize()}.</b> {action}'
                  + (f'<div class="pg-why">{why}.</div>' if why else "")
                  + "</div></div>")

    # Stale data that looks live is worse than no data. The desk rebuilds on a GitHub
    # cron, and GitHub drops crons - so say plainly how old these numbers are.
    stale = ""
    built = sc.get("built_at") or ""
    if built:
        try:
            b = dt.datetime.fromisoformat(built)
            if b.tzinfo is None:
                b = b.replace(tzinfo=dt.timezone.utc)
            age = (dt.datetime.now(dt.timezone.utc) - b).total_seconds() / 60
            if age > 180:
                stale = (f'<div class="pg-stale">These numbers are '
                         f'<b>{int(age // 60)}h {int(age % 60)}m old</b>. The desk has not '
                         f'rebuilt since. Prices in the news alerts are still live.</div>')
            else:
                stale = (f'<div class="pg-age">Updated {int(age)} min ago</div>'
                         if age >= 1 else '<div class="pg-age">Updated just now</div>')
        except Exception:                                      # noqa: BLE001
            pass

    nxt = sc.get("next_high_impact") or ""
    when = ""
    if nxt:
        try:
            t = dt.datetime.fromisoformat(nxt)
            mins = (t - dt.datetime.now(dt.timezone.utc)).total_seconds() / 60
            when = (f'<div class="pg-next">Next big number: '
                    f'<b>{t:%a %d %b %H:%M} UTC</b>'
                    + (f" - in {int(mins//60)}h {int(mins%60)}m" if 0 < mins < 5760 else "")
                    + "</div>")
        except Exception:                                      # noqa: BLE001
            pass

    return (f'{stale}<div class="pg-lead">{regime_line()}</div>'
            f'<h3 class="pg-h3">Currencies</h3>{rows}'
            f'<h3 class="pg-h3">Gold, Silver, Oil</h3>{crows}{when}'
            '<div class="pg-foot">Scores run -100 to +100. Plus means buy-side, '
            'minus means sell-side. This is a guide, not advice.</div>')


CSS = """
<style>
.pg-wrap{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 margin:14px 0 22px}
.pg-tabs{display:flex;gap:6px;margin:0 0 12px}
.pg-tab{flex:1;text-align:center;padding:9px 6px;border-radius:8px;cursor:pointer;
 font-weight:700;font-size:13px;letter-spacing:.04em;border:1px solid rgba(128,128,128,.3);
 background:rgba(128,128,128,.07);user-select:none}
.pg-tab.on{background:#3b5bdb;color:#fff;border-color:#3b5bdb}
.pg-sub{font-size:12px;opacity:.55;margin:-6px 0 12px}
.pg-lead{font-size:14px;line-height:1.55;padding:11px 13px;border-radius:8px;
 background:rgba(59,91,219,.10);border-left:3px solid #3b5bdb;margin:0 0 14px}
.pg-h3{font-size:12px;letter-spacing:.08em;text-transform:uppercase;opacity:.6;
 margin:16px 0 6px}
.pg-row{display:flex;gap:10px;align-items:baseline;padding:7px 0;
 border-bottom:1px solid rgba(128,128,128,.16)}
.pg-name{font-weight:700;width:56px;flex:none}
.pg-score{width:44px;flex:none;text-align:right;font-weight:700;
 font-family:ui-monospace,Menlo,Consolas,monospace}
.pg-score.up{color:#12924b}.pg-score.dn{color:#d1344a}
.pg-txt{flex:1;font-size:13px}
.pg-why{opacity:.6;font-size:12px;margin-top:2px}
.pg-next{margin-top:14px;font-size:13px;padding:9px 12px;border-radius:8px;
 background:rgba(128,128,128,.10)}
.pg-foot{margin-top:12px;font-size:11.5px;opacity:.5}
.pg-age{font-size:11.5px;opacity:.5;margin:0 0 8px}
.pg-stale{font-size:12.5px;margin:0 0 10px;padding:8px 11px;border-radius:7px;border-left:3px solid #d99000;background:rgba(217,144,0,.12)}
.pg-empty{opacity:.55;font-size:13px;padding:10px 0}
@media (prefers-color-scheme:dark){.pg-score.up{color:#35d07f}.pg-score.dn{color:#ff6b7d}
 .pg-tab.on{background:#4c6ef5;border-color:#4c6ef5}}
</style>
"""

JS = """
<script>
(function(){
  // N tabs, discovered from the DOM. It was hardcoded to the macro/micro pair, so adding
  // a third pane silently broke the switcher; now any [data-pg-pane] just works.
  function panes(){ return Array.prototype.slice.call(
      document.querySelectorAll('[data-pg-pane]')); }
  function show(which){
    var ps = panes();
    if(!ps.length) return;
    var names = ps.map(function(p){ return p.getAttribute('data-pg-pane'); });
    if(names.indexOf(which) < 0) which = names[0];
    ps.forEach(function(p){
      p.style.display = (p.getAttribute('data-pg-pane')===which) ? 'block' : 'none';
    });
    names.forEach(function(n){
      var t=document.getElementById('pg-tab-'+n);
      if(t) t.className = 'pg-tab'+(n===which?' on':'');
    });
    try{ localStorage.setItem('pgTab', which); }catch(e){}
  }
  window.pgShow = show;

  // "12 min ago" beats "02 SEP 12:27 UTC" on a phone, and it has to be computed when
  // the page is OPENED - the page itself may have been built hours earlier.
  function ago(){
    var now = Date.now();
    var els = document.querySelectorAll('.cn-when[data-ts]');
    for (var i=0;i<els.length;i++){
      var t = Date.parse(els[i].getAttribute('data-ts'));
      if (!t) continue;
      var m = Math.round((now - t)/60000), s;
      if (m < 1) s = 'just now';
      else if (m < 60) s = m + ' min ago';
      else if (m < 1440) s = Math.floor(m/60) + 'h ' + (m%60) + 'm ago';
      else s = Math.floor(m/1440) + 'd ago';
      var span = els[i].querySelector('.cn-ago');
      if (span) span.textContent = s;
    }
  }
  ago();
  setInterval(ago, 60000);
  var saved='macro';
  try{ saved = localStorage.getItem('pgTab') || 'macro'; }catch(e){}
  if(document.readyState==='loading')
    document.addEventListener('DOMContentLoaded', function(){ show(saved); });
  else show(saved);
})();
</script>
"""


def build(feed=None):
    if feed is None:
        try:
            feed = json.loads((DATA / "commodity_feed.json").read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            feed = []
    micro = cw.render_block(feed)

    # MACRO and MICRO only. Open interest deliberately lives on its OWN page
    # (oi.py -> open-interest.html), not as a third tab here - the user asked for the two
    # to stay separate. The switcher below is still N-tab-safe on purpose: a phone that
    # stored pgTab='oi' while the tab briefly existed must not load with every pane hidden.
    tabs = ['<div class="pg-tab on" id="pg-tab-macro" onclick="pgShow(\'macro\')">'
            'MACRO &middot; big picture</div>',
            '<div class="pg-tab" id="pg-tab-micro" onclick="pgShow(\'micro\')">'
            'MICRO &middot; breaking news</div>']
    panes = [f'<div id="pg-macro" data-pg-pane="macro">{macro_block()}</div>',
             f'<div id="pg-micro" data-pg-pane="micro" style="display:none">{micro}</div>']

    return (CSS + '<section class="pg-wrap">'
            f'<div class="pg-tabs">{"".join(tabs)}</div>'
            + "".join(panes) + '</section>' + JS)


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "dashboard.html"
    block = build()
    out = Path(__file__).parent / "commodity-news.html"
    out.write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" '
                   'content="width=device-width,initial-scale=1">'
                   '<title>Macro / Micro</title>'
                   '<body style="margin:0;padding:16px;max-width:760px">'
                   + block + "</body>", encoding="utf-8")
    cw.inject(target, block)
    print(f"  built macro + micro pages -> {target}")


if __name__ == "__main__":
    main()
