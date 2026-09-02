"""Commodity news decoder — gold, silver, oil. Micro alerts, straight to the phone.

Sister to news_watch.py, but with one job: when news breaks, say in plain words what
it means and WHICH WAY IT LEANS for XAU / XAG / WTI, so you know whether to go hunting
for longs or for shorts. No essays.

    27 Aug 2026 is the reference case. Warsh signalled the Fed may have to RAISE rates.
    That is news against gold. The alert this file sends for that headline reads
    "GOLD DOWN DOWN - look for shorts", and it would have fired the same afternoon.

Each alert is five short blocks:
    what happened  ->  why it matters (one sentence, no jargon)
    ->  a lean per instrument  ->  live prices  ->  what would flip it

A LIVE REALITY CHECK sits on top of the textbook read: if gold is falling while US
yields and oil rise, the market is trading the oil->inflation->rates channel, and a
war headline PRESSURES gold instead of lifting it. In that regime the gold lean is
inverted and the alert says so. That is the trap that caught the 1 Sep oil shock.

Runs on GitHub Actions every ~15 min. Stdlib only, no API keys.

    python commodity_watch.py             # scan + push
    python commodity_watch.py --dry-run   # scan + print, push nothing
    python commodity_watch.py --audit     # classify everything on the wires now
    python commodity_watch.py --replay    # show the 27 Aug Warsh alert as it would look
    python commodity_watch.py --test      # send one test push
    python commodity_watch.py --render    # rebuild the feed page / dashboard block
"""
import json, os, sys, re, hashlib, time
import datetime as dt
from pathlib import Path

# reuse the plumbing that already works in the FX watcher
from news_watch import (_get, _parse_date, market_snapshot, level_map, push,
                        gather_items as _fx_items, VETO, UA, _GN, _q,
                        theme_claim)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                              # noqa: BLE001
    pass

DATA = Path(__file__).parent / "data"
SEEN_FILE = DATA / "commodity_seen.json"
FEED_FILE = DATA / "commodity_feed.json"

MAX_AGE_MIN = 45          # only alert on items published this recently
SEEN_TTL_DAYS = 4
FEED_KEEP = 60            # alerts retained for the in-app feed
COOLDOWN_MIN = 90         # one alert per category per this many minutes (see note below)

# sha256 of the topic the phone actually subscribes to, truncated to 24 bits. Used only
# to detect a mis-set NTFY_TOPIC secret; too short to reveal the topic itself.
EXPECTED_TOPIC_FP = "557c95"

# A single story - Warsh at Jackson Hole - was carried by 15+ outlets inside the same hour
# during testing. Hashing the title only dedups exact repeats, so without a cooldown the
# phone gets buzzed 15 times for one event. One alert per category per COOLDOWN_MIN fixes
# it; a genuinely new development in the same category still gets through once the window
# passes, and a higher-severity item overrides the cooldown immediately.

# ------------------------------------------------------------------ sources
# The FX watcher's feeds are dollar-first. These are the commodity-first ones.
_QUERIES = [
    '(gold OR "gold price" OR bullion) (Fed OR rates OR inflation OR dollar OR yields '
    'OR "central bank" OR demand OR ETF)',
    '(silver OR "silver price") (squeeze OR shortage OR supply OR solar OR industrial '
    'OR mine OR LBMA OR demand)',
    '(oil OR crude OR Brent OR WTI) (OPEC OR supply OR output OR inventories OR sanctions '
    'OR refinery OR pipeline OR demand OR stockpiles)',
    '("Strait of Hormuz" OR Iran OR Russia OR Venezuela OR Libya OR Nigeria) '
    '(oil OR crude OR tanker OR sanctions OR export OR attack OR strike)',
    '("Federal Reserve" OR Powell OR Warsh OR FOMC) (rate hike OR rate cut OR inflation '
    'OR "higher for longer" OR yields OR "basis points")',
    '("central bank gold" OR "gold reserves" OR "PBoC gold" OR "gold ETF" OR "bullion demand" '
    'OR "mine supply" OR "mine strike")',
]
# Direct wires on top of the Google News queries - they carry a story minutes before it
# reaches an aggregator. Financial Juice was requested and tested first: it is behind
# Cloudflare and returns "error code: 1015" / Access Denied to any automated client,
# including a real browser, so it cannot be used. These are the fast ones that do work.
FEEDS = [_GN.format(q=_q(x)) for x in _QUERIES] + [
    "https://www.forexlive.com/feed/news/",          # squawk-style, fastest of these
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",  # WSJ markets
    "https://www.investing.com/rss/news_1.rss",
    "https://oilprice.com/rss/main",
    "https://www.mining.com/feed/",
]

# Financial Juice carries the squawk headlines earlier than anyone here, and its feed
# DOES work - the endpoint returns 41KB of real RSS. What it does not tolerate is being
# polled: Cloudflare answers 429/1015 after roughly one request, and stays angry for a
# while. So it is fetched on its own slow clock instead of every scan. Being rate-limited
# is not an error here; the pull is simply skipped and the next one tries later.
SLOW_FEEDS = {"https://www.financialjuice.com/feed.ashx": 900}   # url -> min seconds apart
SLOW_STATE = DATA / "slow_feeds.json"


