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



def t_snapshot_missing_is_dash_and_zero_is_real():
    hist = {"2026-11-09": {"Gold": {"oi_opt": 10, "oi_opt_calls": None, "oi_opt_puts": 0,
                                    "chg_opt": None, "oi_opt_status": "PRELIMINARY",
                                    "oi_opt_basis": oi.VOI_OPT_BASIS},
                           "NZD": {"oi_opt": 5, "oi_opt_calls": 2, "oi_opt_puts": 3,
                                   "chg_opt": 0, "oi_opt_status": "FINAL",
                                   "oi_opt_basis": oi.VOI_OPT_BASIS}}}
    html = oi.options_snapshot(hist)
    gold = html.split(">Gold")[1].split("</tr>")[0]
    check("missing calls and change are dashes", gold.count("&mdash;") >= 2 and "+0" not in gold)
    check("a stored put of zero is shown", ">0<" in gold and "PRELIMINARY" in gold and ">10<" in gold)
    nzd = html.split(">NZD")[1].split("</tr>")[0]
    check("a real zero change is shown", "+0" in nzd and "FINAL" in nzd)
    chf = html.split(">CHF")[1].split("</tr>")[0]
    check("a market with no options is dashes, not zeros",
          chf.count("&mdash;") >= 5 and ">0<" not in chf and "+0" not in chf)
    check("labeled monthly standard raw counts",
          "raw contract counts (calls + puts)" in html and "monthly standard options only" in html)
    check("snapshot lists every standard code",
          all(code in html for code in ("GC", "SI", "CL", "6E", "6B", "6J", "6A", "6N", "6C", "6S")))


def t_options_only_day_does_not_relabel_futures():
    hist = {"2026-11-06": {"EUR": {"oi": 10, "chg": 1, "oi_opt": 19, "chg_opt": None,
                                   "oi_opt_calls": 11, "oi_opt_puts": 8,
                                   "oi_opt_status": "PRELIMINARY", "oi_opt_basis": oi.VOI_OPT_BASIS}},
            "2026-11-09": {"EUR": {"oi_opt": 25, "chg_opt": 6, "oi_opt_calls": 14, "oi_opt_puts": 11,
                                   "oi_opt_status": "PRELIMINARY", "oi_opt_basis": oi.VOI_OPT_BASIS}}}
    html = oi.render(hist, {}, {}, {})
    check("futures banner stays on the futures day",
          "trade date 2026-11-06" in html and "trade date 2026-11-09" not in html)
    check("options block shows the later reading",
          "2026-11-09" in html and ">25<" in html and "Calls" in html and "PRELIMINARY" in html)
    before = json.dumps(hist, sort_keys=True)
    oi.render(hist, {}, {}, {})
    check("render does not mutate history", json.dumps(hist, sort_keys=True) == before)


def t_render_only_does_not_rewrite_options(tmp_path=None):
    import tempfile
    d = Path(tempfile.mkdtemp())
    hist = {"2026-11-06": {"EUR": {"oi": 10, "chg": 1, "volume": 1,
                                   "oi_opt": 19, "chg_opt": None,
                                   "oi_opt_calls": 11, "oi_opt_puts": 8,
                                   "oi_opt_status": "PRELIMINARY",
                                   "oi_opt_basis": oi.VOI_OPT_BASIS}}}
    path = d / "oi_history.json"
    path.write_text(json.dumps(hist), encoding="utf-8")
    dash = d / "dashboard.html"
    dash.write_text("<html><body></body></html>", encoding="utf-8")
    saved = (oi.HIST_FILE, oi.STATE_FILE, oi.PAGE, oi.price_moves, sys.argv)
    oi.HIST_FILE = path
    oi.STATE_FILE = d / "oi_state.json"
    oi.PAGE = d / "open-interest.html"
    oi.price_moves = lambda *a, **k: {}
    sys.argv = ["oi.py", "--render-only", str(dash)]
    try:
        oi.main()
    finally:
        oi.HIST_FILE, oi.STATE_FILE, oi.PAGE, oi.price_moves, sys.argv = saved
    got = json.loads(path.read_text(encoding="utf-8"))
    check("render-only leaves the options file byte-for-byte in meaning", got == hist)
    page = (d / "open-interest.html").read_text(encoding="utf-8")
    card = dash.read_text(encoding="utf-8")
    check("standalone page shows the options reading",
          "19" in page and "PRELIMINARY" in page and "monthly standard options only" in page
          and "11" in page and "8" in page)
    check("dashboard card names the options series",
          "monthly standard options only" in card and "PRELIMINARY" in card)


def t_render_has_options_and_states():
    hist = {}
    for i in range(14):
        day = (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()
        hist[day] = {"EUR": {"oi": 700000 + i * 1000, "chg": 1000, "volume": 50000,
                             "oi_opt": 120000 + i * 200, "chg_opt": 200, "volume_opt": 8000}}
    html = oi.render(hist, {"last_good_date": sorted(hist)[-1]}, oi.analyse(hist, {}), {})
    check("merged table has futures column", "Fut" in html)
    check("merged table has options column", "Opt" in html)
    check("page preserves the 4-state key", "New money long" in html and "Short covering" in html)


def main():
    for fn in [t_fold_rows_fut_and_opt, t_pull_into_fut_opt, t_field_wise_freeze,
               t_pctile, t_snapshot_missing_is_dash_and_zero_is_real,
               t_options_only_day_does_not_relabel_futures,
               t_render_only_does_not_rewrite_options,
               t_render_has_options_and_states]:
        fn()
    print(f"\n{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
