"""Education glossary: one definition per term at four levels (spec STEP 14 education modes).

Teaching text only; it never carries market values. Terms for engines that do not exist yet say so.
"""

from __future__ import annotations

import re
from typing import cast

from app.domain.enums import EducationLevel

BEG, INT, ADV, PRO = (
    EducationLevel.BEGINNER,
    EducationLevel.INTERMEDIATE,
    EducationLevel.ADVANCED,
    EducationLevel.PROFESSIONAL,
)

GLOSSARY: dict[str, dict[str, object]] = {
    "DOL": {
        "aliases": ["dol", "draw on liquidity"],
        BEG: "The draw on liquidity is the price area the market is most likely heading toward: a place where many stop orders rest.",
        INT: "DOL is the liquidity pool (e.g. a previous day high, equal highs) price is expected to reach next; this system ranks untaken pools by a magnet score.",
        ADV: "Primary/secondary DOL are selected from untaken pools by magnet score (type, distance in ATR, confluence); a narrow margin between them makes the DOL UNCLEAR, which blocks the decision.",
        PRO: "DOL selection is deterministic: untaken pools scored 0-100, margin between primary and secondary drives DolConfidence; DOL_UNCLEAR is a WAIT-class blocker, never overridden by score.",
    },
    "BSL/SSL": {
        "aliases": ["bsl", "ssl", "buy side liquidity", "sell side liquidity", "buy-side", "sell-side"],
        BEG: "Buy-side liquidity sits above highs (stops of sellers); sell-side liquidity sits below lows (stops of buyers).",
        INT: "BSL/SSL are pools above swing highs / below swing lows; price often sweeps them before reversing or runs them to continue.",
        ADV: "A sweep trades through a pool and closes back inside; a run closes and accepts beyond it. This system tracks FRESH, APPROACHING, TOUCHED, SWEPT, RUN, BROKEN and RECLAIMED.",
        PRO: "Pools are known only after their pivots confirm (no lookahead); key levels and session pools are windowed, so historical replay can see a different pool universe.",
    },
    "BOS": {
        "aliases": ["bos", "break of structure"],
        BEG: "A break of structure is when price closes beyond a previous swing in the direction of the trend.",
        INT: "BOS is a continuation break confirmed by a candle close; wick-only breaks are only POTENTIAL.",
        ADV: "BOS extends the current trend's protected level; it is tracked separately for internal and external structure.",
        PRO: "Confirmation is candle-close by default; a BOS never counts as an LTF entry refinement (reversal breaks only).",
    },
    "CHOCH": {
        "aliases": ["choch", "change of character"],
        BEG: "A change of character is the first sign the trend may be turning: price breaks the last swing against the trend.",
        INT: "CHoCH is an early reversal break; it is weaker than an MSS.",
        ADV: "CHoCH scores partial STRUCTURE points in the evaluation and can confirm an LTF refinement entry on M5.",
        PRO: "CHoCH vs MSS is decided by the break's qualifiers (liquidity, displacement) under the structure spec.",
    },
    "MSS": {
        "aliases": ["mss", "market structure shift"],
        BEG: "A market structure shift is a strong break against the trend, usually right after liquidity is taken.",
        INT: "MSS is a reversal break qualified by a prior liquidity event and displacement.",
        ADV: "The core setup requires an MSS (with displacement) within 12 bars of the liquidity event before it arms.",
        PRO: "MSS carries liquidity and displacement qualifiers; displacement on the break is credited once (correlated-evidence adjustment).",
    },
    "FVG": {
        "aliases": ["fvg", "fair value gap", "imbalance"],
        BEG: "A fair value gap is a gap left by a fast three-candle move where price did not trade both ways.",
        INT: "An FVG is the space between candle 1 and candle 3 of a displacement leg; price often returns to it (mitigation).",
        ADV: "FVGs track fill % (partial, half, full) and are invalidated only by a close through the far edge, not by wicks.",
        PRO: "Quality scores size in ATR, displacement grade and age; weak FVGs (< 0.3 ATR) trigger an early-entry warning.",
    },
    "IFVG": {
        "aliases": ["ifvg", "inverse fvg", "inversion fair value gap"],
        BEG: "An inverse FVG is an old gap that price broke through strongly and now acts in the opposite direction.",
        INT: "An IFVG is a failed FVG that price accepted beyond with displacement; it is not created from every failed FVG.",
        ADV: "States POTENTIAL_IFVG, CONFIRMED_IFVG, FAILED_IFVG; a confirmed IFVG leg zone labels an M15 close entry as IFVG.",
        PRO: "Inversion requires acceptance or displacement beyond the zone; confirmation is sequential and never retroactive.",
    },
    "NO_WICK": {
        "aliases": ["no wick", "no-wick", "nowick", "marubozu"],
        BEG: "A no-wick candle opens or closes at its extreme: buyers or sellers were in control for the whole candle.",
        INT: "No Wick events classify true/near marubozu and one-sided no-wick candles; the body must be meaningful versus ATR.",
        ADV: "Candle quality, context and relevance scores stay separate; rebalance zones track 25/50/75% and full rebalance.",
        PRO: "No Wick is context only: it never authorizes a direction, and no later candle or FVG can improve a past event.",
    },
    "DISPLACEMENT": {
        "aliases": ["displacement"],
        BEG: "Displacement is a strong, fast move that shows real commitment from buyers or sellers.",
        INT: "Displacement legs are graded WEAK, MODERATE, STRONG or EXCEPTIONAL by size in ATR and body quality.",
        ADV: "Grades use the ATR before the leg; a grade can upgrade as the leg extends, and the break's displacement qualifier depends on it.",
        PRO: "Displacement is scored once per setup (correlated-evidence penalty when it only qualifies the break).",
    },
    "PDH/PDL": {
        "aliases": ["pdh", "pdl", "previous day high", "previous day low", "pwh", "pwl"],
        BEG: "The previous day's high and low are levels many traders watch; stops often rest just beyond them.",
        INT: "PDH/PDL (and weekly PWH/PWL) come from New York 17:00 trading days and are treated as external liquidity.",
        ADV: "Key levels are known only after their period closes and are used as DOL candidates and TP targets.",
        PRO: "Trading-day buckets follow the New York close with DST; the H4/D1 series derive from H1 in those buckets.",
    },
    "KILL_ZONE": {
        "aliases": ["kill zone", "killzone", "session", "london session", "new york session"],
        BEG: "Kill zones are the times of day when big moves usually happen, like the London and New York opens.",
        INT: "London 02:00-05:00, NY AM 07:00-10:00 and NY PM 13:30-16:00 New York time; time quality rates IDEAL to AVOID.",
        ADV: "Time never creates a trade by itself: session quality feeds the SESSION score and conflict, and session highs/lows become liquidity pools.",
        PRO: "Windows are New York wall-clock times (DST-aware); London DST gaps shift London wall times intentionally.",
    },
    "PO3": {
        "aliases": ["po3", "power of three", "judas", "judas swing"],
        BEG: "Power of three describes a day that ranges, fakes one way, then moves the other way.",
        INT: "PO3 phases: accumulation, manipulation (often a Judas swing against the day's bias) and distribution.",
        ADV: "The PO3 state uses the daily open, ADR and the setup bias; Judas swings are candidates until confirmed or failed.",
        PRO: "PO3 and Judas are descriptive context in this system and never change the verdict.",
    },
    "RR": {
        "aliases": ["r:r", "risk reward", "risk-reward", "rr"],
        BEG: "Risk-to-reward compares how much you could lose at the stop with how much you could gain at the target.",
        INT: "R:R = (target - entry) / (entry - stop); the STANDARD mode needs at least 2.0 to TP1.",
        ADV: "If price moves away before confirmation and R:R from the current price falls below the minimum, the setup is ENTRY_MISSED (do not chase).",
        PRO: "R:R is price-only; sizing, spread and locks are the risk engine's job and risk has final veto.",
    },
    "NEWS_GATE": {
        "aliases": ["news blackout", "news gate", "post news wait", "post-news wait"],
        BEG: "Around big economic releases prices can jump unpredictably, so the system waits before and after them.",
        INT: "High-impact events for the market's currencies create a BLACKOUT before and after the release, then a post-news wait.",
        ADV: "EXTREME events black out 30 min before to 30 min after, HIGH events 15/15, MEDIUM events only raise CAUTION; if the calendar cannot prove the next 24 h are clear the state is UNAVAILABLE.",
        PRO: "BLACKOUT is a hard blocker (a confirmed plan becomes NO_TRADE); a stale, synthetic or incomplete calendar can never clear the gate.",
    },
    "MACRO_CONTEXT": {
        "aliases": ["macro context", "macro bias", "macro conflict", "correlation regime"],
        BEG: "Macro looks at the dollar, interest rates and market fear to see whether the bigger picture helps or hurts a trade idea.",
        INT: "Each market has drivers (for gold: DXY and real yields down are supportive); their recent direction adds up to a macro bias.",
        ADV: "Each series' 5-day change is compared with its own volatility; weighted drivers give a score from -1 to +1, and the state versus the setup is SUPPORTIVE, NEUTRAL or CONFLICT.",
        PRO: "Macro is context only: it adds up to 5 score points and conflict on disagreement, never blocks, and is not scored when its data is missing, stale or synthetic; an inverted DXY correlation halves DXY's weight.",
    },
    "ORDER_BLOCK": {
        "aliases": ["order block", "ob", "breaker", "mitigation block", "bpr"],
        BEG: "An order block is the last opposite candle before a strong move.",
        INT: "Order blocks, breakers, mitigation blocks and BPRs are advanced PD arrays.",
        ADV: "They are not implemented yet (Phase 20+); this system uses FVG/IFVG zones only.",
        PRO: "Not implemented (Phase 20+): no order-block values exist in the engine state.",
    },
    "PREMIUM_DISCOUNT": {
        "aliases": ["premium", "discount", "equilibrium", "ote"],
        BEG: "Premium and discount describe whether price is high or low inside its recent range.",
        INT: "Dealing ranges split into premium, equilibrium and discount; OTE is a 62-79% retracement.",
        ADV: "Premium/discount and OTE are not implemented yet (Phase 20+).",
        PRO: "Not implemented (Phase 20+): no dealing-range values exist in the engine state.",
    },
}


def find_term(question: str) -> str | None:
    q = question.lower()
    best: tuple[int, str] | None = None
    for term, entry in GLOSSARY.items():
        aliases = cast(list[str], entry["aliases"])
        for alias in aliases:
            if re.search(rf"(?<![a-z]){re.escape(str(alias))}(?![a-z])", q):
                length = len(str(alias))
                if best is None or length > best[0]:
                    best = (length, term)
    return best[1] if best else None


def define(term: str, level: EducationLevel) -> str:
    return str(GLOSSARY[term][level])
