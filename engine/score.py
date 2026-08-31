"""Blend the four inputs into a per-currency strength score, daily bias, and pair ranking."""
import json, math, datetime as dt
from config import (DATA, ORDER, WEIGHTS, COT_CATEGORY, rating,
                    directional_read, retracement_zone, cot_extreme, cot_reversal_adjust)

CROWDED = 0.35   # |net| / open interest above this = crowded, squeeze-prone


def cot_score(cot):
    """Positioning score per currency.

    USD needs special handling. Its own contract is the ICE dollar index, which is thin
    (48k open interest against 805k for EUR), while EVERY other contract is already a
    position on that currency versus the dollar. Scoring USD only from the small contract
    let the board hold contradictory views - USD, AUD, GBP and EUR all bullish at once,
    summing to +34 instead of ~0. USD is therefore read mostly as the inverse of the
    open-interest-weighted basket, with a minority weight on its own contract.
    """
    out = {}
    for ccy in ORDER:
        d = cot["currencies"].get(ccy, {})
        am, oi = d.get(COT_CATEGORY), d.get("open_interest")
        if not am or not oi:
            out[ccy] = {"score": 0.0, "note": "no data"}
            continue
        level_ratio = am["net"] / oi
        flow_ratio = am["net_chg"] / oi
        level = 100 * math.tanh(level_ratio * 3.0)
        flow = 100 * math.tanh(flow_ratio * 20.0)
        score = 0.6 * level + 0.4 * flow
        crowded = abs(level_ratio) > CROWDED
        out[ccy] = {
            "score": round(score, 1), "level": round(level, 1), "flow": round(flow, 1),
            "net": am["net"], "net_chg": am["net_chg"], "oi": oi,
            "net_pct_oi": round(level_ratio * 100, 1), "crowded": crowded,
            "note": f"asset-mgr net {am['net']:+,} ({level_ratio*100:+.1f}% of OI), "
                    f"week {am['net_chg']:+,}" + (" - CROWDED, squeeze risk" if crowded else ""),
        }
    # --- USD: inverse of the basket, blended with its own (thin) index contract
    basket_num = basket_den = 0.0
    for c in ORDER:
        if c == "USD" or out[c].get("score") is None:
            continue
        oi = cot["currencies"].get(c, {}).get("open_interest") or 0
        basket_num += out[c]["score"] * oi
        basket_den += oi
    if basket_den and out.get("USD"):
        basket = -basket_num / basket_den
        own = out["USD"]["score"]
        out["USD"]["score"] = round(0.75 * basket + 0.25 * own, 1)
        out["USD"]["basket"] = round(basket, 1)
        out["USD"]["own_contract"] = round(own, 1)
        out["USD"]["note"] = (f"inverse basket {basket:+.0f} (75%) blended with dollar-index "
                              f"contract {own:+.0f} (25%); index OI only "
                              f"{cot['currencies']['USD']['open_interest']:,}")
    return out


