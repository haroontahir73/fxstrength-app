"""Options Futures-Equivalent OI - market mapping and CFTC source identifiers (PR #12).

WHAT THIS LAYER IS (read before touching the numbers)
-----------------------------------------------------
This is NOT exchange option-contract open interest. CME publishes call/put/strike OI only in
its licensed Daily Bulletin, which we may not automate or redistribute (see README, "Options
Futures-Equivalent OI"). Instead we use the CFTC's own public Commitments of Traders reports.

The CFTC publishes every market twice each week, from the SAME positions, under the SAME
report family:
  * Futures-Only          - open interest in the futures contract alone.
  * Futures-and-Options-Combined - the CFTC folds each trader's option position into the
    futures contract it is an option ON, converting it to a futures-equivalent number of
    contracts using the exchange-supplied delta, and adds it to the futures open interest.

So, for one market and one weekly report date:

    Options futures-equivalent OI = Combined open_interest_all - Futures-Only open_interest_all

is the delta-adjusted options exposure, expressed in futures-contract-equivalents. It is a
WEEKLY, DELTA-ADJUSTED measure, not a count of outstanding option contracts, and it says
nothing about calls vs puts, strikes or expiries - those need a licensed options-chain feed.

MAPPING REUSE
-------------
The CFTC contract codes come straight from config.py (the COT system's verified mapping) so
there is exactly ONE market mapping in the project. The only market the COT config does not
already carry is COMEX Copper, added here as 085692 (COPPER- #1 - COMMODITY EXCHANGE INC.).

FAMILIES
--------
FX currency futures are financial contracts -> the Traders in Financial Futures (TFF) report.
Metals and oil are physical commodities -> the Disaggregated report. Futures-only and combined
MUST be read from the same family; the two are never mixed. Dataset ids verified live on
2026-10-15 (all four refreshed 2026-10-09):

    TFF          - Futures Only  gpe5-46if   Combined  yw9f-hn96
    Disaggregated- Futures Only  72hh-3qpy   Combined  kh3c-gbw2
"""
import config as _cfg

SCHEMA_VERSION = 1

# CFTC Socrata resource ids, per report family: (futures-only, futures-and-options-combined).
SOURCES = {
    "TFF":    {"futonly": "gpe5-46if", "combined": "yw9f-hn96", "report": "Traders in Financial Futures"},
    "DISAGG": {"futonly": "72hh-3qpy", "combined": "kh3c-gbw2", "report": "Disaggregated"},
}
SOCRATA = "https://publicreporting.cftc.gov/resource/{ds}.json"

# COMEX Copper is the one market the COT config does not already map.
COPPER_CODE = "085692"

# Each dashboard market -> the CFTC contract whose futures-equivalent options OI we report.
# `code` is pulled from the COT mapping in config.py (no second mapping). `inverse` marks the
# three pairs the dashboard quotes USD-first while CME quotes the foreign currency: raw OI
# needs NO inversion (it is a property of the CME contract, direction-agnostic), but any LATER
# field that calls options bullish / bearish for the DISPLAYED pair must invert - PR #12 adds
# no such field, so `inverse` is carried only so a future PR cannot forget it.
def _ccy(sym):
    return _cfg.CURRENCIES[sym]["cot"]


def _cmdty(sym):
    return _cfg.COMMODITIES[sym]["cot"]


MARKETS = [
    {"key": "EURUSD", "label": "EUR/USD", "family": "TFF",    "code": _ccy("EUR"),   "inverse": False},
    {"key": "GBPUSD", "label": "GBP/USD", "family": "TFF",    "code": _ccy("GBP"),   "inverse": False},
    {"key": "AUDUSD", "label": "AUD/USD", "family": "TFF",    "code": _ccy("AUD"),   "inverse": False},
    {"key": "NZDUSD", "label": "NZD/USD", "family": "TFF",    "code": _ccy("NZD"),   "inverse": False},
    {"key": "USDJPY", "label": "JPY/USD", "family": "TFF",    "code": _ccy("JPY"),   "inverse": True},
    {"key": "USDCHF", "label": "CHF/USD", "family": "TFF",    "code": _ccy("CHF"),   "inverse": True},
    {"key": "USDCAD", "label": "CAD/USD", "family": "TFF",    "code": _ccy("CAD"),   "inverse": True},
    {"key": "Gold",   "label": "Gold",    "family": "DISAGG", "code": _cmdty("XAU"), "inverse": False},
    {"key": "Silver", "label": "Silver",  "family": "DISAGG", "code": _cmdty("XAG"), "inverse": False},
    {"key": "Copper", "label": "Copper",  "family": "DISAGG", "code": COPPER_CODE,   "inverse": False},
    {"key": "WTI",    "label": "WTI",     "family": "DISAGG", "code": _cmdty("WTI"),  "inverse": False},
]
MARKET_ORDER = [m["key"] for m in MARKETS]
BY_KEY = {m["key"]: m for m in MARKETS}

# Rolling-window lengths (in weekly reports). 52 weeks ~ one year of COT.
SHORT_WIN = 13
LONG_WIN = 52

# The methodology sentence the UI (PR #13) must show verbatim.
METHODOLOGY = (
    "CFTC converts options positions and open interest into futures-equivalent units using "
    "exchange-supplied delta factors. This is therefore a weekly delta-adjusted options "
    "exposure measure, not the number of outstanding option contracts."
)
