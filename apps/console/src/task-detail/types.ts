import type { MacroTaskTimeline, TimelineRow } from "../macro-task-types";
import type { Actor, RoutingMode, Task, TokenUsage } from "../types";

/** One buddy as the board records it: harness, provider, model and reasoning effort. */
export type Buddy = { harness: string; provider: string; model: string; effort: string };

/** One execution of a micro task, as the compact collaboration record indexes it. */
export type Turn = {
  turnId: string;
  turnIndex: number;
  attemptId: string;
  expectedSeconds: number | null;
  resumeMode: string;
  sessionId: string | null;
  state: string;
  disposition: string | null;
  createdAt: string;
  updatedAt: string;
  buddy: Buddy | null;
  /** The frozen route of this turn; null when it was not recorded. A null decision is a Host's own pick. */
  routing: { decisionId: string | null; buddyRevision: number } | null;
  tokenUsage: TokenUsage | null;
};

/** The newest turn also carries its result. */
export type CurrentTurn = Turn & { summary: string; summaryTruncated: boolean; remaining: string[] };

export type CompactRequest = {
  requestId: string;
  kind: string;
  state: string;
  createdAt: string;
  decidedAt: string | null;
  childTaskId: string | null;
  routing: boolean;
};

export type OpenRequest = CompactRequest & {
  summary: string;
  attempted: string | null;
  neededWork: string[];
  expectedArtifacts: string[];
  acceptance: string | null;
  preparationError: { code: string; message: string } | null;
};

export type CumulativePatch = {
  baseCommit: string | null;
  outputCommit: string | null;
  path: string | null;
  sha256: string | null;
  changedPaths: string[];
};

export type Artifact = {
  artifactId: string;
  kind: string;
  attemptId: string;
  sourceTaskId: string;
  manifestSha256: string;
  outputCommit: string | null;
  diffPath: string | null;
  changedPaths: string[];
  partial: boolean;
  cumulativePatch: CumulativePatch | null;
};

export type Integration = {
  integrationId: string;
  artifactId: string;
  state: string;
  strategy: string;
  target: { path: string; ref: string } | null;
  beforeCommit: string | null;
  afterCommit: string | null;
  verificationSummary: string | null;
  hostPaths: string[];
  reason: string | null;
  actor: Actor | null;
  createdAt: string;
};

export type HostConclusion = {
  conclusionId: string;
  executionStatus: string;
  note: string;
  evidence: string[];
  artifactId: string | null;
  integrationId: string | null;
  attemptId: string | null;
  actor: Actor | null;
  createdAt: string;
  runRevision: number | null;
};

export type Routing = {
  status: string;
  decisionId: string | null;
  source: string | null;
};

/** The compact collaboration record of a micro task (`workflow_get {runId}`). */
export type Workflow = {
  runId: string;
  revision: number;
  state: string;
  hostId: string | null;
  buddy: Buddy | null;
  buddyLocked: boolean;
  delegationAuthority: boolean;
  routing: Routing | null;
  turns: Turn[];
  currentTurn: CurrentTurn | null;
  activeRequest: OpenRequest | null;
  requests: CompactRequest[];
  counts: { openRequests: number };
  children: { taskId: string; role: string; state: string }[];
  artifacts: Artifact[];
  integrations: Integration[];
  finalArtifactId: string | null;
  hostConclusion: HostConclusion | null;
  cwd: string | null;
  shutdown: { selfConfirmed: boolean; descendantsConfirmed: boolean; unconfirmedCount: number } | null;
};

/** One full request of the audit, with the decision made on it. */
export type AuditRequest = OpenRequest & {
  turnId: string | null;
  decision: { decision: string | null; reason: string | null; actor: Actor | null } | null;
  /** The routing decision a routing boundary stopped at. */
  decisionId: string | null;
};

export type Continuation = { continuationId: string; turnId: string | null; input: string; createdAt: string; kind: string; reason: string | null };

/** What only the audit read carries (`workflow_get {runId, includeAudit: true}`). */
export type Audit = {
  turns: { turnId: string; summary: string; remaining: string[] }[];
  continuations: Continuation[];
  requests: AuditRequest[];
  hostConclusions: HostConclusion[];
  revision: number;
};

/** One recorded routing decision of a micro task (`workflow_get`'s `routingHistory`). */
export type RoutingEntry = {
  decisionId: string;
  status: string;
  source: string | null;
  selectedBuddy: Buddy | null;
  routerBuddy: Buddy | null;
  routingMode: RoutingMode | null;
  reason: string | null;
};

export type InquiryState = "queued" | "claimed" | "delivered" | "answered" | "discarded" | "unavailable";

export type Inquiry = {
  messageId: string;
  attemptId: string | null;
  question: string;
  author: string;
  createdAt: string;
  state: InquiryState;
  reason: string | null;
  deliveredAt: string | null;
  answer: { text: string; at: string | null; truncated: boolean } | null;
};

export type Inquiries = {
  /** Oldest first. */
  items: Inquiry[];
  /** True when the board returned as many as asked for: earlier ones may not be listed. */
  more: boolean;
};

/** The task view of one run plus the instant the server last checked it. */
export type TaskRead = { task: Task; verifiedAtMs: number | null };

/** What every pane of one micro task's detail draws from. */
export type Context = {
  task: Task;
  workflow: Workflow;
  /** Null until the audit has been read: what only it holds is then still to come. */
  audit: Audit | null;
  routing: RoutingEntry[];
  inquiries: Inquiries | null;
  timeline: MacroTaskTimeline | null;
  row: TimelineRow | null;
  nowMs: number;
  /** The micro task belongs to a Lead, whose decision it waits for. */
  lead: boolean;
  /** Opens another micro task's detail. */
  open: (runId: string) => void;
};
