import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  CHART_TIMEFRAMES,
  type MacroObservation,
  type MacroSeries,
  type MacroFile,
  type SeriesTrend,
  type DriverContribution,
  type CorrelationInfo,
  type MacroAssessment,
  type MacroSeriesResponse,
  type JournalTradeFill,
  type CreateJournalEntryRequest,
  type RecordOutcomeRequest,
  type SnapshotSummary,
  type JournalSnapshot,
  type JournalOutcome,
  type JournalEntry,
  type JournalEntryRow,
  type JournalStoreInfo,
  type JournalListResponse,
  type JournalExport,
  type AssumedCosts,
  type CreatePaperSimRequest,
  type PaperEvent,
  type PaperResult,
  type PaperSim,
  type PaperSimRow,
  type PaperStoreInfo,
  type PaperListResponse,
  type GroupStats,
  type Breakdown,
  type Drawdown,
  type EquityPoint,
  type ProcessStats,
  type DolAccuracy,
  type Unavailable,
  type AnalyticsFilters,
  type ExcludedCounts,
  type AnalyticsReport,
  type BacktestVariant,
  type BacktestRequest,
  type BacktestTrade,
  type Funnel,
  type SegmentStats,
  type MonteCarlo,
  type VariantResult,
  type BacktestData,
  type BacktestResult,
  type BacktestProgress,
  type BacktestRun,
  type BacktestRunRow,
  type BacktestStoreInfo,
  type BacktestListResponse,
  type CreateReplayRequest,
  type StepRequest,
  type QuizAnswerRequest,
  type ReplaySetupView,
  type ReplayAnalysis,
  type ReplayEvent,
  type ReplayGuidance,
  type QuizQuestion,
  type QuizResult,
  type QuizScore,
  type ReplayState,
  type ReplayReveal,
  type ReplaySessionRow,
  type ReplayStatus,
  type EconomicEvent,
  type CalendarFile,
  type EventView,
  type CalendarInfo,
  type NewsAssessment,
  type CalendarResponse,
  type AskRequest,
  type Fact,
  type ToolCallRecord,
  type DecisionRef,
  type AlertProposal,
  type GuardReport,
  type AssistantAnswer,
  type ToolInfo,
  type AssistantCapabilities,
  type Alert,
  type MonitorStatus,
  type AlertFeed,
  type ReadyCondition,
  type ReadyWatch,
  type ReadyWatchRequest,
  type MarketRow,
  type ScanRow,
  type ScanResponse,
  type RiskLimits,
  type RiskBudget,
  type RiskLockItem,
  type VolatilityState,
  type PositionSize,
  type RiskAssessment,
  type PropRules,
  type AccountProfile,
  type OpenPosition,
  type AccountState,
  type InstrumentSpec,
  type RiskCalculationRequest,
  type DecisionEvaluation,
  type EntryPlan,
  type ScoreAdjustment,
  type ScoreItem,
  type BiasPoint,
  type BiasState,
  type BreakRef,
  type LiquidityRef,
  type Po3State,
  type Setup,
  type SetupAnalysis,
  type SetupEvent,
  type SetupStepState,
  type TargetRef,
  type AdrState,
  type JudasSwing,
  type PreviousSession,
  type SessionAnalysis,
  type SessionClock,
  type SessionInstance,
  type SessionOpens,
  type CandleFeatures,
  type NoWickAnalysis,
  type NoWickEvent,
  type NoWickZone,
  type NoWickZoneEvent,
  type ScoreComponent,
  type ChartCandle,
  type ChartSeriesResponse,
  type DataReport,
  type DisplacementEvent,
  type DolSelection,
  type DolTarget,
  type LevelStructure,
  type LiquidityAnalysis,
  type LiquidityEvent,
  type LiquidityPool,
  type MasterDecision,
  type MtfStructureResponse,
  type PdArrayAnalysis,
  type PdArrayEvent,
  type PdArrayZone,
  type StructureAnalysis,
  type StructureEvent,
  type Swing,
  type SystemStatus,
  type TimeframeStructureState,
} from "../src/index";

