"""HTML shell for the dashboard. Kept apart from build_dashboard.py so the markup
stays readable and the generator stays about data."""

TEMPLATE = r"""<title>Currency Strength Desk</title>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Sans+Condensed:wght@600;700&family=UnifrakturMaguntia&display=swap">
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

.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:14px}
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
table{border-collapse:collapse;width:100%;font-size:13px}
.tw table{min-width:560px}
.oi-hist .tw{margin-top:6px}
.oi-hist table.oi-tw{min-width:420px}
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

.tabwrap{display:flex;flex-direction:column;gap:34px}
.tabin{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip:rect(0 0 0 0)}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tabs label{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-weight:700;
  font-size:15px;padding:10px 16px;cursor:pointer;color:var(--mut);
  border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.tabs label:hover{color:var(--ink2)}
.panel{display:none;flex-direction:column;gap:34px}
/* Nine tabs. Fed Watch is folded into Rates; COT and Open interest are their own tabs again
   (Retail sentiment rides along on the COT tab). The id `tab-yld` is kept for the merged
   Rates pair rather than renamed - internal wiring, churning it buys nothing. */
#tab-cot:checked~#p-cot,#tab-oi:checked~#p-oi,
#tab-macro:checked~#p-macro,
#tab-micro:checked~#p-micro,#tab-board:checked~#p-board,
#tab-mx:checked~#p-mx,#tab-yld:checked~#p-yld,
#tab-seas:checked~#p-seas,#tab-idx:checked~#p-idx{display:flex}
#tab-cot:checked~.tabs label[for="tab-cot"],
#tab-oi:checked~.tabs label[for="tab-oi"],
#tab-macro:checked~.tabs label[for="tab-macro"],
#tab-micro:checked~.tabs label[for="tab-micro"],
#tab-mx:checked~.tabs label[for="tab-mx"],
#tab-yld:checked~.tabs label[for="tab-yld"],
#tab-seas:checked~.tabs label[for="tab-seas"],
#tab-idx:checked~.tabs label[for="tab-idx"],
#tab-board:checked~.tabs label[for="tab-board"]{color:var(--accent);border-bottom-color:var(--accent)}
/* A merged tab stacks two or three sections. Give them a divider so the seam is obvious and
   the reader knows where one subject ends and the next begins. */
.panel > section + section{border-top:1px solid var(--line);padding-top:26px}
/* eleven tabs no longer fit one line on a phone - let the strip scroll rather than wrap into
   a three-deep block that pushes the content off the first screen */
@media (max-width:720px){
  .tabs{flex-wrap:nowrap;overflow-x:auto;-webkit-overflow-scrolling:touch;
    scrollbar-width:none}
  .tabs::-webkit-scrollbar{display:none}
  .tabs label{padding:10px 12px;font-size:14px}
}
.tabin:focus-visible~.tabs{outline:2px solid var(--accent);outline-offset:3px;border-radius:3px}
.cotgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:14px}
.cotcard{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px 6px;box-shadow:var(--shadow)}
.cotcard h3{font-family:"IBM Plex Mono",monospace;font-size:13px;font-weight:600;margin:0 0 6px;
  display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.cotcard h3 .cnm{margin-left:0}
.cotcard h3 .drill{margin-left:auto;flex:none;font-family:"IBM Plex Mono",monospace;font-size:10px;
  font-weight:600;letter-spacing:.03em;text-transform:uppercase;text-decoration:none;
  color:var(--accent);border:1px solid var(--line2);border-radius:999px;padding:2px 8px;white-space:nowrap}
.cotcard h3 .drill:hover{border-color:var(--accent);background:var(--posbg)}
.cotcard h3 .drill+.mono{margin-left:8px;flex:none}
.cotcard table{min-width:0;font-size:11.5px}
.cotcard th,.cotcard td{padding:5px 6px}
/* The two classic COT summary tables, laid out like the weekly "Commitment of Traders
   Report" sheets. 17 columns: wider than a phone, so .crtw scrolls sideways with the first
   column pinned. Own tokens, so both themes keep the sheet's colour logic. */
:root{
  --cr-hd:#4f6228; --cr-hd2:#8a7a4a; --cr-hdtx:#fffdf0; --cr-lbl:#fcd5b4; --cr-lbltx:#3b2a1a;
  --cr-cell:#ffffff; --cr-grid:#dcdcd2; --cr-edge:#4f6228;
  --cr-good:#c6efce; --cr-goodtx:#006100; --cr-bad:#ffc7ce; --cr-badtx:#9c0006;
  --cr-l2:#a9dcb5; --cr-l1:#d3eedb; --cr-mid:#cdd9f2; --cr-s1:#f8d7de; --cr-s2:#efb0bf;
  --cr-h0:#fdf1e6; --cr-h1:#fbdcbf; --cr-h2:#f6bd8b; --cr-h3:#e69a58; --cr-h4:#b8732f;
  --cr-xl:#b0122a; --cr-xs:#0f3f8c; --cr-ref:#6f7445; --cr-avg:#b8622a; --cr-pc:#ecebf6;
  --cr-nc:#4f6228; --cr-cm:#c0632b;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --cr-hd:#46581f; --cr-hd2:#6b5e36; --cr-hdtx:#f4f1dc; --cr-lbl:#4a3626; --cr-lbltx:#fbe3cf;
    --cr-cell:#1d1d25; --cr-grid:#2f2f3b; --cr-edge:#46581f;
    --cr-good:rgba(63,190,131,.20); --cr-goodtx:#6fdaa4; --cr-bad:rgba(236,106,94,.22); --cr-badtx:#ff958b;
    --cr-l2:rgba(63,190,131,.34); --cr-l1:rgba(63,190,131,.16); --cr-mid:rgba(139,141,240,.22);
    --cr-s1:rgba(236,106,94,.16); --cr-s2:rgba(236,106,94,.34);
    --cr-h0:rgba(224,140,58,.08); --cr-h1:rgba(224,140,58,.18); --cr-h2:rgba(224,140,58,.32);
    --cr-h3:rgba(224,140,58,.5); --cr-h4:rgba(224,140,58,.72);
    --cr-xl:#e5485c; --cr-xs:#4f8ef5; --cr-ref:#55593a; --cr-avg:#8f4b22; --cr-pc:rgba(139,141,240,.10);
    --cr-nc:#a9c46c; --cr-cm:#f0a36b;
  }
}
:root[data-theme="dark"]{
  --cr-hd:#46581f; --cr-hd2:#6b5e36; --cr-hdtx:#f4f1dc; --cr-lbl:#4a3626; --cr-lbltx:#fbe3cf;
  --cr-cell:#1d1d25; --cr-grid:#2f2f3b; --cr-edge:#46581f;
  --cr-good:rgba(63,190,131,.20); --cr-goodtx:#6fdaa4; --cr-bad:rgba(236,106,94,.22); --cr-badtx:#ff958b;
  --cr-l2:rgba(63,190,131,.34); --cr-l1:rgba(63,190,131,.16); --cr-mid:rgba(139,141,240,.22);
  --cr-s1:rgba(236,106,94,.16); --cr-s2:rgba(236,106,94,.34);
  --cr-h0:rgba(224,140,58,.08); --cr-h1:rgba(224,140,58,.18); --cr-h2:rgba(224,140,58,.32);
  --cr-h3:rgba(224,140,58,.5); --cr-h4:rgba(224,140,58,.72);
  --cr-xl:#e5485c; --cr-xs:#4f8ef5; --cr-ref:#55593a; --cr-avg:#8f4b22; --cr-pc:rgba(139,141,240,.10);
  --cr-nc:#a9c46c; --cr-cm:#f0a36b;
}
.crsec{gap:18px}
.crg{display:flex;flex-direction:column;gap:6px}
.crhead{display:flex;justify-content:space-between;align-items:baseline;gap:6px 16px;flex-wrap:wrap}
.crt{font-family:"UnifrakturMaguntia","Old English Text MT",serif;font-weight:400;margin:0;
  font-size:clamp(19px,2.3vw,26px);line-height:1.15;letter-spacing:.005em;color:var(--ink)}
.crd{white-space:nowrap}
.crtw{overflow-x:auto;-webkit-overflow-scrolling:touch;border:2px solid var(--cr-edge);
  border-radius:4px;box-shadow:var(--shadow);background:var(--cr-cell)}
table.cotrep{min-width:1100px;width:100%;border-collapse:collapse;font-size:12.5px;
  font-family:"IBM Plex Sans",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.cotrep th,.cotrep td{padding:5px 7px;text-align:center;white-space:nowrap;
  border:1px solid var(--cr-grid);border-width:0 1px 1px 0;vertical-align:middle}
.cotrep th{font-family:"IBM Plex Sans",system-ui,sans-serif;text-transform:none;letter-spacing:0;
  font-size:12px;color:var(--cr-hdtx);background:var(--cr-hd2);font-weight:500;
  border-color:rgba(255,255,255,.22)}
.cotrep tr.g1 th{background:var(--cr-hd);font-weight:600;padding-top:6px;padding-bottom:6px}
.cotrep th.lbl{background:var(--cr-hd);font-weight:700;line-height:1.25}
.cotrep .gs{border-left:1px solid var(--cr-edge)}
.cotrep td{color:var(--ink);background:var(--cr-cell)}
.cotrep .lbl{position:sticky;left:0;z-index:1;min-width:76px;box-shadow:2px 0 0 var(--cr-edge)}
.cotrep td.lbl{background:var(--cr-lbl);color:var(--cr-lbltx);font-weight:600}
.cotrep td.lbl a{color:inherit;text-decoration:underline dotted;text-underline-offset:3px}
.cotrep td.lbl a:hover{text-decoration-style:solid}
.cotrep td.lbl sup{font-weight:700;margin-left:1px}
.cotrep td.lbl.wk{font-weight:500;font-variant-numeric:tabular-nums}
.cotrep td.good{background:var(--cr-good);color:var(--cr-goodtx)}
.cotrep td.bad{background:var(--cr-bad);color:var(--cr-badtx)}
.cotrep td.net{font-weight:600}
.cotrep td.l2{background:var(--cr-l2)} .cotrep td.l1{background:var(--cr-l1)}
.cotrep td.mid{background:var(--cr-mid)}
.cotrep td.s1{background:var(--cr-s1)} .cotrep td.s2{background:var(--cr-s2)}
.cotrep td.h0{background:var(--cr-h0)} .cotrep td.h1{background:var(--cr-h1)}
.cotrep td.h2{background:var(--cr-h2)} .cotrep td.h3{background:var(--cr-h3)}
.cotrep td.h4{background:var(--cr-h4);color:#fff;font-weight:600}
.cotrep td.miss{color:var(--mut);font-style:italic}
.cotrep tbody tr:last-child td{border-bottom:0}
.cotrep tfoot td{background:var(--cr-hd);color:var(--cr-hdtx);font-weight:600;font-size:12.5px;
  white-space:normal;text-align:left;padding:6px 12px;border:0}
@media (min-width:1100px){.cotrep tfoot td{text-align:center}}
.crstat{margin:-6px 0 0;font-size:11.5px;color:var(--mut);line-height:1.6}
.crstat b{color:var(--ink2)}
.crstat .chip{white-space:normal}
.crkey{list-style:none;margin:2px 0 0;padding:0;display:grid;gap:2px 18px;font-size:11.5px;
  color:var(--mut);grid-template-columns:repeat(auto-fit,minmax(min(360px,100%),1fr))}
.crkey b{color:var(--ink2)}
/* ---- the per-instrument 50-week Legacy report (inside the cot50 layer) ---- */
.cotmodal.crmodal .mwrap{max-width:780px}
.crrep{display:flex;flex-direction:column;gap:12px;margin:0 0 30px}
.crrep h4,.crlf h4{margin:0;font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;
  font-size:16px;font-weight:700}
.crlf{display:flex;flex-direction:column;gap:8px;border-top:1px solid var(--line);padding-top:18px}
.crmeta{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:0;font-size:12.5px}
.crmeta dt{color:var(--mut);font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  text-transform:uppercase;letter-spacing:.06em;padding-top:2px}
.crmeta dd{margin:0;color:var(--ink2)}
.crnote{margin:0;font-size:12px;color:var(--mut);line-height:1.5}
.crgap{font-size:10px;color:var(--warn);margin-left:4px;font-weight:600}
.crchart{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:10px;
  padding:8px 10px 6px}
.crchart svg{display:block;width:100%;height:auto}
.crchart text{font:11px "IBM Plex Mono",monospace;fill:var(--mut)}
.crchart .gl{stroke:var(--line);stroke-width:1}
.crchart .zl{stroke:var(--line2);stroke-width:1.2;stroke-dasharray:4 3}
.crchart polyline{fill:none;stroke-width:2.2;stroke-linejoin:round;stroke-linecap:round}
.crchart polyline.ncl{stroke:var(--cr-nc)} .crchart polyline.cml{stroke:var(--cr-cm)}
.crchart .ncd{fill:var(--cr-nc)} .crchart .cmd{fill:var(--cr-cm)}
.crchart text.ncv{fill:var(--cr-nc);font-weight:600} .crchart text.cmv{fill:var(--cr-cm);font-weight:600}
.crchart figcaption{font-size:11.5px;color:var(--mut);display:flex;gap:4px;align-items:center;
  flex-wrap:wrap;padding:2px 2px 0}
.crchart .k{display:inline-block;width:16px;height:3px;border-radius:2px;margin-left:6px}
.crchart .k.ncl{background:var(--cr-nc)} .crchart .k.cml{background:var(--cr-cm)}
.crleg{font-size:12px;color:var(--ink2);background:var(--surface);border:1px solid var(--line);
  border-radius:10px;padding:9px 12px;line-height:1.55}
.crleg p{margin:0 0 4px} .crleg p:last-child{margin:0;color:var(--mut)}
.crsw{display:inline-block;width:44px;height:11px;border-radius:2px;vertical-align:-1px;
  margin:0 4px 0 2px;border:1px solid var(--line2)}
.crsw-l{background:linear-gradient(90deg,var(--cr-cell),var(--cr-xl))}
.crsw-s{background:linear-gradient(90deg,var(--cr-cell),var(--cr-xs))}
.crsw-f{background:linear-gradient(90deg,var(--cr-xl) 0 25%,
  color-mix(in srgb,var(--cr-xl) 56%,var(--cr-cell)) 25% 50%,
  color-mix(in srgb,var(--cr-xl) 32%,var(--cr-cell)) 50% 75%,
  color-mix(in srgb,var(--cr-xl) 10%,var(--cr-cell)) 75%)}
.cotrep.cr7{min-width:560px}
/* summary tables: let the sub-headers wrap so all 17 columns fit the desktop width */
.cotrep:not(.cr7) tr.g2 th{white-space:normal;line-height:1.2;padding:5px 4px}
.cotrep:not(.cr7) td{padding:5px 6px}
@media (max-width:600px){.crchart text{font-size:25px}}
.cotrep tr.g0 th{background:var(--cr-hd);text-align:center;font-weight:700;font-size:13px;
  padding:6px 8px}
.cotrep tr.g0 th span{font-weight:400;opacity:.85}
.cotrep tr.ref th,.cotrep tr.ref td{color:#fff;font-weight:600;border-color:rgba(255,255,255,.22)}
.cotrep tr.mx th,.cotrep tr.mx td,.cotrep tr.mn th,.cotrep tr.mn td{background:var(--cr-ref)}
.cotrep tr.av th,.cotrep tr.av td{background:var(--cr-avg)}
.cotrep td.pc{background:var(--cr-pc)}
/* Long / Short shading: --a = percentile strength x age fade, 0..1 */
.cotrep td.x{cursor:help;outline:none}
.cotrep td.xl{background:rgb(176 18 42 / var(--a,0));
  background:color-mix(in srgb,var(--cr-xl) calc(var(--a,0) * 100%),var(--cr-cell))}
.cotrep td.xs{background:rgb(15 63 140 / var(--a,0));
  background:color-mix(in srgb,var(--cr-xs) calc(var(--a,0) * 100%),var(--cr-cell))}
.cotrep td.xw{color:#fff}
.cotrep td.x:focus{box-shadow:inset 0 0 0 2px var(--accent)}
/* percentile labels: hidden until the report's "Show percentiles" toggle is on */
.cotrep td.x .pp{display:none;font-size:10px;line-height:1.15;font-weight:500;opacity:.85}
.crpct{position:absolute;opacity:0;pointer-events:none}
.crpctl{align-self:flex-start;font:600 12px "IBM Plex Mono",monospace;cursor:pointer;
  border:1px solid var(--line2);border-radius:999px;padding:5px 12px;color:var(--accent);
  background:var(--surface)}
.crpctl::before{content:"\25CB  "}
.crpct:checked+.crpctl{background:var(--accent);color:#fff;border-color:var(--accent)}
.crpct:checked+.crpctl::before{content:"\25CF  "}
.crpct:focus-visible+.crpctl{outline:2px solid var(--accent);outline-offset:2px}
.crpct:checked~.crscroll td.x .pp{display:block}
/* tap (phones have no hover): the cell takes focus and shows its percentile underneath */
.cotrep td.x:focus::after{content:attr(title);display:block;white-space:normal;min-width:150px;max-width:190px;
  margin:4px auto 0;font-size:10.5px;line-height:1.3;font-weight:500;text-align:left}
/* the report tables: on a phone they scroll in a box with the header and the date column
   pinned; once they fit the width, the layer itself scrolls and the header sticks to it */
.crscroll{max-height:78vh;overflow:auto}
.crscroll thead{position:sticky;top:0;z-index:3}
@media (min-width:820px){.crscroll{max-height:none;overflow:visible}}
/* Per-instrument 50-week drill-down. A hash link opens it; :target makes it a full-screen
   layer over the tab, and the browser Back button (or the back link) closes it - it behaves
   like its own page without any script. Lives outside .panel so no hidden parent can eat it. */
/* Always fixed (so it has no in-flow position and the hash link cannot scroll-jump the page
   to the bottom). A CLOSED layer is display:none, not just invisible: with 16 markets x 50
   weeks of tables, laying out the closed layers was most of the page's load time on a
   phone (measure_phone.py). Only the :target layer is laid out. */
.cotmodal{position:fixed;inset:0;z-index:90;background:var(--bg);overflow-y:auto;
  -webkit-overflow-scrolling:touch;padding:22px 18px 64px;display:none}
.cotmodal:target{display:block}
.cotmodal .mwrap{max-width:620px;margin:0 auto}
.cotmodal .mhead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:0 0 14px}
.cotmodal .mhead h3{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;
  font-size:21px;font-weight:700;margin:0;letter-spacing:-.005em}
.cotmodal .mback{font-family:"IBM Plex Mono",monospace;font-size:12px;text-decoration:none;
  color:var(--accent);border:1px solid var(--line2);border-radius:999px;padding:4px 12px;flex:none}
.cotmodal .mback:hover{border-color:var(--accent);background:var(--posbg)}
.cotmodal table{width:100%;font-size:12px}
.pbar{position:relative;display:block;height:10px;border-radius:3px;background:var(--surface2);
  border:1px solid var(--line);overflow:hidden;min-width:90px}
.pfill{position:absolute;left:0;top:0;bottom:0;border-radius:2px}
.pfill.neg{background:var(--neg)} .pfill.pos{background:var(--pos)}
tr.next td{background:var(--surface2)}
.fwlead{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:10px;padding:13px 16px;box-shadow:var(--shadow)}
.fwlead p{margin:0 0 6px;font-size:14px;line-height:1.5;color:var(--ink)}
.fwlead p:last-child{margin-bottom:0}
.fwlead p.mnote{font-size:12.5px}
.fwchart{display:flex;align-items:flex-end;gap:10px;height:190px;padding:10px 14px 0;
  background:var(--surface);border:1px solid var(--line);border-radius:12px;
  box-shadow:var(--shadow);overflow-x:auto}
.fwcol{flex:1;min-width:62px;display:flex;flex-direction:column;align-items:center;
  justify-content:flex-end;height:100%;gap:5px}
/* the bar lives in its own flexible track - as a direct flex child it was being shrunk,
   which made the 42% bar taller than the 58% one */
.fwtrack{flex:1 1 auto;min-height:0;width:100%;max-width:74px;display:flex;align-items:flex-end}
.fwbar{width:100%;background:var(--line2);border-radius:4px 4px 0 0;min-height:3px}
.fwcol.hi .fwbar{background:var(--accent)}
.fwpct{font-family:"IBM Plex Mono",monospace;font-size:12px;font-weight:600;color:var(--mut);
  height:19px;display:flex;align-items:flex-end}   /* fixed, so the highlighted column's
     larger figure does not steal height from its bar and skew the comparison */
.fwcol.hi .fwpct{color:var(--ink);font-size:13.5px}
.fwrange{font-family:"IBM Plex Mono",monospace;font-size:10.5px;color:var(--mut);
  white-space:nowrap;padding-bottom:8px}
.fwcol.hi .fwrange{color:var(--ink2);font-weight:600}
td.fwtop{font-weight:700;color:var(--ink);background:var(--posbg)}
.spreadnote{font-size:12.5px;color:var(--ink2);font-family:"IBM Plex Mono",monospace;margin:0}

/* ---- live market strip ------------------------------------------------------------
   Context, not signal. Scrolls sideways on a phone rather than wrapping to three rows. */
.tickerwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line);
  background:var(--surface);border-radius:8px}
.tickerwrap::-webkit-scrollbar{display:none}
.ticker{display:flex;gap:0;white-space:nowrap;min-width:min-content}
.tq{display:inline-flex;align-items:baseline;gap:7px;padding:9px 15px;
  border-right:1px solid var(--line);font-family:"IBM Plex Mono",monospace}
.tq:last-child{border-right:0}
.tqk{font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--mut)}
.tqv{font-size:13px;font-weight:600;font-variant-numeric:tabular-nums}
.tqc{font-size:11px;font-variant-numeric:tabular-nums}

/* ---- since-yesterday move ---------------------------------------------------------- */
.dchip{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;
  margin-left:6px;white-space:nowrap}
.mscore .dchip{display:block;margin:1px 0 0;font-weight:400}
.dlabel{font-family:"IBM Plex Mono",monospace;font-size:10.5px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--mut)}

/* ---- "how to read this": long explanations, out of the way on a phone -------------
   On the desktop these paragraphs are useful and there is room for them. On a phone they
   push the actual numbers two full screens down, which is the opposite of helpful. A
   <details> collapses them there and leaves the desktop exactly as it was: the summary is
   hidden and the body force-shown above 720px, so nothing changes for a mouse. */
details.howto > summary{display:none}
details.howto:not([open]) > .sub{display:block}
details.howto > .sub{margin-top:-8px}
@media (max-width:720px){
  details.howto > summary{display:block;cursor:pointer;list-style:none;
    font-family:"IBM Plex Mono",monospace;font-size:11px;letter-spacing:.1em;
    text-transform:uppercase;color:var(--accent);padding:5px 0}
  details.howto > summary::-webkit-details-marker{display:none}
  details.howto > summary::after{content:" +"}
  details.howto[open] > summary::after{content:" \2212"}
  details.howto:not([open]) > .sub{display:none}
}

/* ---- signal matrix: 8 currencies x 18 factors ------------------------------------- */
.mxwrap{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
  border-radius:12px;box-shadow:var(--shadow)}
table.mx{border-collapse:separate;border-spacing:0;width:100%;min-width:940px;font-size:12px}
table.mx th,table.mx td{padding:6px 5px;border-bottom:1px solid var(--line)}
table.mx th.grp{text-align:center;font-size:9.5px;letter-spacing:.09em;
  border-bottom:1px solid var(--line2);color:var(--mut)}
table.mx th.grp+th.grp{border-left:1px solid var(--line)}
table.mx th.fac{text-align:center;font-size:9.5px;letter-spacing:.04em;padding:7px 3px;
  writing-mode:horizontal-tb}
table.mx td.gsep,table.mx th.gsep{border-left:1px solid var(--line)}
table.mx td.mxccy{font-family:"IBM Plex Mono",monospace;font-weight:600;font-size:13px;
  white-space:nowrap;position:sticky;left:0;background:var(--surface);z-index:1}
table.mx td.mxbias{font-size:11.5px;white-space:nowrap}
table.mx td.mxsc{text-align:right;font-family:"IBM Plex Mono",monospace;font-weight:600;
  font-size:13px;font-variant-numeric:tabular-nums;padding-right:9px}
table.mx td.cell{text-align:center;font-family:"IBM Plex Mono",monospace;font-size:11px;
  font-weight:600;width:40px;cursor:default}
/* the +/-2 colour buckets. Deliberately two steps each way, so a strong reading is visibly
   different from a mild one without inventing precision the cell does not have. */
.b2{background:var(--pos);color:#fff}
.b1{background:var(--posbg);color:var(--pos)}
.b0{color:var(--mut)}
.bm1{background:var(--negbg);color:var(--neg)}
.bm2{background:var(--neg);color:#fff}
.bna{color:var(--line2)}
table.mx tr:last-child td{border-bottom:0}
.mxconf{font-family:"IBM Plex Mono",monospace;font-size:11px;white-space:nowrap;
  text-align:right;padding-right:10px}
/* On a phone the frozen Ccy+Bias+Score+Bull/bear block ate ~80% of the width and only two
   of the eighteen factor columns were reachable without a long horizontal scrub. The bias
   PILL is the one that can go: the score right beside it already carries the same reading,
   in colour and with a number. Dropping it buys roughly six more factor columns. */
@media (max-width:720px){
  /* NOT display:none - the grouped header above spans the first four columns with a
     colspan, and removing a cell from the row would slide every group label one column
     left. Collapse it to zero width instead and hide its contents, so the column count
     and the colspan still agree. */
  table.mx td.mxbias,table.mx th.mxbias{
    width:0;max-width:0;padding:0;border-left:0;font-size:0;overflow:hidden}
  table.mx td.mxbias > *{display:none}
  .mxconf .mxof{display:none}
  table.mx td.cell{width:32px;font-size:10.5px;padding:6px 3px}
  /* The table is ~940px wide against a ~390px phone viewport, so it scrolls - fine. What is
     NOT fine is the frozen left block eating most of the 390px you can actually see: with
     default widths, Ccy+Score+Bull/bear took ~300px and left room for two factor columns.
     Pinning them narrow gets the block to ~125px and brings eight factors into view. */
  table.mx td.mxccy,table.mx thead th:first-child{width:38px;padding:6px 2px 6px 7px;
    font-size:12px}
  table.mx td.mxsc{width:48px;padding:6px 4px;font-size:12px}
  table.mx td.mxconf{width:40px;padding:6px 5px 6px 2px;font-size:10px}
}
.mxlegend{display:flex;flex-wrap:wrap;gap:14px;align-items:center;font-size:11.5px;
  color:var(--mut);font-family:"IBM Plex Mono",monospace}
.mxlegend b{display:inline-flex;align-items:center;justify-content:center;width:26px;
  height:18px;border-radius:3px;font-size:10.5px;margin-right:5px;vertical-align:middle}
/* a row where the weighted score and the factor count disagree - the case the grid exists
   to surface, flagged rather than left for the eye to find */
tr.mxsplit td.mxccy{box-shadow:inset 3px 0 0 var(--warn)}

/* ---- seasonality ------------------------------------------------------------------ */
table.seas{min-width:820px}
table.seas td.mo{text-align:center;font-family:"IBM Plex Mono",monospace;font-size:11px;
  width:52px;padding:6px 4px}
td.mo.cur{outline:2px solid var(--accent);outline-offset:-2px;border-radius:3px}
.seasbar{display:inline-block;height:7px;border-radius:2px;vertical-align:middle}
.starred{color:var(--accent);font-weight:700}

/* ---- yields ----------------------------------------------------------------------- */
.ycurve{display:flex;align-items:center;gap:3px}
.ydot{width:7px;height:7px;border-radius:50%;background:var(--mut);display:inline-block}
tr.inv td{background:var(--negbg)}

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
        <span class="v mono" style="font-size:13px">{{NEXTHIGH_EVENT}}</span><span class="s">{{NEXTHIGH}} &middot; {{NEXTHIGH_REL}}</span></div>
      <div class="stat"><span class="k">Commodities</span>
        <span class="v mono">{{CTOP}}</span><span class="s">{{CTOPRATING}}</span></div>
    </div>
    {{TICKER}}
  </header>

  <div class="tabwrap">
  <input type="radio" name="tab" id="tab-mx" class="tabin" checked>
  <input type="radio" name="tab" id="tab-board" class="tabin">
  <input type="radio" name="tab" id="tab-cot" class="tabin">
  <input type="radio" name="tab" id="tab-oi" class="tabin">
  <input type="radio" name="tab" id="tab-yld" class="tabin">
  <input type="radio" name="tab" id="tab-seas" class="tabin">
  <input type="radio" name="tab" id="tab-idx" class="tabin">
  <input type="radio" name="tab" id="tab-macro" class="tabin">
  <input type="radio" name="tab" id="tab-micro" class="tabin">
  <nav class="tabs" role="tablist" aria-label="Dashboard views">
    <label for="tab-mx">Matrix</label>
    <label for="tab-board">Strength desk</label>
    <label for="tab-cot">COT</label>
    <label for="tab-oi">Open interest</label>
    <label for="tab-yld">Rates</label>
    <label for="tab-seas">Seasonality</label>
    <label for="tab-idx">Indices</label>
    <label for="tab-macro">Brief</label>
    <label for="tab-micro">News</label>
  </nav>

  <div class="panel" id="p-mx">{{MATRIX_PANEL}}</div>
  <div class="panel" id="p-cot">{{COT_PANEL}}</div>
  <div class="panel" id="p-oi">{{OI_PANEL}}</div>
  <div class="panel" id="p-yld">{{RATES_PANEL}}</div>
  <div class="panel" id="p-seas">{{SEASONALITY_PANEL}}</div>
  <div class="panel" id="p-idx">{{INDICES_PANEL}}</div>
  <div class="panel" id="p-macro">{{MACRO_PANEL}}</div>
  <div class="panel" id="p-micro">{{MICRO_PANEL}}</div>
  {{COT_DRILLDOWN}}

  <div class="panel" id="p-board">
  <section>
    <h2>Strength meter</h2>
    <p class="sub">Blended score from &minus;100 to +100. Bars diverge from the centre line: right is
    bullish, left is bearish. The score is a medium-term bias; the chip beside it reads it against
    the last 5 trading days &mdash; <b class="pos">on&nbsp;trend</b> when price confirms the bias,
    <b class="warn">poss.&nbsp;retracement</b> when price is pulling back against it (a continuation
    watch, not a reversal call). <em>Crowded</em> = speculative net above 35% of open interest;
    <b class="warn">COT&nbsp;extreme</b> = speculative net at a multi-year high or low;
    <b class="warn">COT&nbsp;level</b> = net back on a price it has reversed from before (the
    horizontal support/resistance of positioning &mdash; strongest where it has held 3+ times);
    <b class="neg">COT&nbsp;turning</b> once the net actually rolls over. Each pulls the score
    a few points <em>toward the reversal</em> &mdash; crowded longs down, crowded shorts up
    &mdash; shown in the breakdown below.
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
  </div><!-- /p-board -->
  </div><!-- /tabwrap -->

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
