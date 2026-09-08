"""Economic calendar + news-surprise engine.

Source: TradingView's public economic-calendar API. Chosen over the ForexFactory
weekly JSON because that feed carries no `actual` field at all (only forecast /
previous), which makes release detection impossible. This one returns actual,
forecast and previous as pre-parsed numerics (`actualRaw` etc.), an importance
tier, and accepts an arbitrary date range.

Writes data/calendar.json:
  released[]     events that printed, each with a signed surprise score
  news_score{}   per-currency time-decayed surprise, squashed to -100..+100
  upcoming[]     future events, soonest first
  next_release   drives the news-triggered refresh
"""
import json, math, collections, urllib.request, urllib.error, datetime as dt
from config import DATA, NEWS_HALFLIFE_HOURS, NEWS_IMPACT_WEIGHT, ORDER

API = "https://economic-calendar.tradingview.com/events"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}
COUNTRY = {"US": "USD", "GB": "GBP", "JP": "JPY", "EU": "EUR",
           "AU": "AUD", "NZ": "NZD", "CA": "CAD", "CH": "CHF"}
IMPORTANCE = {1: "High", 0: "Medium", -1: "Low"}

# Indicators where a HIGHER print is bearish for the currency.
INVERTED = ("unemployment", "jobless", "claims", "deficit",
            "delinquen", "foreclosure", "bankrupt", "misery",
            # rising imports widen the trade gap; rising public borrowing is a fiscal negative
            "imports", "net borrowing", "budget deficit", "public sector net")

# Events that release constantly but say nothing about currency strength. Left in, they
# swamped the score: 194 of 932 released events, 136 of them USD - the dollar's news reading
# was largely EIA oil inventories. Energy stocks belong to crude, not to the dollar.
EXCLUDE = ("crude oil", "gasoline stock", "natural gas", "distillate", "rig count",
           "cushing", "eia ", "api ", "heating oil", "ethanol",
           "bill auction", "bond auction", "note auction", "jgb auction", "gilt auction",
           "index auction", "bund auction", "btp auction", "oat auction",
           # lender-side plumbing, not a currency signal
           "mba mortgage market", "mba mortgage refinance", "mortgage rate",
           "bba mortgage rate")

# Revisions of one datapoint: "Building Permits MoM Prel" / "... Final" / "... Adv" all
# describe the SAME release and were each scored, quadruple-counting a single print.
REVISION_TAGS = (" prel", " final", " adv", " 2nd est", " 3rd est", " flash", " revised",
                 " preliminary", " advance")


def base_indicator(title):
    t = title.lower()
    for tag in REVISION_TAGS:
        t = t.replace(tag, "")
    return " ".join(t.split())

HORIZON_TAGS = (" mom", " yoy", " qoq", " m/m", " y/y", " q/q", " annualized",
                " annualised", " ann", " 3 mon", " quarterly", " monthly", " weekly")


def index_family(title):
    """The underlying index, ignoring WHICH HORIZON it is reported on.

    Used only to share weight between several readings of one publication - never for
    dedup. Merging horizons in base_indicator() picks a winner and that was a genuine
    bug once: GDP QoQ and GDP YoY collapsed to a single key, the weaker one won, and it
    knocked AUD from +18 to +3 on the live page."""
    t = base_indicator(title)
    for tag in HORIZON_TAGS:
        t = t.replace(tag, "")
    return " ".join(t.split())


LOOKBACK_DAYS = 45   # long enough that every monthly indicator has printed at least once
LOOKAHEAD_DAYS = 10


def fetch(frm, to):
    q = (f"{API}?from={frm.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
         f"&to={to.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
         f"&countries={'%2C'.join(COUNTRY)}")
    req = urllib.request.Request(q, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "replace")
    payload = json.loads(raw)
    if payload.get("status") != "ok":
        raise RuntimeError(f"calendar API status={payload.get('status')}")
    # A window with no events comes back as {"status":"ok"} with no "result" key at all.
    return payload.get("result", [])


def is_scoreable(title):
    """False for events that release constantly but carry no currency signal."""
    return not any(k in title.lower() for k in EXCLUDE)


PCT_SCALE_FLOOR = 0.5          # see surprise(): stops near-zero %% series pinning at +/-1


def surprise(ev):
    """Signed -1..1 surprise, oriented so positive is bullish for the currency."""
    a, f, p = ev.get("actualRaw"), ev.get("forecastRaw"), ev.get("previousRaw")
    if a is None or not is_scoreable(ev["title"]):
        return None
    base = f if f is not None else p
    if base is None:
        return None
    # The scale is the SIZE OF THE NUMBER, which breaks when an indicator sits near zero:
    # a 0.1%-growth series that prints -0.4 collapsed to the 0.1 floor and scored a maximal
    # -1.00 for what is an ordinary monthly wobble. That is the same "near-zero value pins
    # the score" fault that already forced fiscal balance and debt level to be manual-only.
    # Percentage-unit series therefore get a floor set from what a miss ACTUALLY looks like:
    # across 2,970 scored events the median %-point miss is 0.20 and the 75th percentile is
    # 0.70, so 0.5 sits between "normal" and "notable". It only binds when the level itself
    # is under 0.5 - CPI, unemployment, payrolls and every large-number series are untouched.
    floor = PCT_SCALE_FLOOR if (ev.get("unit") or "").strip() == "%" else 0.1
    scale = max(abs(base), abs(p) if p is not None else 0.0, floor)
    # tanh rather than a hard clip: with forecasts as small as 0.2, any modest beat is a
    # 100% relative miss and 20% of all events pinned at exactly +/-1.00. Compressing
    # smoothly keeps the ordering while letting genuine outliers still outrank small beats.
    z = math.tanh((a - base) / scale)
    if any(k in ev["title"].lower() for k in INVERTED):
        z = -z
    return z


