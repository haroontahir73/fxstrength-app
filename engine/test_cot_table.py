"""Tests for the Legacy COT report tables and 50-week reports (cot_table.py + renderer).

The fixture is real CFTC Legacy futures-only data for three report weeks (22 Sep, 29 Sep and
6 Oct 2026) as the Socrata API returns it, plus the 13 latest WTI reports. Headline checks:
  * the 29 Sep week reproduces a published "Commitment of Traders Report" table row for row
    (one exception, the table's: it printed NZD commercial short as 54,797 where CFTC has
    64,797, so for NZD the expected values here are CFTC's);
  * the oil 50-week report's 13-wk Avg row reproduces the user's oil sheet exactly.

    python test_cot_table.py

Stdlib only, offline. Every build runs against a temporary data dir with an injected fetch,
so the suite never reaches CFTC and never writes the live data/ directory.
"""
import json, sys, tempfile, re
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL = [], []
UTC = dt.timezone.utc


def check(group, name, cond, detail=""):
    (PASS if cond else FAIL).append((group, name, detail))
    if not cond:
        print(f"  FAIL  [{group}] {name}" + (f"\n          {detail}" if detail else ""))


def _tmp():
    d = Path(tempfile.mkdtemp(prefix="fxcot-"))
    (d / "data").mkdir()
    return d / "data"


# date, code, name, open interest, NC long, NC short, NC spread, Comm long, Comm short
_FIX = """
2026-10-06 232741 AUSTRALIAN DOLLAR 328682 100138 198726 2406 193742 108650
2026-10-06 209742 NASDAQ MINI 283586 86745 28622 6120 148690 217136
2026-10-06 13874A E-MINI S&P 500 1932227 212009 366079 34781 1437320 1394255
2026-10-06 124608 MICRO E-MINI DJIA 42889 28891 5762 45 3822 28127
2026-10-06 112741 NZ DOLLAR 129883 27821 57167 626 97032 65652
2026-10-06 099741 EURO FX 870291 243020 342352 39238 504296 432272
2026-10-06 098662 USD INDEX 51095 30526 18515 1796 14771 29042
2026-10-06 097741 JAPANESE YEN 368138 190368 128043 3004 140231 201980
2026-10-06 067651 WTI-PHYSICAL 1889422 336706 237646 581197 894330 1020788
2026-10-06 084691 SILVER 105130 33648 10495 12831 31745 73337
2026-10-06 085692 COPPER- #1 304033 108820 39386 74259 103794 181717
2026-10-06 088691 GOLD 396109 245403 35068 44440 55802 300329
2026-10-06 090741 CANADIAN DOLLAR 371048 91109 181272 4394 248311 144809
2026-10-06 092741 SWISS FRANC 137185 27818 51558 6649 92327 55041
2026-10-06 096742 BRITISH POUND 265890 45259 142870 2899 197105 85238
2026-10-06 020601 UST BOND 1898067 221234 341046 19316 1422476 1373770
2026-10-06 124603 DJIA x $5 95576 23625 14134 468 57201 72548
2026-09-29 209742 NASDAQ MINI 270554 81430 30183 6749 142377 203968
2026-09-29 232741 AUSTRALIAN DOLLAR 309800 109690 172929 1311 165187 118839
2026-09-29 067651 WTI-PHYSICAL 1878576 361351 251888 563738 873637 1016255
2026-09-29 084691 SILVER 107047 33482 11399 12335 34095 74105
2026-09-29 085692 COPPER- #1 301201 123305 37894 66934 93132 187973
2026-09-29 088691 GOLD 406456 249736 31104 49105 58831 309798
2026-09-29 090741 CANADIAN DOLLAR 347274 85047 163718 4177 229952 140388
2026-09-29 092741 SWISS FRANC 133872 24828 49445 5336 94396 54256
2026-09-29 096742 BRITISH POUND 251740 41396 132471 2062 183786 82904
2026-09-29 097741 JAPANESE YEN 360720 176266 120826 4632 144855 200840
2026-09-29 098662 USD INDEX 48342 29742 17861 1518 13524 27398
2026-09-29 099741 EURO FX 853959 238183 301439 31864 503847 463270
2026-09-29 112741 NZ DOLLAR 114777 23338 40653 3388 83791 64797
2026-09-29 124608 MICRO E-MINI DJIA 19531 6002 4184 49 3420 5944
2026-09-29 13874A E-MINI S&P 500 1895922 209587 352086 28572 1410900 1375619
2026-09-29 020601 UST BOND 1879173 189516 376391 24906 1423264 1314956
2026-09-29 124603 DJIA x $5 88098 22490 12646 217 51108 66936
2026-09-22 209742 NASDAQ MINI 286321 88507 32357 4624 152580 218343
2026-09-22 232741 AUSTRALIAN DOLLAR 306488 108533 155347 1206 161396 134236
2026-09-22 067651 WTI-PHYSICAL 1841811 360810 219704 557266 847389 1017278
2026-09-22 084691 SILVER 106474 34701 9257 12867 31176 75930
2026-09-22 085692 COPPER- #1 301657 129398 38876 59949 92911 193346
2026-09-22 088691 GOLD 412800 253982 28129 48923 57458 320361
2026-09-22 090741 CANADIAN DOLLAR 311085 86030 139240 3234 191370 135372
2026-09-22 092741 SWISS FRANC 133495 22189 48941 5737 96091 52975
2026-09-22 096742 BRITISH POUND 244925 54455 137023 3225 162042 74362
2026-09-22 097741 JAPANESE YEN 378701 192274 120292 4376 143954 220368
2026-09-22 098662 USD INDEX 46328 27768 17438 1866 13356 25523
2026-09-22 099741 EURO FX 821689 220708 273042 31289 489579 460612
2026-09-22 112741 NZ DOLLAR 101377 21571 32951 4475 71430 59019
2026-09-22 13874A E-MINI S&P 500 1890653 218043 351271 34061 1388362 1359482
2026-09-22 020601 UST BOND 1887864 184831 340636 30313 1444003 1353636
2026-09-22 124603 DJIA x $5 88087 20365 10295 640 52042 68354
"""

