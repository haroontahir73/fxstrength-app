"""The signal matrix: eight currencies against eighteen factors, one grid.

WHAT THIS IS FOR. The strength meter gives one number per currency and the breakdown cards
explain it, but neither answers the question a grid answers instantly: is this currency strong
because everything agrees, or because one loud factor is dragging six quiet ones? A score of
+12 built from fifteen mildly bullish factors is a different trade from a +12 built from one
enormous news surprise against a bearish everything-else.

TWO NUMBERS PER ROW, AND WHY NOT ONE.

  score       the desk's existing WEIGHTED score - news 0.40, fundamentals 0.25, cot 0.15 and
              so on, the weights the backtests in config.py actually established. This stays
              the headline. It is the number to trade off.
  confluence  how many of the eighteen factors lean each way. This is what the grid is good
              at and the weighted score cannot tell you: agreement.

A deliberate departure from the terminal this layout is modelled on, which scores each of its
eighteen factors as a discrete +/-2 and takes the plain SUM - every factor weighted equally on
a +/-36 scale. That is easy to read and easy to explain, and it is worse: it hands seasonality
the same vote as CPI, and it collapses a 0.1% inflation beat and a 1.0% beat into the same
cell. This module keeps the readable grid and the +/-2 colour buckets, but every cell carries
its real continuous value underneath, and the headline stays weighted.

CELLS. Every factor is on the same -100..+100 scale as the rest of the desk, bucketed for
colour only: >= +40 strong, >= +12 mild, inside +/-12 flat, and the mirror below zero. A
factor with no data is a dot, not a zero - "no reading" and "neutral reading" are different
statements and the grid must not blur them.

Reads score.json, yields.json, sentiment.json, seasonality.json, prices_fx.json,
fundamentals.json. Writes data/matrix.json.
"""
import json, datetime as dt
from config import DATA, ORDER, all_pairs

OUT = DATA / "matrix.json"

# (key, short label, group). Order here is the column order on the tab.
FACTORS = [
    ("trend",       "Trend",  "Technical"),
    ("momentum",    "Mom",    "Technical"),
    ("seasonality", "Seas",   "Technical"),
    ("cot",         "COT",    "Sentiment"),
    ("crowd",       "Crowd",  "Sentiment"),
    ("oi",          "OI",     "Sentiment"),
    ("carry",       "Carry",  "Rates & policy"),
    ("real",        "Real",   "Rates & policy"),
    ("curve",       "Curve",  "Rates & policy"),
    ("expect",      "Expect", "Rates & policy"),
    ("cbank",       "CB",     "Rates & policy"),
    ("growth",      "Growth", "Growth"),
    ("trade",       "Trade",  "Growth"),
    ("inflation",   "CPI",    "Inflation"),
    ("policy",      "Rates",  "Inflation"),
    ("jobs",        "Jobs",   "Jobs"),
    ("news",        "News",   "News"),
    ("risk",        "Risk",   "Risk"),
]
GROUPS = ["Technical", "Sentiment", "Rates & policy", "Growth", "Inflation", "Jobs",
          "News", "Risk"]

STRONG, MILD = 40.0, 12.0
# Which checklist category feeds which factor. The checklist scores 1-5; _from_cat re-centres.
CAT = {"cbank": "Central Bank", "growth": "Economic Growth", "trade": "Trade Balance",
       "inflation": "Inflation", "policy": "Interest Rates", "jobs": "Employment",
       "risk": "Geopolitics & Risk"}


def bucket(v):
    """-2..+2 for colour. None stays None - an unknown factor is not a neutral one."""
    if v is None:
        return None
    if v >= STRONG:
        return 2
    if v >= MILD:
        return 1
    if v <= -STRONG:
        return -2
    if v <= -MILD:
        return -1
    return 0


def _load(name, default):
    p = DATA / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _from_cat(fun_row, cat):
    """A 1-5 checklist category to -100..+100. Unset categories return None so they show as a
    dot rather than a neutral reading - fundamentals.py leaves judgment indicators at 3 until
    they are set, and a wall of fake zeroes would read as information."""
    cats = (fun_row or {}).get("categories") or {}
    v = cats.get(cat)
    if isinstance(v, dict):
        v = v.get("score", v.get("avg_1_5"))
    if v is None:
        return None
    try:
        return round((float(v) - 3) / 2 * 100, 1)
    except (TypeError, ValueError):
        return None


def _seasonal_by_ccy(seas):
    """A currency-level seasonal from the pair table: for every pair the currency appears in,
    take this month's excess with the sign flipped when it is the quote, then average. Scaled
    so 1 percentage point of average excess is a full reading - tighter than the 1.5pp used
    for a single pair, because averaging seven pairs damps the spread."""
    inst = (seas or {}).get("instruments") or {}
    acc = {c: [] for c in ORDER}
    for pair in all_pairs():
        d = inst.get(pair) or {}
        ex = (d.get("this_month") or {}).get("excess")
        if ex is None:
            continue
        base, quote = pair[:3], pair[3:]
        if base in acc:
            acc[base].append(ex)
        if quote in acc:
            acc[quote].append(-ex)
    out = {}
    for c in ORDER:
        vals = acc[c]
        out[c] = (round(max(-100.0, min(100.0, (sum(vals) / len(vals)) / 1.0 * 100)), 1)
                  if vals else None)
    return out