def _slow_due():
    """The slow feeds whose cool-off has passed, marking them as pulled now."""
    try:
        state = json.loads(SLOW_STATE.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        state = {}
    now, due = time.time(), []
    for url, gap in SLOW_FEEDS.items():
        if now - state.get(url, 0) >= gap:
            due.append(url)
            state[url] = now
    if due:
        try:
            SLOW_STATE.parent.mkdir(exist_ok=True)
            SLOW_STATE.write_text(json.dumps(state), encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            pass
    return due

# ------------------------------------------------------------------ classifier
# (category, severity 1-3, [keyword...]).  First match wins, so order = priority.
RULES = [
    # ---- the Fed / rates channel: gold's single biggest driver ----------------
    ("rates_up", 3, [
        "may have to rise", "may have to raise", "rates could rise", "rate hike",
        "hike on the table", "open to a hike", "favor of a hike", "higher for longer",
        "fed hike", "hike bets", "hike odds", "hike expectations",
        "not ready to cut", "premature to cut", "no rush to cut", "pushes back on rate cut",
        "restrictive for longer", "inflation too high", "work to do on inflation",
        "more work to do", "hawkish", "raises rates", "raised rates", "hikes rates",
        "inflation concerning", "concerning inflation", "tightening", "dissent in favor of a hike",
    ]),
    ("rates_down", 3, [
        "rate cut", "cut is coming", "ready to cut", "time to cut", "cut is warranted",
        "close to cutting", "signals rate cut", "open the door to a cut", "dovish",
        "cuts rates", "cut rates", "lowered rates", "lowers rates", "50 basis point cut",
        "emergency cut", "intermeeting cut", "labor market weakening", "cooling labor market",
        "easing cycle", "start easing",
    ]),
    ("fed_independence", 3, [
        "fire powell", "remove powell", "replace powell", "oust powell", "powell resign",
        "fed independence", "attack on the fed", "shadow fed chair", "shadow chair",
        "pressure the fed", "trump slams powell", "trump blasts powell", "demands rate cut",
        "take over the fed", "control of the fed", "packing the fed",
    ]),
    # ---- inflation prints ----------------------------------------------------
    ("inflation_hot", 2, [
        "inflation rises", "inflation accelerates", "inflation hotter", "hotter than expected",
        "cpi jumps", "cpi rises", "cpi beats", "price pressures build", "inflation surprise",
        "ppi jumps", "core inflation rises", "inflation picks up", "sticky inflation",
    ]),
    ("inflation_cold", 2, [
        "inflation falls", "inflation cools", "inflation slows", "cooler than expected",
        "cpi falls", "cpi cools", "cpi misses", "disinflation", "price pressures ease",
        "inflation eases", "softer inflation",
    ]),
    # ---- geopolitics ---------------------------------------------------------
    ("geo_escalation", 3, [
        "airstrike", "air strike", "missile strike", "attacked iran", "strikes iran",
        "strike on iran", "attack on iran", "striking iran", "us strikes", "american attack",
        "strike iran", "strike israel", "strike russia", "hit iran",
        "bombing", "invasion", "invaded", "declares war", "act of war",
        "military strike", "military action", "retaliatory strike", "ballistic missile",
        "troops enter", "ground offensive", "attacks israel", "bombed", "warships",
        "blockade", "tanker attacked", "tanker seized", "drone attack", "shot down",
        "fighting resumes", "strikes resume", "iran strikes", "strikes send",
        "fires on", "opens fire", "retaliates", "retaliating", "strikes back",
        "attack on vessel", "attack on ship", "attack on tanker",
        "sailors killed", "crew killed", "vessel hit", "ship hit",
        "strikes deepen", "strikes escalate",
    ]),
    ("geo_deescalation", 3, [
        "ceasefire", "cease-fire", "peace deal", "peace agreement", "truce agreed",
        "truce holds", "de-escalat", "deescalat", "stand down", "halt strikes",
        "halts strikes", "stops attacks", "ends strikes", "end to hostilities",
        "sanctions lifted", "sanctions eased", "prisoner swap", "hostage deal",
        "withdraw troops", "pull back forces", "diplomatic breakthrough",
        "reopen the strait", "talks resume", "back to the table",
    ]),
    # ---- oil supply ----------------------------------------------------------
    ("oil_supply_tight", 3, [
        "opec+ cut", "opec cut", "production cut", "output cut", "supply disruption",
        "disrupts supply", "supply disrupted", "war disrupts", "halts supply",
        "pipeline attack", "refinery fire", "refinery outage", "oil embargo",
        "shut the strait", "close the strait", "strait of hormuz", "export halt",
        "halts exports", "supply shortfall", "sanctions on oil", "oil sanctions",
        "force majeure", "field shut", "platform evacuated", "hurricane shuts",
    ]),
    ("oil_supply_loose", 3, [
        "opec+ raises", "opec raises", "output increase", "raise production",
        "boost production", "increase output", "unwind cuts", "unwinding cuts",
        "more barrels", "supply glut", "oversupply", "record production",
        "hold output steady", "holds output steady", "output steady",
        "resume production", "restart production", "sanctions waiver",
        "more oil flowed", "oil reserves deal", "control over venezuela",
        "resumes exports", "export resumes", "spare capacity", "quota increase",
    ]),
    ("oil_inv_build", 2, [
        "crude stockpiles rise", "inventories rise", "stockpiles build", "crude build",
        "inventory build", "bigger than expected build", "crude stocks jump",
    ]),
    ("oil_inv_draw", 2, [
        "crude stockpiles fall", "inventories fall", "stockpiles draw", "crude draw",
        "inventory draw", "bigger than expected draw", "crude stocks drop",
    ]),
    # ---- demand --------------------------------------------------------------
    ("demand_up", 2, [
        "china stimulus", "stimulus package", "demand forecast raised", "demand outlook raised",
        "record demand", "demand surges", "stronger demand", "growth upgrade",
        "manufacturing rebounds", "factory activity expands",
    ]),
    ("demand_down", 2, [
        "recession", "demand destruction", "demand outlook cut", "demand forecast cut",
        "weaker demand", "slowdown", "growth downgrade", "contraction", "factory slump",
        "china slowdown", "manufacturing shrinks",
    ]),
    # ---- metals-specific -----------------------------------------------------
    ("cb_gold_buying", 2, [
        "central bank gold", "central banks bought", "gold reserves rose", "adds to gold reserves",
        "pboc gold", "buying gold", "gold purchases", "boosts gold holdings",
        "de-dollarisation", "de-dollarization", "reserve diversification",
    ]),
    ("etf_inflow", 1, [
        "gold etf inflow", "etf inflows", "holdings rose", "adds to holdings",
        "biggest inflow", "investors pile into gold",
    ]),
    ("etf_outflow", 1, [
        "gold etf outflow", "etf outflows", "holdings fell", "cuts holdings",
        "biggest outflow", "investors dump gold",
    ]),
    ("metal_supply", 2, [
        # NOT "mine strike" - that matched "Supertanker Mine Strike" (a naval mine) in testing
        "miners strike", "miners' strike", "strike at the mine", "workers strike",
        "mine closure", "mine halted", "mine suspended", "smelter fire",
        "output falls at", "production halted", "supply deficit", "refinery shut",
    ]),
    ("silver_squeeze", 3, [
        "silver squeeze", "silver shortage", "lease rates", "backwardation",
        "lbma stocks", "vault stocks fall", "silver deficit", "physical shortage",
        "borrowing costs spike",
    ]),
    ("tariff", 2, [
        "new tariff", "tariffs on", "tariff hike", "raise tariffs", "impose tariffs",
        "trade war", "section 232", "section 301", "export controls", "export ban",
    ]),
    ("risk_off", 2, [
        "safe haven", "risk-off", "risk off", "flight to safety", "market rout",
        "stocks plunge", "stocks tumble", "sell-off deepens", "vix spikes", "crash",
    ]),
]

# ------------------------------------------------------------------ the decoder
# Per category: the plain-words meaning, plus a lean for each metal / oil.
# lean = (direction, strength 0-3, short reason).  Direction: "up" | "down" | "flat".
# Strength drives the arrows and the call: 3 = strong, 2 = tradeable, 1 = mild drag.
DECODE = {
    "rates_up": {
        "emoji": "\U0001f4b5", "label": "FED — leaning toward higher rates",
        "why": "Higher US rates make cash and bonds pay more. Gold pays you nothing to "
               "hold it, so it becomes less attractive and money leaves it.",
        "gold": ("down", 3, "rates up = gold's worst enemy"),
        "silver": ("down", 3, "same hit as gold, only bigger"),
        "oil": ("down", 1, "only indirectly, via slower growth"),
        "flip": "a weak US jobs or inflation number takes this straight back off",
    },
    "rates_down": {
        "emoji": "\U0001f4b8", "label": "FED — leaning toward rate cuts",
        "why": "Lower US rates mean cash pays you less, so holding gold costs you nothing "
               "in lost interest. The dollar usually weakens too, and everything priced "
               "in dollars gets cheaper for the rest of the world.",
        "gold": ("up", 3, "cheaper money is gold's best friend"),
        "silver": ("up", 3, "follows gold up, only harder"),
        "oil": ("up", 1, "weaker dollar + a bit more growth"),
        "flip": "a hot inflation print would kill the cut hopes",
    },
    "fed_independence": {
        "emoji": "\U0001f3db", "label": "POLITICS vs THE FED",
        "why": "If people stop trusting that the Fed is independent, they stop trusting "
               "the dollar. That is the single most gold-positive story there is.",
        "gold": ("up", 3, "a straight bet against trust in the dollar"),
        "silver": ("up", 2, "drags along behind gold"),
        "oil": ("flat", 0, "no clean link"),
        "flip": "it fades fast if the threat turns out to be talk only",
    },
    "inflation_hot": {
        "emoji": "\U0001f321", "label": "INFLATION — running hot",
        "why": "Gold is supposed to protect you from inflation, but hot inflation first "
               "makes the Fed keep rates high — and high rates hurt gold more than the "
               "inflation helps it. The rates channel wins in the short run.",
        "gold": ("down", 2, "the rates reaction beats the hedge story"),
        "silver": ("down", 2, "same, amplified"),
        "oil": ("flat", 0, "usually a cause, not a victim"),
        "flip": "if inflation is coming from oil AND growth is dying, gold turns up instead",
    },
    "inflation_cold": {
        "emoji": "❄", "label": "INFLATION — cooling",
        "why": "Softer inflation lets the Fed cut rates sooner. Cheaper money, weaker "
               "dollar, better for anything priced in dollars.",
        # MEASURED: gold up on 70% of these vs a 55% baseline (+15pp, +0.50% over
        # drift, n=204); silver +11pp, +0.53%, n=204. Best signal in the whole table,
        # so both are strength 3. See backtest_decode.py.
        "gold": ("up", 3, "opens the door to rate cuts - best-tested signal here"),
        "silver": ("up", 3, "leads gold on the way up"),
        "oil": ("flat", 1, "mild help from a softer dollar"),
        "flip": "one hot print reverses the whole thing",
    },
    "geo_escalation": {
        "emoji": "\U0001f6a8", "label": "GEOPOLITICS — escalation",
        "why": "War scares people into buying safety, and it threatens the supply of oil "
               "coming out of the region.",
        # MEASURED over 7.5 years (n=88 event-days): gold +4pp over baseline but with
        # ~0.00% excess move, silver +3pp with a NEGATIVE excess move. The panic buy is
        # real but weak - not a "look for longs" signal - so gold drops to a mild bias
        # and silver makes no claim. Oil is the leg that measures: +7pp, +0.67%.
        # This is the third independent measurement saying the same thing: on oil and
        # war news, the OIL leg carries the edge and the metals legs do not.
        "gold": ("up", 1, "panic buy is real but measures weak (+4pp, ~0% excess)"),
        "silver": ("flat", 0, "no measurable edge - it is half an industrial metal"),
        "oil": ("up", 3, "supply at risk = price up fast - measured +7pp"),
        "flip": "any hint of a ceasefire and the whole premium comes straight out",
        "regime_sensitive": True,
    },
    "geo_deescalation": {
        "emoji": "\U0001f54a", "label": "GEOPOLITICS — calming down",
        "why": "The war premium that got priced in now comes back out. Everything that "
               "rallied on fear gives it back.",
        # MEASURED (n=121 event-days, backtest_geo.py): the textbook "fear bid unwinds,
        # gold down" is NOT what happens. Gold fell on only 36% of these days against a
        # 45% baseline (-9pp); silver -10pp. Two mechanisms fight - the fear premium
        # coming out (gold down) versus oil falling, inflation expectations easing and
        # yields dropping (gold up) - and neither wins reliably. Claiming a direction
        # would be inventing one. Oil is the tradeable leg: 51% vs 47%, +0.51% over drift.
        "gold": ("flat", 0, "measured both ways - fear-out vs yields-down cancel"),
        "silver": ("flat", 0, "same, no reliable direction"),
        "oil": ("down", 3, "supply worry gone, barrels flow again - measured +4pp"),
        "flip": "these deals break — one broken truce and it all goes back on",
    },
    "oil_supply_tight": {
        "emoji": "\U0001f6e2", "label": "OIL — supply getting tighter",
        "why": "Fewer barrels reaching the market than people expected. Buyers chase what "
               "is left, so the price goes up.",
        # MEASURED (n=38): the "dearer oil = inflation = gold up" step does not survive
        # contact with the data - gold -7pp against baseline, silver -8pp. Dearer oil
        # lifts yields as readily as it lifts inflation hedges. Oil itself is the
        # strongest news signal in either backtest: 66% vs a 53% baseline (+13pp,
        # +1.43% over drift).
        "gold": ("flat", 0, "the inflation-hedge step does not hold up in the data"),
        "silver": ("flat", 0, "measured against the call"),
        "oil": ("up", 3, "the direct hit - best-measured news signal (+13pp)"),
        "flip": "if the disruption turns out to be small or short, it fades within days",
    },
    "oil_supply_loose": {
        "emoji": "\U0001f6e2", "label": "OIL — more barrels coming",
        "why": "More supply than the market expected. Too much oil chasing the same buyers "
               "means a lower price.",
        "gold": ("flat", 0, "no direct link"),
        "silver": ("flat", 0, "no direct link"),
        "oil": ("down", 3, "extra barrels push the price down"),
        "flip": "OPEC often says more than it actually pumps — watch the real exports",
    },
    "oil_inv_build": {
        "emoji": "\U0001f6e2", "label": "OIL — stockpiles building",
        "why": "More oil is sitting in storage than expected, which means demand is not "
               "keeping up with supply.",
        "gold": ("flat", 0, ""), "silver": ("flat", 0, ""),
        "oil": ("down", 2, "storage filling up = weak demand"),
        "flip": "one week's number is noise; the trend over a month is the signal",
    },
    "oil_inv_draw": {
        "emoji": "\U0001f6e2", "label": "OIL — stockpiles drawing down",
        "why": "Storage is emptying faster than expected — demand is beating supply.",
        "gold": ("flat", 0, ""), "silver": ("flat", 0, ""),
        "oil": ("up", 2, "storage emptying = real demand"),
        "flip": "same caveat — one week is noise",
    },
    "demand_up": {
        "emoji": "\U0001f4c8", "label": "DEMAND — growth picking up",
        "why": "A stronger world economy burns more oil and uses more silver in factories, "
               "solar panels and electronics. Gold cares least — it is not an industrial metal.",
        "gold": ("flat", 0, "growth is not gold's story"),
        "silver": ("up", 2, "half of all silver goes into industry"),
        "oil": ("up", 2, "more activity = more barrels burned"),
        "flip": "if it comes with higher rates, silver's gain gets cancelled out",
    },
    "demand_down": {
        "emoji": "\U0001f4c9", "label": "DEMAND — growth slowing",
        "why": "A weaker economy burns less oil and uses less silver. Gold usually goes the "
               "OTHER way, because a slowdown means rate cuts and people want safety.",
        "gold": ("up", 1, "slowdown brings cuts and safety buying"),
        "silver": ("down", 2, "the industrial half gets hurt"),
        "oil": ("down", 3, "less activity = fewer barrels needed"),
        "flip": "in a real panic everything gets sold at once, gold included, for a day or two",
    },
    "cb_gold_buying": {
        "emoji": "\U0001f3e6", "label": "CENTRAL BANKS — buying gold",
        "why": "Countries swapping dollars for gold in their reserves. This is slow, steady "
               "buying that does not care about the price — it puts a floor under the market.",
        "gold": ("up", 2, "price-blind buyers under the market"),
        "silver": ("up", 1, "sentiment spillover only — they do not buy silver"),
        "oil": ("flat", 0, ""),
        "flip": "the data is monthly and late, so it confirms a trend rather than starting one",
    },
    "etf_inflow": {
        "emoji": "\U0001f4b0", "label": "INVESTORS — money coming into gold",
        "why": "Funds are buying and holding physical metal. Real buying, but it usually "
               "follows the price rather than leading it.",
        "gold": ("up", 1, "confirms the trend, does not start it"),
        "silver": ("up", 1, "same"), "oil": ("flat", 0, ""),
        "flip": "crowded ETF buying is also how tops get built",
    },
    "etf_outflow": {
        "emoji": "\U0001f4b8", "label": "INVESTORS — money leaving gold",
        "why": "Funds are selling metal. Again it tends to follow the price, so treat it as "
               "confirmation of weakness.",
        "gold": ("down", 1, "confirms weakness"),
        "silver": ("down", 1, "same"), "oil": ("flat", 0, ""),
        "flip": "heavy outflows near a low are how bottoms get built",
    },
    "metal_supply": {
        "emoji": "⛏", "label": "MINES — supply hit",
        "why": "A mine or smelter has stopped producing. Less new metal reaching the market.",
        "gold": ("up", 1, "mine supply moves gold slowly"),
        "silver": ("up", 2, "silver's market is small — supply hits bite harder"),
        "oil": ("flat", 0, ""),
        "flip": "mines restart; the effect fades unless the stoppage runs for months",
    },
    "silver_squeeze": {
        "emoji": "⚡", "label": "SILVER — physical squeeze",
        "why": "There is not enough physical silver where it is needed, so the cost of "
               "borrowing it spikes and anyone who is short has to buy back fast.",
        "gold": ("up", 1, "gets pulled along"),
        "silver": ("up", 3, "this is silver's own violent move"),
        "oil": ("flat", 0, ""),
        "flip": "squeezes end abruptly and give most of it back — do not chase late",
    },
    "tariff": {
        "emoji": "\U0001f6a2", "label": "TARIFFS / TRADE WAR",
        "why": "Taxes on imports make goods dearer and trade smaller. Bad for growth, and "
               "it drives money toward safety.",
        "gold": ("up", 2, "safety buying + inflation worry"),
        "silver": ("flat", 1, "torn — safety says up, less industry says down"),
        "oil": ("down", 2, "less trade means less fuel burned"),
        "flip": "most tariff threats get watered down at the deadline",
    },
    "us_data_strong": {
        "emoji": "\U0001f4ca", "label": "US DATA — came in strong",
        "why": "A stronger US economy means the Fed has no reason to cut rates, and may "
               "even raise them. Money stays in the dollar and in things that pay interest, "
               "and gold pays nothing.",
        # MEASURED (n=167): gold fell only 40% of the time vs a 45% baseline (-6pp) -
        # no edge, so this is a mild bias, not a short signal. Silver +1pp, likewise.
        # Oil is the real one: up 62% vs 53% baseline (+9pp, +0.40% over drift).
        "gold": ("down", 1, "textbook drag, but measures weak - do not short on this alone"),
        "silver": ("down", 1, "no measurable edge in the data"),
        "oil": ("up", 2, "a busier economy burns more fuel - measured +9pp"),
        "flip": "one number is not a trend - the next weak print undoes it",
    },
    "us_data_weak": {
        "emoji": "\U0001f4ca", "label": "US DATA — came in weak",
        "why": "A weaker US economy pushes the Fed toward cutting rates. Cheaper money and "
               "a softer dollar are what gold wants. Silver is torn - it likes cheap money "
               "but it needs factories busy.",
        # MEASURED (n=132): gold up 61% vs 55% baseline (+7pp). Silver only +2pp -
        # the industrial drag really does cancel the rate-cut help.
        "gold": ("up", 2, "brings rate cuts back onto the table - measured +7pp"),
        "silver": ("up", 1, "cuts help, weaker industry hurts - nets to nothing measurable"),
        "oil": ("down", 1, "a slower economy burns less fuel"),
        "flip": "one number is not a trend - watch the next jobs print",
    },
    "risk_off": {
        "emoji": "\U0001f4c9", "label": "RISK-OFF — money running for cover",
        "why": "Investors are selling risky things and buying safe ones.",
        "gold": ("up", 2, "the safety trade"),
        "silver": ("down", 1, "sold with the risky stuff, not bought as safety"),
        "oil": ("down", 2, "fear = less growth = less fuel"),
        "flip": "if it turns into a full panic, gold gets sold too — to cover losses elsewhere",
        "regime_sensitive": True,
    },
}

# calendar prints map onto the rates channel
CAL_MAP = {True: "rates_up", False: "rates_down"}

# ------------------------------------------------------------------ impact filter
# "I don't want the low impact or medium, only those which can make a difference."
# 3 = moves the market on its own.  2 = real but slow, or usually already in the price.
# 1 = follows the price rather than leading it.
IMPACT = {
    # --- HIGH -------------------------------------------------------------------
    "rates_up": 3, "rates_down": 3, "fed_independence": 3,
    "inflation_hot": 3, "inflation_cold": 3,
    "geo_escalation": 3, "geo_deescalation": 3,
    "oil_supply_tight": 3, "oil_supply_loose": 3,
    # oil_inv_* WAS 3. Demoted on evidence: across 475 EIA releases the day's oil
    # direction beat its own baseline by +2pp either way, with ~0.00% excess move.
    # The weekly number genuinely is noise, exactly as this category's own FLIP line
    # says. It still shows in the feed with --all; it no longer buzzes the phone.
    "silver_squeeze": 3,
    "us_data_strong": 3, "us_data_weak": 3,
    # --- MEDIUM: real, but rarely the thing that turns a market ------------------
    "oil_inv_build": 2, "oil_inv_draw": 2,
    "demand_up": 2, "demand_down": 2, "tariff": 2,
    "cb_gold_buying": 2, "metal_supply": 2, "risk_off": 2,
    # --- LOW: confirmation, not a catalyst --------------------------------------
    "etf_inflow": 1, "etf_outflow": 1,
}
MIN_IMPACT = 3            # lower it with --all

ARROWS = {("up", 3): "↑↑↑", ("up", 2): "↑↑", ("up", 1): "↑",
          ("down", 3): "↓↓↓", ("down", 2): "↓↓", ("down", 1): "↓",
          ("flat", 1): "→", ("flat", 0): "→", ("up", 0): "→", ("down", 0): "→"}

CALL = {3: "STRONG — go hunt {side}s", 2: "look for {side}s", 1: "mild {side} bias",
        0: "no clean read"}


def _lean_line(label, lean):
    """'GOLD    DOWN DOWN   look for shorts - rates up = gold's worst enemy'"""
    direction, strength, reason = lean
    arrow = ARROWS.get((direction, strength), "→")
    if direction == "flat" or strength == 0:
        call = "no clean read"
    else:
        call = CALL[strength].format(side="short" if direction == "down" else "long")
    bits = f"{label:<7}{arrow:<4} {call}"
    if reason:
        bits += f" - {reason}"
    return bits


# ------------------------------------------------------------------ live reality check
def regime(lmap):
    """Which channel is the market ACTUALLY trading right now?

    Returns (tag, text). tag is 'yields' when gold is being driven by the
    oil -> inflation -> rates chain, in which case a war headline pushes gold DOWN,
    not up, and any 'geo -> gold up' textbook line has to be inverted.
    """
    if not lmap or not all(k in lmap for k in ("Gold", "US10Y", "WTI")):
        return "", ""
    g_dn = lmap["Gold"][0] < lmap["Gold"][1] * 0.995
    y_up = lmap["US10Y"][0] > lmap["US10Y"][1] + 0.03
    oil_up = lmap["WTI"][0] > lmap["WTI"][1] * 1.02
    if y_up and oil_up and g_dn:
        return "yields", ("REALITY CHECK: gold is FALLING while yields and oil RISE. The "
                          "market is trading oil -> inflation -> rates stay high, NOT "
                          "safety. Right now scary news pushes gold DOWN, not up.")
    if not g_dn and "S&P" in lmap and lmap["S&P"][0] < lmap["S&P"][1] * 0.985:
        return "haven", ("REALITY CHECK: gold firm while shares fall - the safety trade IS "
                         "working, so the textbook read below holds.")
    return "", ""


def apply_regime(dec, tag):
    """In a yields-driven regime, invert the gold/silver lean on fear-driven categories.

    BOTH directions. This only flipped "up" to "down" at first, which quietly left the
    more valuable half wrong: when the market is trading oil -> inflation -> rates, a
    CEASEFIRE takes the inflation premium out, yields fall and gold RALLIES - but the
    textbook line ("fear unwinds, gold down") was still being printed.
    """
    out = dict(dec)
    if tag == "yields" and dec.get("regime_sensitive"):
        flip = {"up": "down", "down": "up"}
        for k in ("gold", "silver"):
            d, s, _ = dec[k]
            if d in flip:
                out[k] = (flip[d], max(s - 1, 1), "FLIPPED - see the reality check below")
    return out


def decoded(cat, lmap):
    """The decode table for this category AFTER the live regime check has had its say.

    Everything downstream - body text and the phone title - must go through here, or the
    lock screen ends up saying GOLD UP while the alert itself says short it.
    """
    tag, rtext = regime(lmap)
    return apply_regime(DECODE[cat], tag), rtext


def market_closed(now=None):
    """Are the metals/crude futures shut right now?

    CME runs Sunday 22:00 UTC to Friday 21:00 UTC. Without this an alert fired on a
    Saturday shows Friday's closing prices with no warning, which reads as a live
    quote - the most misleading thing this tool could do, because weekend geopolitical
    news is exactly when the gap on reopening matters most.
    """
    n = now or dt.datetime.now(dt.timezone.utc)
    wd = n.weekday()                                   # Mon=0 .. Sun=6
    if wd == 5:                                        # Saturday
        return True
    if wd == 4 and n.hour >= 21:                       # Friday after the close
        return True
    if wd == 6 and n.hour < 22:                        # Sunday before the reopen
        return True
    return False


# The 90-minute cooldown stops one story buzzing fifteen times - but it also blocks a
# genuinely NEW development in the same category, and during a live conflict that is
# exactly when the second headline matters most. So the market gets a vote: if the
# instrument this category is really about has moved MOVE_OVERRIDE_PCT since the last
# alert, the story is treated as a new event and goes through anyway.
MOVE_OVERRIDE_PCT = 0.8
_SNAP_KEY = {"gold": "Gold", "silver": "Silver", "oil": "WTI"}


def lead_price(cat, snap):
    """Price of the instrument this category leans on hardest, or None."""
    dec = DECODE.get(cat)
    if not dec or not snap:
        return None
    best, strength = None, 0
    for k in ("gold", "silver", "oil"):
        d, s, _ = dec[k]
        if d != "flat" and s > strength:
            best, strength = k, s
    if not best:
        return None
    v = snap.get(_SNAP_KEY[best])
    return v[0] if v else None


def _snap(snap):
    order = [("Gold", 0), ("Silver", 2), ("WTI", 2)]
    bits = []
    for name, ndp in order:
        if name in snap:
            v, p = snap[name]
            bits.append(f"{name} {v:,.{ndp}f} ({p:+.1f}%)")
    for name in ("DXY", "US10Y"):
        if name in snap:
            v, p = snap[name]
            bits.append(f"{name} {v:,.2f} ({p:+.1f}%)")
    return "NOW  " + "   ".join(bits) if bits else ""


# ------------------------------------------------------------------ alert build
def soften(dec, note="talk, not action yet"):
    """One notch off every lean - intent moves markets less than the thing itself."""
    out = dict(dec)
    for k in ("gold", "silver", "oil"):
        d, s, r = dec[k]
        if d != "flat" and s > 1:
            out[k] = (d, s - 1, f"{r} ({note})")
    return out


def parts(cat, headline, src, snap, lmap, talk=False):
    """The alert broken into fields, so the phone gets flat text and the in-app feed
    gets a properly laid-out card off the same decode."""
    dec, rtext = decoded(cat, lmap)
    if talk:
        dec = soften(dec)
    leans = []
    for key, label in (("gold", "GOLD"), ("silver", "SILVER"), ("oil", "OIL")):
        d, s, reason = dec[key]
        call = ("no clean read" if d == "flat" or s == 0
                else CALL[s].format(side="short" if d == "down" else "long"))
        leans.append({"label": label, "arrow": ARROWS.get((d, s), "→"), "dir": d,
                      "strength": s, "call": call, "reason": reason})
    return {"emoji": dec["emoji"], "label": dec["label"], "headline": headline,
            "src": src, "why": dec["why"], "leans": leans, "snap": _snap(snap),
            "reality": rtext, "flip": dec["flip"]}


def build(cat, headline, src, snap, lmap, when=None, talk=False):
    """Flat text for the ntfy push."""
    p = parts(cat, headline, src, snap, lmap, talk)
    lines = [f"{p['emoji']} {p['label']}", ""]
    lines.append(p["headline"] + (f"  ({p['src']})" if p["src"] else ""))
    if talk:
        lines.append("")
        lines.append("^ This is TALK, not something that has happened yet. "
                     "Conviction cut one notch.")
    lines += ["", "WHY: " + p["why"], ""]
    for ln in p["leans"]:
        row = f"{ln['label']:<7}{ln['arrow']:<4} {ln['call']}"
        if ln["reason"]:
            row += f" - {ln['reason']}"
        lines.append(row)
    if p["snap"]:
        lines += ["", p["snap"]]
        if market_closed():
            lines.append("^ MARKETS ARE SHUT - that is the last close, not a live price. "
                         "Expect a gap in this direction when they reopen Sunday 22:00 UTC.")
    if p["reality"]:
        lines += ["", p["reality"]]
    lines += ["", "FLIP: " + p["flip"]]
    return "\n".join(lines)


def headline_title(cat, dec=None):
    """The one line that shows on the phone's lock screen - lead with the lean.

    ASCII ONLY, deliberately. ntfy sends the title as an HTTP header and non-ASCII
    gets replaced character by character: the first live push landed on the phone as
    "GOLD ?? - US DATA ? came in weak", which throws away the entire point of the
    title. So the arrows become words here, and the body keeps the real arrows.

    Pass the REGIME-ADJUSTED dec from decoded(), never the raw table.
    """
    dec = dec or DECODE[cat]
    # Group by direction AND strength. Lumping them together produced "GOLD+OIL STRONG
    # SHORT" for a case where oil was strong and gold only moderate - overstating half
    # the call on the one line you read without unlocking the phone.
    groups = {}
    for key, label in (("gold", "GOLD"), ("silver", "SILVER"), ("oil", "OIL")):
        d, s, _ = dec[key]
        if d == "flat" or s < 2:
            continue
        groups.setdefault((d, s >= 3), []).append(label)

    side = []
    for (d, strong) in sorted(groups, key=lambda g: (not g[1], g[0])):
        word = ("STRONG " if strong else "") + ("LONG" if d == "up" else "SHORT")
        side.append("+".join(groups[(d, strong)]) + " " + word)

    label = dec["label"].replace("—", "-")
    if not side:
        return _ascii(label)
    return _ascii(", ".join(side) + " | " + label)


def _ascii(s):
    """Last-resort transliteration so nothing reaches the header as '?'."""
    return (s.replace("—", "-").replace("–", "-")
             .replace("’", "'").replace("‘", "'")
             .encode("ascii", "ignore").decode().strip())


# ------------------------------------------------------------------ state
def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return default


def load_seen():
    """Two kinds of entry live in here: story hashes (-> timestamp) and category
    cooldowns keyed 'cat:<name>' (-> [timestamp, severity])."""
    raw = _load(SEEN_FILE, {})
    cutoff = time.time() - SEEN_TTL_DAYS * 86400
    out = {}
    for k, v in raw.items():
        ts = v[0] if isinstance(v, list) else v
        if isinstance(ts, (int, float)) and ts > cutoff:
            out[k] = v
    return out


def save_seen(seen):
    DATA.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=0), encoding="utf-8")


