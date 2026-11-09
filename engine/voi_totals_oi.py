"""Import standard-product open interest from local CME VOI Totals .xls files.

The workbooks are files the user already has (Energy, Metals, FX). This module
does not fetch, scrape, or open a network connection. The sheets print no trade
date and no preliminary/final banner, so both are required arguments. They are
never taken from the file name or the file's modification time.

Only the standard futures row and the standard options row are used, matched by
the exact product name: GC, SI, CL, 6E, 6B, 6J, 6A, 6N, 6C, 6S. Micros, minis,
crosses, weeklies, related and financial WTI products, and natural gas are left
out. Natural gas is the bulletin's energy code 6J; it is not JPY. Options are
raw contract counts, calls plus puts, not a futures-equivalent figure.

This path uses the monthly FX option only. bulletin_oi.py can add the weekly
FX series on top of the monthly one. The two bases are stored separately and
are not differenced against each other. A final reading is never replaced by a
preliminary one. Futures cells already stored for that date are not written.

    python voi_totals_oi.py report --date 2026-11-06 --status preliminary energy.xls metals.xls fx.xls
    python voi_totals_oi.py apply --date 2026-11-06 --status preliminary --data data energy.xls metals.xls fx.xls
"""
import datetime as dt
import json
import struct
import sys
from pathlib import Path

BASIS = "voi-totals:standard-full-size-monthly-option-contracts"
SOURCE = "cme-voi-totals-local-xls"

# Exact printed names. Anything else (Micro Gold, E-mini Japanese Yen,
# JPY/USD Weekly, WTI Financial, Natural Gas Option, Japan Crude Cocktail,
# Euro/Japanese Yen, ...) is a different product and is not summed in.
MARKETS = (
    ("GC", "Gold", "Gold Futures", "Gold Option"),
    ("SI", "Silver", "Silver Futures", "Silver Option"),
    ("CL", "Oil", "Crude Oil Futures", "Crude Oil Option"),
    ("6E", "EUR", "Euro FX Futures", "EUR/USD Monthly Options"),
    ("6B", "GBP", "British Pound Futures", "GBP/USD Monthly Options"),
    ("6J", "JPY", "Japanese Yen Futures", "JPY/USD Monthly Options"),
    ("6A", "AUD", "Australian Dollar Futures", "AUD/USD Monthly Options"),
    ("6N", "NZD", "New Zealand Dollar Futures", "NZD/USD Monthly Options"),
    ("6C", "CAD", "Canadian Dollar Futures", "CAD/USD Monthly Options"),
    ("6S", "CHF", "Swiss Franc Futures", "CHF/USD Monthly Options"),
)
ORDER = [code for code, _inst, _fut, _opt in MARKETS]
_BY_NAME = {}
_INST = {}
for _code, _inst, _fut, _opt in MARKETS:
    _BY_NAME[_fut] = (_code, _inst, "futures")
    _BY_NAME[_opt] = (_code, _inst, "options")
    _INST[_code] = _inst

_HEADER_KEYS = {
    "name": "name",
    "type": "type",
    "globex": "globex",
    "clearport": "clear_port",
    "volume": "volume",
    "openinterest": "open_interest",
    "change": "change",
}
_NEED = ("name", "type", "volume", "open_interest", "change")

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAXREG = 0xFFFFFFFA
_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_XL_SST = 0x00FC
_XL_CONTINUE = 0x003C
_XL_LABELSST = 0x00FD
_XL_LABEL = 0x0204
_XL_NUMBER = 0x0203
_XL_RK = 0x027E
_XL_MULRK = 0x00BD
_XL_BOF = 0x0809
_XL_EOF = 0x000A
_XL_FILEPASS = 0x002F


def normalize_date(text):
    """YYYY-MM-DD, or an error. A filename is not a date."""
    raw = (text or "").strip()
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        raise ValueError("date must be YYYY-MM-DD")
    try:
        return dt.date(int(raw[0:4]), int(raw[5:7]), int(raw[8:10])).isoformat()
    except ValueError as e:
        raise ValueError("date must be YYYY-MM-DD") from e


def normalize_status(text):
    """preliminary or final, as given. The workbook does not say which."""
    key = " ".join((text or "").split()).lower()
    if key in ("preliminary", "prelim"):
        return "PRELIMINARY"
    if key == "final":
        return "FINAL"
    raise ValueError("status must be preliminary or final")


