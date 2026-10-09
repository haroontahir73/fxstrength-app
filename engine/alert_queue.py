"""A durable retry queue for phone alerts, shared by both watchers.

THE FAILURE THIS FIXES
----------------------
Both watchers marked a story as seen BEFORE trying to deliver it, and saved that dedup
state whether or not the push succeeded. So a failed alert was never retried: the hash
was on file, the next pass skipped the story, and the only trace was `pushed: false` on
a feed entry. The FX watcher was worse - it discarded push()'s return value entirely, so
nothing anywhere recorded that the phone had missed an alert.

Retrying inside push() cannot fix this on its own. ntfy rate-limits by IP and GitHub
runners share addresses, so the outage that loses an alert routinely outlasts the ~50s
of in-call retries; and the process itself can be cancelled mid-pass (an engine push
deliberately cancels the running watcher). A retry has to survive the process, which
means it has to be on disk.

HOW IT WORKS
------------
    enqueue(...)  - record an undelivered alert, with everything needed to resend it
    drain(send)   - called at the START of every pass: resend what is due, oldest first
    pending()     - what is still waiting, for the health report

Backoff is per entry and widening (2, 5, 15, 45 minutes, then hourly), capped by
MAX_ATTEMPTS and MAX_AGE_H - an alert whose moment has passed is dropped rather than
delivered hours late, because a stale "BREAKING" buzz is its own kind of wrong. Dropped
entries are kept in a small dead-letter list so the loss is still visible afterwards.

The queue file lives alongside the other watcher state and is committed with the feed by
the workflow, so a cancelled run does not lose it.
"""
import json, time
import datetime as dt
from pathlib import Path

DATA = Path(__file__).parent / "data"
QUEUE_FILE = DATA / "alert_queue.json"

BACKOFF_MIN = (2, 5, 15, 45)     # then hourly
MAX_ATTEMPTS = 8
MAX_AGE_H = 6.0                  # past this the alert is no longer news
DEAD_KEEP = 30


def _load():
    try:
        d = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"pending": [], "dead": []}
    if isinstance(d, list):                       # tolerate an older bare-list file
        return {"pending": d, "dead": []}
    d.setdefault("pending", [])
    d.setdefault("dead", [])
    return d


def _save(d):
    try:
        DATA.mkdir(exist_ok=True)
        d["dead"] = d.get("dead", [])[:DEAD_KEEP]
        QUEUE_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")
    except Exception as e:                                     # noqa: BLE001
        print(f"  could not write {QUEUE_FILE.name}: {type(e).__name__}")


def _next_due(attempts):
    mins = BACKOFF_MIN[attempts - 1] if attempts <= len(BACKOFF_MIN) else 60
    return time.time() + mins * 60


def enqueue(key, who, title, body, link="", priority="high", tags="rotating_light",
            topic_required=True, meta=None):
    """Record an alert the phone did not get, so a later pass can resend it.

    `key` identifies the alert (the story hash is ideal) and makes this idempotent: a
    second failure on the same alert updates the existing entry instead of queueing a
    duplicate.
    """
    d = _load()
    now = time.time()
    for e in d["pending"]:
        if e.get("key") == key:
            e["attempts"] = e.get("attempts", 1) + 1
            e["last_error"] = (meta or {}).get("error", e.get("last_error", ""))
            e["due_at"] = _next_due(e["attempts"])
            _save(d)
            return e
    entry = {"key": key, "who": who, "title": title, "body": body, "link": link,
             "priority": priority, "tags": tags, "topic_required": topic_required,
             "first_seen": now, "first_seen_iso": dt.datetime.now(dt.timezone.utc).isoformat(),
             "attempts": 1, "due_at": _next_due(1),
             "last_error": (meta or {}).get("error", ""), "meta": meta or {}}
    d["pending"].append(entry)
    _save(d)
    print(f"  queued for retry ({len(d['pending'])} waiting): {title[:60]}")
    return entry


def _retire(d, entry, why):
    entry["dropped_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    entry["dropped_why"] = why
    # the body is the bulky part and the title plus reason is what a health report needs
    d["dead"].insert(0, {k: entry.get(k) for k in
                         ("key", "who", "title", "attempts", "first_seen_iso",
                          "dropped_at", "dropped_why", "last_error")})


def drain(send, now=None):
    """Resend everything due. `send(entry) -> bool` does the actual delivery.

    Returns (delivered, still_pending, dropped). Call this at the start of a pass, before
    scanning for new stories, so a backlog goes out ahead of anything newer.
    """
    d = _load()
    now = now or time.time()
    keep, delivered, dropped = [], 0, 0
    for e in sorted(d["pending"], key=lambda x: x.get("first_seen", 0)):
        age_h = (now - e.get("first_seen", now)) / 3600
        if age_h > MAX_AGE_H:
            _retire(d, e, f"{age_h:.1f}h old - no longer news")
            dropped += 1
            continue
        if e.get("attempts", 0) >= MAX_ATTEMPTS:
            _retire(d, e, f"gave up after {e['attempts']} attempts")
            dropped += 1
            continue
        if now < e.get("due_at", 0):
            keep.append(e)
            continue
        try:
            ok = bool(send(e))
        except Exception as ex:                                # noqa: BLE001
            ok = False
            e["last_error"] = f"{type(ex).__name__}"
        if ok:
            delivered += 1
            print(f"  retry DELIVERED after {e.get('attempts', 1)} attempt(s)"
                  f" ({age_h * 60:.0f} min late): {e.get('title', '')[:60]}")
            continue
        e["attempts"] = e.get("attempts", 1) + 1
        e["due_at"] = _next_due(e["attempts"])
        keep.append(e)
    d["pending"] = keep
    _save(d)
    if delivered or dropped or keep:
        print(f"  retry queue: {delivered} delivered, {len(keep)} waiting, {dropped} dropped")
    return delivered, len(keep), dropped


def pending():
    return _load()["pending"]


def dead(since_h=24):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=since_h)
    out = []
    for e in _load()["dead"]:
        try:
            if dt.datetime.fromisoformat(e["dropped_at"]) >= cutoff:
                out.append(e)
        except Exception:                                      # noqa: BLE001
            continue
    return out


if __name__ == "__main__":
    p, dl = pending(), dead()
    print(f"{len(p)} pending, {len(dl)} dropped in the last 24h")
    for e in p:
        age = (time.time() - e.get("first_seen", 0)) / 60
        due = (e.get("due_at", 0) - time.time()) / 60
        print(f"  [{e.get('who')}] {e.get('title', '')[:58]}  "
              f"{age:.0f} min old, attempt {e.get('attempts')}, next in {due:.0f} min"
              + (f"  ({e['last_error']})" if e.get("last_error") else ""))
    for e in dl:
        print(f"  DROPPED [{e.get('who')}] {e.get('title', '')[:50]}  {e.get('dropped_why')}")
