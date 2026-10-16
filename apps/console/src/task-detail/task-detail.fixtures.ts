import { vi } from "vitest";
import { macroTaskSummary } from "../macro-task-fixtures";
import type { MacroTaskTimeline, TimelineBuddy, TimelineEvent, TimelineRow, TimelineSpan } from "../macro-task-types";
import type { Task } from "../types";
import type { TaskDetailClient } from "./client";
import { parseTask } from "./parse";
import type { Audit, Basis, Buddy, Inquiries, Inquiry, ResultTurn, RoutingEntry, Turn, Workflow } from "./types";

/**
 * One micro task, `m1`, as the board records it: delegated at 10:00, routed,
 * run by Codex, held for its Host, continued with a different buddy and run
 * again, and delivered. Each builder takes the parts a test changes.
 */
export const CODEX: Buddy = { harness: "codex", provider: "openai", model: "gpt-6.1-sol", effort: "high" };
export const OPUS: Buddy = { harness: "claude", provider: "anthropic", model: "claude-opus-5-5", effort: "high" };
export const ROUTER: Buddy = { harness: "codex", provider: "openai", model: "gpt-6-luna", effort: "high" };
export const HOST = { role: "host" as const, id: "codex-desktop" };

export const at = (clock: string, second = 0) => `2026-09-26T${clock}:${String(second).padStart(2, "0")}Z`;

/** The task view of `m1` as `/api/tasks/<id>` answers it; `selectedAttempt` is merged, not replaced. */
export function task(overrides: Record<string, unknown> = {}): Task {
  const { selectedAttempt, ...rest } = overrides;
  return {
    runId: "m1", task: "实现微任务详情，左边是步骤，右边是这一步的内容。", status: "completed", owner: null, cwd: "/work/m1",
    revision: 5, createdAt: at("10:00"), updatedAt: at("11:00"), acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
    activeAttemptId: null, selectedAttemptId: "att-2", resultAvailable: true, shutdownConfirmed: true,
    lineage: { kind: "micro-task", sourceHostId: "codex-desktop", currentHostId: "codex-desktop", parentRunId: null, rootRunId: "m1", rootLeadRunId: null,
      project: { id: "p1", label: "hey-my-buddy", path: "~/projects/hey-my-buddy" }, buddy: OPUS },
    workflow: { state: "delivered", awaitingHost: false, hostId: "codex-desktop", ownerGeneration: 1, revision: 9, title: "微任务详情", macroTaskId: "macro-1" },
    selectedAttempt: { attemptId: "att-2", startedAt: at("10:42"), finishedAt: at("11:00"), error: null, shutdownConfirmed: true, tokenUsage: null,
      reminders: { overdue: false, possiblyStuck: false, lastNativeEventAt: null, toolInProgress: false },
      result: { terminationReason: "completed", result: { finalText: "完成。" } }, ...(selectedAttempt as object | undefined) },
    ...rest,
  } as Task;
}

export function turn(index: number, overrides: Partial<Turn> = {}): Turn {
  return {
    turnId: `turn-${index}`, turnIndex: index, attemptId: `att-${index}`, expectedSeconds: 1800, resumeMode: index === 1 ? "initial" : "native-session",
    sessionId: `01a2f7c4-${index}e31`.padEnd(36, "0"), state: "returned", disposition: "completed", createdAt: at(index === 1 ? "10:02" : "10:42"),
    updatedAt: at(index === 1 ? "10:30" : "11:00"), buddy: index === 1 ? CODEX : OPUS, routing: index === 1 ? { decisionId: "decision-1", buddyRevision: 1 } : { decisionId: null, buddyRevision: 2 },
    tokenUsage: { inputTokens: 23400, cachedInputTokens: 18000, outputTokens: 3100, source: "codex-native", scope: "attempt", inputBasis: "includes-cached" },
    ...overrides,
  };
}

export const result = (index: number, summary: string, remaining: string[] = []): ResultTurn => ({ ...turn(index), summary, remaining });

export const NO_BASIS: Basis = { constraints: {}, requiredCapabilities: [], candidateCount: null, excluded: [], preferences: [] };

