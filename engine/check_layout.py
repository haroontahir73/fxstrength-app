"""Desktop + phone layout check of a BUILT dashboard.html's COT report tables and the 16
50-week reports, with screenshots. Needs Playwright (CI installs it; not a pipeline dep).

    python check_layout.py dashboard.html --out build-check

Checks at 1280x900 and 390x844 (phone): the summary tables fit the desktop width and
scroll sideways on a phone with the first column pinned and no page-level sideways scroll;
every report opens from its link, is visible, has a drawn chart, keeps its header and date
column pinned while its tables scroll, shows the percentile when a shaded figure is tapped,
and reveals every percentile label with "Show percentiles". Writes layout.json and PNGs to
--out; exit 1 on any failure.
"""
import argparse, json, re, sys
from pathlib import Path

FAILS, OKS = [], []


def ok(cond, what, detail=""):
    (OKS if cond else FAILS).append(what)
    if not cond:
        print(f"  FAIL {what}" + (f"  ({detail})" if detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--out", default="build-check")
    ap.add_argument("--chromium", default=None, help="explicit browser binary (optional)")
    a = ap.parse_args()
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
        b.close()
    (out / "layout.json").write_text(json.dumps({"passed": len(OKS), "failed": FAILS, "metrics": res}, indent=1))
    print(f"\ncheck_layout: {len(OKS)} passed, {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
