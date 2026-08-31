# Currency Strength Desk

A daily bias score for USD, EUR, GBP, JPY, AUD, NZD and CAD, blended from four inputs
and rendered to `dashboard.html`. Rebuilds itself when new data lands.

A separate **commodity track** (gold, silver, WTI crude) rides alongside it — see
[Commodities](#commodities) below. It is deliberately not part of the currency board.

Published dashboard: https://claude.ai/code/artifact/9b99f02c-bc66-4b61-b746-4b54fd211fa3
(that link is a snapshot — the always-current copy is the local `dashboard.html`)

## Running it

```
python run.py bootstrap   # cold start: 10y COT history from CFTC, then a full cot run
python run.py daily       # checklist + news + prices + rebuild (reuses the weekly COT)
python run.py cot         # full refresh including COT (currencies + commodities) + rates
python run.py next        # print the next scheduled release, fetch nothing
python auto.py            # rebuild ONLY if something changed - what the local scheduler calls
python commodities.py     # rebuild just the gold/silver/WTI track from cached data
python validate.py        # check score vs realised price, per currency and per pair
python backtest.py 520    # forward-return backtest of the COT leg over the deep history
python backtest_blend.py 14  # forward-return backtest of the whole blend (calendar-limited)
```

Nothing to install: stdlib only, Python 3.14. Runs against an empty `data/` — `bootstrap`
(or the first `daily`) populates it; COT comes from CFTC's public API so it works from CI.

## How the score is built

Each input is scored −100..+100 on its own, then weighted (`config.py: WEIGHTS`):

| Input | Weight | Source | Cadence |
|---|---|---|---|
| Checklist | 0.25 | 31 indicators, 1–5 each | as data releases |
| Rate odds | 0.10 | market-implied hike/hold/cut for the next meeting | as pricing moves |
| COT | 0.15 | CFTC leveraged-fund net position + weekly flow | weekly |
| Open interest | 0.10 | OI level and change vs positioning direction | weekly |
| News | 0.40 | actual vs forecast, time-decayed | continuous |

After the weighted blend, a **COT-extreme contrarian pull** (`cot_reversal_adjust`) can shove
the score ±8–13 toward a reversal when the speculative net is at a multi-year extreme, back on
a level it has reversed from before, or already unwinding — see [COT extreme / positioning
turn](#cot-extreme--positioning-turn). It is not a weighted leg; it is applied after the blend
and shown as its own breakdown row.

**Rate odds was promoted out of the checklist** on 2026-08-27. As 2 boxes out of 28 it was
worth 2.5% of the score, which is a structural mis-weighting: market-implied policy odds are
the most direct statement available about a currency's future carry, and rate differentials
are the most established driver in FX. Sized at 0.10 — modest, because **this one is not
backtested**. `data/expectations_history.json` now records a snapshot per run so it becomes
measurable in a few weeks; revisit the weight then. The two indicators stay visible in the
checklist but are excluded from its average so they are not counted twice.

### What is actually evidenced

`backtest_blend.py` reconstructs the whole signal as it stood at each past COT release and
measures it on **forward** returns. Over 14 weeks:

| Component | Mean forward rho | Positive weeks | t |
|---|---|---|---|
| **Blended** | **+0.179** | 8/14 | +1.64 |
| News only | +0.194 | 11/14 | +2.04 |
| Checklist only (auto half) | +0.048 | 7/14 | +0.58 |
| COT only | +0.020 | 7/14 | +0.23 |

The checklist originally scored `3 + 2*surprise` — actual versus forecast, which is *exactly*
what the news leg measures, only without time decay. It was a stale duplicate, and it
backtested at **−0.117**: redundant and actively harmful.

It now scores **levels instead of surprises** — percent-unit series ranked cross-sectionally
against the other currencies, everything else on its own direction of travel, and no forecast
used anywhere. That made it independent by construction, moved it from −0.117 to +0.048, and
more than doubled the blend from +0.077 to **+0.179**.

14 observations, so t is still short of 2. Re-run both backtests before moving weights again.

### Directional vs retracement read

Every score is a **medium-term bias**. Beside it the board shows how that bias sits against
the **last 5 trading days** of price (`config.py: directional_read`, fed by `fetch_fx_prices.py`
for currencies and the commodity trend leg for metals/oil):

| Read | Meaning |
|---|---|
| **on trend** / *directional* | price is confirming the bias — trend running |
| **poss. retracement** | price has moved ≥ the threshold *against* the bias — a pullback within the trend, i.e. a continuation watch (buy-the-dip / sell-the-rally), **not** a reversal call |
| *holding* | a directional bias but price roughly flat this week |
| *(nothing)* | score inside the neutral band — no directional edge |

Thresholds (`READ_THRESHOLDS`): FX needs |score| ≥ 5 and a ±0.35% counter-move; commodities
|score| ≥ 18 and ±1.5%. The commodity `pullback` flag is now just `read.state == "retracement"`.

Each directional name also gets a **retracement dip / rally zone** (`config.py:
retracement_zone`) — the 38.2–61.8% Fibonacci band of the *current* leg, plus the 20-period
mean and how much has retraced so far. The leg is found dynamically: the swing low in the
window (48 trading days for FX, 75 for commodities) and the highest point since it, for a
bullish bias; mirrored for bearish. The swing points carry dates so every zone is auditable
("3986 (16 Jul) → 4641 (24 Aug) leg"). Commodities show real price levels; currencies show
the band as % of the currency's strength (there is no single pair). States:

- `no pullback yet` — retraced < 8%, leg still extending. The card leads with the 20-day mean
  as first support and shows the fib band as a *deeper* reference, not a close entry.
- `approaching` — a real pullback under way but not yet at the band.
- `in zone` — price is inside the 38.2–61.8% band now.
- `overshot` — past 61.8%; the leg may be failing.

Needs a leg of at least 1.2% (FX) / 4% (commodity); returns nothing when the recent structure
disagrees with the bias (e.g. a bearish score while price is still trending up). Fed by the
strength-index `series` + `series_dates` in `prices_fx.json` and the commodity close history.

**Verified 2026-08-30** by independent re-derivation: fib levels, retraced %, band % and the
20-period mean all match; the swing highs are the true recent peaks; no bad ticks in the
gold/silver series. Note the commodity feed (Yahoo continuous futures) shows large 6-month
moves in this scenario — the zone measures the current recovery leg, which is what the score
is on, but the leg dates on the card show the wider picture.

Scores are **centred on zero**. FX strength is relative by construction — if every currency
scores positive the board is incoherent. Before centring (and before USD was reconciled with
the basket) the seven scores summed to **+34.3**, holding USD, AUD, GBP and EUR all bullish
at once.

The four weighted contributions sum to the headline score. The dashboard shows both the
raw and weighted numbers per currency, so any score can be traced back to its inputs.

**COT** blends positioning level (net as a share of open interest) with weekly flow, 60/40,
using the **Leveraged Funds** category — see below. A net position above 35% of OI is flagged
`crowded` — the squeeze risk your weekly playbook already warns about.

### COT extreme / positioning turn

Separately from `crowded` (an *absolute* %-of-OI threshold), the board flags **relative**
positioning extremes against the speculative net's own history (`config.py: cot_extreme`, fed
by `cot_history*.json` — **10 years** of weekly data, straight from CFTC):

| Flag | Meaning |
|---|---|
| **COT extreme** | speculative net at (within 6% of range of) its **1-year** *high* while genuinely net long, or its 1-year *low* while net short — positioning stretched, the point a trend change tends to start. The note also shows the 3-year and 10-year percentile so you can tell a genuine multi-year extreme from a merely-elevated one. |
| **COT level** | net back on a value it has **reversed from before** — the horizontal support/resistance of positioning (`config.py: _cot_levels`). A pivot counts only if it sat in the top/bottom quarter of the 10-year range *and* was followed within ~3 months by a reversal of ≥ 30% of that range; pivots within 10% of range are one level; a level needs ≥ 2 touches to show and its strength is the touch count. Fires when the net is within 10%-of-range of such a level *and moving into it*, even if that is not a fresh 1-year extreme. |
| **COT turning** | a **26-week** extreme was hit in the last ~6 weeks and the net has since unwound ≥ 15% of that window's range — longs leaving / shorts covering, which is *when* the trend change usually happens |

Sign-aware: "least short in the window" is **not** a stretched long. It is a contrarian read
— when everyone is already positioned one way, there is no one left to push the trend further.

**Why levels, and does it work?** A walk-forward test over 10 years × 6 currencies (clusters
built only from history before each week): a net sitting on a **≥ 3-touch** level that was
**not** also a 1-year extreme led the reversal by **+1.3% over the next 8 weeks, 68%
directional** (n = 80). 2-touch levels showed almost nothing — so the pull scales with touch
count. Like the rest of COT it is a slow, multi-week read, not a one-week timing call.

**The flag pulls the score toward the reversal** (`config.py: cot_reversal_adjust`,
`COT_REVERSAL_PULL`). It is not one of the weighted legs — it is an additive shove applied
to the blended score *after* the blend, before FX centring:

| State | Pull |
|---|---|
| **COT extreme** / **COT level** (stretched, or on a proven level, not yet turning) | `0.30 × \|score\| + 3.0` (FX) / `+ 8.0` (commodity) |
| **COT turning** (extreme already unwinding — reversal in motion, higher conviction) | `0.50 × \|score\| + 5.0` (FX) / `+ 14.0` (commodity) |

A **COT level** hit additionally scales the pull by touch count: `× 0.6` at 2 touches,
`× 1.0` at ~3, up to `× 1.6` at 4+ — putting the weight where the backtest found the edge.

Direction is set by **which side is crowded**, not by the score's own sign: crowded longs
(`stretched long` / `long unwinding` / `at long ceiling`) pull the score **down**, crowded
shorts (`stretched short` / `short covering` / `at short floor`) pull it **up**. Sized to
move a mid-strength name by ~8–13 points — visible against the rest of the board without
overriding the checklist and news. It shows as its own **COT extreme** row in the "what moved
each score" breakdown, and the card's Positioning note states the points applied and lists
the proven levels.

**History.** `cot_history.json` (Leveraged Funds, TFF report — plus Asset Manager and Dealer)
and `cot_history_commodity.json` (Managed Money, disaggregated — plus Producer and Swap) hold
**~523 weekly reports back to 2016-08** (10 years). `fetch_cot.py backfill_cftc 10` pulls the
lot in one query per contract from CFTC's public Socrata feed (`publicreporting.cftc.gov`,
data to 2006) — pass `3` / `5` / `7` / `10` for the depth you want. Net values match
tradingster exactly. Every weekly `fetch_cot.py` run appends the new report; the older
`backfill N` (weeks, via tradingster page-scraping) is a fallback.

