#!/usr/bin/env python3
"""Read-only verification probe for the PR #14 economic-calendar data sources.

Runs ONLY on the throwaway `verify/econ-endpoints` branch, from GitHub Actions
(the sandbox cannot reach these hosts). It makes a few GET/POST calls to each
official source, records what actually comes back, and writes a redacted report
to verify_report/. It writes nothing to production, touches no scoring code and
deploys nothing.

Honesty rules (user conditions):
  * record HTTP status, latency, parse result, key fields, and the LATEST data
    date (a proxy for "how fresh / how quickly actuals appear");
  * record errors, missing fields and unsupported sources plainly;
  * never print or commit an API key - every URL is redacted before it is stored.
"""
import json, os, time, datetime as dt, urllib.request, urllib.error, urllib.parse, re
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "verify_report"
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "fxstrength-econ-probe/1.0 (verification; contact via GitHub)"}
TIMEOUT = 25

FRED_KEY = os.environ.get("FRED_API_KEY", "")
EIA_KEY = os.environ.get("EIA_API_KEY", "")
ESTAT_KEY = os.environ.get("ESTAT_APP_ID", "")
STATSNZ_KEY = os.environ.get("STATSNZ_API_KEY", "")

# Secrets to scrub from any stored URL / error text, longest first.
_SECRETS = [v for v in sorted({FRED_KEY, EIA_KEY, ESTAT_KEY, STATSNZ_KEY}, key=len, reverse=True) if len(v) >= 6]


def redact(s):
    if not s:
        return s
    s = str(s)
    for v in _SECRETS:
        s = s.replace(v, "REDACTED")
    # also blank common key params even if the value wasn't in env
    s = re.sub(r"(api_key|appId|apikey|subscription-key)=[^&\s]+", r"\1=REDACTED", s, flags=re.I)
    return s


def _latest_date(obj):
    """Best-effort: find the most recent ISO-ish date anywhere in a small payload."""
    found = []
    def walk(x, depth=0):
        if depth > 6:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", v):
                    found.append(v)
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x[:50]:
                walk(v, depth + 1)
        elif isinstance(x, str) and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", x):
            found.append(x)
    walk(obj)
    return max(found) if found else None


