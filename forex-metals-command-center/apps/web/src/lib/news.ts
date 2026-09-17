/**
 * News gate client (Phase 13). The client refuses a news payload that claims CLEAR without an available calendar,
 * a BLACKOUT without its blocker, a synthetic calendar without NEWS_DATA_SYNTHETIC, or events for other currencies.
 */
import {
  BLOCKERS,
  EVENT_IMPORTANCES,
  EVENT_STATUSES,
  NEWS_STATES,
  type MasterDecision,
  type NewsAssessment,
  type NewsState,
} from "@fmcc/shared-types";

import type { ChartState } from "./candles";
import type { Overlay, OverlayMarker } from "./structure";

export type NewsLoadState = { status: "READY"; news: NewsAssessment } | { status: "UNAVAILABLE"; reason: string };

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
const member = <T extends string>(values: readonly T[], v: unknown): v is T =>
  typeof v === "string" && (values as readonly string[]).includes(v);

const REQUIRED_BLOCKER: Partial<Record<NewsState, string>> = {
  BLACKOUT: "NEWS_BLACKOUT",
  POST_NEWS_WAIT: "NEWS_POST_WAIT",
  UNAVAILABLE: "NEWS_DATA_UNAVAILABLE",
};

export function newsError(symbol: string, n: unknown): string | null {
  if (!isObject(n)) return "news payload malformed";
  if (n.symbol !== symbol.toUpperCase()) return "news for another market";
  if (!member(NEWS_STATES, n.state)) return "news state unknown";
  if (!Array.isArray(n.blockers) || !n.blockers.every((b) => member(BLOCKERS, b))) return "news blocker unknown";
  const blockers = n.blockers as string[];
  const required = REQUIRED_BLOCKER[n.state];
  if (required && !blockers.includes(required)) return `${n.state} without ${required}`;
  const calendar = n.calendar;
  if (!isObject(calendar) || typeof calendar.available !== "boolean") return "calendar info missing";
  if ((n.state === "UNAVAILABLE") !== !calendar.available) return "calendar availability does not match the state";
  if (calendar.isSynthetic === true && !blockers.includes("NEWS_DATA_SYNTHETIC")) return "synthetic calendar not flagged";
  if (!Array.isArray(n.relevantCurrencies) || !Array.isArray(n.events)) return "news fields missing";
  const currencies = n.relevantCurrencies as string[];
  for (const e of n.events) {
    if (!isObject(e) || !member(EVENT_IMPORTANCES, e.importance) || !member(EVENT_STATUSES, e.status)) return "event invalid";
    if (!currencies.includes(String(e.currency))) return "event for an unrelated currency";
  }
  return null;
}

export function reconcileNews(symbol: string, payload: unknown): NewsLoadState {
  if (payload === null || payload === undefined) return { status: "UNAVAILABLE", reason: "News API unreachable" };
  const err = newsError(symbol, payload);
  return err ? { status: "UNAVAILABLE", reason: `Rejected news: ${err}` } : { status: "READY", news: payload as NewsAssessment };
}

export type NewsDecisionState = {
  state: NewsState;
  nextEvent: { name: string; currency: string; importance: string; scheduledTime: string; minutesToEvent: number } | null;
  activeEvent: { name: string; currency: string; importance: string; scheduledTime: string } | null;
  calendarAvailable: boolean;
  calendarSynthetic: boolean;
};

export function readNewsState(decision: MasterDecision): NewsDecisionState | null {
  const s: unknown = decision.newsState;
  if (!isObject(s) || !member(NEWS_STATES, s.state) || typeof s.calendarAvailable !== "boolean") return null;
  return s as unknown as NewsDecisionState;
}

/** Status-bar label: state plus the next relevant event countdown. */
export function newsLabel(s: NewsDecisionState | null): string {
  if (!s) return "UNAVAILABLE";
  const synthetic = s.calendarSynthetic ? " · synthetic" : "";
  if (s.state === "UNAVAILABLE") return `UNAVAILABLE${synthetic}`;
  const next = s.nextEvent ? ` · ${s.nextEvent.importance} ${s.nextEvent.currency} in ${Math.round(s.nextEvent.minutesToEvent)}m` : "";
  return `${s.state}${next}${synthetic}`;
}

const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** Markers for HIGH/EXTREME events inside the drawn candle range, snapped to the candle containing the event. */
export function buildNewsOverlay(news: NewsAssessment, chart: ChartState): Overlay {
  if (chart.status !== "READY" || chart.candles.length === 0) return { markers: [], segments: [], priceLines: [] };
  const times = chart.candles.map((c) => sec(c.time));
  const first = times[0]!;
  const last = times[times.length - 1]!;
  const markers: OverlayMarker[] = [];
  for (const e of news.events) {
    if (e.importance !== "HIGH" && e.importance !== "EXTREME") continue;
    const t = sec(e.scheduledTime);
    if (t < first || t > last + 3600) continue;
    const candle = [...times].reverse().find((x) => x <= t);
    if (candle === undefined) continue;
    markers.push({ time: candle, position: "aboveBar", shape: "circle", color: e.importance === "EXTREME" ? "#f85149" : "#d29922", text: `${e.currency} ${e.importance}`, size: 0.6 });
  }
  return { markers, segments: [], priceLines: [] };
}
