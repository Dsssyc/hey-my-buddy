/** The five recorded states of one harness in the console snapshot. */
export type HarnessStatus = "unknown" | "ready" | "missing" | "login-required" | "unhealthy";
/** One location discovery tried, and why it was not chosen. */
export type HarnessCandidate = {
  path?: string;
  source?: string;
  reasonCode?: string;
  status?: string;
};
/**
 * One harness's recorded health. `ready` carries the chosen executable, its
 * version and where it came from; every other status carries the attempted
 * locations and a remedy. `manualPath` is the user's stored override (`null`
 * means automatic detection) and `revision` records its current generation.
 */
export type HarnessHealth = {
  harness: string;
  status: HarnessStatus;
  available: boolean;
  revision: number;
  manualPath: string | null;
  command?: string[];
  executable?: string;
  version?: string;
  source?: string;
  candidates?: HarnessCandidate[];
  reasonCode?: string;
  remedy?: string;
  checkedAt?: string | null;
  expiresAt?: string | null;
  /** Latest recorded native quota observation; null or absent means unknown. */
  quota?: HarnessQuota | null;
  /** Persisted exhaustion records with their recovery state; absent means none recorded. */
  quotaRouting?: QuotaRoutingRecord[];
  readOnlyStructured?: ReadOnlyStructured;
  systemSandbox?: boolean;
  billingByProvider?: Record<string, BillingFact>;
  account?: import("./harness-account").HarnessAccount;
};

export type ReadOnlyStructured = {
  eligible: boolean;
  systemSandbox: boolean;
  reasonCode: string | null;
  reason: string | null;
  sameAttemptContinuation: boolean;
  implemented?: boolean;
};

export type BillingFact = {
  kind: "subscription" | "metered" | "unknown";
  source: string;
  observedAt: string;
};

/**
 * One execution's native token usage (ADR-018 §22). `scope` is `attempt`: the
 * numbers belong to this single execution, never to a session cumulative
 * total. `inputTokens` already includes the cached input, so
 * `cachedInputTokens` is a subset that must never be added again; a null field
 * is unknown and is never rendered as 0.
 */
export type TokenUsage = {
  inputTokens: number | null;
  cachedInputTokens: number | null;
  outputTokens: number | null;
  source: string;
  scope: "attempt";
  completeness?: "complete" | "partial" | "unknown";
  coverage?: "native-root-session" | "native-root-thread" | "native-attempt";
};

/**
 * One recorded native quota observation (ADR-018 §23). This is the latest
 * observation the harness reported, never a live account reading: `stale`
 * means it may already be out of date, an unknown `usedPercent` stays unknown
 * and is never shown as 0 or "available".
 */
export type QuotaWindow = {
  name: string;
  usedPercent: number | null;
  resetsAt: string | null;
  stale?: boolean;
};
export type HarnessQuota = {
  observedAt: string;
  source: string;
  provider?: string;
  stale?: boolean;
  reachedType?: string;
  ordinaryUsageAllowed?: boolean;
  balanceZero?: boolean;
  windows: QuotaWindow[];
};
/** The single-use routing retry window of one no-reset exhaustion record (ADR-019). */
export type QuotaRetryWindow = {
  eligibleAt: string;
  open: boolean;
  pendingManual: boolean;
  manualAt: string | null;
  consumedAt: string | null;
  consumedBy: string | null;
};
/** One persisted native exhaustion record with its recovery state (ADR-019). */
export type QuotaRoutingRecord = {
  provider: string;
  limitId: string | null;
  code: string | null;
  source: string;
  observedAt: string;
  resetsAt: string | null;
  blocked: boolean;
  retry: QuotaRetryWindow | null;
};

/**
 * The board's model-level catalog state (ADR-027 §2/§6), published on every
 * buddy view. `pending` marks a model absent from a confirmed reading: it
 * stays usable until a later reading confirms the absence. The console only
 * renders this state; the board owns every judgment about it.
 */
export type CatalogStatus = "available" | "pending" | "unavailable";

