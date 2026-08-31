"""Shared configuration for the FX strength meter."""
from pathlib import Path

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

# CFTC codes on tradingster.com/cot/futures/fin/<code>
CURRENCIES = {
    "USD": {"name": "US Dollar",      "cot": "098662", "contract": "USD INDEX"},
    "EUR": {"name": "Euro",           "cot": "099741", "contract": "EURO FX"},
    "GBP": {"name": "British Pound",  "cot": "096742", "contract": "BRITISH POUND"},
    "JPY": {"name": "Japanese Yen",   "cot": "097741", "contract": "JAPANESE YEN"},
    "AUD": {"name": "Australian Dlr", "cot": "232741", "contract": "AUSTRALIAN DOLLAR"},
    "NZD": {"name": "NZ Dollar",      "cot": "112741", "contract": "NZ DOLLAR"},
    "CAD": {"name": "Canadian Dlr",   "cot": "090741", "contract": "CANADIAN DOLLAR"},
}
ORDER = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD"]

# Commodities are NOT currencies - no central bank, no policy rate, no CPI/jobs checklist,
# and not zero-sum against each other - so they get their own lightweight track rather than
# a seat on the FX board. Only what transfers cleanly is scored: CFTC positioning (the
# DISAGGREGATED report, Managed Money = the speculative money), open interest, and price
# trend. See commodities.py. CFTC codes on tradingster.com/cot/futures/disagg/<code>.
COMMODITIES = {
    "XAU": {"name": "Gold",       "cot": "088691", "yahoo": "GC=F"},
    "XAG": {"name": "Silver",     "cot": "084691", "yahoo": "SI=F"},
    "WTI": {"name": "Crude Oil",  "cot": "067651", "yahoo": "CL=F"},
}
COMMODITY_ORDER = ["XAU", "XAG", "WTI"]

# Blend for the commodity bias. Sums to 1.0. NOT backtested the way the FX blend is.
# Weighting rationale, 2026-08-30 (revised same day after the first cut leaned too hard on
# positioning): TREND leads, because momentum is the one edge with real empirical support in
# commodities and it references the same front-month contract the COT is on. COT is cut to
# 0.25 - close to the FX side's 0.15 - for the reason the FX backtest already established:
# a large Managed Money net is contrarian at the extremes ("crowded positioning precedes
# reversal"), so it earns its keep as context and as the `crowded` / `pullback` flags, not as
# the main directional call. Overlay carries a real share because the macro backdrop (real
# yields, the dollar, OPEC+, demand) is independent information. Revisit once validate.py has
# a few weeks of commodity price history behind it.
COMMODITY_WEIGHTS = {
    "trend":   0.35,   # front-month close vs 50-day MA (60) + 20-day change (40)
    "cot":     0.25,   # Managed Money net %OI (60) + weekly flow (40) - contrarian when crowded
    "oi":      0.15,   # open-interest conviction vs positioning direction
    "overlay": 0.25,   # manual macro overlay - real yields / DXY / OPEC+ / demand / safe-haven
}

# The fundamentals checklist: 7 categories, each scored 1-5 and equally weighted (a category's
# 1/7 share does not change with how many indicators sit under it). Started as 7x4=28; grew to
# 31 on 2026-08-30 with consumer confidence, retail sales and the fiscal balance - the
# FX-relevant drivers that were only reaching the score through the news leg, if at all.
CHECKLIST = {
    "Interest Rates": ["Policy rate", "Rate differential vs USD", "Expected rate change", "Real yield"],
    "Economic Growth": ["GDP rate", "GDP surprise", "PMI Manufacturing", "PMI Services",
                        "Consumer confidence", "Retail sales"],
    "Inflation": ["CPI YoY", "Inflation vs target", "Core inflation", "Inflation trend"],
    "Employment": ["Unemployment rate", "Job creation", "Wage growth", "Labour participation"],
    "Trade Balance": ["Trade balance", "Export growth", "Import growth", "Current account"],
    "Central Bank": ["CB stance", "Next meeting expectation", "QE status", "FX intervention risk"],
    "Geopolitics & Risk": ["Political stability", "Risk premium", "Safe haven status",
                           "Debt level", "Fiscal balance"],
}

