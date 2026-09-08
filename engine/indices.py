"""Blend the equity-index track - S&P 500, Nasdaq 100, Dow - into one -100..+100 bias each.

A third track alongside FX and commodities, and separate for the same reason: an index is an
outright directional bet with a strong upward drift, not a relative call against a peer, so it
must not sit in the currency centring or the pair ranking.

Legs (INDEX_WEIGHTS below):
  trend        3-day-average close vs the 50-day average plus the ~20-day change - same maths
               as the commodity track. Leads the blend: momentum is the input with the most
               empirical support in equities.
  cot          CFTC Leveraged Funds net as a share of open interest (60) + weekly flow (40).
               The TFF report, so this is the same speculative category the FX board uses.
  oi           open-interest change read against the direction of that flow
  seasonality  this month's EXCESS return over a normal month for this index, from
               seasonality.py. Equities have the clearest and best-documented seasonal
               pattern of anything on this desk, which is why it earns a leg here and not on
               the FX board.
  overlay      judgment - earnings, breadth, policy, positioning - held at a true 0 until set

WHY REAL YIELDS ARE NOT A LEG. Rising real yields compress equity multiples; that link is
real and yields.py now computes the number. It is deliberately left OUT of the score and
shown as context on the tab instead. The reason is double-counting: the trend leg already
carries most of what a yield shock does to an index, and adding a second leg that moves with
the same shock would let one macro event hit the score twice. Revisit if a backtest ever
says otherwise.

DJI trades as the MICRO e-mini in the report - about 43k open interest against the S&P's 2m -
so its positioning legs are thinner and noisier than the other two. Flagged on the row.

Writes data/indices.json.
"""
import json, math, datetime as dt
from config import (DATA, INDICES, INDEX_ORDER, commodity_rating, directional_read,
                    retracement_zone, cot_extreme, cot_reversal_adjust)

MANUAL_FILE = DATA / "indices_manual.json"
OUT = DATA / "indices.json"
CROWDED = 0.35
THIN_OI = 100_000          # below this the positioning legs get a "thin contract" note

INDEX_WEIGHTS = {
    "trend":       0.40,
    "cot":         0.20,
    "oi":          0.10,
    "seasonality": 0.15,
    "overlay":     0.15,
}

OVERLAY_INDICATORS = {
    "SPX": ["Earnings trend", "Market breadth", "Fed policy direction", "Credit spreads"],
    "NDX": ["Earnings trend", "Mega-cap concentration", "Real yield direction", "AI capex cycle"],
    "DJI": ["Earnings trend", "Cyclical vs defensive", "Fed policy direction", "Industrial demand"],
}


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                                       # noqa: BLE001
        print(f"  {path.name} unreadable ({type(e).__name__}: {e})")
        return default


def _load_manual():
    if not MANUAL_FILE.exists():
        seed = {"_help": "Per index, an indicator->1-5 map (5 = bullish). A bare number or "
                         "{\"score\": n, \"note\": \"why\"} for auditability. Unset = held at "
                         "neutral and excluded, exactly like commodities_manual.json.",
                **{s: {} for s in INDEX_ORDER}}
        MANUAL_FILE.write_text(json.dumps(seed, indent=2), encoding="utf-8")
        return {s: {} for s in INDEX_ORDER}
    raw = _load(MANUAL_FILE, {})
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _cot_leg(c):
    lev, oi = c.get("leveraged"), c.get("open_interest")
    if not lev or not oi:
        return {"score": 0.0, "note": "no COT data"}
    level_ratio = lev["net"] / oi
    flow_ratio = lev["net_chg"] / oi
    level = 100 * math.tanh(level_ratio * 3.0)
    flow = 100 * math.tanh(flow_ratio * 20.0)
    crowded = abs(level_ratio) > CROWDED
    return {"score": round(0.6 * level + 0.4 * flow, 1),
            "level": round(level, 1), "flow": round(flow, 1),
            "net": lev["net"], "net_chg": lev["net_chg"], "oi": oi,
            "net_pct_oi": round(level_ratio * 100, 1), "crowded": crowded,
            "thin": oi < THIN_OI,
            "note": f"Leveraged Funds net {lev['net']:+,} ({level_ratio*100:+.1f}% of OI), "
                    f"week {lev['net_chg']:+,}"
                    + (" - CROWDED, squeeze risk" if crowded else "")
                    + (" - thin contract, treat as weak evidence" if oi < THIN_OI else "")}


