"""Desktop + phone layout check of a BUILT dashboard.html's COT report tables and the 16
50-week reports, with screenshots. Needs Playwright (CI installs it; not a pipeline dep).

    python check_layout.py dashboard.html --out build-check

Checks at 1280x900 and 390x844 (phone): the summary tables fit the desktop width and
scroll sideways on a phone with the first column pinned and no page-level sideways scroll;
every report opens from its link, is visible, has a drawn chart, keeps its header and date
column pinned while its tables scroll, shows the percentile when a shaded figure is tapped,
and reveals every percentile label with "Show percentiles". Writes layout.json and PNGs to
--out; exit 1 on any failure.

The 390x844 pass above is a narrow WINDOW: it ignores <meta viewport>, so it cannot show
how a phone renders the page. Two real-phone passes follow (mobile emulation + touch, every
interaction a real tap), each over all 16 reports: `phone_browser` (dashboard.html opened
in a phone browser) and `android_app` (the page inside the Android app's own wrapper,
reproduced from the app's DashboardRepo.wrap). `--device-only` runs just those two, e.g.
against the live page for a before/after.
"""
import argparse, json, re, sys
from pathlib import Path

FAILS, OKS = [], []


def ok(cond, what, detail=""):
    (OKS if cond else FAILS).append(what)
    if not cond:
        print(f"  FAIL {what}" + (f"  ({detail})" if detail else ""))


APP_HEAD = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>')


def app_wrap(html):
    """What the Android app (com.hbt.fxstrength, DashboardRepo.wrap) does with the page it
    fetches: anything not starting with <!doctype / <html is put inside this shell."""
    low = html.lstrip().lower()
    return html if low.startswith("<!doctype") or low.startswith("<html") else APP_HEAD + html + "</body></html>"


