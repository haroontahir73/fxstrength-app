"""Pipeline runner.

  python run.py daily      fundamentals + news + rebuild      (COT is weekly, reused)
  python run.py cot        full refresh including COT          (Saturday, after Fri 15:30 ET)
  python run.py news       same as daily; used by the release-triggered run
  python run.py bootstrap  cold start - deep COT backfill then a full cot run
  python run.py next       print the next scheduled release and exit (no fetching)

Every mode ends by rebuilding dashboard.html. Safe to run against an empty data/ - `daily`
falls back to a `cot` fetch when cot.json is missing.
"""
import json, sys, datetime as dt
from config import DATA

import fetch_cot, fetch_calendar, fetch_oi, fetch_rates, rate_expectations, speakers, fundamentals, score, build_dashboard
import fetch_prices, fetch_fx_prices, commodities

STATE = DATA / "state.json"


def _state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def bootstrap():
    """Bring an empty data/ up to a full dashboard: 10 years of COT history from CFTC,
    then a normal `cot` run for everything else."""
    print("Bootstrap: deep COT history from CFTC")
    try:
        fetch_cot.backfill_cftc(10)
    except Exception as e:
        print(f"  backfill_cftc failed ({type(e).__name__}: {e}) - continuing; "
              f"the extreme read will warm up over subsequent weekly runs")
    run("cot")


def run(mode):
    now = dt.datetime.now(dt.timezone.utc)
    print(f"[{now:%Y-%m-%d %H:%M UTC}] mode={mode}")

    if mode == "bootstrap":
        return bootstrap()

    if mode == "next":
        cal = json.loads((DATA / "calendar.json").read_text(encoding="utf-8"))
        print(f"  next release      {cal.get('next_release')}")
        print(f"  next high-impact  {cal.get('next_high_impact')}")
        return

    cotf = DATA / "cot.json"
    if mode == "cot" or not cotf.exists():
        if mode != "cot":
            print("COT: cot.json missing - fetching")
        else:
            print("COT:")
        cot = fetch_cot.main()
    else:
        cot = json.loads(cotf.read_text(encoding="utf-8"))
        print(f"COT: reusing report {cot['currencies'].get('EUR', {}).get('report_date')}")

    print("Calendar:")
    cal = fetch_calendar.main()

    # Policy rates move rarely and cost several paged calls, so refresh at most every 3 days.
    rfile = DATA / "rates.json"
    stale = True
    if rfile.exists():
        rates = json.loads(rfile.read_text(encoding="utf-8"))
        age = (now - dt.datetime.fromisoformat(rates["fetched_at"])).days
        stale = age >= 3 or mode == "cot"
    if stale:
        print("Policy rates:")
        rates = fetch_rates.main()
        print("  " + "  ".join(f"{c} {d['rate']}" for c, d in rates["currencies"].items()
                               if d.get("rate") is not None))
    else:
        print(f"Policy rates: cached (USD {rates['usd_rate']}%)")

    print("Commentary:")
    spk = speakers.build(cal)
    c = spk["counts"]
    print(f"  {c['total']} events ({spk['ff_status']}) - {c['scored']} scored, "
          f"{c['pending']} awaiting tone, {c['upcoming']} upcoming")

    print("Rate expectations:")
    exp = rate_expectations.main()
    have = [c for c, d in exp["currencies"].items() if "score" in d]
    print(f"  {exp['status']}; probabilities for {', '.join(have) or 'none'}")

    print("Checklist:")
    fun = fundamentals.build(cal, rates, spk, exp)
    print("  " + "  ".join(f"{c} {fun['currencies'][c]['avg_1_5']:.2f}" for c in fun["currencies"]))

    print("Open interest:")
    fetch_oi.build(cot)

    # The commodity track is an add-on; a failure here must not stop the FX pipeline from
    # rebuilding and publishing. build_dashboard degrades to the last commodities.json (or
    # hides the section) on its own.
    print("Prices (for the directional / retracement read):")
    try:
        fetch_fx_prices.main()
    except Exception as e:
        print(f"  fetch_fx_prices failed ({type(e).__name__}: {e}) - keeping cached")
    try:
        fetch_prices.main()
    except Exception as e:
        print(f"  fetch_prices failed ({type(e).__name__}: {e}) - keeping cached")

    print("Scores:")
    s = score.build()
    for c in s["ranked"]:
        r = s["currencies"][c]
        print(f"  {c} {r['score']:+6.1f}  {r['rating']}")

    print("Commodities:")
    cm = None
    try:
        cm = commodities.build()
        for sym in cm["ranked"]:
            d = cm["commodities"][sym]
            print(f"  {sym} {d['score']:+6.1f}  {d['rating']}")
    except Exception as e:
        print(f"  commodities.build failed ({type(e).__name__}: {e}) - section left as-is")

    build_dashboard.build()

    prev = _state()
    STATE.write_text(json.dumps({
        "last_run": now.isoformat(), "last_mode": mode,
        "last_cot_report": cot["currencies"].get("EUR", {}).get("report_date"),
        "next_release": cal.get("next_release"),
        "next_high_impact": cal.get("next_high_impact"),
        "released_count": len(cal.get("released", [])),
        "prev_released_count": prev.get("released_count"),
        "ranked": s["ranked"],
        "scores": {c: s["currencies"][c]["score"] for c in s["ranked"]},
        "prev_scores": prev.get("scores"),
        "commodity_scores": ({sym: cm["commodities"][sym]["score"] for sym in cm["ranked"]}
                             if cm else prev.get("commodity_scores")),
        "prev_commodity_scores": prev.get("commodity_scores"),
    }, indent=2))

    if prev.get("scores"):
        moves = [(c, s["currencies"][c]["score"] - prev["scores"].get(c, 0)) for c in s["ranked"]]
        moves = [m for m in moves if abs(m[1]) >= 3]
        if moves:
            print("  moved since last run: " + ", ".join(f"{c} {d:+.1f}" for c, d in moves))
    if cm and prev.get("commodity_scores"):
        cmoves = [(sym, cm["commodities"][sym]["score"] - prev["commodity_scores"].get(sym, 0))
                  for sym in cm["ranked"]]
        cmoves = [m for m in cmoves if abs(m[1]) >= 3]
        if cmoves:
            print("  commodities moved: " + ", ".join(f"{sym} {d:+.1f}" for sym, d in cmoves))
    return s


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "daily")
