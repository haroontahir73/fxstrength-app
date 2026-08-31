"""The 31-indicator checklist: auto-derive what the calendar can prove, keep the rest manual.

Each indicator is scored 1-5 (5 = strongly bullish for that currency, 1 = strongly bearish),
matching the original workbook. Two sources feed it:

  auto    - indicators backed by a datapoint that actually released. Mapped from calendar
            surprises: score = 3 + 2*surprise, so an in-line print sits at neutral 3.
  manual  - judgment calls with no single release behind them (CB stance, political
            stability, debt level...). Held in data/fundamentals_manual.json and edited
            by hand or by the scheduled run. Anything unset stays at neutral 3 and is
            reported as unset rather than silently counted as a real reading.

Writes data/fundamentals.json.
"""
import json, math, datetime as dt
from config import CHECKLIST, DATA, ORDER

MANUAL_FILE = DATA / "fundamentals_manual.json"

# indicator -> keywords matched against released calendar event titles
AUTO_MAP = {
    "GDP rate":            ["gdp growth rate", "gdp yoy"],
    "GDP surprise":        ["gdp"],
    "PMI Manufacturing":   ["manufacturing pmi", "ism manufacturing"],
    "PMI Services":        ["services pmi", "ism services", "non-manufacturing"],
    "CPI YoY":             ["inflation rate yoy", "cpi yoy"],
    "Core inflation":      ["core inflation", "core cpi", "core pce", "trimmed mean"],
    "Inflation trend":     ["inflation rate mom", "cpi mom"],
    "Unemployment rate":   ["unemployment rate"],
    "Job creation":        ["employment change", "payrolls", "nonfarm", "employment chg"],
    "Wage growth":         ["wage", "earnings", "labour cost", "labor cost"],
    "Labour participation":["participation rate"],
    "Trade balance":       ["balance of trade", "trade balance"],
    "Export growth":       ["exports"],
    "Import growth":       ["imports"],
    "Current account":     ["current account"],
    "Consumer confidence": ["consumer confidence", "consumer sentiment", "consumer climate",
                            "gfk consumer", "consumer morale"],
    # YoY only - MoM retail is too noisy to rank one country against another
    "Retail sales":        ["retail sales yoy"],
}
# indicators that are inherently judgment; never auto-filled. `Fiscal balance` is here rather
# than in AUTO_MAP because the monthly budget prints are seasonal and unit-inconsistent
# ($bn / C$ / % of GDP) across countries, and a near-zero previous value pins the direction
# score - the same reason `Debt level` is manual. The daily research pass sets it.
MANUAL_ONLY = {"Real yield",
               "Inflation vs target", "QE status",
               "FX intervention risk", "Political stability", "Risk premium",
               "Safe haven status", "Debt level", "Fiscal balance"}

ALL_INDICATORS = [i for cat in CHECKLIST.values() for i in cat]

# Scored as their own top-level component now, so they are still DISPLAYED in the checklist
# but excluded from its average - otherwise they would be counted twice.
SCORED_ELSEWHERE = {"Next meeting expectation", "Expected rate change"}


def _load_manual():
    if MANUAL_FILE.exists():
        raw = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    seed = {c: {} for c in ORDER}
    MANUAL_FILE.write_text(json.dumps(seed, indent=2), encoding="utf-8")
    return seed


def _rate_scores(rates):
    """Policy rate (relative carry) and rate differential vs USD, both 1-5."""
    out = {c: {} for c in ORDER}
    if not rates:
        return out
    vals = {c: d["rate"] for c, d in rates["currencies"].items() if d.get("rate") is not None}
    if not vals:
        return out
    lo, hi = min(vals.values()), max(vals.values())
    span = (hi - lo) or 1.0
    for ccy, r in vals.items():
        d = rates["currencies"][ccy]
        out[ccy]["Policy rate"] = {
            "score": round(1 + 4 * (r - lo) / span, 2),
            "basis": f"{r:.2f}% ({d['title']}, {d['as_of']}); range across G7 {lo:.2f}-{hi:.2f}%",
            "when": d["as_of"],
        }
        diff = d.get("diff_vs_usd")
        if diff is not None:
            out[ccy]["Rate differential vs USD"] = {
                "score": round(max(1.0, min(5.0, 3 + 2 * math.tanh(diff / 2))), 2),
                "basis": f"{diff:+.2f}pp vs USD {rates['usd_rate']:.2f}%",
                "when": d["as_of"],
            }
    return out