def device_phone(b, page_path, keys, out, label="device"):
    """A real phone, not a narrow window: mobile emulation (the page's <meta viewport> is
    honoured or ignored exactly as on a handset - without one Chrome lays the page out
    980 px wide and shrinks it), touch input, and every interaction a real tap."""
    url = page_path.as_uri()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                        is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(url)
    vw = pg.evaluate("() => [window.innerWidth, document.documentElement.scrollWidth]")
    ok(vw[0] == 390, f"{label}: page laid out at the phone's width (390), not desktop width", vw)
    ok(vw[1] <= vw[0] + 1, f"{label}: no page-level sideways scroll", vw)
    pg.tap('label[for="tab-cot"]')
    pg.wait_for_timeout(250)
    m = pg.evaluate("""() => { const t = document.querySelector('.crsec .crtw');
        const l = t.querySelector('tbody td.lbl'); const x0 = l.getBoundingClientRect().left;
        t.scrollLeft = 400; const r = [t.scrollWidth > t.clientWidth, x0, l.getBoundingClientRect().left, t.scrollLeft];
        t.scrollLeft = 0; return r; }""")
    ok(m[0] and m[3] > 0 and abs(m[1] - m[2]) <= 2, f"{label}: summary table scrolls sideways, market column pinned", m)
    link = '.crsec a[href="#cot50-WTI"]'
    pg.locator(link).scroll_into_view_if_needed()
    pg.screenshot(path=str(out / f"{label}_summary.png"))
    for k in keys:
        pg.goto(url)
        pg.tap('label[for="tab-cot"]')
        pg.locator(f'.crsec a[href="#{k}"]').scroll_into_view_if_needed()
        y0 = pg.evaluate("() => Math.round(window.scrollY)")
        pg.tap(f'.crsec a[href="#{k}"]')
        pg.wait_for_timeout(200)
        info = pg.evaluate(f"""() => {{
            const L = document.getElementById({json.dumps(k)});
            const svg = L.querySelector('.crchart svg').getBoundingClientRect();
            const boxes = [...L.querySelectorAll('.crrep .crscroll')];
            const nc = boxes[0].querySelector('table');
            const cap = (nc.querySelector('caption') || nc.querySelector('thead tr')).textContent;
            const hr = [...nc.querySelectorAll('tr')].find(r => r.cells[0] && r.cells[0].textContent.trim() === 'Date');
            const hdr = hr ? [...hr.cells].map(t => t.textContent.trim()) : [];
            const ref = [...nc.querySelectorAll('tr.mx, tr.mn, tr.av')].length;
            const wk = [...nc.querySelectorAll('tbody tr')].filter(r => !/\\b(mx|mn|av)\\b/.test(r.className)).length;
            return {{d: getComputedStyle(L).display, y: Math.round(window.scrollY), cap, hdr, ref, wk,
                     n: boxes.length, comm: boxes[1] ? boxes[1].textContent.includes('Commercial') : false,
                     chart: [Math.round(svg.width), Math.round(svg.height), Math.round(svg.right)],
                     over: [L.scrollWidth, L.clientWidth]}}; }}""")
        ok(info["d"] == "block" and abs(info["y"] - y0) <= 2, f"{label}: tapping {k} opens its report without moving the page", (y0, info["d"], info["y"]))
        ok("Non-Commercial" in info["cap"] and info["hdr"][:7] == ["Date", "Long", "Short", "Total (L+S)", "% Long", "% Short", "Net Positions"]
           and info["ref"] == 3 and info["wk"] == 50, f"{label}: {k} Non-Commercial table complete (7 columns, Max/Min/13-wk Avg, 50 weeks)",
           (info["cap"][:40], info["hdr"], info["ref"], info["wk"]))
        ok(info["n"] == 3 and info["comm"], f"{label}: {k} separate Commercial and Net Difference tables", info["n"])
        ok(info["chart"][0] > 200 and info["chart"][1] > 40 and info["chart"][2] <= 391, f"{label}: {k} chart drawn inside the screen", info["chart"])
        ok(info["over"][0] <= info["over"][1] + 1, f"{label}: {k} no sideways scroll of the report", info["over"])
        jumps = pg.evaluate(f"() => [...document.querySelectorAll('#{k} .crjump a')].map(a => a.getAttribute('href'))")
        if jumps:
            ok(jumps == [f"#{k}-nc", f"#{k}-comm", f"#{k}-net"], f"{label}: {k} jump links to its three sections", jumps)
            for h in jumps:
                pg.locator(f'#{k} .crjump a[href="{h}"]').tap()
                pg.wait_for_timeout(200)
                j = pg.evaluate(f"""() => {{ const L = document.getElementById({json.dumps(k)});
                    const t = document.getElementById({json.dumps(h[1:])});
                    return {{d: getComputedStyle(L).display, top: Math.round(t.getBoundingClientRect().top),
                             open: [...document.querySelectorAll('.cotmodal')].filter(m => getComputedStyle(m).display !== 'none').length,
                             title: t.querySelector('thead tr').textContent.trim().slice(0, 60)}}; }}""")
                ok(j["d"] == "block" and j["open"] == 1 and 0 <= j["top"] <= 40,
                   f"{label}: {k} 'Jump to' {h.rsplit('-', 1)[1]} keeps the report open and brings that table to the top", j)
                if k == "cot50-WTI" and h.endswith("-comm"):
                    pg.screenshot(path=str(out / f"{label}_WTI_jump_commercial.png"))
            pg.go_back()
            pg.wait_for_timeout(200)
            j = pg.evaluate(f"""() => [getComputedStyle(document.getElementById({json.dumps(k)})).display, location.hash]""")
            ok(j[0] == "block" and j[1] == f"#{k}-comm", f"{label}: {k} Back after a jump stays in the report (previous section)", j)
            pg.evaluate(f"() => document.getElementById({json.dumps(k)}).scrollTop = 0")
            if k == "cot50-WTI":
                pg.screenshot(path=str(out / f"{label}_WTI_jumplinks.png"))
        pin = pg.evaluate(f"""() => {{
            const box = document.getElementById({json.dumps(k)}).querySelector('.crrep .crscroll');
            box.scrollTop = 500; box.scrollLeft = 160;
            const b = box.getBoundingClientRect(), th = box.querySelector('thead').getBoundingClientRect(),
                  lb = box.querySelector('tbody td.lbl').getBoundingClientRect();
            const r = [Math.round(th.top - b.top), Math.round(lb.left - b.left), box.scrollTop, box.scrollLeft];
            box.scrollTop = 0; box.scrollLeft = 0; return r; }}""")
        ok(pin[2] > 0 and pin[3] > 0 and 0 <= pin[0] <= 3 and 0 <= pin[1] <= 3,
           f"{label}: {k} table scrolls with header and date column pinned", pin)
        cell = f'#{k} .crrep .crscroll td.x[title*="percentile"]'
        pg.locator(cell).first.scroll_into_view_if_needed()
        pg.locator(cell).first.tap()
        pg.wait_for_timeout(120)
        tap = pg.evaluate("""() => { const c = document.activeElement;
            return c && c.matches('td.x') ? getComputedStyle(c, '::after').content : null; }""")
        ok(bool(tap) and "percentile" in tap, f"{label}: {k} tapping a shaded figure shows its percentile", tap)
        if k == "cot50-WTI":
            pg.screenshot(path=str(out / f"{label}_WTI_tap.png"))
        pg.locator(f"#{k} .crpctl").tap()
        lab = pg.evaluate(f"""() => {{ const ps = [...document.getElementById({json.dumps(k)}).querySelectorAll('td.x .pp')];
            return [ps.length, ps.filter(p => getComputedStyle(p).display === 'block').length]; }}""")
        ok(lab[0] > 0 and lab[0] == lab[1], f"{label}: {k} 'Show percentiles' (tapped) reveals every label", lab)
        if k == "cot50-WTI":
            pg.evaluate("() => document.getElementById('cot50-WTI').scrollTop = 0")
            pg.screenshot(path=str(out / f"{label}_WTI_top.png"))
            pg.locator("#cot50-WTI .crrep .crscroll").first.scroll_into_view_if_needed()
            pg.screenshot(path=str(out / f"{label}_WTI_noncommercial.png"))
        pg.locator(f"#{k} .crpctl").tap()
        pg.locator(f"#{k} a.mback").tap()
        pg.wait_for_timeout(150)
        d = pg.evaluate(f"() => getComputedStyle(document.getElementById({json.dumps(k)})).display")
        ok(d == "none", f"{label}: {k} back link (tapped) closes the report", d)
    # The phone's own Back button, from a tapped-open report.
    pg.goto(url)
    pg.tap('label[for="tab-cot"]')
    pg.locator(link).scroll_into_view_if_needed()
    y0 = pg.evaluate("() => Math.round(window.scrollY)")
    pg.tap(link)
    pg.wait_for_timeout(200)
    pg.go_back()
    pg.wait_for_timeout(250)
    st = pg.evaluate("() => [getComputedStyle(document.getElementById('cot50-WTI')).display, Math.round(window.scrollY)]")
    ok(st[0] == "none" and abs(st[1] - y0) <= 2, f"{label}: Back closes the report and returns to the same place", (y0, st))
    retail_phone(pg, url, label, out)
    divergence_phone(pg, url, label, out)
    ctx.close()