def main():
    now = dt.datetime.now(dt.timezone.utc)
    try:
        rows = fetch(now - dt.timedelta(days=LOOKBACK_DAYS), now + dt.timedelta(days=LOOKAHEAD_DAYS))
    except Exception as e:
        print(f"  calendar fetch FAILED: {type(e).__name__}: {e}")
        cached = DATA / "calendar.json"
        if cached.exists():
            print("  reusing cached calendar.json")
            return json.loads(cached.read_text(encoding="utf-8"))
        # cold start with no cache: emit an empty-but-valid calendar so the rest of the
        # pipeline still produces a dashboard (news leg = 0). It self-heals next run.
        print("  no cache - writing an empty calendar so the build can proceed")
        empty = {"fetched_at": now.isoformat(), "released": [],
                 "news_score": {c: 0.0 for c in ORDER}, "contributors": {c: [] for c in ORDER},
                 "next_release": None, "next_high_impact": None,
                 "upcoming": [], "event_count": 0, "degraded": True}
        (DATA / "calendar.json").write_text(json.dumps(empty, indent=2), encoding="utf-8")
        return empty

    events, released = [], []
    for ev in rows:
        ccy = COUNTRY.get(ev.get("country"))
        if not ccy:
            continue
        when = dt.datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        rec = {"title": ev["title"], "ccy": ccy,
               "impact": IMPORTANCE.get(ev.get("importance"), "Low"),
               "when": when.isoformat(),
               "actual": ev.get("actual"), "forecast": ev.get("forecast"),
               "previous": ev.get("previous"), "unit": ev.get("unit") or "",
               # raw numerics kept so the checklist can score LEVELS, independently of
               # the surprise-vs-forecast reading the news leg already makes
               "actual_raw": ev.get("actualRaw"), "previous_raw": ev.get("previousRaw")}
        s = surprise(ev)
        if s is not None:
            rec["surprise"] = round(s, 3)
            released.append(rec)
        events.append(rec)
    events.sort(key=lambda e: e["when"])

    # Keep only the LATEST revision of each indicator per currency.
    latest = {}
    for rec in sorted(released, key=lambda r: r["when"]):
        latest[(rec["ccy"], base_indicator(rec["title"]))] = rec
    deduped = list(latest.values())

    # A weighted MEAN, not a sum. Summing rewarded whichever currency simply publishes more
    # data - the US had 42 scored events in the window against 3 for the euro area, which
    # pinned USD at the tanh ceiling regardless of what the prints actually said.
    num = {c: 0.0 for c in ORDER}
    den = {c: 0.0 for c in ORDER}
    contrib = {c: [] for c in ORDER}
    # TRIED AND REJECTED 2026-09-07: sharing weight between several horizons of one
    # publication (the Lloyds survey lands as both YoY and MoM and between them made up 99%
    # of GBP's news reading). It looked right, but backtest_blend measured it: the news leg
    # fell from +0.217 to +0.176 mean forward rho, 11/13 positive weeks down to 10/13, t
    # from 2.29 to 1.97. News is the one component with real evidence, so a change that
    # dents it is not worth a tidier-looking GBP - the redundancy apparently carries signal
    # (a release that is bad on every horizon really is worse news). `index_family` is kept;
    # it is the right grouping if this is ever revisited with more history.
    for rec in deduped:
        age_h = (now - dt.datetime.fromisoformat(rec["when"])).total_seconds() / 3600
        if age_h < 0 or age_h > NEWS_HALFLIFE_HOURS * 4:
            continue
        decay = 0.5 ** (age_h / NEWS_HALFLIFE_HOURS)
        w = NEWS_IMPACT_WEIGHT.get(rec["impact"], 0.15) * decay
        num[rec["ccy"]] += rec["surprise"] * w
        den[rec["ccy"]] += w
        contrib[rec["ccy"]].append({**rec, "points": round(rec["surprise"] * w * 100, 1),
                                    "age_h": round(age_h, 1)})

    # Confidence grows with how much evidence there is and saturates, so a lone Low-impact
    # print cannot swing the score the way one CBI survey was swinging GBP to -50.
    news = {}
    for c in ORDER:
        if den[c] <= 0:
            news[c] = 0.0
            continue
        mean = num[c] / den[c]
        confidence = math.tanh(den[c] / 1.5)
        news[c] = round(mean * confidence * 100, 1)
    for c in contrib:
        contrib[c].sort(key=lambda r: -abs(r["points"]))
        del contrib[c][8:]

    upcoming = [e for e in events if dt.datetime.fromisoformat(e["when"]) > now]
    nxt = upcoming[0]["when"] if upcoming else None
    nxt_high_e = next((e for e in upcoming if e["impact"] == "High"), None)
    nxt_high = nxt_high_e["when"] if nxt_high_e else None
    nxt_high_label = (f"{nxt_high_e['ccy']} {nxt_high_e['title']}"
                      if nxt_high_e else None)

    out = {"fetched_at": now.isoformat(), "released": released,
           "news_score": news, "contributors": contrib,
           "next_release": nxt, "next_high_impact": nxt_high,
           "next_high_impact_event": nxt_high_label,
           "upcoming": upcoming[:30], "event_count": len(events)}
    (DATA / "calendar.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"  {len(events)} events, {len(released)} with actuals")
    print("  news: " + "  ".join(f"{c} {news[c]:+.0f}" for c in ORDER))
    print(f"  next: {nxt}   next high-impact: {nxt_high}")
    return out


if __name__ == "__main__":
    main()
