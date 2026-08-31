"""Blend the commodity track: CFTC Managed Money + open interest + price trend + a manual
macro overlay, into one -100..+100 bias per commodity.

Deliberately NOT wired into score.py. The FX board is relative and zero-sum - it centres on
zero and ranks currencies against each other. Gold, silver and oil are outright directional
bets with no such constraint, so they get their own scorer, their own section, and their own
line in validate.py. Nothing here touches the currency scores.

Legs (config.COMMODITY_WEIGHTS):
  trend    3-day-average close vs the 50-day average, plus the ~20-day change. Leads the blend.
  cot      Managed Money net as a share of OI (60) + weekly flow (40), same tanh math as FX
  oi       open-interest change read against the direction of the Managed Money flow
  overlay  judgment - real yields, the dollar, OPEC+ policy, demand, safe-haven bid - held at a
           true 0 and reported as unset until entered in data/commodities_manual.json

Each row also carries `crowded` (Managed Money net > 35% of OI) and `read` - how the bias
sits against the last week of price: directional / possible retracement / holding / none
(config.directional_read). `pullback` is kept as a bool alias for read.state == "retracement".
Writes data/commodities.json.
"""
import json, math, datetime as dt
from config import (DATA, COMMODITIES, COMMODITY_ORDER, COMMODITY_WEIGHTS,
                    commodity_rating, directional_read, retracement_zone, cot_extreme)

MANUAL_FILE = DATA / "commodities_manual.json"
CROWDED = 0.35

# What the overlay leg is asking for, per commodity. Not enforced - a free-text key in the
# manual file still scores - but this is what the scheduled research pass fills in.
OVERLAY_INDICATORS = {
    "XAU": ["Real yield direction", "US dollar direction", "Central-bank & ETF demand", "Safe-haven bid"],
    "XAG": ["Real yield direction", "US dollar direction", "Industrial demand", "Gold-silver ratio"],
    "WTI": ["OPEC+ supply policy", "Inventory trend", "Demand outlook", "Geopolitical risk premium"],
}


def _load_manual():
    if not MANUAL_FILE.exists():
        seed = {"_help": "Per commodity, an indicator->1-5 map (5 = bullish). A bare number or "
                         "{\"score\": n, \"note\": \"why\"} for auditability. Unset = held at "
                         "neutral and excluded from the average, exactly like fundamentals_manual.",
                **{s: {} for s in COMMODITY_ORDER}}
        MANUAL_FILE.write_text(json.dumps(seed, indent=2), encoding="utf-8")
        return {s: {} for s in COMMODITY_ORDER}
    try:
        raw = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
    except Exception as e:                          # hand-edited file - bad JSON must not crash
        print(f"  commodities_manual.json unreadable ({type(e).__name__}: {e}) - overlay off")
        return {s: {} for s in COMMODITY_ORDER}
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _cot_leg(c):
    mm, oi = c.get("managed_money"), c.get("open_interest")
    if not mm or not oi:
        return {"score": 0.0, "note": "no COT data"}
    level_ratio = mm["net"] / oi
    flow_ratio = mm["net_chg"] / oi
    level = 100 * math.tanh(level_ratio * 3.0)
    flow = 100 * math.tanh(flow_ratio * 20.0)
    crowded = abs(level_ratio) > CROWDED
    return {
        "score": round(0.6 * level + 0.4 * flow, 1),
        "level": round(level, 1), "flow": round(flow, 1),
        "net": mm["net"], "net_chg": mm["net_chg"], "oi": oi,
        "net_pct_oi": round(level_ratio * 100, 1), "crowded": crowded,
        "note": f"Managed Money net {mm['net']:+,} ({level_ratio*100:+.1f}% of OI), "
                f"week {mm['net_chg']:+,}" + (" - CROWDED, squeeze risk" if crowded else ""),
    }


