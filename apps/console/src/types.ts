export type Profile = {
  profileId: string;
  label: string;
  adapter: string;
  provider: string;
  model: string;
  effort: string;
  available: boolean;
  enabled: boolean;
  capabilities: string[];
  contextWindow: number | null;
  description: string;
  source: string;
  unavailableReason?: string;
};
export type Preference = {
  profileId: string;
  mode: "prefer" | "pin" | "exclude";
  reason: string;
};
/**
 * Automatic, evidence-linked assessment. Published only by a maintenance
 * Harness through `assessment_publish`; the human console never writes it.
 * `origin` is the recorded authorship: "maintenance" or "unattributed"; an
 * absent value is unknown history and must not be presented as automatic.
 */
export type Card = {
  profileId: string;
  revision: number;
  summary: string;
  strengths: string[];
  limitations: string[];
  risks: string[];
  evidenceIds: string[];
  updatedAt: string | null;
  origin?: string;
};
/** One human opinion, stored apart from the automatic card and its evidence. */
export type Annotation = {
  profileId: string;
  text: string;
  revision: number;
  updatedAt: string | null;
};
export type Evidence = {
  evidenceId: string;
  profileId: string;
  kind: string;
  summary: string;
  project: string | null;
  conditions: string[];
  source: string;
  runId: string | null;
  createdAt: string;
};
export type Decision = {
  decisionId: string;
  kind?: "select" | "maintain";
  runId?: string | null;
  requestId?: string;
  status: string;
  task: string;
  profileId: string | null;
  tableRevision: number;
  reason: string;
  evidenceIds: string[];
  createdAt: string;
  error?: string | null;
  updatedAt?: string;
};
export type Delegation = {
  kind: "goal" | "helper" | "decision" | "execution";
  sourceHostId: string | null;
  currentHostId: string | null;
  parentRunId: string | null;
  rootRunId: string | null;
  project: { id: string; path: string | null; label: string };
  configuration: { adapter: string; provider: string; model: string; effort: string } | null;
};
export type TaskPage = { runs: Task[]; total: number; nextCursor: string | null };
export type TaskQuery = {
  limit?: number; before?: string; rootsOnly?: boolean;
  query?: string; projectId?: string; hostId?: string;
  filter?: "all" | "active" | "host" | "review";
};
export type Task = {
  runId: string;
  task: string;
  status: string;
  owner: string;
  cwd: string;
  revision: number;
  createdAt: string;
  acceptedAt: string | null;
  acceptanceVerdict: string | null;
  queueReason?: string | null;
  delegation?: Delegation;
  spec?: Record<string, unknown>;
  /** Bounded per-attempt activity projection; null or absent means unknown. */
  activity?: TaskActivity | null;
  /** Recorded real termination cause when the task view carries it directly. */
  terminationReason?: string | null;
  /** Selected attempt receipt; its `result.terminationReason` is the durable cause. */
  selectedAttempt?: AttemptReceipt | null;
  workflow?: {
    state: string;
    awaitingHost: boolean;
    hostId: string;
    ownerGeneration: number;
    revision: number;
    requestSummary?: string;
  };
  workflowShutdown?: Shutdown;
  [key: string]: unknown;
};
/** Public attempt receipt. The claim capability and nonce verifier never appear. */
export type AttemptReceipt = {
  attemptId?: string;
  generation?: number;
  executionState?: string;
  resultAvailable?: boolean;
  shutdownConfirmed?: boolean;
  result?: Record<string, unknown> | null;
  error?: string | null;
  terminationReason?: string | null;
};
/** Phases the frozen ADR-010 activity projection allows; nothing else claims progress. */
export type ActivityPhase =
  | "starting"
  | "waiting-model"
  | "streaming-model"
  | "tool-running"
  | "waiting-external"
  | "waiting-host"
  | "finishing"
  | "unknown";
export type ActivityCounts = {
  modelTurns?: number | null;
  toolCalls?: number | null;
};
/**
 * Bounded `worker_progress` activity. Unknown fields stay absent instead of being
 * invented; there is deliberately no percentage or completion estimate here.
 */
export type TaskActivity = {
  phase: ActivityPhase;
  observedAt?: string | null;
  eventSeq?: number | null;
  nativeSessionId?: string | null;
  lastNativeActivityAt?: string | null;
  lastToolActivityAt?: string | null;
  toolName?: string | null;
  waitingReason?: string | null;
  counts?: ActivityCounts | null;
};
export type Shutdown = {
  selfConfirmed: boolean;
  descendantsConfirmed: boolean;
  unconfirmedRunIds: string[];
  unconfirmedCount: number;
  truncated: boolean;
};
export type Gate = {
  phase: "open" | "draining" | "writing";
  readers: number;
  waitingWriters: number;
  writer: null | {
    writerId: string;
    kind: string;
    generation: number;
    expiresAt: string;
  };
};
export type Configuration = {
  revision: number;
  decisionProfileId: string | null;
};
export type Snapshot = {
  csrfToken: string;
  tableRevision: number;
  gate: Gate;
  configuration: Configuration;
  profiles: Profile[];
  preferences: Preference[];
  cards: Card[];
  /** Human opinions, kept apart from the automatic evidence-linked cards. */
  annotations: Annotation[];
  /**
   * Every unavailable configuration in the table, including one the snapshot
   * still lists (the retained decision selector). The console subtracts the
   * unavailable rows it has already loaded and pages the rest through
   * `model_profiles`; they are never silently dropped from the count.
   */
  unavailableProfileCount?: number;
  evidence: Evidence[];
  decisions: Decision[];
  /** Recorded verification samples per profile; independent of published card prose. */
  sampleCounts: Record<string, number>;
  tasks: { runs: Task[]; total: number };
  capabilities: Record<string, boolean>;
};
export type WriterGrant = {
  writerId: string;
  generation: number;
  writerToken: string;
  /** Gate phase while the intent waits or holds the table. */
  phase: string;
  /** Writer state from the board: "waiting", "active", "published", "aborted" or "expired". */
  state?: string;
  expiresAt: string;
  tableRevision: number;
  queuePosition?: number;
  waitingWriters?: number;
};
/** Local draft of the fields a human may publish through `user_policy_publish`. */
export type Draft = Pick<
  Snapshot,
  "profiles" | "preferences" | "annotations" | "configuration"
> & { tableRevision: number };
/** One page of retained profile identities, including unavailable history. */
export type ProfilePage = {
  profiles: Profile[];
  cards: Card[];
  annotations: Annotation[];
  preferences: Preference[];
  sampleCounts: Record<string, number>;
  tableRevision: number;
  nextCursor: string | null;
};
