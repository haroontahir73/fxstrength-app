"""Central-bank speaker calendars, straight from the banks themselves.

WHY
---
The two economic calendars between them listed 11 commentary events across 7 currencies -
1.5% of all events, 9 of them USD, and ZERO for EUR, GBP, NZD and CAD. That is not a sample
of what policymakers said, it is a handful of set-pieces. The banks publish their own
schedules, which are far more complete.

    Federal Reserve   federalreserve.gov/json/calendar.json  (official JSON, note the UTF-8
                      BOM - decode as utf-8-sig). Carries type Speeches / Testimony / FOMC
                      and puts the speaker's RANK in the title ("Speech - Chairman Kevin
                      Warsh"), which is exactly what the seniority weighting needs.
    Bank of England   bankofengland.co.uk/rss/speeches   (RSS, ~50 recent speeches)
    ECB               ecb.europa.eu/rss/press.html       (RSS, speeches mixed with releases)

BoE and ECB feeds are PUBLISHED items, so they are backward-looking - they fill history
rather than giving forward notice. The Fed JSON is a true calendar and covers both.
"""
import html, json, re, urllib.request, datetime as dt

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
FED = "https://www.federalreserve.gov/json/calendar.json"
BOE = "https://www.bankofengland.co.uk/rss/speeches"
ECB = "https://www.ecb.europa.eu/rss/press.html"

FED_TYPES = {"Speeches", "Testimony", "FOMC"}
ET = dt.timezone(dt.timedelta(hours=-4))       # US Eastern, DST half of the year
UK = dt.timezone(dt.timedelta(hours=1))
CET = dt.timezone(dt.timedelta(hours=2))

# BoE and ECB titles name the person but not the office, so rank has to be looked up.
RANK = {
    "andrew bailey": "BoE Governor", "christine lagarde": "ECB President",
    "philip r. lane": "Chief Economist", "philip lane": "Chief Economist",
    "piero cipollone": "Board Member", "isabel schnabel": "Board Member",
    "luis de guindos": "Vice President", "frank elderson": "Board Member",
    "clare lombardelli": "Deputy Governor", "dave ramsden": "Deputy Governor",
    "sarah breeden": "Deputy Governor", "huw pill": "Chief Economist",
    "megan greene": "Policymaker", "catherine mann": "Policymaker",
    "swati dhingra": "Policymaker", "alan taylor": "Policymaker",
}


def _get(url, decode="utf-8"):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=45).read().decode(decode, "replace")


def _clean(v):
    """Feeds embed HTML markup and entities in titles - strip both."""
    if not v:
        return ""
    v = html.unescape(str(v))
    v = re.sub(r"<[^>]+>", " ", v)
    v = html.unescape(v)
    return re.sub(r"\s+", " ", v).strip()


def _rank_suffix(title):
    low = title.lower()
    for name, rank in RANK.items():
        if name in low:
            return f" ({rank})"
    return ""


def fed():
    """Official Fed calendar. Returns forward AND past speeches/testimony/FOMC."""
    raw = json.loads(_get(FED, "utf-8-sig"))
    out = []
    for ev in raw.get("events", []):
        if ev.get("type") not in FED_TYPES:
            continue
        month, days = ev.get("month"), str(ev.get("days") or "").strip()
        if not month or not days:
            continue
        day = re.split(r"[^0-9]", days)[0]      # "28-29" -> "28"
        if not day:
            continue
        t = (ev.get("time") or "").lower().replace(".", "").strip()
        hh, mm = 12, 0
        m = re.match(r"(\d{1,2}):?(\d{2})?\s*(am|pm)?", t)
        if m:
            hh = int(m.group(1)) % 12
            mm = int(m.group(2) or 0)
            if m.group(3) == "pm":
                hh += 12
        try:
            when = dt.datetime(int(month[:4]), int(month[5:7]), int(day), hh, mm, tzinfo=ET)
        except ValueError:
            continue
        title = _clean(ev.get("title"))
        desc = _clean(ev.get("description"))
        if desc and desc.lower() not in title.lower():
            title = f"{title} - {desc}"
        out.append({"ccy": "USD", "title": title,
                    "when": when.astimezone(dt.timezone.utc).isoformat(),
                    "src": "federalreserve.gov", "feed_impact": "Medium"})
    return out


def _rss(url, ccy, src, tz):
    items = re.findall(r"<item>(.*?)</item>", _get(url), re.S)
    out = []
    for it in items:
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
        d = re.search(r"<(?:pubDate|dc:date)>(.*?)</(?:pubDate|dc:date)>", it, re.S)
        if not t or not d:
            continue
        title = _clean(t.group(1))
        raw = d.group(1).strip()
        when = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                when = dt.datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
        out.append({"ccy": ccy, "title": title + _rank_suffix(title),
                    "when": when.astimezone(dt.timezone.utc).isoformat(),
                    "src": src, "feed_impact": "Medium"})
    return out


def boe():
    return _rss(BOE, "GBP", "bankofengland.co.uk", UK)


def ecb():
    rows = _rss(ECB, "EUR", "ecb.europa.eu", CET)
    # the ECB feed mixes speeches with statistical releases; keep the person-led items
    return [r for r in rows if ":" in r["title"] or "speech" in r["title"].lower()]


def all_events():
    out, status = [], {}
    for name, fn in (("fed", fed), ("boe", boe), ("ecb", ecb)):
        try:
            rows = fn()
            out.extend(rows)
            status[name] = len(rows)
        except Exception as e:
            status[name] = f"failed: {type(e).__name__}"
    return out, status


if __name__ == "__main__":
    rows, status = all_events()
    print("fetched:", status)
    now = dt.datetime.now(dt.timezone.utc)
    recent = sorted((r for r in rows
                     if abs((now - dt.datetime.fromisoformat(r["when"])).days) <= 30),
                    key=lambda r: r["when"])
    print(f"\nwithin 30 days either side: {len(recent)}")
    for r in recent[:20]:
        print(f"  {r['when'][:16]} {r['ccy']} {r['title'][:74]}")
