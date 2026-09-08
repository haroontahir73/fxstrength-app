"""Government bond yields, curves, real yields and rate differentials - one row per currency.

Rate differentials are the most established driver in FX, and the FRONT END (2 year) is the
part that moves spot, because it prices the policy path the market actually expects rather
than the term premium. This module reads the 2y and 10y benchmark for each of the eight
majors plus headline CPI, and derives:

    curve        10y minus 2y - positive is a normal upward-sloping curve, negative is an
                 inversion, which historically leads a slowdown by several quarters
    real 10y     10y minus headline CPI YoY - the ex-post real yield. The textbook figure is
                 the inflation-linked yield (FRED's DFII10). FRED's keyed API host
                 (api.stlouisfed.org) IS reachable - only its CSV graph endpoint times out -
                 but it needs a free API key the desk does not have yet. Until then
                 nominal-minus-CPI needs no second feed and moves with the same signal.
    vs USD       the differential every FX pair is quoted against
    score        -100..+100, the leg that feeds the board and the factor matrix

EUR is the German bund and CHF the Swiss confederation bond - the benchmarks those currencies
actually trade off. Source is TradingView's scanner symbol endpoint, the same host the
economic calendar already comes from.

Writes data/yields.json, and appends a dated snapshot to data/yields_history.json so the
momentum leg has something to measure once a few weeks have accumulated.
"""
import json, urllib.parse, urllib.request, datetime as dt
from config import DATA, ORDER, YIELD_SYMBOLS

API = "https://scanner.tradingview.com/symbol?symbol={sym}&fields=close&no_404=true"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Safari/537.36"}
HIST = DATA / "yields_history.json"
MOM_DAYS = 20          # trading-month lookback for the front-end momentum leg

# The blend. Front-end differential leads because it is the part of the curve that prices
# policy and the part that has the clearest link to spot. Real yield carries a real share -
# a high nominal yield with inflation above it is not a reason to own a currency. Momentum
# is the smallest and stays at zero until yields_history.json has MOM_DAYS behind it, so a
# cold start reports "no history" rather than a fabricated number.
LEGS = {"front": 0.45, "real": 0.35, "momentum": 0.20}


def _close(sym):
    url = API.format(sym=urllib.parse.quote(sym))
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read()
    v = json.loads(raw.decode("utf-8", "replace")).get("close")
    return float(v) if v is not None else None


def _spread_score(vals, cur, cap):
    """Where `cur` sits against the cross-currency average, in percentage points, mapped to
    -100..+100 and clipped at `cap` points of differential. Averaging across the board (rather
    than against USD alone) keeps the leg centred the way the rest of the board is."""
    have = [v for v in vals if v is not None]
    if cur is None or len(have) < 3:
        return None
    avg = sum(have) / len(have)
    return round(max(-100.0, min(100.0, (cur - avg) / cap * 100)), 1)


def _load_hist():
    if not HIST.exists():
        return {}
    try:
        return json.loads(HIST.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build():
    now = dt.datetime.now(dt.timezone.utc)
    rows = {}
    for ccy in ORDER:
        s = YIELD_SYMBOLS.get(ccy, {})
        d = {"y2": None, "y10": None, "cpi": None}
        for k, sym in s.items():
            try:
                d[k] = _close(sym)
            except Exception as e:                                # noqa: BLE001
                print(f"  {ccy} {k} ({sym}) failed: {type(e).__name__}: {e}")
        d["curve"] = (round(d["y10"] - d["y2"], 3)
                      if d["y10"] is not None and d["y2"] is not None else None)
        d["real10"] = (round(d["y10"] - d["cpi"], 3)
                       if d["y10"] is not None and d["cpi"] is not None else None)
        d["inverted"] = (d["curve"] is not None and d["curve"] < 0)
        rows[ccy] = d

    usd = rows.get("USD", {})
    for ccy, d in rows.items():
        for k, base in (("y2", usd.get("y2")), ("y10", usd.get("y10")),
                        ("real10", usd.get("real10"))):
            d[f"{k}_vs_usd"] = (round(d[k] - base, 3)
                                if d.get(k) is not None and base is not None else None)

    # --- history, for the momentum leg. Keyed by date so a second run the same day overwrites
    # rather than double-counting.
    hist = _load_hist()
    hist[now.date().isoformat()] = {c: {"y2": rows[c]["y2"], "y10": rows[c]["y10"]}
                                    for c in ORDER}
    for stale in sorted(hist)[:-800]:
        hist.pop(stale, None)
    HIST.write_text(json.dumps(hist, indent=2), encoding="utf-8")

    days = sorted(hist)
    ref = hist[days[-1 - MOM_DAYS]] if len(days) > MOM_DAYS else None
    if ref is None:
        print(f"  front-end momentum: {len(days)}/{MOM_DAYS + 1} days of history - leg held at 0")

    y2s = [rows[c]["y2"] for c in ORDER]
    reals = [rows[c]["real10"] for c in ORDER]
    for ccy in ORDER:
        d = rows[ccy]
        parts = {"front": _spread_score(y2s, d["y2"], cap=2.0),
                 "real": _spread_score(reals, d["real10"], cap=2.0),
                 "momentum": None}
        if ref and d["y2"] is not None and (ref.get(ccy) or {}).get("y2") is not None:
            # a currency whose front end is repricing HIGHER relative to the board is being
            # bid for carry; 50bp of relative move over the month is a full-scale reading
            moves = [rows[c]["y2"] - ref[c]["y2"] for c in ORDER
                     if rows[c]["y2"] is not None and (ref.get(c) or {}).get("y2") is not None]
            if len(moves) >= 3:
                mine = d["y2"] - ref[ccy]["y2"]
                rel = mine - sum(moves) / len(moves)
                parts["momentum"] = round(max(-100.0, min(100.0, rel / 0.50 * 100)), 1)
        live = {k: v for k, v in parts.items() if v is not None}
        if live:
            wsum = sum(LEGS[k] for k in live)
            d["score"] = round(sum(live[k] * LEGS[k] for k in live) / wsum, 1)
        else:
            d["score"] = None
        d["parts"] = parts

    out = {"fetched_at": now.isoformat(), "asof": now.date().isoformat(),
           "legs": LEGS, "mom_days": MOM_DAYS, "hist_days": len(days),
           "currencies": rows,
           "ranked": sorted([c for c in ORDER if rows[c].get("score") is not None],
                            key=lambda c: -rows[c]["score"])}
    (DATA / "yields.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    print(f"  {'ccy':4} {'2y':>7} {'10y':>7} {'curve':>7} {'CPI':>6} {'real':>7} {'score':>7}")
    for c in r["ranked"] or ORDER:
        d = r["currencies"][c]
        f = lambda v, w=7, p=2: (f"{v:{w}.{p}f}" if v is not None else " " * (w - 3) + "n/a")
        print(f"  {c:4} {f(d['y2'])} {f(d['y10'])} {f(d['curve'])} {f(d['cpi'],6,1)} "
              f"{f(d['real10'])} {f(d['score'],7,1)}" + ("  INVERTED" if d["inverted"] else ""))