# WHICH COT TRADER CATEGORY DRIVES THE DIRECTIONAL SIGNAL
# --------------------------------------------------------
# "asset_manager" was the original choice and it reads BACKWARDS for FX. Real-money
# managers hold foreign assets and short the currency future to HEDGE them, so they sit
# structurally short currencies that are rallying. Measured against realised price on
# 2026-08-24 (rank correlation of COT score vs currency move):
#       horizon   asset_manager   leveraged
#       1 week        -0.09         +0.49
#       2 weeks       -0.60         +0.94
#       5 weeks       +0.26         +0.14
# Leveraged Funds is the speculative, directional money - the FX analogue of Managed Money
# in the commodity report. That is what belongs in a momentum-style positioning score.
# NOTE: one weekly snapshot across 6 currencies, not a backtest. Re-check with validate.py.
COT_CATEGORY = "leveraged"

# Blend weights for the final bias. Must sum to 1.0.
# Backtested 2026-08-26 over 14 weekly COT reports, entered at the Friday release and
# measured on FORWARD returns (see backtest.py). Mean forward rank correlation:
#       hold    asset_manager   leveraged
#        5d        -0.003         +0.107
#       10d        -0.184         -0.118
#       28d        -0.271         -0.261
# Leveraged beats asset_manager at every horizon, which supports COT_CATEGORY above, but
# neither PREDICTS next-week returns - and both turn contrarian as the horizon extends,
# i.e. crowded positioning precedes reversal. So COT's weight is cut from 0.30 to 0.15;
# it earns its place as context and as the `crowded` squeeze flag, not as a directional call.
# Small sample (10-14 observations). Re-run backtest.py as history accumulates.
# Second backtest (backtest_blend.py, same 14 weeks, forward returns) tested the WHOLE
# signal rather than just COT. Mean forward rank correlation, and t across 14 weeks:
#       news only         +0.194   11/14 positive   t +2.04   <- the only component that lands
#       blended           +0.043    8/14            t +0.43
#       cot only          +0.020    7/14            t +0.23
#       checklist only    -0.117    4/14            t -1.12   <- actively negative
# Note the AUTO half of the checklist derives from the SAME calendar surprises as news, but
# without time decay - so it is largely a STALE COPY of the news leg, which is the likeliest
# reason it scores worse. Weight therefore shifts from checklist to news. The manual half of
# the checklist (stances, safe-haven, debt) is NOT in that backtest and is genuinely
# independent information, so fundamentals keeps a substantial share.
# 14 observations and t just over 2 - a moderate shift, not a dramatic one. Re-run both
# backtests as history accumulates before moving these again.
# Rate expectations were promoted out of the checklist on 2026-08-27. Reasoning, stated
# plainly because this one is NOT backtested: market-implied policy odds are the most direct
# statement available about a currency's future carry, and rate differentials are the most
# established driver in FX. As 2 boxes out of 28 they were worth 2.5% of the total score,
# which is a structural mis-weighting rather than a tuning choice. Sized at 0.10 - modest,
# because there is no history to test it against yet. data/expectations_history.json now
# accumulates a snapshot per run so this can be backtested properly in a few weeks; revisit
# the weight then rather than arguing about it.
WEIGHTS = {
    "fundamentals": 0.25,   # the 31-indicator checklist (manual half is the real value)
    "expectations": 0.10,   # market-implied hike/hold/cut odds for the next meeting
    "cot":          0.15,   # positioning - contrarian at longer horizons, weak short-term
    "oi":           0.10,   # open interest conviction
    "news":         0.40,   # surprise vs forecast, time-decayed - best-evidenced component
}

# How long a news surprise keeps influencing the bias.
NEWS_HALFLIFE_HOURS = 36
NEWS_IMPACT_WEIGHT = {"High": 1.0, "Medium": 0.45, "Low": 0.15}

# Calibrated to the CENTRED score distribution. The original bands (+/-25, +/-60) were set
# for uncentred scores; after centring the spread collapsed to about +/-15 and every currency
# read "Neutral", making the column useless. These thresholds put roughly the top and bottom
# of a typical board outside neutral while keeping a genuine middle.
# score_history.json accumulates every rebuild - once there are a few hundred rows, recalibrate
# these to real percentiles (see tools/calibrate note in README) rather than to judgement.
RATING_BANDS = [
    (14,  "Very Strong Bullish", "vsb"),
    (5,   "Moderately Bullish",  "mb"),
    (-5,  "Neutral",             "neu"),
    (-14, "Moderately Bearish",  "mbr"),
    (-201,"Very Weak / Bearish", "vwb"),
]

