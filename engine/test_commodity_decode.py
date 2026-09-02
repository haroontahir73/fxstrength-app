"""Regression tests for the commodity news decoder.

Every case here is one that was ACTUALLY WRONG at some point, found by running
`commodity_watch.py --audit` against the live wires. Keyword classifiers rot as
rules get added - a new keyword silently steals headlines from an existing rule -
so run this after touching RULES, VETO_EXTRA or the DECODE table.

    python test_commodity_decode.py
"""
import sys
import commodity_watch as cw

# (headline, expected category or "NO MATCH", why this case exists)
CASES = [
    # --- negation: these were all decoded BACKWARDS, on gold's biggest driver -------
    ("Fed rules out a rate cut this year", "rates_up",
     "ruling out a cut IS a lean toward higher rates - fired as rates_down"),
    ("Powell: no rate cut until inflation is beaten", "rates_up",
     "negation straddles the keyword boundary ('no rate' + 'rate cut')"),
    ("Warsh rules out a rate hike in September", "rates_down",
     "the mirror case"),
    ("Fed officials say a rate hike is off the table", "rates_down",
     "negation sits AFTER the keyword"),
    ("Fed unlikely to cut rates before December, says Barr", "rates_up", ""),
    ("Warsh dismisses talk of a rate cut", "rates_up", ""),

    # --- the plain readings must survive the negation logic ------------------------
    ("Warsh signals rates may have to rise in coming months", "rates_up",
     "the 27 Aug reference case"),
    ("Fed cuts rates by 25 basis points", "rates_down", ""),
    ("Investors see 70% chance of Federal Reserve rate hike", "rates_up", ""),
    ("Fed pushes back on rate cut bets", "rates_up",
     "'pushes back on' must NOT read as a negation of the hike lean"),
    ("Fed delivers emergency cut as jobs collapse", "rates_down", ""),
    ("US CPI comes in hotter than expected", "inflation_hot", ""),
    ("US inflation cools more than expected in August", "inflation_cold", ""),

    # --- ambiguity: a headline pointing both ways is not a lean --------------------
    ("Trump Pushes Rate Cuts as Markets Price in Fed Hike", "NO MATCH",
     "fired as STRONG LONG GOLD off a story about a hike being priced"),

    # --- wrong-sense keyword matches ------------------------------------------------
    ("Chevron, Exxon and Other Oil Stocks Jump as Two Huge Energy Stories Collide",
     "NO MATCH", "'oil stocks' = share prices; fired as OIL SHORT on stockpiles"),
    ("CENTCOM Denies IRGC Claims of Supertanker Mine Strike", "NO MATCH",
     "'mine strike' was a NAVAL mine, not a miners' strike"),
    ("EIA: crude stockpiles build by 5 million barrels", "oil_inv_build",
     "the genuine inventory case still has to fire"),

    # --- opinion columns dressed as news --------------------------------------------
    ("Gold Isn't Falling on a Hawkish Fed. Here's Why.", "NO MATCH", ""),
    ("Gold Price Forecast: XAU/USD extends reversal below 4400", "NO MATCH", ""),
    ("Economic and event calendar in Asia Wednesday - RBZN rate hike expected", "NO MATCH",
     "calendar previews are not news"),

    # --- non-US rate stories are not our business -----------------------------------
    ("Australian Q2 GDP 0.4% q/q (expected 0.3%, prior 0.3%)", "NO MATCH",
     "fired as 'FED - leaning toward higher rates'"),

    # --- RECALL: these fired NOTHING, which is the failure you never notice ----------
    # Found by listing commodity-relevant headlines that classified to nothing at all.
    # Precision testing (is what it fires correct?) will never surface these.
    ("Gold Steadies After Tumbling as Warsh Spurs Fed Rate-Hike Bets", "rates_up",
     "outlets write 'rate-hike'; only the spaced form was matched. Bloomberg."),
    ("Gold dips as Fed rate-hike bets rise, still heads for best month", "rates_up", ""),
    ("Gold tests $4,311 support as Fed-hike odds hold near 66%", "rates_up",
     "'Fed-hike' - neither the hyphen nor the 'fed hike' phrasing was covered"),
    ("Gold collapses as US-Iran strikes send Oil, US yields higher", "geo_escalation",
     "'US-Iran strikes' matched no escalation keyword"),

    # --- geopolitics ------------------------------------------------------------------
    ("Two More Oil Tankers Are Attacked in the Strait of Hormuz", "oil_supply_tight", ""),
    ("Trump confirms US striking Iran.", "geo_escalation", ""),
    ("Crude oil moves higher as reports of an American attack on Iran", "geo_escalation",
     "an RSS blurb mentioning 'ceasefire' used to make this a de-escalation alert"),
    ("Iran and US agree ceasefire, Strait of Hormuz to reopen", "geo_deescalation", ""),
]


def test_classify():
    bad = []
    for headline, expected, why in CASES:
        got = cw.classify(headline)
        got = got[0] if got else "NO MATCH"
        if got != expected:
            bad.append((headline, expected, got, why))
    return bad