# Higher print is BEARISH for the currency (used for level scoring, not surprise scoring).
WORSE_WHEN_HIGH = ("unemployment", "jobless", "claims", "deficit", "imports",
                   "borrowing", "delinquen", "bankrupt")


def _auto_scores(calendar):
    """Score the LEVEL each economy is actually at - deliberately NOT the surprise.

    The previous version scored `3 + 2*surprise`, i.e. actual-versus-forecast. That is the
    exact quantity the news leg already measures, only without news's time decay, so the
    checklist was a stale duplicate of it. Backtested over 14 weeks the news leg came in at
    +0.194 while this leg came in at -0.117 - redundant AND worse.

    So this now asks a different question: not "did it beat expectations" but "where does
    this economy stand". Two ways, depending on whether the number is comparable across
    countries:
        percent-unit series  -> ranked cross-sectionally against the other currencies,
                                so 5 = best of the seven, 1 = worst
        everything else      -> its own trend, actual versus previous, since an absolute
                                count (US payrolls vs NZ employment change) means nothing
                                ranked against another country
    Neither uses `forecast`, so this leg is now independent of the news leg by construction.
    """
    rel = sorted(calendar.get("released", []), key=lambda r: r["when"])

    # latest reading per (currency, checklist indicator)
    latest = {}
    for ev in rel:
        if ev.get("actual_raw") is None or ev["ccy"] not in ORDER:
            continue
        title = ev["title"].lower()
        for ind, keys in AUTO_MAP.items():
            if ind not in ALL_INDICATORS or ind in MANUAL_ONLY:
                continue
            if any(k in title for k in keys):
                latest[(ev["ccy"], ind)] = ev

    out = {c: {} for c in ORDER}
    indicators = {ind for (_, ind) in latest}
    for ind in indicators:
        group = {c: latest[(c, ind)] for c in ORDER if (c, ind) in latest}
        invert = any(k in ind.lower() for k in WORSE_WHEN_HIGH)

        comparable = [c for c, e in group.items() if e.get("unit") == "%"]
        if len(comparable) >= 3:
            vals = {c: group[c]["actual_raw"] for c in comparable}
            lo, hi = min(vals.values()), max(vals.values())
            span = (hi - lo) or 1.0
            for c, v in vals.items():
                pos = (v - lo) / span
                if invert:
                    pos = 1 - pos
                e = group[c]
                out[c][ind] = {
                    "score": round(1 + 4 * pos, 2),
                    "basis": f"{e['title']}: {e['actual']} - ranked {sorted(vals, key=lambda k: -vals[k]).index(c)+1}"
                             f" of {len(vals)} across the board"
                             + (" (lower is better)" if invert else ""),
                    "when": e["when"], "impact": e["impact"],
                }
            rest = [c for c in group if c not in comparable]
        else:
            rest = list(group)

        # not cross-country comparable: score its own direction of travel instead
        for c in rest:
            e = group[c]
            a, p = e.get("actual_raw"), e.get("previous_raw")
            if a is None or p is None:
                continue
            scale = max(abs(p), 0.1)
            z = math.tanh((a - p) / scale)
            if invert:
                z = -z
            out[c][ind] = {
                "score": round(max(1.0, min(5.0, 3 + 2 * z)), 2),
                "basis": f"{e['title']}: {e['actual']} vs {e['previous']} previous"
                         + (" (lower is better)" if invert else ""),
                "when": e["when"], "impact": e["impact"],
            }
    return out