def hit(url, method="GET", data=None, headers=None, expect="json"):
    """One request. Returns a redacted result dict; never raises."""
    h = dict(UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = data if isinstance(data, (bytes, bytearray)) else json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    t0 = time.time()
    rec = {"url": redact(url), "method": method}
    try:
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            rec["status"] = r.status
            rec["ms"] = round((time.time() - t0) * 1000)
            rec["bytes"] = len(raw)
            text = raw.decode("utf-8", "replace")
            if expect == "json":
                try:
                    obj = json.loads(text)
                    rec["parsed"] = "json-ok"
                    rec["top_keys"] = list(obj.keys())[:12] if isinstance(obj, dict) else f"list[{len(obj)}]"
                    rec["latest_date"] = _latest_date(obj)
                    rec["sample"] = redact(text[:280])
                except Exception as e:                                      # noqa: BLE001
                    rec["parsed"] = f"not-json ({type(e).__name__})"
                    rec["sample"] = redact(text[:280])
            else:  # csv / text
                lines = [ln for ln in text.splitlines() if ln.strip()]
                rec["parsed"] = "text-ok"
                rec["lines"] = len(lines)
                rec["sample"] = redact("\n".join(lines[:4])[:320])
                rec["latest_date"] = _latest_date(lines[-6:]) if lines else None
    except urllib.error.HTTPError as e:
        rec["status"] = e.code
        rec["ms"] = round((time.time() - t0) * 1000)
        try:
            rec["error_body"] = redact(e.read().decode("utf-8", "replace")[:240])
        except Exception:                                                   # noqa: BLE001
            rec["error_body"] = ""
    except Exception as e:                                                  # noqa: BLE001
        rec["status"] = None
        rec["ms"] = round((time.time() - t0) * 1000)
        rec["error"] = redact(f"{type(e).__name__}: {e}")[:240]
    return rec


# Each source: (currency/market, label, what it gives, a callable returning a result dict,
#               key_status). key_status documents whether a key is needed and if we hold one.
def _fred(path, **params):
    params["api_key"] = FRED_KEY
    params.setdefault("file_type", "json")
    return "https://api.stlouisfed.org/fred/" + path + "?" + urllib.parse.urlencode(params)


def statcan():
    return hit("https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods",
               method="POST", data=[{"vectorId": 41690973, "latestN": 3}])  # CPI all-items Canada


def bfs_px(table):
    # PX-Web: GET returns table metadata (confirms the table id + variables).
    return hit(f"https://www.pxweb.bfs.admin.ch/api/v1/en/{table}", expect="text")


PROBES = [
    # ===== GBP (ONS v0 API retired 2024; try the website-JSON route, which is keyless) =====
    ("GBP", "ONS website CPI annual rate (D7G7/MM23)", "actual + previous",
     lambda: hit("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7g7/mm23/data"),
     "keyless (website JSON)"),
    ("GBP", "ONS website CPIH annual rate (L55O/MM23)", "actual + previous",
     lambda: hit("https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/l55o/mm23/data"),
     "keyless (website JSON)"),
    ("GBP", "ONS website unemployment rate (MGSX/LMS)", "actual + previous",
     lambda: hit("https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/"
                 "employmentandemployeetypes/timeseries/mgsx/lms/data"),
     "keyless (website JSON)"),
    ("GBP", "ONS website GDP MoM (ECY2/QNA?)", "actual + previous",
     lambda: hit("https://www.ons.gov.uk/economy/grossdomesticproductgdp/timeseries/ecy2/qna/data"),
     "keyless (website JSON)"),
    ("GBP", "ONS release calendar JSON", "scheduled releases",
     lambda: hit("https://www.ons.gov.uk/releasecalendar/data?size=10&query="),
     "keyless (website JSON)"),
    ("GBP", "ONS new beta API datasets list", "dataset discovery",
     lambda: hit("https://api.beta.ons.gov.uk/v1/datasets?limit=3"),
     "keyless (new API)"),
    # ===== CHF (find the right BFS CPI table + a working SNB policy-rate cube) =====
    ("CHF", "BFS CPI table meta (px-x-0501000000_101)", "actual + previous",
     lambda: bfs_px("px-x-0501000000_101/px-x-0501000000_101.px"),
     "keyless"),
    ("CHF", "BFS CPI table meta (px-x-0501000000_102)", "actual + previous",
     lambda: bfs_px("px-x-0501000000_102/px-x-0501000000_102.px"),
     "keyless"),
    ("CHF", "BFS PX-Web price-stats db node (05)", "navigate to CPI",
     lambda: bfs_px("px-x-0501000000_101"),
     "keyless"),
    ("CHF", "SNB cube snbgwdjba (rates)", "central-bank rate",
     lambda: hit("https://data.snb.ch/api/cube/snbgwdjba/data/json/en"),
     "keyless"),
    ("CHF", "SNB cube snboffzia (policy rate)", "central-bank rate",
     lambda: hit("https://data.snb.ch/api/cube/snboffzia/data/json/en"),
     "keyless"),
    ("CHF", "SNB cube zimoma (money-market rates)", "central-bank rate",
     lambda: hit("https://data.snb.ch/api/cube/zimoma/data/json/en"),
     "keyless"),
    # ===== NZD (keyless options are thin; confirm whether a free key is unavoidable) =====
    ("NZD", "Stats NZ OData v1 root", "actual (needs free key?)",
     lambda: hit("https://api.stats.govt.nz/opendata/v1/", expect="text"),
     "likely needs free subscription key"),
    ("NZD", "RBNZ OCR graph CSV (b2 daily)", "central-bank rate",
     lambda: hit("https://www.rbnz.govt.nz/-/media/project/sites/rbnz/files/statistics/"
                 "series/b/b2/hb2-daily.csv", expect="csv"),
     "keyless if reachable"),
    ("NZD", "RBNZ statistics key graph data", "central-bank rate",
     lambda: hit("https://www.rbnz.govt.nz/statistics/key-graphs/key-graph-ocr", expect="text"),
     "keyless (HTML)"),
]


def main():
    now = dt.datetime.now(dt.timezone.utc)
    results = []
    for ccy, label, gives, fn, keystat in PROBES:
        print(f"probing {ccy}: {label} ...")
        r = fn()
        ok = isinstance(r.get("status"), int) and 200 <= r["status"] < 300 and "not-json" not in str(r.get("parsed", ""))
        results.append({"currency": ccy, "label": label, "provides": gives,
                        "key_status": keystat, "ok": ok, **r})

    report = {"generated_utc": now.isoformat(), "timeout_s": TIMEOUT,
              "note": "Read-only verification probe. Keys redacted. No production impact.",
              "keys_available": {"FRED": bool(FRED_KEY), "EIA": bool(EIA_KEY),
                                 "e-Stat": bool(ESTAT_KEY), "StatsNZ": bool(STATSNZ_KEY)},
              "results": results}
    (OUT / "econ_endpoints.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # human-readable summary
    lines = [f"# Economic-calendar endpoint probe — {now:%Y-%m-%d %H:%M UTC}", "",
             f"Keys present: FRED={bool(FRED_KEY)} EIA={bool(EIA_KEY)} "
             f"e-Stat={bool(ESTAT_KEY)} StatsNZ={bool(STATSNZ_KEY)}", "",
             "| Ccy | Source | Provides | OK | HTTP | ms | Latest | Key |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['currency']} | {r['label']} | {r['provides']} | "
                     f"{'✅' if r['ok'] else '❌'} | {r.get('status')} | {r.get('ms')} | "
                     f"{r.get('latest_date') or '—'} | {r['key_status']} |")
    lines += ["", "## Per-source detail", ""]
    for r in results:
        lines.append(f"### {r['currency']} — {r['label']}")
        for k in ("url", "status", "ms", "parsed", "top_keys", "lines", "latest_date",
                  "sample", "error", "error_body"):
            if k in r and r[k] not in (None, ""):
                lines.append(f"- **{k}**: {r[k]}")
        lines.append("")
    (OUT / "econ_endpoints.md").write_text("\n".join(lines), encoding="utf-8")

    passed = sum(1 for r in results if r["ok"])
    print(f"\nPROBE DONE: {passed}/{len(results)} endpoints returned usable data.")


if __name__ == "__main__":
    main()