def _trend_by_ccy(px):
    """Trend and momentum from the strength-index series fetch_fx_prices already builds.
    `trend` is the 20-day move, `momentum` the 5-day - kept apart because they disagree at
    exactly the moment that matters, which is a turn."""
    moves = (px or {}).get("moves") or {}
    trend, mom = {}, {}
    for c in ORDER:
        m = moves.get(c) or {}
        d20, d5 = m.get("d20"), m.get("d5")
        # 3% over a month / 1.5% over a week is a full reading for a major
        trend[c] = round(max(-100.0, min(100.0, d20 / 3.0 * 100)), 1) if d20 is not None else None
        mom[c] = round(max(-100.0, min(100.0, d5 / 1.5 * 100)), 1) if d5 is not None else None
    return trend, mom


def _curve_by_ccy(y):
    """Curve slope against the board average. An inverted curve, or one flatter than its
    peers, is the market pricing a slowdown and eventual cuts - bearish for the currency."""
    rows = (y or {}).get("currencies") or {}
    vals = [(rows.get(c) or {}).get("curve") for c in ORDER]
    have = [v for v in vals if v is not None]
    if len(have) < 3:
        return {c: None for c in ORDER}
    avg = sum(have) / len(have)
    out = {}
    for c in ORDER:
        v = (rows.get(c) or {}).get("curve")
        out[c] = round(max(-100.0, min(100.0, (v - avg) / 1.0 * 100)), 1) if v is not None else None
    return out


def build():
    sc = _load("scores.json", {})
    y = _load("yields.json", {})
    sent = _load("sentiment.json", {})
    seas = _load("seasonality.json", {})
    px = _load("prices_fx.json", {})
    fun = _load("fundamentals.json", {})

    seas_c = _seasonal_by_ccy(seas)
    trend_c, mom_c = _trend_by_ccy(px)
    curve_c = _curve_by_ccy(y)

    rows = {}
    for c in ORDER:
        s = (sc.get("currencies") or {}).get(c) or {}
        parts = s.get("parts") or {}
        yr = ((y.get("currencies") or {}).get(c) or {})
        yp = yr.get("parts") or {}
        fr = ((fun.get("currencies") or {}).get(c) or {})
        cells = {
            "trend": trend_c.get(c),
            "momentum": mom_c.get(c),
            "seasonality": seas_c.get(c),
            "cot": parts.get("cot"),
            "crowd": ((sent.get("instruments") or {}).get(c) or {}).get("score"),
            "oi": parts.get("oi"),
            "carry": yp.get("front"),
            "real": yp.get("real"),
            "curve": curve_c.get(c),
            "expect": parts.get("expectations"),
        }
        for key, cat in CAT.items():
            cells[key] = _from_cat(fr, cat)
        cells["news"] = parts.get("news")

        buckets = {k: bucket(v) for k, v in cells.items()}
        live = [b for b in buckets.values() if b is not None]
        bull = sum(1 for b in live if b > 0)
        bear = sum(1 for b in live if b < 0)
        rows[c] = {
            "score": s.get("score"), "rating": s.get("rating"), "cls": s.get("cls"),
            "cells": {k: (round(v, 1) if v is not None else None) for k, v in cells.items()},
            "buckets": buckets,
            "bull": bull, "bear": bear, "flat": len(live) - bull - bear,
            "covered": len(live), "total": len(FACTORS),
            # the equal-weight sum the layout this is modelled on uses as ITS headline. Kept
            # for comparison only - never the number this desk trades off.
            "equal_weight_sum": sum(live),
        }

    scored = [c for c in ORDER if rows[c].get("score") is not None]
    ranked = sorted(scored, key=lambda c: -rows[c]["score"])
    bulls = sum(1 for c in scored if (rows[c]["score"] or 0) >= 5)
    bears = sum(1 for c in scored if (rows[c]["score"] or 0) <= -5)
    out = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "factors": [{"key": k, "label": lab, "group": g} for k, lab, g in FACTORS],
        "groups": GROUPS, "currencies": rows, "ranked": ranked,
        "consensus": {"bull": bulls, "bear": bears,
                      "neutral": len(scored) - bulls - bears, "of": len(scored)},
        "spread": (round(rows[ranked[0]]["score"] - rows[ranked[-1]]["score"], 1)
                   if len(ranked) >= 2 else None),
        "strongest": ranked[0] if ranked else None,
        "weakest": ranked[-1] if ranked else None,
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    labs = [lab for _, lab, _ in FACTORS]
    print("  " + " " * 22 + " ".join(f"{l[:5]:>5}" for l in labs))
    for c in r["ranked"]:
        d = r["currencies"][c]
        cells = " ".join(
            (f"{d['buckets'][k]:>+5}" if d["buckets"][k] is not None else "    .")
            for k, _, _ in FACTORS)
        print(f"  {c:4} {d['score']:+6.1f} {d['bull']:>2}b/{d['bear']:<2}b  {cells}")
    con = r["consensus"]
    print(f"\n  consensus {con['bull']} bull / {con['bear']} bear / {con['neutral']} neutral "
          f"of {con['of']}   spread {r['spread']} pts  {r['strongest']} over {r['weakest']}")
