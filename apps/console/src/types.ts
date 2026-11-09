import { z } from "zod";
import { isReason } from "./reason";
import type { Reason } from "./reason";
import { isModelServiceConcurrencyEntry } from "./console-data";

export const safeCount = z.int().nonnegative();
// Number.isInteger intentionally accepts integers beyond Number.MAX_SAFE_INTEGER.
export const integer = z.number().refine(Number.isInteger);
export const nonblankString = z.string().refine(value => value.trim().length > 0);
export const dateString = z.string().refine(value => Number.isFinite(Date.parse(value)));
const reason = z.custom<Reason>(isReason);

export const ACTOR_ROLES = ["host", "lead", "console", "service", "workflow", "bridge"] as const;
export type ActorRole = (typeof ACTOR_ROLES)[number];
export type Actor = { role: "host"; id?: string } | { role: Exclude<ActorRole, "host">; id: string };

/** The five recorded states of one harness in the console snapshot. */
const harnessStatus = z.enum(["unknown", "ready", "missing", "login-required", "unhealthy"]);
export type HarnessStatus = z.infer<typeof harnessStatus>;
const candidateFields = z.looseObject({
  path: z.string().nullish(), source: z.string().nullish(), status: z.string().nullish(), reason: reason.nullish(),
});
// The previous read boundary also accepted arrays as candidate objects. Keep
// that acceptance without converting the returned array or its contents.
const harnessCandidate = z.union([candidateFields,
  z.array(z.unknown()).transform(value => value as unknown as z.infer<typeof candidateFields>),
]);
export type HarnessCandidate = z.infer<typeof harnessCandidate>;

export const permissionLevelsSchema = z.strictObject({
  none: z.enum(["enforced", "convention"]).optional(), web: z.enum(["enforced", "convention"]).optional(), read: z.enum(["enforced", "convention"]).optional(),
  write: z.enum(["enforced", "convention"]).optional(), full: z.enum(["enforced", "convention"]).optional(),
}).refine(value => !Object.values(value).some(enforcement => enforcement === undefined));
export type PermissionLevels = z.infer<typeof permissionLevelsSchema>;

/** One harness's core facts; providers and permissions are parsed independently. */
export const harnessHealthSchema = z.looseObject({
  harness: nonblankString, status: harnessStatus, revision: integer.nonnegative(), manualPath: z.string().nullish(),
  command: z.array(z.string()).nullish(), executable: z.string().nullish(), version: z.string().nullish(),
  permissionsVerifiedVersion: z.string().nullable(),
  source: z.string().nullish(), candidates: z.array(harnessCandidate).nullish(), reason: reason.nullish(),
  checkedAt: z.string().nullish(), expiresAt: z.string().nullish(),
  providers: z.custom<ProviderCooling[]>().optional(), permissions: z.custom<PermissionLevels>().optional(),
  billingByProvider: z.custom<Record<string, BillingFact>>().optional(),
  account: z.custom<import("./harness-account").HarnessAccount>().optional(),
});
export type HarnessHealth = z.infer<typeof harnessHealthSchema> & { manualPath: string | null };

type BillingFact = {
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
  /** Whether `inputTokens` already holds the cached tokens; the board writes the first form. */
  inputBasis?: "includes-cached" | "excludes-cached";
  completeness?: "complete" | "partial" | "unknown";
  coverage?: "native-root-session" | "native-root-thread" | "native-attempt";
};

const recordedTokenUsageSchema = z.object({
  inputTokens: safeCount.nullable(), cachedInputTokens: safeCount.nullable(), outputTokens: safeCount.nullable(), source: nonblankString, scope: z.literal("attempt"),
  inputBasis: z.enum(["includes-cached", "excludes-cached"]).optional(), completeness: z.enum(["complete", "partial", "unknown"]).optional(),
  coverage: z.enum(["native-root-session", "native-root-thread", "native-attempt"]).optional(),
}).refine(usage => usage.inputBasis === "excludes-cached" || usage.inputTokens === null || usage.cachedInputTokens === null || usage.cachedInputTokens <= usage.inputTokens);

