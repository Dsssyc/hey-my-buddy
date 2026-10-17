import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { parseAudit, parseBasis, parseInquiries, parseRoutingPage, parseTask, parseTokenUsage, parseWorkflow } from "./parse";
import { task } from "./task-detail.fixtures";

const usage = { inputTokens: 20000, cachedInputTokens: 15000, outputTokens: 800, source: "codex-native", scope: "attempt" };

describe("one execution's usage", () => {
  it("keeps the cached input inside the input and reads a partial record as it is", () => {
    expect(parseTokenUsage(usage)).toEqual({ ...usage, inputBasis: "includes-cached" });
    expect(parseTokenUsage({ ...usage, inputTokens: null, cachedInputTokens: null })).toMatchObject({ inputTokens: null, cachedInputTokens: null, outputTokens: 800 });
    expect(parseTokenUsage({ ...usage, completeness: "partial" })).toMatchObject({ completeness: "partial" });
  });

  it("keeps the basis the usage states, and lets a cache that is counted apart exceed the input", () => {
    expect(parseTokenUsage({ ...usage, inputBasis: "excludes-cached", inputTokens: 5000 })).toMatchObject({ inputBasis: "excludes-cached", inputTokens: 5000, cachedInputTokens: 15000 });
    expect(parseTokenUsage({ ...usage, inputBasis: "includes-cached" })).toMatchObject({ inputBasis: "includes-cached" });
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

/** The seals the board writes for a micro task, in the shapes the service builds them in (see `_artifact_view`). */
const seals = [
  { artifactId: "s-in", kind: "input", attemptId: null, turnId: null, sourceTaskId: null, manifestSha256: "a".repeat(64), createdAt: "t", path: "/work/m1", baseCommit: "c1", inputCommit: "c1" },
  { artifactId: "s-turn", kind: "turn-input", attemptId: "a1", turnId: "t1", sourceTaskId: "m1", manifestSha256: "b".repeat(64), createdAt: "t", path: "/work/m1", baseCommit: "c1", inputCommit: "c1" },
  { artifactId: "s-refs", kind: "shared-refs", attemptId: "a1", turnId: "t1", sourceTaskId: "m1", manifestSha256: "c".repeat(64), createdAt: "t", sharedStash: { state: "observed" } },
];
const output = { artifactId: "o1", kind: "output", attemptId: "a1", turnId: "t1", sourceTaskId: "m1", manifestSha256: "d".repeat(64), createdAt: "t",
  outputCommit: "c2", diffPath: "/d.diff", diffSha256: "e".repeat(64), changedPaths: ["a.ts"], cumulativePatch: { baseCommit: "c1", outputCommit: "c2", path: "/p", sha256: "f".repeat(64), changedPaths: ["a.ts"] } };
const partial = { ...output, artifactId: "o2", kind: "partial-output", partial: true, verified: false, final: false, cumulativePatch: undefined };

const turn = { turnId: "t1", turnIndex: 1, attemptId: "a1", expectedSeconds: 1800, resumeMode: "initial", sessionId: null, state: "returned", disposition: "completed",
  createdAt: "x", updatedAt: "y", buddy: { harness: "codex", provider: "openai", model: "m", effort: "high" }, routing: { decisionId: null, buddyRevision: 1 }, tokenUsage: null };
const reply = (overrides: Record<string, unknown> = {}) => ({
  runId: "m1", revision: 3, state: "executing", hostId: null, buddy: null, buddyLocked: false, delegationAuthority: false, routing: null,
  turns: [], currentTurn: null, activeRequest: null, requests: [], counts: { turns: 0, openRequests: 0 }, children: [], artifacts: [], integrations: [],
  finalArtifactId: null, hostConclusion: null, shutdown: null, workspace: null, task: {}, ...overrides,
});

describe("the compact collaboration record", () => {
  it("belongs to the run asked for, with a revision", () => {
    expect(parseWorkflow(reply(), "m1")).toMatchObject({ runId: "m1", revision: 3, buddyLocked: false, delegationAuthority: false, counts: { turns: 0, openRequests: 0 } });
  });

  it("refuses another run's record, and a record that lacks what the service always sends, all the same way", () => {
    for (const bad of [reply({ runId: "other" }), reply({ revision: undefined }), reply({ revision: 1.5 }), reply({ counts: { openRequests: 0 } }),
      reply({ buddyLocked: undefined }), reply({ turns: "none" }), reply({ children: [{ taskId: "h", role: "helper", state: "delivered" }] }), null, "text"]) {
      expect(() => parseWorkflow(bad, "m1")).toThrow(ApiError);
      expect(() => parseWorkflow(bad, "m1")).toThrow(expect.objectContaining({ code: "INVALID_RESPONSE" }));
    }
  });

  it("reads the seals of a real record: the input and each turn's seals have no attempt or no output, and are not drawn", () => {
    const parsed = parseWorkflow(reply({ artifacts: [output, partial, ...seals] }), "m1");
    expect(parsed.artifacts.map(item => [item.artifactId, item.kind, item.partial, item.verified, item.final])).toEqual([
      ["o1", "output", false, null, null], ["o2", "partial-output", true, false, false],
    ]);
    expect(parsed.artifacts[0]!.cumulativePatch).toMatchObject({ baseCommit: "c1", outputCommit: "c2", changedPaths: ["a.ts"] });
    expect(parsed.artifacts[1]!.cumulativePatch).toBeNull();
    // A sealed output must name its attempt: that is what files it under a turn.
    expect(() => parseWorkflow(reply({ artifacts: [{ ...output, attemptId: null }] }), "m1")).toThrow(ApiError);
  });

  it("reads a user's lock, and a turn without its recorded route as unrecorded", () => {
    const parsed = parseWorkflow(reply({ buddyLocked: true, turns: [{ ...turn, routing: null, buddy: null, tokenUsage: null }] }), "m1");
    expect(parsed.buddyLocked).toBe(true);
    expect(parsed.turns[0]).toMatchObject({ routing: null, tokenUsage: null, buddy: null });
  });

  it("reads a current turn that has returned nothing yet, and the routing bounds of the current route", () => {
    const parsed = parseWorkflow(reply({ currentTurn: turn, routing: { status: "completed", decisionId: "d1", source: "single-candidate",
      constraints: { harness: "dsh" }, requiredCapabilities: ["execution:dsh"], routingBasis: { candidateCount: 1, excludedBuddies: [{ harness: "dsh", provider: "p", model: "m", effort: "off", reason: "排除低强度" }] } } }), "m1");
    expect(parsed.currentTurn).toMatchObject({ summary: null, remaining: [] });
    expect(parsed.routing?.basis).toEqual({ constraints: { harness: "dsh" }, requiredCapabilities: ["execution:dsh"], candidateCount: 1,
      excluded: [{ buddy: { harness: "dsh", provider: "p", model: "m", effort: "off" }, reason: "排除低强度" }], preferences: [] });
  });

  it("refuses a Host conclusion without its actor, and an actor that is not one", () => {
    const conclusion = { conclusionId: "c", attemptId: null, executionStatus: "failed", note: "n", evidence: [], artifactId: null, integrationId: null,
      actor: { role: "host", id: "codex-desktop" }, createdAt: "t", ownerGeneration: 1, runRevision: 3 };
    expect(parseWorkflow(reply({ hostConclusion: conclusion }), "m1").hostConclusion?.actor).toEqual({ role: "host", id: "codex-desktop" });
    for (const actor of [null, { role: "robot", id: "x" }, { role: "host", id: "" }]) {
      expect(() => parseWorkflow(reply({ hostConclusion: { ...conclusion, actor } }), "m1")).toThrow(ApiError);
    }
  });

  it("reads an integration that needed none, with no target, and one that was verified", () => {
    const base = { integrationId: "i", artifactId: "o1", attemptId: "a1", state: "not-required", strategy: "not-required", target: null, beforeCommit: null, afterCommit: null,
      verification: {}, reason: "只读", actor: { role: "host", id: "h" }, createdAt: "t" };
    const verified = { ...base, state: "verified", strategy: "merge", target: { kind: "checkout", path: "~/p", ref: "main" }, afterCommit: "c3",
      verification: { summary: "通过", hostPaths: ["docs/a.md"] } };
    const [first, second] = parseWorkflow(reply({ integrations: [base, verified] }), "m1").integrations;
    expect(first).toMatchObject({ state: "not-required", target: null, verificationSummary: null, hostPaths: [], reason: "只读" });
    expect(second).toMatchObject({ target: { path: "~/p", ref: "main" }, afterCommit: "c3", verificationSummary: "通过", hostPaths: ["docs/a.md"] });
  });
});

describe("the audit", () => {
  const result = { ...turn, summary: "结果", remaining: ["还差一项"] };
  const entry = { decisionId: "d1", status: "completed", source: "single-candidate", selectedBuddy: { harness: "dsh", provider: "p", model: "m", effort: "off", buddyId: "x", label: "L" },
    routerBuddy: null, routingMode: "review", reason: null };
  const audit = { runId: "m1", revision: 4, audit: { turns: [result], continuations: [], requests: [], hostConclusions: [] }, routingHistory: { entries: [entry], nextCursor: 12, total: 40 } };

  it("carries every turn with its result, the Host's inputs, the requests and the first page of the routes", () => {
    const parsed = parseAudit(audit, "m1");
    expect(parsed.audit.turns[0]).toMatchObject({ turnId: "t1", attemptId: "a1", summary: "结果", remaining: ["还差一项"] });
    expect(parsed.routing.entries).toEqual([{ decisionId: "d1", status: "completed", source: "single-candidate", routerBuddy: null, routingMode: "review", reason: null,
      selectedBuddy: { harness: "dsh", provider: "p", model: "m", effort: "off" } }]);
    expect(parsed.routing.nextCursor).toBe(12);
  });

  it("reads a turn that has returned nothing as one with no result yet", () => {
    const { summary: _summary, remaining: _remaining, ...bare } = result;
    expect(parseAudit({ ...audit, audit: { ...audit.audit, turns: [bare] } }, "m1").audit.turns[0]).toMatchObject({ summary: null, remaining: [] });
  });

  it("reads a request decided with a resolution, and one decided with a verdict", () => {
    const request = { requestId: "r", kind: "attention", state: "approved", summary: "s", attempted: null, neededWork: [], expectedArtifacts: [], acceptance: null,
      turnId: "t1", attemptId: "a1", childTaskId: null, createdAt: "t", decidedAt: "t", routing: false, payload: {}, decision: null };
    const [adopted, approved, open] = parseAudit({ ...audit, audit: { ...audit.audit, requests: [
      { ...request, decision: { resolution: { action: "adopt", actor: { role: "host", id: "h" } } } },
      { ...request, decision: { decision: "approve", reason: "可以", actor: { role: "lead", id: "l" } } },
      { ...request, state: "open", decidedAt: null },
    ] } }, "m1").audit.requests;
    expect(adopted!.decision).toEqual({ decision: null, reason: null, actor: { role: "host", id: "h" } });
    expect(approved!.decision).toEqual({ decision: "approve", reason: "可以", actor: { role: "lead", id: "l" } });
    expect(open!.decision).toBeNull();
  });

  it("refuses another run's audit, one without the routes, and a route in a mode that does not exist", () => {
    expect(() => parseAudit({ ...audit, runId: "other" }, "m1")).toThrow(ApiError);
    expect(() => parseAudit({ ...audit, routingHistory: undefined }, "m1")).toThrow(ApiError);
    expect(() => parseAudit({ ...audit, routingHistory: { entries: [{ ...entry, routingMode: "slow" }], nextCursor: null } }, "m1")).toThrow(ApiError);
  });

  it("reads a page of routes on its own, with the cursor that goes on", () => {
    const page = parseRoutingPage({ runId: "m1", routingHistory: { entries: [entry], nextCursor: null, total: 1 } }, "m1");
    expect(page.entries).toHaveLength(1);
    expect(page.nextCursor).toBeNull();
    expect(() => parseRoutingPage({ runId: "other", routingHistory: { entries: [], nextCursor: null } }, "m1")).toThrow(ApiError);
  });
});

describe("what a decision was bound by", () => {
  const decision = { decisionId: "d1", constraints: { harness: "dsh" }, requiredCapabilities: ["execution:dsh"],
    routingBasis: { candidateCount: 1, excludedCount: 1, excludedBuddies: [{ harness: "dsh", provider: "p", model: "m", effort: "off", reason: "提交时排除低推理强度" }] },
    output: { programSelection: { preferences: [{ buddyId: "dsh:p:m:max", mode: "pin", reason: "当时固定此 buddy" }] } } };

  it("keeps the constraints, the capabilities, the exclusions with their reasons and the preferences the program applied", () => {
    expect(parseBasis({ decision }, "d1")).toEqual({
      constraints: { harness: "dsh" }, requiredCapabilities: ["execution:dsh"], candidateCount: 1,
      excluded: [{ buddy: { harness: "dsh", provider: "p", model: "m", effort: "off" }, reason: "提交时排除低推理强度" }],
      preferences: [{ buddyId: "dsh:p:m:max", mode: "pin", reason: "当时固定此 buddy" }],
    });
  });

  it("is empty for a decision that recorded none of it, and refuses another decision's record or a mode that does not exist", () => {
    expect(parseBasis({ decision: { decisionId: "d1" } }, "d1")).toEqual({ constraints: {}, requiredCapabilities: [], candidateCount: null, excluded: [], preferences: [] });
    expect(() => parseBasis({ decision }, "d2")).toThrow(ApiError);
    expect(() => parseBasis({ decision: { ...decision, output: { programSelection: { preferences: [{ buddyId: "x", mode: "like", reason: null }] } } } }, "d1")).toThrow(ApiError);
  });
});

describe("the inquiries", () => {
  const message = (id: string, state: string, extra: Record<string, unknown> = {}) => ({ messageId: id, inquiryId: `question-${id}`, attemptId: "a1", question: "问", author: "host", createdAt: "t", state, reason: null, delivery: null, answer: null, ...extra });

  it("lists the oldest first, as the board returns them newest first, and says when the page may not hold all", () => {
    const parsed = parseInquiries({ messages: [message("new", "delivered"), message("old", "answered", { answer: { text: "答", at: "t2", truncated: true } })] }, 2);
    expect(parsed.items.map(item => item.messageId)).toEqual(["old", "new"]);
    expect(parsed.items.map(item => item.inquiryId)).toEqual(["question-old", "question-new"]);
    expect(parsed.items[0]!.answer).toEqual({ text: "答", at: "t2", truncated: true });
    expect(parsed.more).toBe(true);
    expect(parseInquiries({ messages: [] }, 200)).toEqual({ items: [], more: false });
  });

  it("keeps an inquiry that names no attempt", () => {
    expect(parseInquiries({ messages: [message("x", "queued", { attemptId: null })] }, 200).items[0]!.attemptId).toBeNull();
  });

  it("refuses a state it does not know", () => {
    expect(() => parseInquiries({ messages: [message("x", "sent")] }, 200)).toThrow(ApiError);
    expect(() => parseInquiries({ messages: "none" }, 200)).toThrow(ApiError);
  });
});

describe("the task view", () => {
  const attempt = (overrides: Record<string, unknown> = {}) => ({ attemptId: "att-2", ...overrides });

  it("belongs to the run asked for and carries what the detail reads, made definite", () => {
    const view = parseTask(task(), "m1");
    expect(view.task.task).toContain("实现微任务详情");
    expect(view).toMatchObject({ updatedAt: "2026-09-26T11:00:00Z", lead: false });
    expect(view.attempt).toMatchObject({ attemptId: "att-2", active: false, overdue: false, possiblyStuck: false, activity: null, ending: null, session: null });
    expect(() => parseTask(task({ runId: "other" }), "m1")).toThrow(ApiError);
    for (const key of ["task", "status", "cwd", "createdAt", "updatedAt", "lineage"]) {
      expect(() => parseTask({ ...task(), [key]: undefined }, "m1"), key).toThrow(ApiError);
    }
  });

  it("refuses malformed metadata and actors while accepting an unrecorded project", () => {
    expect(() => parseTask(task({ lineage: { ...task().lineage, kind: "other" } }), "m1")).toThrow(ApiError);
    expect(parseTask(task({ lineage: { ...task().lineage, project: null } }), "m1").task.lineage?.project).toBeNull();
    expect(() => parseTask(task({ lineage: { ...task().lineage, sourceHostId: 7 } }), "m1")).toThrow(ApiError);
    expect(() => parseTask(task({ status: "cancelled", cancellation: { actor: { role: "robot", id: "x" }, reason: null } }), "m1")).toThrow(ApiError);
    expect(() => parseTask(task({ selectedAttempt: { reminders: null } }), "m1")).toThrow(ApiError);
  });

  it("accepts micro tasks and helpers with workflows, and refuses raw records", () => {
    expect(parseTask(task({ lineage: { ...task().lineage, kind: "helper" } }), "m1").lead).toBe(false);
    expect(() => parseTask(task({ workflow: undefined }), "m1")).toThrow(ApiError);
    expect(() => parseTask(task({ lineage: { ...task().lineage, kind: "execution" } }), "m1")).toThrow(ApiError);
    expect(() => parseTask(task({ lineage: { ...task().lineage, kind: "decision" } }), "m1")).toThrow(ApiError);
    expect(parseTask(task({ lineage: { ...task().lineage, rootLeadRunId: "lead-1" } }), "m1").lead).toBe(true);
  });

  it("reads the reminders and the activity of the attempt that runs now, and of no other", () => {
    const reminders = { overdue: true, possiblyStuck: true, lastNativeEventAt: null, toolInProgress: false };
    const activity = { phase: "tool-running", observedAt: "t", toolName: "bash", counts: { modelTurns: 4, toolCalls: 9 }, lastToolActivityAt: "t", lastNativeActivityAt: "t", eventSeq: 7 };
    const running = parseTask(task({ status: "running", activeAttemptId: "att-2", activity, selectedAttempt: attempt({ reminders }) }), "m1").attempt!;
    expect(running).toMatchObject({ active: true, overdue: true, possiblyStuck: true });
    expect(running.activity).toMatchObject({ phase: "tool-running", toolName: "bash", modelTurns: 4, toolCalls: 9, eventSeq: 7, basis: "tool" });
    const idle = parseTask(task({ status: "completed", activeAttemptId: null, activity, selectedAttempt: attempt({ reminders }) }), "m1").attempt!;
    expect(idle).toMatchObject({ active: false, overdue: false, possiblyStuck: false, activity: null });
  });

  it("keeps only the activity's own keys: no prompt, no tool arguments", () => {
    const activity = { phase: "tool-running", observedAt: "t", toolName: "apply_patch", prompt: "秘密", toolArguments: { secret: "x" } };
    const wire = JSON.stringify(parseTask(task({ status: "running", activeAttemptId: "att-2", activity }), "m1").attempt!.activity);
    expect(wire).toContain("apply_patch");
    expect(wire).not.toMatch(/秘密|secret|toolArguments|prompt/);
    // A report that is no record at all is no activity, and one with a phase nobody knows is a report of unknown progress.
    expect(parseTask(task({ status: "running", activeAttemptId: "att-2", activity: null }), "m1").attempt!.activity).toBeNull();
    expect(parseTask(task({ status: "running", activeAttemptId: "att-2", activity: { phase: "unknown" } }), "m1").attempt!.activity).toMatchObject({ phase: "unknown", basis: "unknown" });
  });

  it("says what backs an activity report: a tool, native activity, the phase alone, or nothing", () => {
    const basis = (activity: Record<string, unknown>) => parseTask(task({ status: "running", activeAttemptId: "att-2", activity }), "m1").attempt!.activity!.basis;
    expect(basis({ phase: "waiting-model", lastToolActivityAt: "t" })).toBe("tool");
    expect(basis({ phase: "waiting-model", nativeSessionId: "s" })).toBe("native");
    expect(basis({ phase: "starting", observedAt: "t", eventSeq: 1 })).toBe("phase");
    expect(basis({ phase: "nonsense" })).toBe("unknown");
  });

  it("writes how an attempt ended: a normal end is none, any other has its cause, who stopped it, why, and a native limit", () => {
    expect(parseTask(task(), "m1").attempt!.ending).toBeNull();
    const stopped = parseTask(task({ status: "cancelled", cancellation: { actor: { role: "host", id: "codex-desktop" }, reason: "不再需要" },
      selectedAttempt: attempt({ stopInitiator: "host", result: { terminationReason: "forced-stop" } }) }), "m1").attempt!.ending;
    expect(stopped).toEqual({ cause: "forced-stop", stoppedBy: "host", actor: { role: "host", id: "codex-desktop" }, reason: "不再需要", quota: null });
    const limited = parseTask(task({ status: "failed", quotaFailure: { code: "rate-limited" }, selectedAttempt: attempt({ result: { terminationReason: "harness-error" } }) }), "m1").attempt!.ending;
    expect(limited).toMatchObject({ cause: "harness-error", stoppedBy: null, quota: "rate-limited" });
    // A stop with no recorded initiator is unknown, never a person.
    expect(parseTask(task({ status: "cancelled", selectedAttempt: attempt({ result: { terminationReason: "unknown-stop" } }) }), "m1").attempt!.ending).toMatchObject({ stoppedBy: "unknown" });
    expect(() => parseTask(task({ status: "failed", quotaFailure: { code: "blocked" } }), "m1")).toThrow(ApiError);
  });

  it("reads where the native session is kept and whether it can be resumed", () => {
    const view = parseTask(task({ selectedAttempt: attempt({ result: { terminationReason: "completed", error: null, result: { stdout: "做完了", nativeSession: {
      sessionId: "s-1", storageScope: "task-private", storageOwner: "hey-my-buddy-attempt", nativeAppVisibility: "not-listed-in-native-app", resumable: true, bindingPresent: false, note: "  私有  " } } } }) }), "m1");
    expect(view.attempt!.session).toEqual({ id: "s-1", scope: "task-private", owner: "hey-my-buddy-attempt", visibility: "not-listed-in-native-app", resumable: true, binding: false, note: "私有" });
    const nativeOnly = parseTask(task({ selectedAttempt: attempt({ result: { terminationReason: "completed", result: { nativeIdentity: { sessionId: "s-2" } } } }) }), "m1").attempt!.session;
    expect(nativeOnly).toMatchObject({ id: "s-2", scope: null, resumable: null });
  });

  it("keeps the basis a usage states", () => {
    const view = parseTask(task({ selectedAttempt: attempt({ tokenUsage: { ...usage, inputBasis: "excludes-cached", inputTokens: 100 } }) }), "m1");
    expect(view.attempt!.tokenUsage).toMatchObject({ inputBasis: "excludes-cached", inputTokens: 100, cachedInputTokens: 15000 });
  });
});
