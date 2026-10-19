"""Tests for the Impact-on-currency column + HIGH-only filter on econ_cal_public.

    python test_econ_impact.py

Stdlib only, offline. Covers the add-on behaviour; does not re-cover the base calendar
rendering already in test_econ_cal.py.
"""
import datetime as dt
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config                                                       # noqa: E402
import econ_cal_public as ecp                                       # noqa: E402

PASS, FAIL, CALLS = [], [], []


def _spy(req, *a, **k):
    CALLS.append(getattr(req, "full_url", str(req)))
    raise OSError("network blocked in test_econ_impact")


urllib.request.urlopen = _spy


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def _redirect_data():
    d = Path(tempfile.mkdtemp(prefix="fxei-"))
    config.DATA = d
    ecp.DATA = d
    return d


def _tv(d, released):
    (d / "calendar.json").write_text(json.dumps({"released": released}))


def _arch(events):
    return {"events": events, "data_as_of": "2026-10-14T12:00:00Z"}


def _ev(ccy, event, impact="High", actual="1", previous="1", when="2026-10-14T08:30:00Z"):
    return {"currency": ccy, "event": event, "actual": actual, "previous": previous,
            "impact": impact, "dt_utc": when, "scheduled": True, "status": "released"}


def t_impact_direction():
    g = "impact"
    d = _redirect_data()
    _tv(d, [
        {"ccy": "USD", "title": "CPI YoY (Sep)", "when": "2026-10-14T08:30:00Z", "surprise": 0.4},
        {"ccy": "AUD", "title": "Unemployment Rate (Sep)", "when": "2026-10-14T00:30:00Z", "surprise": -0.3},
        {"ccy": "USD", "title": "Retail Sales (Sep)", "when": "2026-10-14T13:30:00Z", "surprise": 0.02},
    ])
    arch = _arch([
        _ev("USD", "CPI YoY"),
        _ev("AUD", "Unemployment Rate", when="2026-10-14T00:30:00Z"),
        _ev("USD", "Retail Sales", when="2026-10-14T13:30:00Z"),
    ])
    h = ecp.panel(arch)
    check(g, "Bullish arrow + currency on a positive surprise", "↑ Bullish USD" in h)
    check(g, "Bearish arrow + currency on an INVERTED beat (unemployment higher)",
          "↓ Bearish AUD" in h)
    check(g, "Neutral arrow when surprise is inside the neutral band",
          "→ Neutral USD" in h)


def t_no_tv_match():
    g = "impact"
    d = _redirect_data()
    _tv(d, [])
    arch = _arch([_ev("USD", "Unmapped Event")])
    h = ecp.panel(arch)
    # the arrow symbols only appear inside the Impact column cell; the note text uses them
    # too, so look at the <td class="im"> cell content instead
    row_imp = re.search(r'<td class="im"[^>]*>(.*?)</td>', h).group(1)
    check(g, "no TradingView match -> em-dash in Impact cell, no direction tag",
          "&mdash;" in row_imp and "Bullish" not in row_imp and "Bearish" not in row_imp
          and "Neutral" not in row_imp)


def t_upcoming_shows_dash():
    g = "impact"
    d = _redirect_data()
    _tv(d, [])
    arch = _arch([
        {"currency": "GBP", "event": "BoE Rate Decision", "impact": "High",
         "dt_utc": "2099-01-01T11:00:00Z", "scheduled": True}
    ])
    h = ecp.panel(arch)
    row = re.search(r'BoE Rate Decision.*?</tr>', h, re.S).group(0)
    row_imp = re.search(r'<td class="im"[^>]*>(.*?)</td>', row).group(1)
    check(g, "upcoming (no actual) shows em-dash in Impact cell",
          "&mdash;" in row_imp and "Bullish" not in row_imp and "Bearish" not in row_imp)


def t_high_only_filter():
    g = "filter"
    d = _redirect_data()
    _tv(d, [])
    arch = _arch([
        _ev("USD", "CPI YoY"),
        _ev("EUR", "Trade Balance", impact="Medium"),
        _ev("JPY", "Housing Starts", impact="Low"),
    ])
    h = ecp.panel(arch)
    check(g, "HIGH event shown", "CPI YoY" in h)
    check(g, "Medium event dropped", "Trade Balance" not in h)
    check(g, "Low event dropped", "Housing Starts" not in h)


def t_forecast_still_hidden():
    g = "sourcing"
    d = _redirect_data()
    _tv(d, [
        {"ccy": "USD", "title": "CPI YoY (Sep)", "when": "2026-10-14T08:30:00Z",
         "surprise": 0.4, "forecast": "3.6%"},
    ])
    arch = _arch([_ev("USD", "CPI YoY")])
    h = ecp.panel(arch)
    # the TradingView forecast "3.6%" must NOT appear anywhere - this is the B2 contract
    check(g, "TradingView forecast is NOT displayed in the public page (B2 contract)",
          "3.6%" not in h)
    check(g, "Forecast cell still shows the em-dash on this official-source path",
          '<td class="f" data-l="Forecast">&mdash;</td>' in h or '>&mdash;</td>' in h)


def t_frozen_and_safety():
    g = "safety"
    import divergence_backtest as bt
    check(g, "strategy fingerprint unchanged (0bcac9b16befcd54)", bt.fingerprint() == "0bcac9b16befcd54")
    # the module reads only local files, no network host
    src = (Path(__file__).parent / "econ_cal_public.py").read_text()
    check(g, "econ_cal_public references no CME / Myfxbook / network host",
          not re.search(r"(?i)cmegroup|myfxbook\.com|urlopen|urlretrieve", src))
    check(g, "no network call escaped this suite", CALLS == [])


if __name__ == "__main__":
    for fn in (t_impact_direction, t_no_tv_match, t_upcoming_shows_dash, t_high_only_filter,
               t_forecast_still_hidden, t_frozen_and_safety):
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    groups = {}
    for gp, _ in PASS:
        groups.setdefault(gp, [0, 0])[0] += 1
    for gp, _ in FAIL:
        groups.setdefault(gp, [0, 0])[1] += 1
    print()
    for gp, (okc, bad) in groups.items():
        print(f"{gp}: {okc}/{okc + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