def rating(score: float):
    for floor, label, cls in RATING_BANDS:
        if score >= floor:
            return label, cls
    return "Neutral", "neu"


# Commodity scores are NOT centred - a commodity can be legitimately, persistently long or
# short with nothing on the other side of the board to net against - so the FX bands (set
# for a distribution that sums to zero and spans about +/-15) would pin every commodity at
# the extremes. These are wider, judged against the uncentred -100..+100 range.
COMMODITY_RATING_BANDS = [
    (45,   "Very Strong Bullish", "vsb"),
    (18,   "Moderately Bullish",  "mb"),
    (-18,  "Neutral",             "neu"),
    (-45,  "Moderately Bearish",  "mbr"),
    (-201, "Very Weak / Bearish", "vwb"),
]


def commodity_rating(score: float):
    for floor, label, cls in COMMODITY_RATING_BANDS:
        if score >= floor:
            return label, cls
    return "Neutral", "neu"


# How the blended bias sits against the last week of price:
#   directional   price is moving WITH the bias - trend running
#   retracement   price is moving AGAINST the bias by a meaningful amount - a possible
#                 pullback within the trend (buy-the-dip / sell-the-rally watch, not a reversal)
#   holding       a directional bias but price roughly flat this week
#   none          no directional edge (score inside the neutral band)
# dir_floor / retr_floor differ by asset: FX scores are centred (~+/-15) and FX weekly moves
# are small; commodity scores are uncentred and moves are larger.
READ_THRESHOLDS = {"fx": (5.0, 0.35), "commodity": (18.0, 1.5)}


def directional_read(score: float, chg_5d_pct, kind: str = "fx"):
    dir_floor, retr_floor = READ_THRESHOLDS[kind]
    if abs(score) < dir_floor:
        return {"state": "none", "tag": "", "cls": "neu",
                "label": "Neutral — no directional edge"}
    bias = "Bullish" if score > 0 else "Bearish"
    cls = "pos" if score > 0 else "neg"
    if chg_5d_pct is None:
        return {"state": "unknown", "tag": "", "cls": cls,
                "label": f"{bias} bias — no recent price feed to classify"}
    if abs(chg_5d_pct) < retr_floor:
        return {"state": "holding", "tag": "holding", "cls": cls,
                "label": f"{bias} — directional, price holding this week"}
    with_trend = (chg_5d_pct > 0) == (score > 0)
    if with_trend:
        return {"state": "directional", "tag": "directional", "cls": cls,
                "label": f"{bias} — directional, trend running ({chg_5d_pct:+.1f}% 5d)"}
    return {"state": "retracement", "tag": "retracement", "cls": "warn",
            "label": f"Possible {bias.lower()} retracement ({chg_5d_pct:+.1f}% 5d against the bias)"}


# A retracement leg must be at least this big to be worth a zone (% of the level).
_LEG_MIN_PCT = {"fx": 1.2, "commodity": 4.0}


def _pctl(vals, cur):
    lo, hi = min(vals), max(vals)
    return round((cur - lo) / (hi - lo) * 100) if hi > lo else 50


def ordinal(n):
    if n is None:
        return "n/a"
    suf = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


# The COT-extreme flag can PULL THE SCORE toward a reversal, not just show a chip - user's
# call 2026-08-31, weighting the contrarian read above the model default (which keeps
# positioning as momentum because the 513-week backtest shows COT does not TIME reversals to
# the week - extremes can persist and deepen for months). The pull, as (fraction-of-|score|,
# floor): a net that is merely FLAGGED (`stretched`, or sitting on a proven reversal level)
# gets a nudge; one that is already UNWINDING (`turning` - the reversal in motion) gets a
# firmer shove. Sized to move a mid-strength name by ~8-13 on the -100..100 scale.
COT_REVERSAL_PULL = {
    "fx":        {"stretched": (0.30, 3.0),  "turning": (0.50, 5.0)},
    "commodity": {"stretched": (0.30, 8.0),  "turning": (0.50, 14.0)},
}


