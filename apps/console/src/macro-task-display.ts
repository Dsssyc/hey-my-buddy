import i18n from "i18next";
import { dateTimeFormat } from "./i18n/format";
import { parseTimelineInstant } from "./timeline/instant";
import { excerpt } from "./task-state";
import { stopActorText } from "./actor";
import type { TimelineBuddy, TimelineEvent, TimelineRow, TimelineSpan } from "./macro-task-types";

/** Stable identity for an execution buddy's palette colour. */
export function buddyColorKey(buddy: TimelineBuddy | null): string {
  return JSON.stringify([buddy?.harness ?? null, buddy?.provider ?? null, buddy?.model ?? null, buddy?.effort ?? null]);
}

/** The same recorded event has one name on the timeline and in its detail. */
export function eventName(event: TimelineEvent): string {
  return event.kind === "cancel" ? stopActorText(event.actor?.role)
    : i18n.t(`tasks:drawing.events.${event.kind}`);
}

/** An event carries its own decision facts and authored note, without service prose. */
export function eventDetails(event: TimelineEvent): string {
  return [...(["decision", "verdict", "state", "strategy"] as const).flatMap(key =>
    event[key] ? [i18n.t(`taskDetail:event.value.${event[key]}`, { defaultValue: event[key] })] : []), event.note].filter(Boolean).join(" · ");
}

/** Recorded execution states that keep a null-ended span visually open. */
const OPEN_EXECUTION_STATES = new Set(["starting", "executing", "finalizing", "running", "active"]);

export type SpanOutcome =
  | "open" | "admitted" | "resolved" | "running" | "failed"
  | "cancelled" | "unknown" | "finished" | "unplaced" | "abstention" | "rejected";

export function spanOutcome(span: TimelineSpan): SpanOutcome {
  const state = typeof span.state === "string" ? span.state.trim().toLowerCase() : "";
  if (span.kind === "queue") return state === "queued" && span.endAt == null ? "open" : "admitted";
  if (span.kind === "host") return state === "open" && span.endAt == null ? "open" : "resolved";
  // Unknown shutdown outranks a recorded failure disposition: an unconfirmed
  // stop is never labelled plain failed/cancelled, though the failure text stays.
  if (span.uncertain === true || state === "uncertain") return "unknown";
  if (span.kind === "execution" && state === "rejected") return "rejected";
  if (span.resultStatus === "cancelled") return "cancelled";
  if (span.kind === "routing" && span.disposition === "routing-failed") return "failed";
  if (span.kind === "routing" && (state === "abstention" || span.disposition === "abstention")) return "abstention";
  if (span.resultStatus === "failed") return "failed";
  if (span.resultStatus !== "ok" && typeof span.error === "string" && span.error.trim()) return "failed";
  if (span.endAt == null) {
    return OPEN_EXECUTION_STATES.has(state) && span.shutdownConfirmed !== true ? "running" : "unplaced";
  }
  return "finished";
}

const OUTCOME_LABEL: Record<SpanOutcome, string> = {
  get open() { return i18n.t("console:stillWaiting"); }, get admitted() { return i18n.t("console:started"); }, get resolved() { return i18n.t("console:decided"); }, get running() { return i18n.t("console:executing"); },
  get failed() { return i18n.t("console:executionFailed"); }, get cancelled() { return i18n.t("console:cancelled"); }, get unknown() { return i18n.t("console:stopUnconfirmed"); }, get finished() { return i18n.t("console:executionCompleted"); },
  get unplaced() { return i18n.t("console:timeMissing"); }, get abstention() { return i18n.t("console:abstained"); },
  get rejected() { return i18n.t("console:reroutedAfterRejection"); },
};

/** Host-request decision labels use the recorded request state, not a guess. */
const HOST_STATE_LABEL: Record<string, string> = {
  get approved() { return i18n.t("console:approved"); }, get declined() { return i18n.t("console:declined"); }, get superseded() { return i18n.t("console:superseded"); }, get cancelled() { return i18n.t("console:cancelled"); },
};

