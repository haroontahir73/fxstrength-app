"""COT x Myfxbook retail divergence (Step 2) - every setting in one place.

Nothing here is inferred at runtime. The orientation of each market is written out by hand
below and checked by test_divergence.py against cot_table (which futures are read inverted)
and myfxbook (which Myfxbook symbols Step 1 may pick). Change a threshold here and only
here.

This is a POSITIONING signal, not an execution signal. It is display only: it does not feed
the strength score, alerts, trading signals or the Pi.
"""

# ---------------------------------------------------------------- orientation (16 markets)
# key: the dashboard market (cot_table report key = myfxbook market key)
#   shown           the instrument the page shows; COT and retail both read in its terms
#   cot_future      the CFTC Legacy futures contract (cot_table row)
#   cot_orientation "direct"   - a long future is long the instrument shown
#                   "inverted" - a long future is SHORT the instrument shown (the future is
#                                the foreign currency in dollars; the pair is USD/xxx)
#   retail_symbols  the only Myfxbook symbols accepted for a divergence reading, each quoted
#                   exactly as the instrument shown (so Myfxbook long = long `shown`). If
#                   Step 1 picked any other symbol the mapping is treated as uncertain and
#                   no divergence is given.
#   note            why, in words
ORIENTATION = {
    "USD": {"shown": "USDX", "cot_future": "ICE US Dollar Index (098662)", "cot_orientation": "direct",
            "retail_symbols": ("USDX",),
            "note": "Reviewed: the ICE US Dollar Index future and Myfxbook USDX are both the dollar "
                    "against the six-currency DXY basket. A long future is long the index, so COT "
                    "bullish = USDX bullish. Only USDX is accepted; DXY is not (not reviewed)."},
    "AUD": {"shown": "AUDUSD", "cot_future": "CME Australian dollar (232741)", "cot_orientation": "direct",
            "retail_symbols": ("AUDUSD",), "note": "AUD futures bullish = AUDUSD bullish."},
    "CAD": {"shown": "USDCAD", "cot_future": "CME Canadian dollar (090741)", "cot_orientation": "inverted",
            "retail_symbols": ("USDCAD",), "note": "CAD futures bullish = USDCAD bearish."},
    "CHF": {"shown": "USDCHF", "cot_future": "CME Swiss franc (092741)", "cot_orientation": "inverted",
            "retail_symbols": ("USDCHF",), "note": "CHF futures bullish = USDCHF bearish."},
    "EUR": {"shown": "EURUSD", "cot_future": "CME euro FX (099741)", "cot_orientation": "direct",
            "retail_symbols": ("EURUSD",), "note": "EUR futures bullish = EURUSD bullish."},
    "GBP": {"shown": "GBPUSD", "cot_future": "CME British pound (096742)", "cot_orientation": "direct",
            "retail_symbols": ("GBPUSD",), "note": "GBP futures bullish = GBPUSD bullish."},
    "JPY": {"shown": "USDJPY", "cot_future": "CME Japanese yen (097741)", "cot_orientation": "inverted",
            "retail_symbols": ("USDJPY",), "note": "JPY futures bullish = USDJPY bearish."},
    "NZD": {"shown": "NZDUSD", "cot_future": "CME New Zealand dollar (112741)", "cot_orientation": "direct",
            "retail_symbols": ("NZDUSD",), "note": "NZD futures bullish = NZDUSD bullish."},
    "XAU": {"shown": "XAUUSD", "cot_future": "COMEX gold (088691)", "cot_orientation": "direct",
            "retail_symbols": ("XAUUSD",), "note": "Gold futures bullish = XAUUSD bullish."},
    "XAG": {"shown": "XAGUSD", "cot_future": "COMEX silver (084691)", "cot_orientation": "direct",
            "retail_symbols": ("XAGUSD",), "note": "Silver futures bullish = XAGUSD bullish."},
    "HG": {"shown": "COPPER", "cot_future": "COMEX copper (085692)", "cot_orientation": "direct",
           "retail_symbols": ("COPPER",), "note": "Copper futures bullish = COPPER bullish."},
    "WTI": {"shown": "XTIUSD / USOIL", "cot_future": "NYMEX WTI crude (067651)", "cot_orientation": "direct",
            "retail_symbols": ("XTIUSD", "USOIL"), "note": "WTI futures bullish = XTIUSD / USOIL bullish."},
    "ES": {"shown": "US500", "cot_future": "CME E-mini S&P 500 (13874A)", "cot_orientation": "direct",
           "retail_symbols": ("US500",), "note": "S&P 500 futures bullish = US500 bullish."},
    "NQ": {"shown": "NAS100", "cot_future": "CME E-mini Nasdaq-100 (209742)", "cot_orientation": "direct",
           "retail_symbols": ("NAS100",), "note": "Nasdaq-100 futures bullish = NAS100 bullish."},
    "YM": {"shown": "US30", "cot_future": "CBOT E-mini Dow $5 (124603)", "cot_orientation": "direct",
           "retail_symbols": ("US30",), "note": "Dow futures bullish = US30 bullish."},
    "ZB": {"shown": "UST Bonds", "cot_future": "CBOT U.S. Treasury bond (020601)", "cot_orientation": "direct",
           "retail_symbols": (),
           "note": "Myfxbook has no Treasury bond symbol, so there is never a divergence for ZB."},
}

# ---------------------------------------------------------------- the COT measure
# Non-Commercial net (long - short, the large speculators) in the instrument's terms:
#     normalized net = (NC long - NC short of the future) x (+1 direct, -1 inverted)
# ranked against the 50 weekly reports the market's 50-week report shows (latest included),
# mid-rank for ties - the same percentile_rank cot_table uses.
COT_WEEKS = 50                 # the 50-week window; fewer reports -> no divergence
COT_MAX_AGE_DAYS = 21          # latest CFTC report older than this -> no divergence

# ---------------------------------------------------------------- V1 classification
# Checked in this order; the first that holds wins. Percentiles in the normalized
# direction; retail % exactly as Myfxbook publishes them.
RULES = (
    # (class key,     label,            COT pct test,  value, retail side, retail % >=)
    ("strong_bull", "Strong Bullish", ">=", 85, "short", 70),
    ("strong_bear", "Strong Bearish", "<=", 15, "long", 70),
    ("bull",        "Bullish",        ">=", 70, "short", 60),
    ("bear",        "Bearish",        "<=", 30, "long", 60),
)
NONE_LABEL = "No significant divergence"

# ---------------------------------------------------------------- data quality
RETAIL_MAX_AGE_MIN = 180       # Myfxbook snapshot older than 3 h -> no divergence
# (the 100-position minimum is Step 1's myfxbook.MIN_POSITIONS, used as is)

# ---------------------------------------------------------------- display only
DIRECTION_BULL = 70            # COT direction label: >= this percentile "Bullish"
DIRECTION_BEAR = 30            # <= this "Bearish", otherwise "Neutral"
MOMENTUM_FLAT_PCT = 5.0        # |weekly change| < 5% of the 50-week net range -> Flat/neutral
