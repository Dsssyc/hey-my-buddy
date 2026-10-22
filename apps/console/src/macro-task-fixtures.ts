/**
 * Realistic, contract-typed fixtures for the work macro task tests. Every value
 * mirrors the documented read shapes in docs/reference/macro-tasks.md; nothing
 * here claims a cost, a model call or an invented duration.
 */

import type {
  MacroTaskSummary,
  MacroTaskTimeline,
  TimelineBuddy,
  TimelineEvent,
  TimelineRow,
  TimelineSpan,
} from "./macro-task-types";

const OPUS: TimelineBuddy = { harness: "claude", provider: "anthropic", model: "claude-opus-5-5", effort: "high" };
const CODEX: TimelineBuddy = { harness: "codex", provider: "openai", model: "gpt-5-codex", effort: "max" };
const GLM: TimelineBuddy = { harness: "zcode", provider: "bigmodel-api", model: "glm-5", effort: "high" };
const SONNET: TimelineBuddy = { harness: "claude", provider: "anthropic", model: "claude-sonnet-5", effort: "low" };

export const OBSERVED_AT = "2026-09-26T08:12:00Z";

const project = (id: string, label: string, path: string) => ({ id, label, path });

export function macroTaskSummary(overrides: Partial<MacroTaskSummary> = {}): MacroTaskSummary {
  return {
    macroTaskId: "macro-1",
    kind: "macro-task",
    title: "宏任务时间轴：设计、接口与实现",
    titleSource: "macro-task",
    description: "按用户认可的第 X 节设计宏任务时间轴。本宏任务覆盖只读接口、前端呈现与离线升级验证。",
    summary: null,
    project: project("p1", "hey-my-buddy", "~/projects/hey-my-buddy"),
    sourceHostId: "codex-desktop",
    currentHostIds: ["codex-desktop"],
    createdAt: "2026-09-26T01:12:00Z",
    lastActivityAt: "2026-09-26T08:12:00Z",
    lastActivitySeq: 41,
    state: "active",
    counts: { roots: 5, helpers: 1, accepted: 2, failed: 0, active: 1, host: 0, review: 1, ended: 4,
      stoppableRoots: 1, stoppableHelpers: 0, delivered: 1, awaitingHost: 0 },
    matchingRuns: 6,
    rootRunIds: ["r1", "r2", "r5", "r4", "r6"],
    ...overrides,
  };
}

function row(runId: string, overrides: Partial<TimelineRow> = {}): TimelineRow {
  return {
    runId,
    parentRunId: null,
    rootRunId: runId, rootLeadRunId: null, delegationAuthority: false,
    title: `微任务 ${runId}`,
    titleSource: "title",
    taskSummary: null,
    summary: null,
    createdAt: "2026-09-26T01:12:00Z",
    state: "delivered",
    status: "completed",
    category: "ended",
    shutdownConfirmed: true,
    depth: 0,
    kind: "micro-task",
    buddy: GLM,
    acceptedAt: null,
    reviewRejectedAt: null, reviewRejectionNote: null,
    ...overrides,
  };
}

function span(spanId: string, runId: string, kind: TimelineSpan["kind"], startAt: string | null, endAt: string | null, overrides: Partial<TimelineSpan> = {}): TimelineSpan {
  return {
    spanId, runId, kind, startAt, endAt,
    state: kind === "queue" ? "admitted" : kind === "host" ? "approved" : "finished",
    attemptId: null, turnId: null, turnIndex: null, requestId: null,
    buddy: null, expectedSeconds: kind === "execution" ? 1800 : null,
    reminders: null, inquiries: kind === "execution" ? [] : null,
    shutdownConfirmed: null, uncertain: false, clockSkew: false,
    ...overrides,
  };
}

function event(seq: number, runId: string, kind: TimelineEvent["kind"], at: string, overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return { seq, runId, kind, at, decision: null, verdict: null, state: null, strategy: null, note: null, actor: { role: "host", id: "codex-desktop" },
    attemptId: null, requestId: null, artifactId: null, ...overrides };
}

