"""Check the model against realised price, per currency AND per pair.

Exists because the first build scored EUR strongest and AUD weakest while AUD was the best
performer - the COT component was reading backwards. A score that disagrees with price is not
automatically wrong (positioning can lead), but a NEGATIVE rank correlation across horizons
means the sign is broken, not early.

    python validate.py

Prices: Frankfurter (ECB daily reference rates, free, no key; needs a browser UA or 403).
Quotes are USD-base (1 USD = X ccy), so a FALL in the quote means the currency STRENGTHENED.
Getting that flip wrong is the easiest way to falsely "prove" an inverted model.

USD is included via the inverse basket - the dollar has no cross rate against itself, so its
move is the negative mean of the other six. Leaving it out meant the currency ranked #1 was
never actually being checked.
"""
import json, math, urllib.request, itertools, datetime as dt
from config import DATA, ORDER, COT_CATEGORY

FX_API = "https://api.frankfurter.dev/v1/{start}..{end}?base=USD&symbols={syms}"
XCCY = [c for c in ORDER if c != "USD"]
HORIZONS = [(5, "1 week"), (10, "2 weeks"), (None, "full window")]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def prices(days_back=40):
    end = dt.date.today()
    url = FX_API.format(start=end - dt.timedelta(days=days_back), end=end, syms=",".join(XCCY))
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40).read()
    return json.loads(raw.decode("utf-8", "replace"))["rates"]


def moves(fx, days, n):
    """% move of each currency vs USD, including USD itself as the inverse basket."""
    n = len(days) - 1 if n is None else n
    if len(days) <= n:
        return None
    a, b = fx[days[-1 - n]], fx[days[-1]]
    m = {c: -(b[c] - a[c]) / a[c] * 100 for c in XCCY if c in a and c in b}
    m["USD"] = -sum(m.values()) / len(m)
    return m


def pair_move(m, x, y):
    """Move of pair X/Y: how much X gained on Y, in percent."""
    return m[x] - m[y]


def spearman(sa, sb, keys):
    keys = [k for k in keys if k in sa and k in sb]
    n = len(keys)
    if n < 3:
        return float("nan")
    a = sorted(keys, key=lambda c: -sa[c])
    b = sorted(keys, key=lambda c: -sb[c])
    d2 = sum((a.index(c) - b.index(c)) ** 2 for c in keys)
    return 1 - 6 * d2 / (n * (n * n - 1))


def cot_variant(cot, cat):
    out = {}
    for c in ORDER:
        d = cot["currencies"].get(c, {})
        g, oi = d.get(cat), d.get("open_interest")
        if not g or not oi:
            continue
        out[c] = 0.6 * 100 * math.tanh(g["net"] / oi * 3.0) + \
                 0.4 * 100 * math.tanh(g["net_chg"] / oi * 20.0)
    return out


def commodity_check():
    """Commodity score vs its own realised move. Only 3 series, so this is a sign-agreement
    tally and the raw numbers - not a rank correlation, which would be noise at n=3. Kept
    out of the FX verdict entirely."""
    cpath, ppath = DATA / "commodities.json", DATA / "prices_commodity.json"
    if not (cpath.exists() and ppath.exists()):
        return
    cm = json.loads(cpath.read_text(encoding="utf-8"))
    px = json.loads(ppath.read_text(encoding="utf-8"))
    chz = [(5, "1 week"), (10, "2 weeks"), (20, "4 weeks")]   # trading days, own price feed
    print("\nCOMMODITIES  (score vs realised close-to-close move)")
    print(f"{'sym':4} {'score':>7}" + "".join(f"{l:>13}" for _, l in chz))
    tally = {lbl: [0, 0] for _, lbl in chz}
    for s in cm.get("ranked", []):
        closes = px.get("symbols", {}).get(s, {}).get("closes") or []
        sc = cm.get("commodities", {}).get(s, {}).get("score")
        if sc is None:
            continue
        row = f"{s:4} {sc:+7.1f}"
        for k, lbl in chz:
            if len(closes) <= k or closes[-1 - k] == 0:
                row += f"{'n/a':>13}"
                continue
            mv = (closes[-1] - closes[-1 - k]) / closes[-1 - k] * 100
            row += f"{mv:+12.2f}%"
            if abs(sc) >= 1:                        # only score a genuine directional call
                tally[lbl][0] += 1
                tally[lbl][1] += int((sc > 0) == (mv > 0))
        print(row)
    print("  direction agreement: " + "  ".join(
        f"{lbl} {h}/{n}" for lbl, (n, h) in ((l, tally[l]) for _, l in chz) if n)
        + "   (n=3, indicative only)")


