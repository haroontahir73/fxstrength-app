"""Local CME VOI Totals .xls import. No network, no real 6 Nov figures.

    python test_voi_totals_oi.py

The workbook fixture under testdata/ is synthetic. Date and status are arguments.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bulletin_oi as bo                                          # noqa: E402
import oi                                                          # noqa: E402
import voi_totals_oi as vo                                        # noqa: E402

FAILS = []
HERE = Path(__file__).parent
SAMPLE = HERE / "testdata" / "voi_totals_sample.xls"
MINI = HERE / "testdata" / "voi_totals_mini.xls"

# Synthetic fixture, not a published session. Decoy products in the same
# workbook are much larger and must not be added.
EXPECT = {
    "GC": (1100, 15, 7, 100, 80, 20),
    "SI": (1210, 8, -3, 15, 9, 6),
    "CL": (310, 40, -6, 70, 30, 40),
    "6E": (410, 12, -2, 24, 11, 13),
    "6B": (510, 6, 4, 33, 14, 19),
    "6J": (610, 14, 8, 18, 7, 11),
    "6A": (710, 9, 2, 27, 10, 17),
    "6N": (810, 4, 1, 12, 5, 7),
    "6C": (910, 7, -5, 40, 16, 24),
    "6S": (1010, 5, 6, 9, 2, 7),
}


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILS.append(name)


def jpy_block(opt=18, calls=7, puts=11, fut=610, printed=8):
    """Standard JPY plus the lookalikes a loose name or code match would steal."""
    assert calls + puts == opt
    return [
        ["Name", "Type", "Globex", "Clear Port", "Volume", "Open Interest", "Change"],
        ["Energy", "", "", "", "", "", ""],
        ["Henry Hub Natural Gas Futures", "Futures", "6J", "0", "9", "99999", "+1"],
        ["Natural Gas Option (European)", "Options", "6J", "0", "20", "88888", "+20"],
        ["Natural Gas Option (European)", "Calls", "6J", "0", "10", "44444", "+10"],
        ["Natural Gas Option (European)", "Puts", "6J", "0", "10", "44444", "+10"],
        ["Japan Crude Cocktail (Detailed) Futures", "Futures", "1", "0", "1", "2000", "0"],
        ["E-mini Japanese Yen Futures", "Futures", "3", "0", "3", "5000", "+3"],
        ["Micro JPY/USD Futures", "Futures", "3", "0", "3", "4000", "+3"],
        ["Euro/Japanese Yen Futures", "Futures", "1", "0", "1", "3000", "0"],
        ["Japanese Yen Futures", "Futures", "14", "0", "14", str(fut), f"{printed:+d}"],
        ["JPY/USD Weekly Friday Options - Week 2", "Options", "5", "0", "5", "7700", "+5"],
        ["JPY/USD Monthly Options", "Options", "6", "0", "6", str(opt), "-4"],
        ["JPY/USD Monthly Options", "Calls", "2", "0", "2", str(calls), "-1"],
        ["JPY/USD Monthly Options", "Puts", "4", "0", "4", str(puts), "-3"],
    ]


def t_collisions_and_exact_names():
    report = vo.parse_voi_tables([("fx.xls", jpy_block())], "2024-03-04", "preliminary")
    jpy = report["markets"]["6J"]
    check("trade date is the argument", report["trade_date"] == "2024-03-04" and report["status"] == "PRELIMINARY")
    check("JPY is the monthly standard row, not gas or a mini",
          jpy["available"] and jpy["options"]["total_oi"] == 18 and jpy["options"]["calls_oi"] == 7
          and jpy["options"]["puts_oi"] == 11 and jpy["futures"]["open_interest"] == 610
          and jpy["options"]["product"] == "JPY/USD Monthly Options")
    check("natural gas 6J and weeklies were not added",
          jpy["options"]["total_oi"] not in (88888, 18 + 7700, 18 + 88888))
    check("a market with no standard row is unavailable, not zero",
          report["markets"]["6N"]["available"] is False and report["markets"]["6N"]["options"] is None
          and report["markets"]["CL"]["futures"] is None)
    check("bases stay distinct from the bulletin monthly-plus-weekly series",
          vo.BASIS != bo.BASIS and "weekly" in bo.BASIS and "weekly" not in vo.BASIS)


def t_calls_must_equal_total_and_zero_is_real():
    rows = jpy_block()
    # Break the put row so calls + puts no longer match the options total.
    rows[-1][5] = "99"
    bad = vo.parse_voi_tables([("fx.xls", rows)], "2024-03-04", "final")
    jpy = bad["markets"]["6J"]
    check("call/put mismatch is unavailable", jpy["available"] is False and "99" in jpy["reason"]
          and jpy["options"] is None)
    hist = {}
    vo.apply_options(hist, bad)
    check("mismatch is not stored as the options total",
          "2024-03-04" not in hist or "JPY" not in hist.get("2024-03-04", {})
          or "oi_opt" not in hist["2024-03-04"].get("JPY", {}))
    zero = [
        ["Name", "Type", "Volume", "Open Interest", "Change"],
        ["Swiss Franc Futures", "Futures", "0", "0", "0"],
        ["CHF/USD Monthly Options", "Options", "0", "0", "0"],
        ["CHF/USD Monthly Options", "Calls", "0", "0", "0"],
        ["CHF/USD Monthly Options", "Puts", "0", "0", "0"],
    ]
    z = vo.parse_voi_tables([("fx.xls", zero)], "2024-03-04", "preliminary")
    chf = z["markets"]["6S"]
    check("a printed zero is a real open interest",
          chf["available"] and chf["options"]["total_oi"] == 0 and chf["futures"]["open_interest"] == 0)


def t_header_is_by_name():
    rows = [
        ["Change", "Open Outcry", "Name", "Type", "Open Interest", "Volume"],
        ["+5", "999999", "Crude Oil Futures", "Futures", "310", "40"],
        ["+8", "888888", "Crude Oil Option", "Options", "70", "9"],
        ["+5", "1", "Micro WTI Crude Oil Option", "Options", "5000", "8"],
        ["+5", "1", "Crude Oil Option", "Calls", "30", "4"],
        ["+3", "1", "Crude Oil Friday Weekly Option - Week 1", "Options", "1500", "3"],
        ["+3", "1", "Crude Oil Option", "Puts", "40", "5"],
        ["0", "1", "WTI Financial Futures", "Futures", "8000", "2"],
    ]
    report = vo.parse_voi_tables([("energy.xls", rows)], "2024-03-04", "Preliminary")
    oil = report["markets"]["CL"]
    check("columns follow the header, and WTI lookalikes stay out",
          oil["available"] and oil["futures"]["open_interest"] == 310 and oil["futures"]["volume"] == 40
          and oil["futures"]["change"] == 5 and oil["options"]["total_oi"] == 70
          and oil["options"]["calls_oi"] == 30 and oil["options"]["puts_oi"] == 40
          and oil["futures"]["open_interest"] != 999999)


def t_two_files_conflict_and_agree():
    a = jpy_block()
    b = [row[:] for row in a]
    same = vo.parse_voi_tables([("a.xls", a), ("b.xls", b)], "2024-03-04", "final")
    check("the same figures in two files are one reading",
          same["markets"]["6J"]["available"] and same["markets"]["6J"]["options"]["total_oi"] == 18)
    b[-3][5] = "999"  # the standard JPY/USD Monthly Options total
    clash = vo.parse_voi_tables([("a.xls", a), ("b.xls", b)], "2024-03-04", "final")
    check("two different totals conflict instead of being added",
          clash["markets"]["6J"]["available"] is False and "twice" in clash["markets"]["6J"]["reason"])


def t_apply_preserves_futures_and_final():
    report = vo.parse_voi_tables([("fx.xls", jpy_block())], "2024-03-04", "preliminary")
    hist = {"2024-03-04": {"JPY": {"oi": 111, "chg": 4, "volume": 9}}}
    vo.apply_options(hist, report)
    cell = hist["2024-03-04"]["JPY"]
    check("futures cells stay, and futures from the file are not written",
          cell["oi"] == 111 and cell["chg"] == 4 and cell["volume"] == 9 and "oi" in cell)
    check("first options day has no fabricated change",
          cell["oi_opt"] == 18 and cell["chg_opt"] is None and cell["oi_opt_calls"] == 7
          and cell["oi_opt_puts"] == 11 and cell["oi_opt_status"] == "PRELIMINARY"
          and cell["oi_opt_basis"] == vo.BASIS)
    check("missing markets are not inserted as empty objects",
          "NZD" not in hist["2024-03-04"] and "Gold" not in hist["2024-03-04"])
    later = vo.parse_voi_tables([("fx.xls", jpy_block(opt=25, calls=10, puts=15))], "2024-03-05", "preliminary")
    vo.apply_options(hist, later)
    check("change is against the prior reading of this basis, not the printed column",
          hist["2024-03-05"]["JPY"]["oi_opt"] == 25 and hist["2024-03-05"]["JPY"]["chg_opt"] == 7)
    final = vo.parse_voi_tables([("fx.xls", jpy_block(opt=30, calls=12, puts=18))], "2024-03-05", "final")
    vo.apply_options(hist, final)
    check("a different status is not used as the prior change",
          hist["2024-03-05"]["JPY"]["oi_opt"] == 30 and hist["2024-03-05"]["JPY"]["chg_opt"] is None
          and hist["2024-03-05"]["JPY"]["oi_opt_status"] == "FINAL")
    again = vo.parse_voi_tables([("fx.xls", jpy_block(opt=1, calls=1, puts=0))], "2024-03-05", "preliminary")
    vo.apply_options(hist, again)
    check("preliminary does not overwrite final",
          hist["2024-03-05"]["JPY"]["oi_opt"] == 30 and hist["2024-03-05"]["JPY"]["oi_opt_status"] == "FINAL")
    bulletin = {"2024-03-06": {"JPY": {"oi": 50, "oi_opt": 19, "chg_opt": None,
                                       "oi_opt_status": "FINAL", "oi_opt_basis": bo.BASIS}}}
    nxt = vo.parse_voi_tables([("fx.xls", jpy_block())], "2024-03-06", "final")
    vo.apply_options(bulletin, nxt)
    check("monthly-only file does not overwrite the bulletin monthly-plus-weekly total",
          bulletin["2024-03-06"]["JPY"]["oi_opt"] == 19 and bulletin["2024-03-06"]["JPY"]["oi_opt_basis"] == bo.BASIS
          and bulletin["2024-03-06"]["JPY"]["oi"] == 50)
    nxt_day = vo.parse_voi_tables([("fx.xls", jpy_block(opt=40, calls=16, puts=24))], "2024-03-07", "final")
    vo.apply_options(bulletin, nxt_day)
    check("a bulletin reading in between blocks a made-up delta",
          bulletin["2024-03-07"]["JPY"]["chg_opt"] is None and bulletin["2024-03-07"]["JPY"]["oi_opt"] == 40)


def t_merge_days_locks_final_options():
    hist = {"2024-03-05": {"JPY": {"oi": 111, "chg": 4, "volume": 9, "oi_opt": 30, "chg_opt": None,
                                   "oi_opt_status": "FINAL", "oi_opt_basis": vo.BASIS}}}
    oi.merge_days(hist, {"2024-03-05": {"JPY": {"oi": 999, "oi_opt": 1, "chg_opt": 5,
                                                "oi_opt_calls": 1, "oi_opt_status": "PRELIMINARY"}}},
                  freeze=False)
    cell = hist["2024-03-05"]["JPY"]
    check("final options stay even when futures freeze is off",
          cell["oi_opt"] == 30 and cell["oi_opt_status"] == "FINAL")
    # oi_opt_calls was absent, and the key starts with oi_opt, so the lock covers it.
    check("final options fields are not filled in by a later pull", cell.get("oi_opt_calls") is None)
    check("futures can still move when freeze is off", cell["oi"] == 999)


def t_merge_days_locks_preliminary_basis():
    hist = {"2024-03-04": {"Gold": {"oi": 111, "chg": 4, "volume": 9,
                                    "oi_opt": 100, "chg_opt": None,
                                    "oi_opt_calls": 60, "oi_opt_puts": 40,
                                    "oi_opt_status": "PRELIMINARY", "oi_opt_basis": vo.BASIS}}}
    oi.merge_days(hist, {"2024-03-04": {"Gold": {
        "oi": 1, "chg": 1, "volume": 1, "oi_opt": 9, "chg_opt": 9,
        "oi_opt_calls": 4, "oi_opt_puts": 5, "oi_opt_status": "FINAL",
        "oi_opt_basis": "cme-optionOi"}}}, freeze=False)
    cell = hist["2024-03-04"]["Gold"]
    check("preliminary voi options survive a later pull, including a blank change",
          cell["oi_opt"] == 100 and cell["chg_opt"] is None
          and cell["oi_opt_calls"] == 60 and cell["oi_opt_puts"] == 40
          and cell["oi_opt_status"] == "PRELIMINARY" and cell["oi_opt_basis"] == vo.BASIS)
    check("futures can still be revised when that freeze is off", cell["oi"] == 1)


def t_sample_xls_and_mini_stream():
    report = vo.build_report([SAMPLE], "2024-03-04", "preliminary")
    check("synthetic workbook covers every standard market", report["ok"] and not report["problems"])
    for code, (fut, vol, chg, opt, calls, puts) in EXPECT.items():
        rec = report["markets"][code]
        got_f, got_o = rec["futures"], rec["options"]
        good = (rec["available"] and got_f["open_interest"] == fut and got_f["volume"] == vol
                and got_f["change"] == chg and got_o["total_oi"] == opt
                and got_o["calls_oi"] == calls and got_o["puts_oi"] == puts
                and calls + puts == opt)
        check(f"{code} read from the xls", good)
    blob = SAMPLE.read_bytes() + MINI.read_bytes()
    leaked = [n for n in (b"403730", b"403,730", b"845398", b"2592056", b"2,592,056") if n in blob]
    check("fixture does not carry the 6 Nov open-interest figures", not leaked)
    mini = vo.build_report([MINI], "2024-03-04", "final")
    gold = mini["markets"]["GC"]
    check("mini-stream workbook still yields the standard gold rows",
          gold["available"] and gold["futures"]["open_interest"] == 60 and gold["options"]["total_oi"] == 9
          and gold["options"]["calls_oi"] == 4 and gold["options"]["puts_oi"] == 5)
    check("mini workbook is not a complete session", mini["ok"] is False)


def t_cli_requires_date_and_status_and_writes_options_only():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        dated = tmp / "2024-03-04-preliminary.xls"
        shutil.copy(SAMPLE, dated)
        os.utime(dated, (1_560_000_000, 1_560_000_000))
        check("filename and mtime are not a trade date",
              vo.main(["report", str(dated), "--data", str(tmp)]) == 2)
        check("status is not optional",
              vo.main(["report", "--date", "2024-03-04", "--data", str(tmp), str(dated)]) == 2)
        check("a bad status is rejected",
              vo.main(["report", "--date", "2024-03-04", "--status", "settled", str(dated)]) == 2)
        check("a bad date is rejected",
              vo.main(["report", "--date", "03/04/2024", "--status", "final", str(dated)]) == 2)
        rc = vo.main(["report", "--date", "2024-03-04", "--status", "preliminary",
                      "--data", str(tmp), str(dated)])
        check("report is a dry run", rc == 0 and not (tmp / "oi_history.json").exists())
        rc = vo.main(["apply", "--date", "2024-03-04", "--status", "preliminary",
                      "--data", str(tmp), str(MINI)])
        check("incomplete apply writes nothing", rc == 1 and not (tmp / "oi_history.json").exists())
        rc = vo.main(["apply", "--date", "2024-03-04", "--status", "preliminary",
                      "--data", str(tmp), str(dated)])
        hist = json.loads((tmp / "oi_history.json").read_text(encoding="utf-8"))
        jpy = hist["2024-03-04"]["JPY"]
        check("apply stores options only",
              rc == 0 and jpy["oi_opt"] == 18 and jpy["chg_opt"] is None and "oi" not in jpy
              and jpy["oi_opt_status"] == "PRELIMINARY" and hist["2024-03-04"]["Gold"]["oi_opt"] == 100)
        # A final already on the book must survive a second preliminary apply.
        hist["2024-03-04"]["JPY"]["oi_opt_status"] = "FINAL"
        hist["2024-03-04"]["JPY"]["oi"] = 111
        (tmp / "oi_history.json").write_text(json.dumps(hist), encoding="utf-8")
        rc = vo.main(["apply", "--date", "2024-03-04", "--status", "preliminary",
                      "--data", str(tmp), str(dated)])
        hist = json.loads((tmp / "oi_history.json").read_text(encoding="utf-8"))
        check("cli preliminary leaves a final reading and its futures cell",
              rc == 0 and hist["2024-03-04"]["JPY"]["oi_opt"] == 18
              and hist["2024-03-04"]["JPY"]["oi_opt_status"] == "FINAL"
              and hist["2024-03-04"]["JPY"]["oi"] == 111)


def t_no_network_and_html_rejected():
    src = (HERE / "voi_totals_oi.py").read_text(encoding="utf-8")
    banned = ("urllib", "urlopen", "cmegroup", "requests", "getmtime", "st_mtime", "socket")
    check("importer source does not fetch or read mtime", all(word not in src for word in banned))
    with tempfile.TemporaryDirectory() as tmp:
        html = Path(tmp) / "export.xls"
        html.write_text("<table><tr><td>Gold Futures</td></tr></table>", encoding="utf-8")
        try:
            vo.read_voi_table(html)
            check("html export is rejected", False)
        except ValueError as e:
            check("html export is rejected", "HTML" in str(e))


def main():
    for fn in (t_collisions_and_exact_names, t_calls_must_equal_total_and_zero_is_real,
               t_header_is_by_name, t_two_files_conflict_and_agree,
               t_apply_preserves_futures_and_final, t_merge_days_locks_final_options,
               t_merge_days_locks_preliminary_basis,
               t_sample_xls_and_mini_stream, t_cli_requires_date_and_status_and_writes_options_only,
               t_no_network_and_html_rejected):
        fn()
    print(f"\n{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