def build():
    cot = json.loads((DATA / "cot.json").read_text(encoding="utf-8"))
    cal = json.loads((DATA / "calendar.json").read_text(encoding="utf-8"))
    fun = json.loads((DATA / "fundamentals.json").read_text(encoding="utf-8"))
    oi = json.loads((DATA / "oi.json").read_text(encoding="utf-8"))
    efile = DATA / "rate_expectations.json"
    exp = json.loads(efile.read_text(encoding="utf-8")) if efile.exists() else {"currencies": {}}

    cots = cot_score(cot)
    rows = {}
    for ccy in ORDER:
        parts = {
            "fundamentals": fun["currencies"][ccy]["score"],
            "cot": cots[ccy]["score"],
            "oi": oi["currencies"][ccy]["score"],
            "news": cal["news_score"].get(ccy, 0.0),
            # 1-5 centred on 3 -> -100..100; absent probabilities score a true 0, not a guess
            "expectations": round((exp["currencies"].get(ccy, {}).get("score", 3.0) - 3) / 2 * 100, 1),
        }
        total = sum(parts[k] * WEIGHTS[k] for k in WEIGHTS)
        label, cls = rating(total)
        rows[ccy] = {
            "score": round(total, 1), "rating": label, "cls": cls,
            "parts": {k: round(v, 1) for k, v in parts.items()},
            "contrib": {k: round(parts[k] * WEIGHTS[k], 1) for k in WEIGHTS},
            "cot": cots[ccy], "oi": oi["currencies"][ccy],
            "fundamentals": {"avg_1_5": fun["currencies"][ccy]["avg_1_5"],
                             "categories": fun["currencies"][ccy]["categories"],
                             "coverage": fun["currencies"][ccy]["coverage"],
                             "unset": fun["currencies"][ccy]["unset"],
                             "notes": {k: v for k, v in fun["currencies"][ccy]["indicators"].items()
                                       if v.get("src") == "manual"}},
            "news_drivers": cal["contributors"].get(ccy, []),
            "expectations": exp["currencies"].get(ccy, {}),
        }

    # weekly COT-net history for the positioning-extreme / trend-turn read
    chp = DATA / "cot_history.json"
    try:
        chist = json.loads(chp.read_text(encoding="utf-8")) if chp.exists() else {}
    except Exception:
        chist = {}
    cnet, cdate = {}, {}
    for d in sorted(chist):
        week = chist[d] or {}
        for c in ORDER:
            v = ((week.get(c) or {}).get(COT_CATEGORY) or {}).get("net")
            if v is not None:
                cnet.setdefault(c, []).append(v)
                cdate.setdefault(c, []).append(d)

    # COT extreme -> contrarian pull on the RAW score, before centring, so the tilt is
    # relative (a currency with crowded longs looks weaker vs the pack) and the board still
    # sums to zero afterwards.
    for c in ORDER:
        rows[c]["cot_x"] = cot_extreme(cnet.get(c), cdate.get(c))
        rows[c]["score_pre_cot_x"] = rows[c]["score"]
        rows[c]["score"], rows[c]["cot_adj"] = cot_reversal_adjust(
            rows[c]["score"], rows[c]["cot_x"], "fx")

    # Centre on zero. FX strength is relative by construction - if every currency scores
    # positive the board is incoherent, which is exactly what happened before USD was
    # reconciled with the basket. Centring preserves the ordering and the spreads.
    mean = sum(rows[c]["score"] for c in ORDER) / len(ORDER)
    for c in ORDER:
        rows[c]["raw_score"] = rows[c]["score"]
        rows[c]["score"] = round(rows[c]["score"] - mean, 1)
        rows[c]["rating"], rows[c]["cls"] = rating(rows[c]["score"])
    rows["_centering"] = round(mean, 1)

    # directional / retracement read: the centred score vs this currency's own last-5-day
    # move against USD. Optional feed - a missing or unreadable file just means every read
    # comes back "no price feed to classify", it never breaks the score.
    fxp = DATA / "prices_fx.json"
    try:
        fxd = json.loads(fxp.read_text(encoding="utf-8")) if fxp.exists() else {}
    except Exception:
        fxd = {}
    pfx, pser = fxd.get("moves", {}), fxd.get("series", {})
    pdates = fxd.get("series_dates")

    for c in ORDER:
        d5 = pfx.get(c, {}).get("d5")
        rows[c]["chg_5d_pct"] = d5
        rows[c]["read"] = directional_read(rows[c]["score"], d5, "fx")
        rows[c]["retr"] = retracement_zone(pser.get(c), rows[c]["score"], "fx", pdates)

    ranked = sorted(ORDER, key=lambda c: -rows[c]["score"])

    pairs = []
    for i, a in enumerate(ranked):
        for b in ranked[i + 1:]:
            spread = rows[a]["score"] - rows[b]["score"]
            if abs(spread) < 1:
                continue
            warn = []
            if rows[a]["cot"].get("crowded"):
                warn.append(f"{a} positioning crowded")
            if rows[b]["cot"].get("crowded"):
                warn.append(f"{b} positioning crowded")
            pairs.append({"pair": f"{a}/{b}", "long": a, "short": b,
                          "spread": round(spread, 1), "warnings": warn})
    pairs.sort(key=lambda p: -p["spread"])

    out = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "weights": WEIGHTS, "currencies": rows, "ranked": ranked,
           "pairs": pairs[:12],
           "cot_report_date": cot["currencies"].get("EUR", {}).get("report_date"),
           "next_release": cal.get("next_release"),
           "next_high_impact": cal.get("next_high_impact"),
           "upcoming": cal.get("upcoming", [])[:12],
           "oi_source": oi.get("source"), "oi_cadence": oi.get("cadence")}
    (DATA / "scores.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Append to a rolling history so RATING_BANDS can eventually be set from real
    # percentiles instead of judgement. Keep it small - one row per rebuild.
    hist_file = DATA / "score_history.json"
    try:
        hist = json.loads(hist_file.read_text(encoding="utf-8")) if hist_file.exists() else []
    except Exception:
        hist = []
    hist.append({"at": out["built_at"],
                 "scores": {c: rows[c]["score"] for c in ORDER}})
    hist_file.write_text(json.dumps(hist[-2000:], indent=0), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    print(f"  COT report {r['cot_report_date']}   OI source: {r['oi_source']}")
    print(f"  {'ccy':4} {'score':>7}  {'fund':>6} {'cot':>6} {'oi':>6} {'news':>6}   rating")
    for c in r["ranked"]:
        d = r["currencies"][c]
        p = d["parts"]
        print(f"  {c:4} {d['score']:+7.1f}  {p['fundamentals']:+6.1f} {p['cot']:+6.1f} "
              f"{p['oi']:+6.1f} {p['news']:+6.1f}   {d['rating']}")
    print("\n  top pairs:")
    for p in r["pairs"][:5]:
        print(f"    {p['pair']:9} spread {p['spread']:+6.1f}   " + ("; ".join(p['warnings']) or "-"))
