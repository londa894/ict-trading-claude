/**
 * Shared API contract types. Enum value arrays are contract-tested against
 * packages/strategy-spec/enums.json (the same file the Python API is tested against).
 */

export const VERDICTS = ["LONG", "SHORT", "WAIT", "NO_TRADE", "UNAVAILABLE"] as const;
export type Verdict = (typeof VERDICTS)[number];

export const DECISION_CONFIDENCES = ["LOW", "MODERATE", "HIGH", "VERY_HIGH"] as const;
export type DecisionConfidence = (typeof DECISION_CONFIDENCES)[number];

export const DATA_QUALITIES = ["LIVE", "CURRENT", "DELAYED", "STALE", "DISCONNECTED", "INVALID"] as const;
export type DataQuality = (typeof DATA_QUALITIES)[number];

export const TIMEFRAMES = ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

export const ASSET_CLASSES = ["METAL", "FX_MAJOR", "FX_CROSS"] as const;
export type AssetClass = (typeof ASSET_CLASSES)[number];

export const MARKET_STATUSES = ["OPEN", "CLOSED", "DAILY_BREAK", "UNKNOWN"] as const;
export type MarketStatus = (typeof MARKET_STATUSES)[number];

export const PROVIDER_HEALTH_STATUSES = ["HEALTHY", "DEGRADED", "DOWN", "UNKNOWN"] as const;
export type ProviderHealthStatus = (typeof PROVIDER_HEALTH_STATUSES)[number];

export const RESEARCH_STATUSES = [
  "APPROVED_FOR_RESEARCH",
  "APPROVED_WITH_WARNINGS",
  "NOT_APPROVED",
] as const;
export type ResearchStatus = (typeof RESEARCH_STATUSES)[number];

export const ISSUE_SEVERITIES = ["WARNING", "ERROR"] as const;
export type IssueSeverity = (typeof ISSUE_SEVERITIES)[number];

export const VALIDATION_ISSUE_CODES = [
  "NON_UTC_TIMESTAMP",
  "MISALIGNED_OPEN_TIME",
  "NON_POSITIVE_PRICE",
  "NON_FINITE_VALUE",
  "IMPOSSIBLE_OHLC",
  "ZERO_RANGE",
  "NEGATIVE_VOLUME",
  "DUPLICATE_IDENTICAL",
  "DUPLICATE_CONFLICT",
  "OUT_OF_ORDER",
  "MISSING_BARS",
  "SUSPECT_BAD_TICK",
  "CROSSED_QUOTE",
  "WIDE_SPREAD",
  "PROVIDER_DISAGREEMENT",
  "SYMBOL_MISMATCH",
  "TIMEFRAME_MISMATCH",
  "EMPTY_SERIES",
  "INCOMPLETE_BUCKET",
] as const;
export type ValidationIssueCode = (typeof VALIDATION_ISSUE_CODES)[number];

export const POSITION_SIZE_STATUSES = ["VERIFIED", "SIZED_FROM_USER_SPEC", "POSITION_SIZE_UNVERIFIED"] as const;
export type PositionSizeStatus = (typeof POSITION_SIZE_STATUSES)[number];

export const BLOCKERS = [
  "SYSTEM_INTEGRITY_FAILURE",
  "UNKNOWN_SYMBOL",
  "PROVIDER_UNAVAILABLE",
  "NO_DATA",
  "DATA_INVALID",
  "DATA_STALE",
  "DATA_DISCONNECTED",
  "DATA_DELAYED",
  "DATA_GAP",
  "DATA_SUSPECT_BAD_TICK",
  "DATA_SYNTHETIC",
  "MARKET_CLOSED",
  "INSTRUMENT_SPEC_MISSING",
  "DOL_UNCLEAR",
  "ANALYSIS_GATES_NOT_IMPLEMENTED",
  "ENTRY_MISSED",
  "INSUFFICIENT_RR",
  "RISK_GATE_MISSING",
  "NEWS_GATE_MISSING",
  "RISK_PROFILE_MISSING",
  "RISK_PROFILE_INVALID",
  "RISK_LOCKED",
  "UNSAFE_SPREAD",
  "POSITION_SIZE_UNVERIFIED",
  "MARKET_NOT_VALIDATED",
  "NEWS_BLACKOUT",
  "NEWS_POST_WAIT",
  "NEWS_DATA_UNAVAILABLE",
  "NEWS_DATA_SYNTHETIC"
] as const;
export type Blocker = (typeof BLOCKERS)[number];

/** Keyed exactly like strategy-spec/enums.json. */
export const CONTRACT_ENUMS = {
  Verdict: VERDICTS,
  DecisionConfidence: DECISION_CONFIDENCES,
  DataQuality: DATA_QUALITIES,
  Timeframe: TIMEFRAMES,
  AssetClass: ASSET_CLASSES,
  MarketStatus: MARKET_STATUSES,
  ProviderHealthStatus: PROVIDER_HEALTH_STATUSES,
  ResearchStatus: RESEARCH_STATUSES,
  IssueSeverity: ISSUE_SEVERITIES,
  ValidationIssueCode: VALIDATION_ISSUE_CODES,
  PositionSizeStatus: POSITION_SIZE_STATUSES,
  Blocker: BLOCKERS,
} as const;

export type VerdictAuthority = "FAIL_SAFE_ONLY" | "FULL";

export interface ValidationIssue {
  code: ValidationIssueCode;
  severity: IssueSeverity;
  message: string;
  at: string | null;
  count: number;
}

export interface PriceZone {
  low: number;
  high: number;
}

/** Master Decision Object (spec STEP 18). Every surface must render this same object. */
export interface MasterDecision {
  symbol: string;
  verdict: Verdict;
  direction: "LONG" | "SHORT" | null;
  setupType: string | null;
  setupState: string;
  setupGrade: string | null;
  setupScore: number | null;
  decisionConfidence: DecisionConfidence;
  htfBias: string;
  primaryDol: string | null;
  secondaryDol: string | null;
  liquidityEvent: string | null;
  structureEvent: string | null;
  displacement: string | null;
  pdArray: Record<string, unknown> | null;
  noWickState: NoWickDecisionState | null;
  sessionState: SessionDecisionState | null;
  macroState: Record<string, unknown> | null;
  newsState: Record<string, unknown> | null;
  entryZone: PriceZone | null;
  preferredEntry: number | null;
  stop: number | null;
  tp1: number | null;
  tp2: number | null;
  tp3: number | null;
  rr: number | null;
  riskStatus: string;
  blockers: Blocker[];
  nextRequiredEvent: string | null;
  invalidation: string | null;
  dataQuality: DataQuality;
  strategyVersion: string;
  updatedAt: string;
}

export interface DataReport {
  provider: string;
  isSynthetic: boolean;
  timeframe: Timeframe;
  candleCount: number;
  latestClosedOpenTime: string | null;
  quality: DataQuality;
  marketStatus: MarketStatus;
  positionSizeStatus: PositionSizeStatus | null;
  issues: ValidationIssue[];
  providerError: string | null;
}

export interface MarketStateResponse {
  decision: MasterDecision;
  data: DataReport;
}

export interface SystemStatus {
  strategyVersion: string;
  phase: number;
  phaseName: string;
  verdictAuthority: VerdictAuthority;
  enabledEngines: string[];
  environment: string;
  provider: { name: string; isSynthetic: boolean; credentialsConfigured: boolean };
  brokerConnection: "NONE";
  orderExecution: "NONE";
  marketDataSource: string;
  chartRenderer: string;
  primaryMarket: string;
}

/** Timeframes the chart shell serves. H4/D1 are New York 17:00 buckets derived from H1; W1 is the
 * Sunday-17:00 New York trading week derived H1 -> D1 -> W1. M1/M30 are native provider resolutions. */
export const CHART_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"] as const satisfies readonly Timeframe[];
export type ChartTimeframe = (typeof CHART_TIMEFRAMES)[number];

export interface ChartCandle {
  /** Bucket open time, ISO-8601 UTC. */
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  isClosed: boolean;
}