def _oi_leg(c):
    oi, oi_chg, lev = c.get("open_interest"), c.get("oi_change"), c.get("leveraged")
    if not oi or oi_chg is None or not lev:
        return {"score": 0.0, "note": "no OI data"}
    prev = oi - oi_chg
    oi_pct = (oi_chg / prev) if prev else 0.0
    direction = 1 if lev["net_chg"] > 0 else (-1 if lev["net_chg"] < 0 else 0)
    score = direction * math.tanh(abs(oi_pct) * 8) * 100
    flow = "new money"
    if oi_chg < 0:
        score *= 0.5
        flow = "liquidation"
    return {"score": round(score, 1), "oi": oi, "oi_chg": oi_chg,
            "oi_pct": round(oi_pct * 100, 2),
            "note": f"OI {oi:,} ({oi_chg:+,}, {oi_pct*100:+.1f}%), Leveraged net "
                    f"{lev['net_chg']:+,} - {flow} "
                    f"{'long' if direction > 0 else 'short' if direction < 0 else 'flat'}"}


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
    # Indices are less volatile than commodities in percentage terms, so the same tanh
    # constants would leave every reading bunched near zero. Scaled up to match the range an
    # index actually moves in: ~2% above the 50-day is a strong trend for the S&P, where it
    # would be unremarkable for crude.
    score = 100 * (0.6 * math.tanh(dev * 15) + 0.4 * math.tanh(mom * 12))
    return {"score": round(score, 1), "last": round(last, 2), "ma": round(ma, 2),
            "dev_pct": round(dev * 100, 2), "mom_20d_pct": round(mom * 100, 2),
            "chg_5d": round(chg_5d, 4),
            "note": f"{last:,.0f} vs 50-day avg {ma:,.0f} ({dev*100:+.1f}%), "
                    f"20-day {mom*100:+.1f}%, last 5-day {chg_5d*100:+.1f}%"}


def _seasonality_leg(sym, seas):
    d = (seas.get("instruments") or {}).get(sym) or {}
    tm = d.get("this_month") or {}
    if d.get("score") is None:
        return {"score": 0.0, "note": "no seasonal statistics yet"}
    tail = " - tail-driven, pays on average not most years" if tm.get("tail_driven") else ""
    weak = "" if tm.get("reliable") else " (below the reliability bar, scored at half)"
    return {"score": d["score"],
            "excess": tm.get("excess"), "hit": tm.get("hit"), "t": tm.get("t"),
            "n": tm.get("n"), "reliable": tm.get("reliable", False),
            "note": f"{seas.get('month')}: {tm.get('excess'):+.2f}% excess over a normal month, "
                    f"hit {tm.get('hit')}% of {tm.get('n')} years, t={tm.get('t'):+.2f}"
                    f"{weak}{tail}"}


def _overlay_leg(sym, manual):
    m = manual.get(sym, {})
    wanted = OVERLAY_INDICATORS.get(sym, [])
    scores, notes = {}, {}
    for ind, v in m.items():
        try:
            s = float(v["score"]) if isinstance(v, dict) else float(v)
        except (TypeError, ValueError, KeyError):
            continue
        scores[ind] = s
        if isinstance(v, dict) and v.get("note"):
            notes[ind] = v["note"]
    unset = [i for i in wanted if i not in scores]
    if not scores:
        return {"score": 0.0, "coverage": 0, "unset": unset, "notes": {},
                "note": "no overlay set - held neutral"}
    avg = sum(scores.values()) / len(scores)
    have = sum(1 for i in wanted if i in scores)
    return {"score": round((avg - 3) / 2 * 100, 1),
            "coverage": round(100 * have / len(wanted)) if wanted else 100,
            "avg_1_5": round(avg, 2), "unset": unset, "notes": notes,
            "note": f"{len(scores)} indicator(s) set, avg {avg:.2f}/5"}