/** Compile-time: K must list every key of T and nothing else. */
type Exhaustive<T, K extends readonly (keyof T)[]> =
  Exclude<keyof T, K[number]> extends never ? K : ["missing keys:", Exclude<keyof T, K[number]>];

function fields<T>() {
  return <const K extends readonly (keyof T)[]>(keys: Exhaustive<T, K>) => keys as readonly string[];
}

const TS_FIELDS: Record<string, readonly string[]> = {
  MasterDecision: fields<MasterDecision>()([
    "symbol", "verdict", "direction", "setupType", "setupState", "setupGrade", "setupScore",
    "decisionConfidence", "htfBias", "primaryDol", "secondaryDol", "liquidityEvent", "structureEvent",
    "displacement", "pdArray", "noWickState", "sessionState", "macroState", "newsState", "entryZone", "preferredEntry",
    "stop", "tp1", "tp2", "tp3", "rr", "riskStatus", "blockers", "nextRequiredEvent", "invalidation",
    "dataQuality", "strategyVersion", "updatedAt",
  ]),
  DataReport: fields<DataReport>()([
    "provider", "isSynthetic", "timeframe", "candleCount", "latestClosedOpenTime", "quality",
    "marketStatus", "positionSizeStatus", "issues", "providerError",
  ]),
  SystemStatus: fields<SystemStatus>()([
    "strategyVersion", "phase", "phaseName", "verdictAuthority", "enabledEngines", "environment",
    "provider", "brokerConnection", "orderExecution", "marketDataSource", "chartRenderer", "primaryMarket",
  ]),
  ChartCandle: fields<ChartCandle>()(["time", "open", "high", "low", "close", "volume", "isClosed"]),
  ChartSeriesResponse: fields<ChartSeriesResponse>()([
    "symbol", "timeframe", "sourceTimeframe", "provider", "isSynthetic", "quality", "marketStatus",
    "candles", "issues", "providerError", "strategyVersion", "generatedAt",
  ]),
};

Object.assign(TS_FIELDS, {
  Swing: fields<Swing>()(["id", "level", "kind", "label", "price", "time", "confirmedAt", "brokenAt", "brokenBy"]),
  StructureEvent: fields<StructureEvent>()([
    "id", "level", "type", "direction", "status", "confirmation", "price", "time", "brokenSwingId",
    "brokenSwingTime", "trendBefore", "ambiguous", "liquidityQualifier", "displacementQualifier",
  ]),
  LevelStructure: fields<LevelStructure>()([
    "level", "pivotLength", "state", "trend", "swings", "events", "protectedHigh", "protectedLow", "barsSinceLastBreak",
  ]),
  StructureAnalysis: fields<StructureAnalysis>()([
    "symbol", "timeframe", "asOf", "candleCount", "quality", "isSynthetic", "eligibleForDecision", "ineligibility",
    "internal", "external", "events", "providerError", "strategyVersion", "generatedAt",
  ]),
  TimeframeStructureState: fields<TimeframeStructureState>()([
    "timeframe", "state", "trend", "quality", "eligibleForDecision", "ineligibility",
  ]),
  MtfStructureResponse: fields<MtfStructureResponse>()([
    "symbol", "alignment", "htfBias", "eligibleForDecision", "timeframes", "strategyVersion", "generatedAt",
  ]),
});