def retail_phone(pg, url, label, out):
    """The Myfxbook retail tab on a phone: fits the width, bars and percentages readable,
    not excessively tall, and every market name opens that market's report (with its card)."""
    pg.goto(url)
    if not pg.query_selector('label[for="tab-rtl"]'):
        ok(False, f"{label}: Myfxbook retail tab exists")
        return
    pg.locator('label[for="tab-rtl"]').scroll_into_view_if_needed()
    pg.tap('label[for="tab-rtl"]')
    pg.wait_for_timeout(250)
    m = pg.evaluate("""() => { const P = document.getElementById('p-rtl'), W = window.innerWidth;
        const bars = [...P.querySelectorAll('.mfxbar')].map(b => b.getBoundingClientRect());
        const pcts = [...P.querySelectorAll('.mfxl, .mfxs')].map(e => parseFloat(getComputedStyle(e).fontSize));
        const rows = [...P.querySelectorAll('.mfxrow')];
        const over = [...P.querySelectorAll('*')].filter(e => { const r = e.getBoundingClientRect();
            return r.width > 0 && (r.right > W + 1 || r.left < -1); }).length;
        return {disp: getComputedStyle(P).display, W, sw: document.documentElement.scrollWidth, over,
                bars: bars.length, barsIn: bars.every(r => r.left >= 0 && r.right <= W + 1 && r.width > 40),
                minFont: pcts.length ? Math.min(...pcts) : 0, rows: rows.length,
                rowH: rows.length ? Math.max(...rows.map(r => r.getBoundingClientRect().height)) : 0,
                h: Math.round(P.getBoundingClientRect().height),
                empty: !!P.querySelector('.mfxempty')}; }""")
    ok(m["disp"] != "none", f"{label}: Myfxbook retail tab opens", m["disp"])
    ok(m["sw"] <= m["W"] + 1 and m["over"] == 0, f"{label}: Myfxbook tab has no sideways overflow", (m["sw"], m["W"], m["over"]))
    if m["empty"]:
        ok(True, f"{label}: Myfxbook tab shows its no-data note")
        pg.screenshot(path=str(out / f"{label}_myfxbook.png"))
        return
    ok(m["rows"] == 16, f"{label}: Myfxbook lists all 16 markets", m["rows"])
    ok(m["bars"] > 0 and m["barsIn"], f"{label}: Myfxbook bars fit the phone width", m["bars"])
    ok(m["minFont"] >= 11.5, f"{label}: Myfxbook percentages readable (>= 11.5px)", m["minFont"])
    # a header may wrap to two lines on the longest labels (e.g. "Imbalance 100 · Heavy Short")
    ok(m["rowH"] <= 170 and m["h"] <= 3200, f"{label}: Myfxbook rows compact (tallest {m['rowH']}px, tab {m['h']}px)", (m["rowH"], m["h"]))
    pg.screenshot(path=str(out / f"{label}_myfxbook.png"))
    for i, el in enumerate(pg.query_selector_all("#p-rtl .mfxlist")):
        el.screenshot(path=str(out / f"{label}_myfxbook_list{i + 1}.png"))
    keys = pg.evaluate("() => [...document.querySelectorAll('#p-rtl a.mfxname')].map(a => a.getAttribute('href').slice(1))")
    for k in keys:
        pg.goto(url)
        pg.tap('label[for="tab-rtl"]')
        pg.locator(f'#p-rtl a.mfxname[href="#{k}"]').scroll_into_view_if_needed()
        pg.tap(f'#p-rtl a.mfxname[href="#{k}"]')
        pg.wait_for_timeout(150)
        c = pg.evaluate(f"""() => {{ const L = document.getElementById({json.dumps(k)});
            const card = L && L.querySelector('.mfxcard');
            return {{d: L ? getComputedStyle(L).display : null, card: !!card,
                     right: card ? Math.round(card.getBoundingClientRect().right) : 0,
                     open: [...document.querySelectorAll('.cotmodal')].filter(m => getComputedStyle(m).display !== 'none').length}}; }}""")
        ok(c["d"] == "block" and c["open"] == 1 and c["card"] and c["right"] <= m["W"] + 1,
           f"{label}: tapping {k} on the Myfxbook tab opens that report with its retail card", c)
        if k in ("cot50-XAU", "cot50-JPY", "cot50-ZB"):
            pg.locator(f"#{k} .mfxcard").screenshot(path=str(out / f"{label}_{k[6:]}_retail_card.png"))