# The 13 latest WTI reports (Non-Commercial long/short, Commercial long/short), for the
# oil-sheet check. Same CFTC dataset.
_OIL13 = ("261006,336706,237646,894330,1020788;260929,361351,251888,873637,1016255;"
          "260922,360810,219704,847389,1017278;260915,371202,235297,895203,1060061;"
          "260908,350118,213539,889086,1055271;260901,332448,202537,885135,1041140;"
          "260825,323243,199794,892144,1048390;260818,320159,198069,894821,1047908;"
          "260811,314846,215650,897344,1026981;260804,308841,196398,887525,1041300;"
          "260728,314992,194884,871589,1030411;260721,310182,228493,896294,1004895;"
          "260714,302429,239746,923364,1008452")


def _raw(date, code, name, oi, ncl, ncs, ncsp, cl, cs, fo="FutOnly"):
    """A Socrata-shaped row (all strings). Non-reportable is derived so CFTC's identity
    (NC + spreading + Comm + non-reportable = OI, each side) holds, as it does in the data."""
    return {"report_date_as_yyyy_mm_dd": f"{date}T00:00:00.000",
            "cftc_contract_market_code": code, "market_and_exchange_names": name,
            "futonly_or_combined": fo, "open_interest_all": str(oi),
            "noncomm_positions_long_all": str(ncl), "noncomm_positions_short_all": str(ncs),
            "noncomm_postions_spread_all": str(ncsp),
            "comm_positions_long_all": str(cl), "comm_positions_short_all": str(cs),
            "nonrept_positions_long_all": str(oi - ncl - ncsp - cl),
            "nonrept_positions_short_all": str(oi - ncs - ncsp - cs)}


def fixture(upto=None, drop=()):
    rows = []
    for line in _FIX.strip().splitlines():
        p = line.split()
        date, code, nums, name = p[0], p[1], [int(x) for x in p[-6:]], " ".join(p[2:-6])
        if (upto and date > upto) or (date, code) in drop:
            continue
        rows.append(_raw(date, code, name, *nums))
    return rows


def store_of(rows):
    import cot_table as ct
    s = ct.empty_store()
    ct.merge(s, rows)
    return s


def synth(code="067651", weeks=220, end="2026-10-06", f=None, name="SYNTH"):
    """Weekly raw rows for one contract, newest `end`, values from f(i) -> (ncl, ncs, cl, cs)
    with i = 0 for the oldest week."""
    f = f or (lambda i: (100000 + (i * 7919) % 50000, 60000 + (i * 104729) % 40000,
                         300000 + (i * 1299709) % 90000, 350000 + (i * 15485863) % 70000))
    e = dt.date.fromisoformat(end)
    rows = []
    for i in range(weeks):
        d = e - dt.timedelta(weeks=weeks - 1 - i)
        ncl, ncs, cl, cs = f(i)
        oi = max(ncl + cl, ncs + cs) + 10000
        rows.append(_raw(d.isoformat(), code, name, oi, ncl, ncs, 5000, cl, cs))
    return rows


# The published 29 Sep 2026 table: Net Difference, Diff. Prev. Week, Change, % Chng.
PUBLISHED_SEP29 = {
    "USD":        (25755, 22497, 3258, 14),
    "AUDUSD":     (-109587, -73974, -35613, -48),
    "USDCAD":     (168235, 109208, 59027, 54),
    "USDCHF":     (64757, 69868, -5111, -7),
    "EURUSD":     (-103833, -81301, -22532, -28),
    "GBPUSD":     (-191957, -170248, -21709, -13),
    "USDJPY":     (-111425, -148396, 36971, 25),
    # CFTC figures; the published table shows -46,309 / -22,518 / -95% from its 54,797 typo.
    "NZDUSD":     (-36309, -23791, -12518, -53),
    "Gold":       (469599, 488756, -19157, -4),
    "Silver":     (62093, 70198, -8105, -12),
    "Copper":     (180252, 190957, -10705, -6),
    "WTI Oil":    (252081, 310995, -58914, -19),
    "S&P 500":    (-177780, -162108, -15672, -10),
    "Nasdaq 100": (112838, 121913, -9075, -7),
    "Dow Jones":  (25672, 26382, -710, -3),
    "UST Bonds":  (-295183, -246172, -49011, -20),
}


def _rows(t):
    return {r["label"]: r for g in t["groups"] for r in g["rows"]}


# ======================================================================================
def test_matches_published_table():
    import cot_table as ct
    g = "published 29 Sep table"
    t = ct.summary(store_of(fixture(upto="2026-09-29")))
    check(g, "latest week 29 Sep, previous 22 Sep",
          (t["report_date"], t["prev_date"]) == ("2026-09-29", "2026-09-22"))
    rows = _rows(t)
    check(g, "16 rows in two groups of 8",
          len(rows) == 16 and [len(x["rows"]) for x in t["groups"]] == [8, 8])
    for label, exp in PUBLISHED_SEP29.items():
        r = rows.get(label) or {}
        got = (r.get("diff"), r.get("prev_diff"), r.get("change"), r.get("pct_change"))
        check(g, f"{label}: difference / prev / change / %", got == exp, f"expected {exp}, got {got}")
    usd = rows["USD"]
    check(g, "USD Non-Commercial long/short/total/%/net",
          usd["nc"] == {"long": 29742, "short": 17861, "total": 47603,
                        "pct_long": 62, "pct_short": 38, "net": 11881}, usd["nc"])
    check(g, "USD Commercial net", usd["comm"]["net"] == -13874)
    check(g, "NZD commercial short is CFTC's 64,797",
          rows["NZDUSD"]["comm"]["short"] == 64797 and rows["NZDUSD"]["comm"]["net"] == 18994)