export function outcomeLabel(span: TimelineSpan, outcome: SpanOutcome): string {
  if (span.kind === "routing") {
    if (outcome === "running") return i18n.t("console:routingInProgress");
    if (outcome === "failed") return i18n.t("console:routingFailed");
    if (outcome === "finished") return i18n.t("console:completed");
  }
  if (span.kind === "queue") return outcome === "open" ? i18n.t("console:stillQueued") : i18n.t("console:started");
  if (span.kind === "host" && outcome === "resolved") {
    const state = typeof span.state === "string" ? span.state.trim().toLowerCase() : "";
    return HOST_STATE_LABEL[state] || span.state || i18n.t("console:decided");
  }
  return OUTCOME_LABEL[outcome];
}

const ROW_STATE_STYLE: Record<string, { tone: "green" | "amber" | "red" | "neutral"; glyph: string; glyphClass: string }> = {
  executing: { tone: "green", glyph: "▸", glyphClass: "running" },
  running: { tone: "green", glyph: "▸", glyphClass: "running" },
  "awaiting-host": { tone: "amber", glyph: "◆", glyphClass: "warn" },
  "waiting-owned": { tone: "amber", glyph: "◆", glyphClass: "warn" },
  "waiting-helpers": { tone: "green", glyph: "▸", glyphClass: "running" },
  delivered: { tone: "neutral", glyph: "✓", glyphClass: "muted" },
  accepted: { tone: "neutral", glyph: "✓", glyphClass: "ok" },
  cancelled: { tone: "amber", glyph: "◆", glyphClass: "warn" },
  cancelling: { tone: "green", glyph: "▸", glyphClass: "running" },
  failed: { tone: "amber", glyph: "◆", glyphClass: "warn" },
  "reconciliation-needed": { tone: "amber", glyph: "?", glyphClass: "warn" },
};

const STATE_LABELS: Record<string, string> = {
  get queued() { return i18n.t("console:queued"); }, get running() { return i18n.t("console:executing"); }, get cancelling() { return i18n.t("console:cancelling"); }, get completed() { return i18n.t("console:executionCompleted"); },
  get "reconciliation-needed"() { return i18n.t("console:awaitingReconciliation"); },
  get executing() { return i18n.t("console:executing"); }, get "awaiting-host"() { return i18n.t("console:waitingForHost"); }, get "waiting-helpers"() { return i18n.t("console:helpersRunning"); },
  get "waiting-owned"() { return i18n.t("console:waitingForOwnedMicroTasks"); },
  get delivered() { return i18n.t("console:awaitingAcceptance"); }, get accepted() { return i18n.t("console:accepted"); }, get concluded() { return i18n.t("console:ended"); },
};

/** Settled terminal states need no Host action. */
const SETTLED_STATES = new Set(["accepted", "concluded", "completed"]);

export function rowStateInfo(row: TimelineRow, rows: readonly TimelineRow[] = []): { label: string; tone: "green" | "amber" | "red" | "neutral"; glyph: string; glyphClass: string; settled: boolean } {
  if (row.status === "queued" && row.queueReason === "paused") return {
    label: i18n.t("shell:macroWork.paused"), tone: "neutral", glyph: "Ⅱ", glyphClass: "muted", settled: false,
  };
  const key = row.status === "cancelling" || row.state === "cancelled" && rows.some(owned => owned.rootLeadRunId === row.runId && !owned.shutdownConfirmed)
    ? "cancelling"
    : (row.state === "failed" || row.state === "cancelled") && ["queued", "running", "reconciliation-needed"].includes(row.status)
      ? row.status : row.state || row.status;
  const style = ROW_STATE_STYLE[key];
  if (row.kind === "helper" && (key === "failed" || key === "cancelled")) {
    return key === "failed"
      ? { label: i18n.t("console:executionFailed"), tone: "red", glyph: "✕", glyphClass: "bad", settled: false }
      : { label: i18n.t("console:cancelled"), tone: "neutral", glyph: "⊘", glyphClass: "muted", settled: true };
  }
  return {
    label: key === "cancelling" ? i18n.t("tasks:stopping")
      : key === "failed" || key === "cancelled" ? i18n.t(`taskDetail:state.${key}${row.rootLeadRunId ? "Lead" : "Host"}`)
      : key === "awaiting-host" && row.rootLeadRunId ? i18n.t("console:waitingForLead") : STATE_LABELS[key] || key || i18n.t("console:unknown"),
    tone: style?.tone ?? "neutral",
    glyph: style?.glyph ?? "■",
    glyphClass: style?.glyphClass ?? "muted",
    settled: SETTLED_STATES.has(key),
  };
}

