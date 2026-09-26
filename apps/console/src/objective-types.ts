export type ObjectiveFilter = "all" | "active" | "host" | "review";
/** Disjoint member states in display priority 待决定 (host) > 进行中 (active) > 待验收 (review) > 已结束 (ended). */
export type ObjectiveCategory = "host" | "active" | "review" | "ended";
export type ObjectiveCounts = { roots: number; helpers: number; active: number; host: number; review: number; ended: number };
export type ObjectiveSummary = {
  /** `obj-<id>` for an explicit objective, `run:<rootRunId>` for a standalone root delegation. */
  objectiveId: string;
  kind: "objective" | "standalone";
  /** Objective title, or the root's display title; `titleSource: "none"` means the 未命名委派 fallback. */
  title: string;
  titleSource: "objective" | "title" | "summary" | "task" | "none";
  project: { id: string; path: string | null; label: string };
  sourceHostId: string | null;
  currentHostIds: string[];
  createdAt: string;
  lastActivityAt: string;
  lastActivitySeq: number;
  /** Summary state: the highest-priority category present among the members. */
  state: ObjectiveCategory;
  counts: ObjectiveCounts;
  /** Members that match the current filters; counts stay overall. */
  matchingRuns: number;
  rootRunIds: string[];
};
export type ObjectiveQuery = {
  limit?: number; before?: string; projectId?: string; hostId?: string;
  query?: string; filter?: ObjectiveFilter;
};
export type ObjectivePage = {
  objectives: ObjectiveSummary[]; total: number; nextCursor: string | null;
  /** Current event head. */
  cursor: number;
  /** True when activity after the first page may have reordered groups: offer a refresh. */
  changed: boolean;
};
export type TimelineConfiguration = {
  adapter: string | null; provider: string | null; model: string | null; effort: string | null;
};
export type TimelineRow = {
  runId: string; parentRunId: string | null; rootRunId: string;
  title: string;
  titleSource: "title" | "summary" | "task" | "none";
  createdAt: string;
  /** Governed workflow state (for example executing, awaiting-host, delivered, accepted). */
  state: string;
  /** Execution task status (queued, running, completed, failed, cancelled, ...). */
  status: string;
  category: ObjectiveCategory;
  /** Every recorded attempt proved its stop; unknown is never reported as stopped. */
  shutdownConfirmed: boolean;
  /** 0 for a root, +1 per helper level. Rows arrive in tree order. */
  depth: number;
  kind: "goal" | "helper";
  configuration: TimelineConfiguration | null;
  acceptedAt: string | null;
  acceptanceVerdict: string | null;
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
  requestId: string | null; configuration: TimelineConfiguration | null;
  shutdownConfirmed: boolean | null; uncertain: boolean; clockSkew: boolean;
  generation?: number; disposition?: string; error?: string;
  requestKind?: string; summary?: string; decisionTaskId?: string;
};
export type TimelineEventKind =
  "dispatch" | "decide" | "continue" | "integrate" | "accept" | "reject" | "cancel" | "takeover";
export type TimelineEvent = {
  seq: number; runId: string;
  /** One of TimelineEventKind from the current service; kept open for newer kinds. */
  kind: string; at: string;
  label: string; summary: string; actor: string | null;
  attemptId: string | null; requestId: string | null; artifactId: string | null;
  integrationId?: string | null; continuationId?: string | null; eventKind?: string;
};
export type ObjectiveTimeline = {
  objective: ObjectiveSummary; observedAt: string; cursor: number;
  rows: TimelineRow[]; spans: TimelineSpan[]; events: TimelineEvent[];
  /** rows counts the filtered scope; allRows counts every delegation of the group. */
  totals: { rows: number; spans: number; events: number; allRows: number };
  truncated: { rows: boolean; spans: boolean; events: boolean };
  filtered: boolean;
  scopeComplete: boolean;
};
