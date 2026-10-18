"""Tests for the PR #14 economic calendar (econ_cal.py + econ_cal_public.py).

    python test_econ_cal.py

Stdlib only, fully offline. The pure parsers are exercised against fixtures shaped like the
real official-source responses (captured on the verify/econ-endpoints branch). build() is
run with a stubbed fetch so no network is touched, and the renderer is checked for the
ForexFactory column set, the mobile (no-overflow) layout and the neutral change arrow.
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import econ_cal as ec            # noqa: E402
import econ_cal_public as ep     # noqa: E402

FAILS = []


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILS.append(name)


# ---- fixtures (shaped like the live responses) ------------------------------------------
def _bls(series_id, vals):
    """vals: list of (year, 'M09', value-as-str)."""
    data = [{"year": y, "period": p, "value": v} for y, p, v in vals]
    return json.dumps({"status": "REQUEST_SUCCEEDED",
                       "Results": {"series": [{"seriesID": series_id, "data": data}]}})


def _months(n, start=(2025, 8), step=0.1, base=300.0):
    out = []
    y, m = start
    for i in range(n):
        out.append((str(y), f"M{m:02d}", f"{base + i * step:.3f}"))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def t_bls_cpi():
    txt = _bls("CUUR0000SA0", _months(14))                      # 14 months -> YoY computable
    ev = ec.parse_bls_cpi(txt)
    check("BLS CPI -> one High USD inflation event", len(ev) == 1 and ev[0]["impact"] == "High"
          and ev[0]["currency"] == "USD" and ev[0]["unit"] == "%")
    check("BLS CPI YoY value present", ev[0]["actual_raw"] is not None and ev[0]["forecast"] is None)


def t_bls_missing_value():
    rows = _months(3)
    rows[1] = (rows[1][0], rows[1][1], "-")                     # a suppressed value
    ev = ec.parse_bls_unrate(_bls("LNS14000000", rows))
    check("BLS '-' missing value is skipped, not crashing", len(ev) == 1 and ev[0]["actual_raw"] is not None)


def t_eurostat():
    txt = json.dumps({"value": {"0": 2.0, "1": 2.1, "2": 2.2},
                      "dimension": {"time": {"category": {"index": {"2026-07": 0, "2026-08": 1, "2026-09": 2}}}}})
    ev = ec.parse_eurostat_hicp(txt)
    check("Eurostat HICP latest+prev", len(ev) == 1 and ev[0]["actual_raw"] == 2.2 and ev[0]["previous_raw"] == 2.1)


def t_ecb():
    txt = json.dumps({"structure": {"dimensions": {"observation": [
        {"values": [{"id": "2026-08-01"}, {"id": "2026-09-16"}]}]}},
        "dataSets": [{"series": {"0:0:0:0:0:0:0": {"observations": {"0": [2.15], "1": [2.00]}}}}]})
    ev = ec.parse_ecb_rate(txt)
    check("ECB rate decimals=2 formatting", len(ev) == 1 and ev[0]["actual"] == "2.00%")


def t_boc():
    txt = json.dumps({"observations": [{"d": "2026-10-14", "V39079": {"v": "2.75"}},
                                       {"d": "2026-10-15", "V39079": {"v": "2.50"}}]})
    ev = ec.parse_boc_rate(txt)
    check("BoC rate latest+prev", len(ev) == 1 and ev[0]["actual_raw"] == 2.50 and ev[0]["previous_raw"] == 2.75)


def t_statcan():
    pts = [{"refPer": f"2025-{m:02d}-01", "value": 150.0 + m} for m in range(1, 13)]
    pts += [{"refPer": "2026-01-01", "value": 165.0}, {"refPer": "2026-02-01", "value": 166.0}]
    txt = json.dumps([{"object": {"vectorDataPoint": pts}}])
    ev = ec.parse_statcan_cpi(txt)
    check("StatCan CPI YoY", len(ev) == 1 and ev[0]["currency"] == "CAD" and ev[0]["unit"] == "%")


def t_boe():
    ev = ec.parse_boe_rate("DATE,IUDBEDR\n01 Jan 2026,4.50\n15 Sep 2026,4.25")
    check("BoE rate last + prev-different", len(ev) == 1 and ev[0]["actual_raw"] == 4.25 and ev[0]["previous_raw"] == 4.50)


def t_ons():
    txt = json.dumps({"months": [{"date": "2026 AUG", "value": "3.8"}, {"date": "2026 SEP", "value": "3.9"}]})
    ev = ec.parse_ons_cpi(txt)
    check("ONS CPI latest+prev", len(ev) == 1 and ev[0]["actual_raw"] == 3.9 and ev[0]["previous_raw"] == 3.8)


def t_rba():
    csv = "F1 INTEREST RATES\nTitle,Cash Rate Target,Other\nx,3.60,a\ny,3.85,b"
    ev = ec.parse_rba_rate(csv)
    check("RBA cash rate target column", len(ev) == 1 and ev[0]["actual_raw"] == 3.85 and ev[0]["previous_raw"] == 3.60)


def t_mk_event():
    e = ec.mk_event("USD", "X", "inflation", "High", actual_raw=1234567, unit="", period="p")
    check("mk_event integer thousands format", e["actual"] == "1,234,567" and e["forecast"] is None)


def t_build_offline_and_history():
    tmp = Path(tempfile.mkdtemp())
    good = _bls("CUUR0000SA0", _months(14))
    orig_fetch, orig_sources = ec.fetch, ec.SOURCES
    try:
        ec.fetch = lambda url, method="GET", body=None, headers=None: good
        ec.SOURCES = [("USD-CPI", "x", {}, ec.parse_bls_cpi)]
        a1 = ec.build(data=tmp)
        check("build never raises, writes events", a1["events"] and a1["sources"]["USD-CPI"]["ok"])
        check("no fabricated release time (dt_utc stays None)", a1["events"][0].get("dt_utc") is None)
        check("archive file written", (tmp / "econ_calendar.json").exists())
        h1 = (tmp / "econ_history.jsonl").read_text().strip().splitlines()
        check("history logs first_release", len(h1) == 1 and json.loads(h1[0])["kind"] == "first_release")
        # a revised value for the same period -> a revision line
        rows = _months(14)
        rows[-1] = (rows[-1][0], rows[-1][1], "999.000")
        ec.fetch = lambda url, method="GET", body=None, headers=None: _bls("CUUR0000SA0", rows)
        ec.build(data=tmp)
        h2 = (tmp / "econ_history.jsonl").read_text().strip().splitlines()
        check("history logs a revision", len(h2) == 2 and json.loads(h2[1])["kind"] == "revision")
        # a failing source degrades gracefully, never raises
        def boom(*a, **k):
            raise RuntimeError("down")
        ec.fetch = boom
        a3 = ec.build(data=tmp)
        check("failed source -> degraded, no raise", a3["degraded"] and not a3["sources"]["USD-CPI"]["ok"])
    finally:
        ec.fetch, ec.SOURCES = orig_fetch, orig_sources


def t_render_columns_and_mobile():
    arch = {"built_at": "2026-10-18T12:00:00+00:00", "data_as_of": "2026-10-18T12:00:00+00:00",
            "degraded": False, "sources": {"USD-CPI": {"ok": True}},
            "events": [ec.mk_event("USD", "CPI (YoY)", "inflation", "High", actual_raw=3.1,
                                   previous_raw=2.9, unit="%", period="2026-09",
                                   when=dt.datetime(2026, 10, 18, 11, 30, tzinfo=dt.timezone.utc),
                                   has_time=True, source="BLS")]}
    html = ep.page(arch)
    for col in ("Time", "Currency", "Impact", "Event", "Actual", "Forecast", "Previous"):
        check(f"column header present: {col}", col in html)
    check("forecast shown as dash", "&mdash;" in html)
    check("neutral change arrow (not a signal)", 'class="chg up"' in html or "▲" in html)
    check("US flag rendered", "🇺🇸" in html)
    check("historical prints labelled Latest releases", "Latest releases" in html)
    check("mobile no-overflow rule", "max-width:680px" in ep._CSS and "display:block" in ep._CSS)
    check("dark-mode token guard present", "prefers-color-scheme:dark" in ep._CSS and 'data-theme=dark' in ep._CSS)


def t_timezone_bst_gmt():
    if ep.UK == dt.timezone.utc:        # no tzdata in this env; skip (CI has it)
        check("tz skipped (no zoneinfo here)", True)
        return
    win = ep._uk(dt.datetime(2026, 1, 15, 9, 0, tzinfo=dt.timezone.utc)).strftime("%H:%M")
    sm = ep._uk(dt.datetime(2026, 7, 15, 9, 0, tzinfo=dt.timezone.utc)).strftime("%H:%M")
    check("GMT winter 09:00 -> 09:00", win == "09:00")
    check("BST summer 09:00 -> 10:00", sm == "10:00")


def main():
    for fn in [t_bls_cpi, t_bls_missing_value, t_eurostat, t_ecb, t_boc, t_statcan, t_boe,
               t_ons, t_rba, t_mk_event, t_build_offline_and_history,
               t_render_columns_and_mobile, t_timezone_bst_gmt]:
        fn()
    print(f"\n{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
