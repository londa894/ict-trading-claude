// @vitest-environment jsdom
import type { QuizResult, ReplayState } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReplayView, type ReplayApi } from "../src/components/ReplayView";
import { viewForHash } from "../src/components/MarketsViews";
import { answerReplay, createReplay, reconcileReplay, replayRequest, stepReplay } from "../src/lib/replay";

vi.mock("../src/components/CandleChart", () => ({
  CandleChart: ({ candles }: { candles: unknown[] }) => <div data-testid="candle-chart">{candles.length} candles</div>,
}));

afterEach(cleanup);

const CURSOR = "2024-04-17T15:00:00Z";
const bar = (time: string, close = 2000) => ({ time, open: 2000, high: 2001, low: 1999, close, volume: null, isClosed: true });

function state(over: Partial<ReplayState> = {}): ReplayState {
  return {
    id: "11111111-1111-1111-1111-111111111111", mode: "MANUAL", timeframe: "M15", label: "XAUUSD", cursor: CURSOR, start: CURSOR,
    canStepBack: true, atEnd: false, ended: false, candles: [bar("2024-04-17T14:30:00Z"), bar("2024-04-17T14:45:00Z")],
    analysis: {
      eligibleForDecision: false, ineligibility: ["DATA_SYNTHETIC"], setupState: null, currentSetup: null, newYorkTime: "11:00",
      activeSessions: ["NEW_YORK"], timeQuality: "PRIME", authority: "EDUCATION_ONLY",
    },
    guidance: null, quiz: null, lastResult: null, history: [], score: null, masked: false, authority: "EDUCATION_ONLY", strategyVersion: "0.19.0-phase19",
    ...over,
  };
}

const result = (over: Partial<QuizResult> = {}): QuizResult => ({
  questionId: "Q1", type: "NEXT_BARS_DIRECTION", answer: "UP", grade: "CORRECT", correctAnswer: "UP", detail: "close moved up",
  answeredAtCursor: CURSOR, revealedToCursor: CURSOR, ...over,
});

const question = { id: "Q1", type: "NEXT_BARS_DIRECTION" as const, prompt: "Where will price close in 6 bars?", options: ["UP", "DOWN", "SKIP"], horizonBars: 6, referencePrice: 2000, upperLevel: null, lowerLevel: null };
const quizState = (over: Partial<ReplayState> = {}) =>
  state({ mode: "QUIZ", canStepBack: false, analysis: null, quiz: question, score: { asked: 0, correct: 0, incorrect: 0, void: 0, accuracy: null, label: "INSUFFICIENT" }, ...over });
const blindState = (over: Partial<ReplayState> = {}) =>
  state({ mode: "BLIND", label: "Hidden instrument", masked: true, analysis: null, canStepBack: false, ...over });

describe("replay trust rules", () => {
  it("accepts well-formed manual, quiz and blind sessions", () => {
    expect(reconcileReplay(state()).status).toBe("READY");
    expect(reconcileReplay(quizState()).status).toBe("READY");
    expect(reconcileReplay(blindState()).status).toBe("READY");
    const answered = quizState({ lastResult: result(), history: [result()], score: { asked: 1, correct: 1, incorrect: 0, void: 0, accuracy: 1, label: "INSUFFICIENT" }, analysis: state().analysis });
    expect(reconcileReplay(answered).status).toBe("READY");
  });

  it.each([
    ["future candle", state({ candles: [bar("2024-04-17T14:50:00Z")] }), "past the cursor"],
    ["open candle", state({ candles: [{ ...bar("2024-04-17T14:30:00Z"), isClosed: false }] }), "open or malformed"],
    ["authority", state({ authority: "LIVE" }), "claimed authority"],
    ["blind leaks symbol", blindState({ label: "XAUUSD" }), "leaked"],
    ["blind leaks engine", blindState({ analysis: state().analysis }), "leaked"],
    ["mask on manual", state({ masked: true }), "non-blind"],
    ["quiz engine before answer", quizState({ analysis: state().analysis }), "before the first answer"],
    ["score mismatch", quizState({ history: [result()], lastResult: result(), analysis: null }), "score does not match"],
    ["quiz data on manual", state({ history: [result()] }), "non-quiz"],
    ["unknown mode", state({ mode: "LIVE" as never }), "mode unknown"],
  ])("rejects %s", (_name, payload, reason) => {
    const r = reconcileReplay(payload);
    expect(r.status).toBe("UNAVAILABLE");
    expect(r.status === "UNAVAILABLE" && r.reason).toContain(reason);
  });

  it("builds requests and refuses a future start", () => {
    const now = new Date("2024-04-18T15:01:00Z");
    expect(replayRequest({ symbol: "XAUUSD", mode: "QUIZ", timeframe: "M15", start: "2024-04-17T15:01:00Z" }, now)).toEqual({
      body: { symbol: "XAUUSD", mode: "QUIZ", timeframe: "M15", start: "2024-04-17T15:01:00.000Z" },
    });
    expect(replayRequest({ symbol: "XAUUSD", mode: "QUIZ", timeframe: "M15", start: "2024-04-19T00:00:00Z" }, now)).toEqual({ error: "Start must be in the past" });
    expect(replayRequest({ symbol: "XAUUSD", mode: "QUIZ", timeframe: "M15", start: "" }, now)).toEqual({ error: "Start time is required" });
  });

  it("calls the replay routes and surfaces API errors", async () => {
    const fetcher = vi.fn(async (_url: string) => new Response(JSON.stringify(state()), { status: 201 }));
    expect((await createReplay({ symbol: "XAUUSD", mode: "MANUAL", timeframe: "M15", start: CURSOR }, fetcher as unknown as typeof fetch)).status).toBe("READY");
    expect(fetcher.mock.calls[0]![0]).toMatch(/\/api\/v1\/replay\/sessions$/);
    const refused = vi.fn(async () => new Response(JSON.stringify({ detail: "answer the open quiz question first" }), { status: 422 }));
    expect(await stepReplay("x", 1, refused as typeof fetch)).toEqual({ status: "UNAVAILABLE", reason: "answer the open quiz question first" });
    const lying = vi.fn(async () => new Response(JSON.stringify(state({ candles: [bar("2024-04-17T15:00:00Z")] })), { status: 200 }));
    expect((await answerReplay("x", "Q1", "UP", lying as typeof fetch)).status).toBe("UNAVAILABLE");
    const down = vi.fn(async () => {
      throw new Error("offline");
    });
    expect(await stepReplay("x", 1, down as typeof fetch)).toEqual({ status: "UNAVAILABLE", reason: "Replay API unreachable" });
  });
});