export type Buddy = {
  billing?: BillingFact;
  quotaExhausted?: boolean;
  quotaRetry?: QuotaRetryWindow;
  buddyId: string;
  label: string;
  harness: string;
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
  /**
   * Model-identity (harness/provider/model) catalog state, shared by every
   * effort of the model service; absent on boards and fixtures from before ADR-027.
   */
  catalogStatus?: CatalogStatus;
  /** First recorded absence of a pending model, UTC ISO; absent otherwise. */
  pendingSince?: string | null;
};
/** A user's preference for one buddy; an absent row means no preference. */
export type PreferenceMode = "prefer" | "pin" | "exclude";
export type Preference = { buddyId: string; mode: PreferenceMode; reason: string };
/** The model service's human note. */
export type ModelServiceNote = ModelService & {
  text: string;
  updatedAt: string | null;
};
export type Decision = {
  decisionId: string;
  runId?: string | null;
  requestId?: string;
  status: string;
  task: string;
  buddyId: string | null;
  reason: string;
  evidence?: { kind: "annotation" | "preference" | "file"; ref: string }[] | null;
  createdAt: string;
  error?: string | null;
  updatedAt?: string;
  routingMode?: RoutingMode | null;
};
export type Delegation = {
  kind: "goal" | "helper" | "decision" | "execution";
  sourceHostId: string | null;
  currentHostId: string | null;
  parentRunId: string | null;
  rootRunId: string | null;
  project: { id: string; path: string | null; label: string };
  buddy: { harness: string; provider: string; model: string; effort: string } | null;
};
export type TaskPage = { runs: Task[]; total: number; nextCursor: string | null };
export type TaskQuery = {
  limit?: number; before?: string; rootsOnly?: boolean;
  query?: string; projectId?: string; hostId?: string;
  filter?: "all" | "active" | "host" | "review";
};
export type QuotaFailure = { code: "quota-exceeded" | "rate-limited"; nativeCode?: string; source?: string };