def _parse_int(text):
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "—"):
        return None
    if s[0] == "+":
        s = s[1:]
    if len(s) >= 3 and s[0] == "(" and s[-1] == ")":
        s = "-" + s[1:-1]
    if not s or s == "-":
        return None
    if s[0] == "-":
        body = s[1:]
    else:
        body = s
    if not body.isdigit():
        raise ValueError(f"not an integer: {text!r}")
    return int(s)


def _header_token(text):
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _split_table(rows):
    """(column map, data rows) from a VOI sheet. Header names, not positions."""
    header_at = None
    colmap = None
    for i, row in enumerate(rows[:40]):
        mapped = {}
        for c, cell in enumerate(row):
            key = _HEADER_KEYS.get(_header_token(cell))
            if key and key not in mapped:
                mapped[key] = c
        if all(k in mapped for k in _NEED):
            header_at = i
            colmap = mapped
            break
    if colmap is None:
        raise ValueError(
            "no VOI Totals header (Name, Type, Volume, Open Interest, Change)")
    data = []
    for row in rows[header_at + 1:]:
        if not any(str(c).strip() for c in row):
            continue
        data.append(row)
    return colmap, data


def _cell(row, colmap, key):
    idx = colmap.get(key)
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def _nums(row, colmap):
    return {
        "volume": _parse_int(_cell(row, colmap, "volume")),
        "open_interest": _parse_int(_cell(row, colmap, "open_interest")),
        "change": _parse_int(_cell(row, colmap, "change")),
        "globex": _parse_int(_cell(row, colmap, "globex")) if "globex" in colmap else None,
        "clear_port": _parse_int(_cell(row, colmap, "clear_port")) if "clear_port" in colmap else None,
    }


def _same_figure(a, b):
    return all(a.get(k) == b.get(k) for k in ("volume", "open_interest", "change"))


def parse_voi_tables(tables, trade_date, status):
    """Fold already-loaded sheets into a report. `tables` is a list of row grids.

    A market whose calls plus puts do not equal the options total is unavailable.
    It is not stored as a guessed or partial number. Rows that are not the
    standard product are ignored, not added.
    """
    day = normalize_date(trade_date)
    st = normalize_status(status)
    problems = []
    slots = {code: {"futures": None, "options": None, "calls": None, "puts": None,
                    "conflict": None, "names": {}} for code in ORDER}
    parsed = []
    for label, rows in tables:
        try:
            parsed.append((label, _split_table(rows)))
        except ValueError as e:
            problems.append(f"{label}: {e}")
    if not tables and not problems:
        problems.append("no workbook given")

    for label, (colmap, data) in parsed:
        for row in data:
            name = " ".join(str(_cell(row, colmap, "name")).split())
            spec = _BY_NAME.get(name)
            if not spec:
                continue
            code, _inst, kind = spec
            typ = " ".join(str(_cell(row, colmap, "type")).split())
            if kind == "futures" and typ != "Futures":
                continue
            if kind == "options" and typ not in ("Options", "Calls", "Puts"):
                continue
            field = {"Futures": "futures", "Options": "options", "Calls": "calls", "Puts": "puts"}[typ]
            slot = slots[code]
            try:
                rec = _nums(row, colmap)
            except ValueError as e:
                slot["conflict"] = f"{name} {typ}: {e}"
                continue
            if rec["open_interest"] is None:
                slot["conflict"] = f"{name} {typ} has no open interest"
                continue
            prev = slot[field]
            if prev is None:
                slot[field] = rec
                slot["names"][field] = name
            elif not _same_figure(prev, rec):
                slot["conflict"] = f"{name} {typ} printed twice with different figures"

    markets = {}
    for code in ORDER:
        slot = slots[code]
        inst = _INST[code]
        fut = slot["futures"]
        opt, calls, puts = slot["options"], slot["calls"], slot["puts"]
        reason = slot["conflict"]
        if reason is None:
            if opt is None and calls is None and puts is None:
                reason = "no standard option rows in these workbooks"
            elif opt is None or calls is None or puts is None:
                reason = "standard option row is missing the options total or the call/put split"
            elif calls["open_interest"] + puts["open_interest"] != opt["open_interest"]:
                reason = (f"calls {calls['open_interest']} + puts {puts['open_interest']} "
                          f"!= total {opt['open_interest']}")
            elif (calls["change"] is not None and puts["change"] is not None
                  and opt["change"] is not None
                  and calls["change"] + puts["change"] != opt["change"]):
                reason = "call change + put change != options change"
            elif (calls["volume"] is not None and puts["volume"] is not None
                  and opt["volume"] is not None
                  and calls["volume"] + puts["volume"] != opt["volume"]):
                reason = "call volume + put volume != options volume"
        product = slot["names"].get("options")
        futures = None
        if fut is not None:
            futures = {"product": slot["names"].get("futures"), "volume": fut["volume"],
                       "open_interest": fut["open_interest"], "change": fut["change"],
                       "globex": fut["globex"], "clear_port": fut["clear_port"]}
        options = None
        if reason is None:
            options = {"product": product, "total_oi": opt["open_interest"],
                       "calls_oi": calls["open_interest"], "puts_oi": puts["open_interest"],
                       "total_volume": opt["volume"], "total_change": opt["change"],
                       "calls_volume": calls["volume"], "puts_volume": puts["volume"],
                       "calls_change": calls["change"], "puts_change": puts["change"],
                       "calls_plus_puts_equals_total": True}
        markets[code] = {"instrument": inst, "available": reason is None,
                         "futures": futures, "options": options, "reason": reason}

    return {
        "trade_date": day,
        "status": st,
        "ok": not problems and all(m["available"] for m in markets.values()),
        "problems": problems,
        "basis": BASIS,
        "source": SOURCE,
        "markets": markets,
    }