function fakeApi(over: Partial<ReplayApi> = {}): ReplayApi {
  return {
    create: vi.fn(async () => ({ status: "READY" as const, state: state() })),
    step: vi.fn(async () => ({ status: "READY" as const, state: state({ cursor: "2024-04-17T15:15:00Z" }) })),
    answer: vi.fn(async () => ({ status: "READY" as const, state: state() })),
    load: vi.fn(async () => ({ status: "READY" as const, state: state({ ended: true }) })),
    end: vi.fn(async () => ({ ok: true as const, reveal: { id: "1", symbol: "XAUUSD", start: CURSOR, cursor: CURSOR, priceScale: 0.87, weekShift: 900 } })),
    ...over,
  };
}

describe("ReplayView", () => {
  it("is reachable from #replay", () => {
    expect(viewForHash("#replay")).toBe("REPLAY");
  });

  it("starts a manual session, shows the engine view and steps", async () => {
    const api = fakeApi();
    render(<ReplayView symbols={["XAUUSD", "EURUSD"]} activeSymbol="XAUUSD" api={api} />);
    fireEvent.click(screen.getByText("Start replay"));
    await screen.findByTestId("replay-session");
    expect(screen.getByTestId("candle-chart").textContent).toBe("2 candles");
    expect(screen.getByTestId("replay-analysis").textContent).toContain("not eligible (DATA_SYNTHETIC)");
    fireEvent.click(screen.getByText("4 bars ▶"));
    await waitFor(() => expect(api.step).toHaveBeenCalledWith(expect.any(String), 4));
    fireEvent.click(screen.getByText("◀ 1 bar"));
    await waitFor(() => expect(api.step).toHaveBeenCalledWith(expect.any(String), -1));
  });

  it("quiz mode hides stepping, answers and shows the score", async () => {
    const answered = quizState({
      quiz: { ...question, id: "Q2" }, lastResult: result({ grade: "INCORRECT", answer: "DOWN" }), history: [result({ grade: "INCORRECT", answer: "DOWN" })],
      score: { asked: 1, correct: 0, incorrect: 1, void: 0, accuracy: 0, label: "INSUFFICIENT" }, analysis: state().analysis,
    });
    const api = fakeApi({ create: vi.fn(async () => ({ status: "READY" as const, state: quizState() })), answer: vi.fn(async () => ({ status: "READY" as const, state: answered })) });
    render(<ReplayView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={api} />);
    fireEvent.click(screen.getByText("Start replay"));
    await screen.findByTestId("replay-quiz");
    expect(screen.queryByText("4 bars ▶")).toBeNull();
    expect(screen.queryByTestId("replay-analysis")).toBeNull();
    fireEvent.click(screen.getByText("DOWN"));
    await screen.findByTestId("replay-result");
    expect(api.answer).toHaveBeenCalledWith(expect.any(String), "Q1", "DOWN");
    expect(screen.getByTestId("replay-result").textContent).toContain("INCORRECT");
    expect(screen.getByTestId("replay-score").textContent).toContain("0/1 correct (0%)");
  });

  it("blind mode reveals the instrument only on end", async () => {
    const api = fakeApi({ create: vi.fn(async () => ({ status: "READY" as const, state: blindState() })) });
    render(<ReplayView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={api} />);
    fireEvent.click(screen.getByText("Start replay"));
    await screen.findByTestId("replay-blind-note");
    expect(screen.getByTestId("replay-session").textContent).not.toContain("XAUUSD");
    expect(screen.queryByText("◀ 1 bar")).toBeNull();
    fireEvent.click(screen.getByText("End session and reveal"));
    const reveal = await screen.findByTestId("replay-reveal");
    expect(reveal.textContent).toContain("XAUUSD");
    expect(reveal.textContent).toContain("900 weeks");
  });

  it("shows a rejected or failed response instead of stale data", async () => {
    const api = fakeApi({ create: vi.fn(async () => ({ status: "UNAVAILABLE" as const, reason: "Rejected replay: a candle extends past the cursor (future shown)" })) });
    render(<ReplayView symbols={["XAUUSD"]} activeSymbol="XAUUSD" api={api} />);
    fireEvent.click(screen.getByText("Start replay"));
    expect((await screen.findByTestId("replay-error")).textContent).toContain("future shown");
    expect(screen.queryByTestId("replay-session")).toBeNull();
  });
});
