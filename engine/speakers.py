"""Central-bank commentary: who is speaking, and what it meant.

WHY THIS EXISTS
---------------
The numeric pipeline scores `actual vs forecast`. A speech has no actual, so Fed/BoE/ECB
commentary contributed nothing to the score at all - even though guidance from a podium
moves FX as hard as a data print.

Worse, the feeds' own impact tiers are unreliable for commentary. Observed live:
    RBA Monetary Policy Meeting Minutes ... tagged Low
    Jackson Hole Symposium .............. tagged Medium
So this module deliberately IGNORES the feed's impact tier for speakers and weights by
SPEAKER SENIORITY instead - a Chair at a "Low"-tagged event outranks a regional president
at a "High"-tagged one.

TWO SOURCES, EACH FOR ITS STRENGTH
----------------------------------
  TradingView calendar - authoritative for numbers, thin on speakers
  ForexFactory weekly  - no `actual` field at all (useless for data), but much better
                         speaker coverage: it carries "Fed Chairman Warsh Speaks" and
                         "Jackson Hole Symposium" where TradingView carries neither
Both are merged here and de-duplicated.

TONE IS NOT AUTOMATIC
---------------------
What a speaker *said* cannot be derived from a calendar row - it needs reading. Tone lives
in data/speech_tone.json, scored -2 (very dovish) to +2 (very hawkish), filled by hand or by
a Claude run. Unscored events are listed as pending and contribute nothing rather than being
guessed at.

Writes data/speakers.json.
"""
import json, math, re, urllib.request, urllib.error, datetime as dt
from config import DATA, ORDER

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_CACHE = DATA / "ff_cache.json"
FF_ARCHIVE = DATA / "ff_archive.json"   # accumulates past weeks; the feed only serves current
FF_MAX_AGE_H = 6            # the feed 429s under repeated fetching - cache hard
TONE_FILE = DATA / "speech_tone.json"

COMMENTARY = ("speak", "speech", "testimon", "minutes", "press conf", "symposium",
              "remarks", "hearing", "panel", "forum", "address", "statement", "interview")
# things that match the keywords above but are data releases, not commentary
NOT_COMMENTARY = ("budget statement", "eco watchers", "survey", "index", "sales")

# Seniority drives the weight, not the feed's impact tag.
SENIORITY = [
    (1.00, ("chair", "chairman", "chairwoman", "governor of the", "bank governor",
            "president lagarde", "ecb president", "boj governor", "boe governor",
            "fed chair", "fomc minutes", "monetary policy report")),
    (0.85, ("minutes", "press conference", "monetary policy statement", "symposium",
            "jackson hole", "testimony", "semi-annual")),
    (0.70, ("president", "governor", "deputy", "vice", "chief economist", "treasury sec")),
    (0.45, ("board member", "member", "policymaker", "official")),
]
DEFAULT_SENIORITY = 0.35
# "Fed Barkin Speech" / "ECB Lane Speech": a named official of a central bank. The title
# never says "president", so without this they fall to the default and get under-weighted.
BANK_WORDS = ("fed", "fomc", "ecb", "boe", "boj", "rba", "rbnz", "boc", "snb")
SPEECH_WORDS = ("speech", "speaks", "remarks")
BANK_SPEAKER_WEIGHT = 0.55


def _is_bank_speaker(title):
    """'Fed Barkin Speech' - a named official of a central bank. The title never says
    "president", so without this it falls to the default and gets under-weighted."""
    words = [w.strip(".,:;()").lower() for w in title.split()]
    return any(w in BANK_WORDS for w in words) and any(w in SPEECH_WORDS for w in words)

CCY_FROM_FF = {"USD": "USD", "GBP": "GBP", "JPY": "JPY", "EUR": "EUR",
               "AUD": "AUD", "NZD": "NZD", "CAD": "CAD"}
# "All"-country events (Jackson Hole, G20) are read as USD-led
CCY_ALL = "USD"

HALFLIFE_H = 60             # guidance lingers longer than a data print
# Only events still inside the decay window are worth scoring. The bank calendars reach
# back years, so without this the pending list runs to hundreds of speeches that could no
# longer move the score even if scored. Four half-lives is ~10 days.
PENDING_WINDOW_DAYS = 12