def test_reversal():
    import cot_table as ct
    g = "USD/xxx reversal"
    s = store_of(fixture(upto="2026-09-29"))
    rows = _rows(ct.summary(s))
    cad, jpy, eur = rows["USDCAD"], rows["USDJPY"], rows["EURUSD"]
    check(g, "USDCAD Non-Commercial swapped",
          (cad["nc"]["long"], cad["nc"]["short"]) == (163718, 85047), cad["nc"])
    check(g, "USDCAD Commercial swapped",
          (cad["comm"]["long"], cad["comm"]["short"]) == (140388, 229952), cad["comm"])
    check(g, "USDJPY Non-Commercial swapped",
          (jpy["nc"]["long"], jpy["nc"]["short"]) == (120826, 176266))
    check(g, "EURUSD untouched", eur["nc"]["long"] == 238183 and not eur["reversed"])
    check(g, "only CAD, CHF, JPY reversed",
          sorted(k for k, r in rows.items() if r["reversed"]) == ["USDCAD", "USDCHF", "USDJPY"])
    check(g, "storage keeps the ORIGINAL futures positions",
          s["contracts"]["090741"]["weeks"]["2026-09-29"][1:3] == [85047, 163718])
    rep = ct.report(s, "090741", True, "2026-09-29")
    check(g, "the reversal applies to every historical week and its comparison",
          [(w["nc"]["long"], w["nc"]["short"]) for w in rep["rows"]] == [(163718, 85047), (139240, 86030)]
          and rep["rows"][0]["prev_diff"] == 109208)
    check(g, "the footnote text is carried", "reversed" in ct.summary(s)["groups"][0]["note"])


def test_contracts():
    import cot_table as ct
    g = "contracts"
    s = store_of(fixture())
    rows = _rows(ct.summary(s))
    check(g, "Dow Jones is DJIA x $5 (124603), not the micro (124608)",
          rows["Dow Jones"]["code"] == "124603" and rows["Dow Jones"]["contract"].startswith("DJIA x $5"))
    check(g, "the micro Dow is ignored, never added in", "124608" not in s["contracts"])
    check(g, "Dow Jones 6 Oct difference from the e-mini alone", rows["Dow Jones"]["diff"] == 24838)
    check(g, "16 unique codes and unique report keys",
          len(set(ct.ALL_CODES)) == 16 and len({r[3] for r in ct.ALL_ROWS}) == 16)
    check(g, "labels as specified", [r[0] for r in ct.CM_ROWS] == [
        "Gold", "Silver", "Copper", "WTI Oil", "S&P 500", "Nasdaq 100", "Dow Jones", "UST Bonds"])
    check(g, "every row says what its contract is", all(r[4] for r in ct.ALL_ROWS))
    check(g, "USD row is explained as the dollar index, not a pair",
          "Dollar Index" in ct.FX_ROWS[0][4] and "not a pair" in ct.FX_ROWS[0][4])
    check(g, "existing drill-down keys reused (no duplicate layers)",
          [r[3] for r in ct.FX_ROWS] == ["USD", "AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD"]
          and [r[3] for r in ct.CM_ROWS][:4] == ["XAU", "XAG", "HG", "WTI"])


def test_weeks():
    import cot_table as ct
    g = "week picking"
    full = ct.summary(store_of(fixture()))
    rows = _rows(full)
    check(g, "newest full week 6 Oct vs 29 Sep", (full["report_date"], full["prev_date"]) == ("2026-10-06", "2026-09-29"))
    sep = _rows(ct.summary(store_of(fixture(upto="2026-09-29"))))
    check(g, "6 Oct 'Diff. Prev. Week' = the 29 Sep difference, every row",
          all(rows[k]["prev_diff"] == sep[k]["diff"] for k in rows))
    partial = fixture() + [dict(r, report_date_as_yyyy_mm_dd="2026-10-13T00:00:00.000")
                           for r in fixture() if r["report_date_as_yyyy_mm_dd"].startswith("2026-10-06")][:3]
    check(g, "a partly published week is skipped", ct.summary(store_of(partial))["report_date"] == "2026-10-06")
    t3 = _rows(ct.summary(store_of(fixture(drop={("2026-10-06", "020601")}))))
    check(g, "a missing contract is flagged, not filled from an old week",
          t3["UST Bonds"].get("missing") is True and "diff" not in t3["UST Bonds"])
    t4 = _rows(ct.summary(store_of(fixture(drop={("2026-09-29", "088691"), ("2026-09-22", "088691")}))))
    check(g, "no previous report -> change and % blank, row still shown",
          t4["Gold"]["missing"] is False and t4["Gold"]["prev_diff"] is None and t4["Gold"]["pct_change"] is None)
    t5 = _rows(ct.summary(store_of(fixture(drop={("2026-09-29", "088691")}))))
    check(g, "a skipped week compares with the contract's own previous report",
          t5["Gold"]["prev_date"] == "2026-09-22" and t5["Gold"]["prev_diff"] == 488756)
    check(g, "no data -> no table", ct.summary(ct.empty_store()) is None)


