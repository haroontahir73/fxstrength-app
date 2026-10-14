"""COT x Myfxbook retail positioning divergence (Step 2) - display only.

Compares where the large speculators (CFTC Legacy Non-Commercial net, 50-week percentile)
sit with how Myfxbook's retail crowd is positioned in the same instrument, after putting
both in the terms of the instrument the page shows (divergence_config.ORIENTATION - written
out by hand, never inferred from symbol names).

    normalized net  = (NC long - NC short of the future) x (+1 direct / -1 inverted)
    COT percentile  = mid-rank of the latest normalized net among the last 50 weekly
                      reports (latest included)
    Strong Bullish  COT pct >= 85 and retail SHORT >= 70%
    Strong Bearish  COT pct <= 15 and retail LONG  >= 70%
    Bullish         COT pct >= 70 and retail SHORT >= 60%
    Bearish         COT pct <= 30 and retail LONG  >= 60%
    otherwise       No significant divergence

No classification at all ("Divergence unavailable", with the reasons) when: Myfxbook is
switched off or has no snapshot; the market has no Myfxbook data; the Step 1 symbol is not
one reviewed for this market; fewer than 100 retail positions; the retail snapshot is more
than 3 hours old (checked again in the browser, since the page is static); the retail
figures are malformed; or the COT data needed (latest report for this contract, 50 weekly
reports, a non-flat range, a report no older than 21 days) is missing.

Weekly momentum (latest vs previous report's normalized net) is shown as supporting
evidence only - it never changes the classification.

It feeds NOTHING else: not the strength score, alerts, trading signals or the Pi. A
positioning signal, not an execution signal.
"""
import datetime as dt
import math
import os
from pathlib import Path

import cot_table as ct
import divergence_config as cfg
import myfxbook as mf
from config import DATA

CLASS_KEYS = [r[0] for r in cfg.RULES] + ["none"]
ACTIONABLE = ("strong_bull", "strong_bear", "bull", "bear")
_ROW = {r[3]: r for r in ct.ALL_ROWS}            # key -> cot_table row


def sign(key):
    return -1 if cfg.ORIENTATION[key]["cot_orientation"] == "inverted" else 1


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- COT side
def cot_side(key, store, now):
    """Normalized Non-Commercial net, its 50-week percentile and weekly change, or
    {'error': reason}."""
    row = _ROW.get(key)
    if row is None or not store:
        return {"error": "COT data missing"}
    latest, _ = ct.pick_weeks(store)
    if latest is None:
        return {"error": "COT data missing"}
    weeks = (store.get("contracts", {}).get(row[1]) or {}).get("weeks", {})
    if latest not in weeks:
        return {"error": f"COT data missing - no {_long(latest)} report for this contract"}
    age = (now.date() - dt.date.fromisoformat(latest)).days
    if age > cfg.COT_MAX_AGE_DAYS:
        return {"error": f"COT data out of date - latest report {_long(latest)} is {age} days old "
                         f"(limit {cfg.COT_MAX_AGE_DAYS})"}
    dates = sorted(d for d in weeks if d <= latest)[-cfg.COT_WEEKS:]
    nets = []
    for d in dates:
        v = weeks[d]
        try:
            ncl, ncs = v[ct.NCL], v[ct.NCS]
            if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in (ncl, ncs)):
                raise ValueError
        except (TypeError, IndexError, ValueError):
            return {"error": f"COT data malformed ({_long(d)} report)"}
        nets.append(sign(key) * (ncl - ncs))
    if len(nets) < cfg.COT_WEEKS:
        return {"error": f"COT data missing - only {len(nets)} of {cfg.COT_WEEKS} weekly reports"}
    lo, hi = min(nets), max(nets)
    if hi == lo:
        return {"error": "COT data flat - no 50-week range to rank against"}
    net = nets[-1]
    pct = ct.percentile_rank(nets, net)
    out = {"report_date": latest, "net": net, "pct": pct, "weeks": len(nets),
           "range": (lo, hi), "prev_date": None, "prev_net": None, "change": None,
           "change_pct": None, "momentum": "unavailable"}
    if len(dates) >= 2:
        out.update(prev_date=dates[-2], prev_net=nets[-2], change=net - nets[-2])
        out["change_pct"] = 100.0 * out["change"] / (hi - lo)
        out["momentum"] = momentum(pct, out["change_pct"])
    out["direction"] = direction(pct)
    return out