def cot_reversal_adjust(score, cot_x, kind: str = "fx"):
    """(adjusted score, adjustment) after the COT-extreme contrarian pull. Direction is set
    by WHICH SIDE is stretched - crowded longs pull the score DOWN, crowded shorts pull it UP
    - regardless of the score's own sign. A hit on a proven multi-touch reversal level scales
    the pull by how many times the level has held (2x -> 0.6, 4x+ -> 1.6). Returns (score,
    0.0) when there is no flag."""
    if not cot_x or not cot_x.get("state"):
        return round(score, 1), 0.0
    st = cot_x["state"]
    key = "turning" if st in ("long unwinding", "short covering") else "stretched"
    frac, floor = COT_REVERSAL_PULL.get(kind, COT_REVERSAL_PULL["fx"])[key]
    lvl = cot_x.get("level")
    mult = 1.0
    if lvl and st in ("at long ceiling", "at short floor"):
        mult = min(1.6, max(0.6, 0.6 + 0.35 * (lvl["touches"] - 2)))
    long_side = st in ("stretched long", "long unwinding", "at long ceiling")
    adj = (-1.0 if long_side else 1.0) * mult * (abs(score) * frac + floor)
    return round(score + adj, 1), round(adj, 1)


def _span(weeks):
    if weeks >= 100:
        return f"~{round(weeks / 52)} years"
    if weeks >= 70:
        return "~18 months"
    return f"{weeks} weeks"


def _cot_levels(vals, dates=None, k: int = 5, edge: float = 0.25,
                min_rev: float = 0.30, min_touch: int = 2):
    """Proven recurring reversal levels in the net series - the horizontal support/resistance
    of speculative positioning. A pivot counts only if it is a local extreme in the top/bottom
    `edge` of the whole-history range AND was followed within ~3 months by a reversal of at
    least `min_rev` of that range. Pivots within 10% of the range are one level; a level needs
    `min_touch` pivots to be "proven". Returns (ceilings, floors), each
    [{level, touches, last}], sorted low->high / high->low by |level|.

    Walk-forward test over 10y x 6 currencies: a net sitting on a >=3-touch level led the
    reversal by ~+0.9% over the next 8 weeks (66% directional); 2-touch levels showed almost
    nothing, which is why `cot_reversal_adjust` scales the pull by touch count.
    """
    v = [x for x in (vals or []) if x is not None]
    n = len(v)
    if n < 90:
        return [], []
    dts = dates if (dates and len(dates) == len(vals)) else None
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    hz, lz = hi - edge * rng, lo + edge * rng
    tol = 0.10 * rng
    highs, lows = [], []
    for i in range(k, n - k):
        w = v[i - k:i + k + 1]
        fut = v[i + 1:i + 13]
        if not fut:
            continue
        di = dts[i] if dts else None
        if v[i] == max(w) and v[i] >= hz and (v[i] - min(fut)) >= min_rev * rng:
            highs.append((v[i], di))
        if v[i] == min(w) and v[i] <= lz and (max(fut) - v[i]) >= min_rev * rng:
            lows.append((v[i], di))

    def clump(pts, descend):
        pts = sorted(pts, key=lambda p: p[0])
        groups = []
        for val, d in pts:
            if groups and val - groups[-1][-1][0] <= tol:
                groups[-1].append((val, d))
            else:
                groups.append([(val, d)])
        out = []
        for g in groups:
            if len(g) < min_touch:
                continue
            last = max((d for _, d in g if d), default=None)
            out.append({"level": round(sum(x for x, _ in g) / len(g)),
                        "touches": len(g), "last": last})
        out.sort(key=lambda L: -abs(L["level"]) if descend else abs(L["level"]))
        return out

    return clump(highs, True), clump(lows, True)


