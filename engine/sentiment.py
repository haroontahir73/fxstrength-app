"""Retail crowd positioning, read as a contrarian indicator.

WHERE THE NUMBER COMES FROM, AND WHY NOT A BROKER. The obvious sources for retail sentiment
are Myfxbook's community outlook and DailyFX/IG client positioning. Both 403 automated reads,
and both describe one broker's book - a self-selected slice of the retail world that can
disagree with the rest of it.

The CFTC's NON-REPORTABLE column is better on every axis. It is every account too small to
have to file, across the whole regulated futures market, published weekly by the regulator,
and it arrives in the pull fetch_cot.py already makes - so this module needs no feed, no key
and no scraping. Its one real limitation is cadence: it is Tuesday data published Friday, so
it is a weekly picture, not a live one. That is stated on the tab.

HOW IT IS READ. Small traders are, on average and at extremes, on the wrong side - the
documented result behind every "retail is 70% long, fade it" chart. So the score here is
INVERTED: a crowded retail long scores negative for the currency. Two legs:

    net %       net non-reportable position as a share of open interest, versus that
                currency's own 3-year range - so "crowded" means crowded for THIS contract,
                not against some universal threshold
    flow        the week's change in that net, which catches the crowd piling in or bailing
                out before the level itself looks extreme

Positioning is contrarian at extremes and noise in the middle, which is exactly what the FX
backtest already found for the speculative side (see config.WEIGHTS). So the score is
deliberately flat through the middle of the range and only bites near the edges.

Reads data/cot.json and data/cot_history.json. Writes data/sentiment.json.
"""
import json, datetime as dt
from config import DATA, ORDER, CURRENCIES, COMMODITIES, COMMODITY_ORDER

OUT = DATA / "sentiment.json"
MIN_WEEKS = 26            # below this the percentile is meaningless; report level only
EXTREME_PCTL = 80         # at/above this (or at/below 100-this) the crowd counts as crowded


def _pctl(vals, cur):
    lo, hi = min(vals), max(vals)
    return round((cur - lo) / (hi - lo) * 100) if hi > lo else 50


def _load(name, default):
    p = DATA / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _series(hist, sym):
    """[(week, net % of OI)] oldest first, for one contract's non-reportable column."""
    out = []
    for wk in sorted(hist):
        row = (hist[wk] or {}).get(sym) or {}
        nr, oi = row.get("nonrept"), row.get("open_interest")
        if not nr or not oi:
            continue
        net = nr.get("net")
        if net is None:
            continue
        out.append((wk, net / oi * 100))
    return out


def _score(pctl, flow_pp):
    """Contrarian: crowded retail long -> negative score for the instrument.

    The level leg is flat between the 20th and 80th percentile and then ramps, so an ordinary
    week contributes nothing and only a genuine extreme moves the number. The flow leg is
    linear - a 2 percentage-point swing in one week is a full reading - because a fast build
    is informative wherever it starts from.
    """
    parts = {}
    if pctl is not None:
        if pctl >= EXTREME_PCTL:
            parts["level"] = -(pctl - EXTREME_PCTL) / (100 - EXTREME_PCTL) * 100
        elif pctl <= 100 - EXTREME_PCTL:
            parts["level"] = (100 - EXTREME_PCTL - pctl) / (100 - EXTREME_PCTL) * 100
        else:
            parts["level"] = 0.0
    if flow_pp is not None:
        parts["flow"] = max(-100.0, min(100.0, -flow_pp / 2.0 * 100))
    if not parts:
        return None, parts
    # level leads: an extreme is a stronger statement than a single week's change
    w = {"level": 0.65, "flow": 0.35}
    tot = sum(w[k] for k in parts)
    return round(sum(parts[k] * w[k] for k in parts) / tot, 1), \
        {k: round(v, 1) for k, v in parts.items()}


def _one(sym, name, cur_row, hist, kind):
    nr = (cur_row or {}).get("nonrept") or {}
    oi = (cur_row or {}).get("open_interest")
    if not nr or not oi or nr.get("net") is None:
        return None
    lng, sht = nr.get("long") or 0, nr.get("short") or 0
    tot = lng + sht
    net_pct = nr["net"] / oi * 100
    ser = _series(hist, sym)
    vals = [v for _, v in ser][-156:]              # 3 years
    pctl = _pctl(vals, net_pct) if len(vals) >= MIN_WEEKS else None
    flow = ((nr.get("net_chg") or 0) / oi * 100) if oi else None
    score, parts = _score(pctl, flow)

    if pctl is None:
        state = ""
    elif pctl >= EXTREME_PCTL:
        state = "crowded long"
    elif pctl <= 100 - EXTREME_PCTL:
        state = "crowded short"
    else:
        state = ""
    return {
        "name": name, "kind": kind,
        "long": lng, "short": sht,
        "long_pct": round(lng / tot * 100, 1) if tot else None,
        "short_pct": round(sht / tot * 100, 1) if tot else None,
        "net": nr["net"], "net_pct_oi": round(net_pct, 2),
        "net_chg": nr.get("net_chg"),
        "flow_pp": round(flow, 3) if flow is not None else None,
        "pctl_3y": pctl, "weeks": len(vals), "state": state,
        "score": score, "parts": parts,
    }


def build():
    cot = _load("cot.json", {})
    # currencies and commodities keep SEPARATE history files - reading both from the FX one
    # silently left every metal without a percentile
    hist_fx = _load("cot_history.json", {})
    hist_cm = _load("cot_history_commodity.json", {})
    rows = {}
    for ccy in ORDER:
        d = _one(ccy, CURRENCIES[ccy]["name"], (cot.get("currencies") or {}).get(ccy),
                 hist_fx, "fx")
        if d:
            rows[ccy] = d
    for sym in COMMODITY_ORDER:
        d = _one(sym, COMMODITIES[sym]["name"], (cot.get("commodities") or {}).get(sym),
                 hist_cm, "commodity")
        if d:
            rows[sym] = d

    scored = [k for k in rows if rows[k].get("score") is not None]
    out = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "report_date": (cot.get("currencies", {}).get("EUR") or {}).get("report_date"),
           "min_weeks": MIN_WEEKS, "extreme_pctl": EXTREME_PCTL,
           "instruments": rows,
           "ranked": sorted(scored, key=lambda k: -rows[k]["score"]),
           "crowded": [k for k in rows if rows[k].get("state")]}
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = build()
    print(f"  retail (CFTC non-reportable), week of {r['report_date']}")
    print(f"  {'sym':5} {'long%':>6} {'short%':>7} {'net %OI':>8} {'3y pctl':>8} {'wk flow':>8} {'score':>7}  state")
    for k in (r["ranked"] or list(r["instruments"])):
        d = r["instruments"][k]
        f = lambda v, w, p=1: (f"{v:{w}.{p}f}" if v is not None else " " * (w - 3) + "n/a")
        print(f"  {k:5} {f(d['long_pct'],6)} {f(d['short_pct'],7)} {f(d['net_pct_oi'],8,2)} "
              f"{f(d['pctl_3y'],8,0)} {f(d['flow_pp'],8,2)} {f(d['score'],7)}  {d['state']}")
