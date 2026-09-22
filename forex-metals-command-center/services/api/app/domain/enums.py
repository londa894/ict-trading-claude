"""Canonical enums. Values are contract-tested against packages/strategy-spec/enums.json."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum


class Verdict(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    NO_TRADE = "NO_TRADE"
    UNAVAILABLE = "UNAVAILABLE"


class DecisionConfidence(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class DataQuality(StrEnum):
    LIVE = "LIVE"
    CURRENT = "CURRENT"
    DELAYED = "DELAYED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    INVALID = "INVALID"


class Timeframe(StrEnum):
    M1 = "M1"
    M3 = "M3"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"

    @property
    def is_fixed_intraday(self) -> bool:
        """True for timeframes whose buckets are fixed UTC-aligned durations (<= H1)."""
        return self in _FIXED_INTRADAY

    @property
    def is_trading_day_anchored(self) -> bool:
        """True for H4/D1: buckets anchored to the New York 17:00 trading-day roll (DST-aware)."""
        return self in (Timeframe.H4, Timeframe.D1)

    @property
    def is_engine_supported(self) -> bool:
        """Timeframes the candle engine can align, gap-check and aggregate. W1/MN1 are not supported yet."""
        return self.is_fixed_intraday or self.is_trading_day_anchored

    @property
    def duration(self) -> timedelta:
        if self is Timeframe.MN1:
            raise ValueError("MN1 has no fixed duration")
        return _DURATIONS[self]


_DURATIONS: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M3: timedelta(minutes=3),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
    Timeframe.W1: timedelta(weeks=1),
}

_FIXED_INTRADAY = frozenset(
    {Timeframe.M1, Timeframe.M3, Timeframe.M5, Timeframe.M15, Timeframe.M30, Timeframe.H1}
)


class AssetClass(StrEnum):
    METAL = "METAL"
    FX_MAJOR = "FX_MAJOR"
    FX_CROSS = "FX_CROSS"


class MarketStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    DAILY_BREAK = "DAILY_BREAK"
    UNKNOWN = "UNKNOWN"


class ResearchStatus(StrEnum):
    """Validation verdict of a historical dataset (written by the offline validator)."""

    APPROVED_FOR_RESEARCH = "APPROVED_FOR_RESEARCH"
    APPROVED_WITH_WARNINGS = "APPROVED_WITH_WARNINGS"
    NOT_APPROVED = "NOT_APPROVED"


class ProviderHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class IssueSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class ValidationIssueCode(StrEnum):
    NON_UTC_TIMESTAMP = "NON_UTC_TIMESTAMP"
    MISALIGNED_OPEN_TIME = "MISALIGNED_OPEN_TIME"
    NON_POSITIVE_PRICE = "NON_POSITIVE_PRICE"
    NON_FINITE_VALUE = "NON_FINITE_VALUE"
    IMPOSSIBLE_OHLC = "IMPOSSIBLE_OHLC"
    ZERO_RANGE = "ZERO_RANGE"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    DUPLICATE_IDENTICAL = "DUPLICATE_IDENTICAL"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    MISSING_BARS = "MISSING_BARS"
    SUSPECT_BAD_TICK = "SUSPECT_BAD_TICK"
    CROSSED_QUOTE = "CROSSED_QUOTE"
    WIDE_SPREAD = "WIDE_SPREAD"
    PROVIDER_DISAGREEMENT = "PROVIDER_DISAGREEMENT"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    EMPTY_SERIES = "EMPTY_SERIES"
    INCOMPLETE_BUCKET = "INCOMPLETE_BUCKET"


class PositionSizeStatus(StrEnum):
    VERIFIED = "VERIFIED"
    SIZED_FROM_USER_SPEC = "SIZED_FROM_USER_SPEC"
    POSITION_SIZE_UNVERIFIED = "POSITION_SIZE_UNVERIFIED"


class Blocker(StrEnum):
    SYSTEM_INTEGRITY_FAILURE = "SYSTEM_INTEGRITY_FAILURE"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    NO_DATA = "NO_DATA"
    DATA_INVALID = "DATA_INVALID"
    DATA_STALE = "DATA_STALE"
    DATA_DISCONNECTED = "DATA_DISCONNECTED"
    DATA_DELAYED = "DATA_DELAYED"
    DATA_GAP = "DATA_GAP"
    DATA_SUSPECT_BAD_TICK = "DATA_SUSPECT_BAD_TICK"
    DATA_SYNTHETIC = "DATA_SYNTHETIC"
    MARKET_CLOSED = "MARKET_CLOSED"
    INSTRUMENT_SPEC_MISSING = "INSTRUMENT_SPEC_MISSING"
    DOL_UNCLEAR = "DOL_UNCLEAR"
    ANALYSIS_GATES_NOT_IMPLEMENTED = "ANALYSIS_GATES_NOT_IMPLEMENTED"
    ENTRY_MISSED = "ENTRY_MISSED"
    INSUFFICIENT_RR = "INSUFFICIENT_RR"
    RISK_GATE_MISSING = "RISK_GATE_MISSING"
    NEWS_GATE_MISSING = "NEWS_GATE_MISSING"
    RISK_PROFILE_MISSING = "RISK_PROFILE_MISSING"
    RISK_PROFILE_INVALID = "RISK_PROFILE_INVALID"
    RISK_LOCKED = "RISK_LOCKED"
    UNSAFE_SPREAD = "UNSAFE_SPREAD"
    POSITION_SIZE_UNVERIFIED = "POSITION_SIZE_UNVERIFIED"
    MARKET_NOT_VALIDATED = "MARKET_NOT_VALIDATED"
    NEWS_BLACKOUT = "NEWS_BLACKOUT"
    NEWS_POST_WAIT = "NEWS_POST_WAIT"
    NEWS_DATA_UNAVAILABLE = "NEWS_DATA_UNAVAILABLE"
    NEWS_DATA_SYNTHETIC = "NEWS_DATA_SYNTHETIC"


# --- Phase 2: market structure -------------------------------------------------------------------


class StructureState(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    TRANSITIONING = "TRANSITIONING"
    UNCLEAR = "UNCLEAR"


class TrendDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NONE = "NONE"


class Direction(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class StructureLevel(StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingLabel(StrEnum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"
    EH = "EH"
    EL = "EL"
    NONE = "NONE"


class StructureEventType(StrEnum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    MSS = "MSS"


class StructureEventStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    POTENTIAL = "POTENTIAL"


class BreakConfirmation(StrEnum):
    CANDLE_CLOSE = "CANDLE_CLOSE"
    WICK_ONLY = "WICK_ONLY"


class QualifierStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"


class MtfAlignment(StrEnum):
    ALIGNED_BULLISH = "ALIGNED_BULLISH"
    ALIGNED_BEARISH = "ALIGNED_BEARISH"
    MIXED = "MIXED"
    UNCLEAR = "UNCLEAR"


class HtfBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    RANGING = "RANGING"
    TRANSITIONING = "TRANSITIONING"
    MIXED = "MIXED"
    UNCLEAR = "UNCLEAR"
    UNKNOWN = "UNKNOWN"


class AnalysisIneligibility(StrEnum):
    DATA_SYNTHETIC = "DATA_SYNTHETIC"
    DATA_STALE = "DATA_STALE"
    DATA_INVALID = "DATA_INVALID"
    DATA_DISCONNECTED = "DATA_DISCONNECTED"
    INSUFFICIENT_CANDLES = "INSUFFICIENT_CANDLES"
    KEY_LEVELS_UNAVAILABLE = "KEY_LEVELS_UNAVAILABLE"
    LIQUIDITY_ANALYSIS_FAILED = "LIQUIDITY_ANALYSIS_FAILED"
    PD_ARRAY_ANALYSIS_FAILED = "PD_ARRAY_ANALYSIS_FAILED"
    NO_WICK_ANALYSIS_FAILED = "NO_WICK_ANALYSIS_FAILED"
    SESSION_LEVELS_UNAVAILABLE = "SESSION_LEVELS_UNAVAILABLE"
    SESSION_ANALYSIS_FAILED = "SESSION_ANALYSIS_FAILED"
    SETUP_BIAS_UNAVAILABLE = "SETUP_BIAS_UNAVAILABLE"
    SETUP_ANALYSIS_FAILED = "SETUP_ANALYSIS_FAILED"
    EVALUATION_FAILED = "EVALUATION_FAILED"


# --- Phase 3: liquidity -------------------------------------------------------------------------


class LiquiditySide(StrEnum):
    BSL = "BSL"
    SSL = "SSL"


class LiquidityScope(StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class LiquidityPoolType(StrEnum):
    SWING_HIGH = "SWING_HIGH"
    SWING_LOW = "SWING_LOW"
    EQH = "EQH"
    EQL = "EQL"
    PDH = "PDH"
    PDL = "PDL"
    PWH = "PWH"
    PWL = "PWL"
    ASIA_HIGH = "ASIA_HIGH"
    ASIA_LOW = "ASIA_LOW"
    LONDON_HIGH = "LONDON_HIGH"
    LONDON_LOW = "LONDON_LOW"
    NY_AM_HIGH = "NY_AM_HIGH"
    NY_AM_LOW = "NY_AM_LOW"
    NY_PM_HIGH = "NY_PM_HIGH"
    NY_PM_LOW = "NY_PM_LOW"


class LiquidityState(StrEnum):
    FRESH = "FRESH"
    APPROACHING = "APPROACHING"
    TOUCHED = "TOUCHED"
    SWEPT = "SWEPT"
    RUN = "RUN"
    BROKEN = "BROKEN"
    RECLAIMED = "RECLAIMED"


class LiquidityEventType(StrEnum):
    TOUCH = "TOUCH"
    SWEEP = "SWEEP"
    BREAK = "BREAK"
    RUN = "RUN"
    RECLAIM = "RECLAIM"
    SWEEP_FAILED = "SWEEP_FAILED"


class DolConfidence(StrEnum):
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    UNCLEAR = "UNCLEAR"


# --- Phase 4: displacement + FVG/IFVG -------------------------------------------------------------


class DisplacementGrade(StrEnum):
    WEAK = "WEAK"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    EXCEPTIONAL = "EXCEPTIONAL"

    @property
    def rank(self) -> int:
        return list(DisplacementGrade).index(self)


class PdArrayType(StrEnum):
    FVG = "FVG"
    IFVG = "IFVG"
    IMR = "IMR"  # Immediate Rebalance: a displacement whose gap is instantly overlapped (inverse of an FVG)


class PdArrayState(StrEnum):
    FRESH = "FRESH"
    TOUCHED = "TOUCHED"
    PARTIAL = "PARTIAL"
    HALF = "HALF"
    FULL = "FULL"
    INVALIDATED = "INVALIDATED"


class IfvgStatus(StrEnum):
    POTENTIAL_IFVG = "POTENTIAL_IFVG"
    CONFIRMED_IFVG = "CONFIRMED_IFVG"
    FAILED_IFVG = "FAILED_IFVG"


class PdArrayEventType(StrEnum):
    CREATED = "CREATED"
    TOUCHED = "TOUCHED"
    PARTIAL_FILL = "PARTIAL_FILL"
    HALF_FILL = "HALF_FILL"
    FULL_FILL = "FULL_FILL"
    INVALIDATED = "INVALIDATED"
    IFVG_POTENTIAL = "IFVG_POTENTIAL"
    IFVG_CONFIRMED = "IFVG_CONFIRMED"
    IFVG_FAILED = "IFVG_FAILED"
    IMR_CREATED = "IMR_CREATED"  # an Immediate Rebalance zone formed (kept out of the alert map for now)


# --- Phase 5: No Wick Architecture V1 ----------------------------------------------------------


class NoWickClassification(StrEnum):
    TRUE_BULLISH_MARUBOZU = "TRUE_BULLISH_MARUBOZU"
    TRUE_BEARISH_MARUBOZU = "TRUE_BEARISH_MARUBOZU"
    NEAR_BULLISH_MARUBOZU = "NEAR_BULLISH_MARUBOZU"
    NEAR_BEARISH_MARUBOZU = "NEAR_BEARISH_MARUBOZU"
    BULLISH_NO_LOWER_WICK = "BULLISH_NO_LOWER_WICK"
    BEARISH_NO_UPPER_WICK = "BEARISH_NO_UPPER_WICK"
    BULLISH_NO_UPPER_WICK = "BULLISH_NO_UPPER_WICK"
    BEARISH_NO_LOWER_WICK = "BEARISH_NO_LOWER_WICK"
    NO_ORIGIN_SIDE_WICK = "NO_ORIGIN_SIDE_WICK"
    NO_DESTINATION_SIDE_WICK = "NO_DESTINATION_SIDE_WICK"
    NEWS_DRIVEN_NO_WICK = "NEWS_DRIVEN_NO_WICK"
    INSIGNIFICANT_NO_WICK = "INSIGNIFICANT_NO_WICK"


class NoWickStrength(StrEnum):
    INSIGNIFICANT = "INSIGNIFICANT"
    MEANINGFUL = "MEANINGFUL"
    STRONG = "STRONG"
    EXCEPTIONAL = "EXCEPTIONAL"

    @property
    def rank(self) -> int:
        return list(NoWickStrength).index(self)


class NoWickZoneState(StrEnum):
    FRESH = "FRESH"
    APPROACHING = "APPROACHING"
    TOUCHED = "TOUCHED"
    PARTIAL = "PARTIAL"
    HALF_REBALANCED = "HALF_REBALANCED"
    FULLY_REBALANCED = "FULLY_REBALANCED"
    REACTED = "REACTED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"


class NoWickZoneEventType(StrEnum):
    TOUCHED = "TOUCHED"
    REBALANCE_25 = "REBALANCE_25"
    REBALANCE_50 = "REBALANCE_50"
    REBALANCE_75 = "REBALANCE_75"
    FULLY_REBALANCED = "FULLY_REBALANCED"
    REACTED = "REACTED"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"


class NoWickContextFactor(StrEnum):
    DISPLACEMENT = "DISPLACEMENT"
    STRUCTURE = "STRUCTURE"
    LIQUIDITY = "LIQUIDITY"
    FVG = "FVG"
    TREND = "TREND"
    SESSION = "SESSION"
    NEWS = "NEWS"


class ScoreComponentStatus(StrEnum):
    EVALUATED = "EVALUATED"
    NOT_EVALUATED = "NOT_EVALUATED"


# --- Phase 6: session & time ---------------------------------------------------------------------


class SessionName(StrEnum):
    ASIA = "ASIA"
    LONDON = "LONDON"
    NY_AM = "NY_AM"
    NY_PM = "NY_PM"
    LONDON_CLOSE = "LONDON_CLOSE"


class KillZone(StrEnum):
    LONDON_KZ = "LONDON_KZ"
    NY_AM_KZ = "NY_AM_KZ"
    NY_PM_KZ = "NY_PM_KZ"


class SessionInstanceState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    FORMING = "FORMING"
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class AsianRangeState(StrEnum):
    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    EXPANDED = "EXPANDED"
    ABNORMALLY_LARGE = "ABNORMALLY_LARGE"


class SessionQuality(StrEnum):
    IDEAL = "IDEAL"
    ACCEPTABLE = "ACCEPTABLE"
    LOW_QUALITY = "LOW_QUALITY"
    AVOID = "AVOID"


class ExpansionState(StrEnum):
    CONSOLIDATING = "CONSOLIDATING"
    EARLY = "EARLY"
    ACTIVE = "ACTIVE"
    LATE = "LATE"
    EXHAUSTED = "EXHAUSTED"


class JudasStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


QUALITY_ORDER: tuple[SessionQuality, ...] = (
    SessionQuality.AVOID,
    SessionQuality.LOW_QUALITY,
    SessionQuality.ACCEPTABLE,
    SessionQuality.IDEAL,
)


# --- Phase 7: setup state machine -----------------------------------------------------------------


class SetupState(StrEnum):
    DISCOVERED = "DISCOVERED"
    WATCH = "WATCH"
    SETUP_FORMING = "SETUP_FORMING"
    LIQUIDITY_EVENT = "LIQUIDITY_EVENT"
    WAITING_FOR_MSS = "WAITING_FOR_MSS"
    SETUP_ARMED = "SETUP_ARMED"
    WAITING_FOR_RETRACEMENT = "WAITING_FOR_RETRACEMENT"
    ENTRY_ZONE_APPROACHING = "ENTRY_ZONE_APPROACHING"
    ENTRY_ZONE_TOUCHED = "ENTRY_ZONE_TOUCHED"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    LONG_READY = "LONG_READY"
    SHORT_READY = "SHORT_READY"
    ENTRY_MISSED = "ENTRY_MISSED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class SetupType(StrEnum):
    LIQUIDITY_SWEEP_MSS = "LIQUIDITY_SWEEP_MSS"


class SetupStep(StrEnum):
    HTF_BIAS = "HTF_BIAS"
    DOL_TARGET = "DOL_TARGET"
    LIQUIDITY_EVENT = "LIQUIDITY_EVENT"
    DISPLACEMENT = "DISPLACEMENT"
    MSS = "MSS"
    PD_ARRAY = "PD_ARRAY"
    RETRACEMENT = "RETRACEMENT"
    LTF_CONFIRMATION = "LTF_CONFIRMATION"
    RISK = "RISK"


class SetupStepStatus(StrEnum):
    DONE = "DONE"
    PENDING = "PENDING"
    NOT_EVALUATED = "NOT_EVALUATED"


class Po3Phase(StrEnum):
    ACCUMULATION = "ACCUMULATION"
    MANIPULATION = "MANIPULATION"
    DISTRIBUTION = "DISTRIBUTION"
    UNCLEAR = "UNCLEAR"


TERMINAL_SETUP_STATES = frozenset(
    {SetupState.INVALIDATED, SetupState.EXPIRED, SetupState.ENTRY_MISSED, SetupState.CLOSED}
)
# States the Phase 7 engine never emits: they need entry/verdict (Phase 8), risk (Phase 9) or trade tracking.
AUTHORITY_SETUP_STATES = frozenset(
    {SetupState.LONG_READY, SetupState.SHORT_READY, SetupState.ACTIVE, SetupState.CLOSED}
)


# --- Phase 8: entry & verdict --------------------------------------------------------------------


class EntryModel(StrEnum):
    CONSERVATIVE_RETEST = "CONSERVATIVE_RETEST"
    M15_CLOSE = "M15_CLOSE"
    LTF_REFINEMENT = "LTF_REFINEMENT"
    NO_WICK_REBALANCE = "NO_WICK_REBALANCE"
    BREAKER = "BREAKER"
    IFVG = "IFVG"
    LIMIT_RESEARCH = "LIMIT_RESEARCH"


class EntryMode(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    STANDARD = "STANDARD"
    AGGRESSIVE = "AGGRESSIVE"


class EntryWarning(StrEnum):
    EARLY_BEFORE_MSS = "EARLY_BEFORE_MSS"
    DURING_SWEEP = "DURING_SWEEP"
    INSIDE_DISPLACEMENT = "INSIDE_DISPLACEMENT"
    BEFORE_CLOSE = "BEFORE_CLOSE"
    BEFORE_RETRACEMENT = "BEFORE_RETRACEMENT"
    WEAK_FVG = "WEAK_FVG"
    AGAINST_HTF = "AGAINST_HTF"
    BEFORE_MAJOR_NEWS = "BEFORE_MAJOR_NEWS"


class ScoreFactor(StrEnum):
    HTF = "HTF"
    LIQUIDITY = "LIQUIDITY"
    STRUCTURE = "STRUCTURE"
    DISPLACEMENT = "DISPLACEMENT"
    PD_ARRAY = "PD_ARRAY"
    NO_WICK = "NO_WICK"
    SESSION = "SESSION"
    MACRO = "MACRO"
    ENTRY = "ENTRY"
    RISK_RR = "RISK_RR"


class SetupGrade(StrEnum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class EvaluationOutcome(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NO_TRADE = "NO_TRADE"
    WAIT = "WAIT"
    CONFIRMED_PENDING_GATES = "CONFIRMED_PENDING_GATES"
    CONFIRMED_AWAITING_AUTHORITY = "CONFIRMED_AWAITING_AUTHORITY"


# --- Phase 9: risk calculator ---------------------------------------------------------------------


class RiskStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    INVALID_PROFILE = "INVALID_PROFILE"
    LOCKED = "LOCKED"
    SIZE_UNVERIFIED = "SIZE_UNVERIFIED"
    CLEAR = "CLEAR"
    WITHIN_LIMITS = "WITHIN_LIMITS"
    UNAVAILABLE = "UNAVAILABLE"


class RiskProfileName(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    STANDARD = "STANDARD"
    AGGRESSIVE = "AGGRESSIVE"
    CUSTOM = "CUSTOM"


class RiskLock(StrEnum):
    ACCOUNT_STATE_STALE = "ACCOUNT_STATE_STALE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    WEEKLY_LOSS_LIMIT = "WEEKLY_LOSS_LIMIT"
    MAX_OPEN_RISK = "MAX_OPEN_RISK"
    MAX_POSITIONS = "MAX_POSITIONS"
    MAX_TRADES_PER_DAY = "MAX_TRADES_PER_DAY"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    PROP_DAILY_DRAWDOWN = "PROP_DAILY_DRAWDOWN"
    PROP_TOTAL_DRAWDOWN = "PROP_TOTAL_DRAWDOWN"
    VOLATILITY = "VOLATILITY"
    INVALID_STOP = "INVALID_STOP"
    ADDING_TO_LOSER = "ADDING_TO_LOSER"
    UNSAFE_SPREAD = "UNSAFE_SPREAD"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    BELOW_MIN_VOLUME = "BELOW_MIN_VOLUME"


class RiskWarning(StrEnum):
    RISK_REDUCED_BY_LIMITS = "RISK_REDUCED_BY_LIMITS"
    CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"
    USER_SUPPLIED_SPEC = "USER_SUPPLIED_SPEC"
    SPEC_INCONSISTENT = "SPEC_INCONSISTENT"
    CONVERSION_UNAVAILABLE = "CONVERSION_UNAVAILABLE"
    LEVERAGE_NOT_SET = "LEVERAGE_NOT_SET"
    SPREAD_UNKNOWN = "SPREAD_UNKNOWN"
    VOLATILITY_UNAVAILABLE = "VOLATILITY_UNAVAILABLE"
    VOLATILITY_NOT_CHECKED = "VOLATILITY_NOT_CHECKED"
    ACCOUNT_STATE_NOT_PROVIDED = "ACCOUNT_STATE_NOT_PROVIDED"
    NEWS_NOT_EVALUATED = "NEWS_NOT_EVALUATED"


# --- Phase 11: alerts ------------------------------------------------------------------------------


class AlertCategory(StrEnum):
    INFORMATIONAL = "INFORMATIONAL"
    WATCH = "WATCH"
    SETUP = "SETUP"
    ENTRY = "ENTRY"
    RISK = "RISK"
    MANAGEMENT = "MANAGEMENT"
    CRITICAL = "CRITICAL"


class AlertPriority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertType(StrEnum):
    LIQUIDITY_APPROACHING = "LIQUIDITY_APPROACHING"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    LIQUIDITY_RUN = "LIQUIDITY_RUN"
    EQUAL_LEVEL_CREATED = "EQUAL_LEVEL_CREATED"
    KEY_LEVEL_INTERACTION = "KEY_LEVEL_INTERACTION"
    SESSION_LIQUIDITY_EVENT = "SESSION_LIQUIDITY_EVENT"
    STRUCTURE_BREAK = "STRUCTURE_BREAK"
    DISPLACEMENT = "DISPLACEMENT"
    NO_WICK_EVENT = "NO_WICK_EVENT"
    NO_WICK_REBALANCE = "NO_WICK_REBALANCE"
    FVG_CREATED = "FVG_CREATED"
    FVG_TOUCHED = "FVG_TOUCHED"
    FVG_INVALIDATED = "FVG_INVALIDATED"
    SETUP_ARMED = "SETUP_ARMED"
    ENTRY_ZONE_APPROACHING = "ENTRY_ZONE_APPROACHING"
    ENTRY_ZONE_TOUCHED = "ENTRY_ZONE_TOUCHED"
    ENTRY_CONFIRMED_PENDING_GATES = "ENTRY_CONFIRMED_PENDING_GATES"
    ENTRY_MISSED = "ENTRY_MISSED"
    EARLY_ENTRY_WARNING = "EARLY_ENTRY_WARNING"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    SETUP_EXPIRED = "SETUP_EXPIRED"
    SESSION_CHANGE = "SESSION_CHANGE"
    RISK_LOCK = "RISK_LOCK"
    RISK_LOCK_CLEARED = "RISK_LOCK_CLEARED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_RESTORED = "DATA_RESTORED"
    MONITOR_FAILURE = "MONITOR_FAILURE"
    READY = "READY"
    NEWS_COUNTDOWN = "NEWS_COUNTDOWN"
    NEWS_BLACKOUT = "NEWS_BLACKOUT"
    NEWS_CLEARED = "NEWS_CLEARED"
    MACRO_SHIFT = "MACRO_SHIFT"


class ReadyWatchState(StrEnum):
    WAITING = "WAITING"
    GATES_PENDING = "GATES_PENDING"
    UNAVAILABLE = "UNAVAILABLE"
    FIRED = "FIRED"


class ConditionStatus(StrEnum):
    MET = "MET"
    MISSING = "MISSING"
    NOT_EVALUATED = "NOT_EVALUATED"


# --- Phase 12: AI assistant ------------------------------------------------------------------------


class AssistantIntent(StrEnum):
    ANALYZE = "ANALYZE"
    WHY_WAITING = "WHY_WAITING"
    WHY_NOT_DIRECTION = "WHY_NOT_DIRECTION"
    LIQUIDITY = "LIQUIDITY"
    DOL = "DOL"
    SCORE_CHANGE = "SCORE_CHANGE"
    NO_WICK = "NO_WICK"
    INVALIDATION = "INVALIDATION"
    TRADE_PLAN = "TRADE_PLAN"
    RISK = "RISK"
    SESSION = "SESSION"
    A_PLUS_SETUPS = "A_PLUS_SETUPS"
    COMPARE = "COMPARE"
    BACKTEST = "BACKTEST"
    MACRO = "MACRO"
    CREATE_ALERT = "CREATE_ALERT"
    EXPLAIN_TERM = "EXPLAIN_TERM"
    HELP = "HELP"
    NEWS = "NEWS"
    JOURNAL = "JOURNAL"


class EducationLevel(StrEnum):
    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"
    PROFESSIONAL = "PROFESSIONAL"


class AssistantProvider(StrEnum):
    DETERMINISTIC = "DETERMINISTIC"
    ANTHROPIC = "ANTHROPIC"


class GuardStatus(StrEnum):
    PASSED = "PASSED"
    FALLBACK = "FALLBACK"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ToolStatus(StrEnum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"


# --- Phase 13: economic calendar & news gate -------------------------------------------------------


class NewsState(StrEnum):
    CLEAR = "CLEAR"
    CAUTION = "CAUTION"
    BLACKOUT = "BLACKOUT"
    POST_NEWS_WAIT = "POST_NEWS_WAIT"
    NORMALIZED = "NORMALIZED"
    UNAVAILABLE = "UNAVAILABLE"


class EventImportance(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class EventStatus(StrEnum):
    UPCOMING = "UPCOMING"
    IMMINENT = "IMMINENT"
    RELEASED = "RELEASED"
    REVISED = "REVISED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DELAYED = "DELAYED"


# --- Phase 14: basic macro ----------------------------------------------------------------------------


class MacroState(StrEnum):
    STRONGLY_SUPPORTIVE = "STRONGLY_SUPPORTIVE"
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    CONFLICT = "CONFLICT"
    STRONG_CONFLICT = "STRONG_CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"


class MacroBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"


class MacroSeriesId(StrEnum):
    DXY = "DXY"
    US2Y = "US2Y"
    US10Y = "US10Y"
    US10Y_REAL = "US10Y_REAL"
    VIX = "VIX"


class SeriesDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class CorrelationRegime(StrEnum):
    ALIGNED = "ALIGNED"
    WEAK = "WEAK"
    INVERTED = "INVERTED"
    UNAVAILABLE = "UNAVAILABLE"


# --- Phase 15: journal ---------------------------------------------------------------------------------


class JournalEntryKind(StrEnum):
    TRADE = "TRADE"
    NO_TRADE = "NO_TRADE"
    MISSED_ENTRY = "MISSED_ENTRY"


class JournalStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TradeResult(StrEnum):
    FULL_WIN = "FULL_WIN"
    PARTIAL_WIN = "PARTIAL_WIN"
    BREAK_EVEN = "BREAK_EVEN"
    FULL_LOSS = "FULL_LOSS"
    PARTIAL_LOSS = "PARTIAL_LOSS"
    MANUAL_EXIT = "MANUAL_EXIT"
    INVALIDATION_EXIT = "INVALIDATION_EXIT"
    NEWS_EXIT = "NEWS_EXIT"
    TRAILING_STOP_EXIT = "TRAILING_STOP_EXIT"
    MISSED_ENTRY = "MISSED_ENTRY"
    NO_TRADE = "NO_TRADE"


class ExitReason(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    BREAK_EVEN_STOP = "BREAK_EVEN_STOP"
    MANUAL = "MANUAL"
    INVALIDATION = "INVALIDATION"
    NEWS = "NEWS"
    TRAILING_STOP = "TRAILING_STOP"


class ProcessClassification(StrEnum):
    VALID_WIN = "VALID_WIN"
    BAD_PROCESS_WIN = "BAD_PROCESS_WIN"
    VALID_LOSS = "VALID_LOSS"
    PROCESS_ERROR = "PROCESS_ERROR"


class RuleViolation(StrEnum):
    TRADED_ON_UNAVAILABLE_DECISION = "TRADED_ON_UNAVAILABLE_DECISION"
    TRADED_DURING_NEWS_BLACKOUT = "TRADED_DURING_NEWS_BLACKOUT"
    TRADED_WHILE_RISK_LOCKED = "TRADED_WHILE_RISK_LOCKED"
    NO_CONFIRMED_PLAN = "NO_CONFIRMED_PLAN"
    AGAINST_PLAN_DIRECTION = "AGAINST_PLAN_DIRECTION"
    CHASED_ENTRY = "CHASED_ENTRY"
    RISK_ABOVE_LIMIT = "RISK_ABOVE_LIMIT"
    MOVED_STOP = "MOVED_STOP"
    NO_STOP_PLACED = "NO_STOP_PLACED"
    OVERSIZED_POSITION = "OVERSIZED_POSITION"
    EXITED_EARLY_WITHOUT_REASON = "EXITED_EARLY_WITHOUT_REASON"
    REVENGE_TRADE = "REVENGE_TRADE"
    OTHER = "OTHER"


class SnapshotIntegrity(StrEnum):
    VERIFIED = "VERIFIED"
    TAMPERED = "TAMPERED"


class SnapshotTiming(StrEnum):
    PRE_ENTRY = "PRE_ENTRY"
    POST_ENTRY = "POST_ENTRY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExtremeSource(StrEnum):
    MANUAL = "MANUAL"
    CANDLES = "CANDLES"
    UNAVAILABLE = "UNAVAILABLE"


# --- Phase 16: paper trading --------------------------------------------------------------------------


class PaperEntryType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class PaperSource(StrEnum):
    MANUAL = "MANUAL"
    ENGINE_PLAN = "ENGINE_PLAN"


class PaperStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class PaperEventType(StrEnum):
    CREATED = "CREATED"
    FILLED = "FILLED"
    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    CLOSED_MANUALLY = "CLOSED_MANUALLY"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# --- Phase 17: analytics -------------------------------------------------------------------------------


class SampleSizeLabel(StrEnum):
    INSUFFICIENT = "INSUFFICIENT"
    LIMITED = "LIMITED"
    MODERATE = "MODERATE"
    STRONGER_EVIDENCE = "STRONGER_EVIDENCE"


class AnalyticsSource(StrEnum):
    JOURNAL = "JOURNAL"
    PAPER = "PAPER"


# --- Phase 18: backtesting -----------------------------------------------------------------------------


class BacktestStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BacktestTradeStatus(StrEnum):
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    SKIPPED_OVERLAP = "SKIPPED_OVERLAP"
    OPEN_AT_END = "OPEN_AT_END"


# --- Phase 19: replay ----------------------------------------------------------------------------------


class ReplayMode(StrEnum):
    MANUAL = "MANUAL"
    GUIDED = "GUIDED"
    BLIND = "BLIND"
    QUIZ = "QUIZ"


class QuizQuestionType(StrEnum):
    NEXT_BARS_DIRECTION = "NEXT_BARS_DIRECTION"
    LEVEL_FIRST = "LEVEL_FIRST"
    SETUP_PROGRESS = "SETUP_PROGRESS"


class QuizGrade(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    VOID = "VOID"


CONTRACT_ENUMS: dict[str, type[StrEnum]] = {
    "Verdict": Verdict,
    "DecisionConfidence": DecisionConfidence,
    "DataQuality": DataQuality,
    "Timeframe": Timeframe,
    "AssetClass": AssetClass,
    "MarketStatus": MarketStatus,
    "ProviderHealthStatus": ProviderHealthStatus,
    "ResearchStatus": ResearchStatus,
    "IssueSeverity": IssueSeverity,
    "ValidationIssueCode": ValidationIssueCode,
    "PositionSizeStatus": PositionSizeStatus,
    "Blocker": Blocker,
    "StructureState": StructureState,
    "TrendDirection": TrendDirection,
    "Direction": Direction,
    "StructureLevel": StructureLevel,
    "SwingKind": SwingKind,
    "SwingLabel": SwingLabel,
    "StructureEventType": StructureEventType,
    "StructureEventStatus": StructureEventStatus,
    "BreakConfirmation": BreakConfirmation,
    "QualifierStatus": QualifierStatus,
    "MtfAlignment": MtfAlignment,
    "HtfBias": HtfBias,
    "AnalysisIneligibility": AnalysisIneligibility,
    "LiquiditySide": LiquiditySide,
    "LiquidityScope": LiquidityScope,
    "LiquidityPoolType": LiquidityPoolType,
    "LiquidityState": LiquidityState,
    "LiquidityEventType": LiquidityEventType,
    "DolConfidence": DolConfidence,
    "DisplacementGrade": DisplacementGrade,
    "PdArrayType": PdArrayType,
    "PdArrayState": PdArrayState,
    "IfvgStatus": IfvgStatus,
    "PdArrayEventType": PdArrayEventType,
    "NoWickClassification": NoWickClassification,
    "NoWickStrength": NoWickStrength,
    "NoWickZoneState": NoWickZoneState,
    "NoWickZoneEventType": NoWickZoneEventType,
    "NoWickContextFactor": NoWickContextFactor,
    "ScoreComponentStatus": ScoreComponentStatus,
    "SessionName": SessionName,
    "KillZone": KillZone,
    "SessionInstanceState": SessionInstanceState,
    "AsianRangeState": AsianRangeState,
    "SessionQuality": SessionQuality,
    "ExpansionState": ExpansionState,
    "JudasStatus": JudasStatus,
    "SetupState": SetupState,
    "SetupType": SetupType,
    "SetupStep": SetupStep,
    "SetupStepStatus": SetupStepStatus,
    "Po3Phase": Po3Phase,
    "EntryModel": EntryModel,
    "EntryMode": EntryMode,
    "EntryWarning": EntryWarning,
    "ScoreFactor": ScoreFactor,
    "SetupGrade": SetupGrade,
    "EvaluationOutcome": EvaluationOutcome,
    "RiskStatus": RiskStatus,
    "RiskProfileName": RiskProfileName,
    "RiskLock": RiskLock,
    "RiskWarning": RiskWarning,
    "AlertCategory": AlertCategory,
    "AlertPriority": AlertPriority,
    "AlertType": AlertType,
    "ReadyWatchState": ReadyWatchState,
    "ConditionStatus": ConditionStatus,
    "AssistantIntent": AssistantIntent,
    "EducationLevel": EducationLevel,
    "AssistantProvider": AssistantProvider,
    "GuardStatus": GuardStatus,
    "ToolStatus": ToolStatus,
    "NewsState": NewsState,
    "EventImportance": EventImportance,
    "EventStatus": EventStatus,
    "MacroState": MacroState,
    "MacroBias": MacroBias,
    "MacroSeriesId": MacroSeriesId,
    "SeriesDirection": SeriesDirection,
    "CorrelationRegime": CorrelationRegime,
    "JournalEntryKind": JournalEntryKind,
    "JournalStatus": JournalStatus,
    "TradeResult": TradeResult,
    "ExitReason": ExitReason,
    "ProcessClassification": ProcessClassification,
    "RuleViolation": RuleViolation,
    "SnapshotIntegrity": SnapshotIntegrity,
    "SnapshotTiming": SnapshotTiming,
    "ExtremeSource": ExtremeSource,
    "PaperEntryType": PaperEntryType,
    "PaperSource": PaperSource,
    "PaperStatus": PaperStatus,
    "PaperEventType": PaperEventType,
    "SampleSizeLabel": SampleSizeLabel,
    "AnalyticsSource": AnalyticsSource,
    "BacktestStatus": BacktestStatus,
    "BacktestTradeStatus": BacktestTradeStatus,
    "ReplayMode": ReplayMode,
    "QuizQuestionType": QuizQuestionType,
    "QuizGrade": QuizGrade,
}