YIELDS_REGIME = {"Gold": (4300, 4500, 4200, 4600, 0),      # gold below its baseline
                 "US10Y": (4.80, 4.60, 4.5, 4.9, 2),       # yields above
                 "WTI": (90.0, 84.0, 80, 92, 1)}           # oil above


def test_regime_symmetry():
    """In a yields-driven market the fear-driven gold lean inverts - BOTH ways.

    Only geo_escalation still carries a directional metals lean; geo_deescalation and
    oil_supply_tight were measured flat (see backtest_geo.py) and so have nothing to
    invert. The both-ways property is checked directly on apply_regime instead, because
    it was broken for months while the one-way half kept passing.
    """
    bad = []
    dec, _ = cw.decoded("geo_escalation", YIELDS_REGIME)
    if dec["gold"][0] != "down":
        bad.append(("geo_escalation", "gold", "down", dec["gold"][0]))

    synthetic_down = {"gold": ("down", 2, ""), "silver": ("down", 2, ""),
                      "oil": ("down", 3, ""), "regime_sensitive": True}
    flipped = cw.apply_regime(synthetic_down, "yields")
    if flipped["gold"][0] != "up":
        bad.append(("synthetic down->up", "gold", "up", flipped["gold"][0]))

    synthetic_up = {"gold": ("up", 2, ""), "silver": ("up", 2, ""),
                    "oil": ("up", 3, ""), "regime_sensitive": True}
    flipped = cw.apply_regime(synthetic_up, "yields")
    if flipped["gold"][0] != "down":
        bad.append(("synthetic up->down", "gold", "down", flipped["gold"][0]))
    return bad


def test_title_is_ascii():
    """ntfy sends the title as an HTTP header; non-ASCII arrives as '?'."""
    bad = []
    for cat in cw.DECODE:
        dec, _ = cw.decoded(cat, {})
        t = cw.headline_title(cat, dec)
        if not t.isascii():
            bad.append((cat, t))
    return bad


def test_market_hours():
    """CME metals/crude: Sunday 22:00 UTC to Friday 21:00 UTC."""
    import datetime as dt
    U = dt.timezone.utc
    cases = [(dt.datetime(2026, 9, 2, 12, tzinfo=U), False),   # Wed midday
             (dt.datetime(2026, 9, 4, 20, tzinfo=U), False),   # Fri before the close
             (dt.datetime(2026, 9, 4, 22, tzinfo=U), True),    # Fri after it
             (dt.datetime(2026, 9, 5, 12, tzinfo=U), True),    # Saturday
             (dt.datetime(2026, 9, 6, 20, tzinfo=U), True),    # Sun before the reopen
             (dt.datetime(2026, 9, 6, 23, tzinfo=U), False),   # Sun after it
             (dt.datetime(2026, 9, 7, 2, tzinfo=U), False)]    # Monday
    return [(t, want, cw.market_closed(t)) for t, want in cases
            if cw.market_closed(t) != want]


def test_cross_watcher_claim():
    """One event must not buzz the phone twice, once per watcher."""
    import news_watch as nw
    bad = []
    if nw.THEME_FILE.exists():
        nw.THEME_FILE.unlink()
    if not nw.theme_claim("geo_escalation", "commodity"):
        bad.append("commodity could not claim a free theme")
    if nw.theme_claim("geo_escalation", "fx"):
        bad.append("FX alerted on a theme the commodity watcher just claimed")
    if not nw.theme_claim("tariff", "fx"):
        bad.append("FX blocked on an unrelated theme")
    if not nw.theme_claim("geo_deescalation", "commodity"):
        bad.append("a watcher blocked itself on its own claim")
    if nw.THEME_FILE.exists():
        nw.THEME_FILE.unlink()
    return bad


if __name__ == "__main__":
    fails = 0

    bad = test_classify()
    print(f"classify: {len(CASES) - len(bad)}/{len(CASES)} passed")
    for h, exp, got, why in bad:
        fails += 1
        print(f"  FAIL  want {exp:<16} got {got:<16} {h[:60]}")
        if why:
            print(f"        ({why})")

    bad = test_regime_symmetry()
    print(f"regime flip: {3 - len(bad)}/3 passed")
    for cat, key, want, got in bad:
        fails += 1
        print(f"  FAIL  {cat}.{key}: want {want}, got {got}")

    bad = test_title_is_ascii()
    print(f"ascii titles: {len(cw.DECODE) - len(bad)}/{len(cw.DECODE)} passed")
    for cat, t in bad:
        fails += 1
        print(f"  FAIL  {cat}: {t!r}")

    bad = test_market_hours()
    print(f"market hours: {7 - len(bad)}/7 passed")
    for t, want, got in bad:
        fails += 1
        print(f"  FAIL  {t:%a %H:%M}Z: want closed={want}, got {got}")

    bad = test_cross_watcher_claim()
    print(f"cross-watcher claim: {4 - len(bad)}/4 passed")
    for b in bad:
        fails += 1
        print(f"  FAIL  {b}")

    print("\nALL PASS" if not fails else f"\n{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
