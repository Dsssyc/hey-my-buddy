/**
 * Realistic, contract-typed fixtures for the work-objective tests. Every value
 * mirrors the documented read shapes in docs/reference/objectives.md; nothing
 * here claims a cost, a model call or an invented duration.
 */

import type {
  ObjectivePage,
  ObjectiveSummary,
  ObjectiveTimeline,
  TimelineConfiguration,
  TimelineEvent,
  TimelineRow,
  TimelineSpan,
} from "./objective-types";

export const OPUS: TimelineConfiguration = { adapter: "claude", provider: "anthropic", model: "claude-opus-5-5", effort: "high" };
export const CODEX: TimelineConfiguration = { adapter: "codex", provider: "openai", model: "gpt-5-codex", effort: "max" };
export const GLM: TimelineConfiguration = { adapter: "zcode", provider: "bigmodel-api", model: "glm-5", effort: "high" };
export const SONNET: TimelineConfiguration = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5", effort: "low" };

export const OBSERVED_AT = "2026-09-26T08:12:00Z";

const project = (id: string, label: string, path: string) => ({ id, label, path });

export function objectiveSummary(overrides: Partial<ObjectiveSummary> = {}): ObjectiveSummary {
  return {
    objectiveId: "obj-1",
    kind: "objective",
    title: "工作目标时间轴：设计、接口与实现",
    titleSource: "objective",
    summary: null,
    project: project("p1", "hey-my-buddy", "~/Desktop/codespace/mememe/hey-my-buddy"),
    sourceHostId: "codex-desktop",
    currentHostIds: ["codex-desktop"],
    createdAt: "2026-09-26T01:12:00Z",
    lastActivityAt: "2026-09-26T08:12:00Z",
    lastActivitySeq: 41,
    state: "active",
    counts: { roots: 5, helpers: 1, active: 1, host: 0, review: 1, ended: 4 },
    matchingRuns: 6,
    rootRunIds: ["r1", "r2", "r5", "r4", "r6"],
    ...overrides,
  };
}

function row(runId: string, overrides: Partial<TimelineRow> = {}): TimelineRow {
  return {
    runId,
    parentRunId: null,
    rootRunId: runId,
    title: `委派 ${runId}`,
    titleSource: "title",
    summary: null,
    createdAt: "2026-09-26T01:12:00Z",
    state: "delivered",
    status: "completed",
    category: "ended",
    shutdownConfirmed: true,
    depth: 0,
    kind: "goal",
    configuration: GLM,
    acceptedAt: null,
    acceptanceVerdict: null,
    ...overrides,
  };
}

function span(spanId: string, runId: string, kind: TimelineSpan["kind"], startAt: string | null, endAt: string | null, overrides: Partial<TimelineSpan> = {}): TimelineSpan {
  return {
    spanId, runId, kind, startAt, endAt,
    state: kind === "queue" ? "claimed" : kind === "host" ? "approved" : "finished",
    attemptId: null, turnId: null, turnIndex: null, requestId: null,
    configuration: null, shutdownConfirmed: null, uncertain: false, clockSkew: false,
    ...overrides,
  };
}

function event(seq: number, runId: string, kind: TimelineEvent["kind"], at: string, overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return { seq, runId, kind, at, label: "", summary: "", actor: "codex-desktop",
    attemptId: null, requestId: null, artifactId: null, ...overrides };
}