def test_arithmetic():
    import cot_table as ct
    g = "arithmetic"
    check(g, "62.5 -> 63 and -52.5 -> -53 (spreadsheet rounding)", ct._rnd(62.5) == 63 and ct._rnd(-52.5) == -53)
    v = lambda ncl, ncs, cl, cs: [0, ncl, ncs, 0, cl, cs, 0, 0]               # noqa: E731
    w = ct.week_figures(v(100, 50, 50, 100), v(100, 50, 50, 100), False)
    check(g, "unchanged week -> change 0, 0%", w["change"] == 0 and w["pct_change"] == 0)
    w = ct.week_figures(v(100, 50, 50, 100), v(10, 10, 10, 10), False)
    check(g, "previous difference 0 -> % Chng None (rendered N/A), no division error",
          w["prev_diff"] == 0 and w["change"] == 100 and w["pct_change"] is None)
    w = ct.week_figures(v(0, 0, 5, 5), None, False)
    check(g, "a group with no positions -> % blank", w["nc"]["pct_long"] is None and w["nc"]["total"] == 0)
    check(g, "an unusable row -> None", ct.week_figures([0, None, 1, 0, 1, 1, 0, 0], None, False) is None)


def test_validation():
    import cot_table as ct
    g = "validation + storage"
    s = ct.empty_store()
    good = fixture(upto="2026-09-22")
    bad = [dict(good[0], futonly_or_combined="FutAndOpt", report_date_as_yyyy_mm_dd="2026-09-15T00:00:00.000"),
           dict(good[1], noncomm_positions_long_all="-5", report_date_as_yyyy_mm_dd="2026-09-15T00:00:00.000"),
           dict(good[2], comm_positions_long_all="12.5", report_date_as_yyyy_mm_dd="2026-09-15T00:00:00.000"),
           dict(good[3], open_interest_all="1", report_date_as_yyyy_mm_dd="2026-09-15T00:00:00.000"),
           {k: v for k, v in good[4].items() if k != "comm_positions_short_all"},
           dict(good[5], cftc_contract_market_code="124608")]
    added, revised, rejected = ct.merge(s, good + bad)
    check(g, "valid rows stored", added == len(good), added)
    check(g, "futures+options, negative, fractional, identity-breaking and incomplete rows rejected",
          len(rejected) == 5, rejected)
    check(g, "reasons recorded", any("futures-only" in r for r in rejected)
          and any("open interest" in r for r in rejected))
    check(g, "an untracked contract is ignored, not rejected", "124608" not in s["contracts"])
    one_side = [dict(good[6], noncomm_positions_long_all=str(int(good[6]["noncomm_positions_long_all"]) + 1),
                     report_date_as_yyyy_mm_dd="2026-09-08T00:00:00.000"),
                dict(good[7], comm_positions_short_all=str(int(good[7]["comm_positions_short_all"]) + 1),
                     report_date_as_yyyy_mm_dd="2026-09-08T00:00:00.000")]
    _, _, rej2 = ct.merge(s, one_side)
    check(g, "each side of the open-interest identity is checked on its own",
          len(rej2) == 2 and "long side" in rej2[0] and "short side" in rej2[1], rej2)
    a2, r2, _ = ct.merge(s, good)
    check(g, "re-merging identical data adds and revises nothing", (a2, r2) == (0, 0))
    rev = [dict(good[0], noncomm_positions_long_all=str(int(good[0]["noncomm_positions_long_all"]) + 1),
                nonrept_positions_long_all=str(int(good[0]["nonrept_positions_long_all"]) - 1))]
    a3, r3, _ = ct.merge(s, rev)
    check(g, "a CFTC revision is counted and applied", (a3, r3) == (0, 1))
    old = ct.empty_store(); ct.merge(old, synth(weeks=60))
    ct.merge(old, synth(weeks=5))
    check(g, "a shorter later fetch never drops older weeks", len(old["contracts"]["067651"]["weeks"]) == 60)
    big = ct.empty_store(); ct.merge(big, synth(weeks=ct.KEEP_WEEKS + 7))
    check(g, f"history capped at {ct.KEEP_WEEKS} weeks", len(big["contracts"]["067651"]["weeks"]) == ct.KEEP_WEEKS)


