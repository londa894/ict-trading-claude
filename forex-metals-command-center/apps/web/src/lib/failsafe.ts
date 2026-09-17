/**
 * Client-side fail-safe. The UI never trusts a payload it cannot verify: anything malformed,
 * mismatched, unreachable or inconsistent with the backend's verdict authority renders UNAVAILABLE.
 * This is not trading logic — it only refuses to display a decision it cannot trust.
 */
import {
  BLOCKERS,
  DATA_QUALITIES,
  VERDICTS,
  type Blocker,
  type DataReport,
  type MasterDecision,
  type Verdict,
  type VerdictAuthority,
} from "@fmcc/shared-types";

const DIRECTIONAL: readonly Verdict[] = ["LONG", "SHORT"];
const UNUSABLE_DATA_BLOCKERS: readonly Blocker[] = [
  "SYSTEM_INTEGRITY_FAILURE",
  "UNKNOWN_SYMBOL",
  "PROVIDER_UNAVAILABLE",
  "NO_DATA",
  "DATA_INVALID",
  "DATA_STALE",
  "DATA_DISCONNECTED",
  "DATA_SYNTHETIC",
];

export function unavailableDecision(
  symbol: string,
  blockers: Blocker[],
  nextRequiredEvent: string,
  now: Date,
  strategyVersion = "UNKNOWN",
): MasterDecision {
  return {
    symbol: symbol.toUpperCase(),
    verdict: "UNAVAILABLE",
    direction: null,
    setupType: null,
    setupState: "NOT_EVALUATED",
    setupGrade: null,
    setupScore: null,
    decisionConfidence: "LOW",
    htfBias: "UNKNOWN",
    primaryDol: null,
    secondaryDol: null,
    liquidityEvent: null,
    structureEvent: null,
    displacement: null,
    pdArray: null,
    noWickState: null,
    sessionState: null,
    macroState: null,
    newsState: null,
    entryZone: null,
    preferredEntry: null,
    stop: null,
    tp1: null,
    tp2: null,
    tp3: null,
    rr: null,
    riskStatus: "NOT_EVALUATED",
    blockers,
    nextRequiredEvent,
    invalidation: null,
    dataQuality: "DISCONNECTED",
    strategyVersion,
    updatedAt: now.toISOString(),
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMember<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

export function isMasterDecision(value: unknown): value is MasterDecision {
  if (!isObject(value)) return false;
  return (
    typeof value.symbol === "string" &&
    isMember(VERDICTS, value.verdict) &&
    isMember(DATA_QUALITIES, value.dataQuality) &&
    Array.isArray(value.blockers) &&
    value.blockers.every((b) => isMember(BLOCKERS, b)) &&
    typeof value.strategyVersion === "string" &&
    typeof value.updatedAt === "string"
  );
}

export type ReconciledState = {
  decision: MasterDecision;
  data: DataReport | null;
  trusted: boolean;
};

/**
 * @param authority verdict authority reported by /system/status, or null if unknown.
 */
export function reconcileMarketState(
  requestedSymbol: string,
  payload: unknown,
  authority: VerdictAuthority | null,
  now: Date,
): ReconciledState {
  const symbol = requestedSymbol.toUpperCase();
  const integrityFailure = (why: string): ReconciledState => ({
    decision: unavailableDecision(symbol, ["SYSTEM_INTEGRITY_FAILURE"], why, now),
    data: null,
    trusted: false,
  });

  if (payload === null || payload === undefined) {
    return {
      decision: unavailableDecision(symbol, ["PROVIDER_UNAVAILABLE"], "API unreachable", now),
      data: null,
      trusted: false,
    };
  }
  if (!isObject(payload) || !isMasterDecision(payload.decision)) {
    return integrityFailure("Malformed decision payload");
  }
  const decision = payload.decision;
  const data = isObject(payload.data) ? (payload.data as unknown as DataReport) : null;

  // Asset contexts must stay isolated.
  if (decision.symbol.toUpperCase() !== symbol) {
    return integrityFailure(`Decision symbol ${decision.symbol} does not match ${symbol}`);
  }
  const directional = decision.verdict !== "WAIT" && decision.verdict !== "UNAVAILABLE";
  if (directional && authority !== "FULL") {
    return integrityFailure(`Verdict ${decision.verdict} not permitted under authority ${authority ?? "UNKNOWN"}`);
  }
  if (DIRECTIONAL.includes(decision.verdict) && decision.blockers.some((b) => UNUSABLE_DATA_BLOCKERS.includes(b))) {
    return integrityFailure("Directional verdict alongside unusable-data blocker");
  }
  return { decision, data, trusted: true };
}