/** Six micro tasks, folding gaps, every span kind and terminal marker. */
export function macroTaskTimelineFixture(overrides: Partial<MacroTaskTimeline> = {}): MacroTaskTimeline {
  const rows: TimelineRow[] = [
    row("r1", { title: "设计宏任务时间轴视图与交互规范", taskSummary: "设计只读的宏任务时间轴视图、交互规范与验收标准。",
      summary: "已完成时间轴视图与交互规范设计并交付审阅。", createdAt: "2026-09-26T01:12:00Z", state: "accepted", status: "completed",
      buddy: OPUS, acceptedAt: "2026-09-26T02:20:00Z" }),
    row("r2", { title: "实现 macro_tasks 表、macro_task_list 与只读时间轴接口", taskSummary: "实现 macro_tasks 表与 macro_task_list、macro_task_timeline 两个只读接口。",
      summary: null, createdAt: "2026-09-26T01:20:00Z", state: "accepted", status: "completed",
      buddy: CODEX, acceptedAt: "2026-09-26T03:30:00Z" }),
    row("r3", { title: "补充 schema 升级离线副本的验证测试", taskSummary: "为离线副本升级补充验证测试，覆盖行数与哈希不变。",
      summary: "已补充离线副本验证测试，全部通过。", createdAt: "2026-09-26T01:38:00Z", state: "completed", status: "completed",
      parentRunId: "r2", rootRunId: "r2", rootLeadRunId: null, depth: 1, kind: "helper", buddy: GLM }),
    row("r5", { title: "按设计稿实现 ObjectiveTimeline 组件与布局计算", taskSummary: "按设计稿实现 ObjectiveTimeline 组件与折叠布局计算。",
      summary: null, createdAt: "2026-09-26T05:30:00Z", state: "cancelled", status: "cancelled",
      buddy: GLM }),
    row("r4", { title: "时间轴界面带截图的视觉与交互审查", taskSummary: "带截图审查时间轴界面的视觉与交互问题。",
      summary: null, createdAt: "2026-09-26T06:38:00Z", state: "executing", status: "running",
      category: "active", shutdownConfirmed: false, buddy: SONNET }),
    row("r6", { title: "修正折叠区间展开后的键盘焦点顺序", taskSummary: "修正折叠区间展开后的键盘焦点顺序。",
      summary: "已修正键盘焦点顺序，等待复核。", createdAt: "2026-09-26T07:05:00Z", state: "delivered", status: "completed",
      category: "review", buddy: GLM }),
  ];
  const spans: TimelineSpan[] = [
    span("s-r1-q", "r1", "queue", "2026-09-26T01:12:00Z", "2026-09-26T01:14:00Z"),
    span("s-r1-e", "r1", "execution", "2026-09-26T01:14:00Z", "2026-09-26T01:58:00Z",
      { turnIndex: 1, attemptId: "att-r1", buddy: OPUS, shutdownConfirmed: true, resultStatus: "ok" }),
    span("s-r2-q", "r2", "queue", "2026-09-26T01:20:00Z", "2026-09-26T01:21:00Z"),
    span("s-r2-r", "r2", "routing", "2026-09-26T01:21:00Z", "2026-09-26T01:24:00Z", { decisionTaskId: "run-d02b", decisionId: "decision-r2-first", routing: {
      selectedBuddy: OPUS, reason: null, note: "适合接口审阅", policyCheck: { taskPreference: { ruleIndex: 0, outcome: "matched" }, userPreference: "none" },
      usage: { elapsedMs: 180000, toolCalls: 3, bytesRead: 1024 },
    } }),
    span("s-r2-e1", "r2", "execution", "2026-09-26T01:24:00Z", "2026-09-26T02:10:00Z",
      { turnIndex: 1, attemptId: "att-r2a", buddy: CODEX, shutdownConfirmed: true,
        inquiries: [{ inquiryId: "inq-r2a", createdAt: "2026-09-26T01:50:00Z", state: "answered" }] }),
    span("s-r2-w", "r2", "host", "2026-09-26T02:10:00Z", "2026-09-26T03:00:00Z",
      { state: "approved", requestId: "req-schema", summary: "schema 版本号需与性能优化的合入顺序对齐" }),
    span("s-r2-e2", "r2", "execution", "2026-09-26T03:00:00Z", "2026-09-26T03:10:00Z",
      { turnIndex: 2, attemptId: "att-r2b", buddy: CODEX, shutdownConfirmed: true }),
    span("s-r3-q", "r3", "queue", "2026-09-26T01:38:00Z", "2026-09-26T01:40:00Z"),
    span("s-r3-e1", "r3", "execution", "2026-09-26T01:40:00Z", "2026-09-26T02:02:00Z",
      { turnIndex: 1, attemptId: "att-r3a", buddy: GLM, resultStatus: "failed", error: "测试命令超时", shutdownConfirmed: true }),
    span("s-r3-e2", "r3", "execution", "2026-09-26T02:04:00Z", "2026-09-26T02:30:00Z",
      { turnIndex: 2, attemptId: "att-r3b", buddy: GLM, shutdownConfirmed: true, resultStatus: "ok" }),
    span("s-r5-q", "r5", "queue", "2026-09-26T05:30:00Z", "2026-09-26T05:31:00Z"),
    span("s-r5-e", "r5", "execution", "2026-09-26T05:31:00Z", "2026-09-26T05:50:00Z",
      { turnIndex: 1, attemptId: "att-r5", buddy: GLM, resultStatus: "cancelled", shutdownConfirmed: true }),
    span("s-r4-q", "r4", "queue", "2026-09-26T06:38:00Z", "2026-09-26T06:40:00Z", { state: "admitted" }),
    span("s-r4-e", "r4", "execution", "2026-09-26T06:40:00Z", null,
      { state: "executing", turnIndex: 1, attemptId: "att-r4", buddy: SONNET, shutdownConfirmed: false,
        reminders: { overdue: true, possiblyStuck: false, lastNativeEventAt: "2026-09-26T08:10:00Z", toolInProgress: true } }),
    span("s-r6-e", "r6", "execution", "2026-09-26T07:05:00Z", "2026-09-26T07:38:00Z",
      { state: "uncertain", uncertain: true, turnIndex: 1, attemptId: "att-r6", buddy: GLM, shutdownConfirmed: false }),
  ];
  const events: TimelineEvent[] = [
    event(1, "r1", "dispatch", "2026-09-26T01:12:00Z", {  }),
    event(2, "r2", "dispatch", "2026-09-26T01:20:00Z", {  }),
    event(3, "r3", "dispatch", "2026-09-26T01:38:00Z", {  }),
    event(4, "r1", "accept", "2026-09-26T02:20:00Z", {  }),
    event(5, "r2", "decide", "2026-09-26T03:00:00Z", {  }),
    event(6, "r2", "continue", "2026-09-26T03:02:00Z", {  }),
    event(7, "r2", "accept", "2026-09-26T03:30:00Z", {  }),
    event(8, "r5", "cancel", "2026-09-26T05:50:00Z", {  }),
    event(9, "r4", "dispatch", "2026-09-26T06:38:00Z", {  }),
  ];
  return {
    macroTask: macroTaskSummary(),
    observedAt: OBSERVED_AT,
    cursor: 41,
    rows, spans, events,
    totals: { rows: rows.length, spans: spans.length, events: events.length, allRows: rows.length },
    truncated: { rows: false, spans: false, events: false },
    filtered: false,
    scopeComplete: true,
    ...overrides,
  };
}