def retail_expiry(b, url, out):
    """24 h cut-off in the browser: the page is static, so a snapshot that was fresh when the
    page was built must still disappear if the page is not rebuilt. Opens the page with the
    clock moved to 23 h and 25 h after the snapshot (no real failure needed) and checks that
    the readings hide, the 'unavailable - last successful update' note shows, and the
    Myfxbook credit stays - on the tab and on every report card."""
    from datetime import datetime, timedelta
    pg = b.new_page(viewport={"width": 390, "height": 844})
    pg.goto(url)
    ts = pg.evaluate("() => { const e = document.querySelector('[data-mfx-exp]'); return e ? e.getAttribute('data-mfx-exp') : null; }")
    pg.close()
    if not ts:
        ok(True, "Myfxbook 24 h cut-off: no live snapshot on this page (nothing to hide)")
        return
    t0 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    probe = """() => { const P = document.getElementById('p-rtl'), W = P && P.querySelector('[data-mfx-exp]');
        const d = e => e ? getComputedStyle(e).display : null;
        const cards = [...document.querySelectorAll('.mfxcard[data-mfx-exp]')];
        return {tabLive: d(W && W.querySelector('.mfxlive')), tabMsg: d(W && W.querySelector('.mfxexp')),
                msg: W && W.querySelector('.mfxexp') ? W.querySelector('.mfxexp').textContent : '',
                credit: !!(P && P.querySelector('.mfxsrc a[href="https://www.myfxbook.com/community/outlook"]'))
                        && d(P.querySelector('.mfxsrc')) !== 'none',
                cards: cards.length,
                cardsLive: cards.filter(c => d(c.querySelector('.mfxlive')) !== 'none').length,
                cardsMsg: cards.filter(c => d(c.querySelector('.mfxexp')) !== 'none').length,
                cardsCredit: cards.filter(c => { const a = c.querySelector(':scope > .mfxcl a[href="https://www.myfxbook.com/community/outlook"]');
                                                  return a && d(a.closest('p')) !== 'none'; }).length}; }"""
    for hours, gone in ((23, False), (25, True)):
        pg = b.new_page(viewport={"width": 390, "height": 844})
        pg.clock.install(time=t0 + timedelta(hours=hours))
        pg.goto(url)
        pg.evaluate("() => { const t = document.getElementById('tab-rtl'); if (t) t.checked = true; }")
        pg.wait_for_timeout(200)
        m = pg.evaluate(probe)
        tag = f"Myfxbook 24 h cut-off, page opened {hours} h after the snapshot"
        if gone:
            ok(m["tabLive"] == "none" and m["tabMsg"] == "block" and "Last successful update" in m["msg"],
               f"{tag}: tab readings hidden, 'unavailable - last successful update' shown", m)
            ok(m["cards"] > 0 and m["cardsLive"] == 0 and m["cardsMsg"] == m["cards"],
               f"{tag}: every report card's reading hidden, note shown ({m['cards']} cards)", m)
            pg.screenshot(path=str(out / "myfxbook_expired_phone.png"))
        else:
            ok(m["tabLive"] == "block" and m["tabMsg"] == "none" and m["cardsLive"] == m["cards"] and m["cardsMsg"] == 0,
               f"{tag}: readings still shown (Stale), no unavailable note", m)
        ok(m["credit"] and m["cardsCredit"] == m["cards"], f"{tag}: Myfxbook credit and link still shown on tab and cards", m)
        pg.close()


