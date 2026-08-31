"""Lagged backtest of the BLENDED score, not just the COT leg.

backtest.py only tested positioning - 15% of the score. This reconstructs the whole signal
as it would have stood at each past COT release and measures it on FORWARD returns.

Reconstructable from history:
    cot, oi        from the dated tradingster reports (cached by backtest.py)
    news           from the calendar API's historical actuals
    checklist      the AUTO half - indicators with a released datapoint behind them
NOT reconstructable:
    the MANUAL half of the checklist (CB stance, safe-haven status, ...). There is no
    archive of what was believed on a past date, so those sit at neutral 3 here. The
    backtest therefore measures a weaker model than the live one - treat it as a floor.

    python backtest_blend.py [weeks]        default 14

Requires backtest.py to have run first (it populates data/cot_history.json).
"""
import json, math, sys, datetime as dt
from config import (DATA, ORDER, WEIGHTS, CHECKLIST, NEWS_HALFLIFE_HOURS,
                    NEWS_IMPACT_WEIGHT, COT_CATEGORY)
from fetch_calendar import fetch, surprise, COUNTRY, IMPORTANCE, base_indicator
from fundamentals import AUTO_MAP, MANUAL_ONLY, ALL_INDICATORS, WORSE_WHEN_HIGH
from backtest import cot_score, prices, fwd, spearman

CAL_CACHE = DATA / "calendar_history.json"


def calendar_span(start, end):
    """Whole-span calendar, paged - the API caps at 2000 rows and truncates from the start."""
    if CAL_CACHE.exists():
        c = json.loads(CAL_CACHE.read_text(encoding="utf-8"))
        if c.get("start") <= str(start) and c.get("end") >= str(end):
            return c["rows"]
    rows, cur, step = [], start, dt.timedelta(days=35)
    while cur < end:
        nxt = min(cur + step, end)
        try:
            rows.extend(fetch(dt.datetime.combine(cur, dt.time()).replace(tzinfo=dt.timezone.utc),
                              dt.datetime.combine(nxt, dt.time()).replace(tzinfo=dt.timezone.utc)))
            print(f"  calendar {cur} .. {nxt}: {len(rows)} rows", flush=True)
        except Exception as e:
            print(f"  calendar slice {cur} failed: {type(e).__name__}")
        cur = nxt
    CAL_CACHE.write_text(json.dumps({"start": str(start), "end": str(end), "rows": rows}), encoding="utf-8")
    return rows


def normalise(rows):
    out = []
    for ev in rows:
        ccy = COUNTRY.get(ev.get("country"))
        if not ccy:
            continue
        s = surprise(ev)
        if s is None:
            continue
        out.append({"ccy": ccy, "title": ev["title"],
                    "when": dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00")),
                    "impact": IMPORTANCE.get(ev.get("importance"), "Low"), "surprise": s,
                    "actual_raw": ev.get("actualRaw"), "previous_raw": ev.get("previousRaw"),
                    "unit": ev.get("unit") or ""})
    return out


def news_asof(events, asof):
    """Same weighted-mean-with-confidence rule as the live pipeline."""
    latest = {}
    for e in sorted((e for e in events if e["when"] <= asof), key=lambda r: r["when"]):
        latest[(e["ccy"], base_indicator(e["title"]))] = e
    num = {c: 0.0 for c in ORDER}
    den = {c: 0.0 for c in ORDER}
    for e in latest.values():
        age_h = (asof - e["when"]).total_seconds() / 3600
        if age_h < 0 or age_h > NEWS_HALFLIFE_HOURS * 4:
            continue
        w = NEWS_IMPACT_WEIGHT.get(e["impact"], 0.15) * 0.5 ** (age_h / NEWS_HALFLIFE_HOURS)
        num[e["ccy"]] += e["surprise"] * w
        den[e["ccy"]] += w
    return {c: (num[c] / den[c] * math.tanh(den[c] / 1.5) * 100) if den[c] > 0 else 0.0
            for c in ORDER}