Object.assign(TS_FIELDS, {
  LiquidityPool: fields<LiquidityPool>()(["id","type","side","scope","label","price","formedAt","knownAt","sourceTimes","state","touches","stateChangedAt","taken","distanceAtr","magnetScore"]),
  LiquidityEvent: fields<LiquidityEvent>()(["id","poolId","poolType","side","type","price","time","extreme","close"]),
  DolTarget: fields<DolTarget>()(["poolId","type","side","label","price","magnetScore","distanceAtr"]),
  DolSelection: fields<DolSelection>()(["primary","secondary","confidence","margin","reason"]),
  LiquidityAnalysis: fields<LiquidityAnalysis>()(["symbol","timeframe","asOf","candleCount","quality","isSynthetic","eligibleForDecision","ineligibility","keyLevelsAvailable","pools","events","dol","providerError","strategyVersion","generatedAt"]),
});

Object.assign(TS_FIELDS, {
  DisplacementEvent: fields<DisplacementEvent>()(["id","direction","grade","magnitudeAtr","legStart","time","candleCount","avgBodyPct"]),
  PdArrayZone: fields<PdArrayZone>()(["id","type","direction","top","bottom","midpoint","sizeAtr","sourceTimes","createdAt","knownAt","state","fillPct","ifvgStatus","parentId","displacementGrade","stateChangedAt","invalidatedAt","ageBars","active","qualityScore"]),
  PdArrayEvent: fields<PdArrayEvent>()(["id","zoneId","zoneType","direction","type","time","price","detail"]),
  PdArrayAnalysis: fields<PdArrayAnalysis>()(["symbol","timeframe","asOf","candleCount","quality","isSynthetic","eligibleForDecision","ineligibility","displacements","zones","events","providerError","strategyVersion","generatedAt"]),
});
Object.assign(TS_FIELDS, {
  CandleFeatures: fields<CandleFeatures>()(["time","range","body","upperWick","lowerWick","bodyPct","upperWickPct","lowerWickPct","bodyAtr","rangeAtr","bodyToMedian","rangeToMedian","closeLocationPct"]),
  ScoreComponent: fields<ScoreComponent>()(["factor","status","points","maxPoints","detail"]),
  NoWickEvent: fields<NoWickEvent>()(["id","direction","shape","classification","tags","strength","time","open","high","low","close","bodyPct","upperWickPct","lowerWickPct","bodyAtr","closeLocationPct","insideBar","candleQualityScore","contextScore","relevanceScore","contextComponents","zoneId"]),
  NoWickZone: fields<NoWickZone>()(["id","eventId","direction","closeLevel","level25","level50","level75","openLevel","originExtreme","fvgOverlapIds","obOverlap","state","rebalancePct","createdAt","knownAt","stateChangedAt","ageBars","active","relevanceScore"]),
  NoWickZoneEvent: fields<NoWickZoneEvent>()(["id","zoneId","direction","type","time","price","detail"]),
  NoWickAnalysis: fields<NoWickAnalysis>()(["symbol","timeframe","asOf","candleCount","quality","isSynthetic","eligibleForDecision","ineligibility","features","events","zones","zoneEvents","providerError","strategyVersion","generatedAt"]),
});
Object.assign(TS_FIELDS, {
  SessionClock: fields<SessionClock>()(["now","newYorkTime","londonTime","tradingDay","marketStatus","activeSessions","activeKillZones","timeQuality","nextSession","nextSessionStart"]),
  SessionInstance: fields<SessionInstance>()(["id","session","tradingDay","start","end","state","high","low","midpoint","range","highTime","lowTime","candleCount","expectedCount","knownAt","asianRangeState","asianRangeRatio"]),
  SessionOpens: fields<SessionOpens>()(["dailyOpen","dailyOpenTime","nyMidnightOpen","nyMidnightOpenTime","weeklyOpen","weeklyOpenTime","lastClose","dailyChange","dailyChangePct"]),
  PreviousSession: fields<PreviousSession>()(["instanceId","session","high","low","end"]),
  AdrState: fields<AdrState>()(["adr","periodDays","currentRange","pctUsed","expansion"]),
  JudasSwing: fields<JudasSwing>()(["id","tradingDay","session","direction","status","asianHigh","asianLow","asianMidpoint","sweepTime","sweepExtreme","resolvedAt","detail"]),
  SessionAnalysis: fields<SessionAnalysis>()(["symbol","sourceTimeframe","asOf","candleCount","quality","isSynthetic","eligibleForDecision","ineligibility","clock","sessionQuality","instances","opens","previousSession","adr","judas","providerError","strategyVersion","generatedAt"]),
});
Object.assign(TS_FIELDS, {
  BiasPoint: fields<BiasPoint>()(["timeframe","direction","knownAt","eventId"]),
  TargetRef: fields<TargetRef>()(["poolId","poolType","label","price"]),
  LiquidityRef: fields<LiquidityRef>()(["poolId","poolType","eventType","time","extreme"]),
  BreakRef: fields<BreakRef>()(["eventId","type","level","time","price","displacementQualifier"]),
  SetupStepState: fields<SetupStepState>()(["step","status","detail"]),
  Setup: fields<Setup>()(["id","setupType","direction","state","terminal","tradingDay","discoveredAt","stateChangedAt","target","liquidityEvent","mss","protectiveLevel","zoneIds","touchedZoneId","reason","nextRequiredEvent","steps","entryPlan"]),
  SetupEvent: fields<SetupEvent>()(["id","setupId","direction","state","time","price","detail"]),
  BiasState: fields<BiasState>()(["direction","timeframes","latest"]),
  Po3State: fields<Po3State>()(["tradingDay","phase","dailyOpen","adr","detail"]),
  SetupAnalysis: fields<SetupAnalysis>()(["symbol","timeframe","asOf","candleCount","quality","isSynthetic","eligibleForDecision","ineligibility","bias","currentState","current","setups","events","po3","providerError","strategyVersion","generatedAt"]),
});
Object.assign(TS_FIELDS, {
  EntryPlan: fields<EntryPlan>()(["model","mode","direction","confirmedAt","zoneId","entry","stop","risk","tp1","tp2","tp3","rr1","rr2","rr3","minRr","researchOnly","detail"]),
  ScoreItem: fields<ScoreItem>()(["factor","status","points","maxPoints","detail"]),
  ScoreAdjustment: fields<ScoreAdjustment>()(["name","points","detail"]),
  DecisionEvaluation: fields<DecisionEvaluation>()(["symbol","asOf","eligibleForDecision","ineligibility","outcome","direction","setupId","setupType","setupState","score","evaluatedMax","grade","confidence","conflictScore","dataQualityScore","components","adjustments","hardBlockers","missingGates","warnings","evidenceFor","evidenceAgainst","devilsAdvocate","plan","risk","news","macro","authority","strategyVersion","generatedAt"]),
});