def feed_add(entry):
    feed = _load(FEED_FILE, [])
    feed.insert(0, entry)
    DATA.mkdir(exist_ok=True)
    FEED_FILE.write_text(json.dumps(feed[:FEED_KEEP], indent=1), encoding="utf-8")


# ------------------------------------------------------------------ classify + gather
# Extra kill-list on top of news_watch's VETO. Every one of these came from a real false
# positive in the --audit run: calendar previews, mortgage-rate stories, lawsuits, denials.
VETO_EXTRA = (
    "main events for today", "event calendar", "economic calendar", "week ahead",
    "what to watch", "preview", "session wrap", "news wrap", "market news:",
    "mortgage", "refinanc", "savings account", "credit card",
    "price-fixing", "lawsuit", "court", "denies", "denied", "dismisses claim",
    "borrowers", "here's what", "here is what", "5 things", "what it means for you",
    "here's why", "here is why", "price forecast", "technical analysis",
)

# rates / inflation categories only matter to the metals when the story is about the US.
# Without this gate an Australian GDP print and an RBNZ calendar row both fired as
# "FED - leaning toward higher rates".
US_CTX = ("fed", "fomc", "powell", "warsh", "u.s.", "us ", " us", "america", "dollar",
          "treasury", "washington", "cpi", "pce", "nonfarm", "payroll", "jackson hole")
