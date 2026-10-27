import { z } from "zod";
import { dateString, integer, routerHealthIdentitySchema, safeCount } from "./types";
import type { Reason } from "./reason";
import type { Actor } from "./types";
import type { RoutingMode } from "./types";

export type MacroTaskSort = "activity-desc" | "activity-asc" | "created-desc" | "created-asc";
export type MacroTaskFilter = "all" | "active" | "host" | "review";
/** Disjoint member states in display priority 待决定 (host) > 进行中 (active) > 待验收 (review) > 已结束 (ended). */
type MacroTaskCategory = MacroTaskSummary["state"];
const macroTaskSummarySchema = z.looseObject({
  macroTaskId: z.string().min(1), kind: z.enum(["macro-task", "standalone"]), title: z.string().nullable(),
  titleSource: z.enum(["macro-task", "title", "task", "none"]), description: z.string().nullable(), summary: z.string().nullable(),
  project: z.looseObject({ id: z.string(), path: z.string().nullable(), label: z.string() }).nullable(),
  sourceHostId: z.string().nullable(), currentHostIds: z.array(z.string()), createdAt: z.string().nullable(),
  lastActivityAt: z.string().nullable(), lastActivitySeq: safeCount, state: z.enum(["host", "active", "review", "ended"]),
  counts: z.looseObject({ roots: safeCount, helpers: safeCount, accepted: safeCount, failed: safeCount,
    active: safeCount, host: safeCount, review: safeCount, ended: safeCount, stoppableRoots: safeCount,
    stoppableHelpers: safeCount, delivered: safeCount, awaitingHost: safeCount }),
  matchingRuns: safeCount, rootRunIds: z.array(z.string()),
});
export type MacroTaskSummary = z.infer<typeof macroTaskSummarySchema>;
export type MacroTaskQuery = {
  limit?: number; before?: string; projectId?: string; hostId?: string;
  query?: string; filter?: MacroTaskFilter; sort?: MacroTaskSort;
};
export const macroTaskPageSchema = z.object({
  macroTasks: z.array(macroTaskSummarySchema), totals: z.looseObject({ all: safeCount, active: safeCount, host: safeCount, review: safeCount }),
  nextCursor: z.string().nullable(), cursor: integer, changed: z.boolean(),
});
export type MacroTaskPage = z.infer<typeof macroTaskPageSchema>;
export type TimelineBuddy = {
  harness: string | null; provider: string | null; model: string | null; effort: string | null;
};
const timelineRemindersSchema = z.looseObject({
  overdue: z.boolean(), possiblyStuck: z.boolean(), lastNativeEventAt: dateString.nullable(), toolInProgress: z.boolean(),
});
const timelineInquirySchema = z.looseObject({ inquiryId: z.string().min(1), createdAt: dateString, state: z.string().min(1) });
const timelineRowSchema = z.looseObject({
  delegationAuthority: z.boolean(), title: z.string().nullable(), taskSummary: z.string().nullable(),
  fixes: z.string().min(1).optional(), fixesTitle: z.string().nullable().optional(),
  fixesTitleSource: z.enum(["title", "task", "none"]).optional(), fixedBy: z.array(z.string().min(1)).min(1).optional(),
}).refine(row => (Object.hasOwn(row, "fixes")
  ? row.fixes !== undefined && row.fixesTitle !== undefined && row.fixesTitleSource !== undefined
  : !Object.hasOwn(row, "fixesTitle") && !Object.hasOwn(row, "fixesTitleSource"))
  && (!Object.hasOwn(row, "fixedBy") || row.fixedBy !== undefined));
