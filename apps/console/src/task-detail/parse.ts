import i18n from "i18next";
import { ApiError } from "../api";
import { parseActor } from "../actor";
import { isReason } from "../reason";
import type { Reason } from "../reason";
import type { ActivityPhase, Actor, RoutingMode, Task, TokenUsage } from "../types";
import type {
  Activity, Artifact, Attempt, Audit, AuditRequest, Basis, Buddy, CompactRequest, Continuation, CumulativePatch, Ending,
  HostConclusion, Inquiries, Inquiry, InquiryState, Integration, OpenRequest, ResultTurn, Routing, RoutingEntry, Session,
  TaskView, Turn, Workflow,
} from "./types";

/**
 * The one place a reply of the board is checked, against the views the service
 * builds. Each reader refuses a reply whose shape is not the recorded one with
 * the same read failure; code after it trusts what it returns. A field the
 * service may leave null is read as null; a field it always sends is required.
 */
type Rec = Record<string, unknown>;

function refuse(what: string): never {
  throw new ApiError("INVALID_RESPONSE", () => i18n.t("error.incomplete", { ns: "taskDetail", what }));
}
function rec(value: unknown, what: string): Rec {
  if (!value || typeof value !== "object" || Array.isArray(value)) refuse(what);
  return value as Rec;
}
function recOrNull(value: unknown, what: string): Rec | null {
  return value === null || value === undefined ? null : rec(value, what);
}
function str(value: unknown, what: string): string {
  if (typeof value !== "string") refuse(what);
  return value as string;
}
function strOrNull(value: unknown, what: string): string | null {
  return value === null || value === undefined ? null : str(value, what);
}
function reasonOrNull(value: unknown, what: string): Reason | null {
  if (value === null || value === undefined) return null;
  return isReason(value) ? value : refuse(what);
}
function int(value: unknown, what: string): number {
  if (!Number.isSafeInteger(value)) refuse(what);
  return value as number;
}
function intOrNull(value: unknown, what: string): number | null {
  return value === null || value === undefined ? null : int(value, what);
}
function bool(value: unknown, what: string): boolean {
  if (typeof value !== "boolean") refuse(what);
  return value as boolean;
}
function boolOrNull(value: unknown, what: string): boolean | null {
  return value === null || value === undefined ? null : bool(value, what);
}
function list(value: unknown, what: string): unknown[] {
  if (!Array.isArray(value)) refuse(what);
  return value as unknown[];
}
function strings(value: unknown, what: string): string[] {
  return value === null || value === undefined ? [] : list(value, what).map(item => str(item, what));
}
function oneOf<T extends string>(value: unknown, allowed: readonly T[], what: string): T {
  if (!allowed.includes(value as T)) refuse(what);
  return value as T;
}
/** An actor as the board writes it; one that is present but not that form is not read. */
function actorOrNull(value: unknown, what: string): Actor | null {
  if (value === null || value === undefined) return null;
  return parseActor(value) ?? refuse(what);
}
function actor(value: unknown, what: string): Actor {
  return actorOrNull(value, what) ?? refuse(what);
}

function parseBuddy(value: unknown, what: string): Buddy | null {
  const buddy = recOrNull(value, what);
  return buddy && {
    harness: str(buddy.harness, what), provider: str(buddy.provider, what),
    model: str(buddy.model, what), effort: str(buddy.effort, what),
  };
}

/** The parts of a buddy a frozen constraint or an exclusion names. */
function partialBuddy(value: unknown, what: string): Partial<Buddy> {
  const buddy = rec(value, what);
  const part = (key: keyof Buddy) => (buddy[key] === undefined || buddy[key] === null ? {} : { [key]: str(buddy[key], what) });
  return { ...part("harness"), ...part("provider"), ...part("model"), ...part("effort") };
}

/**
 * One execution's native usage. A usage that cannot be read as the recorded
 * per-execution shape is unknown, never a number made up from it.
 */