US_GATED = ("rates_up", "rates_down", "inflation_hot", "inflation_cold")


# A negated rate headline means the OPPOSITE of the keyword it contains, and the rate
# channel is gold's biggest driver, so getting this backwards is the worst error the
# decoder can make. "Fed rules out a rate cut" fired as rates_down - i.e. it told you to
# buy gold - when it means no cuts are coming, which is gold-negative. Flip instead of
# vetoing: "rules out a cut" genuinely IS a lean toward higher rates, and vice versa.
NEG_BEFORE = ("rules out", "ruled out", "rule out", "ruling out", "no rate", "no need for",
              "not raise", "will not raise", "won't raise", "wont raise", "unlikely to",
              "against a rate", "dismisses", "denies", "ends bets on", "kills bets on",
              "plays down", "downplays", "pours cold water on")
NEG_AFTER = ("off the table", "is unlikely", "was ruled out", "not happening",
             "is not coming", "not on the table")
FLIP = {"rates_up": "rates_down", "rates_down": "rates_up",
        "inflation_hot": "inflation_cold", "inflation_cold": "inflation_hot"}

# A headline pointing BOTH ways is not a trade lean. "Trump Pushes Rate Cuts as Markets
# Price in Fed Hike" fired as rates_down - a STRONG LONG GOLD call - off a story whose
# actual market content is a hike being priced. Ambiguous rate headlines now fire nothing.
HIGHER_TOK = ("hike", "raise", "rises", "higher", "tighten")
LOWER_TOK = ("cut", "lower", "easing", "ease", "reduction")
RATE_CATS = ("rates_up", "rates_down", "fed_independence")

