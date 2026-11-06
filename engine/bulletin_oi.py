"""Import raw options open interest from a user-supplied CME daily bulletin.

The bulletin is a file the user already has (PDF via `pdftotext -layout`, or the
layout text itself). This module does not fetch, scrape, or open a network
connection. It reads printed contract counts: calls plus puts on the standard
full-size future, monthly and weekly series. Micros, minis, average-price
options, calendar-spread options and cross rates are left out. There is no
futures-equivalent or delta adjustment.

Energy code 6J is a natural-gas option, never JPY.

Futures cells already stored for that date are not rewritten. A final bulletin
often revises the preliminary futures open interest; those two figures are
reported side by side and only the options fields are written. The first options
observation has no day-change. A later one is differenced only against the
nearest earlier options reading that used this same basis.

    python bulletin_oi.py report a.layout.txt b.layout.txt
    python bulletin_oi.py report section.pdf          # needs pdftotext on PATH
    python bulletin_oi.py apply --data data section.pdf
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASIS = "bulletin-final:standard-full-size-monthly-and-weekly-option-contracts"
SOURCE = "cme-daily-bulletin-local-file"

# Standard futures row in the futures section, for the revision comparison only.
FUTURES_CODE = {
    "GC": "Gold", "SI": "Silver", "CL": "Oil",
    "EC": "EUR", "BP": "GBP", "JY": "JPY", "AD": "AUD",
    "NE": "NZD", "CD": "CAD", "SF": "CHF",
}

_MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def layout_text(path):
    """Layout text from a .txt file, or from `pdftotext -layout` for a PDF.

    No download. A missing pdftotext is an error that names the local tool.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() == ".txt":
        return p.read_text(encoding="utf-8", errors="replace")
    tool = shutil.which("pdftotext")
    if not tool:
        raise RuntimeError(
            "pdftotext is not on PATH. Install poppler (the `pdftotext` command) "
            "and re-run, or pass the `pdftotext -layout` text as a .txt file. "
            "Nothing is downloaded.")
    proc = subprocess.run([tool, "-layout", str(p), "-"],
                          check=False, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[:200]
        raise RuntimeError(f"pdftotext failed on {p.name}: {err}")
    return proc.stdout.decode("utf-8", errors="replace")


def _pages(text):
    return re.split(r"\f", text)


def _page_kind(page):
    head = page[:2000].upper()
    banner = page.lstrip()[:3]
    asset = {"01B": "fx", "02B": "metals", "02C": "energy"}.get(banner)
    if "OPTIONS ON" in head:
        if "FX" in head:
            return "fx"
        if "METAL" in head:
            return "metals"
        if "ENERGY" in head:
            return "energy"
        if asset:
            return asset
    # A later side of the options section can still be banner-titled as futures
    # while the body is product option rows. Summary lines on a futures page
    # mention options only a handful of times; a real options page has many.
    opt_rows = sum(1 for ln in page.splitlines()
                   if re.search(r"\bOPTIONS?\b", ln.upper())
                   and not ln.strip().upper().startswith("EXCHANGE"))
    if asset and opt_rows >= 8:
        return asset
    if "FUTURES" in head and asset:
        return asset + "-futures"
    return None


def _header(page):
    final = "FINAL" in page[:1500].upper()
    bullet = re.search(r"BULLETIN\s*#\s*(\d+)", page[:1500], re.I)
    date = re.search(r"[A-Z][a-z]{2},\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})", page[:1500])
    iso = None
    if date and date.group(1) in _MONTHS:
        iso = f"{int(date.group(3)):04d}-{_MONTHS[date.group(1)]:02d}-{int(date.group(2)):02d}"
    return {"final": final, "bulletin": bullet.group(1) if bullet else None, "trade_date": iso}


def _oi(line):
    """Open interest is the number in the combined-total OI column, just left of the sign.

    A row with no sign can still print a level in that column (no trade, so no
    change glyph). A number further right is the year-ago column and is ignored.
    """
    sign_at = None
    for j, ch in enumerate(line):
        if ch in "+-" and 100 <= j <= 116 and (j == 0 or line[j - 1] == " ") \
                and (j + 1 >= len(line) or line[j + 1] == " "):
            sign_at = j
            break
    nums = list(re.finditer(r"\d+", line))
    if sign_at is not None:
        cand = [m for m in nums if m.end() <= sign_at and m.start() >= 96]
        if not cand:
            return None
        return int(cand[-1].group())
    cand = [m for m in nums if 96 <= m.start() <= 112]
    if len(cand) == 1:
        return int(cand[0].group())
    return None


def _side(line):
    window = line[36:54] if len(line) > 36 else ""
    m = re.search(r"(?<![A-Z0-9])([CP])(?![A-Z0-9])", window)
    return m.group(1) if m else None


def _label(line):
    head = line[:40]
    st = head.strip()
    if line.strip().startswith("TOTAL"):
        body = re.sub(r"^\s+", "", line.strip()[5:])
        name = re.split(r"\s{2,}\d", body, maxsplit=1)[0]
        name = re.sub(r"\s+", " ", name).strip()
        return None, name
    m = re.match(r"([A-Z0-9]{1,5})\s+(.*)$", st)
    if not m:
        return None, None
    name = re.sub(r"\s+", " ", m.group(2))
    name = re.sub(r"\s+[CP]$", "", name).strip()
    return m.group(1), name


def _rows(page):
    out = []
    for line in page.splitlines():
        if not re.search(r"\bOPT(IONS?)?\b", line.upper()) and not line.strip().startswith("TOTAL"):
            # futures comparison rows have no OPTION
            code, name = _label(line)
            if code in FUTURES_CODE and name and "OPTION" not in name.upper():
                oi = _oi(line)
                if oi is not None:
                    out.append({"kind": "future", "code": code, "name": name, "oi": oi})
            continue
        if line.strip().startswith(("EXCHANGE", "VOLUME", "FORWARD", "OTC")):
            continue
        code, name = _label(line)
        if not name or not re.search(r"\bOPT(IONS?)?\b", name.upper()):
            continue
        side = _side(line)
        kind = "total" if line.strip().startswith("TOTAL") else "side"
        out.append({"kind": kind, "code": code, "name": name, "side": side, "oi": _oi(line)})
    return out


def _exclude(name, code, section):
    n = " ".join(name.upper().split())
    if code == "6J" or "NATURAL GAS" in n:
        return "natural gas (6J is not JPY)"
    if any(w in n for w in ("MICRO", "E-MINI", "EMINI", "MINI ")):
        return "mini or micro"
    if any(w in n for w in ("APO", "AVERAGE PRICE", "CSO", "CALENDAR STR", "CAL SPD",
                            "CAL SPRD", "EVENT CONTRACT")):
        return "not the standard futures option"
    if "(EURO)" in n or "EURO STYLE" in n or "EURO OPTS" in n:
        return "euro-style exercise, separate from the standard American option"
    if any(w in n for w in ("EUR/CHF", "EUR/GBP", "EUR/JPY", "CROSS")):
        return "cross rate"
    return None


def _market(section, code, name):
    why = _exclude(name, code, section)
    if why:
        return None, why
    n = " ".join(name.upper().split())
    if section == "fx":
        if "AUD/USD" in n or n.startswith("AUSTRALIAN DLR"):
            return "AUD", None
        if "GBP/USD" in n or n.startswith("BRITISH POUND"):
            return "GBP", None
        if "EUR/USD" in n or n.startswith("EURO FX"):
            return "EUR", None
        if "JPY/USD" in n or n.startswith("JAPANESE YEN"):
            return "JPY", None
        if "CAD/USD" in n or n.startswith("CANADIAN DOLLAR"):
            return "CAD", None
        if "CHF/USD" in n or n.startswith("SWISS FRANC"):
            return "CHF", None
        if "NZD/USD" in n or n.startswith("NEW ZEALAND"):
            return "NZD", None
    elif section == "metals":
        if "GOLD" in n and "SILVER" not in n:
            return "Gold", None
        if "SILVER" in n:
            return "Silver", None
    elif section == "energy":
        if "CRUDE" in n and "BRENT" not in n:
            return "Oil", None
    return None, None


def parse_layouts(texts):
    """Parse one or more layout-text bulletins into a report dict.

    Markets whose families do not reconcile (call + put != total, or the same
    code printed twice with two different totals) are `unavailable` with a
    reason. They are not stored as zero.
    """
    headers, futures = [], {}
    by_code = {}

    def take(slot):
        if not slot or not slot.get("code"):
            return
        code = slot["code"]
        prev = by_code.get(code)
        if prev is None:
            by_code[code] = slot
            return
        # The same code is printed again under a second name. Same figures are one
        # family. Different figures cannot be added.
        for key in ("calls", "puts", "total"):
            if prev.get(key) is not None and slot.get(key) is not None and prev[key] != slot[key]:
                prev["conflict"] = True
        for key in ("calls", "puts", "total", "section"):
            if prev.get(key) is None and slot.get(key) is not None:
                prev[key] = slot[key]

    pending = None
    for text in texts:
        take(pending)
        pending = None
        current = None
        for page in _pages(text):
            kind = _page_kind(page)
            if kind:
                current = kind
            elif current in ("fx", "metals", "energy") and "OPTION" in page.upper():
                # Later sides of one bulletin section repeat the date and FINAL
                # line but not the "OPTIONS ON FUTURES" title.
                kind = current
            else:
                current = kind
            if not kind:
                continue
            headers.append(_header(page))
            section = kind.split("-")[0]
            if kind.endswith("-futures"):
                take(pending)
                pending = None
            for row in _rows(page):
                if row["kind"] == "future":
                    futures.setdefault(row["code"], row)
                    continue
                if kind not in ("fx", "metals", "energy"):
                    continue
                if row["kind"] == "side":
                    if pending and pending["code"] != row.get("code"):
                        take(pending)
                        pending = None
                    if pending is None:
                        pending = {"code": row.get("code"), "name": row["name"], "section": section,
                                   "calls": None, "puts": None, "total": None}
                    if row.get("side") == "C":
                        pending["calls"] = row["oi"] if pending["calls"] in (None, row["oi"]) else pending["calls"]
                        if pending["calls"] != row["oi"]:
                            pending["conflict"] = True
                    elif row.get("side") == "P":
                        if pending["puts"] not in (None, row["oi"]):
                            pending["conflict"] = True
                        else:
                            pending["puts"] = row["oi"]
                elif row["kind"] == "total" and pending:
                    pending["total"] = row["oi"]
                    pending["total_name"] = row["name"]
                    take(pending)
                    pending = None
    take(pending)

    trade_dates = {h["trade_date"] for h in headers if h["trade_date"]}
    bulletins = {h["bulletin"] for h in headers if h["bulletin"]}
    finals = {h["final"] for h in headers}
    meta_ok = len(trade_dates) == 1 and len(bulletins) == 1 and finals == {True}
    problems = []
    if not meta_ok:
        problems.append("bulletins do not agree on one FINAL trade date and bulletin number")

    markets = {}
    for code, slot in by_code.items():
        name = slot.get("total_name") or slot.get("name") or ""
        section = slot.get("section")
        if not section:
            continue
        market, why = _market(section, code, name)
        if market is None:
            continue
        rec = markets.setdefault(market, {"families": [], "unavailable": None})
        calls, puts, total = slot.get("calls"), slot.get("puts"), slot.get("total")
        reason = None
        if slot.get("conflict"):
            reason = f"{code} printed twice with different figures"
        elif total is None and calls is None and puts is None:
            # No figure in the open-interest column. Not recorded as zero.
            continue
        elif total is None:
            reason = f"{code} has no readable open-interest total"
        else:
            present = [v for v in (calls, puts) if v is not None]
            if len(present) == 2 and calls + puts != total:
                reason = f"{code} calls {calls} + puts {puts} != total {total}"
            elif len(present) == 1 and present[0] != total:
                reason = f"{code} one side {present[0]} does not equal total {total}"
            elif len(present) == 0:
                reason = f"{code} total {total} has no call/put split"
        if reason:
            rec["unavailable"] = reason
        rec["families"].append({"code": code, "name": name, "calls": calls, "puts": puts,
                                 "total": total, "reason": reason})

    order = ["Gold", "Silver", "Oil", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
    out_markets = {}
    for inst in order:
        rec = markets.get(inst)
        if not rec or rec.get("unavailable") or not rec.get("families"):
            out_markets[inst] = {"available": False,
                                 "reason": (rec or {}).get("unavailable") or "no standard option rows in these sections"}
            continue
        if any(f["reason"] for f in rec["families"]):
            out_markets[inst] = {"available": False,
                                 "reason": next(f["reason"] for f in rec["families"] if f["reason"]),
                                 "families": rec["families"]}
            continue
        calls = sum(f["calls"] or 0 for f in rec["families"])
        puts = sum(f["puts"] or 0 for f in rec["families"])
        total = sum(f["total"] for f in rec["families"])
        if calls + puts != total:
            out_markets[inst] = {"available": False,
                                 "reason": f"family sum calls {calls} + puts {puts} != {total}",
                                 "families": rec["families"]}
            continue
        out_markets[inst] = {"available": True, "oi_opt": total, "calls": calls, "puts": puts,
                             "families": [{"code": f["code"], "name": f["name"], "calls": f["calls"],
                                           "puts": f["puts"], "total": f["total"]}
                                          for f in rec["families"]]}

    fut_out = {}
    for code, row in futures.items():
        inst = FUTURES_CODE.get(code)
        if inst and "MICRO" not in row["name"].upper() and "MINI" not in row["name"].upper():
            fut_out[inst] = {"code": code, "name": row["name"], "oi": row["oi"]}

    return {
        "trade_date": next(iter(trade_dates)) if len(trade_dates) == 1 else None,
        "bulletin": next(iter(bulletins)) if len(bulletins) == 1 else None,
        "final": finals == {True} and len(finals) == 1,
        "ok": meta_ok and not problems,
        "problems": problems,
        "basis": BASIS,
        "markets": out_markets,
        "futures_final": fut_out,
    }


def apply_options(hist, report):
    """Write options fields for report['trade_date'] onto hist. Futures cells stay.

    Returns a list of notes. Does not insert a second copy of the date.
    """
    if not report.get("ok"):
        raise ValueError("report is not a single FINAL bulletin: " + "; ".join(report.get("problems") or []))
    day = report["trade_date"]
    notes = []
    bucket = hist.setdefault(day, {})
    for inst, rec in report["markets"].items():
        cur = bucket.setdefault(inst, {})
        if not rec.get("available"):
            notes.append(f"{inst}: unavailable ({rec.get('reason')})")
            continue
        prev = _prior(hist, day, inst)
        chg = None if prev is None else rec["oi_opt"] - prev
        cur["oi_opt"] = rec["oi_opt"]
        cur["chg_opt"] = chg
        cur["oi_opt_calls"] = rec["calls"]
        cur["oi_opt_puts"] = rec["puts"]
        cur["oi_opt_basis"] = BASIS
        cur["oi_opt_status"] = "FINAL"
        cur["oi_opt_bulletin"] = report["bulletin"]
        cur["oi_opt_source"] = SOURCE
        notes.append(f"{inst}: oi_opt {rec['oi_opt']:,} calls {rec['calls']:,} puts {rec['puts']:,} "
                     f"chg {'—' if chg is None else format(chg, '+,')}")
    return notes


def _prior(hist, day, inst):
    earlier = [d for d in sorted(hist) if d < day
               and isinstance(hist.get(d), dict)
               and isinstance((hist[d].get(inst) or {}), dict)
               and hist[d][inst].get("oi_opt") is not None]
    if not earlier:
        return None
    prev = hist[earlier[-1]][inst]
    if prev.get("oi_opt_basis") != BASIS or prev.get("oi_opt_status") != "FINAL":
        return None
    return prev["oi_opt"]


def compare_futures(report, hist):
    """Final bulletin futures OI against the stored preliminary cell. Nothing is written."""
    day = report.get("trade_date")
    rows = []
    stored = (hist or {}).get(day) or {}
    for inst, fin in sorted((report.get("futures_final") or {}).items()):
        got = (stored.get(inst) or {}).get("oi")
        rows.append({"market": inst, "stored_preliminary": got, "bulletin_final": fin["oi"],
                     "same": got == fin["oi"]})
    return rows


def format_report(report, futures_rows=()):
    lines = [f"Bulletin #{report.get('bulletin')} trade date {report.get('trade_date')} "
             f"{'FINAL' if report.get('final') else 'NOT FINAL'}"]
    if report.get("problems"):
        lines.append("PROBLEMS: " + "; ".join(report["problems"]))
    lines.append("Options are raw contract counts (calls + puts), not futures-equivalent.")
    lines.append("Families with no printed row are omitted, not filled in as zero.")
    for inst, rec in report["markets"].items():
        if not rec.get("available"):
            lines.append(f"  {inst}: UNAVAILABLE — {rec.get('reason')}")
            continue
        fam = ", ".join(f"{f['code']} {f['total']}" for f in rec["families"])
        lines.append(f"  {inst}: {rec['oi_opt']:,} (calls {rec['calls']:,}, puts {rec['puts']:,}) [{fam}]")
    if futures_rows:
        lines.append("Futures revision (stored preliminary vs this final bulletin; not written):")
        for row in futures_rows:
            lines.append(f"  {row['market']}: stored {row['stored_preliminary']} final {row['bulletin_final']}"
                         f"{'' if row['same'] else '  REVISED'}")
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("report", "apply"):
        print(__doc__)
        return 2
    cmd = argv.pop(0)
    data = None
    if "--data" in argv:
        data = Path(argv.pop(argv.index("--data") + 1))
        argv.pop(argv.index("--data"))
    if not argv:
        print("give at least one bulletin PDF or layout .txt")
        return 2
    texts = [layout_text(a) for a in argv]
    report = parse_layouts(texts)
    here = Path(__file__).parent
    hist_path = (data or (here / "data")) / "oi_history.json"
    hist = {}
    if hist_path.exists():
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
    print(format_report(report, compare_futures(report, hist)))
    if cmd == "report":
        return 0 if report.get("ok") else 1
    if not report.get("ok"):
        return 1
    apply_options(hist, report)
    hist_path.write_text(json.dumps(hist, indent=1) + "\n", encoding="utf-8")
    print(f"wrote options fields on {report['trade_date']} in {hist_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