def cot_extreme(nets, dates=None, flag_window: int = 52, turn_window: int = 26,
                turn_recent: int = 6):
    """The contrarian positioning read: a speculative net position at a multi-year EXTREME,
    or back on a level it has reversed from before, tends to precede a trend change - the more
    so once it starts to unwind.

    nets : weekly speculative-net values (Leveraged Funds for FX, Managed Money for
    commodities), oldest first, the current week LAST. dates : parallel ISO week dates,
    optional, used only to date the proven levels in the note. Returns None with < 12 weeks.

    States, in priority order:
      - `long unwinding` / `short covering` - the TURN: a `turn_window`-week (default 26)
        extreme was hit in the last `turn_recent` weeks and the net has since unwound >= 15%
        of that window's range - the point the trend change usually shows up;
      - `stretched long` / `stretched short` - the FLAG: net at/near its high/low over the
        last `flag_window` weeks (default 52 = a 1-year extreme) while genuinely on that side;
      - `at long ceiling` / `at short floor` - net within 10% of range of a proven multi-touch
        reversal level (`_cot_levels`) and moving into it, even if not a 1-year extreme.

    Percentiles over 1y / 3y / 5y / all history are reported for context. Sign-aware:
    "least short in the window" is NOT a stretched long.
    """
    full = [x for x in (nets or []) if x is not None]
    if len(full) < 12:
        return None
    cur = full[-1]
    p1y = _pctl(full[-52:], cur) if len(full) >= 30 else None
    p3y = _pctl(full[-156:], cur) if len(full) >= 60 else None
    p5y = _pctl(full[-260:], cur) if len(full) >= 60 else None
    pmax = _pctl(full, cur)

    fw = full[-flag_window:]
    flo, fhi = min(fw), max(fw)
    frng = fhi - flo or 1
    span = _span(len(fw))
    edge = 0.06 * frng

    state, note, extreme = "", "", None
    if fhi > 0 and cur > 0 and cur >= fhi - edge:
        extreme, state = "long", "stretched long"
        note = (f"speculative net long at its highest in {span} — {ordinal(p1y)} percentile over 1 year, {ordinal(p3y)} over 3, {ordinal(pmax)} over {round(len(full)/52)} — "
                f"positioning stretched, a trend change often starts from here")
    elif flo < 0 and cur < 0 and cur <= flo + edge:
        extreme, state = "short", "stretched short"
        note = (f"speculative net short at its lowest in {span} — {ordinal(p1y)} percentile over 1 year, {ordinal(p3y)} over 3, {ordinal(pmax)} over {round(len(full)/52)} — "
                f"positioning stretched, a squeeze / trend change often starts from here")

    # the TURN check (its own shorter window)
    tw = full[-turn_window:]
    if len(tw) >= turn_recent + 3:
        tlo, thi = min(tw), max(tw)
        trng = thi - tlo or 1
        peaked_ago = len(tw) - 1 - tw.index(thi)
        troughed_ago = len(tw) - 1 - tw.index(tlo)
        off_peak = (thi - cur) / trng * 100
        off_trough = (cur - tlo) / trng * 100
        if state != "stretched long" and thi > 0 and peaked_ago <= turn_recent and off_peak >= 15 and cur > tlo + 0.2 * trng:
            state = "long unwinding"
            note = (f"speculative net long peaked {peaked_ago} week(s) ago at a {turn_window}-week high "
                    f"and has since unwound {off_peak:.0f}% of that range — crowded longs leaving, "
                    f"elevated trend-change risk")
        elif state != "stretched short" and tlo < 0 and troughed_ago <= turn_recent and off_trough >= 15 and cur < thi - 0.2 * trng:
            state = "short covering"
            note = (f"speculative net short troughed {troughed_ago} week(s) ago at a {turn_window}-week "
                    f"low and has since covered {off_trough:.0f}% of that range — short squeeze / "
                    f"trend-change risk")

    # --- proven recurring reversal levels: is the net sitting on one it has turned from before?
    ceilings, floors = _cot_levels(nets, dates)
    rng_full = (max(full) - min(full)) or 1
    ltol = 0.10 * rng_full
    trav = cur - full[-5] if len(full) >= 5 else 0.0
    level = None
    if cur > 0 and trav > 0:
        cand = [L for L in ceilings if abs(L["level"] - cur) <= ltol]
        if cand:
            level = {**min(cand, key=lambda L: abs(L["level"] - cur)), "kind": "ceiling"}
    elif cur < 0 and trav < 0:
        cand = [L for L in floors if abs(L["level"] - cur) <= ltol]
        if cand:
            level = {**min(cand, key=lambda L: abs(L["level"] - cur)), "kind": "floor"}

    if level:
        held = (f"{level['touches']}× (last {level['last']})"
                if level.get("last") else f"{level['touches']} times")
        word = "capped" if level["kind"] == "ceiling" else "floored"
        side = "net long" if level["kind"] == "ceiling" else "net short"
        lnote = (f"speculative {side} back on ~{level['level']:+,}, a level that has {word} "
                 f"positioning {held} — reversal risk builds over the coming weeks")
        if not state:
            state = "at long ceiling" if level["kind"] == "ceiling" else "at short floor"
            note = lnote
        else:
            note = f"{note}. Also: {lnote}"

    return {"pctl": p1y, "pctl_1y": p1y, "pctl_3y": p3y, "pctl_5y": p5y, "pctl_max": pmax,
            "hist_weeks": len(full), "flag_weeks": len(fw),
            "extreme": extreme, "state": state, "note": note,
            "level": level, "ceilings": ceilings[:3], "floors": floors[:3]}


