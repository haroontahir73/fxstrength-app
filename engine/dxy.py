"""The dollar index as a tradeable instrument, scored in its own right.

WHY THIS IS NOT JUST THE USD SCORE. The board already gives USD a number, but that number
answers a different question. It is CENTRED against seven peers (so it says "the dollar versus
the pack") and it treats every currency as one vote. DXY is none of those things: it is a
fixed basket dominated by the euro at 57.6%, it has its own price and its own trend, and it is
a thing you can actually buy. A board where USD is mildly positive and EUR is strongly positive
can easily be a DXY that is going DOWN, because the euro is more than half the index. That
disagreement is the reason this exists.

Legs:
  basket  0.45  the board's own scores, inverted and weighted by each currency's real share of
                the index. This is the honest link back to the desk.
  trend   0.30  DX-Y.NYB against its 50-day average plus the ~20-day change - the same maths
                the commodity and index tracks use.
  cot     0.15  ICE Dollar Index, Leveraged Funds net as a share of open interest. The contract
                is THIN - about 50k open interest against 865k for the euro - which is exactly
                why score.py already discounts it to a quarter of the USD reading. Sized small
                here for the same reason and flagged on the row.

NO SEASONALITY LEG. It had 0.10 until backtest_factors.py measured it walk-forward over 15
years and 32 instruments: hit 47.5%, t -3.50, n 3131. Inverted, not merely weak. Removed
rather than flipped - flipping a sign to fit a backtest is how you fit noise.

NO OPEN-INTEREST LEG. The OI conviction leg everywhere else on the desk reads a change in open
interest against the direction of positioning flow, and for the dollar index that would be the
same thin weekly ICE number the COT leg already uses - the same input twice. When a daily ICE
feed exists (the Raspberry Pi plan), add it here as its own leg rather than folding it into
`cot`.

SEK IS MISSING. The index is EUR 57.6 / JPY 13.6 / GBP 11.9 / CAD 9.1 / SEK 4.2 / CHF 3.6.
The desk scores every one of those except the Swedish krona, so the basket leg covers 95.8% of
the index and is renormalised over what is present. That is a real, small gap and the row says
so rather than pretending the basket is complete.

Writes data/dxy.json.
"""
import json, math, datetime as dt
from config import DATA, commodity_rating, directional_read, retracement_zone

OUT = DATA / "dxy.json"
YAHOO = "DX-Y.NYB"
THIN_OI = 100_000

# ICE's published index weights. SEK has no score on this desk - see the module docstring.
WEIGHTS_INDEX = {"EUR": 0.576, "JPY": 0.136, "GBP": 0.119, "CAD": 0.091,
                 "SEK": 0.042, "CHF": 0.036}
# Seasonality was a 0.10 leg until it was measured: walk-forward, 15 years, 32 instruments,
# hit 47.5% and t -3.50. Inverted, not merely weak. Removed; its weight went to the basket,
# which is the leg with an actual mechanism behind it.
LEGS = {"basket": 0.55, "trend": 0.30, "cot": 0.15}


def _load(name, default):
    p = DATA / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _basket_leg(scores):
    """Board scores, inverted and weighted by index share. A strong euro is a weak dollar
    index, and it is worth 57.6% of the answer."""
    have, wsum = {}, 0.0
    for ccy, w in WEIGHTS_INDEX.items():
        s = ((scores.get("currencies") or {}).get(ccy) or {}).get("score")
        if s is None:
            continue
        have[ccy] = (s, w)
        wsum += w
    if not have or wsum <= 0:
        return {"score": 0.0, "note": "no board scores available"}
    # the board is centred and spans roughly +/-20; scale so a 20-point weighted basket move
    # is a full reading, then invert
    raw = sum(s * w for s, w in have.values()) / wsum
    score = max(-100.0, min(100.0, -raw / 20.0 * 100))
    missing = [c for c in WEIGHTS_INDEX if c not in have]
    parts = ", ".join(f"{c} {s:+.1f}x{w*100:.1f}%" for c, (s, w) in
                      sorted(have.items(), key=lambda kv: -kv[1][1]))
    return {"score": round(score, 1), "coverage_pct": round(wsum * 100, 1),
            "missing": missing, "weighted_board": round(raw, 1),
            "note": f"basket {raw:+.1f} inverted ({parts})"
                    + (f"; {', '.join(missing)} not scored on this desk - "
                       f"{wsum*100:.1f}% of the index covered" if missing else "")}