export function parseTokenUsage(value: unknown): TokenUsage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Rec;
  if (row.scope !== "attempt" || typeof row.source !== "string" || !row.source.trim()) return null;
  const counts: Record<"inputTokens" | "cachedInputTokens" | "outputTokens", number | null> = {
    inputTokens: null, cachedInputTokens: null, outputTokens: null,
  };
  for (const key of ["inputTokens", "cachedInputTokens", "outputTokens"] as const) {
    const raw = row[key];
    if (raw === null || raw === undefined) continue;
    if (!Number.isSafeInteger(raw) || (raw as number) < 0) return null;
    counts[key] = raw as number;
  }
  const inputBasis = row.inputBasis === "includes-cached" || row.inputBasis === "excludes-cached" ? row.inputBasis : "includes-cached";
  if (inputBasis === "includes-cached" && counts.cachedInputTokens !== null && counts.inputTokens !== null && counts.cachedInputTokens > counts.inputTokens) return null;
  const completeness = row.completeness === "complete" || row.completeness === "partial" || row.completeness === "unknown"
    ? row.completeness : undefined;
  return { ...counts, source: row.source, scope: "attempt", inputBasis, ...(completeness ? { completeness } : {}) };
}

function parseTurn(value: unknown): Turn {
  const turn = rec(value, "turn");
  const routing = recOrNull(turn.routing, "turn.routing");
  return {
    turnId: str(turn.turnId, "turn.turnId"), turnIndex: int(turn.turnIndex, "turn.turnIndex"),
    attemptId: str(turn.attemptId, "turn.attemptId"), expectedSeconds: intOrNull(turn.expectedSeconds, "turn.expectedSeconds"),
    resumeMode: str(turn.resumeMode, "turn.resumeMode"), sessionId: strOrNull(turn.sessionId, "turn.sessionId"),
    state: str(turn.state, "turn.state"), disposition: strOrNull(turn.disposition, "turn.disposition"),
    createdAt: str(turn.createdAt, "turn.createdAt"), updatedAt: str(turn.updatedAt, "turn.updatedAt"),
    buddy: parseBuddy(turn.buddy, "turn.buddy"),
    routing: routing && { decisionId: strOrNull(routing.decisionId, "turn.routing"), buddyRevision: int(routing.buddyRevision, "turn.routing") },
    tokenUsage: parseTokenUsage(turn.tokenUsage),
  };
}

/** A turn with its result; a turn that has returned nothing has no `summary` yet. */
function parseResultTurn(value: unknown): ResultTurn {
  const turn = rec(value, "turn");
  return { ...parseTurn(value), summary: strOrNull(turn.summary, "turn.summary"), remaining: strings(turn.remaining, "turn.remaining") };
}

function parseCompactRequest(value: unknown): CompactRequest {
  const request = rec(value, "request");
  return {
    requestId: str(request.requestId, "request.requestId"), kind: str(request.kind, "request.kind"),
    state: str(request.state, "request.state"), createdAt: str(request.createdAt, "request.createdAt"),
    decidedAt: strOrNull(request.decidedAt, "request.decidedAt"),
    childTaskId: strOrNull(request.childTaskId, "request.childTaskId"), routing: bool(request.routing, "request.routing"),
  };
}

function parseOpenRequest(value: unknown): OpenRequest {
  const request = rec(value, "request");
  const problem = recOrNull(request.preparationError, "request.preparationError");
  return {
    ...parseCompactRequest(value),
    summary: strOrNull(request.summary, "request.summary"),
    reason: reasonOrNull(request.reason, "request.reason"),
    attempted: strOrNull(request.attempted, "request.attempted"),
    neededWork: strings(request.neededWork, "request.neededWork"),
    expectedArtifacts: strings(request.expectedArtifacts, "request.expectedArtifacts"),
    acceptance: strOrNull(request.acceptance, "request.acceptance"),
    preparationError: problem && { code: str(problem.code, "request.preparationError"), message: str(problem.message, "request.preparationError") },
  };
}

function parsePatch(value: unknown): CumulativePatch | null {
  const patch = recOrNull(value, "cumulativePatch");
  return patch && {
    baseCommit: strOrNull(patch.baseCommit, "cumulativePatch"), outputCommit: strOrNull(patch.outputCommit, "cumulativePatch"),
    path: strOrNull(patch.path, "cumulativePatch"), sha256: strOrNull(patch.sha256, "cumulativePatch"),
    changedPaths: strings(patch.changedPaths, "cumulativePatch"),
  };
}

const OUTPUT_KINDS = ["output", "resolved-output", "partial-output"] as const;

