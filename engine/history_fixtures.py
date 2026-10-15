"""SYNTHETIC fixtures for the Historical Performance UI tests (test_history_panel.py,
check_layout.py). Built by running the real frozen engine (divergence_backtest.replay) on
synthetic inputs - never real Myfxbook data, never published.

    results("pending")    5 episodes like 14 Oct 2026 (AUD Bearish -> Strong Bearish = one
                          episode with two state events), no price bars yet -> all pending
    results("completed")  the same plus prices, so horizons complete; and a market whose
                          signal disappears and reappears (two episodes)
"""
import datetime as dt
import json

import cot_table as ct
import divergence_backtest as bt

SIGN = bt.dv.sign          # Step 2's own orientation (via the engine; the UI layer never imports divergence)

CODE = {r[3]: r[1] for r in ct.ALL_ROWS}
SYM = {"EUR": "EURUSD", "AUD": "AUDUSD", "GBP": "GBPUSD", "JPY": "USDJPY", "XAG": "XAGUSD", "NZD": "NZDUSD"}


def _store(ranks):
    s = ct.empty_store()
    last = dt.date(2026, 10, 6)
    ds = [(last - dt.timedelta(weeks=59 - i)).isoformat() for i in range(60)]
    for k, code in CODE.items():
        r = ranks.get(k, 25)
        seq = [0] * 10 + [i * 1000 for i in range(50) if i != r] + [r * 1000]
        s["contracts"][code] = {"name": k, "weeks": {d: [2000000, 600000 + max(v * SIGN(k), 0),
                                                         600000 + max(-v * SIGN(k), 0), 0, 0, 0, 0, 0]
                                                     for d, v in zip(ds, seq)}}
    return s


def _row(ts, k, lp, n=(900, 600)):
    return {"timestamp": ts, "symbol": SYM[k], "longPercentage": str(lp), "shortPercentage": str(100 - lp),
            "longVolume": "1", "shortVolume": "1", "longPositions": str(n[0]), "shortPositions": str(n[1]),
            "avgLongPrice": "1", "avgShortPrice": "1", "market": k}


def _bars(start, n, f):
    d = dt.date.fromisoformat(start)
    out = {"dates": [], "open": [], "high": [], "low": [], "close": [], "timezone": "Europe/London"}
    i = 0
    while i < n:
        if d.weekday() < 5:
            c = f(i)
            out["dates"].append(d.isoformat())
            out["open"].append(c + 0.1)
            out["close"].append(c)
            out["high"].append(c + 0.6)
            out["low"].append(c - 0.6)
            i += 1
        d += dt.timedelta(days=1)
    return out


def results(kind="pending"):
    ranks = {"EUR": 0, "AUD": 0, "GBP": 2, "JPY": 3, "XAG": 8, "NZD": 46}
    t0 = dt.datetime(2026, 10, 14, 9, 24, tzinfo=dt.timezone.utc)
    rows = []
    for i in range(20):
        ts = bt._iso(t0 + dt.timedelta(minutes=45 * i))
        rows.append(_row(ts, "EUR", 82))
        rows.append(_row(ts, "AUD", 69 if i < 7 else 71))         # Bearish, then Strong Bearish
        rows.append(_row(ts, "XAG", 64))
        if i >= 8:
            rows.append(_row(ts, "GBP", 61))
        if i >= 13:
            rows.append(_row(ts, "JPY", 61))
        if kind == "completed":
            rows.append(_row(ts, "NZD", 30 if (i < 5 or i >= 10) else 50))   # disappears, reappears
    # pending: bars only up to the signal day (14 Oct) - entry sessions not there yet
    n_bars = 30 if kind == "completed" else 3
    prices = {k: _bars("2026-10-12", n_bars, (lambda i, s=s: 100 + s * i))
              for k, s in (("EUR", -1), ("AUD", -1), ("XAG", 1), ("GBP", -1), ("JPY", -1), ("NZD", 1))}
    res = bt.replay(rows, _store(ranks), prices, asof="2026-11-30" if kind == "completed" else "2026-10-14",
                    production={r["timestamp"] for r in rows})
    manifest = {"generated_at": "2026-10-15T00:36:32Z", "code_commit": "5fceee5" + "0" * 33, "prices_asof": "2026-10-14",
                "strategy": {"version": "step2-v1", "fingerprint": bt.fingerprint(), "is_baseline": True}}
    # through JSON, exactly as the published results.json is read back (horizon keys "1".. )
    return manifest, json.loads(json.dumps(res, sort_keys=True))
