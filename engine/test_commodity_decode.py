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


def test_regime_symmetry():
    """A ceasefire in a yields-driven market must read gold UP, not down."""
    bad = []
    yields_regime = {"Gold": (4300, 4500, 4200, 4600, 0),      # gold below baseline
                     "US10Y": (4.80, 4.60, 4.5, 4.9, 2),       # yields above
                     "WTI": (90.0, 84.0, 80, 92, 1)}           # oil above
    for cat, key, want in (("geo_deescalation", "gold", "up"),
                           ("geo_escalation", "gold", "down"),
                           ("oil_supply_tight", "gold", "down")):
        dec, _ = cw.decoded(cat, yields_regime)
        if dec[key][0] != want:
            bad.append((cat, key, want, dec[key][0]))
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

    print("\nALL PASS" if not fails else f"\n{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