export function workflow(overrides: Partial<Workflow> = {}): Workflow {
  const turns = [turn(2), turn(1)];
  return {
    runId: "m1", revision: 9, state: "delivered", hostId: "codex-desktop", buddy: OPUS, buddyLocked: false, delegationAuthority: false,
    routing: { status: "completed", decisionId: "decision-1", source: "model-selection", basis: NO_BASIS },
    turns, currentTurn: result(2, "第二轮：详情页已经按回合重做。", ["浏览器检查还要补两项"]),
    activeRequest: null,
    requests: [{ requestId: "req-1", kind: "assistance", state: "approved", createdAt: at("10:30"), decidedAt: at("10:40"), childTaskId: null, routing: false }],
    counts: { turns: 2, openRequests: 0 }, children: [], artifacts: [], integrations: [], finalArtifactId: null, hostConclusion: null, cwd: "/work/m1",
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedCount: 0 },
    ...overrides,
  };
}

export function audit(overrides: Partial<Audit> = {}): Audit {
  return {
    revision: 9,
    turns: [result(1, "第一轮：读完了旧的详情，列出了要删的部分。"), result(2, "第二轮：详情页已经按回合重做。", ["浏览器检查还要补两项"])],
    continuations: [{ continuationId: "cont-1", turnId: "turn-2", kind: "manual", reason: "批准后继续", createdAt: at("10:41"),
      input: "继续。读取样例目录已获批准；顺带核对路径边界。" }],
    requests: [{ requestId: "req-1", kind: "assistance", state: "approved", createdAt: at("10:30"), decidedAt: at("10:40"), childTaskId: null, routing: false,
      summary: "需要读取样例目录", attempted: "已读完工作树", neededWork: ["批准读取样例目录"], expectedArtifacts: [], acceptance: "读取后继续",
      preparationError: null, turnId: "turn-1", decisionId: null, decision: { decision: "approve", reason: "可以读取", actor: HOST } }],
    hostConclusions: [],
    ...overrides,
  };
}

export const REASON = "这是一个需要读懂布局代码的改动，近期同类微任务里它一次通过。";

export const routing = (overrides: Partial<RoutingEntry> = {}): RoutingEntry => ({
  decisionId: "decision-1", status: "completed", source: "model-selection", selectedBuddy: CODEX, routerBuddy: ROUTER, routingMode: "fast",
  reason: REASON, ...overrides,
});

export function inquiry(overrides: Partial<Inquiry> = {}): Inquiry {
  return { messageId: "inq-1", attemptId: "att-1", question: "测试跑了很久，卡在哪一步？", author: "codex-desktop", createdAt: at("10:20"),
    state: "delivered", reason: null, deliveredAt: at("10:20", 3), answer: null, ...overrides };
}

const tb = (buddy: Buddy | null): TimelineBuddy | null => buddy;

export function span(spanId: string, kind: TimelineSpan["kind"], startAt: string | null, endAt: string | null, overrides: Partial<TimelineSpan> = {}): TimelineSpan {
  return { spanId, runId: "m1", kind, startAt, endAt, state: kind === "queue" ? "admitted" : kind === "host" ? "approved" : "finished",
    attemptId: null, turnId: null, turnIndex: null, requestId: null, buddy: null, shutdownConfirmed: null, uncertain: false, clockSkew: false, ...overrides };
}

export function event(seq: number, kind: string, when: string, overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return { seq, runId: "m1", kind, at: when, label: "", summary: "", actor: HOST, attemptId: null, requestId: null, artifactId: null, ...overrides };
}

export function row(overrides: Partial<TimelineRow> = {}): TimelineRow {
  return { runId: "m1", parentRunId: null, rootRunId: "m1", title: "微任务详情", titleSource: "title", taskSummary: null, summary: null,
    createdAt: at("10:00"), state: "delivered", status: "completed", category: "review", shutdownConfirmed: true, depth: 0, kind: "micro-task",
    rootLeadRunId: null, buddy: tb(OPUS), acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null, ...overrides };
}

