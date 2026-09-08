"""US inflation expectations and true real yields, from the St. Louis Fed (FRED).

WHAT THIS ADDS THAT THE DESK DID NOT HAVE. Everything else here is a nominal yield or a
released inflation print - all backward-looking. FRED's breakevens are the one series that
says what the market EXPECTS inflation to be, which is the number the Fed itself watches and
the thing that actually moves the dollar when it shifts. That is the prize, not the real yield.

  DFII10   10-year Treasury inflation-indexed (TIPS) yield - the TRUE real yield
  DFII5    5-year TIPS yield
  T10YIE   10-year breakeven - market-implied average inflation over 10 years
  T5YIE    5-year breakeven
  T5YIFR   5-year, 5-year forward inflation expectation - the Fed's preferred gauge of
           whether long-run expectations are anchored

WHY THIS DOES NOT REPLACE THE REAL-YIELD LEG IN THE SCORE. FRED is US-only. yields.py ranks
the real yield ACROSS eight currencies, and swapping USD onto a true TIPS yield while the
other seven stay on nominal-minus-CPI would make that column compare two different quantities
and quietly bias the carry ranking toward or against the dollar. So the scoring stays on the
consistent ex-post basis, and these land on the Yields tab as US context. The gap between the
two - market real yield vs ex-post real yield - is itself worth seeing, and is reported.

THE KEY. FRED needs a free API key. It is a credential, this repo is PUBLIC, and it is never
committed. Resolution order:
    1. environment variable  FRED_API_KEY      (how CI supplies it, from a repo secret)
    2. file                  data/fred_key.txt (local only - gitignored)
With no key this writes a `status: "no key"` file and returns cleanly; the panel then shows
nothing rather than breaking. Note it is the KEYED host that works - an earlier attempt used
fred.stlouisfed.org/graph/fredgraph.csv, which times out; api.stlouisfed.org does not.

Writes data/fred.json.
"""
import json, os, urllib.parse, urllib.request, datetime as dt
from config import DATA

OUT = DATA / "fred.json"
KEY_FILE = DATA / "fred_key.txt"
API = ("https://api.stlouisfed.org/fred/series/observations"
       "?series_id={sid}&api_key={key}&file_type=json"
       "&observation_start={start}&sort_order=desc&limit=400")

SERIES = [
    ("DFII10", "10y TIPS (real)",        "Inflation-indexed 10-year Treasury - the true real yield"),
    ("DFII5",  "5y TIPS (real)",         "Inflation-indexed 5-year Treasury"),
    ("T10YIE", "10y breakeven",          "Market-implied average inflation over the next 10 years"),
    ("T5YIE",  "5y breakeven",           "Market-implied average inflation over the next 5 years"),
    ("T5YIFR", "5y5y forward",           "5-year, 5-year forward inflation expectation - the Fed's "
                                         "preferred gauge of whether long-run expectations are anchored"),
]
LOOKBACK_DAYS = 400          # enough for a 1-year change and a 20-day move


def api_key():
    k = (os.environ.get("FRED_API_KEY") or "").strip()
    if k:
        return k, "env"
    if KEY_FILE.exists():
        try:
            k = KEY_FILE.read_text(encoding="utf-8").strip()
            if k:
                return k, "file"
        except Exception:                                        # noqa: BLE001
            pass
    return None, None


def _observations(sid, key):
    """[(date, value)] newest first, missing points (FRED writes '.') dropped."""
    start = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    url = API.format(sid=sid, key=urllib.parse.quote(key), start=start)
    raw = urllib.request.urlopen(url, timeout=30).read()
    obs = json.loads(raw.decode("utf-8", "replace")).get("observations") or []
    out = []
    for o in obs:
        v = o.get("value")
        if v in (None, ".", ""):
            continue
        try:
            out.append((o["date"], float(v)))
        except ValueError:
            continue
    return out


def _move(rows, back):
    """Change from `back` observations ago (rows are newest first)."""
    if len(rows) <= back:
        return None
    return round(rows[0][1] - rows[back][1], 3)


def build():
    key, src = api_key()
    if not key:
        out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "status": "no key", "series": {},
               "note": ("Set FRED_API_KEY (a repo secret in CI) or write the key to "
                        "data/fred_key.txt. Free from fred.stlouisfed.org.")}
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print("  no FRED API key (env FRED_API_KEY or data/fred_key.txt) - panel stays hidden")
        return out

    series, failed = {}, []
    for sid, label, blurb in SERIES:
        try:
            rows = _observations(sid, key)
            if not rows:
                raise RuntimeError("no observations")
            series[sid] = {"label": label, "blurb": blurb,
                           "last": rows[0][1], "asof": rows[0][0],
                           "chg_20": _move(rows, 20), "chg_250": _move(rows, 250),
                           "n": len(rows)}
        except Exception as e:                                   # noqa: BLE001
            failed.append(f"{sid} ({type(e).__name__})")
    if failed:
        print(f"  dropped: {', '.join(failed)}")

    # The comparison worth having: the market's real yield against the desk's ex-post proxy.
    # A wide gap means realised inflation and expected inflation disagree, which is exactly
    # when the ex-post figure is misleading.
    ex_post = None
    try:
        y = json.loads((DATA / "yields.json").read_text(encoding="utf-8"))
        ex_post = ((y.get("currencies") or {}).get("USD") or {}).get("real10")
    except Exception:                                            # noqa: BLE001
        pass
    market = (series.get("DFII10") or {}).get("last")
    gap = (round(market - ex_post, 3) if market is not None and ex_post is not None else None)

    out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "status": "ok", "key_source": src, "series": series,
           "us_real_market": market, "us_real_ex_post": ex_post, "real_gap": gap}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    if r.get("status") != "ok":
        raise SystemExit(f"  {r.get('note', '')}")
    print(f"  key from {r['key_source']}")
    for sid, _, _ in SERIES:
        d = r["series"].get(sid)
        if not d:
            continue
        c20 = f"{d['chg_20']:+.2f}" if d["chg_20"] is not None else "  n/a"
        c250 = f"{d['chg_250']:+.2f}" if d["chg_250"] is not None else "  n/a"
        print(f"  {sid:7} {d['label']:18} {d['last']:>6.2f}%   20d {c20}   1y {c250}   ({d['asof']})")
    if r.get("real_gap") is not None:
        print(f"\n  US 10y real yield: market (TIPS) {r['us_real_market']:.2f}% vs ex-post "
              f"{r['us_real_ex_post']:.2f}% - gap {r['real_gap']:+.2f}pp")