Object.assign(TS_FIELDS, {
  RiskLimits: fields<RiskLimits>()(["riskPerTradePct","dailyRiskLimitPct","weeklyRiskLimitPct","maxOpenRiskPct","maxTradesPerDay","maxPositions","maxConsecutiveLosses"]),
  RiskBudget: fields<RiskBudget>()(["riskPerTradeAmount","dailyRemaining","weeklyRemaining","openRisk","openRiskRemaining","propRemaining","effectiveRiskAmount"]),
  RiskLockItem: fields<RiskLockItem>()(["lock","detail"]),
  VolatilityState: fields<VolatilityState>()(["timeframe","atr","baselineAtr","ratio","lockRatio"]),
  PositionSize: fields<PositionSize>()(["direction","entry","stop","priceDistance","points","pips","spread","sizingDistance","riskPerVolume","volume","minVolume","volumeStep","riskAmount","riskAmountWithoutSpread","riskPct","marginRequired","detail"]),
  RiskAssessment: fields<RiskAssessment>()(["symbol","status","profile","currency","profileError","limits","budget","locks","warnings","volatility","position","sizeStatus","blockers","news","authority","strategyVersion","generatedAt"]),
  PropRules: fields<PropRules>()(["startingBalance","maxDailyDrawdownPct","maxTotalDrawdownPct"]),
  AccountProfile: fields<AccountProfile>()(["balance","currency","profile","riskPerTradePct","dailyRiskLimitPct","weeklyRiskLimitPct","maxOpenRiskPct","maxTradesPerDay","maxPositions","maxConsecutiveLosses","leverage","propRules"]),
  OpenPosition: fields<OpenPosition>()(["symbol","direction","riskAmount","inLoss"]),
  AccountState: fields<AccountState>()(["tradingDay","realizedPnlToday","realizedPnlWeek","tradesToday","consecutiveLosses","openPositions"]),
  InstrumentSpec: fields<InstrumentSpec>()(["symbol","contractSize","tickSize","tickValue","minVolume","volumeStep","quoteCurrency","typicalSpread","platformPipSize"]),
  RiskCalculationRequest: fields<RiskCalculationRequest>()(["symbol","direction","entry","stop","account","state","instrumentSpec","conversionRate"]),
});