/**
 * The sealed outputs of the micro task. Its other seals (the input it was
 * delegated with, the input and shared refs of each turn) carry no output and
 * are not drawn, so they are not read.
 */
function parseArtifacts(value: unknown): Artifact[] {
  return list(value, "workflow.artifacts").flatMap(item => {
    const artifact = rec(item, "artifact");
    if (!OUTPUT_KINDS.includes(artifact.kind as never)) return [];
    const kind = artifact.kind as Artifact["kind"];
    return [{
      artifactId: str(artifact.artifactId, "artifact.artifactId"), kind,
      attemptId: str(artifact.attemptId, "artifact.attemptId"),
      manifestSha256: str(artifact.manifestSha256, "artifact.manifestSha256"),
      outputCommit: strOrNull(artifact.outputCommit, "artifact.outputCommit"),
      diffPath: strOrNull(artifact.diffPath, "artifact.diffPath"),
      changedPaths: strings(artifact.changedPaths, "artifact.changedPaths"),
      partial: kind === "partial-output",
      verified: boolOrNull(artifact.verified, "artifact.verified"), final: boolOrNull(artifact.final, "artifact.final"),
      cumulativePatch: parsePatch(artifact.cumulativePatch),
    }];
  });
}

function parseIntegration(value: unknown): Integration {
  const integration = rec(value, "integration");
  const target = recOrNull(integration.target, "integration.target");
  const verification = recOrNull(integration.verification, "integration.verification") ?? {};
  const summary = strOrNull(verification.summary, "integration.verification")?.trim() || null;
  return {
    integrationId: str(integration.integrationId, "integration.integrationId"),
    artifactId: str(integration.artifactId, "integration.artifactId"),
    state: str(integration.state, "integration.state"), strategy: str(integration.strategy, "integration.strategy"),
    target: target && { path: strOrNull(target.path, "integration.target"), ref: strOrNull(target.ref, "integration.target") },
    beforeCommit: strOrNull(integration.beforeCommit, "integration.beforeCommit"),
    afterCommit: strOrNull(integration.afterCommit, "integration.afterCommit"),
    verificationSummary: summary, hostPaths: strings(verification.hostPaths, "integration.verification"),
    note: strOrNull(integration.note, "integration.note"),
    actor: actorOrNull(integration.actor, "integration.actor"), createdAt: str(integration.createdAt, "integration.createdAt"),
  };
}

function parseConclusion(value: unknown): HostConclusion {
  const conclusion = rec(value, "hostConclusion");
  return {
    conclusionId: str(conclusion.conclusionId, "hostConclusion.conclusionId"),
    executionStatus: str(conclusion.executionStatus, "hostConclusion.executionStatus"),
    note: strOrNull(conclusion.note, "hostConclusion.note"), reason: reasonOrNull(conclusion.reason, "hostConclusion.reason"), evidence: strings(conclusion.evidence, "hostConclusion.evidence"),
    artifactId: strOrNull(conclusion.artifactId, "hostConclusion.artifactId"),
    integrationId: strOrNull(conclusion.integrationId, "hostConclusion.integrationId"),
    attemptId: strOrNull(conclusion.attemptId, "hostConclusion.attemptId"),
    actor: actor(conclusion.actor, "hostConclusion.actor"), createdAt: str(conclusion.createdAt, "hostConclusion.createdAt"),
    runRevision: intOrNull(conclusion.runRevision, "hostConclusion.runRevision"),
  };
}

/** The frozen bounds in a routing view or a decision: what it could choose within. */
function parseBounds(source: Rec, what: string): Omit<Basis, "preferences"> {
  const basis = recOrNull(source.routingBasis, what);
  return {
    constraints: partialBuddy(source.constraints ?? {}, what),
    requiredCapabilities: strings(source.requiredCapabilities, what),
    candidateCount: basis ? intOrNull(basis.candidateCount, what) : null,
    excluded: basis ? list(basis.excludedBuddies ?? [], what).map(item => {
      const excluded = rec(item, what);
      return { buddy: partialBuddy(excluded, what), note: strOrNull(excluded.note, what)?.trim() || null };
    }) : [],
    coolingCount: basis ? intOrNull(basis.coolingCount, what) : null,
    cooling: basis ? list(basis.coolingBuddies ?? [], what).map(item => {
      const cooled = rec(item, what);
      return { buddy: partialBuddy(cooled, what), coolingUntil: strOrNull(cooled.coolingUntil, what) };
    }) : [],
  };
}

