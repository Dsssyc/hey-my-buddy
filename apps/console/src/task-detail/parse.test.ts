import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { parseAudit, parseInquiries, parseTask, parseTokenUsage, parseWorkflow } from "./parse";

const usage = { inputTokens: 20000, cachedInputTokens: 15000, outputTokens: 800, source: "codex-native", scope: "attempt" };

describe("one execution's usage", () => {
  it("keeps the cached input inside the input and reads a partial record as it is", () => {
    expect(parseTokenUsage(usage)).toEqual(usage);
    expect(parseTokenUsage({ ...usage, inputTokens: null, cachedInputTokens: null })).toMatchObject({ inputTokens: null, cachedInputTokens: null, outputTokens: 800 });
    expect(parseTokenUsage({ ...usage, completeness: "partial" })).toMatchObject({ completeness: "partial" });
  });

  it("is unknown, never a number made from a record that is not one", () => {
    expect(parseTokenUsage(null)).toBeNull();
    expect(parseTokenUsage(undefined)).toBeNull();
    expect(parseTokenUsage({ ...usage, inputTokens: 1.5 })).toBeNull();
    expect(parseTokenUsage({ ...usage, outputTokens: -1 })).toBeNull();
    expect(parseTokenUsage({ ...usage, outputTokens: "800" })).toBeNull();
    // The cache is a part of the input: more cache than input is contradictory.
    expect(parseTokenUsage({ ...usage, cachedInputTokens: 25000 })).toBeNull();
    // A usage that is not of one execution is a session's, which this view never shows.
    expect(parseTokenUsage({ ...usage, scope: "session" })).toBeNull();
    expect(parseTokenUsage({ ...usage, source: "" })).toBeNull();
  });
});

const lineage = { kind: "micro-task" };
const reply = (overrides: Record<string, unknown> = {}) => ({
  runId: "m1", revision: 3, state: "executing", hostId: null, buddy: null, turns: [], currentTurn: null, activeRequest: null, requests: [],
  counts: { openRequests: 0 }, children: [], artifacts: [], integrations: [], finalArtifactId: null, hostConclusion: null, shutdown: null, workspace: null,
  task: { lineage }, ...overrides,
});

describe("the compact collaboration record", () => {
  it("is a micro task or a helper that belongs to the run asked for, with a revision", () => {
    expect(parseWorkflow(reply(), "m1")).toMatchObject({ runId: "m1", revision: 3, buddyLocked: false, delegationAuthority: false });
    expect(parseWorkflow(reply({ task: { lineage: { kind: "helper" } } }), "m1").runId).toBe("m1");
  });

  it("refuses an ordinary execution record, another run's record and a record without its revision, all the same way", () => {
    for (const bad of [reply({ task: { lineage: { kind: "execution" } } }), reply({ runId: "other" }), reply({ revision: undefined }), reply({ revision: 1.5 }),
      reply({ task: {} }), reply({ turns: "none" }), { runId: "m1", task: { lineage } }, null, "text"]) {
      expect(() => parseWorkflow(bad, "m1")).toThrow(ApiError);
      expect(() => parseWorkflow(bad, "m1")).toThrow(expect.objectContaining({ code: "INVALID_RESPONSE" }));
    }
  });

  it("reads a user's lock, and a turn without its recorded route as unrecorded", () => {
    const value = reply({ buddyLocked: true, turns: [{ turnId: "t1", turnIndex: 1, attemptId: "a1", expectedSeconds: null, resumeMode: "initial", sessionId: null,
      state: "returned", disposition: "completed", createdAt: "x", updatedAt: "y", buddy: null }] });
    const parsed = parseWorkflow(value, "m1");
    expect(parsed.buddyLocked).toBe(true);
    expect(parsed.turns[0]).toMatchObject({ routing: null, tokenUsage: null, buddy: null });
  });
});

describe("the audit", () => {
  const audit = { runId: "m1", revision: 4, audit: { turns: [{ turnId: "t1", summary: "结果", remaining: ["还差一项"] }], continuations: [], requests: [], hostConclusions: [] },
    routingHistory: { entries: [{ decisionId: "d1", status: "completed", source: "single-candidate", selectedBuddy: null, routerBuddy: null, routingMode: "review", reason: null }] } };

  it("carries the turns' results, the Host's inputs, the requests and the routes", () => {
    const parsed = parseAudit(audit, "m1");
    expect(parsed.audit.turns).toEqual([{ turnId: "t1", summary: "结果", remaining: ["还差一项"] }]);
    expect(parsed.routing).toEqual([{ decisionId: "d1", status: "completed", source: "single-candidate", selectedBuddy: null, routerBuddy: null, routingMode: "review", reason: null }]);
  });

  it("refuses another run's audit and one without the routes", () => {
    expect(() => parseAudit({ ...audit, runId: "other" }, "m1")).toThrow(ApiError);
    expect(() => parseAudit({ ...audit, routingHistory: undefined }, "m1")).toThrow(ApiError);
  });
});

describe("the inquiries", () => {
  const message = (id: string, state: string, extra: Record<string, unknown> = {}) => ({ messageId: id, attemptId: "a1", question: "问", author: "host", createdAt: "t", state, reason: null, delivery: null, answer: null, ...extra });

  it("lists the oldest first, as the board returns them newest first, and says when the page may not hold all", () => {
    const parsed = parseInquiries({ messages: [message("new", "delivered"), message("old", "answered", { answer: { text: "答", at: "t2", truncated: true } })] }, 2);
    expect(parsed.items.map(item => item.messageId)).toEqual(["old", "new"]);
    expect(parsed.items[0]!.answer).toEqual({ text: "答", at: "t2", truncated: true });
    expect(parsed.more).toBe(true);
    expect(parseInquiries({ messages: [] }, 200)).toEqual({ items: [], more: false });
  });

  it("refuses a state it does not know", () => {
    expect(() => parseInquiries({ messages: [message("x", "sent")] }, 200)).toThrow(ApiError);
    expect(() => parseInquiries({ messages: "none" }, 200)).toThrow(ApiError);
  });
});

describe("the task view", () => {
  it("belongs to the run asked for and carries its text and status", () => {
    expect(parseTask({ runId: "m1", task: "做", status: "running", revision: 1 }, "m1").task).toBe("做");
    expect(() => parseTask({ runId: "other", task: "做", status: "running", revision: 1 }, "m1")).toThrow(ApiError);
    expect(() => parseTask({ runId: "m1", status: "running", revision: 1 }, "m1")).toThrow(ApiError);
  });
});