/** The spans and events of the standard `m1`: routed, two turns of two buddies, a Host wait between them, delivered. */
export function timeline(overrides: Partial<MacroTaskTimeline> = {}): MacroTaskTimeline {
  const spans = [
    span("q1", "queue", at("10:00"), at("10:01")),
    span("r1", "routing", at("10:01"), at("10:02"), { attemptId: "att-r1", decisionId: "decision-1", routing: { routingMode: "fast", selectedBuddy: tb(CODEX), reason: REASON, usage: { elapsedMs: 14000, toolCalls: 0, bytesRead: null } } }),
    span("e1", "execution", at("10:02"), at("10:30"), { turnIndex: 1, attemptId: "att-1", turnId: "turn-1", buddy: tb(CODEX), shutdownConfirmed: true, resultStatus: "ok" }),
    span("h1", "host", at("10:30"), at("10:40"), { state: "approved", requestId: "req-1", requestKind: "assistance", summary: "需要读取样例目录" }),
    span("q2", "queue", at("10:41"), at("10:42")),
    span("e2", "execution", at("10:42"), at("11:00"), { turnIndex: 2, attemptId: "att-2", turnId: "turn-2", buddy: tb(OPUS), shutdownConfirmed: true, resultStatus: "ok" }),
  ];
  const events = [
    event(1, "dispatch", at("10:00")),
    event(2, "decide", at("10:40"), { requestId: "req-1", summary: "approve" }),
    event(3, "continue", at("10:41"), { continuationId: "cont-1" }),
  ];
  return { macroTask: macroTaskSummary(), observedAt: at("11:30"), cursor: 10, rows: [row()], spans, events,
    totals: { rows: 1, spans: spans.length, events: events.length, allRows: 1 }, truncated: { rows: false, spans: false, events: false },
    filtered: false, scopeComplete: true, ...overrides };
}

export type Replies = { task?: Task; workflow?: Workflow; audit?: Audit; routing?: RoutingEntry[]; inquiries?: Inquiries; bases?: Record<string, Basis> };

/** A client that answers from what a test holds and records what it was asked. */
export function client(replies: Replies = {}): TaskDetailClient & { held: Required<Replies> } {
  const held = { task: task(), workflow: workflow(), audit: audit(), routing: [routing()], inquiries: { items: [], more: false } as Inquiries, bases: {} as Record<string, Basis>, ...replies };
  const verifiedAtMs = Date.UTC(2026, 8, 26, 11, 30, 5);
  return {
    held,
    task: vi.fn(async () => ({ value: parseTask(structuredClone(held.task), held.task.runId), verifiedAtMs })),
    workflow: vi.fn(async () => ({ value: structuredClone(held.workflow), verifiedAtMs })),
    audit: vi.fn(async (_runId: string, _decisionIds: string[]) => ({ value: { audit: structuredClone(held.audit), routing: structuredClone(held.routing) }, verifiedAtMs })),
    inquiries: vi.fn(async () => ({ value: structuredClone(held.inquiries), verifiedAtMs })),
    basis: vi.fn(async (decisionId: string) => ({ value: structuredClone(held.bases[decisionId]!), verifiedAtMs })),
  };
}

/**
 * The timeline of the macro task the records belong to, as the board would draw it for tests that hold records, not a story: one row for
 * each micro task of the group, a dispatch for each, and an open Host wait for each that waits for its Host.
 */
export function timelineOfRecords(macroTaskId: string, records: Task[]): MacroTaskTimeline {
  const members = records.filter(record => record.lineage?.kind === "micro-task"
    && ((record.workflow as { macroTaskId?: string } | undefined)?.macroTaskId === macroTaskId || `run:${record.runId}` === macroTaskId));
  const base = timeline();
  return { ...base, macroTask: macroTaskSummary({ macroTaskId }),
    rows: members.map(member => row({ runId: member.runId, state: member.workflow?.state ?? "executing" })),
    spans: members.filter(member => member.workflow?.state === "awaiting-host").map(member =>
      span(`host-${member.runId}`, "host", at("10:00"), null, { runId: member.runId, state: "open", requestId: `request-${member.runId}`, requestKind: "assistance" })),
    events: members.map((member, index) => event(index + 1, "dispatch", at("10:00"), { runId: member.runId, label: "派发", actor: null })) };
}