function parseRouting(value: unknown): Routing | null {
  const routing = recOrNull(value, "routing");
  return routing && {
    status: str(routing.status, "routing.status"), decisionId: strOrNull(routing.decisionId, "routing.decisionId"),
    source: strOrNull(routing.source, "routing.source"), basis: { ...parseBounds(routing, "routing"), preferences: [] },
  };
}

/** The compact collaboration record of one micro task (`workflow_get {runId}`). */
export function parseWorkflow(value: unknown, runId: string): Workflow {
  const workflow = rec(value, "workflow");
  if (workflow.runId !== runId) refuse("workflow.runId");
  const counts = rec(workflow.counts, "workflow.counts");
  const shutdown = recOrNull(workflow.shutdown, "workflow.shutdown");
  const workspace = recOrNull(workflow.workspace, "workflow.workspace");
  return {
    runId, revision: int(workflow.revision, "workflow.revision"), state: str(workflow.state, "workflow.state"),
    hostId: strOrNull(workflow.hostId, "workflow.hostId"), buddy: parseBuddy(workflow.buddy, "workflow.buddy"),
    buddyLocked: bool(workflow.buddyLocked, "workflow.buddyLocked"), delegationAuthority: bool(workflow.delegationAuthority, "workflow.delegationAuthority"),
    routing: parseRouting(workflow.routing),
    turns: list(workflow.turns, "workflow.turns").map(parseTurn),
    currentTurn: workflow.currentTurn === null || workflow.currentTurn === undefined ? null : parseResultTurn(workflow.currentTurn),
    activeRequest: workflow.activeRequest === null || workflow.activeRequest === undefined ? null : parseOpenRequest(workflow.activeRequest),
    requests: list(workflow.requests, "workflow.requests").map(parseCompactRequest),
    counts: { turns: int(counts.turns, "workflow.counts"), openRequests: int(counts.openRequests, "workflow.counts") },
    children: list(workflow.children, "workflow.children").map(child => {
      const item = rec(child, "workflow.children");
      return { taskId: str(item.taskId, "child.taskId"), role: str(item.role, "child.role"), state: oneOf(item.state, ["active", "attention", "succeeded", "failed", "cancelled"] as const, "child.state") };
    }),
    artifacts: parseArtifacts(workflow.artifacts),
    integrations: list(workflow.integrations, "workflow.integrations").map(parseIntegration),
    finalArtifactId: strOrNull(workflow.finalArtifactId, "workflow.finalArtifactId"),
    hostConclusion: workflow.hostConclusion === null || workflow.hostConclusion === undefined ? null : parseConclusion(workflow.hostConclusion),
    cwd: workspace ? strOrNull(workspace.path, "workflow.workspace.path") : null,
    shutdown: shutdown && {
      selfConfirmed: bool(shutdown.selfConfirmed, "workflow.shutdown"),
      descendantsConfirmed: bool(shutdown.descendantsConfirmed, "workflow.shutdown"),
      unconfirmedCount: int(shutdown.unconfirmedCount, "workflow.shutdown"),
    },
  };
}

function parseAuditRequest(value: unknown): AuditRequest {
  const request = rec(value, "audit.request");
  const decision = recOrNull(request.decision, "audit.request.decision");
  const payload = rec(request.payload, "audit.request.payload");
  const resolution = decision && recOrNull(decision.resolution, "audit.request.decision");
  return {
    ...parseOpenRequest(value),
    turnId: strOrNull(request.turnId, "audit.request.turnId"),
    decision: decision && {
      decision: strOrNull(decision.decision, "audit.request.decision"),
      note: strOrNull(decision.note, "audit.request.decision"),
      actor: actorOrNull(decision.actor ?? resolution?.actor, "audit.request.decision"),
    },
    decisionId: strOrNull(payload.decisionId, "audit.request.payload"),
  };
}