Object.assign(TS_FIELDS, {
  MarketRow: fields<MarketRow>()(["symbol","assetClass","base","quote","priority","deeplyValidated","marketStatus","positionSizeStatus"]),
  ScanRow: fields<ScanRow>()(["rank","symbol","assetClass","priority","deeplyValidated","marketStatus","verdict","dataQuality","htfBias","setupState","setupType","setupProgress","setupScore","setupGrade","decisionConfidence","riskStatus","primaryDol","blockers","nextRequiredEvent","latestClosedOpenTime","evaluatedAt","cacheAgeSeconds","error"]),
  ScanResponse: fields<ScanResponse>()(["rows","requestedSymbols","minScore","onlySetups","ranking","cacheSeconds","durationMs","verdictAuthority","authority","strategyVersion","scannedAt"]),
});

Object.assign(TS_FIELDS, {
  Alert: fields<Alert>()(["id","seq","dedupeKey","symbol","type","category","priority","title","message","direction","price","occurredAt","createdAt","strategyVersion"]),
  MonitorStatus: fields<MonitorStatus>()(["enabled","running","symbols","pollSeconds","maxQuietSeconds","cycles","lastCycleAt","lastCycleMs","lastError","baselined"]),
  AlertFeed: fields<AlertFeed>()(["alerts","nextCursor","suppressed","monitor","authority","generatedAt"]),
  ReadyCondition: fields<ReadyCondition>()(["name","status","detail"]),
  ReadyWatch: fields<ReadyWatch>()(["id","symbol","direction","state","conditions","nextRequiredEvent","createdAt","lastCheckedAt","firedAt"]),
  ReadyWatchRequest: fields<ReadyWatchRequest>()(["symbol","direction"]),
});

Object.assign(TS_FIELDS, {
  AskRequest: fields<AskRequest>()(["symbol","question","level","compareSymbols"]),
  Fact: fields<Fact>()(["label","value","source"]),
  ToolCallRecord: fields<ToolCallRecord>()(["name","symbol","status","detail"]),
  DecisionRef: fields<DecisionRef>()(["symbol","verdict","dataQuality","blockers","strategyVersion","updatedAt"]),
  AlertProposal: fields<AlertProposal>()(["symbol","direction","reason"]),
  GuardReport: fields<GuardReport>()(["status","violations"]),
  AssistantAnswer: fields<AssistantAnswer>()(["symbol","question","intent","level","answer","facts","unknowns","tools","decision","proposal","provider","model","guard","authority","strategyVersion","generatedAt"]),
  ToolInfo: fields<ToolInfo>()(["name","description","available","detail"]),
  AssistantCapabilities: fields<AssistantCapabilities>()(["provider","model","externalAiConfigured","sharesAccountData","commands","levels","tools","authority"]),
});

