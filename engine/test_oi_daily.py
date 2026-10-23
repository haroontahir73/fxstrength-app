"""Tests for the restored daily CME OI view with options (oi.py).

    python test_oi_daily.py

Stdlib only, offline. Exercises the PURE helpers (no network, no writes to engine/data):
export-row classification into futures+options, field-wise freeze, the Pi-pull parser,
percentiles, and the rendered page. The live CME fetch
and the history file are never touched here (collection runs on the Pi).
"""
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import oi                                                          # noqa: E402

FAILS = []


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILS.append(name)


def t_fold_rows_fut_and_opt():
    html = ("<table>"
            "<tr><td>Euro FX</td><td>Futures</td><td>g</td><td>o</td><td>c</td>"
            "<td>50,000</td><td>700,000</td><td>1,000</td></tr>"
            "<tr><td>Euro FX</td><td>Options</td><td>g</td><td>o</td><td>c</td>"
            "<td>8,000</td><td>120,000</td><td>200</td></tr></table>")
    out = {}
    oi._fold_rows(out, oi._parse_cme_xls(html.encode()))
    check("futures row captured", out.get("EUR", {}).get("oi") == 700000 and out["EUR"]["chg"] == 1000)
    check("options row captured separately", out["EUR"].get("oi_opt") == 120000 and out["EUR"]["chg_opt"] == 200)


def t_pull_into_fut_opt():
    dm = {}
    oi._pull_into(dm, "EUR", [["20261001", 700000, 50000], ["20261002", 701000, 51000]], "")
    oi._pull_into(dm, "EUR", [["20261001", 120000, 8000], ["20261002", 120500, 8100]], "_opt")
    check("pull derives futures chg", dm["2026-10-02"]["EUR"]["chg"] == 1000)
    check("pull derives options chg", dm["2026-10-02"]["EUR"]["chg_opt"] == 500)
    check("first day chg is None", dm["2026-10-01"]["EUR"]["chg"] is None)


def t_field_wise_freeze():
    hist = {"2026-10-01": {"EUR": {"oi": 700000, "chg": 1000, "volume": 50000}}}
    # a later pull carrying FINALS for futures + NEW options for the same day
    dm = {"2026-10-01": {"EUR": {"oi": 999999, "chg": 9, "volume": 9,
                                 "oi_opt": 120000, "chg_opt": 200, "volume_opt": 8000}}}
    w, s = oi.merge_days(hist, dm)
    r = hist["2026-10-01"]["EUR"]
    check("futures finals do NOT overwrite preliminary", r["oi"] == 700000)
    check("options backfill onto a frozen day", r["oi_opt"] == 120000)


def t_pctile():
    hist = {}
    for i in range(30):
        hist[f"2026-09-{i+1:02d}"] = {"EUR": {"oi": 700000 + i * 100, "chg": 100}}
    check("latest value is top percentile", oi.pctile(hist, "EUR", "oi") == 100)
    check("absent field -> None", oi.pctile(hist, "EUR", "oi_opt") is None)



def t_render_has_options_and_states():
    hist = {}
    for i in range(14):
        day = (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()
        hist[day] = {"EUR": {"oi": 700000 + i * 1000, "chg": 1000, "volume": 50000,
                             "oi_opt": 120000 + i * 200, "chg_opt": 200, "volume_opt": 8000}}
    html = oi.render(hist, {"last_good_date": sorted(hist)[-1]}, oi.analyse(hist, {}), {})
    check("separate options section present", "Options open interest" in html)
    check("options table has its own change column", "Options OI change" in html)
    check("futures table NOT mixed with options", "Opt OI chg" not in html)
    check("page preserves the 4-state key", "New money long" in html and "Short covering" in html)


def main():
    for fn in [t_fold_rows_fut_and_opt, t_pull_into_fut_opt, t_field_wise_freeze,
               t_pctile, t_render_has_options_and_states]:
        fn()
    print(f"\n{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
