// @vitest-environment jsdom
import type { AssistantAnswer, AssistantCapabilities } from "@fmcc/shared-types";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiAnalysisTab } from "../src/components/AiAnalysisTab";
import { IntelligencePanel } from "../src/components/IntelligencePanel";
import { askAssistant, reconcileAnswer, reconcileCapabilities, TRADE_INSTRUCTION } from "../src/lib/assistant";
import { unavailableDecision } from "../src/lib/failsafe";

afterEach(cleanup);

const T = "2024-04-18T15:01:00Z";

function answer(over: Partial<AssistantAnswer> = {}): AssistantAnswer {
  return {
    symbol: "XAUUSD", question: "Where is liquidity?", intent: "LIQUIDITY", level: "INTERMEDIATE",
    answer: "Master Decision primary DOL (H1): H1 BSL EQH x3 @ 2050.91.",
    facts: [{ label: "Decision primary DOL (H1)", value: "H1 BSL EQH x3 @ 2050.91", source: "get_market_state" }],
    unknowns: [], tools: [{ name: "get_market_state", symbol: "XAUUSD", status: "OK", detail: null }],
    decision: { symbol: "XAUUSD", verdict: "WAIT", dataQuality: "CURRENT", blockers: ["ANALYSIS_GATES_NOT_IMPLEMENTED"], strategyVersion: "0.20.0-phase20", updatedAt: T },
    proposal: null, provider: "DETERMINISTIC", model: null, guard: { status: "NOT_APPLICABLE", violations: [] },
    authority: "NOT_AUTHORIZED", strategyVersion: "0.20.0-phase20", generatedAt: T, ...over,
  };
}

const caps: AssistantCapabilities = {
  provider: "DETERMINISTIC", model: null, externalAiConfigured: false, sharesAccountData: false, commands: ["Analyze Gold"],
  levels: ["BEGINNER", "INTERMEDIATE", "ADVANCED", "PROFESSIONAL"], tools: [], authority: "NOT_AUTHORIZED",
};

describe("reconcileAnswer", () => {
  it("accepts deterministic, passed external and fallback answers", () => {
    expect(reconcileAnswer("xauusd", answer()).status).toBe("READY");
    expect(reconcileAnswer("XAUUSD", answer({ provider: "ANTHROPIC", model: "claude-sonnet-5", guard: { status: "PASSED", violations: [] } })).status).toBe("READY");
    expect(reconcileAnswer("XAUUSD", answer({ guard: { status: "FALLBACK", violations: ["value 2099.99 is not in the engine state"] } })).status).toBe("READY");
  });

  const a = answer();
  it.each([
    ["unreachable", null],
    ["authority claimed", { ...a, authority: "AUTHORIZED" }],
    ["another market", { ...a, symbol: "EURUSD" }],
    ["external answer without a passed guard", { ...a, provider: "ANTHROPIC", guard: { status: "FALLBACK", violations: ["x"] } }],
    ["decision for another market", { ...a, decision: { ...a.decision!, symbol: "EURUSD" } }],
    ["a tool that read another market", { ...a, tools: [{ name: "get_liquidity", symbol: "EURUSD", status: "OK", detail: null }] }],
    ["a trade instruction", { ...a, answer: "Verdict WAIT but you should buy the dip." }],
    ["unknown intent", { ...a, intent: "EXECUTE" }],
    ["proposal for another market", { ...a, proposal: { symbol: "EURUSD", direction: null, reason: "" } }],
  ])("rejects %s", (_n, payload) => {
    expect(reconcileAnswer("XAUUSD", payload).status).toBe("UNAVAILABLE");
  });

  it("recognises instructions but not explanations", () => {
    expect(TRADE_INSTRUCTION.test("Go long above 2050")).toBe(true);
    expect(TRADE_INSTRUCTION.test("It is not a buy because the verdict is WAIT")).toBe(false);
    expect(reconcileCapabilities(caps)).not.toBeNull();
    expect(reconcileCapabilities({ ...caps, authority: "FULL" })).toBeNull();
  });
});