/** The standalone-root and second-project rows used by the list tests. */
export function listFixture(): MacroTaskSummary[] {
  return [
    macroTaskSummary({ macroTaskId: "macro-1", lastActivitySeq: 41, lastActivityAt: "2026-09-26T08:12:00Z", state: "active" }),
    macroTaskSummary({ macroTaskId: "macro-2", title: "0.13 控制台入口候选版收尾与文档", description: null, lastActivitySeq: 30,
      lastActivityAt: "2026-09-26T04:40:00Z", state: "review",
      counts: { roots: 6, helpers: 0, accepted: 5, failed: 0, active: 0, host: 0, review: 1, ended: 5,
        stoppableRoots: 0, stoppableHelpers: 0, delivered: 1, awaitingHost: 0 } }),
    macroTaskSummary({ macroTaskId: "run:3f0e", kind: "standalone", title: "修复标题回退在 CRLF 输入下的显示，补充回归测试并核对两处相关调用的输出稳定性与提示文案",
      titleSource: "task", description: null, lastActivitySeq: 22, lastActivityAt: "2026-09-25T10:22:00Z", state: "ended",
      sourceHostId: "codex-cli",
      counts: { roots: 1, helpers: 0, accepted: 0, failed: 0, active: 0, host: 0, review: 0, ended: 1,
        stoppableRoots: 0, stoppableHelpers: 0, delivered: 0, awaitingHost: 0 } }),
    macroTaskSummary({ macroTaskId: "macro-4", title: "DSH 插件在 Windows 路径下的沙箱回归排查",
      project: project("p2", "dsh-harness", "~/projects/dsh-harness"),
      sourceHostId: "dsh-host-02", lastActivitySeq: 39, lastActivityAt: "2026-09-26T08:11:00Z", state: "host",
      counts: { roots: 2, helpers: 0, accepted: 0, failed: 0, active: 1, host: 1, review: 0, ended: 0,
        stoppableRoots: 1, stoppableHelpers: 0, delivered: 0, awaitingHost: 1 } }),
  ];
}
