export type ObjectiveFilter = "all" | "active" | "host" | "review";
export type ObjectiveCounts = { roots: number; helpers: number; active: number; host: number; review: number; ended: number };
export type ObjectiveSummary = {
  objectiveId: string;
  kind: "objective" | "standalone";
  title: string;
  project: { id: string; path: string | null; label: string };
  sourceHostId: string | null;
  createdAt: string;
  lastActivityAt: string;
  lastActivitySeq: number;
  counts: ObjectiveCounts;
  matchingRuns: number;
};
export type ObjectiveQuery = {
  limit?: number; before?: string; projectId?: string; hostId?: string;
  query?: string; filter?: ObjectiveFilter;
};
export type ObjectivePage = {
  objectives: ObjectiveSummary[]; total: number; nextCursor: string | null;
  cursor: number; changed: boolean;
};
export type TimelineConfiguration = {
  adapter: string | null; provider: string | null; model: string | null; effort: string | null;
};
export type TimelineRow = {
  runId: string; parentRunId: string | null; rootRunId: string;
  title: string; createdAt: string; state: string; status: string;
  shutdownConfirmed: boolean; depth: number;
};
export type TimelineSpan = {
  spanId: string; runId: string; kind: "queue" | "routing" | "execution" | "host";
  startAt: string | null; endAt: string | null; state: string;
  attemptId: string | null; turnId: string | null; turnIndex: number | null;
  requestId: string | null; configuration: TimelineConfiguration | null;
  shutdownConfirmed: boolean | null; uncertain: boolean; clockSkew: boolean;
};
export type TimelineEvent = {
  seq: number; runId: string; kind: string; at: string;
  label: string; summary: string; actor: string | null;
  attemptId: string | null; requestId: string | null; artifactId: string | null;
};
export type ObjectiveTimeline = {
  objective: ObjectiveSummary; observedAt: string;
  rows: TimelineRow[]; spans: TimelineSpan[]; events: TimelineEvent[];
  totals: { rows: number; spans: number; events: number };
  truncated: { rows: boolean; spans: boolean; events: boolean };
  scopeComplete: boolean;
};