describe("askAssistant", () => {
  it("posts the question and validates the answer", async () => {
    let body: unknown = null;
    const fetcher = (async (_u: string, init: RequestInit) => {
      body = JSON.parse(String(init.body));
      return new Response(JSON.stringify(answer()), { status: 200 });
    }) as unknown as typeof fetch;
    expect((await askAssistant("http://api", "xauusd", "Where is liquidity?", "BEGINNER", fetcher)).status).toBe("READY");
    expect(body).toEqual({ symbol: "XAUUSD", question: "Where is liquidity?", level: "BEGINNER" });
    const rejected = (async () => new Response("{}", { status: 422 })) as unknown as typeof fetch;
    expect(await askAssistant("http://api", "XAUUSD", "x", "BEGINNER", rejected)).toEqual({
      status: "UNAVAILABLE", reason: "Assistant rejected the question (HTTP 422)",
    });
  });
});

function routes(result: AssistantAnswer | object, created: string[] = []) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/api/v1/assistant/capabilities")) return new Response(JSON.stringify(caps), { status: 200 });
    if (url.endsWith("/api/v1/alerts/ready-watches")) {
      created.push(String(init?.body));
      return new Response("{}", { status: 201 });
    }
    return new Response(JSON.stringify(result), { status: 200 });
  }) as unknown as typeof fetch;
}

describe("AI_ANALYSIS tab", () => {
  it("is enabled in the panel and shows the scope disclaimer", () => {
    const decision = unavailableDecision("XAUUSD", ["DATA_SYNTHETIC"], "x", new Date(T), "0.20.0-phase20");
    render(<IntelligencePanel decision={decision} data={null} status={null} trusted={false} />);
    fireEvent.click(screen.getByRole("tab", { name: "AI_ANALYSIS" }));
    expect(screen.queryByTestId("tab-not-available")).toBeNull();
    expect(screen.getByTestId("ai-disclaimer").textContent).toContain("never creates prices, verdicts or trade instructions");
  });

  it("asks, shows the answer with facts, guard and unknowns", async () => {
    const fetcher = routes(answer({ guard: { status: "FALLBACK", violations: ["value 2099.99 is not in the engine state"] }, unknowns: ["Score history: UNKNOWN"] }));
    render(<AiAnalysisTab symbol="XAUUSD" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId("ai-provider").textContent).toContain("Deterministic explainer"));
    fireEvent.change(screen.getByLabelText("Education level"), { target: { value: "BEGINNER" } });
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Where is liquidity?" } });
    fireEvent.submit(screen.getByRole("button", { name: "Ask" }).closest("form")!);
    await waitFor(() => expect(screen.getByTestId("ai-answer")).toBeTruthy());
    expect(screen.getByTestId("ai-facts").textContent).toContain("H1 BSL EQH x3 @ 2050.91");
    expect(screen.getByTestId("ai-guard").textContent).toContain("2099.99");
    expect(screen.getByTestId("ai-unknowns").textContent).toContain("Score history");
    expect(screen.getByTestId("ai-meta").textContent).toContain("decision WAIT");
  });

  it("rejects an untrusted answer and only creates a watch when the user confirms", async () => {
    render(<AiAnalysisTab symbol="XAUUSD" fetcher={routes({ ...answer(), symbol: "EURUSD" })} />);
    fireEvent.click(screen.getByRole("button", { name: "Analyze Gold" }));
    await waitFor(() => expect(screen.getByTestId("ai-unavailable").textContent).toContain("another market"));
    cleanup();

    const created: string[] = [];
    const proposal = answer({ intent: "CREATE_ALERT", proposal: { symbol: "XAUUSD", direction: "BEARISH", reason: "Not executed" } });
    render(<AiAnalysisTab symbol="XAUUSD" fetcher={routes(proposal, created)} />);
    fireEvent.click(screen.getByRole("button", { name: "Why are we waiting?" }));
    await waitFor(() => expect(screen.getByTestId("ai-proposal")).toBeTruthy());
    expect(created).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm watch" }));
    await waitFor(() => expect(screen.getByTestId("ai-watch-message").textContent).toContain("watch created"));
    expect(JSON.parse(created[0]!)).toEqual({ symbol: "XAUUSD", direction: "BEARISH" });
  });
});