def _prior(hist, day, inst, status):
    """Nearest earlier options reading. A different basis or status is not a delta."""
    earlier = []
    for d in sorted(hist):
        if d >= day:
            continue
        bucket = hist.get(d)
        if not isinstance(bucket, dict):
            continue
        cur = bucket.get(inst)
        if isinstance(cur, dict) and cur.get("oi_opt") is not None:
            earlier.append(d)
    if not earlier:
        return None
    prev = hist[earlier[-1]][inst]
    if prev.get("oi_opt_basis") != BASIS or prev.get("oi_opt_status") != status:
        return None
    return prev["oi_opt"]


def _blocked(cur, status):
    """Why this cell must be left alone, or None if options may be written.

    Futures keys are never offered to this check. A FINAL options reading stays
    when the file is PRELIMINARY. A different basis (the bulletin's monthly plus
    weekly total, for example) is not overwritten by this monthly-only series.
    """
    if not isinstance(cur, dict):
        return None
    if cur.get("oi_opt") is None and not cur.get("oi_opt_status") and not cur.get("oi_opt_basis"):
        return None
    if cur.get("oi_opt_status") == "FINAL" and status != "FINAL":
        return "FINAL options reading is not overwritten by a PRELIMINARY file"
    basis = cur.get("oi_opt_basis")
    if basis and basis != BASIS:
        return f"existing options basis {basis} is a different series"
    return None


def apply_options(hist, report):
    """Write options fields for report['trade_date']. Futures cells stay as they are.

    The stored change is the difference against the nearest earlier reading of
    this same basis and status. The workbook's printed Change column is not
    copied, and the first observation is None rather than a made-up zero.
    Returns a list of notes. Does not insert a second copy of the date.
    """
    if report.get("problems"):
        raise ValueError("refusing to write: " + "; ".join(report["problems"]))
    day = report["trade_date"]
    status = report["status"]
    notes = []
    existing = hist.get(day)
    bucket = existing if isinstance(existing, dict) else {}
    wrote = False
    for code, rec in report["markets"].items():
        inst = rec["instrument"]
        cur = bucket.get(inst) if isinstance(bucket.get(inst), dict) else None
        fresh = cur is None
        if fresh:
            cur = {}
        if not rec.get("available"):
            notes.append(f"{code} {inst}: unavailable ({rec.get('reason')})")
            continue
        why = _blocked(cur, status)
        if why:
            notes.append(f"{code} {inst}: left unchanged ({why})")
            continue
        prev = _prior(hist, day, inst, status)
        oi_opt = rec["options"]["total_oi"]
        chg = None if prev is None else oi_opt - prev
        cur["oi_opt"] = oi_opt
        cur["chg_opt"] = chg
        cur["oi_opt_calls"] = rec["options"]["calls_oi"]
        cur["oi_opt_puts"] = rec["options"]["puts_oi"]
        cur["oi_opt_basis"] = BASIS
        cur["oi_opt_status"] = status
        cur["oi_opt_source"] = SOURCE
        if fresh:
            bucket[inst] = cur
        wrote = True
        notes.append(f"{code} {inst}: oi_opt {oi_opt:,} calls {rec['options']['calls_oi']:,} "
                     f"puts {rec['options']['puts_oi']:,} "
                     f"chg {'—' if chg is None else format(chg, '+,')}")
    if wrote and existing is None:
        hist[day] = bucket
    return notes