export interface ChartSeriesResponse {
  symbol: string;
  timeframe: Timeframe;
  sourceTimeframe: Timeframe;
  provider: string;
  isSynthetic: boolean;
  quality: DataQuality;
  marketStatus: MarketStatus;
  /** Always empty when quality is INVALID or DISCONNECTED. */
  candles: ChartCandle[];
  issues: ValidationIssue[];
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 2: market structure ------------------------------------------------------------------

export const STRUCTURE_STATES = ["BULLISH", "BEARISH", "RANGING", "TRANSITIONING", "UNCLEAR"] as const;
export type StructureState = (typeof STRUCTURE_STATES)[number];
export const TREND_DIRECTIONS = ["BULLISH", "BEARISH", "NONE"] as const;
export type TrendDirection = (typeof TREND_DIRECTIONS)[number];
export const DIRECTIONS = ["BULLISH", "BEARISH"] as const;
export type Direction = (typeof DIRECTIONS)[number];
export const STRUCTURE_LEVELS = ["INTERNAL", "EXTERNAL"] as const;
export type StructureLevel = (typeof STRUCTURE_LEVELS)[number];
export const SWING_KINDS = ["HIGH", "LOW"] as const;
export type SwingKind = (typeof SWING_KINDS)[number];
export const SWING_LABELS = ["HH", "HL", "LH", "LL", "EH", "EL", "NONE"] as const;
export type SwingLabel = (typeof SWING_LABELS)[number];
export const STRUCTURE_EVENT_TYPES = ["BOS", "CHOCH", "MSS"] as const;
export type StructureEventType = (typeof STRUCTURE_EVENT_TYPES)[number];
export const STRUCTURE_EVENT_STATUSES = ["CONFIRMED", "POTENTIAL"] as const;
export type StructureEventStatus = (typeof STRUCTURE_EVENT_STATUSES)[number];
export const BREAK_CONFIRMATIONS = ["CANDLE_CLOSE", "WICK_ONLY"] as const;
export type BreakConfirmation = (typeof BREAK_CONFIRMATIONS)[number];
export const QUALIFIER_STATUSES = ["NOT_EVALUATED", "PRESENT", "ABSENT"] as const;
export type QualifierStatus = (typeof QUALIFIER_STATUSES)[number];
export const MTF_ALIGNMENTS = ["ALIGNED_BULLISH", "ALIGNED_BEARISH", "MIXED", "UNCLEAR"] as const;
export type MtfAlignment = (typeof MTF_ALIGNMENTS)[number];
export const HTF_BIASES = ["BULLISH", "BEARISH", "RANGING", "TRANSITIONING", "MIXED", "UNCLEAR", "UNKNOWN"] as const;
export type HtfBias = (typeof HTF_BIASES)[number];
export const ANALYSIS_INELIGIBILITIES = [
  "DATA_SYNTHETIC",
  "DATA_STALE",
  "DATA_INVALID",
  "DATA_DISCONNECTED",
  "INSUFFICIENT_CANDLES",
  "KEY_LEVELS_UNAVAILABLE",
  "LIQUIDITY_ANALYSIS_FAILED",
  "PD_ARRAY_ANALYSIS_FAILED",
  "NO_WICK_ANALYSIS_FAILED",
  "SESSION_LEVELS_UNAVAILABLE",
  "SESSION_ANALYSIS_FAILED",
  "SETUP_BIAS_UNAVAILABLE",
  "SETUP_ANALYSIS_FAILED",
  "EVALUATION_FAILED",
] as const;
export type AnalysisIneligibility = (typeof ANALYSIS_INELIGIBILITIES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 2 additions). */
export const STRUCTURE_CONTRACT_ENUMS = {
  StructureState: STRUCTURE_STATES,
  TrendDirection: TREND_DIRECTIONS,
  Direction: DIRECTIONS,
  StructureLevel: STRUCTURE_LEVELS,
  SwingKind: SWING_KINDS,
  SwingLabel: SWING_LABELS,
  StructureEventType: STRUCTURE_EVENT_TYPES,
  StructureEventStatus: STRUCTURE_EVENT_STATUSES,
  BreakConfirmation: BREAK_CONFIRMATIONS,
  QualifierStatus: QUALIFIER_STATUSES,
  MtfAlignment: MTF_ALIGNMENTS,
  HtfBias: HTF_BIASES,
  AnalysisIneligibility: ANALYSIS_INELIGIBILITIES,
} as const;

export interface Swing {
  id: string;
  level: StructureLevel;
  kind: SwingKind;
  label: SwingLabel;
  price: number;
  /** Pivot candle open time (ISO UTC). */
  time: string;
  /** Close time of the confirming candle; the swing is unknown before this. */
  confirmedAt: string;
  brokenAt: string | null;
  brokenBy: string | null;
}

export interface StructureEvent {
  id: string;
  level: StructureLevel;
  type: StructureEventType;
  direction: Direction;
  status: StructureEventStatus;
  confirmation: BreakConfirmation;
  /** The broken swing level. */
  price: number;
  /** Open time of the breaking candle. */
  time: string;
  brokenSwingId: string;
  brokenSwingTime: string;
  trendBefore: TrendDirection;
  ambiguous: boolean;
  liquidityQualifier: QualifierStatus;
  displacementQualifier: QualifierStatus;
}

export interface LevelStructure {
  level: StructureLevel;
  pivotLength: number;
  state: StructureState;
  trend: TrendDirection;
  swings: Swing[];
  events: StructureEvent[];
  protectedHigh: Swing | null;
  protectedLow: Swing | null;
  barsSinceLastBreak: number | null;
}

export interface StructureAnalysis {
  symbol: string;
  timeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  /** Null when data is INVALID or DISCONNECTED. */
  internal: LevelStructure | null;
  external: LevelStructure | null;
  /** Chronological log of both levels. */
  events: StructureEvent[];
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

export interface TimeframeStructureState {
  timeframe: Timeframe;
  state: StructureState | null;
  trend: TrendDirection | null;
  quality: DataQuality;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
}

export interface MtfStructureResponse {
  symbol: string;
  alignment: MtfAlignment;
  htfBias: HtfBias;
  eligibleForDecision: boolean;
  timeframes: TimeframeStructureState[];
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 3: liquidity -------------------------------------------------------------------------

export const LIQUIDITY_SIDES = ["BSL", "SSL"] as const;
export type LiquiditySide = (typeof LIQUIDITY_SIDES)[number];
export const LIQUIDITY_SCOPES = ["INTERNAL", "EXTERNAL"] as const;
export type LiquidityScope = (typeof LIQUIDITY_SCOPES)[number];
export const LIQUIDITY_POOL_TYPES = ["SWING_HIGH", "SWING_LOW", "EQH", "EQL", "PDH", "PDL", "PWH", "PWL", "ASIA_HIGH", "ASIA_LOW", "LONDON_HIGH", "LONDON_LOW", "NY_AM_HIGH", "NY_AM_LOW", "NY_PM_HIGH", "NY_PM_LOW"] as const;
export type LiquidityPoolType = (typeof LIQUIDITY_POOL_TYPES)[number];
export const LIQUIDITY_STATES = ["FRESH", "APPROACHING", "TOUCHED", "SWEPT", "RUN", "BROKEN", "RECLAIMED"] as const;
export type LiquidityState = (typeof LIQUIDITY_STATES)[number];
export const LIQUIDITY_EVENT_TYPES = ["TOUCH", "SWEEP", "BREAK", "RUN", "RECLAIM", "SWEEP_FAILED"] as const;
export type LiquidityEventType = (typeof LIQUIDITY_EVENT_TYPES)[number];
export const DOL_CONFIDENCES = ["HIGH", "MODERATE", "LOW", "UNCLEAR"] as const;
export type DolConfidence = (typeof DOL_CONFIDENCES)[number];
export const LIQUIDITY_ELIGIBILITIES = ["EXTERNAL_TREND_ALIGNED", "INTERNAL_ONLY", "UNRESTRICTED", "REVERSAL_EXEMPT"] as const;
export type LiquidityEligibility = (typeof LIQUIDITY_ELIGIBILITIES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 3 additions). */
export const LIQUIDITY_CONTRACT_ENUMS = {
  LiquiditySide: LIQUIDITY_SIDES,
  LiquidityScope: LIQUIDITY_SCOPES,
  LiquidityPoolType: LIQUIDITY_POOL_TYPES,
  LiquidityState: LIQUIDITY_STATES,
  LiquidityEventType: LIQUIDITY_EVENT_TYPES,
  DolConfidence: DOL_CONFIDENCES,
  LiquidityEligibility: LIQUIDITY_ELIGIBILITIES,
} as const;

export interface LiquidityPool {
  id: string;
  type: LiquidityPoolType;
  side: LiquiditySide;
  scope: LiquidityScope;
  label: string;
  price: number;
  formedAt: string;
  /** The pool does not exist before this instant (no lookahead). */
  knownAt: string;
  sourceTimes: string[];
  state: LiquidityState;
  touches: number;
  stateChangedAt: string | null;
  taken: boolean;
  distanceAtr: number | null;
  /** 0-100 ranking for untaken pools only. Not a probability. */
  magnetScore: number | null;
}

export interface LiquidityEvent {
  id: string;
  poolId: string;
  poolType: LiquidityPoolType;
  side: LiquiditySide;
  type: LiquidityEventType;
  price: number;
  time: string;
  extreme: number;
  close: number;
}

export interface DolTarget {
  poolId: string;
  type: LiquidityPoolType;
  side: LiquiditySide;
  label: string;
  price: number;
  magnetScore: number;
  distanceAtr: number;
}

export interface DolSelection {
  primary: DolTarget | null;
  secondary: DolTarget | null;
  confidence: DolConfidence;
  /** Which trend-aligned-targeting regime produced this selection (reversal spec section 5). */
  eligibility: LiquidityEligibility;
  margin: number | null;
  reason: string;
}

export interface LiquidityAnalysis {
  symbol: string;
  timeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  keyLevelsAvailable: boolean;
  pools: LiquidityPool[];
  events: LiquidityEvent[];
  dol: DolSelection | null;
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 4: displacement + FVG/IFVG -----------------------------------------------------------

export const DISPLACEMENT_GRADES = ["WEAK", "MODERATE", "STRONG", "EXCEPTIONAL"] as const;
export type DisplacementGrade = (typeof DISPLACEMENT_GRADES)[number];
export const PD_ARRAY_TYPES = ["FVG", "IFVG", "IMR", "REVERSAL_FVG"] as const;
export type PdArrayType = (typeof PD_ARRAY_TYPES)[number];
export const PD_ARRAY_STATES = ["FRESH", "TOUCHED", "PARTIAL", "HALF", "FULL", "INVALIDATED"] as const;
export type PdArrayState = (typeof PD_ARRAY_STATES)[number];
export const IFVG_STATUSES = ["POTENTIAL_IFVG", "CONFIRMED_IFVG", "FAILED_IFVG"] as const;
export type IfvgStatus = (typeof IFVG_STATUSES)[number];
export const PD_ARRAY_EVENT_TYPES = [
  "CREATED",
  "TOUCHED",
  "PARTIAL_FILL",
  "HALF_FILL",
  "FULL_FILL",
  "INVALIDATED",
  "IFVG_POTENTIAL",
  "IFVG_CONFIRMED",
  "IFVG_FAILED",
  "IMR_CREATED",
  "REVERSAL_FVG_CREATED",
] as const;
export type PdArrayEventType = (typeof PD_ARRAY_EVENT_TYPES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 4 additions). */
export const PD_ARRAY_CONTRACT_ENUMS = {
  DisplacementGrade: DISPLACEMENT_GRADES,
  PdArrayType: PD_ARRAY_TYPES,
  PdArrayState: PD_ARRAY_STATES,
  IfvgStatus: IFVG_STATUSES,
  PdArrayEventType: PD_ARRAY_EVENT_TYPES,
} as const;

export interface DisplacementEvent {
  id: string;
  direction: Direction;
  grade: DisplacementGrade;
  /** Leg body move in ATRs, measured with the ATR from before the leg started. */
  magnitudeAtr: number;
  legStart: string;
  time: string;
  candleCount: number;
  avgBodyPct: number;
}

export interface PdArrayZone {
  id: string;
  type: PdArrayType;
  direction: Direction;
  top: number;
  bottom: number;
  /** Consequent encroachment (50%). */
  midpoint: number;
  sizeAtr: number;
  sourceTimes: string[];
  createdAt: string;
  knownAt: string;
  state: PdArrayState;
  fillPct: number;
  ifvgStatus: IfvgStatus | null;
  parentId: string | null;
  displacementGrade: DisplacementGrade | null;
  stateChangedAt: string | null;
  invalidatedAt: string | null;
  ageBars: number;
  active: boolean;
  /** 0-100 ranking for active zones only. Not a probability. */
  qualityScore: number | null;
}

export interface PdArrayEvent {
  id: string;
  zoneId: string;
  zoneType: PdArrayType;
  direction: Direction;
  type: PdArrayEventType;
  time: string;
  price: number;
  detail: string;
}

export interface PdArrayAnalysis {
  symbol: string;
  timeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  displacements: DisplacementEvent[];
  zones: PdArrayZone[];
  events: PdArrayEvent[];
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 5: No Wick Architecture V1 -----------------------------------------------------------

export const NO_WICK_CLASSIFICATIONS = [
  "TRUE_BULLISH_MARUBOZU",
  "TRUE_BEARISH_MARUBOZU",
  "NEAR_BULLISH_MARUBOZU",
  "NEAR_BEARISH_MARUBOZU",
  "BULLISH_NO_LOWER_WICK",
  "BEARISH_NO_UPPER_WICK",
  "BULLISH_NO_UPPER_WICK",
  "BEARISH_NO_LOWER_WICK",
  "NO_ORIGIN_SIDE_WICK",
  "NO_DESTINATION_SIDE_WICK",
  "NEWS_DRIVEN_NO_WICK",
  "INSIGNIFICANT_NO_WICK",
] as const;
export type NoWickClassification = (typeof NO_WICK_CLASSIFICATIONS)[number];
export const NO_WICK_STRENGTHS = ["INSIGNIFICANT", "MEANINGFUL", "STRONG", "EXCEPTIONAL"] as const;
export type NoWickStrength = (typeof NO_WICK_STRENGTHS)[number];
export const NO_WICK_VARIANTS = ["LATE_CANDLE_FADE", "EARLY_CANDLE_CONTINUATION"] as const;
export type NoWickVariant = (typeof NO_WICK_VARIANTS)[number];
export const NO_WICK_ZONE_STATES = [
  "FRESH",
  "APPROACHING",
  "TOUCHED",
  "PARTIAL",
  "HALF_REBALANCED",
  "FULLY_REBALANCED",
  "REACTED",
  "FAILED",
  "INVALIDATED",
] as const;
export type NoWickZoneState = (typeof NO_WICK_ZONE_STATES)[number];
export const NO_WICK_ZONE_EVENT_TYPES = [
  "TOUCHED",
  "REBALANCE_25",
  "REBALANCE_50",
  "REBALANCE_75",
  "FULLY_REBALANCED",
  "REACTED",
  "FAILED",
  "INVALIDATED",
] as const;
export type NoWickZoneEventType = (typeof NO_WICK_ZONE_EVENT_TYPES)[number];
export const NO_WICK_CONTEXT_FACTORS = [
  "DISPLACEMENT",
  "STRUCTURE",
  "LIQUIDITY",
  "FVG",
  "TREND",
  "SESSION",
  "NEWS",
] as const;
export type NoWickContextFactor = (typeof NO_WICK_CONTEXT_FACTORS)[number];
export const SCORE_COMPONENT_STATUSES = ["EVALUATED", "NOT_EVALUATED"] as const;
export type ScoreComponentStatus = (typeof SCORE_COMPONENT_STATUSES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 5 additions). */
export const NO_WICK_CONTRACT_ENUMS = {
  NoWickClassification: NO_WICK_CLASSIFICATIONS,
  NoWickStrength: NO_WICK_STRENGTHS,
  NoWickVariant: NO_WICK_VARIANTS,
  NoWickZoneState: NO_WICK_ZONE_STATES,
  NoWickZoneEventType: NO_WICK_ZONE_EVENT_TYPES,
  NoWickContextFactor: NO_WICK_CONTEXT_FACTORS,
  ScoreComponentStatus: SCORE_COMPONENT_STATUSES,
} as const;

/** Candle-local features; ATR and medians come from prior candles only. Null when undefined. */
export interface CandleFeatures {
  time: string;
  range: number;
  body: number;
  upperWick: number;
  lowerWick: number;
  bodyPct: number | null;
  upperWickPct: number | null;
  lowerWickPct: number | null;
  bodyAtr: number | null;
  rangeAtr: number | null;
  bodyToMedian: number | null;
  rangeToMedian: number | null;
  closeLocationPct: number | null;
}

/** One ContextScore factor. NOT_EVALUATED means the source engine is missing, never "scored zero". */
export interface ScoreComponent {
  factor: NoWickContextFactor;
  status: ScoreComponentStatus;
  points: number;
  maxPoints: number;
  detail: string;
}

export interface NoWickEvent {
  id: string;
  direction: Direction;
  shape: NoWickClassification;
  /** Equals shape, or INSIGNIFICANT_NO_WICK when the body is too small. */
  classification: NoWickClassification;
  tags: NoWickClassification[];
  strength: NoWickStrength;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  bodyPct: number;
  upperWickPct: number;
  lowerWickPct: number;
  bodyAtr: number;
  closeLocationPct: number;
  insideBar: boolean;
  /** 0-100, candle only. Not a probability. */
  candleQualityScore: number;
  /** 0-100, facts known at the candle's close only. Not a probability. */
  contextScore: number;
  /** 0-100 ranking. Not a probability and never a trade authorization. */
  relevanceScore: number;
  contextComponents: ScoreComponent[];
  zoneId: string | null;
}

export interface NoWickZone {
  id: string;
  eventId: string;
  direction: Direction;
  closeLevel: number;
  level25: number;
  level50: number;
  level75: number;
  openLevel: number;
  originExtreme: number;
  fvgOverlapIds: string[];
  /** Order blocks arrive in a later phase. */
  obOverlap: ScoreComponentStatus;
  state: NoWickZoneState;
  rebalancePct: number;
  createdAt: string;
  knownAt: string;
  stateChangedAt: string | null;
  ageBars: number;
  active: boolean;
  relevanceScore: number;
}

export interface NoWickZoneEvent {
  id: string;
  zoneId: string;
  direction: Direction;
  type: NoWickZoneEventType;
  time: string;
  price: number;
  detail: string;
}

/** The live, not-yet-closed bar with how far through its bucket it is. Read-only context for the
 * reversal playbook's LATE_CANDLE_FADE / EARLY_CANDLE_CONTINUATION variants — never authorizes a
 * verdict (the decision engine stays closed-bar). Ratios are null when the range is zero. */
export interface FormingCandle {
  timeframe: Timeframe;
  openTime: string;
  closeTime: string;
  /** Clock at which this snapshot was taken. */
  asOf: string;
  /** 0..100, wall-clock elapsed through the bucket. */
  maturityPct: number;
  /** BULLISH close>open, BEARISH close<open, null when flat. */
  direction: Direction | null;
  open: number;
  high: number;
  low: number;
  close: number;
  range: number;
  body: number;
  upperWick: number;
  lowerWick: number;
  bodyPct: number | null;
  upperWickPct: number | null;
  lowerWickPct: number | null;
  closeLocationPct: number | null;
  /** Section-1 no-wick variant on the live candle; null when neither applies. Confluence only. */
  variant: NoWickVariant | null;
  /** Fade / continuation direction of the variant. */
  signalDirection: Direction | null;
  /** Origin-timeframe directional-quality weight (0 when the timeframe is not weighted). */
  originWeight: number;
}

export interface NoWickAnalysis {
  symbol: string;
  timeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  features: CandleFeatures[];
  events: NoWickEvent[];
  zones: NoWickZone[];
  zoneEvents: NoWickZoneEvent[];
  /** The live bar (context only); null when the last bar is closed. */
  forming: FormingCandle | null;
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

/** MasterDecision.noWickState: context only, never authorizes LONG/SHORT. */
export interface NoWickDecisionState {
  timeframe: Timeframe;
  time: string;
  direction: Direction;
  classification: NoWickClassification;
  strength: NoWickStrength;
  candleQualityScore: number;
  contextScore: number;
  relevanceScore: number;
  zoneState: NoWickZoneState | null;
  authority: "CONTEXT_ONLY";
}

// --- Phase 6: session & time -------------------------------------------------------------------

export const SESSION_NAMES = ["ASIA", "LONDON", "NY_AM", "NY_PM", "LONDON_CLOSE"] as const;
export type SessionName = (typeof SESSION_NAMES)[number];
export const KILL_ZONES = ["LONDON_KZ", "NY_AM_KZ", "NY_PM_KZ"] as const;
export type KillZone = (typeof KILL_ZONES)[number];
export const SESSION_INSTANCE_STATES = ["NOT_STARTED", "FORMING", "COMPLETE", "INCOMPLETE"] as const;
export type SessionInstanceState = (typeof SESSION_INSTANCE_STATES)[number];
export const ASIAN_RANGE_STATES = ["TIGHT", "NORMAL", "EXPANDED", "ABNORMALLY_LARGE"] as const;
export type AsianRangeState = (typeof ASIAN_RANGE_STATES)[number];
export const SESSION_QUALITIES = ["IDEAL", "ACCEPTABLE", "LOW_QUALITY", "AVOID"] as const;
export type SessionQuality = (typeof SESSION_QUALITIES)[number];
export const EXPANSION_STATES = ["CONSOLIDATING", "EARLY", "ACTIVE", "LATE", "EXHAUSTED"] as const;
export type ExpansionState = (typeof EXPANSION_STATES)[number];
export const JUDAS_STATUSES = ["CANDIDATE", "CONFIRMED", "FAILED"] as const;
export type JudasStatus = (typeof JUDAS_STATUSES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 6 additions). */
export const SESSION_CONTRACT_ENUMS = {
  SessionName: SESSION_NAMES,
  KillZone: KILL_ZONES,
  SessionInstanceState: SESSION_INSTANCE_STATES,
  AsianRangeState: ASIAN_RANGE_STATES,
  SessionQuality: SESSION_QUALITIES,
  ExpansionState: EXPANSION_STATES,
  JudasStatus: JUDAS_STATUSES,
} as const;

/** Time-only facts at `now` (no market data involved). */
export interface SessionClock {
  now: string;
  newYorkTime: string;
  londonTime: string;
  tradingDay: string;
  marketStatus: MarketStatus;
  activeSessions: SessionName[];
  activeKillZones: KillZone[];
  timeQuality: SessionQuality;
  nextSession: SessionName | null;
  nextSessionStart: string | null;
}

export interface SessionInstance {
  id: string;
  session: SessionName;
  tradingDay: string;
  start: string;
  end: string;
  /** FORMING levels are provisional; INCOMPLETE instances are never used for pools, range state or Judas. */
  state: SessionInstanceState;
  high: number | null;
  low: number | null;
  midpoint: number | null;
  range: number | null;
  highTime: string | null;
  lowTime: string | null;
  candleCount: number;
  expectedCount: number;
  knownAt: string | null;
  asianRangeState: AsianRangeState | null;
  asianRangeRatio: number | null;
}

export interface SessionOpens {
  dailyOpen: number | null;
  dailyOpenTime: string | null;
  nyMidnightOpen: number | null;
  nyMidnightOpenTime: string | null;
  weeklyOpen: number | null;
  weeklyOpenTime: string | null;
  lastClose: number | null;
  dailyChange: number | null;
  dailyChangePct: number | null;
}

export interface PreviousSession {
  instanceId: string;
  session: SessionName;
  high: number;
  low: number;
  end: string;
}

export interface AdrState {
  adr: number;
  periodDays: number;
  currentRange: number | null;
  pctUsed: number | null;
  expansion: ExpansionState | null;
}

export interface JudasSwing {
  id: string;
  tradingDay: string;
  session: SessionName;
  /** Direction of the expected real move (opposite the sweep). Never a trade signal. */
  direction: Direction;
  status: JudasStatus;
  asianHigh: number;
  asianLow: number;
  asianMidpoint: number;
  sweepTime: string;
  sweepExtreme: number;
  resolvedAt: string | null;
  detail: string;
}

/** Elevated-confidence multi-session sweep (spec section 5): London failed to take Asia's level, then
 * one New York candle swept BOTH in one move. Read-only session context; never a trade. */
export interface SessionSweepConfluence {
  id: string;
  tradingDay: string;
  session: SessionName;
  /** SSL = both session lows swept, BSL = both session highs swept. */
  side: LiquiditySide;
  asiaLevel: number;
  londonLevel: number;
  sweepTime: string;
  sweepExtreme: number;
  detail: string;
}

export interface SessionAnalysis {
  symbol: string;
  sourceTimeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  clock: SessionClock;
  sessionQuality: SessionQuality | null;
  instances: SessionInstance[];
  opens: SessionOpens;
  previousSession: PreviousSession | null;
  adr: AdrState | null;
  judas: JudasSwing[];
  sweepConfluence: SessionSweepConfluence[];
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

/** MasterDecision.sessionState: context only. Time never creates a trade by itself. */
export interface SessionDecisionState {
  tradingDay: string;
  marketStatus: MarketStatus;
  activeSessions: SessionName[];
  activeKillZones: KillZone[];
  timeQuality: SessionQuality;
  sessionQuality: SessionQuality | null;
  asianRangeState: AsianRangeState | null;
  adrPctUsed: number | null;
  expansion: ExpansionState | null;
  judas: string | null;
  authority: "CONTEXT_ONLY";
}

// --- Phase 7: setup state machine --------------------------------------------------------------

export const SETUP_STATES = ["DISCOVERED", "WATCH", "SETUP_FORMING", "LIQUIDITY_EVENT", "WAITING_FOR_MSS", "REBALANCE_WATCH", "REBALANCE_TOUCH", "REACTION", "CONFIRMATION", "SETUP_ARMED", "WAITING_FOR_RETRACEMENT", "ENTRY_ZONE_APPROACHING", "ENTRY_ZONE_TOUCHED", "WAITING_FOR_CONFIRMATION", "LONG_READY", "SHORT_READY", "ENTRY_MISSED", "INVALIDATED", "EXPIRED", "BLOCKED", "ACTIVE", "CLOSED"] as const;
export type SetupState = (typeof SETUP_STATES)[number];
export const SETUP_TYPES = ["LIQUIDITY_SWEEP_MSS", "REVERSAL_NO_WICK_IFVG"] as const;
export type SetupType = (typeof SETUP_TYPES)[number];
export const REVERSAL_ORIGIN_KINDS = ["NO_WICK", "IMR"] as const;
export type ReversalOriginKind = (typeof REVERSAL_ORIGIN_KINDS)[number];
export const REVERSAL_CONFIRMATIONS = ["IFVG_FLIP", "NEW_FVG", "IMR"] as const;
export type ReversalConfirmation = (typeof REVERSAL_CONFIRMATIONS)[number];
export const SETUP_STEPS = ["HTF_BIAS", "DOL_TARGET", "LIQUIDITY_EVENT", "DISPLACEMENT", "MSS", "PD_ARRAY", "RETRACEMENT", "LTF_CONFIRMATION", "RISK"] as const;
export type SetupStep = (typeof SETUP_STEPS)[number];
export const SETUP_STEP_STATUSES = ["DONE", "PENDING", "NOT_EVALUATED"] as const;
export type SetupStepStatus = (typeof SETUP_STEP_STATUSES)[number];
export const PO3_PHASES = ["ACCUMULATION", "MANIPULATION", "DISTRIBUTION", "UNCLEAR"] as const;
export type Po3Phase = (typeof PO3_PHASES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 7 additions). */
export const SETUP_CONTRACT_ENUMS = {
  SetupState: SETUP_STATES,
  SetupType: SETUP_TYPES,
  ReversalOriginKind: REVERSAL_ORIGIN_KINDS,
  ReversalConfirmation: REVERSAL_CONFIRMATIONS,
  SetupStep: SETUP_STEPS,
  SetupStepStatus: SETUP_STEP_STATUSES,
  Po3Phase: PO3_PHASES,
} as const;

// --- REVERSAL_NO_WICK_IFVG (counter-bias reversal setup). Read-only; `enabled` gates the live verdict. ---
export interface OriginZone {
  id: string;
  kind: ReversalOriginKind;
  timeframe: Timeframe;
  direction: Direction;
  bodyTop: number;
  bodyBottom: number;
  farEdge: number;
  weight: number;
  knownAt: string;
}

export interface ReversalEvent {
  id: string;
  setupId: string;
  direction: Direction;
  state: SetupState;
  time: string;
  price: number;
  detail: string;
}

export interface ReversalSetup {
  id: string;
  setupType: SetupType;
  direction: Direction;
  state: SetupState;
  terminal: boolean;
  tradingDay: string;
  origin: OriginZone;
  discoveredAt: string;
  stateChangedAt: string;
  rebalancedAt: string | null;
  reactionAt: string | null;
  armedAt: string | null;
  confirmations: ReversalConfirmation[];
  confirmingZoneIds: string[];
  protectiveLevel: number | null;
  entryPrice: number | null;
  entryZoneId: string | null;
  stopPrice: number | null;
  targetPrice: number | null;
  targetPoolId: string | null;
  targetLabel: string | null;
  rr: number | null;
  reason: string | null;
}

export interface ReversalAnalysis {
  symbol: string;
  timeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  /** A/B flag: when false the reversal setup never reaches the live verdict. */
  enabled: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  bias: HtfBias;
  originCount: number;
  current: ReversalSetup | null;
  setups: ReversalSetup[];
  events: ReversalEvent[];
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

export interface BiasPoint {
  timeframe: Timeframe;
  direction: Direction;
  knownAt: string;
  eventId: string;
}

export interface TargetRef {
  poolId: string;
  poolType: LiquidityPoolType;
  label: string;
  price: number;
}

export interface LiquidityRef {
  poolId: string;
  poolType: LiquidityPoolType;
  eventType: LiquidityEventType;
  time: string;
  extreme: number;
}

export interface BreakRef {
  eventId: string;
  type: StructureEventType;
  level: StructureLevel;
  time: string;
  price: number;
  displacementQualifier: QualifierStatus;
}

export interface SetupStepState {
  step: SetupStep;
  status: SetupStepStatus;
  detail: string;
}

export interface Setup {
  id: string;
  setupType: SetupType;
  direction: Direction;
  state: SetupState;
  terminal: boolean;
  tradingDay: string;
  discoveredAt: string;
  stateChangedAt: string;
  target: TargetRef | null;
  liquidityEvent: LiquidityRef | null;
  mss: BreakRef | null;
  protectiveLevel: number | null;
  zoneIds: string[];
  touchedZoneId: string | null;
  /** Why the setup was INVALIDATED / EXPIRED. */
  reason: string | null;
  nextRequiredEvent: string | null;
  steps: SetupStepState[];
  /** Phase 8: the confirmed plan. Never a trade authorization while verdict authority is FAIL_SAFE_ONLY. */
  entryPlan: EntryPlan | null;
}

export interface SetupEvent {
  id: string;
  setupId: string;
  direction: Direction;
  state: SetupState;
  time: string;
  price: number;
  detail: string;
}

export interface BiasState {
  direction: Direction | null;
  timeframes: Timeframe[];
  latest: BiasPoint[];
}

export interface Po3State {
  tradingDay: string | null;
  phase: Po3Phase;
  dailyOpen: number | null;
  adr: number | null;
  detail: string;
}

export interface SetupAnalysis {
  symbol: string;
  timeframe: Timeframe;
  asOf: string | null;
  candleCount: number;
  quality: DataQuality;
  isSynthetic: boolean;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  bias: BiasState;
  /** The open setup's state; BLOCKED when data is ineligible; null = no open setup. */
  currentState: SetupState | null;
  current: Setup | null;
  setups: Setup[];
  events: SetupEvent[];
  po3: Po3State;
  providerError: string | null;
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 8: entry & verdict ------------------------------------------------------------------

export const ENTRY_MODELS = ["CONSERVATIVE_RETEST", "M15_CLOSE", "LTF_REFINEMENT", "NO_WICK_REBALANCE", "BREAKER", "IFVG", "LIMIT_RESEARCH"] as const;
export type EntryModel = (typeof ENTRY_MODELS)[number];
export const ENTRY_MODES = ["CONSERVATIVE", "STANDARD", "AGGRESSIVE"] as const;
export type EntryMode = (typeof ENTRY_MODES)[number];
export const ENTRY_WARNINGS = ["EARLY_BEFORE_MSS", "DURING_SWEEP", "INSIDE_DISPLACEMENT", "BEFORE_CLOSE", "BEFORE_RETRACEMENT", "WEAK_FVG", "AGAINST_HTF", "BEFORE_MAJOR_NEWS"] as const;
export type EntryWarning = (typeof ENTRY_WARNINGS)[number];
export const SCORE_FACTORS = ["HTF", "LIQUIDITY", "STRUCTURE", "DISPLACEMENT", "PD_ARRAY", "NO_WICK", "SESSION", "MACRO", "ENTRY", "RISK_RR"] as const;
export type ScoreFactor = (typeof SCORE_FACTORS)[number];
export const SETUP_GRADES = ["A+", "A", "B", "C", "D"] as const;
export type SetupGrade = (typeof SETUP_GRADES)[number];
export const EVALUATION_OUTCOMES = ["UNAVAILABLE", "NO_TRADE", "WAIT", "CONFIRMED_PENDING_GATES", "CONFIRMED_AWAITING_AUTHORITY"] as const;
export type EvaluationOutcome = (typeof EVALUATION_OUTCOMES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 8 additions). */
export const ENTRY_CONTRACT_ENUMS = {
  EntryModel: ENTRY_MODELS,
  EntryMode: ENTRY_MODES,
  EntryWarning: ENTRY_WARNINGS,
  ScoreFactor: SCORE_FACTORS,
  SetupGrade: SETUP_GRADES,
  EvaluationOutcome: EVALUATION_OUTCOMES,
} as const;

export interface EntryPlan {
  model: EntryModel;
  mode: EntryMode;
  direction: Direction;
  confirmedAt: string;
  zoneId: string;
  entry: number;
  stop: number;
  risk: number;
  tp1: number;
  tp2: number | null;
  tp3: number | null;
  rr1: number;
  rr2: number | null;
  rr3: number | null;
  minRr: number;
  researchOnly: boolean;
  detail: string;
}

export interface ScoreItem {
  factor: ScoreFactor;
  status: ScoreComponentStatus;
  points: number;
  maxPoints: number;
  detail: string;
}

export interface ScoreAdjustment {
  name: string;
  points: number;
  detail: string;
}

/** Deterministic verdict evaluation. Scores rank setups; they are never win probabilities. */
export interface DecisionEvaluation {
  symbol: string;
  asOf: string | null;
  eligibleForDecision: boolean;
  ineligibility: AnalysisIneligibility[];
  outcome: EvaluationOutcome;
  direction: Direction | null;
  setupId: string | null;
  setupType: SetupType | null;
  setupState: SetupState | null;
  score: number | null;
  evaluatedMax: number | null;
  grade: SetupGrade | null;
  confidence: DecisionConfidence;
  conflictScore: number;
  dataQualityScore: number;
  components: ScoreItem[];
  adjustments: ScoreAdjustment[];
  hardBlockers: Blocker[];
  missingGates: Blocker[];
  warnings: EntryWarning[];
  evidenceFor: string[];
  evidenceAgainst: string[];
  devilsAdvocate: string[];
  plan: EntryPlan | null;
  /** Phase 13: the news gate (null only when not wired). A BLACKOUT on a confirmed plan makes the outcome NO_TRADE. */
  news: NewsAssessment | null;
  macro: MacroAssessment | null;
  /** Phase 9: the risk assessment (null only when the risk gate is not wired). Risk has final veto. */
  risk: RiskAssessment | null;
  /** Always NOT_AUTHORIZED until every required gate exists. */
  authority: "NOT_AUTHORIZED";
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 9: risk calculator ---------------------------------------------------------------------

export const RISK_STATUSES = ["NOT_CONFIGURED", "INVALID_PROFILE", "LOCKED", "SIZE_UNVERIFIED", "CLEAR", "WITHIN_LIMITS", "UNAVAILABLE"] as const;
export type RiskStatus = (typeof RISK_STATUSES)[number];
export const RISK_PROFILE_NAMES = ["CONSERVATIVE", "STANDARD", "AGGRESSIVE", "CUSTOM"] as const;
export type RiskProfileName = (typeof RISK_PROFILE_NAMES)[number];
export const RISK_LOCKS = ["ACCOUNT_STATE_STALE", "DAILY_LOSS_LIMIT", "WEEKLY_LOSS_LIMIT", "MAX_OPEN_RISK", "MAX_POSITIONS", "MAX_TRADES_PER_DAY", "CONSECUTIVE_LOSSES", "PROP_DAILY_DRAWDOWN", "PROP_TOTAL_DRAWDOWN", "VOLATILITY", "INVALID_STOP", "ADDING_TO_LOSER", "UNSAFE_SPREAD", "INSUFFICIENT_MARGIN", "BELOW_MIN_VOLUME"] as const;
export type RiskLock = (typeof RISK_LOCKS)[number];
export const RISK_WARNINGS = ["RISK_REDUCED_BY_LIMITS", "CORRELATED_EXPOSURE", "USER_SUPPLIED_SPEC", "SPEC_INCONSISTENT", "CONVERSION_UNAVAILABLE", "LEVERAGE_NOT_SET", "SPREAD_UNKNOWN", "VOLATILITY_UNAVAILABLE", "VOLATILITY_NOT_CHECKED", "ACCOUNT_STATE_NOT_PROVIDED", "NEWS_NOT_EVALUATED"] as const;
export type RiskWarning = (typeof RISK_WARNINGS)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 9 additions). */
export const RISK_CONTRACT_ENUMS = {
  RiskStatus: RISK_STATUSES,
  RiskProfileName: RISK_PROFILE_NAMES,
  RiskLock: RISK_LOCKS,
  RiskWarning: RISK_WARNINGS,
} as const;

export interface RiskLimits {
  riskPerTradePct: number;
  dailyRiskLimitPct: number;
  weeklyRiskLimitPct: number;
  maxOpenRiskPct: number;
  maxTradesPerDay: number;
  maxPositions: number;
  maxConsecutiveLosses: number;
}

/** Account-currency amounts. The effective budget is the smallest cap; losses only shrink it. */
export interface RiskBudget {
  riskPerTradeAmount: number;
  dailyRemaining: number;
  weeklyRemaining: number;
  openRisk: number;
  openRiskRemaining: number;
  propRemaining: number | null;
  effectiveRiskAmount: number;
}

export interface RiskLockItem {
  lock: RiskLock;
  detail: string;
}

export interface VolatilityState {
  timeframe: Timeframe;
  atr: number;
  baselineAtr: number;
  ratio: number;
  lockRatio: number;
}

export interface PositionSize {
  direction: Direction;
  entry: number;
  stop: number;
  priceDistance: number;
  points: number | null;
  pips: number | null;
  spread: number | null;
  sizingDistance: number;
  riskPerVolume: number | null;
  volume: number | null;
  minVolume: number | null;
  volumeStep: number | null;
  riskAmount: number | null;
  riskAmountWithoutSpread: number | null;
  riskPct: number | null;
  marginRequired: number | null;
  detail: string;
}

/** Deterministic risk assessment. A clean assessment never authorizes a trade. */
export interface RiskAssessment {
  symbol: string;
  status: RiskStatus;
  profile: RiskProfileName | null;
  currency: string | null;
  profileError: string | null;
  limits: RiskLimits | null;
  budget: RiskBudget | null;
  locks: RiskLockItem[];
  warnings: RiskWarning[];
  volatility: VolatilityState | null;
  position: PositionSize | null;
  sizeStatus: PositionSizeStatus | null;
  blockers: Blocker[];
  news: string;
  authority: "NOT_AUTHORIZED";
  strategyVersion: string;
  generatedAt: string;
}

export interface PropRules {
  startingBalance: number;
  maxDailyDrawdownPct: number;
  maxTotalDrawdownPct: number;
}

export interface AccountProfile {
  balance: number;
  currency: string;
  profile: RiskProfileName;
  riskPerTradePct?: number | null;
  dailyRiskLimitPct?: number | null;
  weeklyRiskLimitPct?: number | null;
  maxOpenRiskPct?: number | null;
  maxTradesPerDay?: number | null;
  maxPositions?: number | null;
  maxConsecutiveLosses?: number | null;
  leverage?: number | null;
  propRules?: PropRules | null;
}

export interface OpenPosition {
  symbol: string;
  direction: Direction;
  riskAmount: number;
  inLoss: boolean;
}

export interface AccountState {
  tradingDay: string;
  realizedPnlToday: number;
  realizedPnlWeek: number;
  tradesToday: number;
  consecutiveLosses: number;
  openPositions?: OpenPosition[];
}

/** tickValue is the value of one tick for 1.0 volume, in quoteCurrency. */
export interface InstrumentSpec {
  symbol: string;
  contractSize: number;
  tickSize: number;
  tickValue: number;
  minVolume: number;
  volumeStep: number;
  quoteCurrency: string;
  typicalSpread?: number | null;
  platformPipSize?: number | null;
}

/** What-if calculation body. Stored nowhere. */
export interface RiskCalculationRequest {
  symbol: string;
  direction: Direction;
  entry: number;
  stop: number;
  account: AccountProfile;
  state?: AccountState | null;
  instrumentSpec?: InstrumentSpec | null;
  conversionRate?: number | null;
}

// --- Phase 10: watchlist & scanner -----------------------------------------------------------------

export interface MarketRow {
  symbol: string;
  assetClass: AssetClass;
  base: string;
  quote: string;
  priority: number;
  deeplyValidated: boolean;
  marketStatus: MarketStatus;
  positionSizeStatus: PositionSizeStatus;
}

/** One symbol's Master Decision, projected for the scanner. Never a trade signal. */
export interface ScanRow {
  rank: number;
  symbol: string;
  assetClass: AssetClass;
  priority: number;
  deeplyValidated: boolean;
  marketStatus: MarketStatus;
  verdict: Verdict;
  dataQuality: DataQuality;
  htfBias: string;
  setupState: string;
  setupType: string | null;
  setupProgress: number;
  setupScore: number | null;
  setupGrade: string | null;
  decisionConfidence: DecisionConfidence;
  riskStatus: string;
  primaryDol: string | null;
  blockers: Blocker[];
  nextRequiredEvent: string | null;
  latestClosedOpenTime: string | null;
  evaluatedAt: string;
  cacheAgeSeconds: number;
  error: string | null;
}

/** Attention ranking across markets; every row keeps its own fail-safe verdict. */
export interface ScanResponse {
  rows: ScanRow[];
  requestedSymbols: string[];
  minScore: number | null;
  onlySetups: boolean;
  ranking: string[];
  cacheSeconds: number;
  durationMs: number;
  verdictAuthority: VerdictAuthority;
  authority: "NOT_AUTHORIZED";
  strategyVersion: string;
  scannedAt: string;
}

// --- Phase 11: alerts ------------------------------------------------------------------------------

export const ALERT_CATEGORIES = ["INFORMATIONAL", "WATCH", "SETUP", "ENTRY", "RISK", "MANAGEMENT", "CRITICAL"] as const;
export type AlertCategory = (typeof ALERT_CATEGORIES)[number];
export const ALERT_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type AlertPriority = (typeof ALERT_PRIORITIES)[number];
export const ALERT_TYPES = ["LIQUIDITY_APPROACHING", "LIQUIDITY_SWEEP", "LIQUIDITY_RUN", "EQUAL_LEVEL_CREATED", "KEY_LEVEL_INTERACTION", "SESSION_LIQUIDITY_EVENT", "STRUCTURE_BREAK", "DISPLACEMENT", "NO_WICK_EVENT", "NO_WICK_REBALANCE", "FVG_CREATED", "FVG_TOUCHED", "FVG_INVALIDATED", "SETUP_ARMED", "ENTRY_ZONE_APPROACHING", "ENTRY_ZONE_TOUCHED", "ENTRY_CONFIRMED_PENDING_GATES", "ENTRY_MISSED", "EARLY_ENTRY_WARNING", "SETUP_INVALIDATED", "SETUP_EXPIRED", "SESSION_CHANGE", "RISK_LOCK", "RISK_LOCK_CLEARED", "DATA_UNAVAILABLE", "DATA_RESTORED", "MONITOR_FAILURE", "READY", "NEWS_COUNTDOWN", "NEWS_BLACKOUT", "NEWS_CLEARED", "MACRO_SHIFT"] as const;
export type AlertType = (typeof ALERT_TYPES)[number];
export const READY_WATCH_STATES = ["WAITING", "GATES_PENDING", "UNAVAILABLE", "FIRED"] as const;
export type ReadyWatchState = (typeof READY_WATCH_STATES)[number];
export const CONDITION_STATUSES = ["MET", "MISSING", "NOT_EVALUATED"] as const;
export type ConditionStatus = (typeof CONDITION_STATUSES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 11 additions). */
export const ALERT_CONTRACT_ENUMS = {
  AlertCategory: ALERT_CATEGORIES,
  AlertPriority: ALERT_PRIORITIES,
  AlertType: ALERT_TYPES,
  ReadyWatchState: READY_WATCH_STATES,
  ConditionStatus: CONDITION_STATUSES,
} as const;

/** An engine state change. Never a trade instruction. */
export interface Alert {
  id: string;
  seq: number;
  dedupeKey: string;
  symbol: string;
  type: AlertType;
  category: AlertCategory;
  priority: AlertPriority;
  title: string;
  message: string;
  direction: Direction | null;
  price: number | null;
  occurredAt: string;
  createdAt: string;
  strategyVersion: string;
}

export interface MonitorStatus {
  enabled: boolean;
  running: boolean;
  symbols: string[];
  pollSeconds: number;
  maxQuietSeconds: number;
  cycles: number;
  lastCycleAt: string | null;
  lastCycleMs: number | null;
  lastError: string | null;
  baselined: string[];
}

/** Newest first; pass nextCursor as `since` to receive only newer alerts. */
export interface AlertFeed {
  alerts: Alert[];
  nextCursor: number;
  suppressed: Record<string, number>;
  monitor: MonitorStatus;
  authority: "NOT_AUTHORIZED";
  generatedAt: string;
}

export interface ReadyCondition {
  name: string;
  status: ConditionStatus;
  detail: string;
}

/** ALERT_ME_WHEN_READY: fires once, only on a LONG/SHORT verdict under FULL verdict authority. */
export interface ReadyWatch {
  id: string;
  symbol: string;
  direction: Direction | null;
  state: ReadyWatchState;
  conditions: ReadyCondition[];
  nextRequiredEvent: string | null;
  createdAt: string;
  lastCheckedAt: string | null;
  firedAt: string | null;
}

export interface ReadyWatchRequest {
  symbol: string;
  direction?: Direction | null;
}

// --- Phase 12: AI assistant ------------------------------------------------------------------------

export const ASSISTANT_INTENTS = ["ANALYZE", "WHY_WAITING", "WHY_NOT_DIRECTION", "LIQUIDITY", "DOL", "SCORE_CHANGE", "NO_WICK", "INVALIDATION", "TRADE_PLAN", "RISK", "SESSION", "A_PLUS_SETUPS", "COMPARE", "BACKTEST", "MACRO", "CREATE_ALERT", "EXPLAIN_TERM", "HELP", "NEWS", "JOURNAL"] as const;
export type AssistantIntent = (typeof ASSISTANT_INTENTS)[number];
export const EDUCATION_LEVELS = ["BEGINNER", "INTERMEDIATE", "ADVANCED", "PROFESSIONAL"] as const;
export type EducationLevel = (typeof EDUCATION_LEVELS)[number];
export const ASSISTANT_PROVIDERS = ["DETERMINISTIC", "ANTHROPIC"] as const;
export type AssistantProvider = (typeof ASSISTANT_PROVIDERS)[number];
export const GUARD_STATUSES = ["PASSED", "FALLBACK", "NOT_APPLICABLE"] as const;
export type GuardStatus = (typeof GUARD_STATUSES)[number];
export const TOOL_STATUSES = ["OK", "UNAVAILABLE", "PROPOSED", "REJECTED"] as const;
export type ToolStatus = (typeof TOOL_STATUSES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 12 additions). */
export const ASSISTANT_CONTRACT_ENUMS = {
  AssistantIntent: ASSISTANT_INTENTS,
  EducationLevel: EDUCATION_LEVELS,
  AssistantProvider: ASSISTANT_PROVIDERS,
  GuardStatus: GUARD_STATUSES,
  ToolStatus: TOOL_STATUSES,
} as const;

export interface AskRequest {
  symbol: string;
  question: string;
  level?: EducationLevel;
  compareSymbols?: string[];
}

/** A value copied from a deterministic service; `source` names the tool that returned it. */
export interface Fact {
  label: string;
  value: string;
  source: string;
}

export interface ToolCallRecord {
  name: string;
  symbol: string | null;
  status: ToolStatus;
  detail: string | null;
}

export interface DecisionRef {
  symbol: string;
  verdict: Verdict;
  dataQuality: DataQuality;
  blockers: Blocker[];
  strategyVersion: string;
  updatedAt: string;
}

/** create_alert is never executed by the assistant: the user confirms the proposal. */
export interface AlertProposal {
  symbol: string;
  direction: Direction | null;
  reason: string;
}

export interface GuardReport {
  status: GuardStatus;
  violations: string[];
}

/** Explanation of the deterministic engine state. Never a price source, verdict or trade instruction. */
export interface AssistantAnswer {
  symbol: string;
  question: string;
  intent: AssistantIntent;
  level: EducationLevel;
  answer: string;
  facts: Fact[];
  unknowns: string[];
  tools: ToolCallRecord[];
  decision: DecisionRef | null;
  proposal: AlertProposal | null;
  provider: AssistantProvider;
  model: string | null;
  guard: GuardReport;
  authority: "NOT_AUTHORIZED";
  strategyVersion: string;
  generatedAt: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  available: boolean;
  detail: string | null;
}

export interface AssistantCapabilities {
  provider: AssistantProvider;
  model: string | null;
  externalAiConfigured: boolean;
  sharesAccountData: boolean;
  commands: string[];
  levels: EducationLevel[];
  tools: ToolInfo[];
  authority: "NOT_AUTHORIZED";
}

// --- Phase 13: economic calendar & news gate -------------------------------------------------------

export const NEWS_STATES = ["CLEAR", "CAUTION", "BLACKOUT", "POST_NEWS_WAIT", "NORMALIZED", "UNAVAILABLE"] as const;
export type NewsState = (typeof NEWS_STATES)[number];
export const EVENT_IMPORTANCES = ["LOW", "MEDIUM", "HIGH", "EXTREME"] as const;
export type EventImportance = (typeof EVENT_IMPORTANCES)[number];
export const EVENT_STATUSES = ["UPCOMING", "IMMINENT", "RELEASED", "REVISED", "COMPLETED", "CANCELLED", "DELAYED"] as const;
export type EventStatus = (typeof EVENT_STATUSES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 13 additions). */
export const NEWS_CONTRACT_ENUMS = {
  NewsState: NEWS_STATES,
  EventImportance: EVENT_IMPORTANCES,
  EventStatus: EVENT_STATUSES,
} as const;

export type CalendarValue = number | string | null;

export interface EconomicEvent {
  id: string;
  country: string;
  currency: string;
  name: string;
  scheduledTime: string;
  importance: EventImportance;
  actual?: CalendarValue;
  forecast?: CalendarValue;
  previous?: CalendarValue;
  revisedPrevious?: CalendarValue;
  status?: EventStatus | null;
}

export interface CalendarFile {
  source: string;
  fetchedAt: string;
  coverageStart: string;
  coverageEnd: string;
  events: EconomicEvent[];
}

export interface EventView {
  id: string;
  country: string;
  currency: string;
  name: string;
  scheduledTime: string;
  importance: EventImportance;
  status: EventStatus;
  actual: CalendarValue;
  forecast: CalendarValue;
  previous: CalendarValue;
  revisedPrevious: CalendarValue;
  /** actual - forecast when both are numeric; no direction is inferred. */
  surprise: number | null;
  surprisePct: number | null;
  blackoutStart: string | null;
  blackoutEnd: string | null;
  minutesToEvent: number;
}

export interface CalendarInfo {
  provider: string;
  source: string | null;
  isSynthetic: boolean;
  available: boolean;
  fetchedAt: string | null;
  coverageStart: string | null;
  coverageEnd: string | null;
  reason: string | null;
}

/** The news gate for one symbol. BLACKOUT is a hard blocker; UNAVAILABLE means clear cannot be proven. */
export interface NewsAssessment {
  symbol: string;
  state: NewsState;
  relevantCurrencies: string[];
  activeEvent: EventView | null;
  nextEvent: EventView | null;
  windowStart: string | null;
  windowEnd: string | null;
  events: EventView[];
  calendar: CalendarInfo;
  blockers: Blocker[];
  warnings: string[];
  strategyVersion: string;
  generatedAt: string;
}

export interface CalendarResponse {
  events: EventView[];
  calendar: CalendarInfo;
  generatedAt: string;
}

// --- Phase 14: basic macro ----------------------------------------------------------------------------

export const MACRO_STATES = ["STRONGLY_SUPPORTIVE", "SUPPORTIVE", "NEUTRAL", "CONFLICT", "STRONG_CONFLICT", "UNAVAILABLE"] as const;
export type MacroState = (typeof MACRO_STATES)[number];
export const MACRO_BIASES = ["BULLISH", "BEARISH", "NEUTRAL", "UNAVAILABLE"] as const;
export type MacroBias = (typeof MACRO_BIASES)[number];
export const MACRO_SERIES_IDS = ["DXY", "US2Y", "US10Y", "US10Y_REAL", "VIX"] as const;
export type MacroSeriesId = (typeof MACRO_SERIES_IDS)[number];
export const SERIES_DIRECTIONS = ["UP", "DOWN", "FLAT"] as const;
export type SeriesDirection = (typeof SERIES_DIRECTIONS)[number];
export const CORRELATION_REGIMES = ["ALIGNED", "WEAK", "INVERTED", "UNAVAILABLE"] as const;
export type CorrelationRegime = (typeof CORRELATION_REGIMES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 14 additions). */
export const MACRO_CONTRACT_ENUMS = {
  MacroState: MACRO_STATES,
  MacroBias: MACRO_BIASES,
  MacroSeriesId: MACRO_SERIES_IDS,
  SeriesDirection: SERIES_DIRECTIONS,
  CorrelationRegime: CORRELATION_REGIMES,
} as const;

// --- Phase 14: basic macro wire types -------------------------------------------------------------------

export interface MacroObservation {
  date: string;
  value: number;
}

export interface MacroSeries {
  id: MacroSeriesId;
  name: string;
  unit: string;
  observations: MacroObservation[];
}

export interface MacroFile {
  source: string;
  fetchedAt: string;
  series: MacroSeries[];
}

export interface SeriesTrend {
  id: MacroSeriesId;
  lastDate: string;
  lastValue: number;
  change: number;
  zScore: number | null;
  direction: SeriesDirection;
  stale: boolean;
}

export interface DriverContribution {
  series: string;
  configured: string;
  relationship: number;
  weight: number;
  direction: SeriesDirection | null;
  contribution: number | null;
  detail: string;
}

export interface CorrelationInfo {
  series: MacroSeriesId;
  observations: number;
  coefficient: number | null;
  regime: CorrelationRegime;
  detail: string;
}

/** Deterministic macro context for one market. Bias is for the market rising; state is versus the setup. Context only: never blocks. */
export interface MacroAssessment {
  symbol: string;
  bias: MacroBias;
  score: number | null;
  state: MacroState | null;
  direction: Direction | null;
  drivers: DriverContribution[];
  series: SeriesTrend[];
  correlation: CorrelationInfo | null;
  provider: string;
  source: string | null;
  isSynthetic: boolean;
  available: boolean;
  fetchedAt: string | null;
  reason: string | null;
  warnings: string[];
  thresholds: Record<string, number>;
  strategyVersion: string;
  generatedAt: string;
}

/** Raw macro series as supplied by the configured provider. */
export interface MacroSeriesResponse {
  provider: string;
  isSynthetic: boolean;
  available: boolean;
  reason: string | null;
  source: string | null;
  fetchedAt: string | null;
  series: MacroSeries[];
  generatedAt: string;
}

// --- Phase 15: journal ---------------------------------------------------------------------------------

export const JOURNAL_ENTRY_KINDS = ["TRADE", "NO_TRADE", "MISSED_ENTRY"] as const;
export type JournalEntryKind = (typeof JOURNAL_ENTRY_KINDS)[number];
export const JOURNAL_STATUSES = ["OPEN", "CLOSED"] as const;
export type JournalStatus = (typeof JOURNAL_STATUSES)[number];
export const TRADE_RESULTS = ["FULL_WIN", "PARTIAL_WIN", "BREAK_EVEN", "FULL_LOSS", "PARTIAL_LOSS", "MANUAL_EXIT", "INVALIDATION_EXIT", "NEWS_EXIT", "TRAILING_STOP_EXIT", "MISSED_ENTRY", "NO_TRADE"] as const;
export type TradeResult = (typeof TRADE_RESULTS)[number];
export const EXIT_REASONS = ["TARGET", "STOP", "BREAK_EVEN_STOP", "MANUAL", "INVALIDATION", "NEWS", "TRAILING_STOP"] as const;
export type ExitReason = (typeof EXIT_REASONS)[number];
export const PROCESS_CLASSIFICATIONS = ["VALID_WIN", "BAD_PROCESS_WIN", "VALID_LOSS", "PROCESS_ERROR"] as const;
export type ProcessClassification = (typeof PROCESS_CLASSIFICATIONS)[number];
export const RULE_VIOLATIONS = ["TRADED_ON_UNAVAILABLE_DECISION", "TRADED_DURING_NEWS_BLACKOUT", "TRADED_WHILE_RISK_LOCKED", "NO_CONFIRMED_PLAN", "AGAINST_PLAN_DIRECTION", "CHASED_ENTRY", "RISK_ABOVE_LIMIT", "MOVED_STOP", "NO_STOP_PLACED", "OVERSIZED_POSITION", "EXITED_EARLY_WITHOUT_REASON", "REVENGE_TRADE", "OTHER"] as const;
export type RuleViolation = (typeof RULE_VIOLATIONS)[number];
export const SNAPSHOT_INTEGRITIES = ["VERIFIED", "TAMPERED"] as const;
export type SnapshotIntegrity = (typeof SNAPSHOT_INTEGRITIES)[number];
export const SNAPSHOT_TIMINGS = ["PRE_ENTRY", "POST_ENTRY", "NOT_APPLICABLE"] as const;
export type SnapshotTiming = (typeof SNAPSHOT_TIMINGS)[number];
export const EXTREME_SOURCES = ["MANUAL", "CANDLES", "UNAVAILABLE"] as const;
export type ExtremeSource = (typeof EXTREME_SOURCES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 15 additions). */
export const JOURNAL_CONTRACT_ENUMS = {
  JournalEntryKind: JOURNAL_ENTRY_KINDS,
  JournalStatus: JOURNAL_STATUSES,
  TradeResult: TRADE_RESULTS,
  ExitReason: EXIT_REASONS,
  ProcessClassification: PROCESS_CLASSIFICATIONS,
  RuleViolation: RULE_VIOLATIONS,
  SnapshotIntegrity: SNAPSHOT_INTEGRITIES,
  SnapshotTiming: SNAPSHOT_TIMINGS,
  ExtremeSource: EXTREME_SOURCES,
} as const;

// --- Phase 15: journal wire types ------------------------------------------------------------------------

/** The user's own manual trade (prices typed in by the user; no broker data). */
export interface JournalTradeFill {
  direction: Direction;
  entry: number;
  stop: number | null;
  targets: number[];
  volume: number | null;
  riskPct: number | null;
  openedAt: string;
}

export interface CreateJournalEntryRequest {
  symbol: string;
  kind: JournalEntryKind;
  trade: JournalTradeFill | null;
  notes: string;
}

export interface RecordOutcomeRequest {
  exitPrice: number;
  exitedAt: string;
  exitReason: ExitReason;
  mfePrice: number | null;
  maePrice: number | null;
  reportedViolations: RuleViolation[];
  notes: string;
}

export interface SnapshotSummary {
  capturedAt: string;
  dayOfWeek: string;
  activeSessions: string[];
  activeKillZones: string[];
  timeQuality: string | null;
  verdict: string;
  dataQuality: string;
  engineAuthorization: string;
  executionTimeframe: string | null;
  htfBias: string | null;
  primaryDol: string | null;
  liquidityEvent: string | null;
  structureEvent: string | null;
  displacement: string | null;
  pdArray: string | null;
  noWick: string | null;
  newsState: string | null;
  macroBias: string | null;
  macroState: string | null;
  setupType: string | null;
  setupState: string | null;
  setupScore: number | null;
  setupGrade: string | null;
  confidence: string | null;
  planEntry: number | null;
  planStop: number | null;
  planTargets: number[];
  planRr: number | null;
  riskStatus: string | null;
  blockers: string[];
  isSynthetic: boolean;
  strategyVersion: string;
}

export interface JournalSnapshot {
  capturedAt: string;
  timing: SnapshotTiming;
  integrity: SnapshotIntegrity;
  hash: string;
  decision: Record<string, unknown>;
  evaluation: Record<string, unknown> | null;
  data: Record<string, unknown> | null;
}

export interface JournalOutcome {
  revision: number;
  recordedAt: string;
  exitPrice: number;
  exitedAt: string;
  exitReason: ExitReason;
  mfePrice: number | null;
  maePrice: number | null;
  extremeSource: ExtremeSource;
  extremesSynthetic: boolean;
  extremesDetail: string | null;
  result: TradeResult;
  rMultiple: number | null;
  plannedR: number | null;
  mfeR: number | null;
  maeR: number | null;
  entryEfficiency: number | null;
  exitEfficiency: number | null;
  durationMinutes: number;
  reportedViolations: RuleViolation[];
  violations: RuleViolation[];
  classification: ProcessClassification;
  notes: string;
  integrity: SnapshotIntegrity;
}

/** A private manual journal record with its immutable, hash-verified engine snapshot. Never an authorization. */
export interface JournalEntry {
  id: string;
  kind: JournalEntryKind;
  status: JournalStatus;
  symbol: string;
  createdAt: string;
  trade: JournalTradeFill | null;
  notes: string;
  detectedViolations: RuleViolation[];
  result: TradeResult | null;
  classification: ProcessClassification | null;
  rMultiple: number | null;
  summary: SnapshotSummary;
  snapshot: JournalSnapshot;
  outcome: JournalOutcome | null;
  outcomeRevisions: JournalOutcome[];
  strategyVersion: string;
}

export interface JournalEntryRow {
  id: string;
  kind: JournalEntryKind;
  status: JournalStatus;
  symbol: string;
  createdAt: string;
  direction: Direction | null;
  entry: number | null;
  result: TradeResult | null;
  classification: ProcessClassification | null;
  rMultiple: number | null;
  detectedViolations: RuleViolation[];
  snapshotTiming: SnapshotTiming;
  integrity: SnapshotIntegrity;
  isSynthetic: boolean;
  setupType: string | null;
  verdict: string;
}

export interface JournalStoreInfo {
  available: boolean;
  backend: string;
  reason: string | null;
}

export interface JournalListResponse {
  entries: JournalEntryRow[];
  nextCursor: string | null;
  store: JournalStoreInfo;
  generatedAt: string;
}

export interface JournalExport {
  exportedAt: string;
  strategyVersion: string;
  store: JournalStoreInfo;
  entries: JournalEntry[];
}

// --- Phase 16: paper trading --------------------------------------------------------------------------

export const PAPER_ENTRY_TYPES = ["MARKET", "LIMIT"] as const;
export type PaperEntryType = (typeof PAPER_ENTRY_TYPES)[number];
export const PAPER_SOURCES = ["MANUAL", "ENGINE_PLAN"] as const;
export type PaperSource = (typeof PAPER_SOURCES)[number];
export const PAPER_STATUSES = ["PENDING", "OPEN", "CLOSED", "EXPIRED", "CANCELLED"] as const;
export type PaperStatus = (typeof PAPER_STATUSES)[number];
export const PAPER_EVENT_TYPES = ["CREATED", "FILLED", "STOP_HIT", "TARGET_HIT", "CLOSED_MANUALLY", "EXPIRED", "CANCELLED"] as const;
export type PaperEventType = (typeof PAPER_EVENT_TYPES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 16 additions). */
export const PAPER_CONTRACT_ENUMS = {
  PaperEntryType: PAPER_ENTRY_TYPES,
  PaperSource: PAPER_SOURCES,
  PaperStatus: PAPER_STATUSES,
  PaperEventType: PAPER_EVENT_TYPES,
} as const;

// --- Phase 16: paper trading wire types -------------------------------------------------------------------

/** Assumed per-symbol costs in price units (not broker specifications). */
export interface AssumedCosts {
  spread: number;
  slippage: number;
  commission: number;
}

export interface CreatePaperSimRequest {
  symbol: string;
  source: PaperSource;
  direction: Direction | null;
  entryType: PaperEntryType;
  limitPrice: number | null;
  stop: number | null;
  target: number | null;
  notes: string;
}

export interface PaperEvent {
  seq: number;
  type: PaperEventType;
  at: string;
  price: number | null;
  ambiguous: boolean;
  detail: string;
  integrity: SnapshotIntegrity;
}

export interface PaperResult {
  result: TradeResult;
  exitReason: ExitReason;
  rMultiple: number | null;
  netRMultiple: number | null;
  plannedR: number | null;
  mfeR: number | null;
  maeR: number | null;
  entryEfficiency: number | null;
  exitEfficiency: number | null;
  durationMinutes: number;
  violations: RuleViolation[];
  classification: ProcessClassification;
}

/** A broker-free paper simulation on the independent market data. authority is always SIMULATION_ONLY. */
export interface PaperSim {
  id: string;
  symbol: string;
  source: PaperSource;
  direction: Direction;
  entryType: PaperEntryType;
  referencePrice: number;
  limitPrice: number | null;
  stop: number;
  target: number;
  costs: AssumedCosts;
  status: PaperStatus;
  createdAt: string;
  fillPrice: number | null;
  filledAt: string | null;
  exitPrice: number | null;
  exitedAt: string | null;
  processedThrough: string | null;
  dataQuality: DataQuality | null;
  dataNote: string | null;
  isSynthetic: boolean;
  detectedViolations: RuleViolation[];
  result: PaperResult | null;
  events: PaperEvent[];
  summary: SnapshotSummary;
  integrity: SnapshotIntegrity;
  notes: string;
  authority: string;
  strategyVersion: string;
}

export interface PaperSimRow {
  id: string;
  symbol: string;
  source: PaperSource;
  direction: Direction;
  entryType: PaperEntryType;
  status: PaperStatus;
  createdAt: string;
  fillPrice: number | null;
  exitPrice: number | null;
  result: TradeResult | null;
  netRMultiple: number | null;
  classification: ProcessClassification | null;
  isSynthetic: boolean;
  integrity: SnapshotIntegrity;
}

export interface PaperStoreInfo {
  available: boolean;
  backend: string;
  reason: string | null;
  monitorEnabled: boolean;
}

export interface PaperListResponse {
  sims: PaperSimRow[];
  nextCursor: string | null;
  store: PaperStoreInfo;
  generatedAt: string;
}

// --- Phase 17: analytics -------------------------------------------------------------------------------

export const SAMPLE_SIZE_LABELS = ["INSUFFICIENT", "LIMITED", "MODERATE", "STRONGER_EVIDENCE"] as const;
export type SampleSizeLabel = (typeof SAMPLE_SIZE_LABELS)[number];
export const ANALYTICS_SOURCES = ["JOURNAL", "PAPER"] as const;
export type AnalyticsSource = (typeof ANALYTICS_SOURCES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 17 additions). */
export const ANALYTICS_CONTRACT_ENUMS = {
  SampleSizeLabel: SAMPLE_SIZE_LABELS,
  AnalyticsSource: ANALYTICS_SOURCES,
} as const;

// --- Phase 17: analytics wire types -----------------------------------------------------------------------

export interface GroupStats {
  key: string;
  count: number;
  label: SampleSizeLabel;
  wins: number;
  losses: number;
  breakeven: number;
  winRate: number | null;
  rCount: number;
  avgR: number | null;
  totalR: number | null;
  avgWinR: number | null;
  avgLossR: number | null;
  expectancyR: number | null;
  profitFactor: number | null;
}

export interface Breakdown {
  dimension: string;
  groups: GroupStats[];
  best: string | null;
  bestReason: string;
}

export interface Drawdown {
  maxDrawdownR: number;
  peakAt: string | null;
  troughAt: string | null;
  recoveredAt: string | null;
  recoveryTrades: number | null;
}

export interface EquityPoint {
  at: string;
  cumulativeR: number;
}

export interface ProcessStats {
  classifications: Record<string, number>;
  violationCounts: Record<string, number>;
  withViolations: GroupStats;
  withoutViolations: GroupStats;
}

export interface DolAccuracy {
  evaluated: number;
  reached: number;
  rate: number | null;
  label: SampleSizeLabel;
  aligned: GroupStats;
  opposed: GroupStats;
  unknown: number;
  detail: string;
}

export interface Unavailable {
  available: boolean;
  reason: string;
}

export interface AnalyticsFilters {
  source: AnalyticsSource;
  symbol: string | null;
  start: string | null;
  end: string | null;
  includeSynthetic: boolean;
  strategyVersion: string | null;
}

export interface ExcludedCounts {
  tampered: number;
  synthetic: number;
  notClosed: number;
  filtered: number;
}

/** Descriptive statistics of verified closed records (authority DESCRIPTIVE_ONLY). Never a probability or trade signal. */
export interface AnalyticsReport {
  filters: AnalyticsFilters;
  available: boolean;
  reason: string | null;
  overall: GroupStats;
  drawdown: Drawdown;
  avgDurationMinutes: number | null;
  medianDurationMinutes: number | null;
  avgMfeR: number | null;
  avgMaeR: number | null;
  avgEntryEfficiency: number | null;
  avgExitEfficiency: number | null;
  breakdowns: Breakdown[];
  process: ProcessStats;
  dol: DolAccuracy;
  alertUsefulness: Unavailable;
  decisionRecords: Record<string, number>;
  excluded: ExcludedCounts;
  includesSynthetic: boolean;
  strategyVersions: string[];
  equityCurve: EquityPoint[];
  disclaimer: string;
  authority: string;
  strategyVersion: string;
  generatedAt: string;
}

// --- Phase 18: backtesting -----------------------------------------------------------------------------

export const BACKTEST_STATUSES = ["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"] as const;
export type BacktestStatus = (typeof BACKTEST_STATUSES)[number];
export const BACKTEST_TRADE_STATUSES = ["CLOSED", "EXPIRED", "SKIPPED_OVERLAP", "OPEN_AT_END"] as const;
export type BacktestTradeStatus = (typeof BACKTEST_TRADE_STATUSES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 18 additions). */
export const BACKTEST_CONTRACT_ENUMS = {
  BacktestStatus: BACKTEST_STATUSES,
  BacktestTradeStatus: BACKTEST_TRADE_STATUSES,
} as const;

// --- Phase 18: backtesting wire types ---------------------------------------------------------------------

export interface BacktestVariant {
  name: string;
  entryMode: EntryMode;
  costMultiplier: number;
}

export interface BacktestRequest {
  symbol: string;
  start: string;
  end: string;
  variants: BacktestVariant[];
  segments: number;
  outOfSampleFrom: string | null;
}

export interface BacktestTrade {
  variant: string;
  status: BacktestTradeStatus;
  setupId: string;
  setupType: string;
  model: string;
  direction: Direction;
  confirmedAt: string;
  limitPrice: number;
  stop: number;
  target: number;
  fillPrice: number | null;
  filledAt: string | null;
  exitPrice: number | null;
  exitedAt: string | null;
  exitReason: ExitReason | null;
  result: TradeResult | null;
  rMultiple: number | null;
  netRMultiple: number | null;
  mfeR: number | null;
  maeR: number | null;
  ambiguous: boolean;
  detail: string;
}

export interface Funnel {
  steps: number;
  ineligibleSteps: number;
  ineligibleReasons: Record<string, number>;
  setupsDiscovered: number;
  statesReached: Record<string, number>;
  plansConfirmed: number;
  plansSkippedOverlap: number;
  fills: number;
  expired: number;
  closed: number;
  openAtEnd: number;
}

export interface SegmentStats {
  name: string;
  start: string;
  end: string;
  stats: GroupStats;
}

export interface MonteCarlo {
  resamples: number;
  seed: number;
  trades: number;
  label: SampleSizeLabel;
  totalRP05: number;
  totalRP50: number;
  totalRP95: number;
  maxDrawdownRP50: number;
  maxDrawdownRP95: number;
  detail: string;
}

export interface VariantResult {
  variant: BacktestVariant;
  funnel: Funnel;
  stats: GroupStats;
  drawdown: Drawdown;
  equityCurve: EquityPoint[];
  breakdowns: Breakdown[];
  segments: SegmentStats[];
  inSample: GroupStats | null;
  outOfSample: GroupStats | null;
  monteCarlo: MonteCarlo | null;
  ambiguousTrades: number;
  trades: BacktestTrade[];
}

export interface BacktestData {
  provider: string;
  isSynthetic: boolean;
  executionBars: number;
  stepBars: number;
  firstBar: string | null;
  lastBar: string | null;
}

export interface BacktestResult {
  variants: VariantResult[];
  data: BacktestData;
  disclosures: string[];
  configHash: string;
  completedAt: string;
}

export interface BacktestProgress {
  variant: string | null;
  stepsDone: number;
  stepsTotal: number;
  pct: number;
}

/** A research replay of the live setup engine over closed history (authority RESEARCH_ONLY). Never a forecast or authorization. */
export interface BacktestRun {
  id: string;
  status: BacktestStatus;
  request: BacktestRequest;
  progress: BacktestProgress;
  error: string | null;
  result: BacktestResult | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  integrity: string;
  authority: string;
  strategyVersion: string;
}

export interface BacktestRunRow {
  id: string;
  symbol: string;
  start: string;
  end: string;
  status: BacktestStatus;
  pct: number;
  variants: string[];
  closedTrades: Record<string, number>;
  netTotalR: Record<string, number | null>;
  createdAt: string;
  strategyVersion: string;
}

export interface BacktestStoreInfo {
  available: boolean;
  backend: string;
  reason: string | null;
  runningId: string | null;
}

export interface BacktestListResponse {
  runs: BacktestRunRow[];
  store: BacktestStoreInfo;
  generatedAt: string;
}

// --- Phase 19: replay ----------------------------------------------------------------------------------

export const REPLAY_MODES = ["MANUAL", "GUIDED", "BLIND", "QUIZ"] as const;
export type ReplayMode = (typeof REPLAY_MODES)[number];
export const QUIZ_QUESTION_TYPES = ["NEXT_BARS_DIRECTION", "LEVEL_FIRST", "SETUP_PROGRESS"] as const;
export type QuizQuestionType = (typeof QUIZ_QUESTION_TYPES)[number];
export const QUIZ_GRADES = ["CORRECT", "INCORRECT", "VOID"] as const;
export type QuizGrade = (typeof QUIZ_GRADES)[number];

/** Keyed exactly like strategy-spec/enums.json (Phase 19 additions). */
export const REPLAY_CONTRACT_ENUMS = {
  ReplayMode: REPLAY_MODES,
  QuizQuestionType: QUIZ_QUESTION_TYPES,
  QuizGrade: QUIZ_GRADES,
} as const;

// --- Phase 19: replay wire types --------------------------------------------------------------------------

export interface CreateReplayRequest {
  symbol: string;
  mode: ReplayMode;
  timeframe: ChartTimeframe;
  start: string;
}

export interface StepRequest {
  bars: number;
}

export interface QuizAnswerRequest {
  questionId: string;
  answer: string;
}

export interface ReplaySetupView {
  id: string;
  setupType: string;
  direction: string;
  state: string;
  nextRequiredEvent: string | null;
  planEntry: number | null;
  planStop: number | null;
  planTp1: number | null;
}

export interface ReplayAnalysis {
  eligibleForDecision: boolean;
  ineligibility: string[];
  setupState: string | null;
  currentSetup: ReplaySetupView | null;
  newYorkTime: string;
  activeSessions: string[];
  timeQuality: string;
  authority: string;
}

export interface ReplayEvent {
  time: string;
  state: string;
  direction: string;
  detail: string;
}

export interface ReplayGuidance {
  events: ReplayEvent[];
  narrative: string[];
}

export interface QuizQuestion {
  id: string;
  type: QuizQuestionType;
  prompt: string;
  options: string[];
  horizonBars: number;
  referencePrice: number;
  upperLevel: number | null;
  lowerLevel: number | null;
}

export interface QuizResult {
  questionId: string;
  type: QuizQuestionType;
  answer: string;
  grade: QuizGrade;
  correctAnswer: string | null;
  detail: string;
  answeredAtCursor: string;
  revealedToCursor: string;
}

export interface QuizScore {
  asked: number;
  correct: number;
  incorrect: number;
  void: number;
  accuracy: number | null;
  label: SampleSizeLabel;
}

/** A replay session as of its cursor (authority EDUCATION_ONLY). Candles never extend beyond the cursor. */
export interface ReplayState {
  id: string;
  mode: ReplayMode;
  timeframe: ChartTimeframe;
  label: string;
  cursor: string;
  start: string;
  canStepBack: boolean;
  atEnd: boolean;
  ended: boolean;
  candles: ChartCandle[];
  analysis: ReplayAnalysis | null;
  guidance: ReplayGuidance | null;
  quiz: QuizQuestion | null;
  lastResult: QuizResult | null;
  history: QuizResult[];
  score: QuizScore | null;
  masked: boolean;
  authority: string;
  strategyVersion: string;
}

export interface ReplayReveal {
  id: string;
  symbol: string;
  start: string;
  cursor: string;
  priceScale: number;
  weekShift: number;
}

export interface ReplaySessionRow {
  id: string;
  mode: ReplayMode;
  timeframe: ChartTimeframe;
  label: string;
  cursor: string;
  ended: boolean;
  score: QuizScore | null;
}

export interface ReplayStatus {
  sessions: ReplaySessionRow[];
  maxSessions: number;
  idleTtlHours: number;
  storage: string;
  generatedAt: string;
}