function parseContinuation(value: unknown): Continuation {
  const continuation = rec(value, "audit.continuation");
  return {
    continuationId: str(continuation.continuationId, "audit.continuation"), turnId: strOrNull(continuation.turnId, "audit.continuation"),
    input: str(continuation.input, "audit.continuation"), createdAt: str(continuation.createdAt, "audit.continuation"),
    kind: str(continuation.kind, "audit.continuation"), note: strOrNull(continuation.note, "audit.continuation"),
  };
}

function parseRoutingEntry(value: unknown): RoutingEntry {
  const entry = rec(value, "routingHistory");
  const mode = entry.routingMode;
  return {
    decisionId: str(entry.decisionId, "routingHistory.decisionId"), status: str(entry.status, "routingHistory.status"),
    source: strOrNull(entry.source, "routingHistory.source"),
    selectedBuddy: parseBuddy(entry.selectedBuddy, "routingHistory.selectedBuddy"),
    routerBuddy: parseBuddy(entry.routerBuddy, "routingHistory.routerBuddy"),
    routingMode: (mode === null || mode === undefined ? null : oneOf(mode, ["fast", "review"] as const, "routingHistory.routingMode")) as RoutingMode | null,
    reason: reasonOrNull(entry.reason, "routingHistory.reason"),
    note: strOrNull(entry.note, "routingHistory.note"),
  };
}

/** One page of the routing decisions of a micro task, newest first. */
type RoutingPage = { entries: RoutingEntry[]; nextCursor: number | null };

export function parseRoutingPage(value: unknown, runId: string): RoutingPage {
  const reply = rec(value, "routingHistory");
  if (reply.runId !== runId) refuse("routingHistory.runId");
  const history = rec(reply.routingHistory, "routingHistory");
  return { entries: list(history.entries, "routingHistory.entries").map(parseRoutingEntry), nextCursor: intOrNull(history.nextCursor, "routingHistory.nextCursor") };
}

/** The audit read: what the compact record leaves out, and the first page of the routing decisions. */
export function parseAudit(value: unknown, runId: string): { audit: Audit; routing: RoutingPage } {
  const reply = rec(value, "audit");
  if (reply.runId !== runId) refuse("audit.runId");
  const audit = rec(reply.audit, "audit");
  return {
    audit: {
      revision: int(reply.revision, "audit.revision"),
      turns: list(audit.turns, "audit.turns").map(parseResultTurn),
      continuations: list(audit.continuations, "audit.continuations").map(parseContinuation),
      requests: list(audit.requests, "audit.requests").map(parseAuditRequest),
      hostConclusions: list(audit.hostConclusions, "audit.hostConclusions").map(parseConclusion),
    },
    routing: parseRoutingPage(value, runId),
  };
}

/** What a routing decision was bound by when it was asked for (`selection_get {decisionId, includeAudit: true}`). */
export function parseBasis(value: unknown, decisionId: string): Basis {
  const decision = rec(rec(value, "decision").decision, "decision");
  if (decision.decisionId !== decisionId) refuse("decision.decisionId");
  const output = recOrNull(decision.output, "decision.output");
  const program = output && recOrNull(output.programSelection, "decision.output");
  return {
    ...parseBounds(decision, "decision"),
    preferences: program ? list(program.preferences ?? [], "decision.output").map(item => {
      const preference = rec(item, "decision.output");
      return {
        buddyId: str(preference.buddyId, "decision.output"), mode: oneOf(preference.mode, ["prefer", "pin", "exclude"] as const, "decision.output"),
        note: strOrNull(preference.note, "decision.output")?.trim() || null,
      };
    }) : [],
  };
}

const INQUIRY_STATES: readonly InquiryState[] = ["queued", "claimed", "delivered", "answered", "discarded", "unavailable"];

function parseInquiry(value: unknown): Inquiry {
  const message = rec(value, "message");
  const delivery = recOrNull(message.delivery, "message.delivery");
  const answer = recOrNull(message.answer, "message.answer");
  return {
    messageId: str(message.messageId, "message.messageId"), inquiryId: str(message.inquiryId, "message.inquiryId"), attemptId: strOrNull(message.attemptId, "message.attemptId"),
    question: str(message.question, "message.question"), author: str(message.author, "message.author"),
    createdAt: str(message.createdAt, "message.createdAt"), state: oneOf(message.state, INQUIRY_STATES, "message.state"),
    reason: reasonOrNull(message.reason, "message.reason"),
    note: strOrNull(message.note, "message.note"),
    deliveredAt: delivery ? strOrNull(delivery.deliveredAt, "message.delivery") : null,
    answer: answer && { text: str(answer.text, "message.answer"), at: strOrNull(answer.at, "message.answer"), truncated: bool(answer.truncated, "message.answer") },
  };
}