export type TimelineRow = z.infer<typeof timelineRowSchema> & {
  runId: string; parentRunId: string | null; rootRunId: string;
  titleSource: "title" | "task" | "none";
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
  /** Lead owning this row's root tree, including helper descendants. */
  rootLeadRunId: string | null;
  /** Recorded owner of a Lead's own micro task, if any. */
  leadOwnership?: { leadRunId: string; leadTurnId: string; createdAt: string } | null;
  buddy: TimelineBuddy | null;
  acceptedAt: string | null;
  reviewRejectedAt: string | null;
  reviewRejectionNote: string | null;
};
type TimelineRouting = {
  routingMode?: RoutingMode | null;
  routerBuddy?: TimelineBuddy | null;
  selectedBuddy?: TimelineBuddy | null;
  reason?: Reason | null;
  note?: string | null;
  policyCheck?: {
    taskPreference?: { ruleIndex?: number | null; outcome?: string | null } | null;
    userPreference?: string | null;
    [key: string]: unknown;
  } | null;
  /** Host owns the budget schema; preserve it without offering budget controls. */
  budget?: Record<string, unknown> | null;
  usage?: { elapsedMs?: number | null; toolCalls?: number | null; bytesRead?: number | null } | null;
};
// Non-execution kinds, including unknown or absent kinds, carry only null
// timing facts. Routing otherwise remains unchecked except for router identity.
const timelineSpanSchema = z.looseObject({
  // Only the execution/non-execution distinction was checked, not the kind enum.
  kind: z.custom<"queue" | "routing" | "execution" | "host">(), expectedSeconds: z.int().positive().nullable(),
  reminders: timelineRemindersSchema.nullable(), inquiries: z.array(timelineInquirySchema).nullable(),
  routing: z.custom<TimelineRouting | null>().optional(),
}).refine(span => span.kind === "execution" ? span.inquiries !== null
  : span.expectedSeconds === null && span.reminders === null && span.inquiries === null)
  .refine(span => routerHealthIdentitySchema.nullish().safeParse(span.routing?.routerBuddy).success);
export type TimelineSpan = z.infer<typeof timelineSpanSchema> & {
  spanId: string; runId: string;
  startAt: string | null; endAt: string | null;
  /**
   * queue: queued (open) or admitted; execution/routing: the attempt's execution state
   * (starting, executing, finalizing, uncertain, finished, or rejected before the model started); host: the request state
   * (open, approved, declined, superseded, cancelled).
   */
  state: string;
  attemptId: string | null; turnId: string | null; turnIndex: number | null;
  requestId: string | null; buddy: TimelineBuddy | null;
  shutdownConfirmed: boolean | null; uncertain: boolean; clockSkew: boolean;
  generation?: number; disposition?: string; error?: string;
  /** Own durable attempt receipt; a model turn disposition is not the execution result. */
  resultStatus?: "ok" | "failed" | "cancelled";
  requestKind?: string; summary?: string | null; reason?: Reason | null; decisionTaskId?: string;
  /** Native usage of the span's own execution; a shape the console does not read is unknown. */
  tokenUsage?: unknown;
  /** Exact recorded decision; never infer it from the run's current route. */
  decisionId?: string | null;
};
export type TimelineEvent = {
  seq: number; runId: string;
  /** A current service event kind; kept open for newer kinds. */
  kind: string; at: string;
  decision: string | null; verdict: string | null; state: string | null; strategy: string | null; note: string | null;
  actor: Actor | null;
  attemptId: string | null; requestId: string | null; artifactId: string | null;
  integrationId?: string | null; continuationId?: string | null; eventKind?: string;
  stopInitiator?: "host" | "lead" | "console" | "service" | "unknown" | null;
};
export const macroTaskTimelineSchema = z.object({
  macroTask: macroTaskSummarySchema, observedAt: z.string(), cursor: integer,
  rows: z.array(timelineRowSchema.transform(row => row as TimelineRow)),
  spans: z.array(timelineSpanSchema.transform(span => span as TimelineSpan)), events: z.array(z.custom<TimelineEvent>()),
  totals: z.looseObject({ rows: integer, spans: integer, events: integer, allRows: integer }),
  truncated: z.looseObject({ rows: z.boolean(), spans: z.boolean(), events: z.boolean() }), filtered: z.boolean(), scopeComplete: z.boolean(),
});
// Only a subset of each row/span was checked by the boundary. Their remaining
// projection fields retain their types without imposing new acceptance rules.
export type MacroTaskTimeline = z.infer<typeof macroTaskTimelineSchema>;
/** Reply of `macro_task_stop`: one result for every member of the macro task. */
export type MacroTaskStopResult = {
  macroTaskId: string;
  duplicate: boolean;
  /** Acknowledgement is not shutdown evidence; the timeline proves each stop. */
  results: ReadonlyArray<{ runId: string; state: string; stopped: boolean }>;
};