def test_schedule():
    import cot_table as ct
    g = "release schedule"
    D = dt.date.fromisoformat
    check(g, "Tue 6 Oct 2026 -> Fri 9 Oct 15:30 EDT = 19:30 UTC",
          ct.release_at(D("2026-10-06")) == dt.datetime(2026, 10, 9, 19, 30, tzinfo=UTC))
    check(g, "Tue 3 Nov 2026 (after DST ends) -> 20:30 UTC",
          ct.release_at(D("2026-11-03")) == dt.datetime(2026, 11, 6, 20, 30, tzinfo=UTC))
    check(g, "Tue 3 Mar 2026 (before DST) -> 20:30 UTC; Tue 10 Mar (after) -> 19:30 UTC",
          ct.release_at(D("2026-03-03")).hour == 20 and ct.release_at(D("2026-03-10")).hour == 19)
    check(g, "a Monday holiday report still maps to that week's Friday",
          ct.release_at(D("2025-11-10")).date() == D("2025-11-14"))
    check(g, "next report after a Monday-dated one is the next Tuesday",
          ct.next_report_date(D("2025-11-10")) == D("2025-11-18"))
    s = store_of(fixture())
    s["status"] = {"report_date": "2026-10-06", "backfill_done": True,
                   "checked_at": "2026-10-11T09:00:00+00:00"}
    at = lambda *a: dt.datetime(*a, tzinfo=UTC)                                # noqa: E731
    check(g, "not due: no re-fetch within a day", ct.should_fetch(s, at(2026, 10, 11, 20))[0] is False)
    check(g, "not due: one re-check a day for revisions", ct.should_fetch(s, at(2026, 10, 12, 9, 1))[0] is True)
    s["status"]["checked_at"] = "2026-10-16T19:40:00+00:00"
    check(g, "due (Fri 16 Oct 19:30 UTC passed): poll again after 30 min",
          ct.should_fetch(s, at(2026, 10, 16, 20, 0))[0] is False and ct.should_fetch(s, at(2026, 10, 16, 20, 11))[0] is True)
    s["status"]["checked_at"] = "2026-10-18T10:00:00+00:00"
    check(g, "over a day late (holiday / shutdown): back off to 3 h",
          ct.should_fetch(s, at(2026, 10, 18, 12, 0))[0] is False and ct.should_fetch(s, at(2026, 10, 18, 13, 1))[0] is True)
    s["status"].update(checked_at="2026-10-11T09:00:00+00:00", last_error="x", last_error_at="2026-10-11T10:00:00+00:00")
    check(g, "after a failed fetch: retry in 30 min even when not due",
          ct.should_fetch(s, at(2026, 10, 11, 10, 31))[0] is True)
    check(g, "force always fetches", ct.should_fetch(s, at(2026, 10, 11, 10, 1), force=True)[0] is True)
    s["status"]["backfill_done"] = False
    check(g, "an un-backfilled store fetches", ct.should_fetch(s, at(2026, 10, 11, 10, 1))[0] is True)


def test_build():
    import cot_table as ct
    g = "build, cache, recovery"
    data = _tmp()
    calls = []

    def fetch_with(rows):
        def f(since):
            calls.append(since)
            return [r for r in rows if r["report_date_as_yyyy_mm_dd"][:10] >= since.isoformat()]
        return f

    def down(since):
        calls.append(since)
        raise OSError("CFTC unreachable")
    t0 = dt.datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    st = ct.build(data=data, fetch=fetch_with(fixture()), now=t0)["status"]
    f = data / ct.OUT_NAME
    check(g, "first build backfills once", calls[-1] == t0.date() - dt.timedelta(weeks=ct.BACKFILL_WEEKS)
          and st["backfill_done"] is True, calls)
    check(g, "first build writes the store", f.exists() and st["report_date"] == "2026-10-06")
    check(g, "no temp file left behind", not list(data.glob("*.tmp")))
    first_seen = st["report_first_seen_at"]
    ct.build(data=data, fetch=down, now=t0 + dt.timedelta(hours=3))
    check(g, "not due -> no fetch at all", len(calls) == 1)
    st = ct.build(data=data, fetch=fetch_with(fixture()), now=t0 + dt.timedelta(hours=25))["status"]
    check(g, "daily re-check fetches the routine window, not the backfill",
          len(calls) == 2 and calls[-1] == (t0 + dt.timedelta(hours=25)).date() - dt.timedelta(weeks=ct.FETCH_WEEKS))
    check(g, "re-fetching unchanged data does not make the report look new",
          st["report_first_seen_at"] == first_seen and st["checked_at"] != first_seen)
    before = json.loads(f.read_text())["contracts"]
    t1 = dt.datetime(2026, 10, 16, 20, 0, tzinfo=UTC)          # the next report is due
    st = ct.build(data=data, fetch=down, now=t1)["status"]
    check(g, "a failed fetch keeps every stored week", json.loads(f.read_text())["contracts"] == before)
    check(g, "the failure is recorded for the page",
          st["last_error"].startswith("OSError") and ct.status_view(json.loads(f.read_text()), t1)["failing"])
    new_week = [dict(r, report_date_as_yyyy_mm_dd="2026-10-13T00:00:00.000")
                for r in fixture() if r["report_date_as_yyyy_mm_dd"].startswith("2026-10-06")]
    st = ct.build(data=data, fetch=fetch_with(fixture() + new_week), now=t1 + dt.timedelta(minutes=31))["status"]
    check(g, "the new report is picked up and stamped first-seen",
          st["report_date"] == "2026-10-13" and st["report_first_seen_at"] == (t1 + dt.timedelta(minutes=31)).isoformat())
    check(g, "a success clears the error", st["last_error"] is None
          and not ct.status_view(json.loads(f.read_text()), t1)["failing"])
    # restart after a long outage: newest stored week 70 weeks back -> fetch reaches it
    gap = _tmp()
    s = ct.empty_store(); ct.merge(s, synth(weeks=10, end="2025-06-03"))
    s["status"] = {"report_date": "2025-06-03", "backfill_done": True, "checked_at": "2025-06-07T00:00:00+00:00"}
    ct._write(gap / ct.OUT_NAME, s)
    seen = []
    ct.build(data=gap, fetch=lambda since: (seen.append(since), [])[1], now=t0)
    check(g, "after a long outage the fetch window widens to cover the missed weeks",
          seen and seen[0] <= dt.date(2025, 6, 3), seen)
    (gap / ct.OUT_NAME).write_text("{corrupt")
    st = ct.build(data=gap, fetch=fetch_with(fixture()), now=t0)["status"]
    check(g, "an unreadable store is rebuilt from a fresh backfill", st["report_date"] == "2026-10-06")
    empty = _tmp()
    try:
        ct.build(data=empty, fetch=down, now=t0)
        check(g, "no store and no CFTC raises (run.py reports it)", False)
    except OSError:
        check(g, "no store and no CFTC raises (run.py reports it)", True)
    check(g, "...and writes nothing", not any(empty.iterdir()))
    lim = _tmp()
    try:
        ct.build(data=lim, fetch=lambda since: [{}] * ct.ROW_LIMIT, now=t0)
        check(g, "a response at the row limit is refused as possibly truncated", False)
    except RuntimeError:
        check(g, "a response at the row limit is refused as possibly truncated", True)
    sv = ct.status_view({"status": {"report_date": "2026-10-06"}}, dt.datetime(2026, 10, 18, 12, tzinfo=UTC))
    check(g, "a report over a day past its due time is flagged overdue",
          sv["overdue"] and sv["next_report"] == "2026-10-13")
    sv = ct.status_view({"status": {"report_date": "2026-10-06"}}, dt.datetime(2026, 10, 16, 21, tzinfo=UTC))
    check(g, "...but not in the first hours after the scheduled release", not sv["overdue"])