/** Six delegations, folding gaps, every span kind and terminal marker. */
export function objectiveTimelineFixture(overrides: Partial<ObjectiveTimeline> = {}): ObjectiveTimeline {
  const rows: TimelineRow[] = [
    row("r1", { title: "设计工作目标时间轴视图与交互规范", createdAt: "2026-09-26T01:12:00Z", state: "accepted", status: "completed",
      configuration: OPUS, acceptedAt: "2026-09-26T02:20:00Z", acceptanceVerdict: "accepted" }),
    row("r2", { title: "实现 objectives 表、objective_list 与只读时间轴接口", createdAt: "2026-09-26T01:20:00Z", state: "accepted", status: "completed",
      configuration: CODEX, acceptedAt: "2026-09-26T03:30:00Z", acceptanceVerdict: "accepted" }),
    row("r3", { title: "补充 schema 升级离线副本的验证测试", createdAt: "2026-09-26T01:38:00Z", state: "completed", status: "completed",
      parentRunId: "r2", rootRunId: "r2", depth: 1, kind: "helper", configuration: GLM }),
    row("r5", { title: "按设计稿实现 ObjectiveTimeline 组件与布局计算", createdAt: "2026-09-26T05:30:00Z", state: "cancelled", status: "cancelled",
      configuration: GLM }),
    row("r4", { title: "时间轴界面带截图的视觉与交互审查", createdAt: "2026-09-26T06:38:00Z", state: "executing", status: "running",
      category: "active", shutdownConfirmed: false, configuration: SONNET }),
    row("r6", { title: "修正折叠区间展开后的键盘焦点顺序", createdAt: "2026-09-26T07:05:00Z", state: "delivered", status: "completed",
      category: "review", configuration: GLM }),
  ];
  const spans: TimelineSpan[] = [
    span("s-r1-q", "r1", "queue", "2026-09-26T01:12:00Z", "2026-09-26T01:14:00Z"),
    span("s-r1-e", "r1", "execution", "2026-09-26T01:14:00Z", "2026-09-26T01:58:00Z",
      { turnIndex: 1, attemptId: "att-r1", configuration: OPUS, shutdownConfirmed: true, resultStatus: "ok" }),
    span("s-r2-q", "r2", "queue", "2026-09-26T01:20:00Z", "2026-09-26T01:21:00Z"),
    span("s-r2-r", "r2", "routing", "2026-09-26T01:21:00Z", "2026-09-26T01:24:00Z", { decisionTaskId: "run-d02b" }),
    span("s-r2-e1", "r2", "execution", "2026-09-26T01:24:00Z", "2026-09-26T02:10:00Z",
      { turnIndex: 1, attemptId: "att-r2a", configuration: CODEX, shutdownConfirmed: true }),
    span("s-r2-w", "r2", "host", "2026-09-26T02:10:00Z", "2026-09-26T03:00:00Z",
      { state: "approved", requestId: "req-schema", summary: "schema 版本号需与性能优化的合入顺序对齐" }),
    span("s-r2-e2", "r2", "execution", "2026-09-26T03:00:00Z", "2026-09-26T03:10:00Z",
      { turnIndex: 2, attemptId: "att-r2b", configuration: CODEX, shutdownConfirmed: true }),
    span("s-r3-q", "r3", "queue", "2026-09-26T01:38:00Z", "2026-09-26T01:40:00Z"),
    span("s-r3-e1", "r3", "execution", "2026-09-26T01:40:00Z", "2026-09-26T02:02:00Z",
      { turnIndex: 1, attemptId: "att-r3a", configuration: GLM, resultStatus: "failed", error: "测试命令超时", shutdownConfirmed: true }),
    span("s-r3-e2", "r3", "execution", "2026-09-26T02:04:00Z", "2026-09-26T02:30:00Z",
      { turnIndex: 2, attemptId: "att-r3b", configuration: GLM, shutdownConfirmed: true, resultStatus: "ok" }),
    span("s-r5-q", "r5", "queue", "2026-09-26T05:30:00Z", "2026-09-26T05:31:00Z"),
    span("s-r5-e", "r5", "execution", "2026-09-26T05:31:00Z", "2026-09-26T05:50:00Z",
      { turnIndex: 1, attemptId: "att-r5", configuration: GLM, resultStatus: "cancelled", shutdownConfirmed: true }),
    span("s-r4-q", "r4", "queue", "2026-09-26T06:38:00Z", "2026-09-26T06:40:00Z", { state: "claimed" }),
    span("s-r4-e", "r4", "execution", "2026-09-26T06:40:00Z", null,
      { state: "executing", turnIndex: 1, attemptId: "att-r4", configuration: SONNET, shutdownConfirmed: false }),
    span("s-r6-e", "r6", "execution", "2026-09-26T07:05:00Z", "2026-09-26T07:38:00Z",
      { state: "uncertain", uncertain: true, turnIndex: 1, attemptId: "att-r6", configuration: GLM, shutdownConfirmed: false }),
  ];
  const events: TimelineEvent[] = [
    event(1, "r1", "dispatch", "2026-09-26T01:12:00Z", { label: "派发" }),
    event(2, "r2", "dispatch", "2026-09-26T01:20:00Z", { label: "派发" }),
    event(3, "r3", "dispatch", "2026-09-26T01:38:00Z", { label: "授权协助任务" }),
    event(4, "r1", "accept", "2026-09-26T02:20:00Z", { label: "验收" }),
    event(5, "r2", "decide", "2026-09-26T03:00:00Z", { label: "决定" }),
    event(6, "r2", "continue", "2026-09-26T03:02:00Z", { label: "续接" }),
    event(7, "r2", "accept", "2026-09-26T03:30:00Z", { label: "验收" }),
    event(8, "r5", "cancel", "2026-09-26T05:50:00Z", { label: "取消" }),
    event(9, "r4", "dispatch", "2026-09-26T06:38:00Z", { label: "派发" }),
  ];
  return {
    objective: objectiveSummary(),
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

export function objectivePageFixture(objectives: ObjectiveSummary[] = [objectiveSummary()]): ObjectivePage {
  return { objectives, total: objectives.length, nextCursor: null, cursor: 41, changed: false };
}

/** The standalone-root and second-project rows used by the list tests. */
export function listFixture(): ObjectiveSummary[] {
  return [
    objectiveSummary({ objectiveId: "obj-1", lastActivitySeq: 41, lastActivityAt: "2026-09-26T08:12:00Z", state: "active" }),
    objectiveSummary({ objectiveId: "obj-2", title: "0.13 控制台入口候选版收尾与文档", lastActivitySeq: 30,
      lastActivityAt: "2026-09-26T04:40:00Z", state: "review",
      counts: { roots: 6, helpers: 0, active: 0, host: 0, review: 1, ended: 5 } }),
    objectiveSummary({ objectiveId: "run:3f0e", kind: "standalone", title: "修复标题回退在 CRLF 输入下的显示",
      titleSource: "task", lastActivitySeq: 22, lastActivityAt: "2026-09-25T10:22:00Z", state: "ended",
      sourceHostId: "codex-cli",
      counts: { roots: 1, helpers: 0, active: 0, host: 0, review: 0, ended: 1 } }),
    objectiveSummary({ objectiveId: "obj-4", title: "DSH 插件在 Windows 路径下的沙箱回归排查",
      project: project("p2", "dsh-harness", "~/Desktop/codespace/dsh-harness"),
      sourceHostId: "dsh-host-02", lastActivitySeq: 39, lastActivityAt: "2026-09-26T08:11:00Z", state: "host",
      counts: { roots: 2, helpers: 0, active: 1, host: 1, review: 0, ended: 0 } }),
  ];
}
