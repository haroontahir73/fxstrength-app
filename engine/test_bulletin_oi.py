"""Local daily-bulletin options import. No network, no real CME file.

    python test_bulletin_oi.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bulletin_oi as bo                                          # noqa: E402
import oi                                                          # noqa: E402

FAILS = []


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILS.append(name)


def line(code, name, side, oi, chg=1):
    buf = [" "] * 140
    for i, ch in enumerate(f"{code} {name}"[:36]):
        buf[i] = ch
    if side:
        buf[42] = side
    text = str(oi)
    start = 107 - len(text)
    for i, ch in enumerate(text):
        buf[start + i] = ch
    buf[110] = "+" if chg >= 0 else "-"
    for i, ch in enumerate(str(abs(chg))):
        buf[116 + i] = ch
    return "".join(buf).rstrip()


def total(name, oi, chg=1):
    return line("TOTAL      ", name, None, oi, chg).replace("TOTAL      ", "TOTAL       ", 1)


def page(banner, title, rows):
    head = (f"{banner} EXCHANGE SUMMARY VOLUME AND OPEN INTEREST {title}\n"
            "FINAL\n"
            "BULLETIN # 9@ Thu, Jan 02, 2026\n")
    return head + "\n".join(rows) + "\n"


def sample():
    fx = page("01B", "FX OPTIONS ON FUTURES", [
        line("JPU", "JPY/USD Monthly Options", "C", 10),
        line("JPU", "JPY/USD Monthly Options", "P", 5),
        total("JPY/USD Monthly Options", 15),
        line("2JY", "JPY/USD Weekly Friday Options", "C", 3),
        line("2JY", "JPY/USD Weekly Friday Options", "P", 1),
        total("JPY/USD Weekly Friday Options", 4),
        # same code printed under a second name; must not be added twice
        line("2JY", "JAPANESE YEN WK2 OPTIONS", "C", 3),
        line("2JY", "JAPANESE YEN WK2 OPTIONS", "P", 1),
        total("JAPANESE YEN WK2 OPTIONS", 4),
    ])
    gas = page("02C", "ENERGY OPTIONS ON FUTURES", [
        line("6J", "NATURAL GAS OPTION", "C", 999),
        line("6J", "NATURAL GAS OPTION", "P", 1),
        total("NATURAL GAS OPTION", 1000),
        line("LO", "NYMEX CRUDE OIL OPTIONS (PHY)", "C", 20),
        line("LO", "NYMEX CRUDE OIL OPTIONS (PHY)", "P", 22),
        total("NYMEX CRUDE OIL OPTIONS (PHY)", 42),
        line("MCO", "MICRO WTI CRUDE OIL OPT", "C", 7),
        line("MCO", "MICRO WTI CRUDE OIL OPT", "P", 1),
        total("MICRO WTI CRUDE OIL OPT", 8),
    ])
    bad = page("02B", "METAL OPTIONS ON FUTURES", [
        line("OG", "COMEX GOLD OPTIONS", "C", 3),
        line("OG", "COMEX GOLD OPTIONS", "P", 4),
        total("COMEX GOLD OPTIONS", 9),
    ])
    return fx + "\f" + gas + "\f" + bad


def t_parse_coverage():
    report = bo.parse_layouts([sample()])
    check("one final bulletin", report["ok"] and report["trade_date"] == "2026-01-02" and report["bulletin"] == "9")
    jpy = report["markets"]["JPY"]
    check("JPY is monthly plus weekly, duplicate not added",
          jpy["available"] and jpy["oi_opt"] == 19 and jpy["calls"] == 13 and jpy["puts"] == 6
          and [f["code"] for f in jpy["families"]] == ["JPU", "2JY"])
    check("6J natural gas is not JPY", jpy["oi_opt"] != 1000 and "6J" not in
          [f["code"] for f in jpy["families"]])
    oil = report["markets"]["Oil"]
    check("crude options kept, micro crude dropped",
          oil["available"] and oil["oi_opt"] == 42 and [f["code"] for f in oil["families"]] == ["LO"])
    check("gold call/put mismatch is unavailable, not a guessed total",
          report["markets"]["Gold"]["available"] is False and "OG" in report["markets"]["Gold"]["reason"])
    check("NZD has no rows so it is unavailable, not zero",
          report["markets"]["NZD"]["available"] is False)


def t_apply_preserves_futures_and_delta():
    report = bo.parse_layouts([sample()])
    hist = {"2026-01-02": {"JPY": {"oi": 111, "chg": 4, "volume": 9},
                           "Oil": {"oi": 50, "chg": 1, "volume": 2}}}
    bo.apply_options(hist, report)
    check("futures cells untouched", hist["2026-01-02"]["JPY"]["oi"] == 111 and hist["2026-01-02"]["Oil"]["oi"] == 50)
    check("first options observation has no fabricated change",
          hist["2026-01-02"]["JPY"]["oi_opt"] == 19 and hist["2026-01-02"]["JPY"]["chg_opt"] is None)
    check("marked final bulletin", hist["2026-01-02"]["JPY"]["oi_opt_status"] == "FINAL")
    # next day, same basis
    later = bo.parse_layouts([sample().replace("2026-01-02", "2026-01-03").replace("Jan 02", "Jan 03")])
    # the date string in the header is "Jan 02" -> replace already done if both forms
    later["trade_date"] = "2026-01-03"
    later["markets"]["JPY"]["oi_opt"] = 25
    bo.apply_options(hist, later)
    check("change is against the prior final reading", hist["2026-01-03"]["JPY"]["chg_opt"] == 6)
    check("still one row per date", list(hist) == ["2026-01-02", "2026-01-03"])
    # a different basis is not a comparable prior
    hist["2026-01-03"]["Oil"]["oi_opt_basis"] = "something-else"
    hist["2026-01-03"]["Oil"]["oi_opt"] = 1
    hist["2026-01-03"]["Oil"]["oi_opt_status"] = "FINAL"
    nxt = dict(later)
    nxt["trade_date"] = "2026-01-04"
    bo.apply_options(hist, nxt)
    check("different basis does not invent a delta", hist["2026-01-04"]["Oil"]["chg_opt"] is None)


def t_sync_cannot_wipe_final_options():
    hist = {"2026-01-02": {"JPY": {"oi": 111, "chg": 4, "volume": 9,
                                   "oi_opt": 19, "chg_opt": None, "oi_opt_status": "FINAL"}}}
    oi.merge_days(hist, {"2026-01-02": {"JPY": {"oi": 999, "oi_opt": 1, "chg_opt": 5}}})
    check("frozen futures stay, and final options stay",
          hist["2026-01-02"]["JPY"]["oi"] == 111 and hist["2026-01-02"]["JPY"]["oi_opt"] == 19)
    oi.overlay_futures(hist, {"2026-01-02": {"JPY": {"oi": 222, "chg": 8, "volume": 3}}})
    check("workbook updates futures and leaves options",
          hist["2026-01-02"]["JPY"]["oi"] == 222 and hist["2026-01-02"]["JPY"]["oi_opt"] == 19
          and hist["2026-01-02"]["JPY"]["volume"] == 3)


def t_render_says_raw_contracts():
    hist = {"2026-01-02": {"EUR": {"oi": 10, "chg": 1, "volume": 1,
                                   "oi_opt": 19, "chg_opt": None, "oi_opt_status": "FINAL"}}}
    html = oi.render(hist, {"last_good_date": "2026-01-02"}, {}, {})
    check("options column and raw-count wording",
          "Options OI" in html and "outstanding option contracts" in html
          and "futures-equivalent" in html and "Options FE OI" not in html)
    check("blank change, not a made-up zero", "Opt" in html and "+0" not in html.split("Options OI", 1)[-1][:400])


def main():
    for fn in (t_parse_coverage, t_apply_preserves_futures_and_delta,
               t_sync_cannot_wipe_final_options, t_render_says_raw_contracts):
        fn()
    print(f"\n{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