const TASK_TITLE_CHARS = 40;

type TitleLine = {
  /** The text shown in labels; single line. */
  text: string;
  /** The complete recorded title for accessible details. */
  fullText: string;
  /** True when the recorded title was longer than the display cap. */
  clipped: boolean;
  /** True for a title taken from the task's first line. */
  fromTask: boolean;
};

/**
 * One-line title for lists and task labels. A task
 * first-line fallback is capped at ~40 characters so a whole task paragraph
 * never masquerades as a title; explicit macroTask/Host titles keep their
 * recorded bounded text. The complete first line stays available as `fullText`.
 */
export function displayTitle(titleSource: string, recorded: string | null): TitleLine {
  const title = titleSource === "none" ? i18n.t("console:unnamedMicroTask") : recorded!;
  const fromTask = titleSource === "task";
  const text = fromTask ? excerpt(title, TASK_TITLE_CHARS) : title;
  return { text, fullText: title, clipped: text !== title, fromTask };
}

export type BuddyNameParts = { name: string; effort: string };

/** The recorded model id and native effort, available separately for execution bars. */
export function buddyNameParts(buddy: TimelineBuddy | null | undefined): BuddyNameParts {
  return { name: buddy?.model ?? "", effort: (buddy?.effort ?? "").trim() };
}

/** One consistent model id and effort form for task rows and timeline spans. */
export function buddyLabel(buddy: TimelineBuddy | null | undefined): string {
  const { name, effort } = buddyNameParts(buddy);
  return [name, effort].filter(Boolean).join(" · ") || i18n.t("console:buddyNotRecorded");
}

/* ---- time and duration formatting (display only) ---- */

function format(value: string | number | null | undefined, options: Intl.DateTimeFormatOptions): string {
  if (value === null || value === undefined || value === "") return i18n.t("console:notRecorded");
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? i18n.t("console:notRecorded") : dateTimeFormat(options).format(date);
}

export function clockTime(value: string | number | null | undefined): string {
  return format(value, { hour: "2-digit", minute: "2-digit", hour12: false });
}

/**
 * The measured acceptance wait (0.16 P1.8): from the row's last execution
 * span's recorded end to its acceptance marker. Null whenever any condition
 * fails — no flag, any execution without a recorded end, a last stop that is
 * not explicitly confirmed
 * (`shutdownConfirmed === true`; a finished/endAt span with null/false
 * shutdown is not confirmation), or an end after the acceptance — because the
 * wait must never be invented.
 */
export function acceptanceWaitMs(row: TimelineRow, spans: readonly TimelineSpan[]): number | null {
  const settledAt = row.acceptedAt ?? row.reviewRejectedAt;
  if (!settledAt) return null;
  const atMs = parseTimelineInstant(settledAt);
  if (atMs === null) return null;
  const executions = spans
    .filter(span => span.kind === "execution")
    .map(span => ({ span, endMs: parseTimelineInstant(span.endAt) }));
  // An execution without a recorded end (still running, or its end missing)
  // means the last stop is not known, so no earlier end may stand in for it.
  if (executions.some(entry => entry.endMs === null)) return null;
  const last = (executions as { span: TimelineSpan; endMs: number }[])
    .sort((left, right) => left.endMs - right.endMs)[executions.length - 1];
  if (!last) return null;
  if (last.span.shutdownConfirmed !== true) return null;
  if (spanOutcome(last.span) === "unknown") return null;
  return last.endMs <= atMs ? atMs - last.endMs : null;
}
