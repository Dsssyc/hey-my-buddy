import type { Shutdown, Task, TokenUsage } from "./types";
import type { RoutingMode, RoutingFallback } from "./types";

export type ExecutionConfiguration = {
  adapter: string;
  provider: string;
  model: string;
  effort: string;
};

/** A user-excluded configuration frozen with one routing request. */
export type ExcludedProfile = ExecutionConfiguration & {
  profileId: string;
  reason?: string | null;
  source?: string | null;
};

/**
 * Submission-time routing facts frozen with the decision record: the candidate
 * count after hard filtering and the user exclusions that narrowed it. They are
 * never recomputed from current preferences, so a historical route keeps the
 * basis it actually had.
 */
export type RoutingBasis = {
  candidateCount?: number;
  excludedCount?: number;
  excludedProfiles?: ExcludedProfile[];
};

export type RoutingRecord = {
  status: string;
  routingMode?: RoutingMode;
  requestedRoutingMode?: RoutingMode;
  fallback?: RoutingFallback | null;
  decisionId?: string | null;
  taskId?: string | null;
  tableRevision?: number | null;
  configurationRevision?: number | null;
  selectedProfile?: ExecutionConfiguration | null;
  reason?: string | null;
  constraints?: Partial<ExecutionConfiguration>;
  requiredCapabilities?: string[];
  /** `model-selection` for a Router choice, `single-candidate` for the program's direct selection. */
  source?: string | null;
  routingBasis?: RoutingBasis | null;
};
export type RoutingHistory = {
  entries: (RoutingRecord & { decisionId: string; createdAt: string; ownerGeneration: number; current: boolean })[];
  nextCursor: number | null;
  total: number;
};
export type TurnRouting = {
  turnId: string; turnIndex: number; attemptId: string;
  executionConfiguration?: ExecutionConfiguration | null;
  routing?: { decisionId: string | null; executionConfigurationRevision: number } | null;
  /** Native usage of this one execution; null or absent means unknown. */
  tokenUsage?: TokenUsage | null;
};

/**
 * The independent Host conclusion of a failed or cancelled goal (ADR-018 §16).
 * It records what the Host did with the remains; it never changes the goal's
 * execution outcome into a success or an acceptance.
 */
export type HostConclusion = {
  conclusionId: string;
  /** The reviewed execution generation; null when the goal never started. */
  attemptId: string | null;
  /** The recorded target outcome this conclusion explains: failed or cancelled. */
  executionStatus: string;
  note: string;
  evidence: string[];
  artifactId: string | null;
  integrationId: string | null;
  actor: string;
  createdAt: string;
  ownerGeneration: number;
  runRevision: number;
};

/**
 * The patch from the goal's recorded `inputCommit` to one output (ADR-018 §20).
 * It is generated from the goal's own fixed input, so the Host can integrate
 * without stacking every turn's incremental patch.
 */
export type CumulativePatch = {
  baseCommit: string;
  outputCommit: string;
  path: string;
  sha256: string;
  changedPaths: string[];
};

export type WorkflowRequest = {
  requestId: string;
  kind: string;
  routing?: boolean;
  state: string;
  summary: string;
  attempted: string;
  neededWork: string[];
  expectedArtifacts: string[];
  acceptance: string;
  childTaskId?: string | null;
  origin?: { runId: string; requestId: string };
  preparationError?: { code: string; message: string };
};
/**
 * One durable integration record from `workflow_get`. `state` is `verified` for
 * a checked target relationship or `not-required` for an explicit Host
 * decision; only those two satisfy acceptance for the named artifact.
 */
export type IntegrationRecord = {
  integrationId: string;
  runId: string;
  artifactId: string;
  attemptId: string;
  state: string;
  strategy: string;
  target: null | {
    kind: string;
    path: string;
    ref: string;
    repositoryId: string | null;
    checkoutId: string | null;
  };
  sourceCommit: string | null;
  sourceTree: string | null;
  beforeCommit: string | null;
  afterCommit: string | null;
  beforeTree: string | null;
  afterTree: string | null;
  verification: { verified?: boolean; notRequired?: boolean; reason?: string; summary?: string } & Record<string, unknown>;
  notRequired: boolean;
  reason: string | null;
  actor: string;
  createdAt: string;
};
export type Workflow = {
  governed: true;
  runId: string;
  hostId: string;
  ownerGeneration: number;
  revision: number;
  state: string;
  awaitingHost: boolean;
  waitReason: string;
  continuationCount: number;
  /** ADR-018 §18: true only when the user required this configuration as a hard constraint. */
  configurationLocked?: boolean | null;
  /** ADR-018 §16: the independent Host conclusion of a failed or cancelled goal. */
  hostConclusion?: HostConclusion | null;
  executionConfiguration?: ExecutionConfiguration | null;
  executionConfigurationRevision?: number;
  routing?: RoutingRecord | null;
  turns?: TurnRouting[];
  truncated?: { turns?: number };
  shutdown?: Shutdown;
  workspace: null | {
    path: string;
    kind: string;
    access: string;
    inputCommit: string;
    manifestSha256: string;
  };
  currentTurn: null | {
    turnId: string;
    turnIndex: number;
    attemptId: string;
    resumeMode: string;
    sessionId?: string;
    previousSessionId?: string;
    summary?: string;
    summaryTruncated?: boolean;
    remaining?: string[];
    /** Native usage of this execution; null or absent means unknown. */
    tokenUsage?: TokenUsage | null;
  };
  activeRequest: WorkflowRequest | null;
  counts?: { openRequests?: number; turns?: number };
  children: {
    taskId: string;
    state: string;
    role: string;
    requestId: string;
  }[];
  artifacts: {
    artifactId: string;
    attemptId: string;
    sourceTaskId: string;
    kind: string;
    manifestSha256: string;
    snapshotSha256?: string;
    commit?: string;
    outputCommit?: string;
    diffPath?: string;
    /** ADR-018 §17: a sealed but unverified intermediate delivery. */
    partial?: boolean;
    verified?: boolean;
    final?: boolean;
    /** ADR-018 §20: the patch from the goal's own input commit to this output. */
    cumulativePatch?: CumulativePatch | null;
  }[];
  /** Host-recorded integration evidence, newest first. */
  integrations: IntegrationRecord[];
  finalArtifactId: string | null;
  finalAttemptId: string | null;
  task: Partial<Task> & Pick<Task, "runId" | "revision" | "status">;
};

export type HelperDraft = {
  id: string;
  profileId: string;
  task: string;
  cwd: string;
  kind: "worktree" | "existing";
  access: "write" | "read";
  writeScope: string;
  includeUntracked: string;
};
