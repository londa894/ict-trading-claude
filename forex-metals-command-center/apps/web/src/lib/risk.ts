/**
 * Risk assessment validation and the what-if calculator client. Risk has final veto; nothing here authorizes a
 * trade, and nothing the user types into the calculator is stored (no localStorage, no server persistence).
 */
import {
  BLOCKERS,
  DIRECTIONS,
  NEWS_STATES,
  POSITION_SIZE_STATUSES,
  RISK_LOCKS,
  RISK_PROFILE_NAMES,
  RISK_STATUSES,
  RISK_WARNINGS,
  type Direction,
  type RiskAssessment,
  type RiskCalculationRequest,
  type RiskProfileName,
} from "@fmcc/shared-types";

export type RiskLoadState = { status: "READY"; risk: RiskAssessment } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);
const allMembers = <T extends string>(values: readonly T[], v: unknown): boolean =>
  Array.isArray(v) && v.every((x) => member(values, x));
const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const MONEY_EPS = 0.011;

/** Returns why a risk payload cannot be trusted, or null. */
export function riskError(symbol: string, r: unknown): string | null {
  if (!isObject(r)) return "risk payload malformed";
  if (r.symbol !== symbol.toUpperCase()) return "risk symbol mismatch";
  if (r.authority !== "NOT_AUTHORIZED") return "risk claims authority";
  if (!member(RISK_STATUSES, r.status)) return "risk status unknown";
  if (r.news !== "NOT_EVALUATED" && !member(NEWS_STATES, r.news)) return "news state unknown";
  if (!Array.isArray(r.locks) || !r.locks.every((l) => isObject(l) && member(RISK_LOCKS, l.lock))) return "risk lock unknown";
  if (!allMembers(RISK_WARNINGS, r.warnings) || !allMembers(BLOCKERS, r.blockers)) return "risk warning or blocker unknown";
  if (r.sizeStatus !== null && !member(POSITION_SIZE_STATUSES, r.sizeStatus)) return "size status unknown";
  const blockers = r.blockers as string[];
  if ((r.status === "LOCKED") !== r.locks.length > 0) return "locks do not match the status";
  if (r.status === "LOCKED" && !blockers.includes("RISK_LOCKED")) return "a lock without the RISK_LOCKED blocker";
  const unconfigured: Record<string, string> = {
    NOT_CONFIGURED: "RISK_PROFILE_MISSING",
    INVALID_PROFILE: "RISK_PROFILE_INVALID",
    UNAVAILABLE: "RISK_GATE_MISSING",
  };
  const required = unconfigured[r.status];
  if (required) {
    if (!blockers.includes(required) || r.position !== null || r.budget !== null) return "an unassessed risk carries values";
    return null;
  }
  if (!isObject(r.limits) || !isObject(r.budget)) return "limits or budget missing";
  const budget = r.budget;
  if (!num(budget.effectiveRiskAmount) || !num(budget.riskPerTradeAmount) || budget.effectiveRiskAmount < 0) {
    return "budget invalid";
  }
  if (budget.effectiveRiskAmount > budget.riskPerTradeAmount + MONEY_EPS) return "effective risk above the per-trade amount";
  const p = r.position;
  if (r.status === "CLEAR" && p !== null) return "a position without a plan";
  if (r.status === "SIZE_UNVERIFIED" && !(isObject(p) && p.volume === null && blockers.includes("POSITION_SIZE_UNVERIFIED"))) {
    return "an unverified size carries a volume";
  }
  if (r.status === "WITHIN_LIMITS") {
    if (!isObject(p) || !member(DIRECTIONS, p.direction) || !num(p.volume) || p.volume <= 0 || !num(p.riskAmount)) {
      return "a sized position is incomplete";
    }
    if (r.sizeStatus !== "VERIFIED" && r.sizeStatus !== "SIZED_FROM_USER_SPEC") return "sized without a spec";
    if (p.riskAmount > budget.effectiveRiskAmount + MONEY_EPS) return "sized risk above the budget";
    const limit = (r.limits as { riskPerTradePct?: unknown }).riskPerTradePct;
    if (!num(limit) || !num(p.riskPct) || p.riskPct > limit + 1e-6) return "sized risk above the per-trade limit";
  }
  return null;
}