Object.assign(TS_FIELDS, {
  EconomicEvent: fields<EconomicEvent>()(["id","country","currency","name","scheduledTime","importance","actual","forecast","previous","revisedPrevious","status"]),
  CalendarFile: fields<CalendarFile>()(["source","fetchedAt","coverageStart","coverageEnd","events"]),
  MacroObservation: fields<MacroObservation>()(["date","value"]),
  MacroSeries: fields<MacroSeries>()(["id","name","unit","observations"]),
  MacroFile: fields<MacroFile>()(["source","fetchedAt","series"]),
  SeriesTrend: fields<SeriesTrend>()(["id","lastDate","lastValue","change","zScore","direction","stale"]),
  DriverContribution: fields<DriverContribution>()(["series","configured","relationship","weight","direction","contribution","detail"]),
  CorrelationInfo: fields<CorrelationInfo>()(["series","observations","coefficient","regime","detail"]),
  MacroAssessment: fields<MacroAssessment>()(["symbol","bias","score","state","direction","drivers","series","correlation","provider","source","isSynthetic","available","fetchedAt","reason","warnings","thresholds","strategyVersion","generatedAt"]),
  MacroSeriesResponse: fields<MacroSeriesResponse>()(["provider","isSynthetic","available","reason","source","fetchedAt","series","generatedAt"]),
  JournalTradeFill: fields<JournalTradeFill>()(["direction","entry","stop","targets","volume","riskPct","openedAt"]),
  CreateJournalEntryRequest: fields<CreateJournalEntryRequest>()(["symbol","kind","trade","notes"]),
  RecordOutcomeRequest: fields<RecordOutcomeRequest>()(["exitPrice","exitedAt","exitReason","mfePrice","maePrice","reportedViolations","notes"]),
  SnapshotSummary: fields<SnapshotSummary>()(["capturedAt","dayOfWeek","activeSessions","activeKillZones","timeQuality","verdict","dataQuality","engineAuthorization","executionTimeframe","htfBias","primaryDol","liquidityEvent","structureEvent","displacement","pdArray","noWick","newsState","macroBias","macroState","setupType","setupState","setupScore","setupGrade","confidence","planEntry","planStop","planTargets","planRr","riskStatus","blockers","isSynthetic","strategyVersion"]),
  JournalSnapshot: fields<JournalSnapshot>()(["capturedAt","timing","integrity","hash","decision","evaluation","data"]),
  JournalOutcome: fields<JournalOutcome>()(["revision","recordedAt","exitPrice","exitedAt","exitReason","mfePrice","maePrice","extremeSource","extremesSynthetic","extremesDetail","result","rMultiple","plannedR","mfeR","maeR","entryEfficiency","exitEfficiency","durationMinutes","reportedViolations","violations","classification","notes","integrity"]),
  JournalEntry: fields<JournalEntry>()(["id","kind","status","symbol","createdAt","trade","notes","detectedViolations","result","classification","rMultiple","summary","snapshot","outcome","outcomeRevisions","strategyVersion"]),
  JournalEntryRow: fields<JournalEntryRow>()(["id","kind","status","symbol","createdAt","direction","entry","result","classification","rMultiple","detectedViolations","snapshotTiming","integrity","isSynthetic","setupType","verdict"]),
  JournalStoreInfo: fields<JournalStoreInfo>()(["available","backend","reason"]),
  JournalListResponse: fields<JournalListResponse>()(["entries","nextCursor","store","generatedAt"]),
  JournalExport: fields<JournalExport>()(["exportedAt","strategyVersion","store","entries"]),
  AssumedCosts: fields<AssumedCosts>()(["spread","slippage","commission"]),
  CreatePaperSimRequest: fields<CreatePaperSimRequest>()(["symbol","source","direction","entryType","limitPrice","stop","target","notes"]),
  PaperEvent: fields<PaperEvent>()(["seq","type","at","price","ambiguous","detail","integrity"]),
  PaperResult: fields<PaperResult>()(["result","exitReason","rMultiple","netRMultiple","plannedR","mfeR","maeR","entryEfficiency","exitEfficiency","durationMinutes","violations","classification"]),
  PaperSim: fields<PaperSim>()(["id","symbol","source","direction","entryType","referencePrice","limitPrice","stop","target","costs","status","createdAt","fillPrice","filledAt","exitPrice","exitedAt","processedThrough","dataQuality","dataNote","isSynthetic","detectedViolations","result","events","summary","integrity","notes","authority","strategyVersion"]),
  PaperSimRow: fields<PaperSimRow>()(["id","symbol","source","direction","entryType","status","createdAt","fillPrice","exitPrice","result","netRMultiple","classification","isSynthetic","integrity"]),
  PaperStoreInfo: fields<PaperStoreInfo>()(["available","backend","reason","monitorEnabled"]),
  PaperListResponse: fields<PaperListResponse>()(["sims","nextCursor","store","generatedAt"]),
  GroupStats: fields<GroupStats>()(["key","count","label","wins","losses","breakeven","winRate","rCount","avgR","totalR","avgWinR","avgLossR","expectancyR","profitFactor"]),
  Breakdown: fields<Breakdown>()(["dimension","groups","best","bestReason"]),
  Drawdown: fields<Drawdown>()(["maxDrawdownR","peakAt","troughAt","recoveredAt","recoveryTrades"]),
  EquityPoint: fields<EquityPoint>()(["at","cumulativeR"]),
  ProcessStats: fields<ProcessStats>()(["classifications","violationCounts","withViolations","withoutViolations"]),
  DolAccuracy: fields<DolAccuracy>()(["evaluated","reached","rate","label","aligned","opposed","unknown","detail"]),
  Unavailable: fields<Unavailable>()(["available","reason"]),
  AnalyticsFilters: fields<AnalyticsFilters>()(["source","symbol","start","end","includeSynthetic","strategyVersion"]),
  ExcludedCounts: fields<ExcludedCounts>()(["tampered","synthetic","notClosed","filtered"]),
  AnalyticsReport: fields<AnalyticsReport>()(["filters","available","reason","overall","drawdown","avgDurationMinutes","medianDurationMinutes","avgMfeR","avgMaeR","avgEntryEfficiency","avgExitEfficiency","breakdowns","process","dol","alertUsefulness","decisionRecords","excluded","includesSynthetic","strategyVersions","equityCurve","disclaimer","authority","strategyVersion","generatedAt"]),
  BacktestVariant: fields<BacktestVariant>()(["name","entryMode","costMultiplier"]),
  BacktestRequest: fields<BacktestRequest>()(["symbol","start","end","variants","segments","outOfSampleFrom"]),
  BacktestTrade: fields<BacktestTrade>()(["variant","status","setupId","setupType","model","direction","confirmedAt","limitPrice","stop","target","fillPrice","filledAt","exitPrice","exitedAt","exitReason","result","rMultiple","netRMultiple","mfeR","maeR","ambiguous","detail"]),
  Funnel: fields<Funnel>()(["steps","ineligibleSteps","ineligibleReasons","setupsDiscovered","statesReached","plansConfirmed","plansSkippedOverlap","fills","expired","closed","openAtEnd"]),
  SegmentStats: fields<SegmentStats>()(["name","start","end","stats"]),
  MonteCarlo: fields<MonteCarlo>()(["resamples","seed","trades","label","totalRP05","totalRP50","totalRP95","maxDrawdownRP50","maxDrawdownRP95","detail"]),
  VariantResult: fields<VariantResult>()(["variant","funnel","stats","drawdown","equityCurve","breakdowns","segments","inSample","outOfSample","monteCarlo","ambiguousTrades","trades"]),
  BacktestData: fields<BacktestData>()(["provider","isSynthetic","executionBars","stepBars","firstBar","lastBar"]),
  BacktestResult: fields<BacktestResult>()(["variants","data","disclosures","configHash","completedAt"]),
  BacktestProgress: fields<BacktestProgress>()(["variant","stepsDone","stepsTotal","pct"]),
  BacktestRun: fields<BacktestRun>()(["id","status","request","progress","error","result","createdAt","startedAt","finishedAt","integrity","authority","strategyVersion"]),
  BacktestRunRow: fields<BacktestRunRow>()(["id","symbol","start","end","status","pct","variants","closedTrades","netTotalR","createdAt","strategyVersion"]),
  BacktestStoreInfo: fields<BacktestStoreInfo>()(["available","backend","reason","runningId"]),
  BacktestListResponse: fields<BacktestListResponse>()(["runs","store","generatedAt"]),
  CreateReplayRequest: fields<CreateReplayRequest>()(["symbol","mode","timeframe","start"]),
  StepRequest: fields<StepRequest>()(["bars"]),
  QuizAnswerRequest: fields<QuizAnswerRequest>()(["questionId","answer"]),
  ReplaySetupView: fields<ReplaySetupView>()(["id","setupType","direction","state","nextRequiredEvent","planEntry","planStop","planTp1"]),
  ReplayAnalysis: fields<ReplayAnalysis>()(["eligibleForDecision","ineligibility","setupState","currentSetup","newYorkTime","activeSessions","timeQuality","authority"]),
  ReplayEvent: fields<ReplayEvent>()(["time","state","direction","detail"]),
  ReplayGuidance: fields<ReplayGuidance>()(["events","narrative"]),
  QuizQuestion: fields<QuizQuestion>()(["id","type","prompt","options","horizonBars","referencePrice","upperLevel","lowerLevel"]),
  QuizResult: fields<QuizResult>()(["questionId","type","answer","grade","correctAnswer","detail","answeredAtCursor","revealedToCursor"]),
  QuizScore: fields<QuizScore>()(["asked","correct","incorrect","void","accuracy","label"]),
  ReplayState: fields<ReplayState>()(["id","mode","timeframe","label","cursor","start","canStepBack","atEnd","ended","candles","analysis","guidance","quiz","lastResult","history","score","masked","authority","strategyVersion"]),
  ReplayReveal: fields<ReplayReveal>()(["id","symbol","start","cursor","priceScale","weekShift"]),
  ReplaySessionRow: fields<ReplaySessionRow>()(["id","mode","timeframe","label","cursor","ended","score"]),
  ReplayStatus: fields<ReplayStatus>()(["sessions","maxSessions","idleTtlHours","storage","generatedAt"]),
  EventView: fields<EventView>()(["id","country","currency","name","scheduledTime","importance","status","actual","forecast","previous","revisedPrevious","surprise","surprisePct","blackoutStart","blackoutEnd","minutesToEvent"]),
  CalendarInfo: fields<CalendarInfo>()(["provider","source","isSynthetic","available","fetchedAt","coverageStart","coverageEnd","reason"]),
  NewsAssessment: fields<NewsAssessment>()(["symbol","state","relevantCurrencies","activeEvent","nextEvent","windowStart","windowEnd","events","calendar","blockers","warnings","strategyVersion","generatedAt"]),
  CalendarResponse: fields<CalendarResponse>()(["events","calendar","generatedAt"]),
});

const here = dirname(fileURLToPath(import.meta.url));
const contract = JSON.parse(readFileSync(resolve(here, "../contract/api_fields.json"), "utf-8")) as Record<
  string,
  string[]
>;

describe("API field contract", () => {
  for (const [name, keys] of Object.entries(TS_FIELDS)) {
    it(`${name} TS interface matches api_fields.json`, () => {
      expect([...keys].sort()).toEqual([...(contract[name] ?? [])].sort());
    });
  }
});

describe("chart timeframe contract", () => {
  it("CHART_TIMEFRAMES matches api_fields.json", () => {
    expect([...CHART_TIMEFRAMES]).toEqual(contract.ChartTimeframes);
  });
});