# Finance ministries and treasuries move the currency, but they do NOT set policy rates.
# Scoring a Treasury Secretary into "CB stance" is a category error - Bessent's 24 Aug
# remarks were Iran sanctions, nothing to do with the rate path. These stay visible on the
# dashboard as commentary and are still tone-scorable, but they do not feed CB stance.
NON_CENTRAL_BANK = ("treasury sec", "treasury secretary", "finance minister", "chancellor",
                    "fin min", "economy minister", "budget", "president trump", "prime minister")


def is_central_bank(title):
    return not any(k in title.lower() for k in NON_CENTRAL_BANK)


def _seniority(title):
    t = title.lower()
    for w, keys in SENIORITY:
        if any(k in t for k in keys):
            return w
    if _is_bank_speaker(title):
        return BANK_SPEAKER_WEIGHT
    return DEFAULT_SENIORITY


def _is_commentary(title):
    t = title.lower()
    if any(k in t for k in NOT_COMMENTARY):
        return False
    return any(k in t for k in COMMENTARY)


def _ff_rows():
    """ForexFactory weekly feed, cached hard because it rate-limits."""
    now = dt.datetime.now(dt.timezone.utc)
    if FF_CACHE.exists():
        try:
            cached = json.loads(FF_CACHE.read_text(encoding="utf-8"))
            age = (now - dt.datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 3600
            if age < FF_MAX_AGE_H:
                return cached["rows"], f"cached {age:.1f}h"
        except Exception:
            pass
    try:
        req = urllib.request.Request(FF_URL, headers={"User-Agent": "Mozilla/5.0"})
        rows = json.loads(urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace"))
        FF_CACHE.write_text(json.dumps({"fetched_at": now.isoformat(), "rows": rows}), encoding="utf-8")
        return rows, "fetched"
    except Exception as e:
        if FF_CACHE.exists():
            return json.loads(FF_CACHE.read_text(encoding="utf-8"))["rows"], f"stale cache ({type(e).__name__})"
        return [], f"unavailable ({type(e).__name__})"


def _key(ccy, title, when):
    return f"{ccy}|{re.sub(r'\\s+', ' ', title).strip()}|{when[:16]}"


def build(calendar):
    now = dt.datetime.now(dt.timezone.utc)
    events = {}

    # --- TradingView side (already fetched into calendar.json)
    for e in calendar.get("released", []) + calendar.get("upcoming", []):
        if not _is_commentary(e["title"]):
            continue
        k = _key(e["ccy"], e["title"], e["when"])
        events[k] = {"ccy": e["ccy"], "title": e["title"], "when": e["when"],
                     "src": "tradingview", "feed_impact": e["impact"]}

    # --- ForexFactory side. The feed only ever serves the CURRENT week, so past
    # commentary used to disappear as the week rolled. Archive every row we see.
    ff_rows, ff_status = _ff_rows()
    try:
        archive = json.loads(FF_ARCHIVE.read_text(encoding="utf-8")) if FF_ARCHIVE.exists() else {}
    except Exception:
        archive = {}
    for e in ff_rows:
        archive[f"{e.get('country')}|{e.get('title')}|{e.get('date')}"] = e
    if archive:
        FF_ARCHIVE.write_text(json.dumps(archive, indent=0), encoding="utf-8")
    ff_rows = list(archive.values())
    for e in ff_rows:
        if not _is_commentary(e.get("title", "")):
            continue
        ccy = CCY_FROM_FF.get(e.get("country")) or (CCY_ALL if e.get("country") == "All" else None)
        if not ccy:
            continue
        try:
            when = dt.datetime.fromisoformat(e["date"]).astimezone(dt.timezone.utc).isoformat()
        except Exception:
            continue
        k = _key(ccy, e["title"], when)
        if k not in events:
            events[k] = {"ccy": ccy, "title": e["title"], "when": when,
                         "src": "forexfactory", "feed_impact": e.get("impact", "Low")}

    # --- the banks' own calendars: far more complete than either economic calendar
    try:
        import speakers_official
        official, off_status = speakers_official.all_events()
    except Exception as e:
        official, off_status = [], {"error": f"{type(e).__name__}"}
    for e in official:
        k = _key(e["ccy"], e["title"], e["when"])
        if k not in events:
            events[k] = dict(e)

    # The two feeds name the same event differently ("RBA Meeting Minutes" vs
    # "Monetary Policy Meeting Minutes"). Same currency + same minute = same event;
    # keep whichever title names the institution.
    BANKS = ("fed", "fomc", "ecb", "boe", "boj", "rba", "rbnz", "boc", "snb")
    by_slot = {}
    for k, e in events.items():
        slot = (e["ccy"], e["when"][:16])
        cur = by_slot.get(slot)
        if cur is None:
            by_slot[slot] = k
            continue
        def rank(key):
            t = events[key]["title"].lower()
            return (any(b in t for b in BANKS), -len(t))
        if rank(k) > rank(cur):
            by_slot[slot] = k
    events = {k: events[k] for k in by_slot.values()}

    tone = json.loads(TONE_FILE.read_text(encoding="utf-8")) if TONE_FILE.exists() else {}
    tone = {k: v for k, v in tone.items() if not k.startswith("_")}

    scored, pending, upcoming = [], [], []
    stance = {c: 0.0 for c in ORDER}

    for k, e in events.items():
        e["seniority"] = round(_seniority(e["title"]), 2)
        e["central_bank"] = is_central_bank(e["title"])
        e["key"] = k
        when = dt.datetime.fromisoformat(e["when"])
        age_h = (now - when).total_seconds() / 3600
        e["age_h"] = round(age_h, 1)

        if age_h < 0:
            upcoming.append(e)
            continue
        t = tone.get(k)
        if t is None:
            # too old to matter - keep it in the pool as history, but do not ask for a tone
            if age_h <= PENDING_WINDOW_DAYS * 24:
                pending.append(e)
            continue
        e["tone"] = float(t)
        e["central_bank"] = is_central_bank(e["title"])
        decay = 0.5 ** (age_h / HALFLIFE_H)
        e["points"] = round(float(t) / 2 * e["seniority"] * decay * 100, 1)
        # only actual central bankers move the CB-stance indicator
        if e["ccy"] in stance and e["central_bank"]:
            stance[e["ccy"]] += e["points"]
        scored.append(e)

    stance = {c: round(100 * math.tanh(v / 70), 1) for c, v in stance.items()}

    scored.sort(key=lambda x: -abs(x["points"]))
    pending.sort(key=lambda x: (-x["seniority"], x["when"]))
    upcoming.sort(key=lambda x: x["when"])

    out = {"built_at": now.isoformat(), "ff_status": ff_status, "official": off_status,
           "stance": stance, "scored": scored,
           "pending": pending[:20], "upcoming": upcoming[:20],
           "counts": {"total": len(events), "scored": len(scored),
                      "pending": len(pending), "upcoming": len(upcoming),
                      "pending_window_days": PENDING_WINDOW_DAYS}}
    (DATA / "speakers.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    cal = json.loads((DATA / "calendar.json").read_text(encoding="utf-8"))
    r = build(cal)
    c = r["counts"]
    print(f"  ForexFactory: {r['ff_status']}   bank calendars: {r['official']}")
    print(f"  {c['total']} commentary events - {c['scored']} scored, "
          f"{c['pending']} awaiting tone, {c['upcoming']} upcoming")
    print("  stance: " + "  ".join(f"{k} {v:+.0f}" for k, v in r["stance"].items()))
    if r["upcoming"]:
        print("\n  upcoming (weight = seniority, NOT the feed's impact tag):")
        for e in r["upcoming"][:8]:
            print(f"    {e['when'][:16]} {e['ccy']} w={e['seniority']:.2f} "
                  f"[feed says {e['feed_impact']:6}] {e['title']}")
    if r["pending"]:
        print("\n  awaiting a tone score:")
        for e in r["pending"][:8]:
            print(f"    {e['when'][:16]} {e['ccy']} w={e['seniority']:.2f} {e['title']}")
