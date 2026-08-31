"""Decide whether anything actually changed, and only then rebuild.

Designed to be run often (every 15 min) from Windows Task Scheduler. Costs nothing
when nothing has happened: it pulls the calendar, compares against the last run, and
exits without touching the rest of the pipeline unless one of these is true:

  new release   an event that had no `actual` last time now has one
  daily due     more than DAILY_HOURS since the last full rebuild
  new COT       it is the weekend and the COT report on file is stale

Exit codes: 0 rebuilt, 10 nothing to do, 1 error.
"""
import json, sys, datetime as dt
from config import DATA
import fetch_calendar, run

STATE = DATA / "state.json"
DAILY_HOURS = 20
RECENT_WINDOW_H = 30          # only events this fresh count as "just released"


def recent_keys(cal, now):
    out = set()
    for e in cal.get("released", []):
        age = (now - dt.datetime.fromisoformat(e["when"])).total_seconds() / 3600
        if 0 <= age <= RECENT_WINDOW_H:
            out.add(f"{e['ccy']}|{e['title']}|{e['when']}")
    return out


def main():
    now = dt.datetime.now(dt.timezone.utc)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}

    cal = fetch_calendar.main()
    keys = recent_keys(cal, now)
    seen = set(state.get("recent_keys", []))
    fresh = keys - seen

    last_run = state.get("last_run")
    hours_since = ((now - dt.datetime.fromisoformat(last_run)).total_seconds() / 3600
                   if last_run else 999)

    cot_stale = False
    if now.weekday() in (5, 6):                       # Sat / Sun
        rep = state.get("last_cot_report")
        if rep:
            age_d = (now.date() - dt.date.fromisoformat(rep)).days
            cot_stale = age_d >= 7                    # Tuesday data, released Friday
        else:
            cot_stale = True

    reasons = []
    if fresh:
        reasons.append(f"{len(fresh)} new release(s): " +
                       ", ".join(sorted(k.split('|')[0] + ' ' + k.split('|')[1] for k in fresh)[:4]))
    if hours_since >= DAILY_HOURS:
        reasons.append(f"daily refresh due ({hours_since:.0f}h since last)")
    if cot_stale:
        reasons.append("COT report stale")

    if not reasons:
        print(f"[{now:%Y-%m-%d %H:%M UTC}] nothing new "
              f"({hours_since:.1f}h since last run, {len(keys)} recent releases already seen)")
        return 10

    mode = "cot" if cot_stale else ("news" if fresh else "daily")
    print(f"[{now:%Y-%m-%d %H:%M UTC}] rebuilding - " + "; ".join(reasons))
    run.run(mode)

    state = json.loads(STATE.read_text(encoding="utf-8"))
    state["recent_keys"] = sorted(recent_keys(
        json.loads((DATA / "calendar.json").read_text(encoding="utf-8")), now))
    state["last_trigger"] = reasons
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