/** A provider's retained cooling record; only a future deadline is active. */
export const providerCoolingSchema = z.looseObject({
  provider: nonblankString, coolingUntil: dateString.nullable(), consecutiveRejections: integer.positive(),
});
export type ProviderCooling = z.infer<typeof providerCoolingSchema>;

/**
 * The board's model-level catalog state (ADR-027 §2/§6), published on every
 * buddy view. `pending` marks a model absent from a confirmed reading: it
 * stays usable until a later reading confirms the absence. The console only
 * renders this state; the board owns every judgment about it.
 */
type CatalogStatus = "available" | "pending" | "unavailable";

export type Buddy = {
  billing?: BillingFact;
  providerCooling?: ProviderCooling | null;
  buddyId: string;
  label: string;
  harness: string;
  provider: string;
  model: string;
  effort: string;
  /** This effort's 0-based position in the model's current native catalog; absent from that catalog is null. */
  effortRank: number | null;
  available: boolean;
  enabled: boolean;
  capabilities: string[];
  contextWindow: number | null;
  description: string;
  source: string;
  unavailableReason?: Reason | null;
  unavailableContent?: string | null;
  /**
   * Model-identity (harness/provider/model) catalog state, shared by every
   * effort of the model service.
   */
  catalogStatus?: CatalogStatus;
  /** First recorded absence of a pending model, UTC ISO; absent otherwise. */
  pendingSince?: string | null;
};
/** A user's preference for one buddy; an absent row means no preference. */
const preferenceMode = z.enum(["prefer", "pin", "exclude"]);
export type PreferenceMode = z.infer<typeof preferenceMode>;
export const preferencesSchema = z.array(z.strictObject({ buddyId: z.string(), mode: preferenceMode, note: z.string().nullable() }));
export type Preference = z.infer<typeof preferencesSchema>[number];
export type ModelCorrespondence = ModelService & {
  correspondenceId: string;
  modelId: string;
  createdAt: string;
  source: "same-name" | "user" | "profile";
  evidenceUrl: string | null;
  evidenceDate: string | null;
  canUndo: boolean;
};
export type ModelCorrespondenceChange = ModelService & (
  | { action: "map"; modelId: string }
  | { action: "undo"; correspondenceId: string }
  | { action: "generation"; displayName: string }
);
export type ModelProfileClaim = {
  polarity: "strength" | "weakness";
  claim: string;
  sourceUrl: string;
  sourceDate: string;
  sourceKind: "vendor" | "third-party";
};
export type ModelProfile = {
  positioning: string;
  builtAt: string;
  profileRunId: string;
  claims: ModelProfileClaim[];
};
export type ProfileToolSummary = {
  toolName: string | null;
  calls: number;
  failed: number;
  unknown: number;
};
export type ProfileRun = {
  profileRunId: string;
  taskId: string;
  buddyId: string;
  buddy: { harness: string; provider: string; model: string; effort: string };
  state: "running" | "completed" | "failed";
  reason: Reason | null;
  startedAt: string;
  finishedAt: string | null;
  correctionRounds: number;
  toolSummary: ProfileToolSummary[] | null;
  usage: { attempts: { attemptId: string; elapsedMs: number | null; toolCalls: number | null; tokenUsage: TokenUsage | null }[] } | null;
};
export type Model = {
  modelId: string;
  displayName: string;
  names: ModelService[];
  profile: ModelProfile | null;
  latestProfileRun: ProfileRun | null;
};
export type ProfileStartResult =
  | { status: "unavailable"; reason: Reason }
  | { status: "running"; profileRunId: string; runId: string; buddyId: string;
      buddy?: { harness: string; provider: string; model: string; effort: string }; usesQuota?: boolean };

