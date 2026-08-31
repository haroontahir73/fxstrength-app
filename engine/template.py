"""HTML shell for the dashboard. Kept apart from build_dashboard.py so the markup
stays readable and the generator stays about data."""

TEMPLATE = r"""<title>Currency Strength Desk</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Sans+Condensed:wght@600;700&display=swap">
<style>
:root{
  --bg:#f7f7fa; --surface:#ffffff; --surface2:#f1f1f6; --line:#e3e3ec; --line2:#d3d3df;
  --ink:#1a1a22; --ink2:#4a4a5c; --mut:#6c6c7e;
  --accent:#5457d6; --pos:#1f9d62; --neg:#d0453b; --warn:#c98a18;
  --posbg:rgba(31,157,98,.13); --negbg:rgba(208,69,59,.13); --warnbg:rgba(201,138,24,.15);
  --shadow:0 1px 2px rgba(20,20,32,.05),0 1px 8px rgba(20,20,32,.04);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#14141a; --surface:#1d1d25; --surface2:#23232d; --line:#2e2e3a; --line2:#3b3b4a;
    --ink:#ecedf2; --ink2:#b8b9c6; --mut:#8f90a2;
    --accent:#8b8df0; --pos:#3fbe83; --neg:#ec6a5e; --warn:#e0a63a;
    --posbg:rgba(63,190,131,.16); --negbg:rgba(236,106,94,.16); --warnbg:rgba(224,166,58,.18);
    --shadow:0 1px 2px rgba(0,0,0,.4),0 1px 10px rgba(0,0,0,.25);
  }
}
:root[data-theme="dark"]{
  --bg:#14141a; --surface:#1d1d25; --surface2:#23232d; --line:#2e2e3a; --line2:#3b3b4a;
  --ink:#ecedf2; --ink2:#b8b9c6; --mut:#8f90a2;
  --accent:#8b8df0; --pos:#3fbe83; --neg:#ec6a5e; --warn:#e0a63a;
  --posbg:rgba(63,190,131,.16); --negbg:rgba(236,106,94,.16); --warnbg:rgba(224,166,58,.18);
  --shadow:0 1px 2px rgba(0,0,0,.4),0 1px 10px rgba(0,0,0,.25);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.5; -webkit-font-smoothing:antialiased;
}
.mono,.num{font-family:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace}
.num,.mscore,.cval,.ccon,.cbig{font-variant-numeric:tabular-nums}
.wrap{max-width:1180px;margin:0 auto;padding:32px 24px 72px;display:flex;flex-direction:column;gap:34px}
.mut{color:var(--mut)}
.pos{color:var(--pos)} .neg{color:var(--neg)}

header.top{display:flex;flex-direction:column;gap:14px;border-bottom:1px solid var(--line);padding-bottom:22px}
.eyebrow{font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--mut)}
h1{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-weight:700;
  font-size:clamp(28px,4vw,40px);margin:0;letter-spacing:-.01em;text-wrap:balance}
.tagline{margin:0;color:var(--ink2);max-width:64ch}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-top:4px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  padding:10px 14px;box-shadow:var(--shadow);min-width:150px}
.stat .k{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--mut);display:block}
.stat .v{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums;display:block}
.stat .s{font-size:12px;color:var(--mut);font-family:"IBM Plex Mono",monospace}

section{display:flex;flex-direction:column;gap:14px}
h2{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-size:20px;
  font-weight:700;margin:0;letter-spacing:-.005em}
.sub{margin:-8px 0 0;color:var(--mut);font-size:13.5px;max-width:70ch}

.bar{position:relative;display:block;height:11px;border-radius:3px;background:var(--surface2);
  border:1px solid var(--line);overflow:hidden}
.bar::before{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;
  background:var(--line2);z-index:2}
.fill{position:absolute;top:0;bottom:0;border-radius:2px}
.fill.pos{background:var(--pos)} .fill.neg{background:var(--neg)}

.meter{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:8px 18px;box-shadow:var(--shadow)}
.mrow{display:grid;grid-template-columns:132px 1fr 62px 190px;gap:16px;align-items:center;
  padding:13px 0;border-bottom:1px solid var(--line)}
.mrow:last-child{border-bottom:0}
.mrow.msep{display:block;padding:9px 0 6px;border-bottom:0}
.mrow.msep span{font-family:"IBM Plex Mono",monospace;font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--mut)}
.mccy{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:15px;display:flex;
  flex-direction:column;line-height:1.25}
.mname{font-family:"IBM Plex Sans",sans-serif;font-weight:400;font-size:11.5px;color:var(--mut)}
.mscore{text-align:right;font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:15px}
.mrate{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.pill{font-family:"IBM Plex Mono",monospace;font-size:11px;padding:3px 8px;border-radius:999px;
  border:1px solid var(--line2);white-space:nowrap}
.pill.vsb,.pill.mb{color:var(--pos);background:var(--posbg);border-color:transparent}
.pill.vwb,.pill.mbr{color:var(--neg);background:var(--negbg);border-color:transparent}
.pill.neu{color:var(--mut);background:var(--surface2)}
.chip{font-family:"IBM Plex Mono",monospace;font-size:10.5px;padding:3px 7px;border-radius:999px}
.chip.warn{color:var(--warn);background:var(--warnbg)}
.chip.ok{color:var(--pos);background:var(--posbg)}
.chip.neg{color:var(--neg);background:var(--negbg)}
.readline{margin:-4px 0 2px;font-size:12.5px;font-family:"IBM Plex Mono",monospace}
.readline.pos{color:var(--pos)} .readline.neg{color:var(--neg)} .readline.warn{color:var(--warn)}
.readline.neu{color:var(--mut)}

.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:14px}
.chead{display:flex;justify-content:space-between;align-items:baseline;gap:10px;
  border-bottom:1px solid var(--line);padding-bottom:10px}
.cccy{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:16px}
.cnm{color:var(--mut);font-size:12px;margin-left:8px}
.cbig{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:21px}
.comp{display:flex;flex-direction:column;gap:8px}
.crow{display:grid;grid-template-columns:112px 1fr 42px 44px;gap:10px;align-items:center;font-size:12.5px}
.clab{color:var(--ink2);display:flex;justify-content:space-between;gap:6px}
.cw{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--mut)}
.cval{text-align:right;font-family:"IBM Plex Mono",monospace;font-size:12px}
.ccon{text-align:right;font-family:"IBM Plex Mono",monospace;font-size:12px;font-weight:600;color:var(--ink)}
.detail{display:flex;flex-direction:column;gap:12px;border-top:1px solid var(--line);padding-top:12px}
.dblock h4{margin:0 0 5px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;
  font-family:"IBM Plex Mono",monospace;color:var(--mut);font-weight:500}
.dblock h4 .mut{letter-spacing:0;text-transform:none}
.dblock p{margin:0 0 3px;font-size:12.5px;color:var(--ink2);font-family:"IBM Plex Mono",monospace}
.cats{display:grid;grid-template-columns:1fr 1fr;gap:2px 14px}
.cat{display:flex;justify-content:space-between;font-size:12px;border-bottom:1px dotted var(--line);padding:2px 0}
.cat b{font-family:"IBM Plex Mono",monospace;font-weight:600}
ul.news{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px}
ul.news li{display:grid;grid-template-columns:16px 1fr auto 44px;gap:8px;align-items:baseline;font-size:12px}
ul.news li.empty{display:block;color:var(--mut)}
.ntitle{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.nnum{font-family:"IBM Plex Mono",monospace;font-size:11.5px}
.npts{text-align:right;font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:11.5px}
.nimp{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
  border-radius:3px;font-family:"IBM Plex Mono",monospace;font-size:9.5px;font-weight:600}
.nimp.high{background:var(--negbg);color:var(--neg)}
.nimp.medium{background:var(--warnbg);color:var(--warn)}
.nimp.low{background:var(--surface2);color:var(--mut)}

.tw{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
  border-radius:12px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:560px}
th{text-align:left;font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--mut);font-weight:500;padding:11px 16px;
  border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 16px;border-bottom:1px solid var(--line);vertical-align:baseline}
tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.pr{font-family:"IBM Plex Mono",monospace;font-weight:600}
.wn{color:var(--warn);font-size:12px}
.mismatch{font-family:"IBM Plex Mono",monospace;font-size:10.5px;padding:2px 6px;
  border-radius:999px;background:var(--warnbg);color:var(--warn);margin-left:6px}
.tone{font-family:"IBM Plex Mono",monospace;font-size:11.5px;font-weight:600}
.wbar{position:relative;display:inline-block;width:46px;height:6px;border-radius:2px;
  background:var(--surface2);border:1px solid var(--line);vertical-align:middle;margin-right:7px}
.wbar i{position:absolute;left:0;top:0;bottom:0;background:var(--accent);border-radius:2px}
.mnote{font-size:11.5px;color:var(--mut);font-family:"IBM Plex Sans",sans-serif;
  margin:2px 0 0;line-height:1.4}
.mnote.warn{color:var(--warn)} .mnote.neg{color:var(--neg)}
.mnote.warn b,.mnote.neg b{color:inherit}

footer{border-top:1px solid var(--line);padding-top:20px;color:var(--mut);font-size:12.5px;
  display:flex;flex-direction:column;gap:8px}
footer p{margin:0}
footer b{color:var(--ink2);font-weight:600}
footer code{font-family:"IBM Plex Mono",monospace;background:var(--surface2);
  padding:1px 5px;border-radius:4px;font-size:11.5px}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
@media (max-width:720px){
  .mrow{grid-template-columns:96px 1fr 56px;row-gap:8px}
  .mrate{grid-column:1/-1}
  .cats{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>

<div class="wrap">
  <header class="top">
    <span class="eyebrow">Positioning &middot; macro &middot; flow</span>
    <h1>Currency Strength Desk</h1>
    <p class="tagline">One blended bias per currency, built from the 31-indicator checklist, CFTC
    positioning, open-interest flow and released economic surprises. Rebuilt on every COT release,
    every daily refresh, and after each scheduled data print.</p>
    <div class="stats">
      <div class="stat"><span class="k">Strongest</span>
        <span class="v mono pos">{{BEST}} {{BEST_SC}}</span></div>
      <div class="stat"><span class="k">Weakest</span>
        <span class="v mono neg">{{WORST}} {{WORST_SC}}</span></div>
      <div class="stat"><span class="k">Widest spread</span>
        <span class="v mono">{{TOPPAIR}}</span><span class="s">{{TOPSPREAD}}</span></div>
      <div class="stat"><span class="k">COT as of</span>
        <span class="v mono">{{COTDATE}}</span><span class="s">weekly, Tuesday data</span></div>
      <div class="stat"><span class="k">Next high-impact</span>
        <span class="v mono" style="font-size:13px">{{NEXTHIGH}}</span><span class="s">{{NEXTHIGH_REL}}</span></div>
      <div class="stat"><span class="k">Commodities</span>
        <span class="v mono">{{CTOP}}</span><span class="s">{{CTOPRATING}}</span></div>
    </div>
  </header>

  <section>
    <h2>Strength meter</h2>
    <p class="sub">Blended score from &minus;100 to +100. Bars diverge from the centre line: right is
    bullish, left is bearish. The score is a medium-term bias; the chip beside it reads it against
    the last 5 trading days &mdash; <b class="pos">on&nbsp;trend</b> when price confirms the bias,
    <b class="warn">poss.&nbsp;retracement</b> when price is pulling back against it (a continuation
    watch, not a reversal call). <em>Crowded</em> = speculative net above 35% of open interest;
    <b class="warn">COT&nbsp;extreme</b> = speculative net at a multi-year high or low, where a
    trend change often starts (<b class="neg">COT&nbsp;turning</b> once it rolls over). Either
    one pulls the score a few points <em>toward the reversal</em> &mdash; crowded longs down,
    crowded shorts up &mdash; shown as its own row in the breakdown below.
    Gold, silver and crude sit below the rule &mdash; own model, wider score range, not in the
    currency centring or pair ranking.</p>
    <div class="meter">{{METER}}</div>
  </section>

  <section>
    <h2>What moved each score</h2>
    <p class="sub">Each input is scored &minus;100 to +100 on its own, then weighted. The right-hand
    column is the weighted contribution. A stretched or turning <b>COT extreme</b> adds a
    contrarian <em>pull toward reversal</em>, applied after the blend; for currencies a
    <em>Centring</em> row then shifts the board to average zero. Every row in the right-hand
    column adds up to the headline score.</p>
    <div class="grid">{{CARDS}}</div>
  </section>

  <section>
    <h2>Commodities <span class="mut" style="font-weight:400;font-size:14px">&mdash; gold, silver, crude</span></h2>
    <p class="sub">A separate track, not part of the currency board. Commodities have no central
    bank, no rate path and no macro checklist, and are not zero-sum against each other &mdash; so
    this is a lighter blend of what does carry over: CFTC <em>Managed Money</em> positioning,
    open-interest conviction, the trend against the 50-day average, and a manual macro overlay
    (real yields, the dollar, OPEC+) held neutral until set. Gold/silver ratio {{GSRATIO}};
    prices to {{PXDATE}}.</p>
    <div class="meter">{{COMMODITY_METER}}</div>
    <div class="grid">{{COMMODITY_CARDS}}</div>
  </section>

  <section>
    <h2>Central bank commentary</h2>
    <p class="sub">Speeches carry no number, so the numeric pipeline cannot see them at all.
    These are weighted by <em>speaker seniority</em> rather than the feed's impact tag, because
    that tag is unreliable for commentary &mdash; a mismatch is flagged below. Tone is read
    separately and feeds the checklist's CB-stance indicator.</p>
    <div class="tw"><table>
      <thead><tr><th>When</th><th>Ccy</th><th class="num">Weight</th><th>Feed tier</th><th>Event</th><th>Tone</th></tr></thead>
      <tbody>{{SPEAKERS}}</tbody>
    </table></div>
  </section>

  <section>
    <h2>Pair ranking</h2>
    <p class="sub">Sorted by strength differential. A wide spread is a starting shortlist, not a
    signal &mdash; location on the chart still decides the trade.</p>
    <div class="tw"><table>
      <thead><tr><th>Pair</th><th>Long</th><th>Short</th><th class="num">Spread</th><th>Warnings</th></tr></thead>
      <tbody>{{PAIRS}}</tbody>
    </table></div>
  </section>

  <section>
    <h2>Release schedule</h2>
    <p class="sub">These are the refresh triggers. The scheduled job wakes shortly after each print,
    scores the surprise against forecast, and rebuilds this page.</p>
    <div class="tw"><table>
      <thead><tr><th>When</th><th>Relative</th><th>Ccy</th><th>Event</th><th class="num">Forecast</th></tr></thead>
      <tbody>{{UPCOMING}}</tbody>
    </table></div>
  </section>

  <footer>
    <p><b>Sources.</b> Positioning and open interest from the CFTC Commitments of Traders via
    tradingster.com &mdash; the financial report (Leveraged Funds) for currencies, the
    disaggregated report (Managed Money) for commodities. Calendar, actuals and forecasts from
    TradingView's economic calendar API. Commodity daily closes from Yahoo Finance (GC=F, SI=F,
    CL=F). The checklist auto-fills any indicator with a released datapoint behind it; judgment
    indicators stay manual and are held at neutral until set.</p>
    <p><b>Open interest is weekly, not daily.</b> {{OISRC}}. No free daily open-interest feed was
    reachable &mdash; CME resets the connection, and MarketWatch, WSJ, Investing.com, Barchart and
    Stooq all block automated reads. Swap in a daily source by implementing an adapter in
    <code>fetch_oi.py</code>.</p>
    <p>Built {{BUILT}}. Scores are a weighted heuristic, not advice &mdash; every number here is
    traceable to the source shown beside it.</p>
  </footer>
</div>
"""