# Inventory words are worthless without a crude context: "Oil Stocks Jump" is a story
# about Chevron and Exxon share prices, and it fired as OIL SHORT (stockpiles building).
OIL_INV_CTX = ("crude", "inventor", "stockpile", "barrel", "eia", "api", "petroleum")


# TALK vs ACTION. The classifier was reading a politician's sentence as an event:
# "Trump: Want to do a Putin summit when we're ready for a peace deal" matched
# "peace deal" and fired OIL STRONG SHORT - go hunt shorts - off a headline that
# says a peace deal does NOT exist. It landed in the same minute as an escalation
# alert pointing the other way.
# Intent is not an event. Talk still matters (oil does move when Trump threatens
# Iran), so it is not vetoed outright - it is marked and its conviction is cut.
TALK_MARKERS = (
    "prepared to", "ready to", "want to", "wants to", "would like", "hopes to",
    "aims to", "plans to", "planning to", "considering", "may ", "might ", "could ",
    "would ", "open to", "willing to", "threatens", "threatened", "warns", "warned",
    "vows", "vowed", "urges", "calls for", "pushes for", "when we", "if needed",
    "says he", "says she", "said he", "said she", "suggests", "proposes", "proposal",
    "talks about", "in talks", "seeking", "expected to", "set to", "poised to",
)
# Words that mean it actually happened. A de-escalation headline needs one of these,
# because "peace deal" as an aspiration is constant background noise on the wires
# while an actual ceasefire is a genuine, tradeable event.
CONCRETE = (
    "agreed", "agrees", "signed", "signs", "reached", "reaches", "announced",
    "announces", "declared", "declares", "takes effect", "in effect", "holds",
    "begins", "began", "started", "starts", "confirmed", "confirms", "struck",
    "brokered", "finalised", "finalized", "implemented", "came into force",
)


