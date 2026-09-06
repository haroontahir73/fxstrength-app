"""Open-interest signal, behind a swappable source adapter.

WHY THIS IS AN ADAPTER
----------------------
The brief asked for DAILY open interest. As of build time no free, automatable daily
source was reachable from this machine:
    cmegroup.com          connection reset (both the daily bulletin and the data pages)
    marketwatch.com       401      wsj.com          401
    investing.com         404      barchart.com     404
    stooq.com             JavaScript proof-of-work wall
The paid options are Databento, Barchart OnDemand and CME's own API portal, all of
which need a key. TradingView carries futures OI but only via the desktop app, which
has to be running - too fragile for a scheduled job.

So SOURCE below defaults to "cot_weekly", which uses the open interest already carried
in the weekly COT report (free, reliable, level + weekly change per currency). To move
to daily, implement a function here and point SOURCE at it - nothing downstream changes.

THE SIGNAL
----------
Open interest on its own says nothing directional; it says how much conviction is behind
the move. Combined with the direction of net positioning it becomes readable:
    OI rising  + net long rising   -> new money buying          bullish
    OI rising  + net long falling  -> new money selling         bearish
    OI falling + net long falling  -> longs liquidating         fading, not reversing
"""
import json, math, datetime as dt
from config import DATA, ORDER, COT_CATEGORY

SOURCE = "cot_weekly"


def _from_cot(cot):
    out = {}
    for ccy in ORDER:
        d = cot["currencies"].get(ccy, {})
        oi, oi_chg, am = d.get("open_interest"), d.get("oi_change"), d.get(COT_CATEGORY)
        if not oi or oi_chg is None or not am:
            out[ccy] = {"score": 0.0, "note": "no data", "oi": oi, "oi_chg": oi_chg}
            continue
        prev = oi - oi_chg
        oi_pct = (oi_chg / prev) if prev else 0.0
        net_chg = am["net_chg"]
        direction = 1 if net_chg > 0 else (-1 if net_chg < 0 else 0)
        # How much new money, softened - `* 8` saturated at ~13% OI change, so any busy week
        # (a central-bank meeting, a squeeze) pinned this near +-93 regardless of content.
        conviction = math.tanh(abs(oi_pct) * 4)          # 0..1
        # ...but only to the extent the week's OI change was actually DIRECTIONAL. A 57k jump
        # in open interest with the net moving only 4k is new positions opening on both sides
        # (spreading / hedging), not conviction behind a direction. Scale by that ratio so a
        # non-directional OI surge cannot max the leg out (this is what inverted the board on
        # 2026-09-05: NZD short-covering and AUD/EUR OI surges all read near +-90).
        directionality = min(1.0, abs(net_chg) / max(abs(oi_chg), 1))
        score = direction * conviction * directionality * 100
        if oi_chg < 0:
            score *= 0.5                                  # liquidation is weaker evidence
            flow = "liquidation"
        else:
            flow = "new money"
        score = max(-75.0, min(75.0, score))              # one week never dominates the blend
        out[ccy] = {
            "score": round(score, 1), "oi": oi, "oi_chg": oi_chg,
            "oi_pct": round(oi_pct * 100, 2), "net_chg": net_chg, "flow": flow,
            "note": f"OI {oi:,} ({oi_chg:+,}, {oi_pct*100:+.1f}%), "
                    f"{COT_CATEGORY.replace('_', ' ')} net {net_chg:+,} - {flow} "
                    f"{'long' if direction > 0 else 'short' if direction < 0 else 'flat'}",
        }
    return out


def build(cot):
    if SOURCE != "cot_weekly":
        raise NotImplementedError(f"no adapter wired for SOURCE={SOURCE!r}")
    res = {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "source": SOURCE, "cadence": "weekly (COT report, Fri 15:30 ET)",
           "currencies": _from_cot(cot)}
    (DATA / "oi.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


if __name__ == "__main__":
    cot = json.loads((DATA / "cot.json").read_text(encoding="utf-8"))
    r = build(cot)
    for c in ORDER:
        print(f"  {c}  {r['currencies'][c]['score']:+6.1f}   {r['currencies'][c]['note']}")
