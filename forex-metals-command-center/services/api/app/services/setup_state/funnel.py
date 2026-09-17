"""Setup-funnel diagnosis: classify why setups terminated.

Observational only. This module re-decides nothing, changes no gate or threshold, and adds no
look-ahead: it reads the reason string the setup engine already recorded and buckets it into a
stable code. Ported in spirit from V2's `scripts/diag_funnel.py`, but keyed off this codebase's
`Setup.reason` wording (see `setup_state/engine.py`) rather than V2's transition history.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.enums import SetupState
from app.services.setup_state.models import Setup


class TerminalReason(StrEnum):
    """Why a setup stopped progressing, at the granularity a human can act on."""

    # Ran out of time or structure at a specific stage
    EXPIRED_TRADING_DAY = "EXPIRED_TRADING_DAY"
    EXPIRED_NO_MSS = "EXPIRED_NO_MSS"
    EXPIRED_NO_FVG = "EXPIRED_NO_FVG"
    EXPIRED_NO_RETRACEMENT = "EXPIRED_NO_RETRACEMENT"
    EXPIRED_NO_CONFIRMATION = "EXPIRED_NO_CONFIRMATION"
    EXPIRED_OTHER = "EXPIRED_OTHER"

    # Killed by price or context
    INVALIDATED_HTF_FLIP = "INVALIDATED_HTF_FLIP"
    INVALIDATED_LIQUIDITY_RAN = "INVALIDATED_LIQUIDITY_RAN"
    INVALIDATED_PROTECTIVE = "INVALIDATED_PROTECTIVE"
    INVALIDATED_DO_NOT_CHASE = "INVALIDATED_DO_NOT_CHASE"
    INVALIDATED_ZONES_GONE = "INVALIDATED_ZONES_GONE"
    INVALIDATED_STOP_TRADED = "INVALIDATED_STOP_TRADED"
    INVALIDATED_OTHER = "INVALIDATED_OTHER"

    ENTRY_MISSED = "ENTRY_MISSED"
    LIVE = "LIVE"
    UNKNOWN = "UNKNOWN"


# Ordered: first matching fragment wins, so more specific phrases come first.
_EXPIRED_PATTERNS: tuple[tuple[str, TerminalReason], ...] = (
    ("trading day ended", TerminalReason.EXPIRED_TRADING_DAY),
    ("no confirmation break", TerminalReason.EXPIRED_NO_MSS),
    ("no fvg formed", TerminalReason.EXPIRED_NO_FVG),
    ("no entry confirmation", TerminalReason.EXPIRED_NO_CONFIRMATION),
    ("no retracement", TerminalReason.EXPIRED_NO_RETRACEMENT),
)

_INVALIDATED_PATTERNS: tuple[tuple[str, TerminalReason], ...] = (
    ("htf bias changed", TerminalReason.INVALIDATED_HTF_FLIP),
    ("liquidity ran", TerminalReason.INVALIDATED_LIQUIDITY_RAN),
    ("do not chase", TerminalReason.INVALIDATED_DO_NOT_CHASE),
    ("every retracement zone", TerminalReason.INVALIDATED_ZONES_GONE),
    ("plan stop traded", TerminalReason.INVALIDATED_STOP_TRADED),
    ("protective extreme", TerminalReason.INVALIDATED_PROTECTIVE),
)


def _match(reason: str, patterns: tuple[tuple[str, TerminalReason], ...]) -> TerminalReason | None:
    lowered = reason.lower()
    return next((code for fragment, code in patterns if fragment in lowered), None)


def classify_terminal(setup: Setup) -> TerminalReason:
    """Bucket one setup. Live setups report LIVE; unrecognised wording falls back to *_OTHER."""
    if not setup.terminal:
        return TerminalReason.LIVE
    reason = setup.reason or ""
    if setup.state is SetupState.EXPIRED:
        return _match(reason, _EXPIRED_PATTERNS) or TerminalReason.EXPIRED_OTHER
    if setup.state is SetupState.INVALIDATED:
        return _match(reason, _INVALIDATED_PATTERNS) or TerminalReason.INVALIDATED_OTHER
    if setup.state is SetupState.ENTRY_MISSED:
        return TerminalReason.ENTRY_MISSED
    return TerminalReason.UNKNOWN


@dataclass(frozen=True)
class FunnelDiagnosis:
    """Terminal-reason histogram over unique setups."""

    total: int = 0
    counts: dict[TerminalReason, int] = field(default_factory=dict)
    dominant: TerminalReason | None = None
    dominant_share: float = 0.0

    def report(self) -> str:
        if not self.total:
            return "no setups observed"
        lines = [f"{self.total} unique setup(s)"]
        lines += [
            f"  {code.value:<28} {n:>5}  ({n / self.total:>6.1%})" for code, n in self.counts.items()
        ]
        if self.dominant is not None:
            lines.append(f"dominant blocker: {self.dominant.value} ({self.dominant_share:.1%})")
        return "\n".join(lines)


def summarize(setups: Iterable[Setup]) -> FunnelDiagnosis:
    """Classify setups, deduped by id (a setup is reported at many replay steps)."""
    latest: dict[str, Setup] = {}
    for s in setups:
        latest[s.id] = s  # last observation wins: the terminal one
    if not latest:
        return FunnelDiagnosis()
    tally = Counter(classify_terminal(s) for s in latest.values())
    ordered = dict(tally.most_common())
    top, top_n = tally.most_common(1)[0]
    total = len(latest)
    return FunnelDiagnosis(
        total=total, counts=ordered, dominant=top, dominant_share=top_n / total
    )