def _oi_leg(c):
    oi, oi_chg, mm = c.get("open_interest"), c.get("oi_change"), c.get("managed_money")
    if not oi or oi_chg is None or not mm:
        return {"score": 0.0, "note": "no OI data"}
    prev = oi - oi_chg
    oi_pct = (oi_chg / prev) if prev else 0.0
    direction = 1 if mm["net_chg"] > 0 else (-1 if mm["net_chg"] < 0 else 0)
    conviction = math.tanh(abs(oi_pct) * 8)
    score = direction * conviction * 100
    flow = "new money"
    if oi_chg < 0:
        score *= 0.5
        flow = "liquidation"
    return {
        "score": round(score, 1), "oi": oi, "oi_chg": oi_chg,
        "oi_pct": round(oi_pct * 100, 2),
        "note": f"OI {oi:,} ({oi_chg:+,}, {oi_pct*100:+.1f}%), Managed Money net "
                f"{mm['net_chg']:+,} - {flow} "
                f"{'long' if direction > 0 else 'short' if direction < 0 else 'flat'}",
    }


def _trend_leg(px):
    closes = px.get("closes") or []
    if len(closes) < 26:
        return {"score": 0.0, "chg_5d": None, "note": "not enough price history"}
    last = closes[-1]
    # Yahoo revises the front-month settlement after the session, so the LAST bar wobbles ~1%
    # between fetches for a day or two. Averaging the last 3 closes keeps the score
    # reproducible run to run while still reflecting recent price; `last` and the 5-day
    # pullback check use the live print.
    ref = sum(closes[-3:]) / 3
    win = closes[-50:] if len(closes) >= 50 else closes
    ma = sum(win) / len(win)
    dev = (ref - ma) / ma                        # % above / below the average
    mom = (ref - closes[-20]) / closes[-20]      # ~20-day change
    chg_5d = (last - closes[-6]) / closes[-6]    # last week, for the pullback check
    # tanh scaling deliberately not too tight: a parabolic move should still read as strong
    # trend, but not pin at +/-100, because an over-extended move carries mean-reversion risk
    tp = math.tanh(dev * 6)
    tm = math.tanh(mom * 5)
    score = 100 * (0.6 * tp + 0.4 * tm)
    return {
        "score": round(score, 1), "last": last, "ma": round(ma, 2),
        "dev_pct": round(dev * 100, 2), "mom_20d_pct": round(mom * 100, 2),
        "chg_5d": round(chg_5d, 4),
        "note": f"{last:g} vs 50-day avg {ma:.0f} ({dev*100:+.1f}%), "
                f"20-day {mom*100:+.1f}%, last 5-day {chg_5d*100:+.1f}%",
    }


def _overlay_leg(sym, manual):
    m = manual.get(sym, {})
    wanted = OVERLAY_INDICATORS.get(sym, [])
    scores, notes = {}, {}
    for ind, v in m.items():                       # canonical or free-text, both score
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
                "note": "no macro overlay set - held neutral"}
    avg = sum(scores.values()) / len(scores)
    # coverage = how many of the canonical indicators are set (free-text extras don't inflate it)
    have = sum(1 for i in wanted if i in scores)
    return {
        "score": round((avg - 3) / 2 * 100, 1),
        "coverage": round(100 * have / len(wanted)) if wanted else 100,
        "avg_1_5": round(avg, 2), "unset": unset, "notes": notes,
        "note": f"{len(scores)} indicator(s) set, avg {avg:.2f}/5",
    }