/** The inquiries of one micro task, oldest first; `limit` is how many were asked for. */
export function parseInquiries(value: unknown, limit: number): Inquiries {
  const messages = list(rec(value, "messages").messages, "messages").map(parseInquiry);
  return { items: messages.reverse(), more: messages.length >= limit };
}

const PHASES: readonly ActivityPhase[] = ["starting", "waiting-model", "streaming-model", "tool-running", "waiting-external", "waiting-host", "finishing", "unknown"];
const STOPPERS = ["host", "lead", "console", "service", "unknown"] as const;

function parseActivity(value: unknown): Activity | null {
  const activity = recOrNull(value, "task.activity");
  if (!activity) return null;
  const counts = recOrNull(activity.counts, "task.activity.counts");
  const toolName = strOrNull(activity.toolName, "task.activity");
  const lastToolAt = strOrNull(activity.lastToolActivityAt, "task.activity");
  const lastNativeAt = strOrNull(activity.lastNativeActivityAt, "task.activity");
  const nativeSession = strOrNull(activity.nativeSessionId, "task.activity");
  const observedAt = strOrNull(activity.observedAt, "task.activity");
  const eventSeq = intOrNull(activity.eventSeq, "task.activity");
  return {
    phase: PHASES.includes(activity.phase as ActivityPhase) ? (activity.phase as ActivityPhase) : "unknown",
    observedAt, toolName, lastToolAt, lastNativeAt, eventSeq,
    modelTurns: counts ? intOrNull(counts.modelTurns, "task.activity.counts") : null,
    toolCalls: counts ? intOrNull(counts.toolCalls, "task.activity.counts") : null,
    basis: toolName || lastToolAt ? "tool" : lastNativeAt || nativeSession ? "native" : observedAt || eventSeq !== null ? "phase" : "unknown",
  };
}

function parseSession(receipt: Rec | null): Session | null {
  const result = receipt && recOrNull(receipt.result, "attempt.result");
  const native = result && recOrNull(result.nativeSession, "attempt.nativeSession");
  const identity = result && recOrNull(result.nativeIdentity, "attempt.nativeIdentity");
  const id = strOrNull(native?.sessionId, "attempt.nativeSession") ?? strOrNull(identity?.sessionId, "attempt.nativeIdentity");
  if (!native && id === null) return null;
  return {
    id,
    scope: strOrNull(native?.storageScope, "attempt.nativeSession"), owner: strOrNull(native?.storageOwner, "attempt.nativeSession"),
    visibility: strOrNull(native?.nativeAppVisibility, "attempt.nativeSession"),
    resumable: boolOrNull(native?.resumable, "attempt.nativeSession"), binding: boolOrNull(native?.bindingPresent, "attempt.nativeSession"),
    note: strOrNull(native?.note, "attempt.nativeSession")?.trim() || null,
  };
}