The COT-leg backtest over the deep history (`backtest.py 520`, ~513 weeks, 1-week-forward
rank correlation): **leveraged mean ρ −0.015 (t −0.77), asset_manager −0.031 (t −1.54)** —
neither carries forward information at the 1-week horizon, which is exactly why COT is
weighted 0.15 and used for these context flags rather than as a directional call.

### Which COT category (this one matters)

The first build used **Asset Manager** and the board came out inverted: EUR scored strongest,
AUD weakest, while AUD was in fact the best-performing currency on the board. Measured against
realised price (rank correlation of COT score vs currency move, 2026-08-24):

| Horizon | Asset Manager | Leveraged Funds |
|---|---|---|
| 1 week | −0.09 | +0.49 |
| 2 weeks | **−0.60** | **+0.94** |
| ~5 weeks | +0.26 | +0.14 |

Asset Managers are real-money hedgers — they hold foreign assets and short the currency future
against them, so they sit structurally short currencies that are rallying. For EUR, GBP and AUD
the two categories were on *opposite sides* of the market. Leveraged Funds is the speculative,
directional money — the FX analogue of Managed Money in the commodity report — and that is what
a momentum-style positioning score needs.

Set by `COT_CATEGORY` in `config.py`.

**But do not overclaim this.** Those numbers compare today's score against price that had
*already happened* — backward-looking, and on 6 observations. A proper lagged backtest
(`backtest.py`, 14 weekly reports, entered at the Friday release, measured on FORWARD returns)
is far more sober:

| Hold | Asset Manager | Leveraged |
|---|---|---|
| 5 days | −0.003 | **+0.107** |
| 10 days | −0.184 | −0.118 |
| 28 days | −0.271 | −0.261 |

Leveraged beats asset_manager at every horizon, so the category switch stands. But **neither
predicts next-week returns**, and both turn contrarian as the horizon extends — crowded
positioning precedes reversal. That is why COT's weight was cut from 0.30 to 0.15: it earns
its place as context and as the `crowded` squeeze flag, not as a directional call.

### USD is a special case

USD's own contract is the ICE dollar index — only ~48k open interest against 805k for EUR —
while every other contract is *already* a position on that currency versus the dollar. Scoring
USD from the thin contract alone was the main source of incoherence. USD's COT is now 75% the
inverse of the open-interest-weighted basket and 25% its own contract.

**Open interest** is direction-less on its own, so it is read against positioning: rising OI
with rising net longs is new money buying; rising OI with falling net longs is new money
selling; falling OI is liquidation and gets half weight.

**News** scores each release as `(actual − forecast) / scale`, clipped to ±1, sign-flipped for
indicators where higher is worse (unemployment, claims, deficits), weighted by impact tier,
then decayed with a 36-hour half-life and squashed with `tanh` so one busy morning cannot
dominate.