def build():
    cot = json.loads((DATA / "cot.json").read_text(encoding="utf-8"))
    pxf = DATA / "prices_commodity.json"
    try:
        px = json.loads(pxf.read_text(encoding="utf-8")) if pxf.exists() else {"symbols": {}}
    except Exception as e:
        print(f"  prices_commodity.json unreadable ({type(e).__name__}: {e}) - trend leg off")
        px = {"symbols": {}}
    manual = _load_manual()

    chp = DATA / "cot_history_commodity.json"
    try:
        chist = json.loads(chp.read_text(encoding="utf-8")) if chp.exists() else {}
    except Exception:
        chist = {}
    mm_net = {}
    for d in sorted(chist):
        week = chist[d] or {}
        for sym in COMMODITY_ORDER:
            v = ((week.get(sym) or {}).get("managed_money") or {}).get("net")
            if v is not None:
                mm_net.setdefault(sym, []).append(v)

    rows = {}
    for sym in COMMODITY_ORDER:
        c = cot.get("commodities", {}).get(sym, {})
        p = px.get("symbols", {}).get(sym, {})
        legs = {
            "cot": _cot_leg(c),
            "oi": _oi_leg(c),
            "trend": _trend_leg(p),
            "overlay": _overlay_leg(sym, manual),
        }
        parts = {k: legs[k]["score"] for k in COMMODITY_WEIGHTS}
        total = sum(parts[k] * COMMODITY_WEIGHTS[k] for k in COMMODITY_WEIGHTS)
        label, cls = commodity_rating(total)
        # directional / retracement read: the score is medium-term; classify it against the
        # last week of price. `read.state == "retracement"` is the old `pullback` flag.
        raw_5d = legs["trend"].get("chg_5d")
        chg_5d_pct = raw_5d * 100 if isinstance(raw_5d, (int, float)) else None
        read = directional_read(total, chg_5d_pct, "commodity")
        retr = retracement_zone(p.get("closes"), total, "commodity", p.get("dates"))
        rows[sym] = {
            "score": round(total, 1), "rating": label, "cls": cls,
            "parts": {k: round(v, 1) for k, v in parts.items()},
            "contrib": {k: round(parts[k] * COMMODITY_WEIGHTS[k], 1) for k in COMMODITY_WEIGHTS},
            "crowded": legs["cot"].get("crowded", False),
            "read": read, "pullback": read["state"] == "retracement",
            "chg_5d_pct": round(chg_5d_pct, 1) if chg_5d_pct is not None else None,
            "retr": retr, "cot_x": cot_extreme(mm_net.get(sym)), "legs": legs,
        }

    ranked = sorted(COMMODITY_ORDER, key=lambda s: -rows[s]["score"])
    gs_ratio = None
    xau, xag = px.get("symbols", {}).get("XAU", {}), px.get("symbols", {}).get("XAG", {})
    if xau.get("last") and xag.get("last"):
        gs_ratio = round(xau["last"] / xag["last"], 1)

    out = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "weights": COMMODITY_WEIGHTS, "commodities": rows, "ranked": ranked,
           "cot_report_date": next((cot["commodities"][s].get("report_date")
                                    for s in COMMODITY_ORDER
                                    if cot.get("commodities", {}).get(s, {}).get("report_date")), None),
           "gold_silver_ratio": gs_ratio,
           "price_asof": next((px["symbols"][s].get("dates", [None])[-1]
                               for s in COMMODITY_ORDER
                               if px.get("symbols", {}).get(s, {}).get("dates")), None)}
    (DATA / "commodities.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    hist_file = DATA / "commodity_history.json"
    try:
        hist = json.loads(hist_file.read_text(encoding="utf-8")) if hist_file.exists() else []
    except Exception:
        hist = []
    hist.append({"at": out["built_at"], "scores": {s: rows[s]["score"] for s in COMMODITY_ORDER}})
    hist_file.write_text(json.dumps(hist[-2000:], indent=0), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    print(f"  COT {r['cot_report_date']}   prices to {r['price_asof']}   "
          f"gold/silver {r['gold_silver_ratio']}")
    print(f"  {'sym':4} {'score':>7}  {'cot':>6} {'oi':>6} {'trend':>6} {'ovl':>6}   rating")
    for s in r["ranked"]:
        d = r["commodities"][s]
        p = d["parts"]
        print(f"  {s:4} {d['score']:+7.1f}  {p['cot']:+6.1f} {p['oi']:+6.1f} "
              f"{p['trend']:+6.1f} {p['overlay']:+6.1f}   {d['rating']}")