def compare_futures(report, hist):
    """File futures OI beside the stored cell. Nothing is written."""
    day = report.get("trade_date")
    stored = (hist or {}).get(day) or {}
    rows = []
    for code, rec in report["markets"].items():
        fin = rec.get("futures")
        if not fin:
            continue
        inst = rec["instrument"]
        got = (stored.get(inst) or {}).get("oi") if isinstance(stored.get(inst), dict) else None
        rows.append({"market": inst, "code": code, "stored": got,
                     "file": fin["open_interest"], "same": got == fin["open_interest"]})
    return rows


def format_report(report, futures_rows=()):
    lines = [f"VOI Totals trade date {report.get('trade_date')} {report.get('status')}"]
    lines.append("Options are raw contract counts (calls + puts), not futures-equivalent.")
    lines.append("Standard monthly product only. Weeklies, micros, minis and crosses are not included.")
    lines.append("bulletin_oi.py may also add weekly FX options; this import does not.")
    lines.append("The printed Change is shown here. A stored change is only the difference "
                 "against an earlier reading of this same basis, and the first day has none.")
    if report.get("problems"):
        lines.append("PROBLEMS: " + "; ".join(report["problems"]))
    for code, rec in report["markets"].items():
        inst = rec["instrument"]
        fut = rec.get("futures")
        if fut:
            chg = fut["change"]
            chg_s = "—" if chg is None else format(chg, "+,")
            vol = "—" if fut["volume"] is None else format(fut["volume"], ",")
            fut_s = f"futures oi {fut['open_interest']:,} chg {chg_s} vol {vol}"
        else:
            fut_s = "futures MISSING"
        if rec.get("available"):
            o = rec["options"]
            lines.append(f"  {code} {inst}: {fut_s} | options {o['total_oi']:,} "
                         f"(calls {o['calls_oi']:,}, puts {o['puts_oi']:,}) [{o['product']}]")
        else:
            lines.append(f"  {code} {inst}: {fut_s} | options UNAVAILABLE — {rec.get('reason')}")
    if futures_rows:
        lines.append("Futures (stored cell vs this file; not written):")
        for row in futures_rows:
            if row["stored"] is None:
                flag = "  (nothing stored)"
            elif row["same"]:
                flag = ""
            else:
                flag = "  DIFFERS"
            lines.append(f"  {row['code']} {row['market']}: stored {row['stored']} file {row['file']}{flag}")
    return "\n".join(lines)


# ---------------------------------------------------------------- OLE / BIFF8
def _u16(buf, off):
    return int.from_bytes(buf[off:off + 2], "little")


def _u32(buf, off):
    return int.from_bytes(buf[off:off + 4], "little")


def _chain(fat, start):
    out = []
    seen = set()
    sid = start
    while sid <= _MAXREG:
        if sid in seen or sid >= len(fat):
            raise ValueError("broken OLE sector chain")
        seen.add(sid)
        out.append(sid)
        sid = fat[sid]
        if len(out) > 100000:
            raise ValueError("OLE sector chain too long")
    return out