def divergence_phone(pg, url, label, out):
    """Step 2 on a phone: the 'Positioning divergences' scanner on the COT tab fits the
    width and each entry, tapped, opens that market's report; every report's divergence card
    fits the screen. Switched off: neither exists."""
    import os
    pg.goto(url)
    pg.tap('label[for="tab-cot"]')
    pg.wait_for_timeout(200)
    on = os.environ.get("MYFXBOOK_ENABLED") == "true"
    m = pg.evaluate("""() => { const S = document.querySelector('#p-cot .dvscan'), W = window.innerWidth;
        const cards = [...document.querySelectorAll('.dvcard')];
        return {scan: !!S, W, cards: cards.length, sw: document.documentElement.scrollWidth,
                over: S ? [...S.querySelectorAll('*')].filter(e => { const r = e.getBoundingClientRect();
                          return r.width > 0 && (r.right > W + 1 || r.left < -1); }).length : 0,
                links: S ? [...S.querySelectorAll('a.dvsc')].map(a => a.getAttribute('href').slice(1)) : [],
                first: S ? (document.querySelector('#p-cot > *') === S || document.querySelector('#p-cot').firstElementChild === S) : false}; }""")
    if not on:
        ok(not m["scan"] and m["cards"] == 0, f"{label}: divergence switched off with Myfxbook - no scanner, no card", m)
        return
    ok(m["scan"] and m["first"], f"{label}: 'Positioning divergences' scanner at the top of the COT tab", m)
    if not m["scan"]:
        return                    # e.g. the live page before Step 2 is deployed - nothing to drive
    ok(m["sw"] <= m["W"] + 1 and m["over"] == 0, f"{label}: divergence scanner fits the phone width", m)
    pg.locator("#p-cot .dvscan").screenshot(path=str(out / f"{label}_divergence_scanner.png"))
    for k in m["links"]:
        pg.goto(url)
        pg.tap('label[for="tab-cot"]')
        pg.locator(f'#p-cot .dvscan a[href="#{k}"]').scroll_into_view_if_needed()
        pg.tap(f'#p-cot .dvscan a[href="#{k}"]')
        pg.wait_for_timeout(150)
        c = pg.evaluate(f"""() => {{ const L = document.getElementById({json.dumps(k)});
            const c = L && L.querySelector('.dvcard');
            return {{d: L ? getComputedStyle(L).display : null, card: !!c, cls: c ? c.dataset.divClass : null,
                     open: [...document.querySelectorAll('.cotmodal')].filter(m => getComputedStyle(m).display !== 'none').length}}; }}""")
        ok(c["d"] == "block" and c["open"] == 1 and c["card"] and c["cls"] not in ("none", "unavailable"),
           f"{label}: tapping {k} in the divergence scanner opens that report with its divergence card", c)
    ok(m["cards"] == 16, f"{label}: a divergence card in all 16 reports ({m['cards']})", m["cards"])
    shot = (m["links"][:1] or []) + ["cot50-XAU", "cot50-JPY", "cot50-USD", "cot50-ZB"]
    for k in dict.fromkeys(shot):
        pg.goto(f"{url}#{k}")
        pg.wait_for_timeout(150)
        c = pg.evaluate(f"""() => {{ const L = document.getElementById({json.dumps(k)}), W = window.innerWidth;
            const c = L.querySelector('.dvcard'); const r = c.getBoundingClientRect();
            const fs = [...c.querySelectorAll('dd, li, .dvres')].map(e => parseFloat(getComputedStyle(e).fontSize));
            const over = [...c.querySelectorAll('*')].filter(e => {{ const q = e.getBoundingClientRect();
                return q.width > 0 && q.right > r.right + 1; }}).length;
            return {{left: Math.round(r.left), right: Math.round(r.right), W, over, h: Math.round(r.height),
                     minFont: fs.length ? Math.min(...fs) : 0, sw: [L.scrollWidth, L.clientWidth]}}; }}""")
        ok(c["left"] >= 0 and c["right"] <= c["W"] + 1 and c["over"] == 0 and c["sw"][0] <= c["sw"][1] + 1,
           f"{label}: {k} divergence card fits the screen", c)
        ok(c["minFont"] >= 11, f"{label}: {k} divergence card text readable (>= 11px)", c["minFont"])
        v = pg.evaluate(f"""() => {{ const c = document.querySelector('#{k} .dvcard');
            return [c.dataset.divClass, c.querySelector('.dvres').innerText.trim(), c.querySelector('.dvlive') ? c.querySelector('.dvlive').innerText : '']; }}""")
        want = {"unavailable": "Divergence unavailable", "none": "No significant divergence"}.get(v[0])
        ok(want is None or (v[1] == want and (v[0] == "unavailable" or "unavailable" not in v[2].lower())),
           f"{label}: {k} divergence card visibly reads '{want or v[1]}'", v[:2])
        pg.locator(f"#{k} .dvcard").scroll_into_view_if_needed()
        pg.locator(f"#{k} .dvcard").screenshot(path=str(out / f"{label}_{k[6:]}_divergence_card.png"))