def is_talk(title_lc):
    """A statement of intent rather than something that happened."""
    return any(m in title_lc for m in TALK_MARKERS)


def is_concrete(title_lc):
    return any(m in title_lc for m in CONCRETE)


def _negated(title_lc, pos, kw_len):
    """Is the keyword at `pos` sitting inside a negation?"""
    # the window has to overlap the keyword itself, or "no rate cut" is missed: the
    # negation ("no rate") straddles the boundary with the keyword ("rate cut").
    before = title_lc[max(0, pos - 22):pos + kw_len]
    after = title_lc[pos + kw_len:pos + kw_len + 26]
    return any(n in before for n in NEG_BEFORE) or any(n in after for n in NEG_AFTER)


def classify(title, desc=""):
    """Match on the HEADLINE, not the blurb.

    Classifying over title+description was the single biggest source of junk: an RSS
    blurb mentioning "ceasefire" turned a story about a fresh attack into a
    de-escalation alert. The description is now only allowed to veto, never to fire.
    """
    t = title.lower()
    # Half the wires write "rate-hike", "Fed-hike", "US-Iran strikes". Matching only the
    # spaced form silently dropped 5 unique stories in one 3-day sample, Bloomberg among
    # them. replace() is length-preserving, so offsets stay valid for the negation check.
    t_n = t.replace("-", " ")
    blob = (title + " " + desc).lower()
    if any(v in blob for v in VETO) or any(v in t_n for v in VETO_EXTRA):
        return None
    for cat, sev, keys in RULES:
        for k in keys:
            pos = t.find(k)
            if pos < 0:
                pos = t_n.find(k)               # hyphenated variant
            if pos < 0:
                continue
            if cat in US_GATED and not any(c in t_n for c in US_CTX):
                continue                        # a non-US rate story, not our business
            if cat in RATE_CATS and any(h in t_n for h in HIGHER_TOK) \
                    and any(l in t_n for l in LOWER_TOK):
                return None                     # points both ways - no lean to give
            if cat in ("oil_inv_build", "oil_inv_draw") \
                    and not any(c in t_n for c in OIL_INV_CTX):
                continue                        # "oil stocks" = share prices, not barrels
            if cat in FLIP and _negated(t_n, pos, len(k)):
                return FLIP[cat], sev, f"NOT {k}"
            # A ceasefire that has not happened is not a ceasefire.
            if cat == "geo_deescalation" and is_talk(t_n) and not is_concrete(t_n):
                continue
            if is_talk(t_n):
                return cat, max(sev - 1, 1), f"TALK:{k}"
            return cat, sev, k
    return None


