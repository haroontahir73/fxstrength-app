"""Walk-forward test of the 'proven recurring reversal level' read (config._cot_levels).

For every weekly COT report from week ~160 on, rebuild the reversal clusters from the history
*before* that week, decide whether the speculative net is sitting on a proven level and moving
into it, then measure the currency's forward move over 2 / 4 / 8 / 12 weeks.

At a ceiling -> expect the currency to weaken; at a floor -> strengthen. The quote is USD-base
so a fall in the quote = currency strength; forward returns are signed so + = "the reversal
happened". Needs network: pulls a 10-year Frankfurter series once.

    python backtest_cot_levels.py
"""
import json, statistics, datetime as dt, urllib.request
from pathlib import Path
from config import _cot_levels, COT_CATEGORY

DATA = Path(__file__).parent / "data"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CCYS = ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD"]

H = json.loads((DATA / "cot_history.json").read_text(encoding="utf-8"))
DATES = sorted(H)
_start = DATES[0]
_end = (dt.date.fromisoformat(DATES[-1]) + dt.timedelta(weeks=14)).isoformat()
_url = (f"https://api.frankfurter.dev/v1/{_start}..{_end}"
        f"?base=USD&symbols={','.join(CCYS)}")
FX = json.loads(urllib.request.urlopen(urllib.request.Request(_url, headers=UA),
                                       timeout=60).read())["rates"]
FXD = sorted(FX)


def net_series(c):
    out = []
    for d in DATES:
        out.append((d, ((H[d].get(c) or {}).get(COT_CATEGORY) or {}).get("net")))
    return out


def fx_at(d, c):
    for x in FXD:
        if x >= d:
            return FX[x].get(c)
    return None


def fwd(d, c, weeks):
    a = fx_at(d, c)
    b = fx_at((dt.date.fromisoformat(d) + dt.timedelta(weeks=weeks)).isoformat(), c)
    return None if not a or not b else -(b - a) / a


def run(horizon):
    hits, baseline = [], []
    for c in CCYS:
        s = net_series(c)
        vals = [v for _, v in s]
        for i in range(160, len(s)):
            cur = vals[i]
            if cur is None:
                continue
            fr = fwd(s[i][0], c, horizon)
            if fr is None:
                continue
            baseline.append(fr)
            hist_v = vals[:i + 1]
            hist_d = [x for x, _ in s[:i + 1]]
            ceilings, floors = _cot_levels(hist_v, hist_d)
            full = [v for v in hist_v if v is not None]
            if len(full) < 5:
                continue
            trav = cur - full[-5]
            rng = (max(full) - min(full)) or 1
            tol = 0.10 * rng
            hit = None
            if cur > 0 and trav > 0:
                cand = [L for L in ceilings if abs(L["level"] - cur) <= tol]
                if cand:
                    hit = ("ceiling", min(cand, key=lambda L: abs(L["level"] - cur))["touches"])
            elif cur < 0 and trav < 0:
                cand = [L for L in floors if abs(L["level"] - cur) <= tol]
                if cand:
                    hit = ("floor", min(cand, key=lambda L: abs(L["level"] - cur))["touches"])
            if hit:
                w52 = [v for v in vals[i - 51:i + 1] if v is not None]
                e = 0.06 * ((max(w52) - min(w52)) or 1)
                at52 = (cur >= max(w52) - e) if cur > 0 else (cur <= min(w52) + e)
                hits.append((fr * (-1 if hit[0] == "ceiling" else 1), hit[1], at52))

    def show(rows, lbl):
        if not rows:
            return
        f = [x for x, *_ in rows]
        print(f"   {lbl:32} n={len(f):4}  favour {statistics.mean(f) * 100:+.2f}%  "
              f"hit {sum(1 for x in f if x > 0) / len(f) * 100:.0f}%")

    print(f"horizon {horizon}w:")
    show(hits, "all at-level")
    show([r for r in hits if r[1] >= 3], ">=3-touch")
    show([r for r in hits if r[1] >= 3 and not r[2]], ">=3-touch & not a 52wk extreme")
    print(f"   {'baseline (all weeks, |move|)':32} n={len(baseline):4}  "
          f"{statistics.mean(abs(x) for x in baseline) * 100:.2f}%")


if __name__ == "__main__":
    for hz in (2, 4, 8, 12):
        run(hz)