def direction(pct):
    return ("Bullish" if pct >= cfg.DIRECTION_BULL else
            "Bearish" if pct <= cfg.DIRECTION_BEAR else "Neutral")


def momentum(pct, change_pct):
    """Strengthening = the weekly change pushes the net further toward the side its
    percentile leans to (>= 50 bullish, < 50 bearish); Weakening = back toward the middle;
    Flat/neutral = less than MOMENTUM_FLAT_PCT of the 50-week range."""
    if change_pct is None:
        return "unavailable"
    if abs(change_pct) < cfg.MOMENTUM_FLAT_PCT:
        return "Flat/neutral"
    lean = 1 if pct >= 50 else -1
    return "Strengthening" if change_pct * lean > 0 else "Weakening"


# ---------------------------------------------------------------- retail side
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def retail_side(key, snap, now, on):
    """The Myfxbook reading if it may be used, else {'error': reason, ...}."""
    o = cfg.ORIENTATION[key]
    if not on:
        return {"error": "Myfxbook retail data is switched off"}
    if not o["retail_symbols"]:
        return {"error": "Myfxbook has no data for this market"}
    ts = (snap or {}).get("fetched_at")
    if not ts:
        return {"error": "Myfxbook retail data unavailable - nothing fetched yet"}
    m = (snap.get("markets") or {}).get(key)
    if not m:
        return {"error": "Myfxbook retail data unavailable for this market", "ts": ts}
    base = {"ts": ts, "symbol": m.get("symbol")}
    if m.get("symbol") not in o["retail_symbols"]:
        return {**base, "error": f"symbol mapping uncertain - Myfxbook symbol {m.get('symbol')!s} is not "
                                 f"the reviewed {' / '.join(o['retail_symbols'])}"}
    need = ("longPercentage", "shortPercentage", "longPositions", "shortPositions")
    lp, sp = m.get("longPercentage"), m.get("shortPercentage")
    if (not all(_num(m.get(k)) for k in need) or not (0 <= lp <= 100 and 0 <= sp <= 100)
            or abs(lp + sp - 100) > 1 or m["longPositions"] < 0 or m["shortPositions"] < 0):
        return {**base, "error": "Myfxbook retail data malformed"}
    n = int(m["longPositions"] + m["shortPositions"])
    base.update(long=lp, short=sp, positions=n, imbalance=mf.imbalance(lp, sp), crowd=mf.crowd(lp, sp))
    try:
        age = mf._age_min(ts, now)
    except (ValueError, AttributeError):
        return {"ts": None, "symbol": m.get("symbol"), "error": "Myfxbook retail data malformed (timestamp)"}
    base["age_min"] = age
    if n < mf.MIN_POSITIONS:
        return {**base, "error": f"Myfxbook sample too small - {n:,} positions, minimum {mf.MIN_POSITIONS}"}
    if age > cfg.RETAIL_MAX_AGE_MIN:
        return {**base, "error": f"Myfxbook retail data stale - {mf._age_txt(age)} old "
                                 f"(limit {cfg.RETAIL_MAX_AGE_MIN // 60} hours)"}
    return base


# ---------------------------------------------------------------- classification
def classify(pct, long_, short):
    """V1 rule (divergence_config.RULES). Returns (class key, label)."""
    for key, label, op, val, side, need in cfg.RULES:
        ok_cot = pct >= val if op == ">=" else pct <= val
        if ok_cot and (short if side == "short" else long_) >= need:
            return key, label
    return "none", cfg.NONE_LABEL