def checklist_auto_asof(events, asof):
    """Mirrors the live checklist: LEVELS, not surprises.

    Percent-unit series are ranked cross-sectionally against the other currencies;
    everything else is scored on its own direction of travel. No forecast is used, so
    this leg is independent of the news leg by construction.
    """
    latest = {}
    for e in sorted((e for e in events if e["when"] <= asof), key=lambda r: r["when"]):
        if e.get("actual_raw") is None:
            continue
        t = e["title"].lower()
        for ind, keys in AUTO_MAP.items():
            if ind in ALL_INDICATORS and ind not in MANUAL_ONLY and any(k in t for k in keys):
                latest[(e["ccy"], ind)] = e

    auto = {c: {} for c in ORDER}
    for ind in {i for (_, i) in latest}:
        group = {c: latest[(c, ind)] for c in ORDER if (c, ind) in latest}
        invert = any(k in ind.lower() for k in WORSE_WHEN_HIGH)
        comparable = [c for c, e in group.items() if e.get("unit") == "%"]
        if len(comparable) >= 3:
            vals = {c: group[c]["actual_raw"] for c in comparable}
            lo, hi = min(vals.values()), max(vals.values())
            span = (hi - lo) or 1.0
            for c, v in vals.items():
                pos = (v - lo) / span
                auto[c][ind] = 1 + 4 * ((1 - pos) if invert else pos)
            rest = [c for c in group if c not in comparable]
        else:
            rest = list(group)
        for c in rest:
            a, p = group[c].get("actual_raw"), group[c].get("previous_raw")
            if a is None or p is None:
                continue
            z = math.tanh((a - p) / max(abs(p), 0.1))
            if invert:
                z = -z
            auto[c][ind] = max(1.0, min(5.0, 3 + 2 * z))

    out = {}
    for c in ORDER:
        vals = [sum(auto[c].get(i, 3.0) for i in inds) / len(inds) for inds in CHECKLIST.values()]
        out[c] = (sum(vals) / len(vals) - 3) / 2 * 100
    return out


def oi_asof(rec):
    oi, chg, g = rec.get("open_interest"), rec.get("oi_change"), rec.get(COT_CATEGORY)
    if not oi or chg is None or not g:
        return 0.0
    prev = oi - chg
    pct = (chg / prev) if prev else 0.0
    direction = 1 if g["net_chg"] > 0 else (-1 if g["net_chg"] < 0 else 0)
    sc = direction * math.tanh(abs(pct) * 8) * 100
    return sc * 0.5 if chg < 0 else sc


def main():
    weeks = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    hist = json.loads((DATA / "cot_history.json").read_text(encoding="utf-8"))
    dates = sorted(hist)[-weeks:]
    d0 = dt.date.fromisoformat(dates[0])

    print(f"reconstructing {len(dates)} weeks from {dates[0]}")
    events = normalise(calendar_span(d0 - dt.timedelta(days=50), dt.date.today()))
    print(f"  {len(events)} scoreable calendar events\n")

    fx = prices(d0, dt.date.today())
    days = sorted(fx)

    variants = {"blended": [], "news only": [], "checklist only": [], "cot only": []}
    rows = []
    for d in dates:
        recs = hist[d]
        D = dt.date.fromisoformat(d)
        entry = D + dt.timedelta(days=3)                       # Friday release
        asof = dt.datetime.combine(entry, dt.time(20), tzinfo=dt.timezone.utc)
        m = fwd(fx, days, entry, entry + dt.timedelta(days=7))
        if not m:
            continue

        nw = news_asof(events, asof)
        ck = checklist_auto_asof(events, asof)
        ct = {c: cot_score(r, COT_CATEGORY) for c, r in recs.items() if r}
        ct = {c: v for c, v in ct.items() if v is not None}
        oi = {c: oi_asof(r) for c, r in recs.items() if r}
        if len(ct) < len(ORDER):
            continue

        # USD from the inverse OI-weighted basket, as the live model does
        num = den = 0.0
        for c in ORDER:
            if c == "USD":
                continue
            w = recs[c]["open_interest"] or 0
            num += ct[c] * w
            den += w
        if den:
            ct["USD"] = 0.75 * (-num / den) + 0.25 * ct["USD"]

        blend = {c: (WEIGHTS["fundamentals"] * ck[c] + WEIGHTS["cot"] * ct[c]
                     + WEIGHTS["oi"] * oi.get(c, 0) + WEIGHTS["news"] * nw[c]) for c in ORDER}
        mean = sum(blend.values()) / len(blend)
        blend = {c: v - mean for c, v in blend.items()}

        row = {"date": d}
        for name, sv in (("blended", blend), ("news only", nw),
                         ("checklist only", ck), ("cot only", ct)):
            rho = spearman(sv, m, ORDER)
            if rho is not None:
                variants[name].append(rho)
                row[name] = rho
        rows.append(row)

    print("FORWARD-WEEK rank correlation, signal at Friday release -> next week's move")
    hdr = f"{'report':12}" + "".join(f"{k:>16}" for k in variants)
    print(hdr)
    for r in rows:
        print(f"{r['date']:12}" + "".join(f"{r.get(k, float('nan')):>+16.2f}" for k in variants))

    print()
    for name, vals in variants.items():
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        se = sd / math.sqrt(len(vals)) if len(vals) > 1 else float("nan")
        t = mean / se if se else float("nan")
        print(f"  {name:16} mean rho {mean:+.3f}   positive {sum(1 for v in vals if v>0)}/{len(vals)}"
              f"   t {t:+.2f}")
    print("\n  |t| under ~2 means not distinguishable from luck at this sample size.")
    print("  The manual half of the checklist is absent here, so this is a FLOOR on the live model.")


if __name__ == "__main__":
    main()
