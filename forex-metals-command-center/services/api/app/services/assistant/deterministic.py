"""Deterministic explainer: classifies the question and answers only from tool payloads.

It is the default provider and the fallback whenever an external model's answer fails the guard. Every value in the
answer is copied from a tool payload; anything the engines do not know is listed under `unknowns`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import AssistantIntent, EducationLevel, Verdict
from app.services.assistant.glossary import define, find_term
from app.services.assistant.models import AlertProposal, Fact
from app.services.assistant.tools import AssistantContext

Intent = AssistantIntent
COMMANDS = [
    "Analyze Gold",
    "Why are we waiting?",
    "Why isn't this a buy?",
    "Where is liquidity?",
    "What is DOL?",
    "Why did score drop?",
    "Is this No Wick important?",
    "What invalidates this?",
    "Give trade plan",
    "What is the risk?",
    "Which session is it?",
    "Show A+ setups",
    "Compare assets",
    "Backtest setup",
    "Is there news soon?",
    "What does macro say?",
    "Show my journal",
    "Alert me when ready",
    "What is an FVG?",
]
# Order matters: the first matching pattern wins.
PATTERNS: list[tuple[AssistantIntent, str]] = [
    (Intent.BACKTEST, r"back ?test"),
    (Intent.JOURNAL, r"\b(journal|my trades?|last trades?|past trades?|trade history|logged)\b"),
    (
        Intent.NEWS,
        r"\b(news|calendar|cpi|nfp|fomc|payrolls?|economic (events?|data|releases?)|high[- ]impact|blackout)\b",
    ),
    (Intent.MACRO, r"\b(macro|dxy|yields?|fed|rates?|intermarket)\b"),
    (Intent.CREATE_ALERT, r"\b(alert me|notify me|watch (it|this|for)|create (an )?alert)\b"),
    (Intent.SCORE_CHANGE, r"score.*\b(drop|fall|fell|chang|lower|decreas)"),
    (Intent.A_PLUS_SETUPS, r"\ba\+|best setups?|top setups?"),
    (Intent.COMPARE, r"\b(compare|versus|vs\.?)\b"),
    (Intent.WHY_NOT_DIRECTION, r"why\b.*\b(not|isn'?t|no)\b.*\b(buy|long|sell|short|bullish|bearish)"),
    (Intent.WHY_WAITING, r"why\b.*\b(wait|waiting|not yet|blocked|no trade)"),
    (Intent.INVALIDATION, r"invalidat"),
    (Intent.TRADE_PLAN, r"\b(trade plan|plan|entry|stop loss|stop|targets?|tp1?)\b"),
    (Intent.NO_WICK, r"no[ -]?wick|marubozu"),
    (Intent.DOL, r"\b(dol|draw on liquidity)\b"),
    (Intent.LIQUIDITY, r"liquidity|\b(bsl|ssl|eqh|eql|pdh|pdl|sweep)\b"),
    (Intent.RISK, r"\b(risk|size|lot|position|lock)"),
    (Intent.SESSION, r"\b(session|kill ?zone|london|new york|asia|adr|time)\b"),
    (Intent.ANALYZE, r"\b(analy[sz]e|overview|summary|state|status|gold|xauusd|market)\b"),
    (Intent.EXPLAIN_TERM, r"\b(what is|what's|what are|explain|define|meaning)\b"),
]
DIRECTION_WORDS = {
    "buy": "BULLISH",
    "long": "BULLISH",
    "bullish": "BULLISH",
    "sell": "BEARISH",
    "short": "BEARISH",
    "bearish": "BEARISH",
}
BLOCKER_TEXT = {
    "DATA_SYNTHETIC": "the data is synthetic fixture data, not market facts",
    "DATA_STALE": "the latest candles are stale",
    "DATA_DISCONNECTED": "the market-data provider is disconnected",
    "DATA_INVALID": "the data failed validation",
    "PROVIDER_UNAVAILABLE": "no market-data provider is available",
    "MARKET_CLOSED": "the market is closed",
    "INSTRUMENT_SPEC_MISSING": "no verified contract spec exists, so size cannot be verified",
    "ANALYSIS_GATES_NOT_IMPLEMENTED": "verdict authority is FAIL_SAFE_ONLY: directional verdicts stay disabled until authority is explicitly switched",
    "DOL_UNCLEAR": "the draw on liquidity is unclear",
    "ENTRY_MISSED": "the last setup's entry was missed (do not chase)",
    "INSUFFICIENT_RR": "the risk-to-reward was below the minimum",
    "RISK_GATE_MISSING": "the risk assessment is unavailable",
    "NEWS_GATE_MISSING": "the news gate does not exist yet (Phase 13)",
    "RISK_PROFILE_MISSING": "no account risk profile is configured",
    "RISK_PROFILE_INVALID": "the account risk profile is invalid",
    "RISK_LOCKED": "a risk lock applies (risk has final veto)",
    "UNSAFE_SPREAD": "the spread is unsafe for the stop distance",
    "POSITION_SIZE_UNVERIFIED": "the position size cannot be verified",
    "MARKET_NOT_VALIDATED": "this market is research only (only XAUUSD is deeply validated)",
    "NEWS_BLACKOUT": "a strict news blackout is active",
    "NEWS_POST_WAIT": "the post-news wait is active",
    "NEWS_DATA_UNAVAILABLE": "the economic calendar cannot prove the next hours are clear",
    "NEWS_DATA_SYNTHETIC": "the economic calendar is synthetic",
    "SYSTEM_INTEGRITY_FAILURE": "a system integrity check failed",
}


def classify(question: str) -> AssistantIntent:
    q = question.lower()
    for intent, pattern in PATTERNS:
        if re.search(pattern, q):
            if intent is Intent.EXPLAIN_TERM and find_term(q) is None:
                continue
            return intent
    return Intent.EXPLAIN_TERM if find_term(q) else Intent.HELP


@dataclass
class Draft:
    answer: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    proposal: AlertProposal | None = None

    def fact(self, label: str, value: Any, source: str) -> None:
        if value is None or value == [] or value == "":
            return
        text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
        self.facts.append(Fact(label=label, value=text, source=source))

    def say(self, text: str) -> None:
        self.answer.append(text)


def _blocker_lines(blockers: list[str]) -> list[str]:
    return [f"{b}: {BLOCKER_TEXT.get(b, b.replace('_', ' ').lower())}" for b in blockers]


async def _decision(ctx: AssistantContext, d: Draft) -> dict[str, Any]:
    _, payload = await ctx.call("get_market_state", {"symbol": ctx.symbol})
    decision: dict[str, Any] = payload["decision"]
    d.fact("Verdict", decision["verdict"], "get_market_state")
    d.fact("Data quality", decision["dataQuality"], "get_market_state")
    d.fact("Blockers", decision["blockers"], "get_market_state")
    return decision


def _unavailable(d: Draft, payload: dict[str, Any], what: str) -> bool:
    if payload.get("status") in ("UNAVAILABLE", "REJECTED"):
        reason = payload.get("reason", "not available")
        d.unknowns.append(f"{what}: UNAVAILABLE ({reason})")
        d.say(f"{what} is UNAVAILABLE: {reason}.")
        return True
    return False


async def explain(
    ctx: AssistantContext, question: str, level: EducationLevel, compare: list[str]
) -> tuple[AssistantIntent, Draft]:
    intent = classify(question)
    d = Draft()
    decision = await _decision(ctx, d)
    verdict = decision["verdict"]
    sym = ctx.symbol
    term = find_term(question)

    if intent is Intent.HELP:
        d.say(
            f"I explain the {sym} engine state; I never create prices or verdicts. Try: "
            + "; ".join(COMMANDS)
            + "."
        )
    elif intent is Intent.EXPLAIN_TERM and term:
        d.say(define(term, level))
    elif intent in (Intent.ANALYZE, Intent.WHY_WAITING):
        d.say(f"{sym} verdict is {verdict} (authority: fail-safe, no trade instruction).")
        if decision["blockers"]:
            d.say("Why not yet: " + "; ".join(_blocker_lines(decision["blockers"])) + ".")
        if decision.get("nextRequiredEvent"):
            d.say(f"Next required event: {decision['nextRequiredEvent']}.")
        d.fact("Next required event", decision.get("nextRequiredEvent"), "get_market_state")
        if intent is Intent.ANALYZE:
            d.fact("HTF bias", decision.get("htfBias"), "get_market_state")
            d.fact("Primary DOL", decision.get("primaryDol"), "get_market_state")
            d.fact("Setup state", decision.get("setupState"), "get_market_state")
            d.fact("Setup score", decision.get("setupScore"), "get_market_state")
            d.fact("Setup grade", decision.get("setupGrade"), "get_market_state")
            d.fact("Risk status", decision.get("riskStatus"), "get_market_state")
            d.say(
                f"HTF bias {decision.get('htfBias')}; setup {decision.get('setupState')}; risk {decision.get('riskStatus')}."
            )
            if decision.get("primaryDol"):
                d.say(f"Primary DOL: {decision['primaryDol']}.")
    elif intent is Intent.WHY_NOT_DIRECTION:
        word = next((w for w in DIRECTION_WORDS if re.search(rf"\b{w}\b", question.lower())), "buy")
        wanted = DIRECTION_WORDS[word]
        d.say(
            f"It is not a {word} because the verdict is {verdict}: the system only emits a direction when every gate passes."
        )
        if decision["blockers"]:
            d.say("Blocking now: " + "; ".join(_blocker_lines(decision["blockers"])) + ".")
        _, plan = await ctx.call("get_trade_plan", {"symbol": sym})
        if not _unavailable(d, plan, "Setup evidence"):
            setup = plan.get("setup")
            if setup is None:
                d.say("There is no open setup.")
            else:
                missing = [s["step"] for s in setup["steps"] if s["status"] != "DONE"]
                d.fact("Open setup", f"{setup['direction']} {setup['state']}", "get_trade_plan")
                d.fact("Missing steps", missing, "get_trade_plan")
                if setup["direction"] != wanted:
                    d.say(f"The open setup is {setup['direction']}, not {wanted}.")
                d.say("Missing steps: " + (", ".join(missing) if missing else "none") + ".")
            for line in plan.get("devilsAdvocate", [])[:4]:
                d.say(f"Against: {line}.")
    elif intent in (Intent.LIQUIDITY, Intent.DOL):
        if intent is Intent.DOL and term:
            d.say(define("DOL", level))
        if decision.get("primaryDol"):
            d.fact("Decision primary DOL (H1)", decision["primaryDol"], "get_market_state")
            d.say(f"Master Decision primary DOL (H1): {decision['primaryDol']}.")
        else:
            d.unknowns.append("Decision primary DOL: NOT CONFIRMED")
            d.say("The Master Decision has no primary DOL.")
        _, liq = await ctx.call("get_liquidity", {"symbol": sym})
        if not _unavailable(d, liq, "Setup-timeframe liquidity"):
            dol = liq.get("dol") or {}
            primary = dol.get("primary")
            if primary:
                d.fact(
                    f"Setup DOL ({liq['timeframe']})",
                    f"{primary['label']} ({primary['side']}) @ {primary['price']}",
                    "get_liquidity",
                )
                d.say(
                    f"Setup timeframe ({liq['timeframe']}) DOL: {primary['label']} {primary['side']} at {primary['price']}, confidence {dol.get('confidence')}."
                )
            else:
                d.say(f"No {liq['timeframe']} DOL: {dol.get('reason', 'unclear')}.")
            if intent is Intent.LIQUIDITY:
                for p in liq["topPools"][:4]:
                    d.fact(
                        f"Pool {p['label']}", f"{p['side']} @ {p['price']} ({p['state']})", "get_liquidity"
                    )
                if liq["recentEvents"]:
                    e = liq["recentEvents"][-1]
                    d.say(
                        f"Latest {liq['timeframe']} liquidity event: {e['type']} of {e['pool']} at {e['price']}."
                    )
    elif intent is Intent.SCORE_CHANGE:
        d.fact("Setup score now", decision.get("setupScore"), "get_market_state")
        d.say(
            f"The current score is {decision.get('setupScore')} ({decision.get('setupGrade')}), but previous scores are not recorded, so why it changed is UNKNOWN."
        )
        d.unknowns.append(
            "Score history: UNKNOWN (scores are not recorded over time; journal snapshots keep only the score at logging time)"
        )
    elif intent is Intent.NO_WICK:
        if term:
            d.say(define("NO_WICK", level))
        _, nw = await ctx.call("get_no_wick_events", {"symbol": sym})
        if not _unavailable(d, nw, "No Wick"):
            if not nw["events"]:
                d.say("There is no recent No Wick event.")
            else:
                e = nw["events"][-1]
                d.fact(
                    "Latest No Wick",
                    f"{e['strength']} {e['classification']} {e['direction']}",
                    "get_no_wick_events",
                )
                d.fact("Relevance score", e["relevanceScore"], "get_no_wick_events")
                important = e["strength"] in ("STRONG", "EXCEPTIONAL")
                d.say(
                    f"The latest is {e['strength']} {e['classification']} (relevance {e['relevanceScore']}). It is {'notable' if important else 'minor'} context only: No Wick never authorizes a direction."
                )
    elif intent is Intent.INVALIDATION:
        _, plan = await ctx.call("get_trade_plan", {"symbol": sym})
        if not _unavailable(d, plan, "Setup"):
            setup = plan.get("setup")
            if setup is None:
                d.say("There is no open setup to invalidate.")
            else:
                level_value = setup.get("protectiveLevel")
                d.fact("Protective level", level_value, "get_trade_plan")
                beyond = (
                    f"a close beyond its protective level {level_value}"
                    if level_value is not None
                    else "its protective level once it forms (none yet: it forms with the liquidity event)"
                )
                d.say(
                    f"The {setup['direction']} setup ({setup['state']}) is invalidated by {beyond}, a change of HTF bias, or expiry at the trading-day end."
                )
                if level_value is None:
                    d.unknowns.append("Protective level: NOT CONFIRMED")
            if plan.get("plan"):
                d.say(f"The confirmed plan's stop is {plan['plan']['stop']} (not authorized).")
    elif intent is Intent.TRADE_PLAN:
        _, plan = await ctx.call("get_trade_plan", {"symbol": sym})
        if not _unavailable(d, plan, "Trade plan"):
            p = plan.get("plan")
            d.fact("Outcome", plan["outcome"], "get_trade_plan")
            if p is None:
                d.say("There is no confirmed plan: no entry, stop or targets exist yet.")
                d.unknowns.append("Entry/stop/targets: NOT CONFIRMED")
            else:
                d.fact(
                    "Plan",
                    f"{p['model']} entry {p['entry']} stop {p['stop']} TP1 {p['tp1']} R:R {p['rr1']}",
                    "get_trade_plan",
                )
                d.say(
                    f"Confirmed plan (NOT AUTHORIZED): {p['model']} entry {p['entry']}, stop {p['stop']}, TP1 {p['tp1']} (R:R {p['rr1']}). It stays blocked: "
                    + ", ".join(plan["missingGates"] + plan["hardBlockers"])
                    + "."
                )
    elif intent is Intent.RISK:
        _, risk = await ctx.call("get_risk_calculation", {"symbol": sym})
        if not _unavailable(d, risk, "Risk"):
            d.fact("Risk status", risk["status"], "get_risk_calculation")
            d.fact("Locks", [x["lock"] for x in risk["locks"]], "get_risk_calculation")
            d.fact("Size status", risk.get("sizeStatus"), "get_risk_calculation")
            d.say(
                f"Risk status {risk['status']}"
                + (f"; locks: {', '.join(x['lock'] for x in risk['locks'])}" if risk["locks"] else "")
                + ". Risk has final veto."
            )
    elif intent is Intent.SESSION:
        _, ses = await ctx.call("get_session_state", {"symbol": sym})
        clock = ses["clock"]
        d.fact("New York time", clock["newYorkTime"], "get_session_state")
        d.fact("Active sessions", clock["activeSessions"] or ["none"], "get_session_state")
        d.fact("Time quality", clock["timeQuality"], "get_session_state")
        d.say(
            f"New York time {clock['newYorkTime']}; active sessions: {', '.join(clock['activeSessions']) or 'none'}; kill zones: {', '.join(clock['activeKillZones']) or 'none'}; time quality {clock['timeQuality']}. Time never creates a trade by itself."
        )
    elif intent in (Intent.A_PLUS_SETUPS, Intent.COMPARE):
        symbols = [s.upper() for s in compare] if intent is Intent.COMPARE and compare else None
        _, scan = await ctx.call("scan_markets", {"symbols": symbols} if symbols else {})
        rows = scan["rows"]
        if intent is Intent.A_PLUS_SETUPS:
            best = [r for r in rows if r["setupGrade"] == "A+"]
            d.say(
                "A+ setups: "
                + (", ".join(r["symbol"] for r in best) if best else "none right now")
                + ". A grade ranks attention; it is not a probability or a trade signal."
            )
        else:
            d.say("Attention ranking (each market's own decision, not a trade signal):")
        for r in rows[:9]:
            d.fact(
                f"#{r['rank']} {r['symbol']}",
                f"{r['verdict']} · {r['setupState']} · score {r['setupScore']} · {'validated' if r['deeplyValidated'] else 'research only'}",
                "scan_markets",
            )
    elif intent is Intent.BACKTEST:
        _, bt = await ctx.call("run_backtest", {"symbol": sym})
        if not _unavailable(d, bt, "Backtesting"):
            d.say(
                f"Latest {sym} backtest ({bt['start'][:10]} to {bt['end'][:10]}"
                + (", SYNTHETIC data" if bt["synthetic"] else "")
                + "): a research replay of the live setup engine, not authorized trades."
            )
            for v in bt["variants"]:
                d.fact(
                    f"Variant {v['name']} ({v['entryMode']})",
                    f"{v['plansConfirmed']} plans · {v['closed']} closed ({v['sampleLabel']}) · win rate {v['winRate']}"
                    f" · expectancy {v['expectancyR']} R · total {v['totalR']} R",
                    "run_backtest",
                )
                if v["sampleLabel"] == "INSUFFICIENT":
                    d.unknowns.append(
                        f"Variant {v['name']} edge: UNKNOWN (fewer than 30 closed simulated trades)"
                    )
            d.say(
                "Risk, news and authority gates were not applied; past simulated results are not a forecast."
            )
    elif intent is Intent.NEWS:
        _, news = await ctx.call("get_news_state", {"symbol": sym})
        if not _unavailable(d, news, "News"):
            d.fact("News state", news["state"], "get_news_state")
            d.fact("Relevant currencies", news["relevantCurrencies"], "get_news_state")
            if news["state"] == "UNAVAILABLE":
                reason = news["calendar"]["reason"]
                d.say(
                    f"News state is UNAVAILABLE: {reason}. The decision stays blocked until news can be proven clear."
                )
                d.unknowns.append(f"Upcoming news: UNKNOWN ({reason})")
            else:
                d.say(f"News state for {sym} ({', '.join(news['relevantCurrencies'])}): {news['state']}.")
                active = news.get("activeEvent")
                if active:
                    d.fact(
                        "Active event",
                        f"{active['importance']} {active['currency']} {active['name']} @ {active['scheduledTime']}",
                        "get_news_state",
                    )
                    d.say(
                        f"Driven by {active['importance']} {active['currency']} {active['name']} at {active['scheduledTime']}; window ends {news.get('windowEnd')}."
                    )
                nxt = news.get("nextEvent")
                if nxt:
                    d.fact(
                        "Next event",
                        f"{nxt['importance']} {nxt['currency']} {nxt['name']} @ {nxt['scheduledTime']}",
                        "get_news_state",
                    )
                    d.say(
                        f"Next relevant event: {nxt['importance']} {nxt['currency']} {nxt['name']} at {nxt['scheduledTime']} ({nxt['minutesToEvent']} min)."
                    )
                if news["calendar"]["isSynthetic"]:
                    d.say("The calendar is synthetic, so the news gate cannot clear the decision.")
    elif intent is Intent.JOURNAL:
        _, journal = await ctx.call("query_journal", {"symbol": sym})
        if not _unavailable(d, journal, "Journal"):
            entries = journal["entries"]
            d.fact("Journal entries (latest 10)", len(entries), "query_journal")
            if not entries:
                d.say(f"Your journal has no {sym} entries yet.")
            else:
                counts = ", ".join(f"{k} {v}" for k, v in journal["resultCounts"].items())
                d.say(f"Your latest {len(entries)} {sym} journal entries: {counts}.")
                for e in entries[:5]:
                    d.fact(
                        f"{e['createdAt'][:16]} {e['kind']}",
                        f"{e['direction'] or '-'} · {e['result'] or 'OPEN'} · R {e['rMultiple']} · {e['classification'] or '-'}"
                        + (
                            f" · violations: {', '.join(e['detectedViolations'])}"
                            if e["detectedViolations"]
                            else ""
                        ),
                        "query_journal",
                    )
                if any(e["isSynthetic"] for e in entries):
                    d.say("Some entries were logged on synthetic market data.")
            stats = journal.get("statistics")
            if stats and stats["closedTrades"]:
                d.fact("Closed trades (verified, non-synthetic)", stats["closedTrades"], "query_journal")
                d.fact("Sample-size label", stats["sampleLabel"], "query_journal")
                d.say(
                    f"Recorded statistics over {stats['closedTrades']} closed {sym} trades ({stats['sampleLabel']}): "
                    f"win rate {stats['winRate']}, expectancy {stats['expectancyR']} R, total {stats['totalR']} R. "
                    f"{stats['disclaimer']}"
                )
                if stats["sampleLabel"] == "INSUFFICIENT":
                    d.unknowns.append("Performance edge: UNKNOWN (fewer than 30 closed trades)")
            else:
                d.say("There are no verified closed trades to describe yet, so no statistics are shown.")
    elif intent is Intent.MACRO:
        _, macro = await ctx.call("get_macro_state", {"symbol": sym})
        if not _unavailable(d, macro, "Macro"):
            d.fact("Macro bias", macro["bias"], "get_macro_state")
            if not macro["available"]:
                d.say(f"Macro is UNAVAILABLE: {macro['reason']}. The MACRO score factor is not evaluated.")
                d.unknowns.append(f"Macro context: UNKNOWN ({macro['reason']})")
            else:
                d.fact("Macro score", macro["score"], "get_macro_state")
                d.fact("Macro state vs setup", macro.get("state"), "get_macro_state")
                d.say(
                    f"Macro bias for {sym}: {macro['bias']} (score {macro['score']} on -1..+1)."
                    + (
                        f" Versus the open {macro['direction']} setup it is {macro['state']}."
                        if macro.get("state")
                        else " There is no open setup direction to compare it with."
                    )
                )
                for drv in macro["drivers"]:
                    if drv["contribution"] is None:
                        continue
                    d.fact(
                        f"Driver {drv['series']}",
                        f"{drv['direction']} · contribution {drv['contribution']} (weight {drv['weight']})",
                        "get_macro_state",
                    )
                corr = macro.get("correlation")
                if corr:
                    d.fact("Correlation regime", f"{corr['series']} {corr['regime']}", "get_macro_state")
                    if corr["regime"] == "INVERTED":
                        d.say(
                            f"The usual {corr['series']} relationship is inverted, so its weight is reduced."
                        )
                if macro["isSynthetic"]:
                    d.say("The macro series are synthetic, so macro is shown but not scored.")
                d.say(
                    "Macro is context: it adjusts score and confidence and never blocks or creates a trade."
                )
    elif intent is Intent.CREATE_ALERT:
        alert_word: str | None = next(
            (w for w in DIRECTION_WORDS if re.search(rf"\b{w}\b", question.lower())), None
        )
        _, payload = await ctx.call(
            "create_alert", {"symbol": sym, "direction": DIRECTION_WORDS[alert_word] if alert_word else None}
        )
        d.proposal = AlertProposal(symbol=sym, direction=payload["direction"], reason=payload["reason"])
        d.say(
            f"Proposed an ALERT_ME_WHEN_READY watch for {sym}. It is not created until you confirm it in the ALERTS view."
        )

    if verdict == Verdict.UNAVAILABLE.value and intent not in (
        Intent.HELP,
        Intent.EXPLAIN_TERM,
        Intent.BACKTEST,
        Intent.MACRO,
        Intent.NEWS,
        Intent.JOURNAL,
    ):
        d.unknowns.append(f"{sym} analysis: UNAVAILABLE while the decision is UNAVAILABLE")
    return intent, d