## The 31-indicator checklist

Seven categories. Started 7×4=28; on 2026-08-30 three FX-relevant drivers that were reaching
the score only through the news leg (if at all) were added: **consumer confidence** (auto)
and **retail sales YoY** (auto) to Economic Growth, and **fiscal balance** to Geopolitics &
Risk. Fiscal balance is **manual**, not auto — the monthly budget prints are seasonal and
unit-inconsistent across countries ($bn / C$ / % of GDP) and a near-zero previous value pins
the direction score, the same reasons Debt level is manual. Each category stays equally
weighted (1/7), so adding indicators changes a category's internal average, not its share of
the score. Two sources fill it:

- **auto** — any indicator with a released datapoint behind it. Mapped from calendar
  surprises as `3 + 2 × surprise`, so an in-line print sits at neutral 3. Policy rate and
  rate differential come from actual central-bank decisions via `fetch_rates.py`.
- **manual** — judgment calls with no single release behind them: expected rate change,
  real yield, inflation vs target, CB stance, next-meeting expectation, QE status, FX
  intervention risk, political stability, risk premium, safe-haven status, debt level,
  fiscal balance.

Anything unset is **held at neutral 3 and reported as unset** rather than counted as a real
reading — the dashboard shows a `% measured` figure per currency so you can see how much of
the score is actually evidenced. Current coverage runs 36–57%.

To set a manual score, edit `data/fundamentals_manual.json`:

```json
{ "JPY": { "FX intervention risk": 2, "CB stance": 4 } }
```

Values are 1–5. They override the auto value for that indicator and survive rebuilds.

## Refresh

`auto.py` runs every 15 minutes from a Windows scheduled task named **FX Strength Desk**.
It pulls the calendar (cheap), then exits without touching anything else unless:

- an event that had no `actual` last run now has one → `news` rebuild
- more than 20 hours since the last rebuild → `daily` rebuild
- it is the weekend and the COT on file is ≥7 days old → `cot` rebuild

Exit code 10 means "nothing to do". Output appends to `data/refresh.log`.

```
schtasks /query /tn "FX Strength Desk" /fo LIST /v     # inspect
schtasks /run   /tn "FX Strength Desk"                 # run now
schtasks /delete /tn "FX Strength Desk" /f             # remove
```