def divergence_expiry(b, url, out):
    """The retail age limit in the browser (3 h by default, read from the page's
    data-div-max): open the page with the clock one minute before and one minute after it
    (no real failure needed)."""
    from datetime import datetime, timedelta
    pg = b.new_page(viewport={"width": 390, "height": 844})
    pg.goto(url)
    ts = pg.evaluate("() => { const e = document.querySelector('[data-div-ts]'); return e ? e.getAttribute('data-div-ts') : null; }")
    pg.close()
    if not ts:
        ok(True, "Divergence 3 h cut-off: no live classification on this page (nothing to hide)")
        return
    t0 = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    probe = """() => { const E = [...document.querySelectorAll('[data-div-ts]')];
        const d = e => e ? getComputedStyle(e).display : null;
        return {n: E.length, live: E.filter(e => d(e.querySelector('.dvlive')) !== 'none').length,
                note: E.filter(e => d(e.querySelector('.dvexp')) === 'block').length}; }"""
    pg = b.new_page(viewport={"width": 390, "height": 844})
    pg.goto(url)
    pg_max = pg.evaluate("() => +document.querySelector('[data-div-ts]').getAttribute('data-div-max')")
    pg.close()
    lim = int(pg_max)                          # divergence_config.RETAIL_MAX_AGE_MIN, as the page carries it
    for mins, gone in ((lim - 1, False), (lim + 1, True)):
        pg = b.new_page(viewport={"width": 390, "height": 844})
        pg.clock.install(time=t0 + timedelta(minutes=mins))
        pg.goto(url)
        pg.wait_for_timeout(150)
        m = pg.evaluate(probe)
        tag = f"Divergence 3 h cut-off, page opened {mins} min after the Myfxbook snapshot"
        if gone:
            ok(m["n"] > 0 and m["live"] == 0 and m["note"] == m["n"], f"{tag}: every classification hidden, 'unavailable' shown ({m['n']})", m)
            pg.evaluate("() => { const t = document.getElementById('tab-cot'); if (t) t.checked = true; }")
            if pg.query_selector("#p-cot .dvscan"):
                pg.locator("#p-cot .dvscan").screenshot(path=str(out / "divergence_scanner_expired.png"))
        else:
            ok(m["n"] > 0 and m["live"] == m["n"] and m["note"] == 0, f"{tag}: classifications still shown", m)
        pg.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--out", default="build-check")
    ap.add_argument("--chromium", default=None, help="explicit browser binary (optional)")
    ap.add_argument("--device-only", action="store_true",
                    help="only the real-phone checks (e.g. against the live page, for a before/after)")
    a = ap.parse_args()
    if a.device_only:
        return device_main(a)
    from playwright.sync_api import sync_playwright
    page_path = Path(a.page).resolve()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    html = page_path.read_text(encoding="utf-8")
    keys = re.findall(r'<td class="lbl"><a href="#(cot50-[^"]+)"', html)
    ok(len(keys) == 16, f"16 report links in the summary tables ({len(keys)})")
    shots = {"cot50-WTI", "cot50-JPY", "cot50-XAU", "cot50-ES"}
    url = page_path.as_uri()
    res = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=a.chromium) if a.chromium else pw.chromium.launch()
        for name, w, h in (("desktop", 1280, 900), ("phone", 390, 844)):
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=1.5)
            pg.goto(url)
            pg.evaluate("() => { const t = document.getElementById('tab-cot'); if (t) { t.checked = true; } }")
            pg.wait_for_timeout(300)
            sec = pg.query_selector(".crsec")
            ok(sec is not None and sec.is_visible(), f"{name}: COT report tables visible on the COT tab")
            m = pg.evaluate("""() => {
                const tw = [...document.querySelectorAll('.crsec .crtw')];
                return {page: [document.documentElement.scrollWidth, window.innerWidth],
                        tw: tw.map(t => [t.scrollWidth, t.clientWidth])}; }""")
            res[f"{name}_summary"] = m
            ok(m["page"][0] <= m["page"][1] + 1, f"{name}: no page-level sideways scroll", m["page"])
            if name == "desktop":
                ok(all(s <= c + 1 for s, c in m["tw"]), "desktop: both summary tables fit the width", m["tw"])
            else:
                ok(all(s > c for s, c in m["tw"]), "phone: summary tables scroll sideways in their box", m["tw"])
                pin = pg.evaluate("""() => { const t = document.querySelector('.crsec .crtw');
                    const l = t.querySelector('tbody td.lbl'); const x0 = l.getBoundingClientRect().left;
                    t.scrollLeft = 400; return [x0, l.getBoundingClientRect().left, t.scrollLeft]; }""")
                ok(pin[2] > 0 and abs(pin[0] - pin[1]) <= 2, "phone: pair column stays pinned while scrolling", pin)
            if sec:
                sec.screenshot(path=str(out / f"summary_{name}.png"))
            # A real tap on a summary link: the report opens over the page without moving it,
            # and Back closes it and returns to the same place.
            if keys:
                pg.evaluate("() => window.scrollTo(0, document.querySelector('.crsec').getBoundingClientRect().top + window.scrollY + 250)")
                y0 = pg.evaluate("() => Math.round(window.scrollY)")
                pg.click(f'.crsec a[href="#{keys[0]}"]')
                pg.wait_for_timeout(200)
                st = pg.evaluate(f"""() => ({{y: Math.round(window.scrollY),
                    d: getComputedStyle(document.getElementById({json.dumps(keys[0])})).display}})""")
                ok(st["d"] == "block" and abs(st["y"] - y0) <= 2,
                   f"{name}: tapping a summary row opens its report without moving the page", (y0, st))
                pg.go_back()
                pg.wait_for_timeout(250)
                st = pg.evaluate(f"""() => ({{y: Math.round(window.scrollY),
                    d: getComputedStyle(document.getElementById({json.dumps(keys[0])})).display}})""")
                ok(st["d"] == "none" and abs(st["y"] - y0) <= 2,
                   f"{name}: Back closes the report and returns to the same place", (y0, st))
                pg.goto(f"{url}#{keys[0]}")
                pg.wait_for_timeout(150)
                pg.click(f'#{keys[0]} a.mback')
                pg.wait_for_timeout(150)
                d = pg.evaluate(f"() => getComputedStyle(document.getElementById({json.dumps(keys[0])})).display")
                ok(d == "none", f"{name}: the report's back link closes it", d)
            # The existing per-instrument cards' "50 wk" links (Leveraged Funds / Managed Money
            # history) must still open their layer - the same layer as the Legacy report.
            drills = pg.evaluate("() => [...new Set([...document.querySelectorAll('a.drill')].map(a => a.getAttribute('href').slice(1)))]")
            for k in drills:
                pg.goto(f"{url}#{k}")
                pg.wait_for_timeout(100)
                st = pg.evaluate(f"""() => {{ const L = document.getElementById({json.dumps(k)});
                    if (!L) return null; const t = L.querySelector('.crlf table, .tw table');
                    return {{d: getComputedStyle(L).display, rows: t ? t.querySelectorAll('tbody tr').length : 0,
                             h: t ? Math.round(t.getBoundingClientRect().height) : 0}}; }}""")
                ok(bool(st) and st["d"] == "block" and st["rows"] > 0 and st["h"] > 0,
                   f"{name}: card '50 wk' link {k} opens its weekly history", st)
            res[f"{name}_card_links"] = len(drills)
            pg.goto(url)
            pg.evaluate("() => { const t = document.getElementById('tab-rtl'); if (t) t.checked = true; }")
            pg.wait_for_timeout(200)
            r = pg.evaluate("""() => { const P = document.getElementById('p-rtl');
                return P ? {d: getComputedStyle(P).display, rows: P.querySelectorAll('.mfxrow').length,
                            empty: !!P.querySelector('.mfxempty'),
                            sw: document.documentElement.scrollWidth, W: window.innerWidth} : null; }""")
            ok(bool(r) and r["d"] != "none" and (r["rows"] == 16 or r["empty"]) and r["sw"] <= r["W"] + 1,
               f"{name}: Myfxbook retail tab renders (16 markets or a no-data note), no sideways scroll", r)
            if r and r["d"] != "none":
                pg.screenshot(path=str(out / f"myfxbook_{name}.png"), full_page=False)
            for k in keys:
                pg.goto(f"{url}#{k}")
                pg.wait_for_timeout(120)
                info = pg.evaluate(f"""() => {{
                    const L = document.getElementById({json.dumps(k)});
                    if (!L) return null;
                    const cs = getComputedStyle(L);
                    const svg = L.querySelector('.crchart svg'); const r = svg ? svg.getBoundingClientRect() : null;
                    const boxes = [...L.querySelectorAll('.crrep .crscroll')];
                    const open = [...document.querySelectorAll('.cotmodal')].filter(m => getComputedStyle(m).display !== 'none').length;
                    const cell = L.querySelector('td.x[title]');
                    L.scrollTop = 400; const sc = L.scrollTop; L.scrollTop = 0;
                    return {{vis: cs.visibility, disp: cs.display, open, scrolled: sc,
                             tall: [L.scrollHeight, L.clientHeight],
                             hover: cell ? cell.getAttribute('title') : null,
                             over: [L.scrollWidth, L.clientWidth],
                             chart: r ? [Math.round(r.width), Math.round(r.height)] : null,
                             boxes: boxes.map(x => [x.scrollWidth, x.clientWidth, x.scrollHeight, x.clientHeight])}}; }}""")
                if not info:
                    ok(False, f"{name}: {k} layer exists")
                    continue
                ok(info["vis"] == "visible" and info["disp"] == "block", f"{name}: {k} opens from its link")
                ok(info["open"] == 1, f"{name}: {k} only the open report is laid out (closed ones display:none)", info["open"])
                ok(info["tall"][0] <= info["tall"][1] or info["scrolled"] > 0, f"{name}: {k} the report scrolls", info["tall"])
                ok(bool(info["hover"]) and ("percentile" in info["hover"] or "not ranked" in info["hover"]),
                   f"{name}: {k} hover text on Long/Short figures", info["hover"])
                ok(info["over"][0] <= info["over"][1] + 1, f"{name}: {k} no sideways page scroll", info["over"])
                ok(info["chart"] and info["chart"][0] > 200 and info["chart"][1] > 40, f"{name}: {k} chart drawn", info["chart"])
                ok(len(info["boxes"]) == 3, f"{name}: {k} three tables", info["boxes"])
                if name == "desktop":
                    ok(all(sw <= cw + 1 for sw, cw, _, _ in info["boxes"]), f"desktop: {k} tables fit the width", info["boxes"])
                else:
                    pin = pg.evaluate(f"""() => {{
                        const box = document.getElementById({json.dumps(k)}).querySelector('.crrep .crscroll');
                        box.scrollTop = 500; box.scrollLeft = 160;
                        const b = box.getBoundingClientRect(), th = box.querySelector('thead').getBoundingClientRect(),
                              lb = box.querySelector('tbody td.lbl').getBoundingClientRect();
                        return [Math.round(th.top - b.top), Math.round(lb.left - b.left), box.scrollTop, box.scrollLeft]; }}""")
                    ok(pin[2] > 0 and pin[3] > 0 and 0 <= pin[0] <= 3 and 0 <= pin[1] <= 3,
                       f"phone: {k} header and date column stay pinned while the table scrolls", pin)
                tap = pg.evaluate(f"""() => {{
                    const L = document.getElementById({json.dumps(k)});
                    const c = L.querySelector('td.x[title*="percentile"]') || L.querySelector('td.x');
                    if (!c) return null; c.focus();
                    return getComputedStyle(c, '::after').content; }}""")
                ok(bool(tap) and ("percentile" in tap or "not ranked" in tap), f"{name}: {k} tap shows the percentile", tap)
                pg.evaluate(f"() => document.getElementById({json.dumps(k)}).querySelector('.crpct').click()")
                lab = pg.evaluate(f"""() => {{ const ps = [...document.getElementById({json.dumps(k)}).querySelectorAll('td.x .pp')];
                    return [ps.length, ps.filter(p => getComputedStyle(p).display === 'block').length]; }}""")
                ok(lab[0] > 0 and lab[0] == lab[1], f"{name}: {k} 'Show percentiles' reveals every label", lab)
                pg.evaluate(f"() => document.getElementById({json.dumps(k)}).querySelector('.crpct').click()")
                if k in shots:
                    big = b.new_page(viewport={"width": w, "height": 2600 if name == "desktop" else 1700},
                                     device_scale_factor=1.25)
                    big.goto(f"{url}#{k}")
                    big.wait_for_timeout(250)
                    big.screenshot(path=str(out / f"{k}_{name}.png"))
                    big.close()
            pg.close()
        retail_expiry(b, url, out)
        divergence_expiry(b, url, out)
        run_devices(b, page_path, html, keys, out)
        b.close()
    (out / "layout.json").write_text(json.dumps({"passed": len(OKS), "failed": FAILS, "metrics": res}, indent=1))
    print(f"\ncheck_layout: {len(OKS)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


def run_devices(b, page_path, html, keys, out):
    """The page as a phone browser opens it, and as the Android app shows it (its wrapper)."""
    device_phone(b, page_path, keys, out, "phone_browser")
    wrapped = page_path.with_name(page_path.stem + ".app-wrapped.html")
    wrapped.write_text(app_wrap(html), encoding="utf-8")
    try:
        device_phone(b, wrapped, keys, out, "android_app")
    finally:
        wrapped.unlink()


def device_main(a):
    from playwright.sync_api import sync_playwright
    page_path = Path(a.page).resolve()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    html = page_path.read_text(encoding="utf-8")
    keys = re.findall(r'<td class="lbl"><a href="#(cot50-[^"]+)"', html)
    ok(len(keys) == 16, f"16 report links in the summary tables ({len(keys)})")
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=a.chromium) if a.chromium else pw.chromium.launch()
        try:
            run_devices(b, page_path, html, keys, out)
        except Exception as e:                       # noqa: BLE001 - a broken page is a result
            ok(False, f"device checks ran to the end ({type(e).__name__}: {str(e)[:160]})")
        b.close()
    (out / "layout.json").write_text(json.dumps({"passed": len(OKS), "failed": FAILS}, indent=1))
    print(f"\ncheck_layout (device): {len(OKS)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