def evaluate(key, store, snap, now, on=True):
    """Everything the card shows for one market."""
    cot = cot_side(key, store, now)
    ret = retail_side(key, snap, now, on)
    reasons = [x["error"] for x in (ret, cot) if x.get("error")]
    r = {"key": key, "shown": cfg.ORIENTATION[key]["shown"], "cot": cot, "retail": ret,
         "reasons": reasons}
    if reasons:
        r.update(cls="unavailable", label="Divergence unavailable")
        return r
    r["cls"], r["label"] = classify(cot["pct"], ret["long"], ret["short"])
    r["why"] = explain(r)
    return r


def _ord(p):
    n = int(math.floor(p + 0.5))
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def explain(r):
    """[(passed?, sentence)] - why this result and not another."""
    cot, ret = r["cot"], r["retail"]
    p = cot["pct"]
    out = []
    rule = next((x for x in cfg.RULES if x[0] == r["cls"]), None)
    where = f"the {_ord(p)} percentile of its {cot['weeks']}-week range"
    if rule:
        _, _, op, val, side, need = rule
        out.append((True, f"Normalized COT positioning is at {where} ({'&ge;' if op == '>=' else '&le;'} {val})"))
        out.append((True, f"Retail traders are {mf._pc(ret[side])}% {side} (&ge; {need}%)"))
    else:
        out.append((None, f"Normalized COT positioning is at {where}"))
        out.append((None, f"Retail traders are {mf._pc(ret['long'])}% long / {mf._pc(ret['short'])}% short"))
        miss = []
        for _, label, op, val, side, need in cfg.RULES:
            c_ok = p >= val if op == ">=" else p <= val
            r_ok = (ret[side]) >= need
            if c_ok and not r_ok:
                miss.append(f"{label} needs retail {side} &ge; {need}%")
            elif r_ok and not c_ok:
                miss.append(f"{label} needs COT {'&ge;' if op == '>=' else '&le;'} {val}th percentile")
        out.append((False, "No rule is met" + (": " + "; ".join(dict.fromkeys(miss)) if miss else
                                                " - COT is not at an extreme and retail is not crowded against it")))
    out.append((True, f"Retail sample is above the minimum position count ({ret['positions']:,} &ge; {mf.MIN_POSITIONS})"))
    out.append((True, f"Retail data is fresh ({mf._age_txt(ret['age_min'])} old, limit {cfg.RETAIL_MAX_AGE_MIN // 60} h)"))
    return out


# ---------------------------------------------------------------- one evaluation per build
_CACHE = {}


