"""Market-implied rate-change probabilities - the FedWatch input.

WHY NOT CME FEDWATCH DIRECTLY
-----------------------------
cmegroup.com returns 403 to automated requests from this machine (the whole domain is
blocked - the daily bulletin resets the connection outright). So FedWatch itself cannot be
read here. rateprobability.com was checked and REJECTED: it served January 2026 meeting
dates and stale policy rates (BoJ 0.75% when the 31 July decision printed 1.00%).

centralbank.watch is current, server-renders its numbers into the HTML, and covers the ECB,
BoE, BoJ and RBA. It does NOT publish Fed probabilities - it links out to FedWatch - and has
no RBNZ. Those gaps fall to data/rate_expectations_manual.json, filled by the daily research
pass exactly like speech tone. Nothing is guessed: an unfilled bank simply has no expectation
and its checklist indicators fall back to the other sources.

Writes data/rate_expectations.json.
"""
import json, re, urllib.request, datetime as dt
from config import DATA, ORDER

URL = "https://centralbank.watch/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MANUAL = DATA / "rate_expectations_manual.json"

BANKS = {
    "Federal Reserve": "USD",
    "European Central Bank": "EUR",
    "Bank of England": "GBP",
    "Bank of Japan": "JPY",
    "Reserve Bank of Australia": "AUD",
    "Bank of Canada": "CAD",
    "Reserve Bank of New Zealand": "NZD",
}


def _flat(html):
    t = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", t)
    t = re.sub(r"\|{2,}", "|", t)
    return re.sub(r"[ \t]+", " ", t)


def scrape():
    html = urllib.request.urlopen(
        urllib.request.Request(URL, headers=UA), timeout=45).read().decode("utf-8", "replace")
    flat = _flat(html)
    out = {}
    positions = []
    for name in BANKS:
        i = flat.find(name)
        if i >= 0:
            positions.append((i, name))
    positions.sort()
    for idx, (i, name) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(flat)
        seg = flat[i:end]
        rec = {"bank": name}
        m = re.search(r"Next Meeting Date:\|([^|]+)\|", seg)
        if m:
            rec["next_meeting"] = m.group(1).strip()
        m = re.search(r"Current Rate:\s*([\d.]+)%", seg)
        if m:
            rec["current_rate"] = float(m.group(1))
        hike = re.search(r"Rate Hike\|\s*([\d.]+)%", seg)
        hold = re.search(r"No Change\|\s*([\d.]+)%", seg)
        cut = re.search(r"Rate Cut\|\s*([\d.]+)%", seg)
        if hike and cut:
            rec["hike"] = float(hike.group(1))
            rec["hold"] = float(hold.group(1)) if hold else None
            rec["cut"] = float(cut.group(1))
            rec["src"] = "centralbank.watch"
        out[BANKS[name]] = rec
    return out


def main():
    now = dt.datetime.now(dt.timezone.utc)
    try:
        scraped = scrape()
        status = "ok"
    except Exception as e:
        scraped, status = {}, f"failed: {type(e).__name__}: {e}"

    try:
        manual = {k: v for k, v in json.loads(MANUAL.read_text(encoding="utf-8")).items()
                  if not k.startswith("_")} if MANUAL.exists() else {}
    except Exception:
        manual = {}

    out = {"fetched_at": now.isoformat(), "status": status, "currencies": {}}
    for ccy in ORDER:
        rec = dict(scraped.get(ccy, {}))
        m = manual.get(ccy)
        if m and ("hike" not in rec or m.get("override")):
            rec.update({k: v for k, v in m.items() if k != "override"})
            rec["src"] = m.get("src", "manual research")
        if "hike" in rec and "cut" in rec:
            net = (rec["hike"] - rec["cut"]) / 100.0        # -1 .. +1
            rec["net"] = round(net, 3)
            rec["score"] = round(max(1.0, min(5.0, 3 + 2 * net)), 2)
            rec["basis"] = (f"{rec['hike']:.0f}% hike / {rec.get('hold') or 0:.0f}% hold / "
                            f"{rec['cut']:.0f}% cut for {rec.get('next_meeting', 'the next meeting')}"
                            f" ({rec.get('src')})")
        out["currencies"][ccy] = rec
    (DATA / "rate_expectations.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Snapshot history. There is no archive of past rate-expectation data anywhere reachable,
    # so the 0.10 weight on this component is currently unbacktestable. Recording one row per
    # day turns that from an argument into a measurement in a few weeks - at which point
    # backtest_blend.py can score it like every other leg.
    hist_file = DATA / "expectations_history.json"
    try:
        hist = json.loads(hist_file.read_text(encoding="utf-8")) if hist_file.exists() else {}
    except Exception:
        hist = {}
    hist[now.date().isoformat()] = {
        c: {k: d[k] for k in ("hike", "hold", "cut", "score", "next_meeting", "src") if k in d}
        for c, d in out["currencies"].items() if "score" in d}
    hist_file.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = main()
    print(f"  status: {r['status']}")
    for c in ORDER:
        d = r["currencies"][c]
        if "score" in d:
            print(f"  {c}  score {d['score']:.2f}/5   {d['basis']}")
        else:
            print(f"  {c}  no probabilities  ({d.get('bank','-')}, "
                  f"next {d.get('next_meeting','?')}) - needs manual research")