def test_report():
    import cot_table as ct
    g = "50-week report"
    s = ct.empty_store()
    ct.merge(s, synth(weeks=220))
    rep = ct.report(s, "067651", False, "2026-10-06")
    rows = rep["rows"]
    check(g, "exactly 50 reports, newest first", len(rows) == 50 and rows[0]["date"] == "2026-10-06"
          and all(rows[i]["date"] > rows[i + 1]["date"] for i in range(49)))
    check(g, "the oldest shown week has a genuine previous-week comparison",
          rep["oldest_has_prev"] and rows[-1]["prev_diff"] is not None)
    check(g, "no shortfall with enough history", rep["shortfall"] == 0 and rep["missing_weeks"] == 0)
    check(g, "age counts weeks back from the latest", [r["age"] for r in rows[:3]] == [0, 1, 2])
    short = ct.empty_store(); ct.merge(short, synth(weeks=30))
    r2 = ct.report(short, "067651", False, "2026-10-06")
    check(g, "a shortfall is reported, not padded", len(r2["rows"]) == 30 and r2["shortfall"] == 20
          and not r2["oldest_has_prev"])
    holes = [r for r in synth(weeks=60) if r["report_date_as_yyyy_mm_dd"][:10] not in ("2026-09-01", "2026-09-08")]
    hs = ct.empty_store(); ct.merge(hs, holes)
    r3 = ct.report(hs, "067651", False, "2026-10-06")
    check(g, "missing weeks are never invented", "2026-09-01" not in [w["date"] for w in r3["rows"]])
    wk = next(w for w in r3["rows"] if w["date"] == "2026-09-15")
    check(g, "a gap is marked on the week after it and counted",
          wk.get("missing_before") == 2 and r3["missing_weeks"] == 2 and wk["prev_date"] == "2026-08-25")


def test_positioning():
    import cot_table as ct
    g = "positioning shading"
    win = list(range(1, 157))
    check(g, "mid-rank percentile", abs(ct.percentile_rank(win, 156) - 99.68) < 0.01
          and abs(ct.percentile_rank(win, 78) - 49.68) < 0.01)
    lo, mid, hi, top = (ct.positioning(win, x) for x in (10, 80, 140, 156))
    check(g, "continuous: low faint, middling light, high strong, extreme darkest",
          0 < lo["strength"] < 0.01 and 0.2 < mid["strength"] < 0.3
          and 0.75 < hi["strength"] < 0.85 and top["strength"] > 0.99)
    check(g, "strength rises smoothly with the percentile (no step)",
          all(b["strength"] - a["strength"] < 0.02
              for a, b in zip(*[[ct.positioning(win, x) for x in xs] for xs in (range(1, 156), range(2, 157))])))
    check(g, "band labels", (lo["state"], mid["state"], hi["state"], top["state"]) == ("low", "moderate", "high", "extreme"))
    check(g, "flat range -> no colour, says so", ct.positioning([5] * 156, 5)["state"] == "flat"
          and ct.positioning([5] * 156, 5)["strength"] == 0)
    check(g, "short history -> not ranked", ct.positioning(list(range(40)), 39)["state"] == "short-history")
    check(g, "missing value -> no colour", ct.positioning(win, None)["state"] == "no-data")
    check(g, "age fade: latest full, each week x0.75 (100, 75, 56, 42 ...)",
          ct.age_fade(0) == 1 and ct.age_fade(1) == 0.75 and abs(ct.age_fade(3) - 0.421875) < 1e-9)
    check(g, "age fade never drops below the 15% floor (reached at 7 weeks)",
          ct.age_fade(6) > 0.15 and ct.age_fade(7) == 0.15 and ct.age_fade(49) == 0.15)
    ext, low = ct.positioning(win, 156), ct.positioning(win, 10)
    check(g, "an old extreme stays visibly tinted; an old low reading stays plain",
          ext["strength"] * ct.age_fade(40) > 0.14 and low["strength"] * ct.age_fade(40) < 0.005)
    # longs and shorts against their own distributions: NC long at its 3y high while NC
    # short sits at its 3y low in the same week
    s = ct.empty_store()
    ct.merge(s, synth(weeks=220, f=lambda i: (100000 + i * 10, 300000 - i * 10, 400000, 400000)))
    p0 = ct.report(s, "067651", False, "2026-10-06")["rows"][0]["pos"]
    check(g, "each column against its own history: long at its high -> extreme, short at its low -> low",
          p0["nc_long"]["state"] == "extreme" and p0["nc_short"]["state"] == "low", p0)
    check(g, "a constant series (Comm here) is flat, not coloured", p0["comm_long"]["state"] == "flat")
    # point in time: a week is ranked against the 156 reports up to it, not later ones
    rows = ct.report(s, "067651", False, "2026-10-06")["rows"]
    check(g, "point in time: every week of a rising series is at its own high then",
          all(r["pos"]["nc_long"]["state"] == "extreme" for r in rows))
    # inverse pair: USDJPY "Long" = yen futures short, ranked as such
    j = ct.empty_store()
    ct.merge(j, synth(code="097741", weeks=220, f=lambda i: (50000, 10000 + i * 100, 200000, 200000)))
    pj = ct.report(j, "097741", True, "2026-10-06")["rows"][0]
    check(g, "inverse pair: USDJPY Long (= yen short) shades as the long, consistently",
          pj["nc"]["long"] == 10000 + 219 * 100 and pj["pos"]["nc_long"]["state"] == "extreme"
          and pj["pos"]["nc_short"]["state"] == "flat")