export function reconcileRisk(symbol: string, payload: unknown): RiskLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Risk API unreachable" };
  const err = riskError(symbol, payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected risk assessment: ${err}` } : { status: "READY", risk: payload as RiskAssessment };
}

// --- what-if calculator ------------------------------------------------------------------------------------

export type CalculatorForm = {
  direction: Direction;
  entry: string;
  stop: string;
  balance: string;
  currency: string;
  profile: RiskProfileName;
  leverage: string;
  contractSize: string;
  tickSize: string;
  tickValue: string;
  minVolume: string;
  volumeStep: string;
  quoteCurrency: string;
  typicalSpread: string;
  platformPipSize: string;
  conversionRate: string;
};

/** Empty on purpose: contract specs are broker specific and are never pre-filled (never guessed). */
export const EMPTY_FORM: CalculatorForm = {
  direction: "BULLISH",
  entry: "",
  stop: "",
  balance: "",
  currency: "USD",
  profile: "STANDARD",
  leverage: "",
  contractSize: "",
  tickSize: "",
  tickValue: "",
  minVolume: "",
  volumeStep: "",
  quoteCurrency: "USD",
  typicalSpread: "",
  platformPipSize: "",
  conversionRate: "",
};

const SPEC_FIELDS = ["contractSize", "tickSize", "tickValue", "minVolume", "volumeStep"] as const;

function positive(raw: string): number | null {
  if (raw.trim() === "") return null;
  const v = Number(raw);
  return Number.isFinite(v) && v > 0 ? v : Number.NaN;
}

export function buildCalculationRequest(
  symbol: string,
  f: CalculatorForm,
): { ok: true; request: RiskCalculationRequest } | { ok: false; error: string } {
  const entry = positive(f.entry);
  const stop = positive(f.stop);
  const balance = positive(f.balance);
  if (entry === null || stop === null || balance === null) return { ok: false, error: "Entry, stop and balance are required" };
  if ([entry, stop, balance].some(Number.isNaN)) return { ok: false, error: "Entry, stop and balance must be positive numbers" };
  if (!/^[A-Z]{3}$/.test(f.currency) || !/^[A-Z]{3}$/.test(f.quoteCurrency)) return { ok: false, error: "Currencies are 3-letter codes" };
  if (!member(RISK_PROFILE_NAMES, f.profile) || f.profile === "CUSTOM") {
    return { ok: false, error: "Choose a preset profile (CUSTOM limits live in the server-side profile file)" };
  }
  const optional = (raw: string, name: string): number | null | string => {
    const v = positive(raw);
    return Number.isNaN(v) ? `${name} must be a positive number` : v;
  };
  const leverage = optional(f.leverage, "Leverage");
  const conversionRate = optional(f.conversionRate, "Conversion rate");
  const platformPipSize = optional(f.platformPipSize, "Pip size");
  const spreadRaw = f.typicalSpread.trim();
  const typicalSpread = spreadRaw === "" ? null : Number(spreadRaw);
  for (const v of [leverage, conversionRate, platformPipSize]) if (typeof v === "string") return { ok: false, error: v };
  if (typicalSpread !== null && !(Number.isFinite(typicalSpread) && typicalSpread >= 0)) {
    return { ok: false, error: "Spread must be zero or a positive number" };
  }
  const specValues = SPEC_FIELDS.map((k) => positive(f[k]));
  let instrumentSpec: RiskCalculationRequest["instrumentSpec"] = null;
  if (specValues.every((v) => v !== null)) {
    if (specValues.some((v) => Number.isNaN(v))) return { ok: false, error: "Contract spec values must be positive numbers" };
    const [contractSize, tickSize, tickValue, minVolume, volumeStep] = specValues as number[];
    instrumentSpec = {
      symbol: symbol.toUpperCase(),
      contractSize: contractSize!,
      tickSize: tickSize!,
      tickValue: tickValue!,
      minVolume: minVolume!,
      volumeStep: volumeStep!,
      quoteCurrency: f.quoteCurrency,
      typicalSpread,
      platformPipSize: platformPipSize as number | null,
    };
  } else if (specValues.some((v) => v !== null)) {
    return { ok: false, error: "Fill every contract spec field or leave them all empty (size stays unverified)" };
  }
  return {
    ok: true,
    request: {
      symbol: symbol.toUpperCase(),
      direction: f.direction,
      entry,
      stop,
      account: { balance, currency: f.currency, profile: f.profile, leverage: leverage as number | null },
      state: null,
      instrumentSpec,
      conversionRate: conversionRate as number | null,
    },
  };
}

const TIMEOUT_MS = 5000;

export async function calculateRisk(
  baseUrl: string,
  request: RiskCalculationRequest,
  fetcher: typeof fetch = fetch,
): Promise<RiskLoadState> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetcher(`${baseUrl}/api/v1/risk/calculate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      return { status: "UNAVAILABLE", reason: `Risk calculator rejected the request (HTTP ${res.status})` };
    }
    return reconcileRisk(request.symbol, (await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Risk API unreachable" };
  } finally {
    clearTimeout(timer);
  }
}

export function money(amount: number | null | undefined, currency: string | null): string {
  return amount === null || amount === undefined ? "—" : `${amount.toFixed(2)} ${currency ?? ""}`.trim();
}
