"""check_build.check_oi follows the daily CME Open interest tab (oi.py).

    python test_check_build_oi.py

Stdlib only, offline. Renders fixture HTML the way oi.render does (no network, no writes
to engine/data) and checks the five assertions that used to require the weekly CFTC
futures-equivalent section. Options columns must pass while oi_opt is absent, including
a table that only says 'no data yet', and must match stored values once oi_opt exists.
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_build                                                      # noqa: E402
import oi                                                                # noqa: E402

FAILS = []
OLD = (
    "CFTC weekly",
    "Options FE OI",
    "Combined OI",
    "Options share",
    "not the number of outstanding option contracts",
    "CFTC archive",
)
FIVE = (
    check_build._OI_SECTION,
    check_build._OI_LABEL,
    check_build._OI_COLUMNS,
    check_build._OI_METHOD,
    check_build._OI_VALUES,
)


def check(name, cond):
    print(("ok  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILS.append(name)


def run(page, data_dir):
    check_build.OKS.clear()
    check_build.FAILS.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        check_build.check_oi(page, Path(data_dir))
    return list(check_build.OKS), list(check_build.FAILS)


def wrap(body):
    return ('<div class="panel" id="p-rtl">retail</div>'
            f'<div class="panel" id="p-oi">{body}</div>'
            '<div class="panel" id="p-yld">rates</div>')


def archive(hist):
    d = Path(tempfile.mkdtemp())
    (d / "oi_history.json").write_text(json.dumps(hist), encoding="utf-8")
    return d


def render(hist):
    last = max(hist) if hist else None
    return oi.render(hist, {"last_good_date": last}, {}, {})


SHELL = (
    '<h2>Open Interest &mdash; the daily bias layer</h2>'
    '<p>Open interest is how many futures contracts are still open.</p>'
    '<div class="oi-hist"><h2>Daily open interest &mdash; last 7 days, day by day</h2>'
    '<p>OI is CME preliminary open interest for that trade date, published overnight.</p>'
)


def eur_table(opt_header, opt_cell):
    opt_h = opt_header or ""
    return (
        SHELL +
        "<h3>EUR<small>1 day &middot; net +1,000 contracts</small></h3>"
        "<table class='oi-tw'><thead><tr><th>Date</th><th>Price</th>"
        "<th><span class='f'>Fut \u0394OI</span><span class='s'>\u0394Fut</span></th>"
        "<th class='vol'><span class='f'>Futures OI</span><span class='s'>Fut OI</span></th>"
        f"{opt_h}<th>Read</th></tr></thead><tbody><tr>"
        "<td class='oi-day'><span class='f'>Thu 01 Oct</span></td>"
        "<td class='na'>&mdash;</td>"
        "<td class='up'><span class='f'>+1,000</span></td>"
        "<td class='vol'><span class='f'>700,000</span><span class='s'>700k</span></td>"
        f"{opt_cell}<td class='st na'>&mdash;</td></tr></tbody></table></div>"
    )


OPT_HEADER = ("<th><span class='f'>Opt \u0394OI</span><span class='s'>\u0394Opt</span></th>"
              "<th class='vol'><span class='f'>Options OI</span><span class='s'>Opt OI</span></th>")


def t_real_archive_empty_options():
    """The committed archive stores VOI options and the rendered tab shows them."""
    data = Path(__file__).parent / "data"
    hist = json.loads((data / "oi_history.json").read_text(encoding="utf-8"))
    body = render(hist)
    check("committed archive stores options", "oi_opt" in json.dumps(hist))
    for inst in oi.ORDER:
        for day in ("2026-11-06", "2026-11-09"):
            cell = hist[day][inst]
            check(f"{day} {inst} keeps futures and stores options",
                  isinstance(cell.get("oi"), int) and cell.get("chg") is not None
                  and isinstance(cell.get("oi_opt"), int)
                  and cell.get("oi_opt_calls", 0) + cell.get("oi_opt_puts", 0) == cell["oi_opt"]
                  and cell.get("oi_opt_status") == "PRELIMINARY"
                  and cell.get("oi_opt_basis") == oi.VOI_OPT_BASIS)
    check("6 Nov is the first options day, so its change is absent",
          all(hist["2026-11-06"][inst].get("chg_opt") is None for inst in oi.ORDER))
    changes_ok = True
    for inst in oi.ORDER:
        prev = hist["2026-11-06"][inst]
        cur = hist["2026-11-09"][inst]
        if ("chg_opt" not in cur or not isinstance(cur.get("oi_opt"), int)
                or not isinstance(prev.get("oi_opt"), int)):
            changes_ok = False
            break
        if cur["chg_opt"] != cur["oi_opt"] - prev["oi_opt"]:
            changes_ok = False
            break
    check("9 Nov change is the difference against 6 Nov, including a real zero",
          changes_ok and hist["2026-11-09"]["NZD"].get("chg_opt") == 0)
    gold = hist["2026-11-09"]["Gold"]
    check("rendered tab shows the gold options split",
          f"{gold['oi_opt']:,}" in body and f"{gold['oi_opt_calls']:,}" in body
          and f"{gold['oi_opt_puts']:,}" in body)
    for old in ("CFTC weekly", "Options FE OI", "Combined OI", "Options share",
                "not the number of outstanding option contracts"):
        check(f"rendered tab has no {old!r}", old not in body)
    check("rendered tab shows futures and options, labeled monthly standard",
          "Futures OI" in body and "Options OI" in body and "Calls" in body and "Puts" in body
          and "PRELIMINARY" in body
          and "raw contract counts (calls + puts)" in body
          and "monthly standard options only" in body
          and "2026-11-09" in body)
    oks, fails = run(wrap(body), data)
    check("real archive: no failures", not fails)
    if fails:
        print("       " + "; ".join(fails))
    for name in FIVE:
        check(f"real archive passes: {name}", name in oks)
    for old in OLD:
        check(f"real archive does not grade {old!r}",
              all(old not in msg for msg in oks + fails))


def t_rendered_options_values():
    hist = {
        "2026-10-01": {"EUR": {"oi": 700000, "chg": 1000,
                               "oi_opt": 120000, "chg_opt": 200}},
        "2026-10-02": {"EUR": {"oi": 701000, "chg": 1000, "oi_opt": None}},
    }
    data = archive(hist)
    oks, fails = run(wrap(render(hist)), data)
    check("options fixture: no failures", not fails)
    if fails:
        print("       " + "; ".join(fails))
    for name in FIVE:
        check(f"options fixture passes: {name}", name in oks)

    # The snapshot and the daily table both print the level and the header.
    # Corrupting only the first copy would leave the table check_oi reads intact.
    broken = render(hist).replace("120,000", "999,999")
    oks, fails = run(wrap(broken), data)
    check("wrong options level fails the archive check", check_build._OI_VALUES in fails)
    check("wrong options level still recognises the section", check_build._OI_SECTION in oks)

    no_header = render(hist).replace("Options OI", "Open interest").replace("Opt OI", "OI")
    oks, fails = run(wrap(no_header), data)
    check("missing Options OI header fails the column check", check_build._OI_COLUMNS in fails)


def t_options_no_data_yet_passes():
    hist = {"2026-10-01": {"EUR": {"oi": 700000, "chg": 1000}}}
    data = archive(hist)
    page = wrap(eur_table(
        OPT_HEADER,
        "<td class='na'>no data yet</td><td class='na'>no data yet</td>"))
    oks, fails = run(page, data)
    check("options 'no data yet' with empty oi_opt: no failures", not fails)
    if fails:
        print("       " + "; ".join(fails))
    check("options 'no data yet' passes the column check", check_build._OI_COLUMNS in oks)

    omitted = wrap(eur_table("", ""))
    oks, fails = run(omitted, data)
    check("omitted options columns with empty oi_opt: no failures", not fails)
    if fails:
        print("       " + "; ".join(fails))

    phantom = wrap(eur_table(
        OPT_HEADER,
        "<td class='up'><span class='f'>+10</span></td>"
        "<td class='vol'><span class='f'>120,000</span></td>"))
    oks, fails = run(phantom, data)
    check("options number with no stored oi_opt fails the column check",
          check_build._OI_COLUMNS in fails)


def t_futures_cell_not_a_substring():
    """A futures level that only appears outside its cell must not satisfy the archive check."""
    hist = {"2026-10-01": {"EUR": {"oi": 700000, "chg": 1000}}}
    data = archive(hist)
    page = wrap(eur_table("", "").replace(
        "<span class='f'>700,000</span>",
        "<span class='f'>1</span><span class='note'>700,000</span>",
        1))
    oks, fails = run(page, data)
    check("futures level outside the cell fails the archive check",
          check_build._OI_VALUES in fails)
    check("futures level outside the cell is not a pass", check_build._OI_VALUES not in oks)


def t_old_cftc_section_fails():
    hist = {"2026-10-01": {"EUR": {"oi": 700000, "chg": 1000}}}
    data = archive(hist)
    old = (
        '<h2>Open interest <span class="mut">&mdash; CFTC weekly</span></h2>'
        '<table><tr><th>Futures OI</th><th>Options FE OI</th>'
        '<th>Combined OI</th><th>Options share</th></tr>'
        '<tr><td>700,000</td><td>10</td><td>700,010</td><td>0.1%</td></tr></table>'
        '<p>not the number of outstanding option contracts</p>'
    )
    oks, fails = run(wrap(old), data)
    check("CFTC weekly section does not pass the daily CME checks", bool(fails))
    check("CFTC weekly section fails the label check", check_build._OI_LABEL in fails)
    check("CFTC weekly wording is not itself a passing check",
          all("CFTC weekly" not in msg for msg in oks))


def t_empty_archive():
    data = archive({})
    oks, fails = run(wrap('<p style="padding:18px;opacity:.5">Daily OI data not available yet.</p>'), data)
    check("empty archive + unavailable copy: no failures", not fails)
    if fails:
        print("       " + "; ".join(fails))
    check("empty archive does not require the five populated-tab checks",
          all(name not in oks for name in FIVE))

    oks, fails = run(wrap("<p>No CFTC open-interest data on file yet.</p>"), data)
    check("empty archive + old CFTC copy fails",
          "OI: no daily CME history -> tab says unavailable" in fails)

    missing = Path(tempfile.mkdtemp())
    oks, fails = run(wrap(oi.render({}, {"last_good_date": None}, {}, {})), missing)
    check("missing archive + renderer's no-data banner: no failures", not fails)
    if fails:
        print("       " + "; ".join(fails))


def t_no_panel():
    data = archive({})
    oks, fails = run("<div>no open interest tab</div>", data)
    check("missing panel fails", fails == ["OI: Open interest tab present (id=p-oi)"])
    check("missing panel records the failure only", oks == [])


def main():
    for fn in (t_real_archive_empty_options, t_rendered_options_values,
               t_options_no_data_yet_passes, t_futures_cell_not_a_substring,
               t_old_cftc_section_fails, t_empty_archive, t_no_panel):
        fn()
    print(f"\n{'FAILED' if FAILS else 'PASSED'}: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
