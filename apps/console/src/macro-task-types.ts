import type { RoutingMode } from "./types";

export type MacroTaskFilter = "all" | "active" | "host" | "review";
/** Disjoint member states in display priority 待决定 (host) > 进行中 (active) > 待验收 (review) > 已结束 (ended). */
export type MacroTaskCategory = "host" | "active" | "review" | "ended";
export type MacroTaskCounts = {
  roots: number; helpers: number;
  /** Accepted root micro tasks; the numerator of progress against the root count. */
  accepted: number;
  active: number; host: number; review: number; ended: number;
};
export type MacroTaskSummary = {
  /** `macro-<id>` for an explicit macroTask, or `run:<rootRunId>` for a standalone root micro task. */
  macroTaskId: string;
  kind: "macro-task" | "standalone";
  /** MacroTask title, or the root's display title; `titleSource: "none"` means the 未命名微任务 fallback. */
  title: string;
  titleSource: "macro-task" | "title" | "task" | "none";
  /** Immutable creation-time description in the user's words; omitted historic descriptions stay null. */
  description: string | null;
  /** Latest recorded own result, separate from intent; groups do not synthesize one. */
  summary: string | null;
  project: { id: string; path: string | null; label: string };
  sourceHostId: string | null;
  currentHostIds: string[];
  createdAt: string;
  lastActivityAt: string;
  lastActivitySeq: number;
  /** Summary state: the highest-priority category present among the members. */
  state: MacroTaskCategory;
  counts: MacroTaskCounts;
  /** Members that match the current filters; counts stay overall. */
  matchingRuns: number;
  rootRunIds: string[];
};
export type MacroTaskQuery = {
  limit?: number; before?: string; projectId?: string; hostId?: string;
  query?: string; filter?: MacroTaskFilter;
};
export type MacroTaskPage = {
  macroTasks: MacroTaskSummary[]; total: number; nextCursor: string | null;
  /** Current event head. */
  cursor: number;
  /** True when activity after the first page may have reordered groups: offer a refresh. */
  changed: boolean;
};
export type TimelineBuddy = {
  harness: string | null; provider: string | null; model: string | null; effort: string | null;
};
export type TimelineRow = {
  runId: string; parentRunId: string | null; rootRunId: string;
  title: string;
  titleSource: "title" | "task" | "none";
  /** First nonempty task line (at most 200 characters); the 做什么/微任务摘要 source. */
  taskSummary: string | null;
  summary: string | null;
  createdAt: string;
  /** Micro task workflow state (for example executing, awaiting-host, delivered, accepted). */
  state: string;
  /** Execution task status (queued, running, completed, failed, cancelled, ...). */
  status: string;
  category: MacroTaskCategory;
  /** Every recorded attempt proved its stop; unknown is never reported as stopped. */
  shutdownConfirmed: boolean;
  /** 0 for a root, +1 per helper level. Rows arrive in tree order. */
  depth: number;
  kind: "micro-task" | "helper";
  /** Recorded owner of a Lead's own micro task, if any. */
  leadOwnership?: { leadRunId: string; leadTurnId: string; createdAt: string } | null;
  buddy: TimelineBuddy | null;
  acceptedAt: string | null;
  acceptanceVerdict: string | null;
  reviewRejectedAt: string | null;
  reviewRejectionNote: string | null;
};
export type TimelineSpan = {
  spanId: string; runId: string; kind: "queue" | "routing" | "execution" | "host";
  startAt: string | null; endAt: string | null;
  /**
   * queue: queued (open) or claimed; execution/routing: the attempt's execution state
   * (starting, executing, finalizing, uncertain, finished); host: the request state
   * (open, approved, declined, superseded, cancelled).
   */
  state: string;
  attemptId: string | null; turnId: string | null; turnIndex: number | null;
  requestId: string | null; buddy: TimelineBuddy | null;
  shutdownConfirmed: boolean | null; uncertain: boolean; clockSkew: boolean;
  generation?: number; disposition?: string; error?: string;
  /** Own durable attempt receipt; a model turn disposition is not the execution result. */
  resultStatus?: "ok" | "failed" | "cancelled";
  requestKind?: string; summary?: string; decisionTaskId?: string;
  /** Exact recorded decision; never infer it from the run's current route. */
  decisionId?: string | null;
  /** Optional projection for historic spans without routing facts. */
  routing?: {
    routingMode?: RoutingMode | null;
    selectedBuddy?: TimelineBuddy | null;
    reason?: string | null;
    policyCheck?: {
      taskPreference?: { ruleIndex?: number | null; outcome?: string | null } | null;
      userPreference?: string | null;
      [key: string]: unknown;
    } | null;
    /** Host owns the budget schema; preserve it without offering budget controls. */
    budget?: Record<string, unknown> | null;
    usage?: { elapsedMs?: number | null; toolCalls?: number | null; bytesRead?: number | null } | null;
  } | null;
};
export type TimelineEventKind =
  "dispatch" | "decide" | "continue" | "integrate" | "accept" | "reject" | "conclude" | "cancel" | "takeover";
export type TimelineEvent = {
  seq: number; runId: string;
  /** One of TimelineEventKind from the current service; kept open for newer kinds. */
  kind: string; at: string;
  label: string; summary: string; actor: string | null;
  attemptId: string | null; requestId: string | null; artifactId: string | null;
  integrationId?: string | null; continuationId?: string | null; eventKind?: string;
  stopInitiator?: "host" | "lead" | "console" | "service" | "unknown" | null;
};
export type MacroTaskTimeline = {
  macroTask: MacroTaskSummary; observedAt: string; cursor: number;
  rows: TimelineRow[]; spans: TimelineSpan[]; events: TimelineEvent[];
  /** rows counts the filtered scope; allRows counts every micro task of the group. */
  totals: { rows: number; spans: number; events: number; allRows: number };
  truncated: { rows: boolean; spans: boolean; events: boolean };
  filtered: boolean;
  scopeComplete: boolean;
};
/** Reply of `macro_task_stop`: the affected scope and per-root cancellation results. */
export type MacroTaskStopResult = {
  macroTaskId: string;
  /** Roots and helpers whose cancellation was requested in this stop. */
  runIds: string[];
  /** Accepted roots retained by the stop; they keep their recorded outcome. */
  acceptedRunIds: string[];
  /** Concluded roots retained by the stop; they keep their Host conclusion. */
  concludedRunIds: string[];
  /** Per-root cancellation results; acknowledgement is not termination evidence. */
  results: ReadonlyArray<{ runId: string; result: string }>;
};