This costs nothing per run — it is plain Python, not a Claude session.

## Data sources

| What | Where | Notes |
|---|---|---|
| COT positioning, open interest | **CFTC Socrata** `publicreporting.cftc.gov` (primary) / tradingster.com (local fallback) | codes in `config.py`; CFTC is a government API with no datacenter-IP blocking, so it works from CI. `cot.json` records which source was used. |
| Calendar, actuals, forecasts | TradingView economic-calendar API | needs an `Origin` header |
| Policy rates | same API, longer window | paged — see below |
| Fed speaker calendar | federalreserve.gov/json/calendar.json | official JSON, **UTF-8 BOM** — decode `utf-8-sig` |
| BoE speeches | bankofengland.co.uk/rss/speeches | RSS, published items (backward-looking) |
| ECB speeches | ecb.europa.eu/rss/press.html | RSS, speeches mixed with releases |
| Rate probabilities | centralbank.watch | server-rendered; no Fed, no RBNZ |
| FX prices (validation only) | Frankfurter ECB daily reference rates | needs a browser UA or 403 |
| Commodity prices | Yahoo Finance chart API, `GC=F` / `SI=F` / `CL=F` | needs a browser UA; front-month futures |

Two gotchas worth remembering:

- The calendar API **caps a response at 2000 rows and truncates from the start of the
  range**, so a long window silently returns the *oldest* events. `fetch_rates.py` pages it
  in 40-day slices. Do not widen a window without paging.
- The ForexFactory weekly JSON (`nfs.faireconomy.media`) **carries no `actual` field** —
  only title, country, date, impact, forecast, previous. It cannot be used for release
  detection. It also rate-limits (429) under repeated fetches.

## Central-bank commentary

The two economic calendars between them listed **11 commentary events — 1.5% of all events,
9 of them USD, and zero for EUR, GBP, NZD and CAD.** The banks publish their own schedules,
which are far more complete: `speakers_official.py` adds 775 Fed calendar entries (speeches,
testimony, FOMC), ~50 BoE speeches and the ECB press feed. Total commentary went 11 → 834.

`data/ff_archive.json` accumulates the ForexFactory weekly feed, which only ever serves the
current week — without it, past commentary vanished every Monday.

The **pending** list is capped to the 12-day scoring decay window. The bank calendars reach
back years; without the cap the list ran to 814 speeches that could no longer move the score
even if scored.

## Rate expectations (the FedWatch input)

CME FedWatch itself is unreachable — cmegroup.com returns 403 here. rateprobability.com was
checked and **rejected**: it served January 2026 meeting dates and stale policy rates (BoJ
0.75% when the 31 July decision printed 1.00%). centralbank.watch is current and
server-renders its numbers, but covers only the ECB, BoE and RBA.

Gaps go in `data/rate_expectations_manual.json`, filled by the daily research pass with the
source and date recorded. Probabilities drive the *Next meeting expectation* and *Expected
rate change* indicators as `3 + 2 × (P(hike) − P(cut))`.

## Open interest is weekly, not daily

The brief asked for daily OI. No free, automatable daily source was reachable:
CME resets the connection, MarketWatch and WSJ return 401, Investing.com and Barchart 404,
Stooq sits behind a JavaScript proof-of-work wall. Paid options are Databento, Barchart
OnDemand and CME's API portal. TradingView carries futures OI but only through the desktop
app, which has to be running.

So `fetch_oi.py` is written as an adapter. It defaults to `SOURCE = "cot_weekly"`, using the
open interest already in the COT report. To go daily, add a function there and repoint
`SOURCE` — nothing downstream changes.

## Files

