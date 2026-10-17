import type { MacroTaskTimeline, TimelineRow } from "../macro-task-types";
import type { Actor, ActivityPhase, RoutingMode, Task, TokenUsage } from "../types";

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

/** A turn with its result; `summary` is null while the turn has returned nothing. */
export type ResultTurn = Turn & { summary: string | null; remaining: string[] };

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

/** A sealed output of a turn: the only artifacts the detail draws. */
export type Artifact = {
  artifactId: string;
  kind: "output" | "resolved-output" | "partial-output";
  attemptId: string;
  manifestSha256: string;
  outputCommit: string | null;
  diffPath: string | null;
  changedPaths: string[];
  partial: boolean;
  /** Stated only by a partial output, which is neither verified nor final; null for a sealed output, which states neither. */
  verified: boolean | null;
  final: boolean | null;
  cumulativePatch: CumulativePatch | null;
};

export type Integration = {
  integrationId: string;
  artifactId: string;
  state: string;
  strategy: string;
  target: { path: string | null; ref: string | null } | null;
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
  actor: Actor;
  createdAt: string;
  runRevision: number | null;
};

export type Routing = {
  status: string;
  decisionId: string | null;
  source: string | null;
  /** The bounds of the current route. */
  basis: Basis;
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
  /** The newest turns only; `counts.turns` is how many there are. */
  turns: Turn[];
  currentTurn: ResultTurn | null;
  activeRequest: OpenRequest | null;
  requests: CompactRequest[];
  counts: { turns: number; openRequests: number };
  children: { taskId: string; role: string; state: "active" | "attention" | "succeeded" | "failed" | "cancelled" }[];
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
  turns: ResultTurn[];
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

/** What was frozen when a routing decision was asked for: the bounds the program chose within. */
export type Basis = {
  constraints: Partial<Buddy>;
  requiredCapabilities: string[];
  /** How many candidates were legal; null when it was not recorded. */
  candidateCount: number | null;
  excluded: { buddy: Partial<Buddy>; reason: string | null }[];
  /** The preferences the program applied when it chose the only candidate. */
  preferences: { buddyId: string; mode: "prefer" | "pin" | "exclude"; reason: string | null }[];
};

export type InquiryState = "queued" | "claimed" | "delivered" | "answered" | "discarded" | "unavailable";

export type Inquiry = {
  messageId: string;
  inquiryId: string;
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

/** What the task view reports of the activity of the attempt that runs now. */
export type Activity = {
  phase: ActivityPhase;
  observedAt: string | null;
  toolName: string | null;
  modelTurns: number | null;
  toolCalls: number | null;
  lastToolAt: string | null;
  lastNativeAt: string | null;
  eventSeq: number | null;
  /** What backs the report: a tool, native activity, or the phase alone (which says nothing of progress). */
  basis: "tool" | "native" | "phase" | "unknown";
};

export type Ending = {
  /** Why the execution stopped, as recorded; null when the cause is not recorded. */
  cause: string | null;
  /** Who stopped it, when it was stopped. */
  stoppedBy: "host" | "lead" | "console" | "service" | "unknown" | null;
  actor: Actor | null;
  reason: string | null;
  quota: "quota-exceeded" | "rate-limited" | null;
};

/** Where an execution's native session lives, in the receipt's own words. */
export type Session = {
  id: string | null;
  scope: string | null;
  owner: string | null;
  visibility: string | null;
  resumable: boolean | null;
  binding: boolean | null;
  note: string | null;
};

/** The execution the task view's per-attempt facts are about: the one running, else the last one chosen. */
export type Attempt = {
  attemptId: string;
  active: boolean;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
  overdue: boolean;
  possiblyStuck: boolean;
  activity: Activity | null;
  /** Null when it ended as it should. */
  ending: Ending | null;
  session: Session | null;
  tokenUsage: TokenUsage | null;
  shutdownConfirmed: boolean;
};

/** The task view of one micro task, with the parts the detail reads made definite. */
export type TaskView = {
  /** For the presentation helpers the list shares; the detail reads its own facts from the fields beside it. */
  task: Task;
  updatedAt: string;
  attempt: Attempt | null;
  /** The micro task belongs to a Lead, whose decision it waits for. */
  lead: boolean;
};

/** What every pane of one micro task's detail draws from. */
export type Context = {
  view: TaskView;
  workflow: Workflow;
  /** Null until the audit has been read: what only it holds is then still to come. */
  audit: Audit | null;
  routing: RoutingEntry[];
  bases: Record<string, Basis>;
  inquiries: Inquiries | null;
  /** Where each inquiry is shown, by the key of the step: none is left out. */
  asked: Record<string, Inquiry[]>;
  highlightedInquiry?: string | null;
  timeline: MacroTaskTimeline | null;
  row: TimelineRow | null;
  nowMs: number;
  /** Opens another micro task's detail. */
  open: (runId: string) => void;
};
