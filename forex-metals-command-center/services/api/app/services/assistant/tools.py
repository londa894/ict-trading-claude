"""Read-only assistant tools over the deterministic services (spec STEP 14 function list).

Every tool reads the same objects the dashboard reads (one Master Decision everywhere), scoped to the
request's symbol (asset contexts isolated). Analysis values are withheld when the data is not eligible for a
decision. Engines that do not exist yet return UNAVAILABLE; create_alert only returns a proposal for the user
to confirm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import AnalyticsSource, ToolStatus, Verdict
from app.services.analytics.service import AnalyticsService
from app.services.backtest.service import BacktestService
from app.services.journal.service import JournalService
from app.services.market_state.service import MarketStateResponse, MarketStateService
from app.services.scanner.service import ScannerService
from app.services.scoring.models import DecisionEvaluation
from app.services.scoring.service import EvaluationService
from app.services.setup_state.service import SetupRun
from app.services.structure.service import StructureService

JSON = dict[str, Any]
LAST_N = 5


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json", by_alias=True) if model is not None else None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    symbol_scoped: bool
    available: bool = True
    detail: str | None = None
    extra_properties: dict[str, Any] = field(default_factory=dict)

    def input_schema(self) -> JSON:
        props: JSON = (
            {"symbol": {"type": "string", "description": "The conversation's symbol"}}
            if self.symbol_scoped
            else {}
        )
        props.update(self.extra_properties)
        return {"type": "object", "properties": props, "required": ["symbol"] if self.symbol_scoped else []}


TOOLS: list[ToolSpec] = [
    ToolSpec(
        "get_market_state",
        "The Master Decision (verdict, blockers, bias, DOL, setup, score, risk status).",
        True,
    ),
    ToolSpec(
        "get_structure", "Multi-timeframe structure alignment and the latest M15 structure events.", True
    ),
    ToolSpec(
        "get_liquidity",
        "Draw on liquidity, the strongest untaken pools and the latest liquidity events (M15).",
        True,
    ),
    ToolSpec("get_pd_array_state", "Active FVG/IFVG zones and the latest displacement (M15).", True),
    ToolSpec(
        "get_no_wick_events",
        "The latest No Wick events with candle, context and relevance scores (context only).",
        True,
    ),
    ToolSpec("get_session_state", "Session clock, kill zones, time quality, ADR and Judas swings.", True),
    ToolSpec(
        "get_trade_plan",
        "The open setup, its steps, the confirmed plan if any (never authorized), score and warnings.",
        True,
    ),
    ToolSpec(
        "get_risk_calculation",
        "Risk status, locks, limits and position-size status for the confirmed plan.",
        True,
    ),
    ToolSpec(
        "scan_markets",
        "Each market's own decision, ranked for attention (never a trade signal).",
        False,
        extra_properties={"symbols": {"type": "array", "items": {"type": "string"}}},
    ),
    ToolSpec(
        "get_news_state",
        "The news gate: relevant economic calendar events and CLEAR / CAUTION / BLACKOUT / POST_NEWS_WAIT.",
        True,
    ),
    ToolSpec(
        "get_macro_state",
        "Macro context: DXY / yields / VIX drivers, bias, state vs the open setup, correlation regime. "
        "Context only: it modifies score and confidence and never blocks or creates a trade.",
        True,
    ),
    ToolSpec(
        "query_journal",
        "The user's recent journal entries for this market (kind, result, R, process class, violations) and "
        "descriptive statistics with the sample-size label (fill prices withheld unless sharing is enabled).",
        True,
    ),
    ToolSpec(
        "run_backtest",
        "Summary of the latest completed backtest (research replay) for this market: funnel, statistics with "
        "the sample-size label and disclosures. Read-only: the assistant never starts a run.",
        True,
    ),
    ToolSpec(
        "create_alert",
        "Propose an ALERT_ME_WHEN_READY watch. Never executed by the assistant: the user confirms it.",
        True,
        extra_properties={"direction": {"type": "string", "enum": ["BULLISH", "BEARISH"]}},
    ),
]
TOOL_BY_NAME = {t.name: t for t in TOOLS}


class AssistantContext:
    """Per-request, lazily loaded state for one symbol. Every value returned is recorded for grounding."""

    def __init__(
        self,
        symbol: str,
        market_state: MarketStateService,
        evaluation: EvaluationService,
        structure: StructureService,
        scanner: ScannerService,
        share_account_data: bool,
        journal: JournalService | None = None,
        analytics: AnalyticsService | None = None,
        backtest: BacktestService | None = None,
    ) -> None:
        self.symbol = symbol
        self._market_state = market_state
        self._evaluation = evaluation
        self._structure = structure
        self._scanner = scanner
        self._share = share_account_data
        self._journal = journal
        self._analytics = analytics
        self._backtest = backtest
        self._market: MarketStateResponse | None = None
        self._eval: tuple[DecisionEvaluation, SetupRun] | None = None
        self.calls: list[tuple[str, str | None, ToolStatus, str | None]] = []
        self.payloads: list[Any] = []

    async def market(self) -> MarketStateResponse:
        if self._market is None:
            self._market = await self._market_state.evaluate(self.symbol)
        return self._market

    async def evaluation(self) -> tuple[DecisionEvaluation, SetupRun]:
        if self._eval is None:
            self._eval = await self._evaluation.evaluate_with_run(self.symbol)
        return self._eval

    async def usable(self) -> bool:
        decision = (await self.market()).decision
        ev, _ = await self.evaluation()
        return decision.verdict is not Verdict.UNAVAILABLE and ev.eligible_for_decision

    async def call(self, name: str, args: JSON) -> tuple[ToolStatus, JSON]:
        spec = TOOL_BY_NAME.get(name)
        requested = str(args.get("symbol", self.symbol)).upper() if spec and spec.symbol_scoped else None
        if spec is None:
            return self._record(name, None, ToolStatus.REJECTED, "unknown tool", {"status": "REJECTED"})
        if requested is not None and requested != self.symbol:
            isolated = f"asset contexts are isolated: this conversation is about {self.symbol}"
            return self._record(
                name, requested, ToolStatus.REJECTED, isolated, {"status": "REJECTED", "reason": isolated}
            )
        if not spec.available:
            return self._record(
                name,
                requested,
                ToolStatus.UNAVAILABLE,
                spec.detail,
                {"status": "UNAVAILABLE", "reason": spec.detail},
            )
        payload = await getattr(self, f"_{name}")(args)
        status = ToolStatus.PROPOSED if name == "create_alert" else ToolStatus.OK
        detail: str | None = str(payload.get("reason")) if payload.get("status") == "UNAVAILABLE" else None
        if payload.get("status") == "UNAVAILABLE":
            status = ToolStatus.UNAVAILABLE
        return self._record(name, requested, status, detail, payload)

    def _record(
        self, name: str, symbol: str | None, status: ToolStatus, detail: str | None, payload: JSON
    ) -> tuple[ToolStatus, JSON]:
        self.calls.append((name, symbol, status, detail))
        self.payloads.append(payload)
        return status, payload

    async def _withheld(self) -> JSON | None:
        if await self.usable():
            return None
        decision = (await self.market()).decision
        blockers = ", ".join(b.value for b in decision.blockers)
        return {
            "status": "UNAVAILABLE",
            "reason": f"data not eligible for a decision ({decision.verdict.value}: {blockers})",
        }

    # --- tools ------------------------------------------------------------------------------------------

    async def _get_market_state(self, _args: JSON) -> JSON:
        m = await self.market()
        return {
            "decision": _dump(m.decision),
            "data": {
                "provider": m.data.provider,
                "isSynthetic": m.data.is_synthetic,
                "timeframe": m.data.timeframe.value,
                "quality": m.data.quality.value,
                "marketStatus": m.data.market_status.value,
                "latestClosedOpenTime": m.data.latest_closed_open_time.isoformat()
                if m.data.latest_closed_open_time
                else None,
            },
        }

    async def _get_structure(self, _args: JSON) -> JSON:
        withheld = await self._withheld()
        if withheld:
            return withheld
        alignment = await self._structure.alignment(self.symbol)
        _, run = await self.evaluation()
        events = run.pipeline.structure.events[-LAST_N:] if run.pipeline else []
        return {
            "alignment": _dump(alignment),
            "m15Events": [
                {
                    "type": e.type.value,
                    "level": e.level.value,
                    "direction": e.direction.value,
                    "status": e.status.value,
                    "price": e.price,
                    "time": e.time.isoformat(),
                }
                for e in events
            ],
        }

    async def _get_liquidity(self, _args: JSON) -> JSON:
        withheld = await self._withheld()
        if withheld:
            return withheld
        _, run = await self.evaluation()
        assert run.pipeline is not None
        liq = run.pipeline.liquidity
        pools = sorted(
            (p for p in liq.pools if not p.taken and p.magnet_score is not None),
            key=lambda p: -(p.magnet_score or 0),
        )
        return {
            "timeframe": liq.timeframe.value,
            "dol": _dump(liq.dol),
            "topPools": [
                {
                    "label": p.label,
                    "side": p.side.value,
                    "price": p.price,
                    "state": p.state.value,
                    "distanceAtr": p.distance_atr,
                    "magnetScore": p.magnet_score,
                }
                for p in pools[:6]
            ],
            "recentEvents": [
                {
                    "type": e.type.value,
                    "pool": e.pool_type.value,
                    "side": e.side.value,
                    "price": e.price,
                    "time": e.time.isoformat(),
                }
                for e in liq.events[-LAST_N:]
            ],
        }

    async def _get_pd_array_state(self, _args: JSON) -> JSON:
        withheld = await self._withheld()
        if withheld:
            return withheld
        _, run = await self.evaluation()
        assert run.pipeline is not None
        pd = run.pipeline.pd_arrays
        active = sorted((z for z in pd.zones if z.active), key=lambda z: -(z.quality_score or 0))
        last = pd.displacements[-1] if pd.displacements else None
        return {
            "timeframe": pd.timeframe.value,
            "activeZones": [
                {
                    "type": z.type.value,
                    "direction": z.direction.value,
                    "top": z.top,
                    "bottom": z.bottom,
                    "state": z.state.value,
                    "fillPct": z.fill_pct,
                    "qualityScore": z.quality_score,
                }
                for z in active[:LAST_N]
            ],
            "latestDisplacement": {
                "grade": last.grade.value,
                "direction": last.direction.value,
                "magnitudeAtr": last.magnitude_atr,
                "time": last.time.isoformat(),
            }
            if last
            else None,
        }

    async def _get_no_wick_events(self, _args: JSON) -> JSON:
        withheld = await self._withheld()
        if withheld:
            return withheld
        _, run = await self.evaluation()
        assert run.pipeline is not None
        nw = run.pipeline.no_wick
        return {
            "authority": "CONTEXT_ONLY",
            "timeframe": nw.timeframe.value,
            "events": [
                {
                    "classification": e.classification.value,
                    "strength": e.strength.value,
                    "direction": e.direction.value,
                    "time": e.time.isoformat(),
                    "candleQualityScore": e.candle_quality_score,
                    "contextScore": e.context_score,
                    "relevanceScore": e.relevance_score,
                }
                for e in nw.events[-LAST_N:]
            ],
        }

    async def _get_session_state(self, _args: JSON) -> JSON:
        _, run = await self.evaluation()
        s = run.session
        return {
            "clock": _dump(s.clock),
            "sessionQuality": s.session_quality.value if s.session_quality else None,
            "adr": _dump(s.adr),
            "latestJudas": _dump(s.judas[-1]) if s.judas else None,
            "eligibleForDecision": s.eligible_for_decision,
        }

    async def _get_trade_plan(self, _args: JSON) -> JSON:
        withheld = await self._withheld()
        if withheld:
            return withheld
        ev, run = await self.evaluation()
        current = run.analysis.current
        return {
            "authority": "NOT_AUTHORIZED",
            "outcome": ev.outcome.value,
            "setup": {
                "direction": current.direction.value,
                "state": current.state.value,
                "type": current.setup_type.value,
                "protectiveLevel": current.protective_level,
                "nextRequiredEvent": current.next_required_event,
                "steps": [
                    {"step": s.step.value, "status": s.status.value, "detail": s.detail}
                    for s in current.steps
                ],
            }
            if current
            else None,
            "lastClosedSetup": {
                "state": run.analysis.setups[-1].state.value,
                "reason": run.analysis.setups[-1].reason,
            }
            if run.analysis.setups and current is None
            else None,
            "plan": _dump(ev.plan),
            "score": ev.score,
            "grade": ev.grade.value if ev.grade else None,
            "confidence": ev.confidence.value,
            "warnings": [w.value for w in ev.warnings],
            "hardBlockers": [b.value for b in ev.hard_blockers],
            "missingGates": [b.value for b in ev.missing_gates],
            "devilsAdvocate": ev.devils_advocate,
        }

    async def _get_risk_calculation(self, _args: JSON) -> JSON:
        ev, _ = await self.evaluation()
        risk = ev.risk
        if risk is None:
            return {"status": "UNAVAILABLE", "reason": "risk gate not wired"}
        data: JSON = _dump(risk)
        if not self._share:
            # Account data stays private: no balance-derived amounts leave the server.
            data["budget"] = None
            data["currency"] = None
            data["locks"] = [
                {"lock": x.lock.value, "detail": "detail withheld (account data)"} for x in risk.locks
            ]
            if data.get("position"):
                for key in (
                    "riskAmount",
                    "riskAmountWithoutSpread",
                    "riskPerVolume",
                    "marginRequired",
                    "detail",
                ):
                    data["position"][key] = None
            data["accountDataWithheld"] = True
        return data

    async def _get_news_state(self, _args: JSON) -> JSON:
        ev, _ = await self.evaluation()
        if ev.news is None:
            return {"status": "UNAVAILABLE", "reason": "news gate not wired"}
        payload: JSON = _dump(ev.news)
        return payload

    async def _query_journal(self, _args: JSON) -> JSON:
        if self._journal is None:
            return {"status": "UNAVAILABLE", "reason": "journal not wired"}
        listing = await self._journal.list_entries(symbol=self.symbol, limit=10)
        if not listing.store.available:
            return {"status": "UNAVAILABLE", "reason": listing.store.reason}
        rows = [r.model_dump(mode="json", by_alias=True) for r in listing.entries]
        if not self._share:
            for row in rows:
                row["entry"] = None  # prices of the user's own fills are private unless sharing is enabled
        counts: dict[str, int] = {}
        for r in listing.entries:
            key = r.result.value if r.result else "OPEN"
            counts[key] = counts.get(key, 0) + 1
        return {
            "symbol": self.symbol,
            "store": listing.store.backend,
            "entries": rows,
            "resultCounts": counts,
            "entryPricesWithheld": not self._share,
            "statistics": await self._journal_statistics(),
        }

    async def _journal_statistics(self) -> JSON | None:
        if self._analytics is None:
            return None
        report = await self._analytics.report(AnalyticsSource.JOURNAL, symbol=self.symbol)
        if not report.available:
            return None
        o = report.overall
        return {
            "authority": report.authority,
            "closedTrades": o.count,
            "sampleLabel": o.label.value,
            "winRate": o.win_rate,
            "expectancyR": o.expectancy_r,
            "totalR": o.total_r,
            "excludedSynthetic": report.excluded.synthetic,
            "disclaimer": report.disclaimer,
        }

    async def _get_macro_state(self, _args: JSON) -> JSON:
        ev, _ = await self.evaluation()
        if ev.macro is None:
            return {"status": "UNAVAILABLE", "reason": "macro not wired"}
        payload: JSON = _dump(ev.macro)
        payload["authority"] = "CONTEXT_ONLY"
        return payload

    async def _scan_markets(self, args: JSON) -> JSON:
        symbols = args.get("symbols") or None
        scan = await self._scanner.scan([str(s) for s in symbols] if symbols else None)
        return {
            "authority": "NOT_AUTHORIZED",
            "ranking": scan.ranking,
            "rows": [
                {
                    "rank": r.rank,
                    "symbol": r.symbol,
                    "deeplyValidated": r.deeply_validated,
                    "verdict": r.verdict.value,
                    "dataQuality": r.data_quality.value,
                    "setupState": r.setup_state,
                    "setupScore": r.setup_score,
                    "setupGrade": r.setup_grade,
                    "riskStatus": r.risk_status,
                    "blockers": [b.value for b in r.blockers],
                }
                for r in scan.rows
            ],
        }

    async def _run_backtest(self, _args: JSON) -> JSON:
        if self._backtest is None:
            return {"status": "UNAVAILABLE", "reason": "backtesting not wired"}
        info = await self._backtest.info()
        if not info.available:
            return {"status": "UNAVAILABLE", "reason": info.reason}
        run = await self._backtest.latest_completed(self.symbol)
        if run is None or run.result is None:
            return {"status": "UNAVAILABLE", "reason": f"no completed backtest for {self.symbol} yet"}
        return {
            "authority": run.authority,
            "runId": run.id,
            "start": run.request.start.isoformat(),
            "end": run.request.end.isoformat(),
            "synthetic": run.result.data.is_synthetic,
            "variants": [
                {
                    "name": v.variant.name,
                    "entryMode": v.variant.entry_mode.value,
                    "steps": v.funnel.steps,
                    "plansConfirmed": v.funnel.plans_confirmed,
                    "closed": v.stats.count,
                    "sampleLabel": v.stats.label.value,
                    "winRate": v.stats.win_rate,
                    "expectancyR": v.stats.expectancy_r,
                    "totalR": v.stats.total_r,
                }
                for v in run.result.variants
            ],
            "disclosures": run.result.disclosures[:4],
        }

    async def _create_alert(self, args: JSON) -> JSON:
        direction = args.get("direction")
        return {
            "status": "PROPOSED",
            "symbol": self.symbol,
            "direction": direction if direction in ("BULLISH", "BEARISH") else None,
            "reason": "Not executed: confirm this ALERT_ME_WHEN_READY watch in the ALERTS view.",
        }