function parseAttempt(task: Rec): Attempt | null {
  const attempt = recOrNull(task.selectedAttempt, "task.selectedAttempt");
  if (!attempt) return null;
  const attemptId = str(attempt.attemptId, "task.selectedAttempt");
  const active = task.activeAttemptId === attemptId;
  const reminders = rec(attempt.reminders, "task.selectedAttempt.reminders");
  const receipt = recOrNull(attempt.result, "task.selectedAttempt.result");
  const quotaFailure = recOrNull(task.quotaFailure, "task.quotaFailure");
  const quota = quotaFailure ? oneOf(quotaFailure.code, ["quota-exceeded", "rate-limited"] as const, "task.quotaFailure") : null;
  const initiator = attempt.stopInitiator ?? task.stopInitiator;
  const cancellation = recOrNull(task.cancellation, "task.cancellation");
  const cancelled = task.status === "cancelled" || task.status === "cancelling" || recOrNull(task.workflow, "task.workflow")?.state === "cancelled";
  const raw = strOrNull(receipt?.terminationReason, "task.selectedAttempt.result")?.trim() || strOrNull(task.terminationReason, "task.terminationReason")?.trim() || null;
  const who = cancellation ? actorOrNull(cancellation.actor, "task.cancellation") : null;
  const stopper = initiator === null || initiator === undefined ? null : oneOf(initiator, STOPPERS, "task.stopInitiator");
  const stoppedBy = stopper ?? (cancelled ? (who && (STOPPERS as readonly string[]).includes(who.role) ? who.role as (typeof STOPPERS)[number] : "unknown") : null);
  // Only a person who stopped it is named; the console and the service are the stopper, not a name.
  const named = who && (stoppedBy === "host" || stoppedBy === "lead") && who.role === stoppedBy ? who : null;
  // A run that is over and says no cause is not a normal end: its cause is unknown.
  const normal = raw === "completed" || (raw === null && !cancelled && !quota && !["completed", "failed"].includes(task.status as string));
  const ending: Ending | null = normal ? null : {
    cause: raw, stoppedBy: cancelled ? stoppedBy : null, actor: cancelled ? named : null,
    note: cancelled && cancellation ? strOrNull(cancellation.note, "task.cancellation.note") : null, quota,
  };
  return {
    attemptId, active,
    startedAt: strOrNull(attempt.startedAt, "task.selectedAttempt"), finishedAt: strOrNull(attempt.finishedAt, "task.selectedAttempt"),
    error: strOrNull(attempt.error, "task.selectedAttempt"),
    releaseReason: reasonOrNull(attempt.releaseReason, "task.selectedAttempt.releaseReason"),
    overdue: active && bool(reminders.overdue, "task.selectedAttempt.reminders"),
    possiblyStuck: active && bool(reminders.possiblyStuck, "task.selectedAttempt.reminders"),
    activity: active ? parseActivity(task.activity) : null,
    ending, session: parseSession(receipt),
    tokenUsage: parseTokenUsage(attempt.tokenUsage),
    shutdownConfirmed: bool(attempt.shutdownConfirmed, "task.selectedAttempt"),
  };
}

const LINEAGE_KINDS = ["micro-task", "helper"] as const;

function parseLineage(value: unknown): Rec {
  const lineage = rec(value, "task.lineage");
  oneOf(lineage.kind, LINEAGE_KINDS, "task.lineage.kind");
  for (const key of ["sourceHostId", "currentHostId", "parentRunId", "rootRunId", "rootLeadRunId"]) {
    if (lineage[key] === undefined) refuse(`task.lineage.${key}`);
    strOrNull(lineage[key], `task.lineage.${key}`);
  }
  if (lineage.project === undefined || lineage.buddy === undefined) refuse("task.lineage");
  const project = recOrNull(lineage.project, "task.lineage.project");
  if (project) {
    str(project.id, "task.lineage.project.id");
    str(project.label, "task.lineage.project.label");
    strOrNull(project.path, "task.lineage.project.path");
  }
  parseBuddy(lineage.buddy, "task.lineage.buddy");
  const ownership = recOrNull(lineage.leadOwnership, "task.lineage.leadOwnership");
  if (ownership) str(ownership.leadRunId, "task.lineage.leadOwnership.leadRunId");
  return lineage;
}

/**
 * The task view of one run, as `/tasks/<id>` answers. The shared presentation
 * helpers read the task as it came; what the detail reads itself is made
 * definite here.
 */
export function parseTask(value: unknown, runId: string): TaskView {
  const task = rec(value, "task");
  if (task.runId !== runId) refuse("task.runId");
  for (const key of ["task", "status", "cwd", "createdAt", "updatedAt"]) str(task[key], `task.${key}`);
  for (const key of ["acceptedAt", "reviewRejectedAt", "reviewRejectionNote", "activeAttemptId", "terminationReason"]) strOrNull(task[key], `task.${key}`);
  const lineage = parseLineage(task.lineage);
  const workflow = rec(task.workflow, "task.workflow");
  str(workflow.state, "task.workflow.state");
  for (const key of ["hostId", "title", "macroTaskId"]) strOrNull(workflow[key], `task.workflow.${key}`);
  return {
    task: task as unknown as Task, updatedAt: task.updatedAt as string, attempt: parseAttempt(task),
    lead: (lineage.rootLeadRunId ?? null) !== null,
  };
}