def evaluate_all(now=None, data=None, env=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    data = Path(data or DATA)
    env = os.environ if env is None else env
    on = mf.enabled(env)
    ck = (_iso(now), str(data), on)
    if ck in _CACHE:
        return _CACHE[ck]
    store = ct.load(data / ct.OUT_NAME)
    snap = mf._load(data) if on else {}
    res = {k: evaluate(k, store, snap, now, on) for k in cfg.ORIENTATION}
    _CACHE.clear()
    _CACHE[ck] = res
    return res


# ---------------------------------------------------------------- rendering
def _esc(s):
    return mf._esc(s)


def _long(iso):
    try:
        return dt.date.fromisoformat(iso).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return str(iso)


def _signed(v):
    return "&mdash;" if v is None else f"{v:+,}".replace("-", "&minus;")


EXP_NOTE = ("Divergence unavailable &mdash; Myfxbook retail data is more than "
            f"{cfg.RETAIL_MAX_AGE_MIN // 60} hours old.")
DISCLAIMER = ("A positioning divergence, not a trade signal: it compares where large speculators "
              "and Myfxbook&rsquo;s retail crowd are positioned. It is not used by the strength "
              "score or alerts.")


def _exp_attrs(r):
    """Browser-side expiry: the static page may outlive the 3-hour retail limit."""
    ts = (r.get("retail") or {}).get("ts")
    return f' data-div-ts="{_esc(ts)}" data-div-max="{cfg.RETAIL_MAX_AGE_MIN}"' if ts else ""


def _cot_dl(cot):
    if cot.get("error"):
        return ""
    ch = cot.get("change")
    return (f'<div class="dvcol"><h5>COT &middot; Non-Commercial</h5><dl>'
            f'<dt>Direction</dt><dd>{cot["direction"]}</dd>'
            f'<dt>50-week percentile</dt><dd>{_ord(cot["pct"])}</dd>'
            f'<dt>Net position</dt><dd>{_signed(cot["net"])}</dd>'
            f'<dt>Weekly change</dt><dd>{_signed(ch)}'
            + (f' <span class="mut">({cot["change_pct"]:+.0f}% of range)</span>' if ch is not None else "")
            + f'</dd><dt>Momentum</dt><dd>{cot["momentum"]}</dd>'
            f'<dt>COT report</dt><dd>{_long(cot["report_date"])} <span class="mut">(weekly)</span></dd></dl></div>')


def _ret_dl(ret):
    if "long" not in ret:
        return ""
    if ret.get("age_min", 0) > cfg.RETAIL_MAX_AGE_MIN:
        # stale: the Retail tab still shows these (marked Stale, hidden after 24 h) - the
        # divergence card does not repeat old figures
        return (f'<div class="dvcol"><h5>Myfxbook retail &middot; {_esc(ret["symbol"])}</h5><dl>'
                f'<dt>Myfxbook update</dt><dd>{mf._fmt_ts(ret["ts"])}</dd></dl></div>')
    if ret["positions"] < mf.MIN_POSITIONS:
        # Step 1's rule: below the minimum only the count is shown - no %, imbalance or crowd
        return (f'<div class="dvcol"><h5>Myfxbook retail &middot; {_esc(ret["symbol"])}</h5><dl>'
                f'<dt>Positions</dt><dd>{ret["positions"]:,} <span class="mut">(below {mf.MIN_POSITIONS} '
                f'&mdash; no reading)</span></dd>'
                f'<dt>Myfxbook update</dt><dd>{mf._fmt_ts(ret["ts"])}</dd></dl></div>')
    return (f'<div class="dvcol"><h5>Myfxbook retail &middot; {_esc(ret["symbol"])}</h5><dl>'
            f'<dt>Long</dt><dd>{mf._pc(ret["long"])}%</dd>'
            f'<dt>Short</dt><dd>{mf._pc(ret["short"])}%</dd>'
            f'<dt>Positions</dt><dd>{ret["positions"]:,}</dd>'
            f'<dt>Imbalance</dt><dd>{ret["imbalance"]}</dd>'
            f'<dt>Crowd</dt><dd>{ret["crowd"]}</dd>'
            f'<dt>Myfxbook update</dt><dd>{mf._fmt_ts(ret["ts"])}</dd></dl></div>')


def card(r):
    """The 'COT x Retail divergence' card inside one market's 50-week report."""
    cls, label = r["cls"], r["label"]
    head = f'<div class="dvhd">COT &times; Retail divergence <span class="mut">&middot; {_esc(r["shown"])}</span></div>'
    cols = f'<div class="dvcols">{_cot_dl(r["cot"])}{_ret_dl(r["retail"])}</div>'
    if cls == "unavailable":
        why = "".join(f'<li class="x">{_esc_reason(x)}</li>' for x in r["reasons"])
        return (f'<div class="dvcard dv-unavailable" data-div-key="{_esc(r["key"])}" data-div-class="unavailable">{head}'
                f'<p class="dvres">Divergence unavailable</p><p class="dvwhyh">Why unavailable?</p>'
                f'<ul class="dvwhy">{why}</ul>{cols}<p class="dvnote">{DISCLAIMER}</p></div>')
    result = label + (" Divergence" if cls != "none" else "")
    marks = {True: ("ok", "&#10003;"), False: ("x", "&#10007;"), None: ("i", "&middot;")}
    why = "".join(f'<li class="{marks[ok][0]}"><span aria-hidden="true">{marks[ok][1]}</span> {t}</li>'
                  for ok, t in r["why"])
    whyh = f"Why {label}?" if cls != "none" else "Why no divergence?"
    return (f'<div class="dvcard dv-{cls}" data-div-key="{_esc(r["key"])}" data-div-class="{cls}"{_exp_attrs(r)}>{head}'
            f'<p class="dvexp">{EXP_NOTE} Last update: {mf._fmt_ts(r["retail"]["ts"])}.</p>'
            f'<div class="dvlive"><p class="dvres">{result}</p>{cols}'
            f'<p class="dvwhyh">{whyh}</p><ul class="dvwhy">{why}</ul></div>'
            f'<p class="dvnote">{DISCLAIMER}</p></div>')


def _esc_reason(s):
    return _esc(s).replace(" - ", " &mdash; ")


def report_card(key, now=None, data=None, env=None):
    """Card for one market, or '' while Myfxbook is switched off (Step 1's rule: nothing at
    all is shown then)."""
    if not mf.enabled(os.environ if env is None else env):
        return ""
    if key not in cfg.ORIENTATION:
        return ""
    return card(evaluate_all(now, data, env)[key])


_ORDER = {"strong_bull": 0, "strong_bear": 0, "bull": 1, "bear": 1}


def scanner(now=None, data=None, env=None):
    """POSITIONING DIVERGENCES: actionable classifications only, strong first."""
    if not mf.enabled(os.environ if env is None else env):
        return ""
    res = evaluate_all(now, data, env)
    labels = {k: lab for k, lab in mf._labels()[0][1] + mf._labels()[1][1]}
    hits = sorted((r for r in res.values() if r["cls"] in ACTIONABLE),
                  key=lambda r: (_ORDER[r["cls"]], -abs(r["cot"]["pct"] - 50), r["key"]))
    n_none = sum(r["cls"] == "none" for r in res.values())
    n_na = sum(r["cls"] == "unavailable" for r in res.values())
    ts = next((r["retail"].get("ts") for r in res.values() if r["retail"].get("ts")), None)
    items = "".join(
        f'<li class="dv-{r["cls"]}"><a href="#cot50-{_esc(r["key"])}" class="dvsc">'
        f'<span class="dvsn">{_esc(labels.get(r["key"], r["key"]))}</span>'
        f'<span class="dvsl">{r["label"]} Divergence</span>'
        f'<span class="dvsd">COT {_ord(r["cot"]["pct"])} pct &middot; retail '
        + (f'{mf._pc(r["retail"]["short"])}% short' if r["cls"] in ("strong_bull", "bull")
           else f'{mf._pc(r["retail"]["long"])}% long')
        + '</span></a></li>' for r in hits)
    body = (f'<ul class="dvlist">{items}</ul>' if hits else
            '<p class="dvempty">No positioning divergence right now.</p>')
    tail = (f'<p class="dvfoot mut">{len(res)} markets checked: {len(hits)} divergence'
            f'{"" if len(hits) == 1 else "s"}, {n_none} none, {n_na} unavailable '
            f'(open a market&rsquo;s report for the reason). {DISCLAIMER}</p>')
    exp = (f' data-div-ts="{_esc(ts)}" data-div-max="{cfg.RETAIL_MAX_AGE_MIN}"' if ts and hits else "")
    expnote = f'<p class="dvexp">{EXP_NOTE} Open a market&rsquo;s report for details.</p>' if exp else ""
    return (f'<section class="dvscan"{exp}><h2>Positioning divergences '
            f'<span class="mut" style="font-weight:400;font-size:14px">&mdash; COT &times; Myfxbook retail</span></h2>'
            f'{expnote}<div class="dvlive">{body}</div>{tail}{SCRIPT}</section>')


# Static page: hide every classification once the retail snapshot is older than the limit.
SCRIPT = ("<script>(function(){function u(){var n=Date.now();document.querySelectorAll('[data-div-ts]')"
          ".forEach(function(e){var t=Date.parse(e.getAttribute('data-div-ts'));if(!isNaN(t))"
          "e.classList.toggle('dvexpired',(n-t)/60000>+e.getAttribute('data-div-max'))})}"
          "u();document.addEventListener('DOMContentLoaded',u);setInterval(u,60000);})();</script>")
