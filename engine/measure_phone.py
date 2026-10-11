"""Phone loading + interaction measurement of a built dashboard.html, optionally side by
side with the live production page. Measurement only - it never fails a build.

    python measure_phone.py dashboard.html [--baseline URL] [--out DIR]

Each page is served locally with gzip (as GitHub Pages serves it) and loaded in headless
Chromium emulating a mid-range phone: 390x844, 3x pixels, touch, CPU slowed 4x, network
"slow 4G" (150 ms RTT, 1.6 Mbit/s down) - the profile Lighthouse uses for mobile. Reported:
bytes on the wire and in memory, first paint, DOM ready, fully loaded, main-thread long
tasks, and the time for each interaction to reach the screen: opening the COT tab,
opening a 50-week report, tapping a shaded figure, turning on percentiles, closing.
Results: printed table + measure_phone.json.
"""
import argparse, gzip, http.server, json, socketserver, sys, tempfile, threading, urllib.request
from pathlib import Path

NET = {"offline": False, "latency": 150, "downloadThroughput": 1.6e6 / 8, "uploadThroughput": 750e3 / 8}
CPU = 4

INIT = """
window.__lt = [];
try { new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lt.push(e.duration); })
      .observe({type: 'longtask', buffered: true}); } catch (e) {}
window.__frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
"""


def serve(path):
    root = Path(path).resolve().parent
    name = Path(path).name

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0].lstrip("/") != name:
                return super().do_GET()
            body = (root / name).read_bytes()
            gz = "gzip" in self.headers.get("Accept-Encoding", "")
            if gz:
                body = gzip.compress(body, 6)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/{name}"


def measure(pw, path, label):
    srv, url = serve(path)
    raw = Path(path).stat().st_size
    wire = len(gzip.compress(Path(path).read_bytes(), 6))
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                        is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    cdp = ctx.new_cdp_session(pg)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", NET)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU})
    pg.add_init_script(INIT)
    pg.goto(url, wait_until="load", timeout=180000)
    nav = pg.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0];
        const p = performance.getEntriesByName('first-contentful-paint')[0];
        return {fcp: p ? p.startTime : null, dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd,
                nodes: document.getElementsByTagName('*').length}; }""")
    res = {"label": label, "raw_kb": round(raw / 1024), "wire_kb": round(wire / 1024), **nav}

    def timed(js):
        return pg.evaluate(f"async () => {{ const t = performance.now(); {js}; await window.__frame(); "
                           f"return performance.now() - t; }}")
    res["open_cot_tab_ms"] = timed("const t2 = document.getElementById('tab-cot'); if (t2) t2.click()")
    has = pg.evaluate("() => !!document.getElementById('cot50-WTI')")
    if has:
        res["open_report_ms"] = timed("location.hash = '#cot50-WTI'")
        res["tap_figure_ms"] = timed("document.querySelector('#cot50-WTI td.x').focus()")
        res["show_percentiles_ms"] = timed("document.querySelector('#cot50-WTI .crpct').click()")
        res["close_report_ms"] = timed("location.hash = '#p-cot'")
    pg.wait_for_timeout(500)
    lt = pg.evaluate("() => window.__lt")
    res["long_tasks"] = len(lt)
    res["long_task_ms"] = round(sum(lt))
    res["heap_mb"] = round(pg.evaluate("() => (performance.memory ? performance.memory.usedJSHeapSize : 0)") / 2**20, 1)
    b.close()
    srv.shutdown()
    return {k: (round(v) if isinstance(v, float) else v) for k, v in res.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--baseline", default=None, help="URL of the live page to compare with")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    rows = []
    with sync_playwright() as pw:
        if a.baseline:
            try:
                tmp = Path(tempfile.mkdtemp()) / "dashboard.html"
                req = urllib.request.Request(a.baseline, headers={"User-Agent": "fxstrength-measure"})
                tmp.write_bytes(urllib.request.urlopen(req, timeout=60).read())
                rows.append(measure(pw, tmp, "live production page"))
            except Exception as e:                       # noqa: BLE001
                print(f"baseline not measured: {type(e).__name__}: {e}")
        rows.append(measure(pw, a.page, "this build"))
    keys = ["raw_kb", "wire_kb", "fcp", "dcl", "load", "nodes", "long_tasks", "long_task_ms",
            "open_cot_tab_ms", "open_report_ms", "tap_figure_ms", "show_percentiles_ms",
            "close_report_ms", "heap_mb"]
    names = {"raw_kb": "page size (KB)", "wire_kb": "download, gzip (KB)", "fcp": "first paint (ms)",
             "dcl": "DOM ready (ms)", "load": "fully loaded (ms)", "nodes": "DOM elements",
             "long_tasks": "long main-thread tasks", "long_task_ms": "time in long tasks (ms)",
             "open_cot_tab_ms": "open COT tab (ms)", "open_report_ms": "open a 50-week report (ms)",
             "tap_figure_ms": "tap a figure (ms)", "show_percentiles_ms": "show percentiles (ms)",
             "close_report_ms": "close report (ms)", "heap_mb": "JS heap (MB)"}
    print(f"\nPhone profile: 390x844 @3x, CPU {CPU}x slower, 150 ms RTT, 1.6 Mbit/s\n")
    print(f"{'':28}" + "".join(f"{r['label']:>24}" for r in rows))
    for k in keys:
        print(f"{names[k]:28}" + "".join(f"{str(r.get(k, '-')):>24}" for r in rows))
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "measure_phone.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