def build():
    cot = _load(DATA / "cot.json", {})
    px = _load(DATA / "prices_index.json", {"symbols": {}})
    seas = _load(DATA / "seasonality.json", {})
    manual = _load_manual()
    hist = _load(DATA / "cot_history.json", {})       # indices ride in the FX history file

    lev_net, lev_date = {}, {}
    for d in sorted(hist):
        for sym in INDEX_ORDER:
            v = (((hist[d] or {}).get(sym) or {}).get("leveraged") or {}).get("net")
            if v is not None:
                lev_net.setdefault(sym, []).append(v)
                lev_date.setdefault(sym, []).append(d)

    rows = {}
    for sym in INDEX_ORDER:
        c = (cot.get("indices") or {}).get(sym, {})
        p = (px.get("symbols") or {}).get(sym, {})
        legs = {"cot": _cot_leg(c), "oi": _oi_leg(c), "trend": _trend_leg(p),
                "seasonality": _seasonality_leg(sym, seas),
                "overlay": _overlay_leg(sym, manual)}
        parts = {k: legs[k]["score"] for k in INDEX_WEIGHTS}
        blended = sum(parts[k] * INDEX_WEIGHTS[k] for k in INDEX_WEIGHTS)
        cx = cot_extreme(lev_net.get(sym), lev_date.get(sym))
        total, cot_adj = cot_reversal_adjust(blended, cx, "commodity")
        label, cls = commodity_rating(total)
        raw_5d = legs["trend"].get("chg_5d")
        chg_5d_pct = raw_5d * 100 if isinstance(raw_5d, (int, float)) else None
        read = directional_read(total, chg_5d_pct, "commodity")
        retr = retracement_zone(p.get("closes"), total, "commodity", p.get("dates"))

        # same rounding-residual handling as the commodity cards: the rows must add up to the
        # headline, so park the difference on the largest leg
        contrib = {k: round(parts[k] * INDEX_WEIGHTS[k], 1) for k in INDEX_WEIGHTS}
        residual = round(round(total, 1) - round(cot_adj, 1) - sum(contrib.values()), 1)
        if residual and contrib:
            big = max(contrib, key=lambda k: abs(contrib[k]))
            contrib[big] = round(contrib[big] + residual, 1)

        rows[sym] = {"score": round(total, 1), "score_pre_cot_x": round(blended, 1),
                     "cot_adj": cot_adj, "rating": label, "cls": cls,
                     "parts": {k: round(v, 1) for k, v in parts.items()},
                     "contrib": contrib,
                     "crowded": legs["cot"].get("crowded", False),
                     "thin": legs["cot"].get("thin", False),
                     "read": read, "pullback": read["state"] == "retracement",
                     "chg_5d_pct": round(chg_5d_pct, 1) if chg_5d_pct is not None else None,
                     "retr": retr, "cot_x": cx, "legs": legs}

    ranked = sorted(INDEX_ORDER, key=lambda s: -rows[s]["score"])
    out = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "weights": INDEX_WEIGHTS, "indices": rows, "ranked": ranked,
           "cot_report_date": next((((cot.get("indices") or {}).get(s) or {}).get("report_date")
                                    for s in INDEX_ORDER
                                    if ((cot.get("indices") or {}).get(s) or {}).get("report_date")),
                                   None),
           "price_asof": next((((px.get("symbols") or {}).get(s) or {}).get("dates", [None])[-1]
                               for s in INDEX_ORDER
                               if ((px.get("symbols") or {}).get(s) or {}).get("dates")), None)}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    print(f"  COT {r['cot_report_date']}   prices to {r['price_asof']}")
    print(f"  {'sym':4} {'score':>7}  {'trend':>6} {'cot':>6} {'oi':>6} {'seas':>6} {'ovl':>6}   rating")
    for s in r["ranked"]:
        d = r["indices"][s]
        p = d["parts"]
        print(f"  {s:4} {d['score']:+7.1f}  {p['trend']:+6.1f} {p['cot']:+6.1f} {p['oi']:+6.1f} "
              f"{p['seasonality']:+6.1f} {p['overlay']:+6.1f}   {d['rating']}"
              + ("  [thin contract]" if d["thin"] else ""))