def test_render():
    import cot_table as ct, build_dashboard as bd
    g = "render"
    data = _tmp()
    real = bd.DATA
    try:
        bd.DATA = data
        check(g, "no store -> a note, not an error", "Not built yet" in bd.cot_report_tables())
        check(g, "no store and no history -> no layers, no error", bd.cot_drilldowns() == "")
        s = ct.empty_store()
        ct.merge(s, fixture())
        oil = []
        for part in _OIL13.split(";"):
            d, ncl, ncs, cl, cs = part.split(",")
            d = f"20{d[:2]}-{d[2:4]}-{d[4:]}"
            oil.append(_raw(d, "067651", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
                            max(int(ncl) + int(cl), int(ncs) + int(cs)) + 1000,
                            int(ncl), int(ncs), 0, int(cl), int(cs)))
        ct.merge(s, oil)
        s["status"] = {"report_date": "2026-10-06", "prev_date": "2026-09-29",
                       "checked_at": "2026-10-11T12:00:00+00:00",
                       "report_first_seen_at": "2026-10-09T20:00:00+00:00", "backfill_done": True}
        ct._write(data / ct.OUT_NAME, s)
        now = dt.datetime(2026, 10, 11, 12, tzinfo=UTC)
        html = bd.cot_report_tables(now=now)
        check(g, "machine-readable report marker for the Pi checker", 'data-cot-report="2026-10-06"' in html)
        check(g, "both titles + report date", ct.FX_TITLE in html and ct.CM_TITLE.replace("&", "&amp;") in html
              and "October 6, 2026" in html)
        check(g, "grouped headers, clear Total / Net Difference labels",
              "Hedge Funds, Banks &amp; Institutions" in html and "Corporations Hedging" in html
              and "Total (L+S)" in html and "Net Difference" in html and "not open interest" in html
              and "combined net position" in html)
        check(g, "16 rows, each linking to its 50-week report",
              len(re.findall(r'<td class="lbl"><a href="#cot50-', html)) == 16)
        check(g, "reversal footnote + futures-proxy and USD explanations",
              ct.REVERSED_NOTE.replace("&", "&amp;") in html and "proxies for the spot pairs" in html
              and "ICE US Dollar Index future" in html)
        check(g, "the exact contract is shown for every row",
              all(f"({c})" in html for c in ct.ALL_CODES) and "DJIA x $5" in html)
        check(g, "source and freshness line", "6aqww" not in html and "dataset 6dca-aqww" in html
              and "first fetched" in html and "last checked" in html)
        check(g, "no '+' signs on numbers", ">+" not in html)
        check(g, "nothing flagged when current", "chip warn" not in html and "chip neg" not in html)
        late = bd.cot_report_tables(now=dt.datetime(2026, 10, 18, 12, tzinfo=UTC))
        check(g, "an overdue report is flagged with its due time", "not published yet" in late)
        s["status"].update(last_error="OSError: down", last_error_at="2026-10-11T13:00:00+00:00")
        ct._write(data / ct.OUT_NAME, s)
        check(g, "a failing fetch is flagged, last good report still shown",
              "CFTC fetch failing" in bd.cot_report_tables(now=now) and "October 6, 2026" in bd.cot_report_tables(now=now))
        z = ct.empty_store()
        v = lambda d, a, b, c, e: _raw(d, "088691", "GOLD", 10**6, a, b, 0, c, e)  # noqa: E731
        ct.merge(z, fixture() + [v("2026-09-29", 10, 10, 10, 10)])
        ct._write(data / ct.OUT_NAME, z)
        check(g, "zero previous difference renders N/A", '<td class="mut">N/A</td>' in bd.cot_report_tables(now=now))
        ct._write(data / ct.OUT_NAME, s)

        lay = bd.cot_drilldowns()
        ids = re.findall(r'id="(cot50-[^"]+)"', lay)
        check(g, "one layer per instrument, no duplicate ids", len(ids) == len(set(ids)) == 16, ids)
        m = re.search(r'id="cot50-WTI".*?(?=<div class="cotmodal|$)', lay, re.S)
        wti = m.group(0) if m else ""
        check(g, "oil report: Non-Commercial, Commercial and Net Difference tables",
              "WTI Oil Non-Commercial" in wti and "WTI Oil Commercial" in wti and "WTI Oil Net Difference" in wti)
        check(g, "columns as the oil sheet", all(c in wti for c in (
            ">Date<", ">Long<", ">Short<", ">Total (L+S)<", ">% Long<", ">% Short<", ">Net Positions<")))
        avg = re.search(r'<tr class="ref av">(.*?)</tr>', wti).group(1)
        check(g, "13-wk Avg row reproduces the oil sheet (331,333 / 217,973 / 549,306 / 60% / 40% / 113,360)",
              re.findall(r"<td>([^<]*)</td>", avg) == ["331,333", "217,973", "549,306", "60%", "40%", "113,360"], avg)
        mx = re.findall(r"<td>([^<]*)</td>", re.search(r'<tr class="ref mx">(.*?)</tr>', wti).group(1))
        check(g, "Max row = highest of the reports shown (here the 13 on file)",
              mx[0] == "371,202" and mx[1] == "251,888", mx)
        check(g, "reference rows labelled with their periods and never faded",
              "Max. (13 wks)" in wti and "Min. (13 wks)" in wti and "13-wk Avg" in wti
              and not re.search(r'<tr class="ref[^"]*">[^\n]*?--a:', wti))
        check(g, "full dates with year", "October 6, 2026" in wti and "July 14, 2026" in wti)
        check(g, "a not-yet-ranked figure still answers on hover / tap",
              'tabindex="0"' in wti and "not ranked" in wti)
        flat = " ".join(wti.split())
        check(g, "legend wording and not-a-signal note",
              "<b>Red</b> = unusually high long positioning" in flat and "<b>blue</b> = unusually high short positioning" in flat
              and "<b>faded colour</b> = older report" in flat and "<b>not</b> buy/sell signals" in flat)
        check(g, "legend separates the 50-report Max/Min from the 3-year colour window",
              "The <b>Max / Min</b> rows cover the 50 reports shown" in flat and "3-year window" in flat
              and "last 156 reports" in flat)
        check(g, "legend spells out the fade as x0.75 per week",
              "multiplies the colour strength by 0.75" in flat and "100% &rarr; 75% &rarr; 56% &rarr; 42%" in flat
              and "never below 15%" in flat)
        check(g, "a Show-percentiles toggle and a percentile label in every Long/Short cell",
              'class="crpct"' in wti and "Show percentiles" in wti
              and wti.count('<span class="pp">') == wti.count('<td class="x'))
        check(g, "net chart present", "<svg" in wti and "Non-Commercial net" in wti)
        check(g, "shortfall shown, not padded", "Only 13 of 50 weekly reports" in wti)
        check(g, "contract, source, latest date and direction shown",
              "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE" in wti and "6dca-aqww" in wti
              and "Latest report" in wti and "Direction" in wti)
        jpy = re.search(r'id="cot50-JPY".*?(?=<div class="cotmodal|$)', lay, re.S).group(0)
        check(g, "inverse pair report explains the swap", "swapped in <b>both</b> groups" in jpy)
        # a ranked cell: colour strength = percentile strength x age fade, true pct in the tip
        big = _tmp(); bd.DATA = big
        q = ct.empty_store(); ct.merge(q, synth(weeks=220, f=lambda i: (100000 + i * 10, 300000 - i * 10, 400000 + (i % 7), 400000 + (i % 5))))
        ct.merge(q, [r for r in fixture() if r["cftc_contract_market_code"] != "067651"])
        q["status"] = {"report_date": "2026-10-06", "checked_at": "2026-10-11T12:00:00+00:00"}
        ct._write(big / ct.OUT_NAME, q)
        oilq = re.search(r'id="cot50-WTI".*?(?=<div class="cotmodal|$)', bd.cot_drilldowns(), re.S).group(0)
        cells = re.findall(r'<td class="x xl[^"]*" tabindex="0"(?: style="--a:([0-9.]+)")? title="([^"]*)"', oilq)
        a0, a1, a2 = (float(c[0] or 0) for c in cells[:3])
        check(g, "latest long at its high: full colour; then faded week by week",
              a0 > 0.99 and abs(a1 - a0 * 0.75) < 0.02 and abs(a2 - a0 * 0.5625) < 0.02, cells[:3])
        check(g, "the tooltip keeps the true (unfaded) percentile on an older week",
              "99th percentile" in cells[2][1] and "extreme" in cells[2][1] and "faded: 2 weeks old" in cells[2][1], cells[2][1])
        check(g, "Long shades red (xl), Short blue (xs)", 'class="x xl' in oilq and 'class="x xs' in oilq)
    finally:
        bd.DATA = real