def _stance_scores(speakers):
    """Central-bank commentary -> CB stance and next-meeting expectation, 1-5.

    Only currencies with at least one TONE-SCORED speech get a value; an unscored
    calendar full of speakers is not evidence of anything and stays neutral."""
    out = {c: {} for c in ORDER}
    if not speakers:
        return out
    have = {}
    for e in speakers.get("scored", []):
        if not e.get("central_bank", True):
            continue          # treasuries and finance ministries do not set rates
        have.setdefault(e["ccy"], []).append(e)
    for ccy, evs in have.items():
        st = speakers["stance"].get(ccy, 0.0)
        score = round(max(1.0, min(5.0, 3 + 2 * st / 100)), 2)
        top = max(evs, key=lambda x: abs(x["points"]))
        basis = (f"{len(evs)} scored speech(es); loudest: {top['title']} "
                 f"tone {top['tone']:+.1f} weight {top['seniority']:.2f}")
        out[ccy]["CB stance"] = {"score": score, "basis": basis, "when": top["when"]}
        out[ccy]["Next meeting expectation"] = {
            "score": score, "basis": basis + " (read as guidance for the next meeting)",
            "when": top["when"]}
    return out


def _expectation_scores(expect):
    """Market-implied rate probabilities -> next-meeting and expected-change indicators."""
    out = {c: {} for c in ORDER}
    if not expect:
        return out
    for ccy, d in expect.get("currencies", {}).items():
        if ccy not in out or "score" not in d:
            continue
        for ind in ("Next meeting expectation", "Expected rate change"):
            out[ccy][ind] = {"score": d["score"], "basis": d["basis"],
                             "when": expect.get("fetched_at", "")}
    return out


def build(calendar, rates=None, speakers=None, expect=None):
    manual = _load_manual()
    auto = _auto_scores(calendar)
    for ccy, inds in _rate_scores(rates).items():
        auto.setdefault(ccy, {}).update(inds)
    for ccy, inds in _stance_scores(speakers).items():
        auto.setdefault(ccy, {}).update(inds)
    # market-implied probabilities outrank a stance read off a speech for these two
    for ccy, inds in _expectation_scores(expect).items():
        auto.setdefault(ccy, {}).update(inds)
    result = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(), "currencies": {}}

    for ccy in ORDER:
        cats, flat, unset = {}, {}, []
        for cat, inds in CHECKLIST.items():
            vals = []
            for ind in inds:
                m = manual.get(ccy, {}).get(ind)
                a = auto.get(ccy, {}).get(ind)
                if m is not None:
                    # accept a bare number, or {"score": x, "note": "why"} for auditability
                    if isinstance(m, dict):
                        entry = {"score": float(m["score"]), "src": "manual",
                                 "basis": m.get("note", "manual override"),
                                 "as_of": m.get("as_of", "")}
                    else:
                        entry = {"score": float(m), "src": "manual", "basis": "manual override"}
                elif a and ind not in MANUAL_ONLY:
                    entry = {"score": a["score"], "src": "auto", "basis": a["basis"],
                             "when": a["when"]}
                else:
                    entry = {"score": 3.0, "src": "unset", "basis": "no reading - held neutral"}
                    unset.append(ind)
                flat[ind] = entry
                if ind not in SCORED_ELSEWHERE:
                    vals.append(entry["score"])
            cats[cat] = round(sum(vals) / len(vals), 2) if vals else 3.0

        avg = sum(cats.values()) / len(cats)
        result["currencies"][ccy] = {
            "indicators": flat, "categories": cats,
            "avg_1_5": round(avg, 2),
            # centre 1-5 on zero and stretch to -100..100
            "score": round((avg - 3) / 2 * 100, 1),
            "unset": unset,
            "coverage": round(100 * (1 - len(unset) / len(ALL_INDICATORS))),
        }
    (DATA / "fundamentals.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    cal = json.loads((DATA / "calendar.json").read_text(encoding="utf-8"))
    rfile = DATA / "rates.json"
    sfile = DATA / "speakers.json"
    r = build(cal,
              json.loads(rfile.read_text(encoding="utf-8")) if rfile.exists() else None,
              json.loads(sfile.read_text(encoding="utf-8")) if sfile.exists() else None)
    for c in ORDER:
        d = r["currencies"][c]
        print(f"  {c}  avg {d['avg_1_5']:.2f}/5   score {d['score']:+6.1f}   coverage {d['coverage']:>3}%")