const modelServiceSchema = z.looseObject({ harness: nonblankString, provider: nonblankString, model: nonblankString });
export const modelCorrespondenceSchema = modelServiceSchema.extend({
  correspondenceId: nonblankString, modelId: nonblankString, createdAt: dateString,
  source: z.enum(["same-name", "user", "profile"]), evidenceUrl: z.string().nullable(),
  evidenceDate: z.string().nullable(), canUndo: z.boolean(),
});
export const modelProfileClaimSchema = z.looseObject({
  polarity: z.enum(["strength", "weakness"]), claim: nonblankString,
  sourceUrl: z.url(), sourceDate: z.iso.date(), sourceKind: z.enum(["vendor", "third-party"]),
});
export const modelProfileSchema = z.looseObject({
  positioning: nonblankString, builtAt: dateString, profileRunId: nonblankString,
  claims: z.array(modelProfileClaimSchema),
});
export const profileRunSchema = z.looseObject({
  profileRunId: nonblankString, taskId: nonblankString, buddyId: nonblankString,
  buddy: z.looseObject({ harness: nonblankString, provider: nonblankString, model: nonblankString, effort: nonblankString }),
  state: z.enum(["running", "completed", "failed"]), reason: reason.nullable(),
  startedAt: dateString, finishedAt: dateString.nullable(), correctionRounds: safeCount,
  toolSummary: z.array(z.looseObject({
    toolName: nonblankString.nullable(), calls: safeCount, failed: safeCount, unknown: safeCount,
  })).nullable(),
  usage: z.looseObject({ attempts: z.array(z.looseObject({
    attemptId: nonblankString, elapsedMs: safeCount.nullable(), toolCalls: safeCount.nullable(),
    tokenUsage: recordedTokenUsageSchema.nullable(),
  })) }).nullable(),
});
export const modelSchema = z.looseObject({
  modelId: nonblankString, displayName: nonblankString, names: z.array(modelServiceSchema),
  profile: modelProfileSchema.nullable(), latestProfileRun: profileRunSchema.nullable(),
});
type Decision = {
  decisionId: string;
  runId?: string | null;
  requestId?: string;
  status: string;
  task: string;
  buddyId: string | null;
  reason: Reason | null;
  note: string | null;
  evidence?: { kind: "annotation" | "preference" | "file"; ref: string }[] | null;
  createdAt: string;
  updatedAt?: string;
  routingMode?: RoutingMode | null;
};
export type Lineage = {
  kind: "micro-task" | "helper" | "decision" | "execution";
  parentRunId: string | null;
  rootRunId: string | null;
  project: { id: string; kind: "git" | "folder"; path: string | null; label: string } | null;
  routingTask?: { runId: string; title: string | null; titleSource: "title" | "first-line" | null; macroTaskId: string | null; macroTaskTitle: string | null } | null;
  buddy: { harness: string; provider: string; model: string; effort: string } | null;
  delegationAuthority?: boolean;
  /** Lead owning this micro task's root tree, including helper descendants. */
  rootLeadRunId: string | null;
  leadOwnership?: { leadRunId: string; leadTurnId: string; createdAt: string } | null;
};
export type ProviderRejection = { code: "quota-exceeded" | "rate-limited" | "overloaded"; nativeCode?: string; source?: string; observedAt?: string; resetsAt?: string | null; rejectionPhase?: "before-model" | "during-model" | "unknown" };

