"""Tests for the COT x Myfxbook retail divergence (divergence.py, divergence_config.py).

    python test_divergence.py

Stdlib only, offline, fixtures only: a synthetic CFTC Legacy store and a synthetic Myfxbook
snapshot in temp dirs - the live data/ directory is never read or written. Covers the
orientation of all 16 markets (direct, inverted USDJPY / USDCHF / USDCAD, metals, copper,
oil, the three indexes, USD/USDX, ZB), every V1 class and its boundaries, retail 50/50 and
0/100, the 100-position minimum, stale / missing / malformed retail data, missing or
malformed COT data, uncertain symbol mapping, momentum, the card and the scanner, the
switch, and that nothing feeds scores or alerts.
"""
import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cot_table as ct                                            # noqa: E402
import divergence as dv                                           # noqa: E402
import divergence_config as cfg                                   # noqa: E402
import myfxbook as mf                                             # noqa: E402

PASS, FAIL = [], []
UTC = dt.timezone.utc
ON = {"MYFXBOOK_ENABLED": "true"}
NOW = dt.datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
LATEST = dt.date(2026, 10, 6)                                     # a Tuesday, 8 days before NOW
CODE = {r[3]: r[1] for r in ct.ALL_ROWS}
SYMBOL = {"USD": "USDX", "AUD": "AUDUSD", "CAD": "USDCAD", "CHF": "USDCHF", "EUR": "EURUSD",
          "GBP": "GBPUSD", "JPY": "USDJPY", "NZD": "NZDUSD", "XAU": "XAUUSD", "XAG": "XAGUSD",
          "HG": "COPPER", "WTI": "XTIUSD", "ES": "US500", "NQ": "NAS100", "YM": "US30"}


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def dates(n=60, latest=LATEST):
    return [(latest - dt.timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


def series(rank, n=50, step=1000, prev=None):
    """Normalized nets, oldest first: n distinct values 0..n-1 (x step) with the latest at
    `rank` -> its mid-rank percentile is (rank + 0.5) / n x 100. `prev` sets last week's."""
    rest = [i * step for i in range(n) if i != rank]
    if prev is not None:
        rest.remove(prev)
        rest.append(prev)
    return rest + [rank * step]


def pct_of(rank, n=50):
    return (rank + 0.5) / n * 100


def make_store(nets=None, n_weeks=60, latest=LATEST, extra=None):
    """nets: key -> normalized nets (oldest first, the last = latest report). Stored as the
    FUTURE's NC long / short, i.e. the orientation is applied in reverse here, so the test
    proves the engine applies it."""
    nets = nets or {}
    ds = dates(n_weeks, latest)
    store = ct.empty_store()
    for key, code in CODE.items():
        s = nets.get(key, series(25))
        s = [None] * (n_weeks - len(s)) + list(s) if len(s) < n_weeks else s[-n_weeks:]
        weeks = {}
        for d, v in zip(ds, s):
            if v is None:
                continue
            fut = v * dv.sign(key)                     # normalized -> futures terms
            ncl, ncs = 600000 + max(fut, 0), 600000 + max(-fut, 0)
            weeks[d] = [2000000, ncl, ncs, 0, 0, 0, 0, 0]
        store["contracts"][code] = {"name": key, "weeks": weeks}
    if extra:
        extra(store)
    return store


def mkt(sym, lp, sp, lpos=1500, spos=900):
    return {"symbol": sym, "longPercentage": lp, "shortPercentage": sp, "longVolume": 1.0,
            "shortVolume": 1.0, "longPositions": lpos, "shortPositions": spos,
            "avgLongPrice": 1.0, "avgShortPrice": 1.0, "candidates": [sym]}


def make_snap(markets=None, age_min=10):
    ts = (NOW - dt.timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = {k: mkt(s, 50, 50) for k, s in SYMBOL.items()}
    m.update(markets or {})
    return {"fetched_at": ts, "markets": {k: v for k, v in m.items() if v is not None}}


def ev(key, store=None, snap=None, now=NOW, on=True):
    return dv.evaluate(key, store if store is not None else make_store(), snap if snap is not None else make_snap(), now, on)


def write_dir(store, snap):
    d = Path(tempfile.mkdtemp(prefix="fxdiv-"))
    (d / ct.OUT_NAME).write_text(json.dumps(store))
    (d / mf.OUT_NAME).write_text(json.dumps(snap))
    return d


# ---------------------------------------------------------------- orientation
def t_orientation_table():
    g = "orientation"
    o = cfg.ORIENTATION
    check(g, "all 16 dashboard markets in the table, nothing else", set(o) == set(CODE) and len(o) == 16, sorted(o))
    want = {"USD": ("USDX", "direct"), "AUD": ("AUDUSD", "direct"), "CAD": ("USDCAD", "inverted"),
            "CHF": ("USDCHF", "inverted"), "EUR": ("EURUSD", "direct"), "GBP": ("GBPUSD", "direct"),
            "JPY": ("USDJPY", "inverted"), "NZD": ("NZDUSD", "direct"), "XAU": ("XAUUSD", "direct"),
            "XAG": ("XAGUSD", "direct"), "HG": ("COPPER", "direct"), "WTI": ("XTIUSD", "direct"),
            "ES": ("US500", "direct"), "NQ": ("NAS100", "direct"), "YM": ("US30", "direct")}
    for k, (sym, orient) in want.items():
        check(g, f"{k}: COT {orient}, retail symbol {sym} accepted",
              o[k]["cot_orientation"] == orient and sym in o[k]["retail_symbols"], o[k])
    check(g, "WTI accepts XTIUSD and USOIL only", o["WTI"]["retail_symbols"] == ("XTIUSD", "USOIL"))
    check(g, "USD/USDX reviewed and documented: direct, USDX only (DXY not accepted)",
          o["USD"]["retail_symbols"] == ("USDX",) and "Reviewed" in o["USD"]["note"] and "DXY" in o["USD"]["note"])
    check(g, "ZB: no retail symbol at all", o["ZB"]["retail_symbols"] == ())
    rev = {r[3]: r[2] for r in ct.ALL_ROWS}
    check(g, "inverted exactly where the COT tables swap long/short (CAD, CHF, JPY)",
          {k for k in o if o[k]["cot_orientation"] == "inverted"} == {k for k, r in rev.items() if r} == {"CAD", "CHF", "JPY"})
    check(g, "every accepted retail symbol is one Step 1 can pick for that market",
          all(set(o[k]["retail_symbols"]) <= set(mf.MARKETS[k]) for k in o), {k: o[k]["retail_symbols"] for k in o})
    check(g, "inverted markets show a USD/xxx pair; direct FX markets an xxx/USD pair",
          all(o[k]["shown"].startswith("USD") for k in ("CAD", "CHF", "JPY"))
          and all(o[k]["shown"].endswith("USD") for k in ("AUD", "EUR", "GBP", "NZD", "XAU", "XAG")))
    src = (Path(__file__).parent / "divergence.py").read_text()
    check(g, "orientation never inferred from symbol names at runtime",
          not re.search(r"startswith\(|endswith\(|\[:3\]|\[3:\]", src.split("# ---------------------------------------------------------------- rendering")[0]))


def t_orientation_values():
    """Bullish futures positioning must read bullish for direct markets and bearish for the
    inverted USD/xxx pairs - through the whole engine, for every market."""
    g = "orientation-values"
    for k in CODE:
        fut_bull = [i * 1000 for i in range(50)]          # FUTURES net rising to its 50-wk high
        store = ct.empty_store()
        ds = dates(50)
        for kk, code in CODE.items():
            store["contracts"][code] = {"name": kk, "weeks": {
                d: [2000000, 600000 + max(v, 0), 600000 + max(-v, 0), 0, 0, 0, 0, 0] for d, v in zip(ds, fut_bull)}}
        c = dv.cot_side(k, store, NOW)
        inverted = cfg.ORIENTATION[k]["cot_orientation"] == "inverted"
        want = pct_of(0) if inverted else pct_of(49)
        check(g, f"{k}: futures at a 50-week LONG extreme -> {cfg.ORIENTATION[k]['shown']} "
                 f"{'bearish (1st pct)' if inverted else 'bullish (99th pct)'}",
              abs(c["pct"] - want) < 1e-9 and c["direction"] == ("Bearish" if inverted else "Bullish"), c)
        check(g, f"{k}: normalized net = futures net x {dv.sign(k):+d}", c["net"] == dv.sign(k) * 49000, c["net"])
    # the engine's normalized net equals the 50-week report's pair-terms Non-Commercial net
    store = make_store({k: series(30) for k in CODE})
    latest, _ = ct.pick_weeks(store)
    for k in ("JPY", "CHF", "CAD", "EUR"):
        rep = ct.report(store, CODE[k], cfg.ORIENTATION[k]["cot_orientation"] == "inverted", latest)
        c = dv.cot_side(k, store, NOW)
        check(g, f"{k}: same net as the 50-week report's Non-Commercial net", c["net"] == rep["rows"][0]["nc"]["net"],
              (c["net"], rep["rows"][0]["nc"]["net"]))
    for k in ("JPY", "CHF", "CAD"):
        r = ev(k, make_store({k: series(46)}), make_snap({k: mkt(SYMBOL[k], 20, 80)}))
        check(g, f"inverted {cfg.ORIENTATION[k]['shown']}: JPY/CHF/CAD futures SHORT extreme + retail short "
                 f"-> Strong Bullish {cfg.ORIENTATION[k]['shown']}", r["cls"] == "strong_bull", r["cls"])


def t_markets():
    g = "markets"
    for k in CODE:
        if k == "ZB":
            continue
        r = ev(k, make_store({k: series(45)}), make_snap({k: mkt(SYMBOL[k], 25, 75)}))
        check(g, f"{k} ({cfg.ORIENTATION[k]['shown']}): COT 91st + retail 75% short -> Strong Bullish",
              r["cls"] == "strong_bull" and abs(r["cot"]["pct"] - 91) < 1e-9, (r["cls"], r["reasons"]))
        r = ev(k, make_store({k: series(4)}), make_snap({k: mkt(SYMBOL[k], 80, 20)}))
        check(g, f"{k}: COT 9th + retail 80% long -> Strong Bearish", r["cls"] == "strong_bear", (r["cls"], r["reasons"]))
    r = ev("WTI", make_store({"WTI": series(45)}), make_snap({"WTI": mkt("USOIL", 25, 75)}))
    check(g, "WTI via USOIL works too", r["cls"] == "strong_bull")
    r = ev("ZB", make_store({"ZB": series(45)}))
    check(g, "ZB: always unavailable - no Myfxbook data", r["cls"] == "unavailable" and
          r["reasons"] == ["Myfxbook has no data for this market"], r["reasons"])


def t_rules():
    g = "rules"
    cases = [  # (rank -> pct, long, short, want)
        (42, 30, 70, "strong_bull"),   # 85.0 exactly, 70% short exactly
        (42, 31, 69, "bull"),          # 85 but retail 69% short -> Bullish
        (41, 20, 80, "bull"),          # 83 -> not strong
        (35, 40, 60, "bull"),          # 71, 60% short exactly
        (34, 30, 70, "none"),          # 69 -> no
        (35, 41, 59, "none"),          # 71 but 59% short
        (7, 70, 30, "strong_bear"),    # 15.0 exactly, 70% long exactly
        (8, 70, 30, "bear"),           # 17 -> moderate
        (14, 60, 40, "bear"),          # 29, 60% long exactly
        (15, 60, 40, "none"),          # 31 -> no
        (14, 59, 41, "none"),
        (49, 70, 30, "none"),          # COT bullish extreme but retail LONG -> agreement, not divergence
        (0, 30, 70, "none"),           # COT bearish extreme but retail short
        (25, 50, 50, "none"),          # middle
    ]
    for rank, lp, sp, want in cases:
        r = ev("EUR", make_store({"EUR": series(rank)}), make_snap({"EUR": mkt("EURUSD", lp, sp)}))
        check(g, f"COT {pct_of(rank):g} pct, retail {lp}/{sp} -> {want}", r["cls"] == want, (r["cls"], r.get("reasons")))
    check(g, "classify() boundaries", [dv.classify(p, l, s)[0] for p, l, s in
                                       ((85, 30, 70), (84.99, 30, 70), (70, 40, 60), (69.99, 40, 60),
                                        (15, 70, 30), (15.01, 70, 30), (30, 60, 40), (30.01, 60, 40))] ==
          ["strong_bull", "bull", "bull", "none", "strong_bear", "bear", "bear", "none"])
    check(g, "labels", [dv.classify(*a)[1] for a in ((90, 10, 90), (10, 90, 10), (75, 35, 65), (25, 65, 35), (50, 50, 50))]
          == ["Strong Bullish", "Strong Bearish", "Bullish", "Bearish", "No significant divergence"])
    check(g, "thresholds live in one config module", [r[3] for r in cfg.RULES] == [85, 15, 70, 30] and
          [r[5] for r in cfg.RULES] == [70, 70, 60, 60])
    # retail 50/50 and the 0/100 extremes
    r = ev("GBP", make_store({"GBP": series(49)}), make_snap({"GBP": mkt("GBPUSD", 50, 50)}))
    check(g, "retail exactly 50/50 at a COT extreme -> no divergence", r["cls"] == "none")
    r = ev("GBP", make_store({"GBP": series(49)}), make_snap({"GBP": mkt("GBPUSD", 0, 100)}))
    check(g, "retail 0/100 (all short) + COT 99th -> Strong Bullish", r["cls"] == "strong_bull")
    r = ev("GBP", make_store({"GBP": series(0)}), make_snap({"GBP": mkt("GBPUSD", 100, 0)}))
    check(g, "retail 100/0 (all long) + COT 1st -> Strong Bearish", r["cls"] == "strong_bear")
    r = ev("GBP", make_store({"GBP": series(0)}), make_snap({"GBP": mkt("GBPUSD", 0, 100)}))
    check(g, "retail 0/100 + COT 1st -> no divergence (same side)", r["cls"] == "none")


def t_quality():
    g = "quality"
    st = make_store({"XAU": series(46)})
    good = make_snap({"XAU": mkt("XAUUSD", 20, 80)})
    check(g, "baseline fixture is a Strong Bullish", ev("XAU", st, good)["cls"] == "strong_bull")
    for lpos, spos, want in ((60, 39, "unavailable"), (60, 40, "strong_bull"), (61, 40, "strong_bull")):
        r = ev("XAU", st, make_snap({"XAU": mkt("XAUUSD", 20, 80, lpos, spos)}))
        check(g, f"{lpos + spos} positions -> {want}", r["cls"] == want, r["reasons"])
    r = ev("XAU", st, make_snap({"XAU": mkt("XAUUSD", 20, 80, 3, 1)}))
    check(g, "below 100: reason names the count and the minimum",
          r["reasons"] == ["Myfxbook sample too small - 4 positions, minimum 100"], r["reasons"])
    html = dv.card(r)
    check(g, "below 100: card shows the count only - no %, imbalance or crowd (Step 1 rule)",
          "4 <span" in html and not re.search(r"<dt>(Long|Short|Imbalance|Crowd)</dt>", html), html)
    for age, want in ((179, "strong_bull"), (180, "strong_bull"), (181, "unavailable"), (60 * 30, "unavailable")):
        r = ev("XAU", st, make_snap({"XAU": mkt("XAUUSD", 20, 80)}, age_min=age))
        check(g, f"retail {age} min old -> {want}", r["cls"] == want, r["reasons"])
    r = ev("XAU", st, make_snap({"XAU": mkt("XAUUSD", 20, 80)}, age_min=200))
    check(g, "stale: reason says how old and the 3 h limit", r["reasons"][0].startswith("Myfxbook retail data stale")
          and "limit 3 hours" in r["reasons"][0], r["reasons"])
    check(g, "stale: card does not repeat old figures", "<dt>Long</dt>" not in dv.card(r))
    r = ev("XAU", st, make_snap({"XAU": None}))
    check(g, "market missing from the snapshot -> unavailable", r["cls"] == "unavailable" and
          "unavailable for this market" in r["reasons"][0])
    r = ev("XAU", st, {})
    check(g, "no Myfxbook snapshot at all -> unavailable", r["cls"] == "unavailable" and "nothing fetched" in r["reasons"][0])
    r = ev("XAU", st, good, on=False)
    check(g, "Myfxbook switched off -> unavailable", r["cls"] == "unavailable" and "switched off" in r["reasons"][0])
    for bad, label in (({"longPercentage": "x"}, "text %"), ({"longPercentage": float("nan")}, "NaN"),
                       ({"longPercentage": 120, "shortPercentage": -20}, "out of range"),
                       ({"longPercentage": 40, "shortPercentage": 40}, "does not add to 100"),
                       ({"longPositions": None}, "missing positions"), ({"shortPercentage": True}, "bool")):
        m = mkt("XAUUSD", 20, 80)
        m.update(bad)
        r = ev("XAU", st, make_snap({"XAU": m}))
        check(g, f"malformed retail ({label}) -> unavailable", r["cls"] == "unavailable" and "malformed" in r["reasons"][0], r["reasons"])
    s2 = make_snap()
    s2["fetched_at"] = "yesterday"
    r = ev("XAU", st, s2)
    check(g, "malformed timestamp -> unavailable", r["cls"] == "unavailable" and "malformed" in r["reasons"][0], r["reasons"])
    for sym in ("GOLD", "XAUEUR"):
        r = ev("XAU", st, make_snap({"XAU": mkt(sym, 20, 80)}))
        check(g, f"Step 1 symbol {sym} (not reviewed for XAU) -> symbol mapping uncertain",
              r["cls"] == "unavailable" and r["reasons"][0].startswith("symbol mapping uncertain"), r["reasons"])
    r = ev("USD", make_store({"USD": series(46)}), make_snap({"USD": mkt("DXY", 20, 80)}))
    check(g, "USD via DXY -> symbol mapping uncertain (only USDX reviewed)", r["cls"] == "unavailable")
    r = ev("USD", make_store({"USD": series(46)}), make_snap({"USD": mkt("USDX", 20, 80)}))
    check(g, "USD via USDX with enough positions -> classified normally", r["cls"] == "strong_bull", r["reasons"])
    r = ev("USD", make_store({"USD": series(46)}), make_snap({"USD": mkt("USDX", 20, 80, 30, 20)}))
    check(g, "USD via USDX below 100 positions (today's situation) -> unavailable", r["cls"] == "unavailable")


def t_cot_quality():
    g = "cot-quality"
    good = make_snap({"EUR": mkt("EURUSD", 20, 80)})

    def drop_latest(store):
        del store["contracts"][CODE["EUR"]]["weeks"][LATEST.isoformat()]
    r = ev("EUR", make_store({"EUR": series(46)}, extra=drop_latest), good)
    check(g, "contract missing the latest report -> unavailable", r["cls"] == "unavailable" and
          "COT data missing" in r["reasons"][0], r["reasons"])
    r = ev("EUR", make_store({"EUR": series(20, n=30)}), good)
    check(g, "only 30 weekly reports -> unavailable (needs 50)", r["cls"] == "unavailable" and
          "only 30 of 50" in r["reasons"][0], r["reasons"])
    r = ev("EUR", None if False else ct.empty_store(), good)
    check(g, "empty COT store -> unavailable", r["cls"] == "unavailable" and "COT data missing" in r["reasons"][0])
    check(g, "no COT store at all -> unavailable", dv.cot_side("EUR", None, NOW) == {"error": "COT data missing"})
    r = ev("EUR", make_store({"EUR": [5000] * 50}), good)
    check(g, "flat 50-week range -> unavailable", r["cls"] == "unavailable" and "flat" in r["reasons"][0])

    def corrupt(store):
        store["contracts"][CODE["EUR"]]["weeks"][LATEST.isoformat()][1] = "lots"
    r = ev("EUR", make_store({"EUR": series(46)}, extra=corrupt), good)
    check(g, "malformed COT row -> unavailable", r["cls"] == "unavailable" and "malformed" in r["reasons"][0], r["reasons"])
    r = ev("EUR", make_store({"EUR": series(46)}), good, now=NOW + dt.timedelta(days=20))
    check(g, "latest COT report 28 days old -> unavailable (limit 21)", r["cls"] == "unavailable" and
          any("COT data out of date" in x and "28 days old" in x for x in r["reasons"]), r["reasons"])
    r = ev("EUR", make_store({"EUR": series(46)}), make_snap({"EUR": mkt("EURUSD", 20, 80, 3, 1)}),
           now=NOW + dt.timedelta(days=20))
    check(g, "several problems -> every reason listed", len(r["reasons"]) == 2, r["reasons"])
    # window: the oldest of 60 stored weeks must not count
    s = [10 ** 6] * 10 + series(46)
    c = dv.cot_side("EUR", make_store({"EUR": s}), NOW)
    check(g, "percentile uses the latest 50 reports only", c["weeks"] == 50 and abs(c["pct"] - pct_of(46)) < 1e-9, c)


def t_momentum():
    g = "momentum"
    c = dv.cot_side("EUR", make_store({"EUR": series(45, prev=40000)}), NOW)
    check(g, "current, previous and change", c["net"] == 45000 and c["prev_net"] == 40000 and c["change"] == 5000, c)
    check(g, "normalized change = change / 50-week range", abs(c["change_pct"] - 100 * 5000 / 49000) < 1e-9)
    check(g, "bullish lean + rising -> Strengthening", c["momentum"] == "Strengthening")
    c = dv.cot_side("EUR", make_store({"EUR": series(45, prev=49000)}), NOW)
    check(g, "bullish lean + falling -> Weakening", c["momentum"] == "Weakening", c)
    c = dv.cot_side("EUR", make_store({"EUR": series(4, prev=10000)}), NOW)
    check(g, "bearish lean + falling -> Strengthening", c["momentum"] == "Strengthening", c)
    c = dv.cot_side("EUR", make_store({"EUR": series(4, prev=0)}), NOW)
    check(g, "bearish lean + rising -> Weakening", c["momentum"] == "Weakening", c)
    c = dv.cot_side("EUR", make_store({"EUR": series(30, prev=28000)}), NOW)
    check(g, "under 5% of the range -> Flat/neutral", c["momentum"] == "Flat/neutral" and abs(c["change_pct"]) < 5, c)
    check(g, "momentum() thresholds", [dv.momentum(p, x) for p, x in ((80, 4.9), (80, 5), (80, -5), (20, -5), (20, 5), (50, 6), (49, 6), (60, None))]
          == ["Flat/neutral", "Strengthening", "Weakening", "Strengthening", "Weakening", "Strengthening", "Weakening", "unavailable"])
    st = make_store({"EUR": series(45, prev=49000)})
    a = ev("EUR", st, make_snap({"EUR": mkt("EURUSD", 20, 80)}))
    st2 = make_store({"EUR": series(45, prev=40000)})
    b = ev("EUR", st2, make_snap({"EUR": mkt("EURUSD", 20, 80)}))
    check(g, "momentum never changes the classification", a["cls"] == b["cls"] == "strong_bull"
          and a["cot"]["momentum"] != b["cot"]["momentum"])
    check(g, "direction labels", [dv.direction(p) for p in (70, 69.9, 30, 30.1, 50)] ==
          ["Bullish", "Neutral", "Bearish", "Neutral", "Neutral"])


def t_card():
    g = "card"
    st = make_store({"XAU": series(45, prev=40000)})
    r = ev("XAU", st, make_snap({"XAU": mkt("XAUUSD", 24, 76, 1500, 1920)}))
    html = dv.card(r)
    for want in ("COT &times; Retail divergence", "Strong Bullish Divergence", "<dt>Direction</dt><dd>Bullish</dd>",
                 "<dt>50-week percentile</dt><dd>91st</dd>", "<dt>Net position</dt><dd>+45,000</dd>",
                 "<dt>Weekly change</dt><dd>+5,000", "<dt>Momentum</dt><dd>Strengthening</dd>",
                 "<dt>Long</dt><dd>24%</dd>", "<dt>Short</dt><dd>76%</dd>", "<dt>Positions</dt><dd>3,420</dd>",
                 "<dt>Imbalance</dt><dd>52</dd>", "<dt>Crowd</dt><dd>Heavy Short</dd>",
                 "Why Strong Bullish?", "91st percentile of its 50-week range", "Retail traders are 76% short",
                 "above the minimum position count", "Retail data is fresh",
                 "<dt>COT report</dt><dd>06 Oct 2026", "<dt>Myfxbook update</dt><dd>14 Oct 2026 11:50 UTC"):
        check(g, f"card shows: {re.sub('<[^>]+>', ' ', want).strip()}", want in html, html[:300])
    check(g, "COT report date and Myfxbook update time kept separate (two fields)",
          html.count("<dt>COT report</dt>") == 1 and html.count("<dt>Myfxbook update</dt>") == 1)
    check(g, "card carries the 3 h browser cut-off", f'data-div-max="{cfg.RETAIL_MAX_AGE_MIN}"' in html
          and 'data-div-ts="2026-10-14T11:50:00Z"' in html and 'class="dvexp"' in html)
    r = ev("XAU", make_store({"XAU": series(25)}), make_snap({"XAU": mkt("XAUUSD", 45, 55)}))
    html = dv.card(r)
    check(g, "no-signal card explains why", "No significant divergence" in html and "Why no divergence?" in html
          and "No rule is met" in html, html)
    r = ev("XAU", make_store({"XAU": series(45)}), make_snap({"XAU": mkt("XAUUSD", 45, 55)}))
    html = dv.card(r)
    check(g, "near-miss names what is missing", "needs retail short &ge; 70%" in html and "needs retail short &ge; 60%" in html, html)
    r = ev("ZB")
    html = dv.card(r)
    check(g, "unavailable card: 'Divergence unavailable' + why", "Divergence unavailable" in html and
          "Why unavailable?" in html and "no data for this market" in html)
    words = " ".join(dv.card(ev(k, make_store({k: series(45)}), make_snap({k: mkt(SYMBOL.get(k, "X"), 20, 80)})))
                     for k in CODE) + dv.DISCLAIMER + dv.EXP_NOTE
    check(g, "no BUY/SELL or trade-advice wording anywhere",
          not re.search(r"(?i)buy now|sell now|guaranteed|high[- ]probability|\bbuy\b|\bsell\b|enter|take profit|stop loss", words))
    check(g, "says it is a positioning divergence, not a trade signal", "not a trade signal" in words)


def t_scanner_and_switch():
    g = "scanner"
    store = make_store({"EUR": series(45), "JPY": series(4), "XAU": series(37), "GBP": series(13), "AUD": series(25),
                        "HG": series(49)})
    snap = make_snap({"EUR": mkt("EURUSD", 20, 80), "JPY": mkt("USDJPY", 80, 20), "XAU": mkt("XAUUSD", 35, 65),
                      "GBP": mkt("GBPUSD", 65, 35), "AUD": mkt("AUDUSD", 50, 50), "HG": mkt("COPPER", 10, 90, 3, 2)})
    d = write_dir(store, snap)
    html = dv.scanner(now=NOW, data=d, env=ON)
    names = re.findall(r'<span class="dvsn">([^<]+)</span><span class="dvsl">([^<]+)</span>', html)
    check(g, "only actionable classes listed, strong first",
          [n[1] for n in names] == ["Strong Bullish Divergence", "Strong Bearish Divergence", "Bullish Divergence", "Bearish Divergence"], names)
    check(g, "the right markets (EURUSD, USDJPY, Gold, GBPUSD)", [n[0] for n in names] == ["EURUSD", "USDJPY", "Gold", "GBPUSD"], names)
    check(g, "no neutral / unavailable market listed", "AUDUSD" not in html.split("dvfoot")[0] and "Copper" not in html.split("dvfoot")[0])
    check(g, "each entry opens that market's report", all(f'href="#cot50-{k}"' in html for k in ("EUR", "JPY", "XAU", "GBP")))
    check(g, "one-line count of the rest", "16 markets checked: 4 divergences, " in html and " unavailable" in html, html[-400:])
    check(g, "heading", "Positioning divergences" in html)
    check(g, "scanner carries the 3 h browser cut-off + script", "data-div-ts=" in html and "dvexpired" in html)
    for k, want in (("EUR", "strong_bull"), ("AUD", "none"), ("HG", "unavailable"), ("ZB", "unavailable")):
        c = dv.report_card(k, now=NOW, data=d, env=ON)
        check(g, f"report card {k}: {want}", f'data-div-class="{want}"' in c)
    d2 = write_dir(make_store(), make_snap())
    html = dv.scanner(now=NOW, data=d2, env=ON)
    check(g, "nothing actionable -> one quiet line", "No positioning divergence right now." in html and "<li" not in html)
    check(g, "switched off: no scanner, no card", dv.scanner(now=NOW, data=d, env={}) == "" and
          dv.report_card("EUR", now=NOW, data=d, env={"MYFXBOOK_ENABLED": "TRUE"}) == "")
    # isolation: nothing in the scoring / alert / Pi code imports the divergence
    root = Path(__file__).parent
    users = sorted(p.name for p in list(root.glob("*.py")) + list((root.parent / "pi").glob("*.py"))
                   if re.search(r"^\s*(import|from)\s+divergence(\s|,|$)", p.read_text(errors="replace"), re.M))
    check(g, "only the page builder (and these tests) use divergence - no score, alert or Pi code",
          users == ["build_dashboard.py", "test_divergence.py"], users)
    check(g, "fixtures never touched the live data dir", not any(str(mf.DATA) in str(p) for p in (d, d2)))


def _res(html):
    """The visible result line of a card (what the reader sees as the outcome)."""
    m = re.search(r'<p class="dvres">(.*?)</p>', html)
    return m.group(1) if m else None


def t_wording():
    """'Divergence unavailable' whenever a dataset is missing / too small / stale / uncertain;
    'No significant divergence' ONLY when both are valid and fresh and no rule is met."""
    g = "wording"
    st = make_store({k: series(25) for k in CODE})          # COT mid-range: no rule can be met
    cases = {
        "ZB (no Myfxbook symbol)": ("ZB", st, make_snap()),
        "market missing from Myfxbook": ("EUR", st, make_snap({"EUR": None})),
        "no Myfxbook snapshot": ("EUR", st, {}),
        "below 100 positions": ("EUR", st, make_snap({"EUR": mkt("EURUSD", 50, 50, 40, 40)})),
        "stale (3 h 01)": ("EUR", st, make_snap(age_min=cfg.RETAIL_MAX_AGE_MIN + 1)),
        "symbol mapping uncertain": ("EUR", st, make_snap({"EUR": mkt("EURUSD.m", 50, 50)})),
        "USD via DXY (uncertain)": ("USD", st, make_snap({"USD": mkt("DXY", 50, 50)})),
        "malformed retail": ("EUR", st, make_snap({"EUR": {**mkt("EURUSD", 50, 50), "longPercentage": "?"}})),
        "COT missing": ("EUR", ct.empty_store(), make_snap()),
    }
    for name, (k, store, snap) in cases.items():
        r = dv.evaluate(k, store, snap, NOW, True)
        html = dv.card(r)
        check(g, f"{name}: 'Divergence unavailable', never 'No significant divergence'",
              r["cls"] == "unavailable" and r["label"] == "Divergence unavailable" and _res(html) == "Divergence unavailable"
              and "No significant divergence" not in html and 'data-div-class="unavailable"' in html, (r["cls"], r["reasons"]))
        check(g, f"{name}: says why", 'Why unavailable?' in html and len(r["reasons"]) >= 1)
    r = dv.evaluate("EUR", st, make_snap({"EUR": mkt("EURUSD", 50, 50)}), NOW, True)
    html = dv.card(r)
    live = html.split('<div class="dvlive">', 1)[1]
    check(g, "both datasets valid + fresh, no rule met -> 'No significant divergence'",
          r["cls"] == "none" and _res(html) == "No significant divergence" and not r["reasons"], (r["cls"], r["reasons"]))
    check(g, "... and its visible part never says 'unavailable'", "unavailable" not in live.lower(), live[:200])
    check(g, "... the only 'unavailable' text is the hidden 3-hour browser note",
          html.count("Divergence unavailable") == 1 and '<p class="dvexp">Divergence unavailable' in html)
    d = write_dir(st, make_snap({"EUR": mkt("EURUSD", 50, 50), "HG": mkt("COPPER", 50, 50, 3, 2)}))
    res = dv.evaluate_all(now=NOW, data=d, env=ON)
    for k, r in res.items():
        want = "No significant divergence" if r["cls"] == "none" else "Divergence unavailable"
        check(g, f"all 16 cards: {k} visible result is '{want}'", _res(dv.card(r)) == want, (r["cls"], r["reasons"]))
    check(g, "today's shape: ZB + the six thin markets unavailable, the rest none",
          sorted(k for k, r in res.items() if r["cls"] == "unavailable") == ["HG", "ZB"]
          and all(r["cls"] == "none" for k, r in res.items() if k not in ("HG", "ZB")))


def t_plural():
    g = "plural"
    check(g, "1 position / 2 positions / 0 positions / 1,500 positions",
          [dv._positions(n) for n in (1, 2, 0, 1500)] == ["1 position", "2 positions", "0 positions", "1,500 positions"])
    st = make_store({"XAU": series(46)})
    for n_long, n_short, want in ((1, 0, "1 position,"), (0, 1, "1 position,"), (1, 1, "2 positions,"), (2, 1, "3 positions,")):
        r = ev("XAU", st, make_snap({"XAU": mkt("XAUUSD", 50, 50, n_long, n_short)}))
        check(g, f"{n_long + n_short} retail position(s): reason reads '{want[:-1]}'",
              r["reasons"] == [f"Myfxbook sample too small - {want} minimum 100"], r["reasons"])


def t_config_driven():
    """Changing divergence_config changes the behaviour - no limit is hard-coded."""
    g = "config"
    saved = (cfg.COT_WEEKS, cfg.RETAIL_MAX_AGE_MIN, cfg.COT_MAX_AGE_DAYS, cfg.RULES)
    try:
        good = make_snap({"EUR": mkt("EURUSD", 20, 80)}, age_min=90)
        st = make_store({"EUR": series(45)})
        check(g, "default: 90-minute-old retail is fresh", ev("EUR", st, good)["cls"] == "strong_bull")
        cfg.RETAIL_MAX_AGE_MIN = 60
        r = ev("EUR", st, good)
        check(g, "RETAIL_MAX_AGE_MIN = 60 -> the same data is now stale", r["cls"] == "unavailable" and
              "limit 1 hour)" in r["reasons"][0], r["reasons"])
        check(g, "the browser cut-off follows it", 'data-div-max="60"' in dv.card(ev("EUR", st, make_snap({"EUR": mkt("EURUSD", 20, 80)}))))
        cfg.RETAIL_MAX_AGE_MIN = saved[1]
        cfg.COT_MAX_AGE_DAYS = 5
        r = ev("EUR", st, make_snap({"EUR": mkt("EURUSD", 20, 80)}))
        check(g, "COT_MAX_AGE_DAYS = 5 -> an 8-day-old report is out of date", r["cls"] == "unavailable" and
              "limit 5" in r["reasons"][0], r["reasons"])
        cfg.COT_MAX_AGE_DAYS = saved[2]
        cfg.COT_WEEKS = 30
        c = dv.cot_side("EUR", make_store({"EUR": series(20, n=30)}), NOW)
        check(g, "COT_WEEKS = 30 -> 30 reports are enough, percentile over 30", c.get("weeks") == 30 and
              abs(c["pct"] - pct_of(20, 30)) < 1e-9, c)
        check(g, "card label follows the lookback", "<dt>30-week percentile</dt>" in dv.card(
            ev("EUR", make_store({"EUR": series(20, n=30)}), make_snap({"EUR": mkt("EURUSD", 50, 50)}))))
        cfg.COT_WEEKS = saved[0]
        cfg.RULES = (("strong_bull", "Strong Bullish", ">=", 95, "short", 70),) + saved[3][1:]
        check(g, "a changed threshold in RULES changes the class (91st no longer Strong)",
              ev("EUR", st, make_snap({"EUR": mkt("EURUSD", 20, 80)}))["cls"] == "bull")
    finally:
        cfg.COT_WEEKS, cfg.RETAIL_MAX_AGE_MIN, cfg.COT_MAX_AGE_DAYS, cfg.RULES = saved
    check(g, "defaults restored: 50 weeks, 180 min, 21 days", (cfg.COT_WEEKS, cfg.RETAIL_MAX_AGE_MIN, cfg.COT_MAX_AGE_DAYS) == (50, 180, 21))


if __name__ == "__main__":
    for fn in (t_orientation_table, t_orientation_values, t_markets, t_rules, t_quality, t_cot_quality,
               t_momentum, t_card, t_scanner_and_switch, t_wording, t_config_driven, t_plural):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised", f"{type(e).__name__}: {e}"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    groups = {}
    for g, n, _ in PASS:
        groups.setdefault(g, [0, 0])[0] += 1
    for g, n, _ in FAIL:
        groups.setdefault(g, [0, 0])[1] += 1
    print()
    for g, (ok, bad) in groups.items():
        print(f"{g}: {ok}/{ok + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
