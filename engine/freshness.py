"""Per-input freshness, recorded next to the score and rendered on the page.

WHY THIS EXISTS
---------------
Every module writes its own `built_at` / `fetched_at` the moment it runs, and the
dashboard prints one "Built <timestamp>" line taken from scores.json. So a rebuild that
reused a cached calendar, carried week-old policy rates and failed to reach the price
feed published a page stamped with the current minute and looked identical to a healthy
one. The build time says when the HTML was written; it says nothing about how old the
evidence inside it is.

This module reads the as-of stamp out of each input the score actually depends on, ages
it against the clock, and compares that against a per-input budget. `collect()` returns
one row per input; score.py stores the rows in scores.json and build_dashboard renders
them, so a stale input is visible on the page rather than inferable from a log nobody
reads.

A budget is "how old can this be before it is suspect", NOT a cadence. COT is weekly by
nature, so its budget is in days; the calendar is continuous, so hours.
"""
import json, datetime as dt
from config import DATA

# (label, filename, [stamp fields, first one present wins], budget_hours, what it feeds)
INPUTS = [
    ("Calendar / news", "calendar.json", ("data_as_of", "fetched_at"), 6, "news leg 0.40"),
    ("COT positioning", "cot.json", ("fetched_at",), 10 * 24, "COT leg 0.15"),
    ("Open interest", "oi.json", ("built_at",), 10 * 24, "OI leg 0.10"),
    ("Checklist", "fundamentals.json", ("built_at",), 6, "checklist leg 0.25"),
    ("Policy rates", "rates.json", ("fetched_at",), 10 * 24, "2 checklist indicators"),
    ("Rate odds", "rate_expectations.json", ("fetched_at",), 72, "rate-odds leg 0.10"),
    ("FX prices", "prices_fx.json", ("fetched_at",), 5 * 24, "directional read"),
]


def _read(name):
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None


def _age_h(stamp, now):
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(str(stamp))
    except Exception:                                          # noqa: BLE001
        try:                                        # a bare date, e.g. prices "asof"
            t = dt.datetime.fromisoformat(str(stamp) + "T00:00:00+00:00")
        except Exception:                                      # noqa: BLE001
            return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return (now - t).total_seconds() / 3600


def collect(now=None):
    """One row per input: how old its data is, and whether that is past its budget."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = []
    for label, fname, fields, budget_h, feeds in INPUTS:
        d = _read(fname)
        if d is None:
            rows.append({"label": label, "file": fname, "feeds": feeds,
                         "as_of": None, "age_h": None, "budget_h": budget_h,
                         "stale": True, "missing": True, "degraded": False,
                         "why": "file missing or unreadable"})
            continue
        stamp = next((d.get(f) for f in fields if d.get(f)), None)
        age = _age_h(stamp, now)
        # A module may say for itself that it is running on fallback data - the calendar
        # does exactly this when it reuses cached events. That is a freshness fact the
        # timestamp alone cannot carry, so it is reported alongside the age.
        degraded = bool(d.get("degraded"))
        why = d.get("degraded_why") or ""
        # carried-over entries inside an otherwise current file (see fetch_rates)
        carried = sorted(k for k, v in (d.get("currencies") or {}).items()
                         if isinstance(v, dict) and v.get("stale"))
        if carried:
            degraded = True
            why = why or f"carried over for {', '.join(carried)}"
        rows.append({"label": label, "file": fname, "feeds": feeds,
                     "as_of": stamp, "age_h": round(age, 1) if age is not None else None,
                     "budget_h": budget_h,
                     "stale": bool(age is None or age > budget_h),
                     "missing": False, "degraded": degraded, "why": why,
                     "carried": carried})
    return rows


def summary(rows):
    """A one-line verdict plus the lists the page and the logs both want."""
    stale = [r["label"] for r in rows if r["stale"]]
    degraded = [r["label"] for r in rows if r["degraded"] and not r["stale"]]
    ages = [r["age_h"] for r in rows if r["age_h"] is not None]
    if stale:
        state = "stale"
    elif degraded:
        state = "degraded"
    else:
        state = "ok"
    return {"state": state, "stale": stale, "degraded": degraded,
            "oldest_input_h": round(max(ages), 1) if ages else None,
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat()}


if __name__ == "__main__":
    rows = collect()
    s = summary(rows)
    print(f"{'input':<18}{'age':>10}{'budget':>9}   state   feeds")
    print("-" * 72)
    for r in rows:
        age = f"{r['age_h']:.1f}h" if r["age_h"] is not None else "--"
        bad = "STALE" if r["stale"] else ("degraded" if r["degraded"] else "ok")
        print(f"{r['label']:<18}{age:>10}{r['budget_h']:>8}h   {bad:<9}{r['feeds']}"
              + (f"  ({r['why']})" if r["why"] else ""))
    print(f"\n  verdict: {s['state']}"
          + (f" - stale: {', '.join(s['stale'])}" if s["stale"] else "")
          + (f" - degraded: {', '.join(s['degraded'])}" if s["degraded"] else ""))