export type Task = {
  quotaFailure?: QuotaFailure | null;
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
  /** First durable cancellation event for this run, when recorded. */
  cancellation?: { actor: string | null; reason: string | null } | null;
  /** Selected attempt receipt; its `result.terminationReason` is the durable cause. */
  selectedAttempt?: AttemptReceipt | null;
  /** Native usage of the selected execution; null or absent means unknown. */
  tokenUsage?: TokenUsage | null;
  workflow?: {
    state: string;
    awaitingHost: boolean;
    hostId: string;
    ownerGeneration: number;
    revision: number;
    requestSummary?: string;
    /** Host-authored explicit delegation title; null or absent means fallback. */
    title?: string | null;
    /**
     * Read-only newest concluded own turn outcome summary (ADR-012 X title
     * fallback); null or absent means no usable result yet, never a guess.
     * Never a title under the 0.16 presentation contract.
     */
    resultSummary?: string | null;
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
  /** Native usage of exactly this execution; null or absent means unknown. */
  tokenUsage?: TokenUsage | null;
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
/**
 * Identity of one model service: the exact harness, provider and model tuple.
 * Effort variants share one model service and never enter the key.
 */
export type ModelService = { harness: string; provider: string; model: string };
/** The user-owned per-model-service concurrent-task limit (integer 1–32, default 2). */
export type ModelServiceConcurrencySetting = ModelService & { limit: number };
/**
 * One snapshot/page observation entry per represented model service. `active`
 * counts unresolved attempts and is observation only; a draft never carries or
 * publishes it.
 */
export type ModelServiceConcurrencyEntry = ModelServiceConcurrencySetting & { active: number };
export type RoutingMode = "fast" | "review";
export type RoutingBudget = "brief" | "standard" | "deep";
export type RoutingBudgetLimits = { preset: RoutingBudget; timeoutSeconds: number; toolCalls: number; bytesRead: number };
/**
 * The ordered Router list settings (ADR-021 §2). `routerBuddyIds` is the
 * user's order; the head is the preferred first choice and the tail follows it.
 * The retired single/dual slot fields (`routerBuddyId`, `fastRouterBuddyId`,
 * `reviewRouterBuddyId`, `decisionBuddyId`) are never valid keys again.
 */
export type Configuration = {
  routerBuddyIds: string[];
  routerRetryIntervalSeconds: number;
  defaultRoutingMode: RoutingMode;
  routingBudget: RoutingBudget;
  routingBudgetLimits?: RoutingBudgetLimits;
};
/** The complete published four-part identity of one listed buddy. */
export type RouterHealthIdentity = { harness: string; provider: string; model: string; effort: string };
/**
 * One listed buddy's health facts (ADR-021 §20): resolution eligibility with its
 * machine reason code, skip window and retry facts, the all-time and windowed
 * outcome counts of its own immutable timeline, and its last recorded error.
 * Unknown-nature entries count into `windowEntries` but never as an answer or a
 * failure; input-changed and cancelled records are not no-answers at all.
 */
export type RouterHealthEntry = {
  buddyId: string;
  index: number;
  identity: RouterHealthIdentity | null;
  eligible: boolean;
  code?: string | null;
  inSkipWindow?: boolean;
  skipUntil?: string | null;
  retryAt?: string | null;
  retryInProgress?: boolean;
  answeredCount?: number;
  noAnswerCount?: number;
  lastAnsweredAt?: string | null;
  lastNoAnswerAt?: string | null;
  consecutiveNoAnswers?: number;
  windowSize?: number;
  windowEntries?: number;
  windowAnsweredCount?: number;
  windowFailureCount?: number;
  windowBudgetExhaustedCount?: number;
  windowBoundsRejectedCount?: number;
  windowAttemptCount?: number;
  lastError?: { code: string | null; at: string; phase?: string | null } | null;
};
export type RoutingHealth = {
  available?: boolean;
  reasonCode?: string | null;
  /** The buddy currently holding the Router role, or null while none can serve. */
  currentRouterBuddyId?: string | null;
  /** Per-buddy facts in the user's list order; absent data is unknown, not success. */
  routers?: RouterHealthEntry[];
  /** Terminal selection records whose attribution or nature cannot be proven. */
  unattributedCount?: number;
  unattributed?: { decisionId: string; at: string; outcome: string; code: string | null }[];
  windowSize: number; sampleCount: number; failureCount: number; consecutiveFailures: number;
  abstentionCount: number; cancelledCount: number;
  budgetExhaustedCount?: number; boundsRejectedCount?: number; inputChangedCount?: number;
  /** Window entries that reached a model attempt; a request stays one entry. */
  attemptCount?: number;
  lastSuccessAt: string | null; lastSuccessDecisionId: string | null;
  recentFailures: { decisionId: string; runId: string | null; at: string; code: string }[];
};
/**
 * Identity of the authenticated HTTP snapshot's browser session. The live
 * service issues exactly `{id, canWrite: true, reason: null}` for a valid
 * login; a missing, malformed or non-writable descriptor never grants write
 * access, and an expired login is reported by a 401 refusal instead. The
 * descriptor never carries a credential; the session CSRF token stays separate.
 */
export type ConsoleSession = {
  id: string;
  canWrite: boolean;
  reason: null;
};
export type ConsoleAccess = {
  requireLogin: boolean;
  revision: number;
  sessions: { id: string; lastSeen: number; current: boolean }[];
};
export type BackupInventory = {
  count: number;
  paths: string[];
  entries: { path: string; reason: string }[];
};
export type BackupPreflight = {
  policy: string;
  ok: boolean;
  needsAttention: boolean;
  copied: BackupInventory;
  skipped: BackupInventory;
  rejected: BackupInventory;
};
export type Snapshot = {
  harnesses?: HarnessHealth[];
  csrfToken: string;
  /** Last recorded event in the coherent board read. */
  eventCursor: number;
  /** Authenticated browser session; missing or malformed fails closed. */
  consoleSession: ConsoleSession;
  consoleAccess?: ConsoleAccess;
  configuration: Configuration | null;
  configurationError?: { code: string; message: string } | null;
  buddies: Buddy[];
  /** Preferences for individual buddies. */
  preferences: Preference[];
  /** Model service notes. */
  modelServiceNotes: ModelServiceNote[];
  /**
   * Every unavailable buddy in the table, including one the snapshot
   * still lists (the retained decision selector). The console subtracts the
   * unavailable rows it has already loaded and pages the rest through
   * `buddy_list`; they are never silently dropped from the count.
   */
  unavailableBuddyCount?: number;
  decisions: Decision[];
  /** Read-only bounded selection health; absent data is unknown, not success. */
  routingHealth?: RoutingHealth;
  /**
   * One concurrent-task entry per model service represented in the response,
   * including defaults for model services without an explicit override. `active` is
   * read-only occupancy; publishing it is refused.
   */
  modelServiceConcurrency: ModelServiceConcurrencyEntry[];
  /**
   * The board-wide count of delegation roots awaiting their Host, computed by
   * the service over every run. It is never inferred from a page of recent
   * rows; the full execution records stay on the paginated `tasks` read.
   */
  tasks: { pendingCount: number };
  capabilities: Record<string, boolean>;
};
/** Local draft of the fields a human may publish through `user_policy_publish`. */
export type Draft = Pick<
  Snapshot,
  "buddies" | "preferences" | "modelServiceNotes" | "configuration"
> & {
  /**
   * User limit settings keyed by the exact model service tuple. Occupancy (`active`)
   * is never carried here, so a draft cannot publish it.
   */
  modelServiceConcurrency: ModelServiceConcurrencySetting[];
};
/** Full console view: recorded facts with the human draft's user fields. */
export type ConsoleView = Omit<Snapshot, "modelServiceConcurrency"> & {
  modelServiceConcurrency: ModelServiceConcurrencySetting[];
};
/** One page of retained buddy identities, including unavailable history. */
export type BuddyPage = {
  buddies: Buddy[];
  preferences: Preference[];
  modelServiceNotes?: ModelServiceNote[];
  /** Concurrent-task entries for the model services this page represents. */
  modelServiceConcurrency: ModelServiceConcurrencyEntry[];
  nextCursor: string | null;
};

/** Facts from the serving process and its recorded installation, read on demand. */
export type VersionFacts = {
  softwareVersion: string | null;
  contractVersion: string | null;
  schemaVersion: number | null;
  sourceCommit: string | null;
  installedAt: string | null;
};
export type RuntimeVersionInfo = {
  running: VersionFacts & { mode: "runtime" | "source" };
  installed: VersionFacts | null;
};