def gather():
    """Same shape as news_watch.gather_items, but over the commodity feeds."""
    import urllib.request
    import xml.etree.ElementTree as ET
    now = dt.datetime.now(dt.timezone.utc)
    fresh, seen_titles = [], set()
    for url in FEEDS + _slow_due():
        raw = _get(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link or title.lower() in seen_titles:
                continue
            desc = re.sub("<[^>]+>", " ", item.findtext("description") or "")
            pub = _parse_date(item.findtext("pubDate") or "")
            if pub is None or (now - pub).total_seconds() > MAX_AGE_MIN * 60:
                continue
            se = item.find("source")
            seen_titles.add(title.lower())
            fresh.append({"title": title, "link": link, "desc": desc.strip(),
                          "src": (se.text or "").strip() if se is not None else "",
                          "pub": pub})
    return fresh


# Releases that actually move metals and oil, and what channel each one runs through.
# This is a WHITELIST rather than a reliance on the feed's own importance flag: the
# 2 Sep ADP print (38k vs 47k expected - a clean gold-positive miss) came through the
# calendar rated importance 0, so an importance>=1 filter dropped it on the floor.
#   jobs / growth  - higher number = stronger economy = rates stay high = gold down
#   jobs_inv       - higher number = WEAKER economy (unemployment, jobless claims)
#   inflation      - higher = hotter
#   oil_inv        - higher = more oil in storage = bearish crude
KEY_RELEASES = {
    "adp employment": "jobs", "nonfarm payroll": "jobs", "non-farm payroll": "jobs",
    "jolts": "jobs", "employment change": "jobs", "challenger": "jobs_inv",
    "unemployment rate": "jobs_inv", "initial jobless": "jobs_inv",
    "continuing jobless": "jobs_inv", "jobless claims": "jobs_inv",
    "cpi": "inflation", "ppi": "inflation", "pce": "inflation",
    "average hourly earnings": "inflation", "inflation rate": "inflation",
    "ism manufacturing": "growth", "ism services": "growth",
    "ism non-manufacturing": "growth", "retail sales": "growth", "gdp": "growth",
    "durable goods": "growth", "consumer confidence": "growth",
    "michigan": "growth", "pmi": "growth",
    "crude oil inventories": "oil_inv", "eia": "oil_inv", "api weekly": "oil_inv",
    "gasoline inventories": "oil_inv", "distillate": "oil_inv",
}
SURPRISE_MIN = 0.10       # 10% off forecast counts (the FX watcher uses 15%)
OIL_INV_MIN = 0.50        # EIA weekly is noisy - needs a big miss to mean anything

# Which releases are worth a phone buzz. Everything else in KEY_RELEASES still gets
# classified, but only fires when MIN_IMPACT is lowered with --all.
RELEASE_TIER = {
    "nonfarm payroll": 3, "non-farm payroll": 3, "unemployment rate": 3,
    "adp employment": 3, "initial jobless": 3, "average hourly earnings": 3,
    "cpi": 3, "ppi": 3, "pce": 3, "inflation rate": 3,
    "ism manufacturing": 3, "ism services": 3, "ism non-manufacturing": 3,
    "retail sales": 3, "gdp": 3,
    "crude oil inventories": 2, "eia": 2,       # demoted with the categories above
}

# Percent-of-forecast is the wrong test for anything already expressed as a rate: an
# unemployment rate of 4.3 against a 4.2 forecast is a 2.4% "miss" and would never clear
# a 10% threshold, yet it is major news. These are judged on the absolute gap instead.
ABS_THRESHOLD = {
    "unemployment rate": 0.1, "inflation rate": 0.1, "cpi": 0.1, "pce": 0.1,
    "average hourly earnings": 0.1, "gdp": 0.2, "ppi": 0.1,
}


def _channel(title):
    t = title.lower()
    for k, v in KEY_RELEASES.items():
        if k in t:
            return v
    return None


def categorise_release(title, actual, forecast):
    """(category, tier, surprise) for one economic release, or None if it does not qualify.

    Split out of calendar_hits so backtest_decode.py can score history through the exact
    same logic the live watcher uses - a re-implementation would drift and quietly make
    the backtest meaningless.
    """
    if actual is None or forecast is None:
        return None
    ch = _channel(title)
    if ch is None:
        return None
    tl = title.lower()
    denom = abs(forecast) if abs(forecast) > 1e-9 else 1.0
    surp = (actual - forecast) / denom
    abs_gate = next((v for k, v in ABS_THRESHOLD.items() if k in tl), None)
    if abs_gate is not None:
        if abs(actual - forecast) < abs_gate:
            return None
    elif abs(surp) < (OIL_INV_MIN if ch == "oil_inv" else SURPRISE_MIN):
        return None
    tier = next((v for k, v in RELEASE_TIER.items() if k in tl), 2)
    above = surp > 0
    if ch == "oil_inv":
        cat = "oil_inv_build" if above else "oil_inv_draw"
    elif ch == "inflation":
        cat = "inflation_hot" if above else "inflation_cold"
    else:
        strong = above if ch != "jobs_inv" else not above
        cat = "us_data_strong" if strong else "us_data_weak"
    return cat, tier, surp


def calendar_hits(window_min=50):
    """US prints released in the last window_min minutes that matter to gold/silver/oil."""
    import urllib.request
    try:
        from fetch_calendar import API, HEADERS
    except Exception:                                          # noqa: BLE001
        return []
    now = dt.datetime.now(dt.timezone.utc)
    frm = (now - dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to = (now + dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        req = urllib.request.Request(f"{API}?from={frm}&to={to}&countries=US", headers=HEADERS)
        rows = json.loads(urllib.request.urlopen(req, timeout=25).read()).get("result", [])
    except Exception as e:                                     # noqa: BLE001
        print(f"  calendar failed: {type(e).__name__}")
        return []

    out = []
    for e in rows:
        title = e.get("title", "")
        when = _parse_date(e.get("date", ""))
        if when is None or when > now or (now - when).total_seconds() > window_min * 60:
            continue
        hit = categorise_release(title, e.get("actual"), e.get("forecast"))
        if hit is None:
            continue
        cat, tier, surp = hit
        if tier < MIN_IMPACT:
            continue
        out.append({"title": title, "actual": e.get("actual"),
                    "forecast": e.get("forecast"), "surp": surp,
                    "cat": cat, "when": when})
    return out


# ------------------------------------------------------------------ the feed page
def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _card(e):
    p = e.get("parts")
    if not p:                                   # entry written by an older version
        return (f'<article class="cn-card"><div class="cn-when">{_esc(e["when"])}</div>'
                f'<pre class="cn-body">{_esc(e.get("body", ""))}</pre></article>')

    rows = ""
    for ln in p["leans"]:
        cls = {"up": "cn-up", "down": "cn-dn"}.get(ln["dir"], "cn-fl")
        why = (f'<div class="cn-why">{_esc(ln["reason"])}</div>'
               if ln["reason"] else "")
        rows += (f'<tr><td class="cn-inst">{ln["label"]}</td>'
                 f'<td class="cn-arrow {cls}">{ln["arrow"]}</td>'
                 f'<td><div class="cn-call {cls}">{_esc(ln["call"])}</div>'
                 f'{why}</td></tr>')

    src = f' <span class="cn-src">{_esc(p["src"])}</span>' if p.get("src") else ""
    # data-ts lets the page render "12 min ago" when the reader opens it, rather than
    # freezing a relative time at build time. Falls back to the stamped UTC string.
    ts = f' data-ts="{_esc(e.get("iso", ""))}"' if e.get("iso") else ""
    out = [f'<article class="cn-card"><div class="cn-when"{ts}>'
           f'<span class="cn-ago">{_esc(e["when"])}</span> '
           f'&middot; {_esc(p["label"])}</div>',
           f'<h3 class="cn-head">{p["emoji"]} {_esc(p["headline"])}{src}</h3>',
           f'<p class="cn-why-p">{_esc(p["why"])}</p>',
           f'<table class="cn-leans">{rows}</table>']
    if p.get("snap"):
        out.append(f'<div class="cn-snap">{_esc(p["snap"])}</div>')
    if p.get("reality"):
        out.append(f'<div class="cn-real">{_esc(p["reality"])}</div>')
    out.append(f'<div class="cn-flip"><b>What flips it:</b> {_esc(p["flip"])}</div>')
    # No "read the story" link, by request. The decode is the product; sending the
    # reader off to the article defeats the point of having decoded it. The URL is
    # still on the feed entry if anything ever needs it.
    out.append("</article>")
    return "".join(out)


CSS = """
<style>
.cn-wrap{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 margin:18px 0 26px}
.cn-h{font-size:15px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;
 opacity:.75;margin:0 0 4px}
.cn-sub{font-size:12px;opacity:.55;margin:0 0 12px}
.cn-card{border:1px solid rgba(128,128,128,.28);border-radius:10px;padding:12px 14px;
 margin:0 0 10px;background:rgba(128,128,128,.06)}
.cn-when{font-size:11px;letter-spacing:.05em;opacity:.55;margin-bottom:6px;
 text-transform:uppercase}
.cn-head{font-size:14.5px;font-weight:650;margin:0 0 6px;line-height:1.35}
.cn-src{font-size:11px;font-weight:400;opacity:.5}
.cn-why-p{font-size:13px;margin:0 0 10px;opacity:.85}
.cn-leans{border-collapse:collapse;width:100%;table-layout:fixed;margin:0 0 10px}
.cn-leans td{padding:3px 0;vertical-align:baseline;font-size:13px}
.cn-inst{font-weight:700;letter-spacing:.04em;width:62px;vertical-align:top}
.cn-arrow{font-size:15px;font-weight:700;width:40px;letter-spacing:-1px;vertical-align:top}
.cn-call{font-weight:600}
.cn-why{opacity:.6;font-size:12px;line-height:1.35;margin-top:1px}
.cn-up{color:#12924b}.cn-dn{color:#d1344a}.cn-fl{opacity:.5}
.cn-snap{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
 opacity:.7;padding:7px 9px;border-radius:6px;background:rgba(128,128,128,.12);
 overflow-wrap:anywhere}
.cn-real{font-size:12.5px;margin-top:8px;padding:8px 10px;border-radius:6px;
 border-left:3px solid #d99000;background:rgba(217,144,0,.10)}
.cn-flip{font-size:12px;opacity:.65;margin-top:8px}
.cn-empty{opacity:.55;font-size:13px;padding:10px 0}
@media (prefers-color-scheme:dark){.cn-up{color:#35d07f}.cn-dn{color:#ff6b7d}}
</style>
"""


def render_block(feed, limit=12):
    cards = "".join(_card(e) for e in feed[:limit]) or (
        '<div class="cn-empty">No commodity news has cleared the filter yet. '
        'Alerts land here the moment something moves gold, silver or oil.</div>')
    return (CSS + '<section class="cn-wrap" id="commodity-news">'
            '<h2 class="cn-h">Gold / Silver / Oil — decoded</h2>'
            '<p class="cn-sub">Breaking news, in plain words, with which way it leans. '
            'Newest first.</p>' + cards + '</section>')


MARK_A, MARK_B = "<!--COMMODITY_NEWS_START-->", "<!--COMMODITY_NEWS_END-->"


def inject(html_path, block):
    """Drop the block into dashboard.html between markers. Idempotent, and it never
    touches anything build_dashboard.py owns."""
    p = Path(html_path)
    if not p.exists():
        print(f"  inject: {p} not found, skipped")
        return False
    html = p.read_text(encoding="utf-8")
    wrapped = MARK_A + block + MARK_B
    if MARK_A in html and MARK_B in html:
        html = re.sub(re.escape(MARK_A) + ".*?" + re.escape(MARK_B), lambda _: wrapped,
                      html, flags=re.S)
    else:
        # Top of the VISIBLE content, not the top of the file. The desk page is long and
        # on a phone the news has to be the first thing you see - but it is also a bare
        # fragment with no <body> (it opens with <title>/<link>/<style>), so prepending
        # blindly puts the block above the document's own head content. Anchor on the
        # first <header> instead, and only fall back to appending.
        m = (re.search(r"<body[^>]*>", html, flags=re.I))
        if m:
            html = html[:m.end()] + wrapped + html[m.end():]
        else:
            m = re.search(r"<header[^>]*>", html, flags=re.I)
            if m:
                html = html[:m.start()] + wrapped + html[m.start():]
            elif "</body>" in html:
                html = html.replace("</body>", wrapped + "\n</body>")
            else:
                html += wrapped
    p.write_text(html, encoding="utf-8")
    print(f"  injected the news block into {p.name}")
    return True


def render(dashboard=None):
    feed = _load(FEED_FILE, [])
    block = render_block(feed)
    out = Path(__file__).parent / "commodity-news.html"
    out.write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1">'
        '<title>Gold / Silver / Oil - decoded</title>'
        '<body style="margin:0;padding:16px;max-width:760px">' + block + '</body>',
        encoding="utf-8")
    print(f"  wrote {out.name} ({len(feed)} alert(s) in the feed)")
    for cand in ([dashboard] if dashboard else
                 [Path(__file__).parent / "dashboard.html"]):
        inject(cand, block)


# ------------------------------------------------------------------ main
def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        tf = DATA / "ntfy_topic.txt"
        if tf.exists():
            topic = tf.read_text(encoding="utf-8").strip()

    argv = sys.argv[1:]

    if "--all" in argv:               # include medium/low impact as well
        global MIN_IMPACT
        MIN_IMPACT = 1

    # Silence here is indistinguishable from "no news", which is how a whole day can go by
    # with nothing on the phone and nothing obviously wrong. Say it loudly instead.
    if not topic and not any(a in argv for a in ("--render", "--audit", "--replay")):
        print("!" * 70)
        print("! NTFY_TOPIC is not set - alerts will be printed here and NOT sent to the")
        print("! phone. On GitHub: Settings > Secrets and variables > Actions > NTFY_TOPIC.")
        print("!" * 70)

    if "--render" in argv:
        i = argv.index("--render")
        dash = argv[i + 1] if len(argv) > i + 1 and not argv[i + 1].startswith("-") else None
        render(dash)
        return

    if "--ping" in argv:
        # Once per cloud run, at min priority: silent on the phone, but it proves the
        # NTFY_TOPIC secret is wired and the watcher is actually alive. Worth having,
        # because GitHub's scheduler drops runs silently and "no alerts" otherwise looks
        # identical to "no news".
        # Exit non-zero when the topic is missing. push() returns quietly in that case,
        # so the step passed while delivering nothing - which is indistinguishable from
        # a working pipe when you cannot read the run logs. A failed step IS readable,
        # via the public /actions/runs/<id>/jobs API. The workflow marks this step
        # continue-on-error so a missing secret never stops the watcher itself.
        if not topic:
            print("PING FAILED: NTFY_TOPIC is empty inside the job. Either the secret is "
                  "not set, is named something other than NTFY_TOPIC, or was added as an "
                  "Environment/Dependabot secret rather than an Actions repository secret.")
            sys.exit(1)
        # Diagnosing the case where the secret IS set, the POST returns 200, and yet the
        # phone gets nothing - i.e. the secret holds a DIFFERENT topic from the one the
        # phone subscribes to. Comparing fingerprints answers that from outside the run,
        # since exit codes are visible via the public /actions/runs/<id>/jobs endpoint
        # while logs need auth. 6 hex is 24 bits: it matches ~16.7 million strings, so
        # publishing it in a public repo identifies nothing, but a mismatch is conclusive.
        fp = hashlib.sha256(topic.encode()).hexdigest()[:6]
        wrong_topic = fp != EXPECTED_TOPIC_FP
        ok = push(topic, "watcher online",
                  "Commodity + FX watcher started on GitHub Actions. Scanning every 5 "
                  "minutes for the next ~5.5 hours. This is a silent status ping.",
                  "https://haroontahir73.github.io/fxstrength-app/dashboard.html",
                  "min", "satellite")
        print(f"ping: topic set ({len(topic)} chars), fingerprint {fp},",
              "delivered" if ok else "POST FAILED")
        if not ok:
            sys.exit(2)
        if wrong_topic:
            print(f"PING WENT TO THE WRONG TOPIC. The secret's fingerprint is {fp}; the "
                  f"topic the phone subscribes to fingerprints as {EXPECTED_TOPIC_FP}. "
                  f"The message was delivered - just not where anyone is listening. "
                  f"Re-save the NTFY_TOPIC secret with the correct topic.")
            sys.exit(3)
        sys.exit(0)

    if "--test" in argv:
        body = build("rates_up", "TEST - Fed's Warsh says rates may have to rise",
                     "test", market_snapshot(), {})
        ok = push(topic, "TEST - " + headline_title("rates_up"), body, "", "default")
        print(body)
        print("test push:", "sent" if ok else "not sent (no topic)")
        return

    if "--replay" in argv:
        # what 27 Aug 2026 would have looked like on the phone
        snap = market_snapshot()
        body = build("rates_up",
                     "Fed Chair Warsh: inflation 'concerning', rates 'may have to' rise in "
                     "coming months", "Jackson Hole", snap, level_map())
        print("TITLE:", headline_title("rates_up"))
        print("-" * 62)
        print(body)
        print("-" * 62)
        return

    if "--audit" in argv:
        global MAX_AGE_MIN
        MAX_AGE_MIN = 60 * 24 * 3
        n = hits = 0
        for it in gather():
            n += 1
            c = classify(it["title"], it["desc"])
            if c:
                hits += 1
                print(f"  [{c[0]}/{c[1]}] {it['title'][:100]}  <{c[2]}>")
        print(f"audit: {hits}/{n} items would alert")
        return

    dry = "--dry-run" in argv
    seen = load_seen()
    items = gather()
    cal = calendar_hits()
    snap = market_snapshot() if (items or cal) else {}
    lmap = level_map() if (items or cal) else {}
    fired = 0

    def emit(cat, head, link, src, talk=False):
        nonlocal fired
        dec, _ = decoded(cat, lmap)
        if talk:
            dec = soften(dec)
        body = build(cat, head, src, snap, lmap, talk=talk)
        title = ("TALK | " if talk else "") + headline_title(cat, dec)
        print(f"\n[{cat}] {title}\n" + "-" * 60 + f"\n{body}\n" + "-" * 60)
        if not dry:
            # No link on the notification, by request: passing one makes ntfy attach a
            # Click action, so the alert turns into a doorway to the news site. The whole
            # point is that the decode replaces reading the article. The story URL is
            # still kept on the feed entry below, where the in-app card offers it quietly.
            push(topic, title, body, "",
                 "urgent" if any(dec[k][1] >= 3 for k in ("gold", "silver", "oil"))
                 else "high", "coin")
            feed_add({"when": dt.datetime.now(dt.timezone.utc).strftime("%d %b %H:%M UTC"),
                      "iso": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "cat": cat, "title": title, "body": body, "link": link,
                      "talk": talk,
                      "parts": parts(cat, head, src, snap, lmap, talk)})
        fired += 1

    OPPOSITE = {"geo_escalation": "geo_deescalation", "geo_deescalation": "geo_escalation",
                "oil_supply_tight": "oil_supply_loose", "oil_supply_loose": "oil_supply_tight",
                "rates_up": "rates_down", "rates_down": "rates_up",
                "inflation_hot": "inflation_cold", "inflation_cold": "inflation_hot",
                "us_data_strong": "us_data_weak", "us_data_weak": "us_data_strong",
                "oil_inv_build": "oil_inv_draw", "oil_inv_draw": "oil_inv_build",
                "demand_up": "demand_down", "demand_down": "demand_up"}

    def contradicts(cat):
        """True if the opposite call went out moments ago. The market cannot be both
        escalating and calming down, and sending both makes the whole thing untrustworthy
        - which is exactly what happened at 18:31 on 2 Sep."""
        prev = seen.get(f"cat:{OPPOSITE.get(cat, '')}")
        return bool(prev) and (time.time() - prev[0]) / 60 < 30

    def on_cooldown(cat, sev):
        """True if this category already fired recently and nothing has moved since."""
        prev = seen.get(f"cat:{cat}")
        if not prev:
            return False
        age_min = (time.time() - prev[0]) / 60
        if age_min >= COOLDOWN_MIN or sev > prev[1]:
            return False
        # the market's vote: a real move since the last alert means a real new event
        now_px = lead_price(cat, snap)
        then_px = prev[2] if len(prev) > 2 else None
        if now_px and then_px:
            moved = abs(now_px - then_px) / then_px * 100
            if moved >= MOVE_OVERRIDE_PCT:
                print(f"  [cooldown overridden] {cat}: lead price moved {moved:.1f}% "
                      f"since the last alert")
                return False
        return True

    for it in items:
        h = hashlib.sha1(it["title"][:120].encode("utf-8", "replace")).hexdigest()[:16]
        if h in seen:
            continue
        hit = classify(it["title"], it["desc"])
        if not hit:
            continue
        cat, sev, _ = hit
        seen[h] = time.time()
        if IMPACT.get(cat, 2) < MIN_IMPACT:
            print(f"  [impact {IMPACT.get(cat, 2)} < {MIN_IMPACT}] {cat}: {it['title'][:70]}")
            continue
        if contradicts(cat):
            print(f"  [contradicts a fresh {OPPOSITE[cat]} alert] {cat}: {it['title'][:60]}")
            continue
        if on_cooldown(cat, sev):
            print(f"  [cooldown] {cat}: {it['title'][:80]}")
            continue
        if not dry and not theme_claim(cat, "commodity"):
            print(f"  [claimed by the FX watcher] {cat}: {it['title'][:70]}")
            continue
        seen[f"cat:{cat}"] = [time.time(), sev, lead_price(cat, snap)]
        emit(cat, it["title"], it["link"], it["src"], talk=hit[2].startswith("TALK:"))

    for s in cal:
        key = f"cal:{s['title']}:{s['actual']}"
        h = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
        if h in seen:
            continue
        seen[h] = time.time()
        verdict = "a MISS" if s["surp"] < 0 else "a BEAT"
        head = (f"US {s['title']}: {s['actual']} vs {s['forecast']} expected - {verdict} "
                f"({s['surp']*100:+.0f}%)")
        # a real data print always goes through - it IS the event, not coverage of it
        seen[f"cat:{s['cat']}"] = [time.time(), 3, lead_price(s["cat"], snap)]
        emit(s["cat"], head, "", "economic calendar")

    if not dry:
        save_seen(seen)
        render()
    print(f"done - {fired} alert(s), {len(seen)} hashes tracked")


if __name__ == "__main__":
    main()