def _workbook_stream(blob):
    """Bytes of the Workbook stream inside an Excel 97 compound document."""
    if len(blob) < 512 or blob[:8] != _OLE_MAGIC:
        head = blob[:80].lstrip().lower()
        if head.startswith(b"<") or b"<table" in head:
            raise ValueError("this is HTML, not the binary VOI Totals .xls")
        raise ValueError("not an Excel 97 VOI Totals workbook")
    if blob[28:30] != b"\xfe\xff":
        raise ValueError("OLE byte order is not little-endian")
    major = _u16(blob, 0x1A)
    sector_size = 1 << _u16(blob, 0x1E)
    mini_size = 1 << _u16(blob, 0x20)
    fat_count = _u32(blob, 0x2C)
    first_dir = _u32(blob, 0x30)
    mini_cutoff = _u32(blob, 0x38)
    first_mini = _u32(blob, 0x3C)
    mini_count = _u32(blob, 0x40)
    difat_next = _u32(blob, 0x44)
    difat_count = _u32(blob, 0x48)

    def sector(sid):
        off = (sid + 1) * sector_size
        chunk = blob[off:off + sector_size]
        if len(chunk) < sector_size:
            raise ValueError("OLE sector runs past the end of the file")
        return chunk

    difat = [_u32(blob, 0x4C + 4 * i) for i in range(109)]
    seen_difat = set()
    hops = 0
    while difat_next <= _MAXREG and hops < difat_count:
        if difat_next in seen_difat:
            raise ValueError("broken OLE DIFAT chain")
        seen_difat.add(difat_next)
        chunk = sector(difat_next)
        n = sector_size // 4 - 1
        difat.extend(_u32(chunk, 4 * i) for i in range(n))
        difat_next = _u32(chunk, sector_size - 4)
        hops += 1
    fat_sids = [v for v in difat if v <= _MAXREG][:fat_count]
    if len(fat_sids) != fat_count:
        raise ValueError("OLE FAT sector list is short")
    fat = []
    for sid in fat_sids:
        chunk = sector(sid)
        fat.extend(_u32(chunk, i) for i in range(0, sector_size, 4))

    def read_regular(start, size=None):
        if start > _MAXREG:
            return b""
        parts = []
        for sid in _chain(fat, start):
            parts.append(sector(sid))
            if size is not None and sum(map(len, parts)) >= size:
                break
        raw = b"".join(parts)
        return raw if size is None else raw[:size]

    dir_blob = read_regular(first_dir)
    entries = []
    for off in range(0, len(dir_blob) - 127, 128):
        dent = dir_blob[off:off + 128]
        name_len = _u16(dent, 0x40)
        etype = dent[0x42]
        if name_len >= 2:
            name = dent[:name_len - 2].decode("utf-16le", "replace")
        else:
            name = ""
        start = _u32(dent, 0x74)
        if major >= 4:
            size = int.from_bytes(dent[0x78:0x80], "little")
        else:
            size = _u32(dent, 0x78)
        entries.append((name, etype, start, size))
    if not entries:
        raise ValueError("OLE directory is empty")

    root_start, root_size = entries[0][2], entries[0][3]
    mini_fat = []
    if mini_count and first_mini <= _MAXREG:
        for sid in _chain(fat, first_mini)[:mini_count]:
            chunk = sector(sid)
            mini_fat.extend(_u32(chunk, i) for i in range(0, sector_size, 4))
    mini_stream = read_regular(root_start, root_size) if root_start <= _MAXREG else b""

    def read_mini(start, size):
        if start > _MAXREG or size <= 0:
            return b""
        parts = []
        sid = start
        seen = set()
        while sid <= _MAXREG and sum(map(len, parts)) < size:
            if sid in seen or sid >= len(mini_fat):
                raise ValueError("broken OLE mini-sector chain")
            seen.add(sid)
            off = sid * mini_size
            parts.append(mini_stream[off:off + mini_size])
            sid = mini_fat[sid]
        return b"".join(parts)[:size]

    chosen = None
    for name, etype, start, size in entries:
        if etype == 2 and name in ("Workbook", "Book"):
            chosen = (name, start, size)
            if name == "Workbook":
                break
    if chosen is None:
        raise ValueError("workbook stream is missing")
    _name, start, size = chosen
    if size < mini_cutoff:
        return read_mini(start, size)
    return read_regular(start, size)


def _iter_records(data):
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        code = _u16(data, pos)
        length = _u16(data, pos + 2)
        pos += 4
        payload = data[pos:pos + length]
        if len(payload) != length:
            raise ValueError("truncated BIFF record")
        pos += length
        yield code, payload


