import i18n from "i18next";
import { ApiError } from "../api";
import { parseActor } from "../actor";
import type { Actor, RoutingMode, Task, TokenUsage } from "../types";
import type {
  Artifact, Audit, AuditRequest, Buddy, CompactRequest, Continuation, CumulativePatch, CurrentTurn, HostConclusion,
  Inquiries, Inquiry, InquiryState, Integration, OpenRequest, Routing, RoutingEntry, Turn, Workflow,
} from "./types";

/**
 * The one place a reply of the board is checked. Each reader refuses a reply
 * whose shape is not the recorded one with the same read failure; code after it
 * trusts what it returns.
 */
type Rec = Record<string, unknown>;

function refuse(what: string): never {
  throw new ApiError("INVALID_RESPONSE", i18n.t("error.incomplete", { ns: "taskDetail", what }));
}
function rec(value: unknown, what: string): Rec {
  if (!value || typeof value !== "object" || Array.isArray(value)) refuse(what);
  return value as Rec;
}
function str(value: unknown, what: string): string {
  if (typeof value !== "string") refuse(what);
  return value as string;
}
function strOrNull(value: unknown, what: string): string | null {
  return value === null || value === undefined ? null : str(value, what);
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
function list(value: unknown, what: string): unknown[] {
  if (!Array.isArray(value)) refuse(what);
  return value as unknown[];
}
function strings(value: unknown, what: string): string[] {
  return value === null || value === undefined ? [] : list(value, what).map(item => str(item, what));
}
function actorOrNull(value: unknown): Actor | null {
  return parseActor(value);
}

export function parseBuddy(value: unknown, what: string): Buddy | null {
  if (value === null || value === undefined) return null;
  const buddy = rec(value, what);
  return {
    harness: str(buddy.harness, what), provider: str(buddy.provider, what),
    model: str(buddy.model, what), effort: str(buddy.effort, what),
  };
}

/**
 * One execution's native usage. A usage that cannot be read as the recorded
 * per-execution shape is unknown, never a number made up from it; the cached
 * input is a subset of the input.
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
  if (counts.cachedInputTokens !== null && counts.inputTokens !== null && counts.cachedInputTokens > counts.inputTokens) return null;
  const completeness = row.completeness === "complete" || row.completeness === "partial" || row.completeness === "unknown"
    ? row.completeness : undefined;
  return { ...counts, source: row.source, scope: "attempt", ...(completeness ? { completeness } : {}) };
}

function parseTurn(value: unknown): Turn {
  const turn = rec(value, "turn");
  const routing = turn.routing === null || turn.routing === undefined ? null : rec(turn.routing, "turn.routing");
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

function parseCurrentTurn(value: unknown): CurrentTurn {
  const turn = rec(value, "currentTurn");
  return {
    ...parseTurn(value),
    summary: turn.summary === undefined ? "" : str(turn.summary, "currentTurn.summary"),
    summaryTruncated: turn.summaryTruncated === true,
    remaining: strings(turn.remaining, "currentTurn.remaining"),
  };
}

function parseCompactRequest(value: unknown): CompactRequest {
  const request = rec(value, "request");
  return {
    requestId: str(request.requestId, "request.requestId"), kind: str(request.kind, "request.kind"),
    state: str(request.state, "request.state"), createdAt: str(request.createdAt, "request.createdAt"),
    decidedAt: strOrNull(request.decidedAt, "request.decidedAt"),
    childTaskId: strOrNull(request.childTaskId, "request.childTaskId"), routing: request.routing === true,
  };
}

function parseOpenRequest(value: unknown): OpenRequest {
  const request = rec(value, "request");
  const problem = request.preparationError === null || request.preparationError === undefined
    ? null : rec(request.preparationError, "request.preparationError");
  return {
    ...parseCompactRequest(value),
    summary: str(request.summary, "request.summary"),
    attempted: strOrNull(request.attempted, "request.attempted"),
    neededWork: strings(request.neededWork, "request.neededWork"),
    expectedArtifacts: strings(request.expectedArtifacts, "request.expectedArtifacts"),
    acceptance: strOrNull(request.acceptance, "request.acceptance"),
    preparationError: problem && { code: str(problem.code, "request.preparationError"), message: str(problem.message, "request.preparationError") },
  };
}

function parsePatch(value: unknown): CumulativePatch | null {
  if (value === null || value === undefined) return null;
  const patch = rec(value, "cumulativePatch");
  return {
    baseCommit: strOrNull(patch.baseCommit, "cumulativePatch"), outputCommit: strOrNull(patch.outputCommit, "cumulativePatch"),
    path: strOrNull(patch.path, "cumulativePatch"), sha256: strOrNull(patch.sha256, "cumulativePatch"),
    changedPaths: strings(patch.changedPaths, "cumulativePatch"),
  };
}

function parseArtifact(value: unknown): Artifact {
  const artifact = rec(value, "artifact");
  const kind = str(artifact.kind, "artifact.kind");
  return {
    artifactId: str(artifact.artifactId, "artifact.artifactId"), kind,
    attemptId: str(artifact.attemptId, "artifact.attemptId"),
    sourceTaskId: str(artifact.sourceTaskId, "artifact.sourceTaskId"),
    manifestSha256: str(artifact.manifestSha256, "artifact.manifestSha256"),
    outputCommit: strOrNull(artifact.outputCommit, "artifact.outputCommit"),
    diffPath: strOrNull(artifact.diffPath, "artifact.diffPath"),
    changedPaths: strings(artifact.changedPaths, "artifact.changedPaths"),
    partial: artifact.partial === true || kind === "partial-output",
    cumulativePatch: parsePatch(artifact.cumulativePatch),
  };
}

function parseIntegration(value: unknown): Integration {
  const integration = rec(value, "integration");
  const target = integration.target === null || integration.target === undefined ? null : rec(integration.target, "integration.target");
  const verification = integration.verification === null || integration.verification === undefined
    ? {} : rec(integration.verification, "integration.verification");
  const summary = typeof verification.summary === "string" && verification.summary.trim() ? verification.summary.trim() : null;
  return {
    integrationId: str(integration.integrationId, "integration.integrationId"),
    artifactId: str(integration.artifactId, "integration.artifactId"),
    state: str(integration.state, "integration.state"), strategy: str(integration.strategy, "integration.strategy"),
    target: target && { path: str(target.path, "integration.target"), ref: str(target.ref, "integration.target") },
    beforeCommit: strOrNull(integration.beforeCommit, "integration.beforeCommit"),
    afterCommit: strOrNull(integration.afterCommit, "integration.afterCommit"),
    verificationSummary: summary, hostPaths: strings(verification.hostPaths, "integration.verification"),
    reason: strOrNull(integration.reason, "integration.reason"),
    actor: actorOrNull(integration.actor), createdAt: str(integration.createdAt, "integration.createdAt"),
  };
}

function parseConclusion(value: unknown): HostConclusion {
  const conclusion = rec(value, "hostConclusion");
  return {
    conclusionId: str(conclusion.conclusionId, "hostConclusion.conclusionId"),
    executionStatus: str(conclusion.executionStatus, "hostConclusion.executionStatus"),
    note: str(conclusion.note, "hostConclusion.note"), evidence: strings(conclusion.evidence, "hostConclusion.evidence"),
    artifactId: strOrNull(conclusion.artifactId, "hostConclusion.artifactId"),
    integrationId: strOrNull(conclusion.integrationId, "hostConclusion.integrationId"),
    attemptId: strOrNull(conclusion.attemptId, "hostConclusion.attemptId"),
    actor: actorOrNull(conclusion.actor), createdAt: str(conclusion.createdAt, "hostConclusion.createdAt"),
    runRevision: intOrNull(conclusion.runRevision, "hostConclusion.runRevision"),
  };
}

function parseRouting(value: unknown): Routing | null {
  if (value === null || value === undefined) return null;
  const routing = rec(value, "routing");
  return {
    status: str(routing.status, "routing.status"), decisionId: strOrNull(routing.decisionId, "routing.decisionId"),
    source: strOrNull(routing.source, "routing.source"),
  };
}

/** The compact collaboration record of one micro task (`workflow_get {runId}`). */
export function parseWorkflow(value: unknown, runId: string): Workflow {
  const workflow = rec(value, "workflow");
  const task = rec(workflow.task, "workflow.task");
  const lineage = rec(task.lineage, "workflow.task.lineage");
  if (workflow.runId !== runId || !["micro-task", "helper"].includes(str(lineage.kind, "lineage.kind"))) refuse("workflow.runId");
  const counts = rec(workflow.counts, "workflow.counts");
  const shutdown = workflow.shutdown === null || workflow.shutdown === undefined ? null : rec(workflow.shutdown, "workflow.shutdown");
  const workspace = workflow.workspace === null || workflow.workspace === undefined ? null : rec(workflow.workspace, "workflow.workspace");
  return {
    runId, revision: int(workflow.revision, "workflow.revision"), state: str(workflow.state, "workflow.state"),
    hostId: strOrNull(workflow.hostId, "workflow.hostId"), buddy: parseBuddy(workflow.buddy, "workflow.buddy"),
    buddyLocked: workflow.buddyLocked === true, delegationAuthority: workflow.delegationAuthority === true,
    routing: parseRouting(workflow.routing),
    turns: list(workflow.turns, "workflow.turns").map(parseTurn),
    currentTurn: workflow.currentTurn === null || workflow.currentTurn === undefined ? null : parseCurrentTurn(workflow.currentTurn),
    activeRequest: workflow.activeRequest === null || workflow.activeRequest === undefined ? null : parseOpenRequest(workflow.activeRequest),
    requests: list(workflow.requests, "workflow.requests").map(parseCompactRequest),
    counts: { openRequests: int(counts.openRequests, "workflow.counts") },
    children: list(workflow.children, "workflow.children").map(child => {
      const item = rec(child, "workflow.children");
      return { taskId: str(item.taskId, "child.taskId"), role: str(item.role, "child.role"), state: str(item.state, "child.state") };
    }),
    artifacts: list(workflow.artifacts, "workflow.artifacts").map(parseArtifact),
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
  const decision = request.decision === null || request.decision === undefined ? null : rec(request.decision, "audit.request.decision");
  const payload = request.payload === null || request.payload === undefined ? null : rec(request.payload, "audit.request.payload");
  return {
    ...parseOpenRequest(value),
    turnId: strOrNull(request.turnId, "audit.request.turnId"),
    decision: decision && {
      decision: typeof decision.decision === "string" ? decision.decision : null,
      reason: typeof decision.reason === "string" && decision.reason.trim() ? decision.reason : null,
      actor: actorOrNull(decision.actor),
    },
    decisionId: payload && typeof payload.decisionId === "string" ? payload.decisionId : null,
  };
}

function parseContinuation(value: unknown): Continuation {
  const continuation = rec(value, "audit.continuation");
  return {
    continuationId: str(continuation.continuationId, "audit.continuation"), turnId: strOrNull(continuation.turnId, "audit.continuation"),
    input: str(continuation.input, "audit.continuation"), createdAt: str(continuation.createdAt, "audit.continuation"),
    kind: str(continuation.kind, "audit.continuation"), reason: strOrNull(continuation.reason, "audit.continuation"),
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
    routingMode: (mode === "fast" || mode === "review" ? mode : null) as RoutingMode | null,
    reason: strOrNull(entry.reason, "routingHistory.reason"),
  };
}

/** The audit read: what the compact record leaves out, and the routing decisions of the micro task. */
export function parseAudit(value: unknown, runId: string): { audit: Audit; routing: RoutingEntry[] } {
  const reply = rec(value, "audit");
  if (reply.runId !== runId) refuse("audit.runId");
  const audit = rec(reply.audit, "audit");
  const history = rec(reply.routingHistory, "routingHistory");
  return {
    audit: {
      revision: int(reply.revision, "audit.revision"),
      turns: list(audit.turns, "audit.turns").map(item => {
        const turn = rec(item, "audit.turns");
        return {
          turnId: str(turn.turnId, "audit.turns"),
          summary: turn.summary === undefined || turn.summary === null ? "" : str(turn.summary, "audit.turns"),
          remaining: strings(turn.remaining, "audit.turns"),
        };
      }),
      continuations: list(audit.continuations, "audit.continuations").map(parseContinuation),
      requests: list(audit.requests, "audit.requests").map(parseAuditRequest),
      hostConclusions: list(audit.hostConclusions, "audit.hostConclusions").map(parseConclusion),
    },
    routing: list(history.entries, "routingHistory.entries").map(parseRoutingEntry),
  };
}

const INQUIRY_STATES: readonly InquiryState[] = ["queued", "claimed", "delivered", "answered", "discarded", "unavailable"];

function parseInquiry(value: unknown): Inquiry {
  const message = rec(value, "message");
  const state = str(message.state, "message.state");
  if (!INQUIRY_STATES.includes(state as InquiryState)) refuse("message.state");
  const delivery = message.delivery === null || message.delivery === undefined ? null : rec(message.delivery, "message.delivery");
  const answer = message.answer === null || message.answer === undefined ? null : rec(message.answer, "message.answer");
  return {
    messageId: str(message.messageId, "message.messageId"), attemptId: strOrNull(message.attemptId, "message.attemptId"),
    question: str(message.question, "message.question"), author: str(message.author, "message.author"),
    createdAt: str(message.createdAt, "message.createdAt"), state: state as InquiryState,
    reason: strOrNull(message.reason, "message.reason"),
    deliveredAt: delivery ? strOrNull(delivery.deliveredAt, "message.delivery") : null,
    answer: answer && { text: str(answer.text, "message.answer"), at: strOrNull(answer.at, "message.answer"), truncated: answer.truncated === true },
  };
}

/** The inquiries of one micro task, oldest first; `limit` is how many were asked for. */
export function parseInquiries(value: unknown, limit: number): Inquiries {
  const messages = list(rec(value, "messages").messages, "messages").map(parseInquiry);
  return { items: messages.reverse(), more: messages.length >= limit };
}

/** The task view of one run, as `/tasks/<id>` answers. */
export function parseTask(value: unknown, runId: string): Task {
  const task = rec(value, "task");
  if (task.runId !== runId || typeof task.task !== "string" || typeof task.status !== "string" || typeof task.revision !== "number") refuse("task");
  return task as Task;
}