def test_live_data_untouched():
    from config import DATA
    g = "isolation"
    now = {p.name: p.stat().st_mtime for p in DATA.glob("cot_*.json")}
    check(g, "live data/cot_*.json untouched", now == _LIVE_BEFORE, f"before {_LIVE_BEFORE} after {now}")


# ======================================================================================
if __name__ == "__main__":
    from config import DATA as _D
    _LIVE_BEFORE = {p.name: p.stat().st_mtime for p in _D.glob("cot_*.json")}
    for fn in (test_matches_published_table, test_reversal, test_contracts, test_weeks,
               test_arithmetic, test_validation, test_schedule, test_build, test_report,
               test_positioning, test_render, test_live_data_untouched):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append((fn.__name__, "raised", f"{type(e).__name__}: {e}"))
            print(f"  ERROR [{fn.__name__}] {type(e).__name__}: {e}")
            traceback.print_exc()
    groups = {}
    for g, n, _ in PASS:
        groups.setdefault(g, [0, 0])[0] += 1
    for g, n, _ in FAIL:
        groups.setdefault(g, [0, 0])[1] += 1
    print()
    for g, (ok, bad) in groups.items():
        print(f"{g}: {ok}/{ok + bad} passed" + (f"  ({bad} FAILED)" if bad else ""))
    print()
    if FAIL:
        print(f"{len(FAIL)} CHECK(S) FAILED")
        sys.exit(1)
    print(f"ALL PASS ({len(PASS)} checks)")
