"""AI assistant V1 models (spec STEP 14, Phase 12): explains deterministic outputs, never decides."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.domain.base import ApiModel
from app.domain.enums import (
    AssistantIntent,
    AssistantProvider,
    Blocker,
    DataQuality,
    Direction,
    EducationLevel,
    GuardStatus,
    ToolStatus,
    Verdict,
)

MAX_QUESTION = 500


class AskRequest(ApiModel):
    symbol: str = Field(pattern=r"^[A-Za-z]{3,12}$")
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    level: EducationLevel = EducationLevel.INTERMEDIATE
    compare_symbols: list[str] = Field(default_factory=list, max_length=8)


class Fact(ApiModel):
    """A value copied from a deterministic service. `source` names the tool that returned it."""

    label: str
    value: str
    source: str


class ToolCallRecord(ApiModel):
    name: str
    symbol: str | None
    status: ToolStatus
    detail: str | None


class DecisionRef(ApiModel):
    symbol: str
    verdict: Verdict
    data_quality: DataQuality
    blockers: list[Blocker]
    strategy_version: str
    updated_at: datetime


class AlertProposal(ApiModel):
    """create_alert is never executed by the assistant: the user confirms the proposal in the UI."""

    symbol: str
    direction: Direction | None
    reason: str


class GuardReport(ApiModel):
    status: GuardStatus
    violations: list[str]


class AssistantAnswer(ApiModel):
    symbol: str
    question: str
    intent: AssistantIntent
    level: EducationLevel
    answer: str
    facts: list[Fact]
    unknowns: list[str]
    tools: list[ToolCallRecord]
    decision: DecisionRef | None
    proposal: AlertProposal | None
    provider: AssistantProvider
    model: str | None
    guard: GuardReport
    authority: str
    strategy_version: str
    generated_at: datetime


class ToolInfo(ApiModel):
    name: str
    description: str
    available: bool
    detail: str | None


class AssistantCapabilities(ApiModel):
    provider: AssistantProvider
    model: str | None
    external_ai_configured: bool
    shares_account_data: bool
    commands: list[str]
    levels: list[EducationLevel]
    tools: list[ToolInfo]
    authority: str
