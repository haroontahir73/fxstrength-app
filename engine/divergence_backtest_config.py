"""Step 3 baseline backtest - every setting in one place.

The STRATEGY is not defined here: the replay calls divergence.evaluate() with the frozen
Step 2 configuration (divergence_config.py, as merged at 79b834a). This file only says how the
replay is measured. Nothing here selects or tunes a parameter.
"""

# Frozen strategy identity. The fingerprint of divergence_config (rules, orientation, limits)
# is computed at run time and must equal BASELINE_FINGERPRINT, or the run is labelled as NOT
# the baseline - so a later change to the live rules can never be reported as the baseline.
STRATEGY_VERSION = "step2-v1"
STRATEGY_COMMIT = "79b834a"
BASELINE_FINGERPRINT = "0bcac9b16befcd54"     # divergence_config at 79b834a (test_backtest checks)

# ---------------------------------------------------------------- forward returns
HORIZONS = (1, 3, 5, 10)        # trading days (= price bars of the instrument)
# Entry: the OPEN of the first daily bar dated AFTER the observation's UTC date - a price that
# is only known after the observation. Exit at horizon h: the CLOSE of the h-th bar from entry.
# MFE / MAE: best / worst high-low excursion over those h bars, in the signal's direction.
MAX_ENTRY_GAP_DAYS = 5          # first bar later than this (calendar days) -> "missing price"
MAX_BAR_GAP_DAYS = 5            # a gap between two bars longer than this -> "missing price"

# ---------------------------------------------------------------- events
# An event is a maximal run of consecutive snapshots in which one market has the SAME
# actionable class. It ends when the class changes (incl. Bearish -> Strong Bearish), when
# the signal disappears (no divergence / unavailable), or when two consecutive snapshots are
# more than EVENT_MAX_GAP_MIN apart (no evidence the condition held in between).
EVENT_MAX_GAP_MIN = 180         # = divergence_config.RETAIL_MAX_AGE_MIN (checked in tests)

# ---------------------------------------------------------------- price instruments
# One fixed instrument per market for the whole history - never switched between periods.
# Source: Yahoo Finance chart API (the project's existing daily price source), daily bars,
# OHLC as published, timestamps = the bar's trading date in the exchange's calendar.
PRICE_SOURCE = "Yahoo Finance chart API v8, interval=1d, OHLC (unadjusted)"
PRICES = {
    "USD": {"symbol": "DX-Y.NYB", "what": "ICE US Dollar Index (cash index)"},
    "AUD": {"symbol": "AUDUSD=X", "what": "AUD/USD spot"},
    "CAD": {"symbol": "USDCAD=X", "what": "USD/CAD spot"},
    "CHF": {"symbol": "USDCHF=X", "what": "USD/CHF spot"},
    "EUR": {"symbol": "EURUSD=X", "what": "EUR/USD spot"},
    "GBP": {"symbol": "GBPUSD=X", "what": "GBP/USD spot"},
    "JPY": {"symbol": "USDJPY=X", "what": "USD/JPY spot"},
    "NZD": {"symbol": "NZDUSD=X", "what": "NZD/USD spot"},
    "XAU": {"symbol": "GC=F", "what": "COMEX gold, continuous front month (rolls between contracts)"},
    "XAG": {"symbol": "SI=F", "what": "COMEX silver, continuous front month (rolls between contracts)"},
    "HG": {"symbol": "HG=F", "what": "COMEX copper, continuous front month (rolls between contracts)"},
    "WTI": {"symbol": "CL=F", "what": "NYMEX WTI crude, continuous front month (rolls between contracts)"},
    "ES": {"symbol": "^GSPC", "what": "S&P 500 cash index"},
    "NQ": {"symbol": "^NDX", "what": "Nasdaq-100 cash index"},
    "YM": {"symbol": "^DJI", "what": "Dow Jones Industrial Average cash index"},
    "ZB": None,                 # no Myfxbook data -> never a signal -> no price needed
}

# ---------------------------------------------------------------- sample-size language
# Descriptive labels only - they do NOT establish statistical significance. Counted in
# INDEPENDENT EVENTS (not snapshots) with a completed horizon.
SAMPLE_STATES = ((100, "Meaningful sample"), (30, "Developing sample"),
                 (10, "Early evidence"), (0, "Insufficient sample"))
CI_Z = 1.96                     # Wilson 95% interval for win rates

# Bands for the aggregate views (descriptive, not parameters of the strategy):
#   COT percentile: <= 15, 15-30, 30-70, 70-85, >= 85
#   retail share AGAINST the signal (short for bullish, long for bearish): < 60, 60-70, >= 70