```
config.py           currencies, commodities, CFTC codes, the 31 indicators, blend weights
fetch_cot.py        COT positioning + open interest (currencies AND commodities) + weekly history
cot_history*.json   32-week speculative-net history -> the COT-extreme / positioning-turn read
fetch_prices.py     commodity daily closes from Yahoo Finance (trend + validation)
fetch_fx_prices.py  recent FX moves vs USD from Frankfurter (the directional / retracement read)
commodities.py      the gold/silver/WTI track - COT + OI + trend + manual overlay
fetch_calendar.py   calendar, actuals, news surprise scoring
fetch_rates.py      policy rates (paged)
fetch_oi.py         open-interest signal, swappable source
speakers_official.py  Fed / BoE / ECB own speaker calendars
rate_expectations.py  market-implied hike/hold/cut probabilities
fundamentals.py     the 31-indicator checklist
score.py            blend, ratings, pair ranking
validate.py         per-currency AND per-pair check against realised FX moves
backtest.py         lagged forward-return backtest of the COT leg (caches COT history)
backtest_blend.py   forward-return backtest of the WHOLE signal, component by component
build_dashboard.py  renders dashboard.html
template.py         the HTML shell
run.py              pipeline runner
auto.py             change detection - what the scheduler calls
refresh.cmd         scheduled-task wrapper
data/               fetched JSON, manual overrides, state, log
```

## Commodities

Gold, silver and WTI crude get their own score, section and validation line. They are **not
on the currency board**: a commodity has no central bank, no policy-rate path and no
macro checklist, and metals/oil are not zero-sum against each other, so the FX machinery
(centring, pair ranking, the 31-indicator checklist, rate-expectation odds) does not apply.

`commodities.py` blends four legs into a −100..+100 bias (`config.py: COMMODITY_WEIGHTS`):

| Leg | Weight | Source |
|---|---|---|
| Trend | 0.35 | Yahoo Finance daily close (GC=F / SI=F / CL=F): last vs the 50-day average (60) + the 20-day change (40). Leads the blend — momentum is the one commodity edge with real empirical support, and it references the same front-month contract the COT is on. |
| COT | 0.25 | CFTC **disaggregated** report, Managed Money net as a share of OI (60) + weekly flow (40) — same tanh math as the FX `leveraged` leg. Cut from an initial 0.40 to near the FX side's 0.15: a large net is *contrarian* at the extremes, so it earns its keep as context and as the `crowded` (net > 35% of OI) and `pullback` flags, not as the directional call. |
| Open interest | 0.15 | COT report OI level and weekly change, read against the Managed Money flow direction |
| Overlay | 0.25 | manual — real yields, the dollar, OPEC+ supply, demand, safe-haven bid. Held at a true 0 and reported as unset until entered in `data/commodities_manual.json`, exactly like the FX checklist. |

**Price-action check.** The score is medium-term. If the last 5 trading days have moved ≥1.5%
*against* the score, the commodity carries a **`pullback`** flag — the bias is fighting fresh
price action. The trend note also prints the 5-day change so the divergence is visible.
(Yahoo revises the front-month settlement after the session, so the trend leg can shift
slightly between runs until it finalises — the scheduled job re-fetches each run.)

Ratings use wider bands than FX (`COMMODITY_RATING_BANDS`) because the scores are **not
centred** — a commodity can sit legitimately long or short with nothing on the other side to
net against, so the FX bands would pin everything at the extremes.

The COT category is **Managed Money**, the commodity-report analogue of Leveraged Funds —
the speculative, directional money, not the producers hedging physical.

**None of this is backtested** the way the FX blend is. `data/commodity_history.json`
snapshots a score per run and `validate.py` now prints commodity score vs realised price;
revisit the weights once there are a few weeks behind it.

To set the overlay, edit `data/commodities_manual.json` (1–5, 5 = bullish):

```json
{ "XAU": { "Real yield direction": 4, "US dollar direction": { "score": 4, "note": "DXY rolling over" } } }
```

## Caveats

Scores are a weighted heuristic, not advice. The pair ranking is a shortlist, not a signal —
location on the chart still decides the trade, per the weekly playbook. Roughly half of each
checklist score is currently unmeasured and held neutral; fill the manual indicators to make
the fundamentals leg mean more.
