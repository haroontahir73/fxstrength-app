"""Live CI smoke test for the economic calendar (PR #14).

Runs the real build against official sources, then asserts: >=3 reliable keyless sources
returned data, the page carries the ForexFactory columns, UK timezone resolved (zoneinfo),
and no event carries a fabricated fetch-time. Read-only; no deploy. Run from engine/.
"""
import datetime as dt
import econ_cal as ec
import econ_cal_public as ep

a = ec.build()
ok = [k for k, v in a["sources"].items() if v.get("ok")]
print("sources OK:", ok)
page = ep.page(a)
cols = [c for c in ("Time", "Currency", "Impact", "Event", "Actual", "Forecast", "Previous") if c not in page]

assert len(ok) >= 3, f"only {len(ok)} source(s) returned data: {a['sources']}"
assert not cols, f"missing columns: {cols}"
assert ep.UK != dt.timezone.utc, "UK timezone did not resolve (zoneinfo/tzdata missing)"
assert a["events"], "no events built"
bad = [e["id"] for e in a["events"] if not e.get("scheduled") and (e.get("dt_utc") or "").startswith(a["built_at"][:16])]
assert not bad, f"events stamped with the build/fetch time: {bad}"
print(f"econ smoke OK: {len(a['events'])} events, {len(ok)} sources, tz={ep.UK}")
