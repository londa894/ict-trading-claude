/**
 * Analytics client (Phase 17). Statistics describe recorded history only. The client refuses a report that claims
 * authority, labels a sample differently from the spec thresholds, names a "best" group below the minimum label,
 * or whose counts do not add up, so a misleading summary is never shown.
 */
import { SAMPLE_SIZE_LABELS, type AnalyticsReport, type AnalyticsSource, type GroupStats, type SampleSizeLabel } from "@fmcc/shared-types";

import { API_BASE_URL } from "./api";

export type AnalyticsState = { status: "READY"; report: AnalyticsReport } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Spec STEP 10 sample-size labels. */
export function sampleLabel(count: number): SampleSizeLabel {
  if (count >= 300) return "STRONGER_EVIDENCE";
  if (count >= 100) return "MODERATE";
  if (count >= 30) return "LIMITED";
  return "INSUFFICIENT";
}

function groupError(g: unknown): string | null {
  if (!isObject(g) || typeof g.count !== "number" || typeof g.wins !== "number" || typeof g.losses !== "number" || typeof g.breakeven !== "number") {
    return "group malformed";
  }
  if (g.wins + g.losses + g.breakeven !== g.count || g.wins < 0 || g.losses < 0 || g.breakeven < 0) return "group counts do not add up";
  if (!(SAMPLE_SIZE_LABELS as readonly string[]).includes(String(g.label)) || g.label !== sampleLabel(g.count)) return "sample label does not match the count";
  if (g.count === 0 ? g.winRate !== null : typeof g.winRate !== "number" || Math.abs(g.winRate - g.wins / g.count) > 1e-3) {
    return "win rate does not match the counts";
  }
  if (typeof g.profitFactor === "number" && g.profitFactor < 0) return "negative profit factor";
  return null;
}

export function analyticsError(r: unknown): string | null {
  if (!isObject(r)) return "analytics payload malformed";
  if (r.authority !== "DESCRIPTIVE_ONLY") return "analytics claimed authority";
  if (typeof r.available !== "boolean" || typeof r.disclaimer !== "string" || r.disclaimer.length === 0) return "disclaimer missing";
  const overall = groupError(r.overall);
  if (overall) return `overall: ${overall}`;
  if (!Array.isArray(r.breakdowns)) return "breakdowns missing";
  const order = SAMPLE_SIZE_LABELS as readonly string[];
  for (const b of r.breakdowns) {
    if (!isObject(b) || !Array.isArray(b.groups)) return "breakdown malformed";
    for (const g of b.groups) {
      const err = groupError(g);
      if (err) return `${String(b.dimension)}: ${err}`;
    }
    if (b.best !== null) {
      const best = (b.groups as GroupStats[]).find((g) => g.key === b.best);
      if (!best || order.indexOf(best.label) < order.indexOf("LIMITED")) return `${String(b.dimension)}: best group named on an insufficient sample`;
    }
  }
  if (!r.available && isObject(r.overall) && r.overall.count !== 0) return "statistics from an unavailable store";
  return null;
}

export function reconcileAnalytics(payload: unknown): AnalyticsState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "Analytics API unreachable" };
  const err = analyticsError(payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected analytics: ${err}` } : { status: "READY", report: payload as AnalyticsReport };
}

export async function loadAnalytics(
  params: { source: AnalyticsSource; symbol?: string; includeSynthetic?: boolean },
  fetcher: typeof fetch = fetch,
): Promise<AnalyticsState> {
  const q = new URLSearchParams({ source: params.source });
  if (params.symbol) q.set("symbol", params.symbol);
  if (params.includeSynthetic) q.set("includeSynthetic", "true");
  try {
    const res = await fetcher(`${API_BASE_URL}/api/v1/analytics?${q}`, { cache: "no-store" });
    if (!res.ok) return { status: "UNAVAILABLE", reason: `Analytics HTTP ${res.status}` };
    return reconcileAnalytics((await res.json()) as unknown);
  } catch {
    return { status: "UNAVAILABLE", reason: "Analytics API unreachable" };
  }
}

export const pct = (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`);
export const rr = (v: number | null) => (v === null ? "—" : `${v > 0 ? "+" : ""}${v}R`);