def main():
    sc = json.loads((DATA / "scores.json").read_text(encoding="utf-8"))
    cot = json.loads((DATA / "cot.json").read_text(encoding="utf-8"))
    fx = prices()
    days = sorted(fx)

    blended = {c: sc["currencies"][c]["score"] for c in ORDER}
    # iterate the live weight keys so a newly added component cannot be silently untested
    parts = {k: {c: sc["currencies"][c]["parts"].get(k, 0.0) for c in ORDER}
             for k in sc.get("weights", {})}
    mv = {lbl: moves(fx, days, n) for n, lbl in HORIZONS}

    print(f"price window {days[0]} .. {days[-1]}  ({len(days)} observations)")
    print(f"COT report {cot['currencies']['EUR']['report_date']}  |  category: {COT_CATEGORY}\n")

    print("PER CURRENCY  (USD move = inverse basket of the other six)")
    print(f"{'ccy':4} {'score':>7}" + "".join(f"{l:>13}" for _, l in HORIZONS))
    for c in ORDER:
        row = f"{c:4} {blended[c]:+7.1f}"
        for _, lbl in HORIZONS:
            row += f"{mv[lbl][c]:+12.2f}%" if mv[lbl] else f"{'n/a':>13}"
        print(row)

    print(f"\nrank correlation vs realised move  (+1 aligned, -1 inverted)")
    print(f"{'':30}" + "".join(f"{l:>13}" for _, l in HORIZONS))
    variants = {"BLENDED SCORE": blended}
    variants.update({f"  component: {k}": v for k, v in parts.items()})
    variants["  COT only: asset_manager"] = cot_variant(cot, "asset_manager")
    variants["  COT only: leveraged"] = cot_variant(cot, "leveraged")
    for name, sv in variants.items():
        line = f"{name:30}"
        for _, lbl in HORIZONS:
            line += f"{spearman(sv, mv[lbl], ORDER):>13.2f}" if mv[lbl] else f"{'n/a':>13}"
        print(line)

    # ---- pair level: every combination, not just vs USD
    print("\nPER PAIR  (predicted = score spread; realised = actual pair move)")
    pairs = list(itertools.combinations(ORDER, 2))
    for _, lbl in HORIZONS:
        m = mv[lbl]
        if not m:
            continue
        rows = []
        for x, y in pairs:
            pred = blended[x] - blended[y]
            real = pair_move(m, x, y)
            rows.append((f"{x}/{y}", pred, real, (pred > 0) == (real > 0)))
        hits = sum(1 for r in rows if r[3])
        # correlation of predicted spread vs realised move across all pairs
        n = len(rows)
        mp = sum(r[1] for r in rows) / n
        mr = sum(r[2] for r in rows) / n
        cov = sum((r[1] - mp) * (r[2] - mr) for r in rows)
        sp = math.sqrt(sum((r[1] - mp) ** 2 for r in rows))
        sr = math.sqrt(sum((r[2] - mr) ** 2 for r in rows))
        corr = cov / (sp * sr) if sp and sr else float("nan")
        print(f"\n  {lbl}: {hits}/{n} pairs correct direction ({hits/n*100:.0f}%), "
              f"corr {corr:+.2f}")
        worst = sorted(rows, key=lambda r: -(abs(r[1]) if not r[3] else 0))[:5]
        worst = [w for w in worst if not w[3]]
        if worst:
            print("    biggest disagreements (score says one way, price went the other):")
            for name, pred, real, _ in worst:
                print(f"      {name:9} score spread {pred:+7.1f}  but price {real:+6.2f}%")

    try:
        commodity_check()
    except Exception as e:                          # never let a commodity issue hide the FX verdict
        print(f"\nCOMMODITIES  check skipped ({type(e).__name__}: {e})")

    worst_c = min((spearman(blended, mv[l], ORDER) for _, l in HORIZONS if mv[l]), default=0)
    print()
    if worst_c <= -0.5:
        print("  VERDICT: strongly inverted somewhere - suspect a sign error, not an early call.")
        print("           Check COT_CATEGORY in config.py first.")
    elif worst_c < 0:
        print("  VERDICT: mildly negative on one horizon. Normal for a positioning signal.")
    else:
        print("  VERDICT: no inversion detected on any horizon.")
    print("  Caveat: one COT snapshot, 7 currencies. Indicative, not a backtest.")


if __name__ == "__main__":
    main()