def _unpack_sst(chunks):
    nstrings = int.from_bytes(chunks[0][4:8], "little", signed=True)
    if nstrings < 0 or nstrings > 5_000_000:
        raise ValueError("shared-string table has an implausible length")
    datainx = 0
    data = chunks[0]
    datalen = len(data)
    pos = 8
    strings = []

    def pull(nbytes):
        nonlocal datainx, data, datalen, pos
        out = []
        need = nbytes
        while need:
            if pos >= datalen:
                datainx += 1
                data = chunks[datainx]
                datalen = len(data)
                pos = 0
            take = min(need, datalen - pos)
            out.append(data[pos:pos + take])
            pos += take
            need -= take
        return b"".join(out)

    for i in range(nstrings):
        nchars = int.from_bytes(data[pos:pos + 2], "little")
        pos += 2
        options = data[pos]
        pos += 1
        rtcount = 0
        phosz = 0
        if options & 0x08:
            rtcount = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        if options & 0x04:
            phosz = int.from_bytes(data[pos:pos + 4], "little", signed=True)
            pos += 4
        acc = []
        got = 0
        while True:
            need = nchars - got
            if options & 0x01:
                avail = min((datalen - pos) // 2, need)
                acc.append(data[pos:pos + 2 * avail].decode("utf-16le", "replace"))
                pos += 2 * avail
            else:
                avail = min(datalen - pos, need)
                acc.append(data[pos:pos + avail].decode("latin-1"))
                pos += avail
            got += avail
            if got == nchars:
                break
            datainx += 1
            data = chunks[datainx]
            datalen = len(data)
            options = data[0]
            pos = 1
        if rtcount:
            pull(rtcount * 4)
        if phosz:
            pull(phosz)
        if pos >= datalen and i != nstrings - 1:
            pos -= datalen
            datainx += 1
            data = chunks[datainx]
            datalen = len(data)
        strings.append("".join(acc))
    return strings


def _unpack_rk(buf):
    flags = buf[0]
    if flags & 2:
        i = int.from_bytes(buf, "little", signed=True)
        val = float(i >> 2)
    else:
        raw = bytes((0, 0, 0, 0, flags & 0xFC, buf[1], buf[2], buf[3]))
        val = struct.unpack("<d", raw)[0]
    if flags & 1:
        val /= 100.0
    return val


def _num_text(val):
    if isinstance(val, float) and val.is_integer() and abs(val) < 10 ** 15:
        return str(int(val))
    return str(val)


def _unpack_label(data):
    nchars = _u16(data, 0)
    flags = data[2]
    pos = 3
    if flags & 0x08:
        pos += 2
    if flags & 0x04:
        pos += 4
    if flags & 0x01:
        raw = data[pos:pos + nchars * 2]
        return raw.decode("utf-16le", "replace")
    return data[pos:pos + nchars].decode("latin-1")


def _sheet_grids(stream):
    """List of {(row, col): text} for each worksheet, first to last."""
    recs = list(_iter_records(stream))
    strings = []
    i = 0
    phase = "globals"
    sheets = []
    cells = None
    while i < len(recs):
        code, payload = recs[i]
        i += 1
        if code == _XL_FILEPASS:
            raise ValueError("workbook is encrypted; nothing was read from it")
        if phase == "globals":
            if code == _XL_SST:
                chunks = [payload]
                while i < len(recs) and recs[i][0] == _XL_CONTINUE:
                    chunks.append(recs[i][1])
                    i += 1
                strings = _unpack_sst(chunks)
            elif code == _XL_EOF:
                phase = "sheets"
            continue
        if code == _XL_BOF and cells is None:
            cells = {}
            continue
        if cells is None:
            continue
        if code == _XL_EOF:
            sheets.append(cells)
            cells = None
            continue
        if code == _XL_LABELSST and len(payload) >= 10:
            row, col, _xf, idx = struct.unpack("<HHHi", payload[:10])
            if idx < 0 or idx >= len(strings):
                raise ValueError("label points outside the shared-string table")
            cells[(row, col)] = strings[idx]
        elif code == _XL_LABEL and len(payload) >= 8:
            row, col = struct.unpack("<HH", payload[:4])
            cells[(row, col)] = _unpack_label(payload[6:])
        elif code == _XL_NUMBER and len(payload) >= 14:
            row, col, _xf, val = struct.unpack("<HHHd", payload[:14])
            cells[(row, col)] = _num_text(val)
        elif code == _XL_RK and len(payload) >= 10:
            row, col = struct.unpack("<HH", payload[:4])
            cells[(row, col)] = _num_text(_unpack_rk(payload[6:10]))
        elif code == _XL_MULRK and len(payload) >= 6:
            row, first = struct.unpack("<HH", payload[:4])
            last = struct.unpack("<H", payload[-2:])[0]
            pos = 4
            for col in range(first, last + 1):
                if pos + 6 > len(payload) - 2:
                    break
                cells[(row, col)] = _num_text(_unpack_rk(payload[pos + 2:pos + 6]))
                pos += 6
    if cells:
        sheets.append(cells)
    return sheets


def _grid_rows(cells):
    if not cells:
        return []
    max_r = max(r for r, _c in cells)
    max_c = max(c for _r, c in cells)
    return [[cells.get((r, c), "") for c in range(max_c + 1)] for r in range(max_r + 1)]


def read_voi_table(path):
    """Row grid of the VOI Totals sheet. Local file only. No date is inferred."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    blob = p.read_bytes()
    stream = _workbook_stream(blob)
    grids = _sheet_grids(stream)
    if not grids:
        raise ValueError("workbook has no worksheet")
    chosen = None
    first_err = None
    for cells in grids:
        rows = _grid_rows(cells)
        try:
            _split_table(rows)
        except ValueError as e:
            if first_err is None:
                first_err = e
            continue
        chosen = rows
        break
    if chosen is None:
        raise ValueError(str(first_err or "workbook has no worksheet"))
    return chosen


def build_report(paths, trade_date, status):
    """Parse one or more local VOI workbooks. The date and status come from the caller."""
    tables = []
    problems = []
    if not paths:
        problems.append("no workbook given")
    for path in paths:
        label = Path(path).name
        try:
            tables.append((label, read_voi_table(path)))
        except Exception as e:                                # noqa: BLE001
            problems.append(f"{label}: {e}")
    try:
        report = parse_voi_tables(tables, trade_date, status)
    except ValueError as e:
        return {"trade_date": None, "status": None, "ok": False, "problems": [str(e)],
                "basis": BASIS, "source": SOURCE, "markets": {}}
    report["problems"] = problems + report["problems"]
    report["ok"] = not report["problems"] and bool(report["markets"]) and all(
        m["available"] for m in report["markets"].values())
    return report


def _load_hist(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in ("report", "apply"):
        print(__doc__)
        return 2
    cmd = argv.pop(0)
    date = status = None
    data = None
    files = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--date", "--status", "--data"):
            if i + 1 >= len(argv):
                print(f"{a} needs a value")
                return 2
            value = argv[i + 1]
            i += 2
            if a == "--date":
                date = value
            elif a == "--status":
                status = value
            else:
                data = value
        elif a.startswith("-"):
            print(f"unknown argument {a}")
            return 2
        else:
            files.append(a)
            i += 1
    if not date or not status:
        print("trade date and status are required: --date YYYY-MM-DD --status preliminary|final")
        print("They are not read from the file name or the file's modification time.")
        return 2
    if not files:
        print("give at least one VOI Totals .xls file")
        return 2
    try:
        normalize_date(date)
        normalize_status(status)
    except ValueError as e:
        print(e)
        return 2
    report = build_report(files, date, status)
    here = Path(__file__).parent
    hist_path = (Path(data) if data else (here / "data")) / "oi_history.json"
    hist = {}
    if hist_path.exists():
        hist = _load_hist(hist_path)
    print(format_report(report, compare_futures(report, hist)))
    if cmd == "report":
        return 0 if report.get("ok") else 1
    # A partial session (one market missing, or calls that do not add) is not
    # written. The operator sees the report and re-runs once the files cover
    # every standard product.
    if not report.get("ok"):
        print("nothing written")
        return 1
    notes = apply_options(hist, report)
    for note in notes:
        print(note)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(hist, indent=1) + "\n", encoding="utf-8")
    print(f"wrote options fields on {report['trade_date']} ({report['status']}) in {hist_path}")
    print("futures cells were not written")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