export type Task = {
  providerCooling?: ProviderCooling | null;
  providerRejection?: ProviderRejection | null;
  runId: string;
  /** Whether the assigned buddy may delegate work from this micro task. */
  delegationAuthority?: boolean;
  task: string;
  state: string;
  /** The execution the task view's per-attempt facts are about: the one running, and the last one chosen. */
  activeAttemptId?: string | null;
  selectedAttemptId?: string | null;
  cwd: string;
  revision: number;
  createdAt: string;
  acceptedAt: string | null;
  reviewRejectedAt: string | null;
  reviewRejectionNote: string | null;
  queueReason?: string | null;
  lineage?: Lineage;
  spec?: Record<string, unknown>;
  /** Bounded per-attempt activity projection; null or absent means unknown. */
  activity?: TaskActivity | null;
  /** Recorded real termination cause when the task view carries it directly. */
  terminationReason?: string | null;
  stopInitiator?: "host" | "lead" | "console" | "service" | "unknown" | null;
  graceDeadline?: string | null;
  /** First durable cancellation event for this run, when recorded. */
  cancellation?: { actor: Actor | null; note: string | null } | null;
  /** Selected attempt receipt; its `result.terminationReason` is the durable cause. */
  selectedAttempt?: AttemptReceipt | null;
  /** Native usage of the selected execution; null or absent means unknown. */
  tokenUsage?: TokenUsage | null;
  workflow?: {
    state: string;
    awaitingHost: boolean;
    checkout?: "none" | "worktree" | "existing";
    projectKind?: "git" | "folder";
    revision: number;
    requestSummary?: string;
    /** Host-authored explicit micro task title; null or absent means fallback. */
    title?: string | null;
    /** The macro task the micro task belongs to; absent for a standalone one, which is its own group. */
    macroTaskId?: string | null;
  };
  workflowShutdown?: Shutdown;
  [key: string]: unknown;
};
/** Public attempt receipt. The claim capability and nonce verifier never appear. */
type AttemptReceipt = {
  attemptId?: string;
  generation?: number;
  executionState?: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  resultAvailable?: boolean;
  shutdownConfirmed?: boolean;
  result?: Record<string, unknown> | null;
  error?: string | null;
  releaseReason?: Reason | null;
  terminationReason?: string | null;
  stopInitiator?: "host" | "lead" | "console" | "service" | "unknown" | null;
  graceDeadline?: string | null;
  /** Native usage of exactly this execution; null or absent means unknown. */
  tokenUsage?: TokenUsage | null;
  reminders?: { overdue: boolean; possiblyStuck: boolean; lastNativeEventAt: string | null; toolInProgress: boolean };
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
type ActivityCounts = {
  modelTurns?: number | null;
  toolCalls?: number | null;
};
/**
 * Bounded `execution_progress` activity. Unknown fields stay absent instead of being
 * invented; there is deliberately no percentage or completion estimate here.
 */
type TaskActivity = {
  phase: ActivityPhase;
  observedAt?: string | null;
  eventSeq?: number | null;
  nativeSessionId?: string | null;
  lastNativeActivityAt?: string | null;
  lastToolActivityAt?: string | null;
  toolName?: string | null;
  waitingReason?: Reason | null;
  counts?: ActivityCounts | null;
};
type Shutdown = {
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
/** The user-owned per-model-service concurrent-task limit (positive safe integer, default 2). */
export type ModelServiceConcurrencySetting = ModelService & { limit: number };
/**
 * One snapshot/page observation entry per represented model service. `active`
 * counts unresolved attempts and is observation only; a draft never carries or
 * publishes it.
 */
export type ModelServiceConcurrencyEntry = ModelServiceConcurrencySetting & { active: number };
const routingMode = z.enum(["fast", "review"]);
export type RoutingMode = z.infer<typeof routingMode>;
/** The current Router settings and service observation fields. */
export const configurationSchema = z.strictObject({
  routerBuddyIds: z.array(nonblankString).refine(ids => new Set(ids).size === ids.length),
  routerRetryIntervalSeconds: integer.min(1).max(2147483647), defaultRoutingMode: routingMode,
  modelResponseWaitSeconds: integer.min(1).max(86400),
  explorationEvery: integer.min(2).nullable(),
  maintenanceBuddyId: nonblankString.nullable().optional(), maintenanceIntervalSeconds: integer.positive().optional(),
  profileBuddyId: nonblankString.nullable(),
  profileUnavailableReason: reason.nullish().optional(),
});
export type Configuration = z.infer<typeof configurationSchema>;

/** The complete published four-part identity of one listed buddy. */
export const routerHealthIdentitySchema = z.looseObject({ harness: z.string(), provider: z.string(), model: z.string(), effort: z.string() });
export type RouterHealthIdentity = z.infer<typeof routerHealthIdentitySchema>;
/** One listed buddy's optional observations; a malformed field refuses all health. */
const routerHealthEntry = z.looseObject({
  buddyId: nonblankString, index: safeCount, identity: routerHealthIdentitySchema.nullish(), eligible: z.boolean(),
  code: z.string().nullish(), inSkipWindow: z.boolean().optional(), skipUntil: z.string().nullish(),
  retryAt: z.string().nullish(), retryInProgress: z.boolean().optional(), answeredCount: safeCount.optional(),
  noAnswerCount: safeCount.optional(), lastAnsweredAt: z.string().nullish(), lastNoAnswerAt: z.string().nullish(),
  consecutiveNoAnswers: safeCount.optional(), windowSize: safeCount.optional(), windowEntries: safeCount.optional(),
  windowAnsweredCount: safeCount.optional(), windowFailureCount: safeCount.optional(), windowNotRespondingCount: safeCount.optional(),
  windowBoundsRejectedCount: safeCount.optional(), windowAttemptCount: safeCount.optional(),
  lastError: z.looseObject({ code: z.string().nullable(), at: z.string(), phase: z.string().nullish() }).nullish(),
});
export type RouterHealthEntry = z.infer<typeof routerHealthEntry>;
export const routingHealthSchema = z.looseObject({
  available: z.boolean().optional(), reason: reason.nullish(), currentRouterBuddyId: z.string().nullish(),
  routers: z.array(routerHealthEntry).optional(), unattributedCount: safeCount.optional(),
  unattributed: z.array(z.looseObject({ decisionId: z.string(), at: z.string(), outcome: z.string(), code: z.string().nullable() })).optional(),
  windowSize: safeCount, sampleCount: safeCount, failureCount: safeCount, consecutiveFailures: safeCount,
  abstentionCount: safeCount, cancelledCount: safeCount, notRespondingCount: safeCount.optional(),
  boundsRejectedCount: safeCount.optional(), inputChangedCount: safeCount.optional(), attemptCount: safeCount.optional(),
  lastSuccessAt: z.string().nullable(), lastSuccessDecisionId: z.string().nullable(),
  recentFailures: z.array(z.looseObject({ decisionId: z.string(), runId: z.string().nullable(), title: z.string().nullable(), at: z.string(), code: z.string() })),
});
export type RoutingHealth = z.infer<typeof routingHealthSchema>;

export const TASK_TYPES = ["create", "change", "debug", "verify", "review", "research", "write"] as const;
export const domainNodeSchema = z.object({ nodeId: nonblankString, name: nonblankString, definition: z.string(), parentId: z.string().nullable(), mergedIntoId: z.string().nullable() });
export type DomainNode = z.infer<typeof domainNodeSchema>;
export const evaluationStatisticsSchema = z.object({
  samples: safeCount, successes: safeCount, failures: safeCount, firstPasses: safeCount, reworkRounds: safeCount,
  firstPassRate: z.number().min(0).max(1).nullable(), failureRate: z.number().min(0).max(1).nullable(),
  medianDeliverySeconds: z.number().nonnegative().nullable(),
  usage: z.object({ samples: safeCount, medianInputTokens: z.number().nonnegative().nullable(), medianCachedInputTokens: z.number().nonnegative().nullable(), medianOutputTokens: z.number().nonnegative().nullable() }),
});
export type EvaluationStatistics = z.infer<typeof evaluationStatisticsSchema>;
export const kindEvaluationSchema = z.object({ harness: nonblankString, modelId: nonblankString,
  modelDisplayName: nonblankString, modelNames: z.array(modelServiceSchema), effort: nonblankString,
  previousGroups: z.array(modelServiceSchema.extend({ modelId: nonblankString, modelDisplayName: nonblankString, since: dateString })),
  summary: z.object({ text: z.string(), citations: z.array(z.object({ runId: nonblankString, title: z.string().nullable(), macroTaskId: z.string().nullable() })) }).nullable(),
  statistics: z.object({ overall: evaluationStatisticsSchema, byDomain: z.record(z.string(), evaluationStatisticsSchema),
    byDomainTaskType: z.record(z.string(), z.partialRecord(z.enum(TASK_TYPES), evaluationStatisticsSchema)),
    infrastructureFailures: z.array(z.object({ provider: z.string(), count: safeCount, lastAt: z.string().nullable() })) }),
});
export type KindEvaluation = z.infer<typeof kindEvaluationSchema>;

export const maintenanceSchema = z.object({ pendingCount: safeCount, runs: z.array(z.object({
  maintenanceRunId: nonblankString, triggerType: z.enum(["manual", "interval"]), buddyId: nonblankString, buddy: routerHealthIdentitySchema, taskId: nonblankString,
  state: z.enum(["running", "completed", "failed"]), reason: reason.nullable(), startedAt: dateString, finishedAt: dateString.nullable(),
  usage: z.object({ attempts: z.array(z.object({ attemptId: nonblankString, elapsedMs: z.number().nullable(), toolCalls: safeCount.nullable(), tokenUsage: recordedTokenUsageSchema.nullable() })) }).nullable(),
})) });
export type Maintenance = z.infer<typeof maintenanceSchema>;

/** Authenticated browser session; the CSRF token stays separate. */
export const consoleSessionSchema = z.looseObject({ id: nonblankString });
export type ConsoleSession = z.infer<typeof consoleSessionSchema>;
export const consoleAccessSchema = z.looseObject({
  requireLogin: z.boolean(), revision: safeCount,
  sessions: z.array(z.looseObject({ id: z.string().regex(/^[a-f0-9]{24}$/), lastSeen: z.number(), current: z.boolean() })).max(64),
});
export type ConsoleAccess = z.infer<typeof consoleAccessSchema>;
export const reminderSettingsSchema = z.looseObject({ silenceSeconds: z.int().min(1).max(86400), revision: z.int().min(1), updatedAt: dateString });
export type ReminderSettings = z.infer<typeof reminderSettingsSchema>;

// Only these snapshot fields were checked at the read boundary. The schema
// validates; the API returns the original representation, including unknowns.
export const snapshotReadSchema = z.object({
  csrfToken: z.string(), eventCursor: safeCount, configuration: configurationSchema.nullable(),
  buddies: z.array(z.custom<Buddy>()), preferences: preferencesSchema,
  models: z.array(modelSchema),
  modelCorrespondences: z.array(modelCorrespondenceSchema),
  modelServiceConcurrency: z.array(z.custom<ModelServiceConcurrencyEntry>(isModelServiceConcurrencyEntry)),
  tasks: z.object({ pendingCount: safeCount }),
  maintenance: maintenanceSchema.optional(), domainNodes: z.array(domainNodeSchema).optional(), kindEvaluations: z.array(kindEvaluationSchema).optional(),
  // Drafts share this field's nullable type; a server read still requires settings.
}).refine(snapshot => snapshot.configuration !== null);
export type Snapshot = z.infer<typeof snapshotReadSchema> & {
  harnesses?: HarnessHealth[];
  /** Authenticated browser session; missing or malformed fails closed. */
  consoleSession: ConsoleSession;
  consoleAccess?: ConsoleAccess;
  reminderSettings?: ReminderSettings;
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
  capabilities: Record<string, boolean>;
};
/** Local draft of the fields a human may publish through `user_policy_publish`. */
export type Draft = Pick<
  Snapshot,
  "buddies" | "preferences" | "modelCorrespondences" | "configuration"
> & {
  modelCorrespondenceChanges: ModelCorrespondenceChange[];
  /**
   * User limit settings keyed by the exact model service tuple. Occupancy (`active`)
   * is never carried here, so a draft cannot publish it.
   */
  modelServiceConcurrency: ModelServiceConcurrencySetting[];
};
/** Full console view: recorded facts with the human draft's user fields. */
export type ConsoleView = Omit<Snapshot, "modelServiceConcurrency"> & {
  modelServiceConcurrency: ModelServiceConcurrencySetting[];
  modelCorrespondenceChanges?: ModelCorrespondenceChange[];
};
/** One page of retained buddy identities, including unavailable history. */
export type BuddyPage = {
  buddies: Buddy[];
  preferences: Preference[];
  models: Model[];
  modelCorrespondences: ModelCorrespondence[];
  /** Concurrent-task entries for the model services this page represents. */
  modelServiceConcurrency: ModelServiceConcurrencyEntry[];
  nextCursor: string | null;
};

/** Facts from the serving process and its recorded installation, read on demand. */
const versionFactsSchema = z.looseObject({
  softwareVersion: nonblankString.nullable(), contractVersion: nonblankString.nullable(), schemaVersion: safeCount.nullable(),
  sourceCommit: nonblankString.nullable(), installedAt: dateString.nullable(),
});
export type VersionFacts = z.infer<typeof versionFactsSchema>;
export const runtimeVersionSchema = z.looseObject({
  running: versionFactsSchema.extend({ mode: z.enum(["runtime", "source"]) }), installed: versionFactsSchema.nullable(),
});
export type RuntimeVersionInfo = z.infer<typeof runtimeVersionSchema>;