_LEG_LOOKBACK = {"fx": 48, "commodity": 75}       # FX drifts; a shorter window keeps the leg current


def retracement_zone(series, score, kind: str = "fx", dates=None):
    """The Fibonacci dip-buy / rally-sell band for a pullback WITHIN the current leg.

    series : strength index (FX) or price (commodity), oldest first. dates : parallel ISO
    dates, optional, recorded on the swing points for auditability.
    The leg is found dynamically: for a bullish bias, the lowest point in the window and the
    highest point SINCE that low (mirror for bearish). Returns None with no directional edge,
    too little history, or no leg >= `_LEG_MIN_PCT`. `retraced_pct` says how far price has
    already pulled back; when that is under ~8% the band is a reference for a deeper pullback,
    not an entry that is close.
    """
    dir_floor = READ_THRESHOLDS[kind][0]
    lookback = _LEG_LOOKBACK[kind]
    if not series or len(series) < 25 or score is None or abs(score) < dir_floor:
        return None
    off = max(0, len(series) - lookback)
    w = series[off:]
    wd = dates[off:] if dates and len(dates) == len(series) else None
    cur = series[-1]
    bull = score > 0
    if bull:
        i_lo = min(range(len(w)), key=lambda i: w[i])
        seg = w[i_lo:]
        lo, hi = w[i_lo], max(seg)
        i_hi = i_lo + seg.index(hi)
    else:
        i_hi = min(range(len(w)), key=lambda i: -w[i])
        seg = w[i_hi:]
        hi, lo = w[i_hi], min(seg)
        i_lo = i_hi + seg.index(lo)
    rng = hi - lo
    # need a real leg: an early extreme, a later opposite extreme, minimum size. The leg's
    # peak CAN be the last bar (price still making highs) - that reports as "no pullback yet".
    if rng <= 0 or rng / cur * 100 < _LEG_MIN_PCT[kind]:
        return None
    if bull and i_hi <= i_lo:
        return None
    if (not bull) and i_lo <= i_hi:
        return None
    ma20 = sum(series[-20:]) / 20
    if bull:
        f382, f50, f618 = hi - 0.382 * rng, hi - 0.5 * rng, hi - 0.618 * rng
        retraced = (hi - cur) / rng * 100
        word = "dip-buy"
    else:
        f382, f50, f618 = lo + 0.382 * rng, lo + 0.5 * rng, lo + 0.618 * rng
        retraced = (cur - lo) / rng * 100
        word = "sell-rally"
    near_pct = (f382 - cur) / cur * 100
    far_pct = (f618 - cur) / cur * 100
    if retraced >= 78:
        state = "overshot"                        # past 61.8% - starting to look like a reversal
    elif min(near_pct, far_pct) <= 0 <= max(near_pct, far_pct):
        state = "in zone"
    elif retraced < 8:
        state = "no pullback yet"                  # leg still extending - band is deeper support
    else:
        state = "approaching"
    return {
        "word": word, "state": state, "retraced_pct": round(retraced),
        "swing_lo": round(lo, 3), "swing_hi": round(hi, 3),
        "swing_lo_at": wd[i_lo] if wd else None, "swing_hi_at": wd[i_hi] if wd else None,
        "f382": round(f382, 3), "f50": round(f50, 3), "f618": round(f618, 3),
        "ma20": round(ma20, 3),
        "band_near_pct": round(near_pct, 1), "band_far_pct": round(far_pct, 1),
        "ma20_pct": round((ma20 - cur) / cur * 100, 1),
    }