def _trend_leg(px):
    closes = px.get("closes") or []
    if len(closes) < 26:
        return {"score": 0.0, "chg_5d": None, "note": "not enough price history"}
    last = closes[-1]
    ref = sum(closes[-3:]) / 3
    win = closes[-50:] if len(closes) >= 50 else closes
    ma = sum(win) / len(win)
    dev = (ref - ma) / ma
    mom = (ref - closes[-20]) / closes[-20]
    chg_5d = (last - closes[-6]) / closes[-6]
    # DXY is a low-volatility instrument - a 1% move off the 50-day average is a real trend,
    # where that would be noise in crude. Scaled accordingly.
    score = 100 * (0.6 * math.tanh(dev * 25) + 0.4 * math.tanh(mom * 20))
    return {"score": round(score, 1), "last": round(last, 3), "ma": round(ma, 3),
            "dev_pct": round(dev * 100, 2), "mom_20d_pct": round(mom * 100, 2),
            "chg_5d": round(chg_5d, 4),
            "note": f"{last:.2f} vs 50-day avg {ma:.2f} ({dev*100:+.2f}%), "
                    f"20-day {mom*100:+.2f}%, last 5-day {chg_5d*100:+.2f}%"}


def _cot_leg(cot):
    """ICE Dollar Index positioning - the contract config.CURRENCIES['USD'] already tracks."""
    d = (cot.get("currencies") or {}).get("USD") or {}
    lev, oi = d.get("leveraged"), d.get("open_interest")
    if not lev or not oi:
        return {"score": 0.0, "note": "no COT data"}
    level_ratio = lev["net"] / oi
    flow_ratio = lev["net_chg"] / oi
    score = 0.6 * 100 * math.tanh(level_ratio * 3.0) + 0.4 * 100 * math.tanh(flow_ratio * 20.0)
    thin = oi < THIN_OI
    return {"score": round(score, 1), "net": lev["net"], "net_chg": lev["net_chg"], "oi": oi,
            "net_pct_oi": round(level_ratio * 100, 1), "thin": thin,
            "note": f"ICE dollar index Leveraged net {lev['net']:+,} "
                    f"({level_ratio*100:+.1f}% of OI), week {lev['net_chg']:+,}"
                    + (f" - thin contract ({oi:,} OI), weak evidence" if thin else "")}



def build():
    scores = _load("scores.json", {})
    cot = _load("cot.json", {})
    px = ((_load("prices_dxy.json", {}) or {}).get("symbols") or {}).get("DXY", {})

    legs = {"basket": _basket_leg(scores), "trend": _trend_leg(px), "cot": _cot_leg(cot)}
    parts = {k: legs[k]["score"] for k in LEGS}
    total = sum(parts[k] * LEGS[k] for k in LEGS)
    label, cls = commodity_rating(total)          # uncentred, like the commodity track

    raw_5d = legs["trend"].get("chg_5d")
    chg_5d_pct = raw_5d * 100 if isinstance(raw_5d, (int, float)) else None
    read = directional_read(total, chg_5d_pct, "commodity")
    retr = retracement_zone(px.get("closes"), total, "commodity", px.get("dates"))

    contrib = {k: round(parts[k] * LEGS[k], 1) for k in LEGS}
    residual = round(round(total, 1) - sum(contrib.values()), 1)
    if residual and contrib:
        big = max(contrib, key=lambda k: abs(contrib[k]))
        contrib[big] = round(contrib[big] + residual, 1)

    out = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "weights": LEGS, "index_weights": WEIGHTS_INDEX,
           "score": round(total, 1), "rating": label, "cls": cls,
           "parts": {k: round(v, 1) for k, v in parts.items()}, "contrib": contrib,
           "legs": legs, "read": read, "retr": retr,
           "thin": legs["cot"].get("thin", False),
           "coverage_pct": legs["basket"].get("coverage_pct"),
           "missing": legs["basket"].get("missing") or [],
           "last": legs["trend"].get("last"),
           "chg_5d_pct": round(chg_5d_pct, 2) if chg_5d_pct is not None else None,
           "price_asof": (px.get("dates") or [None])[-1]}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    print(f"  DXY {r['last']}  score {r['score']:+.1f}  {r['rating']}"
          + ("  [thin COT contract]" if r["thin"] else ""))
    for k in ("basket", "trend", "cot"):
        print(f"    {k:12} {r['parts'][k]:+7.1f} x{r['weights'][k]:.2f} = {r['contrib'][k]:+5.1f}"
              f"   {r['legs'][k]['note']}")
    print(f"  {r['read']['label']}")
