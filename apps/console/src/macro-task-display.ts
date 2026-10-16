import i18n from "i18next";
import { actorRoleText } from "./actor";
import { locale } from "./i18n";
/**
 * Pure display vocabulary for the work macro task timeline. Everything here is
 * derived from recorded facts only: no cost or token claims, no invented
 * durations, and 结束未确认 is never presented as stopped. Positioning and
 * folding stay in the frozen macro-task-timeline-layout module; this file only
 * names things, assigns buddy colours and flattens the chronological
 * list view.
 */

import { recordedRoutingMode } from "./routing-display";
import type {
  MacroTaskCategory,
  MacroTaskSummary,
  MacroTaskTimeline,
  TimelineBuddy,
  TimelineEvent,
  TimelineRow,
  TimelineSpan,
} from "./macro-task-types";
import { TIMELINE_FOLD_THRESHOLD_MS, parseTimelineInstant } from "./macro-task-timeline-layout";
import type { TimelineGap } from "./macro-task-timeline-layout";
import { excerpt } from "./task-state";

export type SectionId = "overview" | "routing" | "assistance" | "artifacts" | "execution";

/** Recorded execution states that keep a null-ended span visually open. */
const OPEN_EXECUTION_STATES = new Set(["starting", "executing", "finalizing", "running", "active"]);

export type SpanOutcome =
  | "open" | "admitted" | "resolved" | "running" | "failed"
  | "cancelled" | "unknown" | "finished" | "unplaced" | "abstention";

export function spanOutcome(span: TimelineSpan): SpanOutcome {
  const state = typeof span.state === "string" ? span.state.trim().toLowerCase() : "";
  if (span.kind === "queue") return state === "queued" && span.endAt == null ? "open" : "admitted";
  if (span.kind === "host") return state === "open" && span.endAt == null ? "open" : "resolved";
  // Unknown shutdown outranks a recorded failure disposition: an unconfirmed
  // stop is never labelled plain failed/cancelled, though the failure text stays.
  if (span.uncertain === true || state === "uncertain") return "unknown";
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

export const OUTCOME_LABEL: Record<SpanOutcome, string> = {
  get open() { return i18n.t("console:stillWaiting"); }, get admitted() { return i18n.t("console:started"); }, get resolved() { return i18n.t("console:decided"); }, get running() { return i18n.t("console:executing"); },
  get failed() { return i18n.t("console:executionFailed"); }, get cancelled() { return i18n.t("console:cancelled"); }, get unknown() { return i18n.t("console:stopUnconfirmed"); }, get finished() { return i18n.t("console:executionCompleted"); },
  get unplaced() { return i18n.t("console:timeMissing"); }, get abstention() { return i18n.t("console:abstained"); },
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

export function spanHead(kind: TimelineSpan["kind"]): string {
  return kind === "queue" ? i18n.t("console:queue") : kind === "routing" ? i18n.t("console:routing") : kind === "host" ? i18n.t("console:waitingForHost") : i18n.t("console:executionSpan");
}

export const SPAN_SECTION: Record<TimelineSpan["kind"], SectionId> = {
  queue: "overview", routing: "routing", execution: "execution", host: "assistance",
};

const EVENT_VOCAB: Record<string, { label: string; glyph: string }> = {
  dispatch: { get label() { return i18n.t("console:dispatch"); }, glyph: "▶" }, decide: { get label() { return i18n.t("console:decide"); }, glyph: "◆" },
  continue: { get label() { return i18n.t("console:continue"); }, glyph: "↻" }, integrate: { get label() { return i18n.t("console:integrate"); }, glyph: "⊕" },
  accept: { get label() { return i18n.t("console:acceptance"); }, glyph: "✓" }, reject: { get label() { return i18n.t("console:acceptanceIssue"); }, glyph: "!" },
  cancel: { get label() { return i18n.t("console:cancel"); }, glyph: "✕" }, takeover: { get label() { return i18n.t("console:takeOver"); }, glyph: "⇄" },
};

export function eventVocab(kind: string): { label: string; glyph: string } {
  return EVENT_VOCAB[kind] ?? { label: kind, glyph: "•" };
}

/**
 * One natural-language Host-event sentence (0.16 P1.7): “13:20 Host 派发「{微任务}」”.
 * A decide marker keeps both verbs unless its raw event kind names one side.
 */
export function eventSentence(event: TimelineEvent, row: TimelineRow | null): string {
  const title = row ? displayTitle(row.titleSource, row.title).text : i18n.t("shell:actor.microTask", { id: event.runId });
  const time = clockTime(event.at);
  const role = actorRoleText(event.actor?.role);
  const sentence = (key: "dispatch" | "approved" | "declined" | "decide" | "continue" | "integrate" | "accept" | "reject" | "stop" | "console" | "service" | "unknown" | "takeover" | "other", actorRole = role) =>
    i18n.t(`shell:actor.events.${key}`, { time, role: actorRole, title, action: event.label || eventVocab(event.kind).label });
  const raw = (event.eventKind ?? "").toLowerCase();
  switch (event.kind) {
    case "dispatch": return sentence("dispatch");
    case "decide": return sentence(raw.includes("approved") ? "approved" : raw.includes("declined") ? "declined" : "decide");
    case "continue": return sentence("continue");
    case "integrate": return sentence("integrate");
    case "accept": return sentence("accept");
    case "reject": return sentence("reject");
    case "cancel": {
      const initiator = event.stopInitiator ?? event.actor?.role;
      if (initiator === "host" || initiator === "lead") return sentence("stop", actorRoleText(initiator));
      return sentence(initiator === "console" || initiator === "service" ? initiator : "unknown");
    }
    case "takeover": return sentence("takeover");
    default: return sentence("other");
  }
}

/** Cluster summary: “Host 事件 n 条：派发 2、验收 1，按 Enter 列出”. */
export function eventClusterLabel(events: readonly TimelineEvent[]): string {
  const byKind = new Map<string, number>();
  for (const event of events) byKind.set(event.kind, (byKind.get(event.kind) ?? 0) + 1);
  const parts = [...byKind.entries()].map(([kind, count]) =>
    count > 1 ? `${eventVocab(kind).label} ${count}` : eventVocab(kind).label);
  return i18n.t("console:hostEventsPressEnterToListThem", { v0: events.length, v1: parts.join(i18n.t("console:listSeparator")) });
}

/** The marker chip of one cluster: the shared glyph with a small count, or ≡ for mixed kinds. */
export function eventClusterGlyph(events: readonly TimelineEvent[]): { glyph: string; count: number } {
  const first = events[0]?.kind ?? "";
  const sameKind = events.every(event => event.kind === first);
  return {
    glyph: events.length > 1 && !sameKind ? "≡" : eventVocab(first).glyph,
    count: events.length > 1 ? events.length : 0,
  };
}

const EVENT_SECTION: Record<string, SectionId> = {
  dispatch: "overview", decide: "assistance", continue: "execution", integrate: "artifacts",
  accept: "artifacts", reject: "artifacts", cancel: "overview", takeover: "overview",
};

export function eventSection(kind: string): SectionId {
  return EVENT_SECTION[kind] ?? "overview";
}

/** Timeline categories are disjoint and already priority-ordered by the service. */
export const CATEGORY_LABEL: Record<MacroTaskCategory, string> = {
  get host() { return i18n.t("console:waitingForHost"); }, get active() { return i18n.t("console:running"); }, get review() { return i18n.t("console:awaitingAcceptance"); }, get ended() { return i18n.t("console:ended"); },
};

/**
 * The one macroTask state word (0.16 0.2): 已完成 is derived only from the
 * recorded acceptance counts — never from a Worker's own summary — and every
 * other ended group keeps the plain 已结束 next to its acceptance progress.
 */
export function macroTaskStateLabel(summary: MacroTaskSummary): string {
  if (summary.state === "ended"
    && summary.counts.roots > 0 && summary.counts.accepted === summary.counts.roots) {
    return i18n.t("console:completed");
  }
  return CATEGORY_LABEL[summary.state];
}

/** The header progress sentence: “{状态} · x / y 个微任务已验收”. */
export function macroTaskProgressText(summary: MacroTaskSummary): string {
  return i18n.t("console:microTasksAccepted", { v0: macroTaskStateLabel(summary), v1: summary.counts.accepted, v2: summary.counts.roots });
}

export function categoryTone(category: MacroTaskCategory): "green" | "amber" | "neutral" {
  return category === "host" ? "amber" : category === "active" ? "green" : "neutral";
}

/** Fixed-order count chips; zero entries are omitted by the caller. */
export const COUNT_ORDER: readonly { key: keyof MacroTaskSummary["counts"]; glyph: string; label: string }[] = [
  { key: "active", glyph: "●", get label() { return i18n.t("console:running"); } },
  { key: "host", glyph: "◆", get label() { return i18n.t("console:waitingForHost"); } },
  { key: "review", glyph: "✓", get label() { return i18n.t("console:awaitingAcceptance"); } },
  { key: "ended", glyph: "■", get label() { return i18n.t("console:ended"); } },
];

export function totalMicroTasks(counts: MacroTaskSummary["counts"]): number {
  return counts.roots + counts.helpers;
}

const ROW_STATE_STYLE: Record<string, { tone: "green" | "amber" | "red" | "neutral"; glyph: string; glyphClass: string }> = {
  executing: { tone: "green", glyph: "▸", glyphClass: "running" },
  "awaiting-host": { tone: "amber", glyph: "◆", glyphClass: "warn" },
  "waiting-owned": { tone: "amber", glyph: "◆", glyphClass: "warn" },
  "waiting-helpers": { tone: "green", glyph: "▸", glyphClass: "running" },
  delivered: { tone: "neutral", glyph: "✓", glyphClass: "muted" },
  accepted: { tone: "neutral", glyph: "✓", glyphClass: "ok" },
  cancelled: { tone: "neutral", glyph: "⊘", glyphClass: "muted" },
  failed: { tone: "red", glyph: "✕", glyphClass: "bad" },
  "reconciliation-needed": { tone: "amber", glyph: "?", glyphClass: "warn" },
};

const STATE_LABELS: Record<string, string> = {
  get queued() { return i18n.t("console:queued"); }, get running() { return i18n.t("console:executing"); }, get cancelling() { return i18n.t("console:cancelling"); }, get completed() { return i18n.t("console:executionCompleted"); },
  get cancelled() { return i18n.t("console:cancelled"); }, get failed() { return i18n.t("console:executionFailed"); }, get "reconciliation-needed"() { return i18n.t("console:awaitingReconciliation"); },
  get executing() { return i18n.t("console:executing"); }, get "awaiting-host"() { return i18n.t("console:waitingForHost"); }, get "waiting-helpers"() { return i18n.t("console:helpersRunning"); },
  get "waiting-owned"() { return i18n.t("console:waitingForOwnedMicroTasks"); },
  get delivered() { return i18n.t("console:awaitingAcceptance"); }, get accepted() { return i18n.t("console:accepted"); }, get concluded() { return i18n.t("console:ended"); },
};

export function rowStateInfo(row: TimelineRow): { label: string; tone: "green" | "amber" | "red" | "neutral"; glyph: string; glyphClass: string } {
  const key = row.state || row.status;
  const style = ROW_STATE_STYLE[key];
  return {
    label: key === "awaiting-host" && row.rootLeadRunId ? i18n.t("console:waitingForLead") : STATE_LABELS[key] || key || i18n.t("console:unknown"),
    tone: style?.tone ?? "neutral",
    glyph: style?.glyph ?? "■",
    glyphClass: style?.glyphClass ?? "muted",
  };
}

/** `title` is always the intent under 0.15; the nullable `summary` is a separate result line. */
export const TITLE_SOURCE_LABEL: Record<string, string> = {
  get "macro-task"() { return i18n.t("console:macroTaskTitle"); }, get title() { return i18n.t("console:hostTitle"); }, get task() { return i18n.t("console:originalTaskFirstLine"); }, get none() { return i18n.t("console:unnamedMicroTask"); },
};

/* ---- one-line title presentation (0.15.1 U3) ---- */

/** `titleSource: task` labels cap at roughly this many characters on one line. */
export const TASK_TITLE_CHARS = 40;
/** Annotation shown wherever a task-first-line title is displayed. */
export const TASK_SOURCE_NOTE = () => i18n.t("console:taskFirstLine");

export type TitleLine = {
  /** The text lists, labels and cards show; single line. */
  text: string;
  /** The complete recorded title for tooltips (Host-revised 0.16 design). */
  fullText: string;
  /** True when the recorded title was longer than the display cap. */
  clipped: boolean;
  /** True for `titleSource: "task"`; callers add the 取自任务首行 note. */
  fromTask: boolean;
};

/**
 * One-line title for lists, timeline labels, chronology and cards. A task
 * first-line fallback is capped at ~40 characters so a whole task paragraph
 * never masquerades as a title; explicit macroTask/Host titles keep their
 * recorded bounded text. Display stays short while the tooltip carries the
 * complete first line (`fullText`) plus the pointer to the detail view.
 */
export function displayTitle(titleSource: string, title: string): TitleLine {
  const fromTask = titleSource === "task";
  const text = fromTask ? excerpt(title, TASK_TITLE_CHARS) : title;
  return { text, fullText: title, clipped: text !== title, fromTask };
}

/** The shared one-line-title tooltip: the complete text, plus the detail pointer for a task source. */
export function titleLineTooltip(line: TitleLine): string {
  return line.fromTask ? line.fullText : line.fullText;
}

/* ---- buddy identity, naming and colour assignment ---- */

export function buddyKey(buddy: TimelineBuddy | null | undefined): string | null {
  if (!buddy) return null;
  return JSON.stringify([buddy.harness ?? "", buddy.provider ?? "", buddy.model ?? ""]);
}

/** Product names for known native model ids (0.16 0.3); unknown ids stay as is. */
const KNOWN_MODEL_NAMES: Record<string, string> = {
  "claude-opus-5-5": "Claude Opus 5.5",
  "claude-sonnet-5": "Claude Sonnet 5",
  "glm-5.3": "GLM-5.3",
};

/** A snapshot buddy reduced to the fields friendly naming needs. */
export type FriendlyBuddy = { harness: string; provider: string; model: string; label?: string; effort?: string };

/**
 * Builds the friendly-name resolver: a snapshot buddy whose harness,
 * provider and model all match wins with its buddyName; then the fixed
 * known-id map; then the raw model id. Colour assignment keeps using
 * buddyKey, so renaming never changes an assigned colour.
 */
export function buddyNamer(buddies?: readonly FriendlyBuddy[] | null): (buddy: TimelineBuddy | null | undefined) => { text: string; title: string } {
  const byTuple = new Map<string, FriendlyBuddy>();
  for (const buddy of buddies ?? []) {
    const key = JSON.stringify([buddy.harness ?? "", buddy.provider ?? "", buddy.model ?? ""]);
    if (!byTuple.has(key)) byTuple.set(key, buddy);
  }
  return (buddy) => {
    if (!buddy) return { text: i18n.t("console:buddyNotRecorded"), title: i18n.t("console:buddyNotRecorded") };
    const raw = [buddy.harness, buddy.provider, buddy.model, buddy.effort]
      .map(part => (part ?? "").trim()).filter(Boolean).join(" / ");
    const matched = byTuple.get(JSON.stringify([buddy.harness ?? "", buddy.provider ?? "", buddy.model ?? ""]));
    const name = matched
      ? buddyDisplayName(matched)
      : KNOWN_MODEL_NAMES[(buddy.model ?? "").trim()] ?? (buddy.model ?? "").trim();
    const effort = effortDisplay(buddy.effort);
    const text = name && effort ? `${name} · ${effort}` : name || effort || i18n.t("console:buddyNotRecorded");
    return { text, title: raw || text };
  };
}

/** buddyName() behaviour for a snapshot buddy (label minus its effort tail). */
function buddyDisplayName(buddy: FriendlyBuddy): string {
  const label = (buddy.label ?? "").trim();
  const effort = (buddy.effort ?? "").trim();
  if (label && effort) {
    const suffix = ` · ${effort}`;
    if (label.endsWith(suffix)) {
      const stripped = label.slice(0, -suffix.length).trim();
      if (stripped) return stripped;
    }
  }
  return label || buddy.model;
}

/** effortText() behaviour without importing the buddy module. */
function effortDisplay(effort: string | null | undefined): string {
  // Native values keep their recorded name, `off` included.
  return (effort ?? "").trim();
}

const defaultNamer = buddyNamer(null);

/**
 * One consistent “友好名 · effort” form for the legend, row subtitles, the
 * inspector and details; the raw harness/provider/model/effort belongs in the
 * tooltip (0.16 0.3).
 */
export function buddyLabel(buddy: TimelineBuddy | null | undefined, buddies?: readonly FriendlyBuddy[] | null): string {
  return (buddies ? buddyNamer(buddies) : defaultNamer)(buddy).text;
}

/** The raw identity line for tooltips. */
export function buddyRawLabel(buddy: TimelineBuddy | null | undefined): string {
  return defaultNamer(buddy).title;
}

export type BuddyStyle = { key: string; label: string; rawTitle: string; color: number; striped: boolean };

/**
 * Stable execution-buddy colours. `assignments` maps a buddy
 * key to the slot it received when first seen for this macroTask and persists
 * across refreshes, so a newly appearing buddy — even in an earlier
 * row — never recolours an existing one; the palette lists the buddies
 * present in the current read by assignment order. Beyond six slots, colours
 * cycle and gain a stripe on both the legend and the execution bars.
 */
export function buddyPalette(
  spansByRun: Map<string, TimelineSpan[]>,
  assignments?: Map<string, number>,
  buddies?: readonly FriendlyBuddy[] | null,
): BuddyStyle[] {
  const slots = assignments ?? new Map<string, number>();
  const byKey = new Map<string, BuddyStyle>();
  const namer = buddyNamer(buddies);
  for (const spans of spansByRun.values()) {
    for (const span of spans) {
      if (span.kind !== "execution") continue;
      const key = buddyKey(span.buddy);
      if (key === null || byKey.has(key)) continue;
      if (!slots.has(key)) slots.set(key, slots.size);
      const slot = slots.get(key)!;
      const named = namer(span.buddy);
      byKey.set(key, { key, label: named.text, rawTitle: named.title, color: slot % 6 + 1, striped: slot >= 6 });
    }
  }
  return [...byKey.values()].sort((left, right) => (slots.get(left.key) ?? 0) - (slots.get(right.key) ?? 0));
}

export function paletteIndex(palette: BuddyStyle[], buddy: TimelineBuddy | null | undefined): BuddyStyle | null {
  const key = buddyKey(buddy);
  return key === null ? null : palette.find(entry => entry.key === key) ?? null;
}

/* ---- time and duration formatting (display only) ---- */

function format(value: string | number | null | undefined, options: Intl.DateTimeFormatOptions): string {
  if (value === null || value === undefined || value === "") return i18n.t("console:notRecorded");
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? i18n.t("console:notRecorded") : new Intl.DateTimeFormat(locale, options).format(date);
}

export function clockTime(value: string | number | null | undefined): string {
  return format(value, { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function clockSeconds(value: string | number | null | undefined): string {
  return format(value, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function dayClock(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return i18n.t("console:notRecorded");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return i18n.t("console:notRecorded");
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function relativeTime(value: string | null | undefined, nowMs: number): string {
  if (!value) return i18n.t("console:notRecorded");
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return i18n.t("console:notRecorded");
  const seconds = Math.max(0, Math.round((nowMs - at) / 1000));
  if (seconds < 45) return i18n.t("console:justNow");
  if (seconds < 3600) return i18n.t("console:minAgo", { v0: Math.round(seconds / 60) });
  if (seconds < 86400) return i18n.t("console:hrAgo", { v0: Math.round(seconds / 3600) });
  if (seconds < 7 * 86400) return i18n.t("console:daysAgo", { v0: Math.round(seconds / 86400) });
  return dayClock(at);
}

export function durationText(ms: number | null): string | null {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return null;
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return i18n.t("console:lessThan1Min");
  if (minutes < 60) return i18n.t("console:min", { v0: minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? i18n.t("console:hrMin", { v0: hours, v1: rest }) : i18n.t("console:hr", { v0: hours });
}

export function durationShort(ms: number): string {
  const minutes = Math.round(ms / 60000);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return i18n.t("console:compactDuration", { v0: hours ? i18n.t("console:hoursPrefix", { v0: hours }) : "", v1: rest });
}

/** Placement uses the layout module's strict parser; display formatting may stay permissive. */
export function toMs(value: string | number | null | undefined): number | null {
  return parseTimelineInstant(value);
}

export function rangeText(startMs: number | null, endMs: number | null): string {
  if (startMs === null) return i18n.t("console:timeNotRecorded");
  if (endMs === null) return i18n.t("console:toNow", { v0: clockTime(startMs) });
  const sameDay = new Date(startMs).toDateString() === new Date(endMs).toDateString();
  const start = sameDay ? clockTime(startMs) : dayClock(startMs);
  const end = sameDay ? clockTime(endMs) : dayClock(endMs);
  return i18n.t("console:to", { v0: start, v1: end });
}

/* ---- item facts shared by the canvas, the list and the inspector ---- */

export type TimelineItem = {
  key: string;
  /** Micro task whose existing detail opens on Enter/double click. */
  runId: string;
  /** Exact recorded routing decision to locate in the micro task's routing tab. */
  decisionId?: string;
  ariaLabel?: string;
  section: SectionId;
  /** Inspector bold head. */
  head: string;
  /** Inspector detail parts. */
  parts: string[];
  /** Short "来自时间轴：" line for the detail locator. */
  locator: string;
  /** Navigation anchor instant; null puts row labels leftmost. */
  atMs: number | null;
  /** Host markers draw a focus guide line. */
  guide?: boolean;
};

function spanPlacement(span: TimelineSpan, observedAtMs: number | null): {
  startMs: number | null; recordedEndMs: number | null; endMs: number | null;
} {
  const startMs = toMs(span.startAt);
  const recordedEndMs = toMs(span.endAt);
  const outcome = spanOutcome(span);
  const openTail = outcome === "running" || outcome === "open" || outcome === "unknown";
  let endMs: number | null = recordedEndMs;
  if (outcome === "unknown" && recordedEndMs !== null) {
    // The Host layout reserves the unknown tail through observedAt; only a
    // nonsensical observation before the recorded end keeps that end.
    endMs = observedAtMs !== null && observedAtMs >= recordedEndMs ? observedAtMs : recordedEndMs;
  } else if (recordedEndMs === null && openTail && observedAtMs !== null) {
    endMs = observedAtMs;
  }
  return { startMs, recordedEndMs, endMs };
}

export function spanFacts(
  span: TimelineSpan,
  row: TimelineRow,
  observedAtMs: number | null,
  buddies?: readonly FriendlyBuddy[] | null,
): { item: TimelineItem; startMs: number | null; endMs: number | null; recordedEndMs: number | null; outcome: SpanOutcome } {
  const outcome = spanOutcome(span);
  const { startMs, recordedEndMs, endMs } = spanPlacement(span, observedAtMs);
  const head = span.kind === "host" && row.rootLeadRunId ? i18n.t("console:waitingForLead") : spanHead(span.kind);
  const parts: string[] = [displayTitle(row.titleSource, row.title).text];
  if (span.kind === "execution" || span.kind === "routing") {
    if (span.turnIndex !== null && span.turnIndex !== undefined) parts.push(i18n.t("console:turnNumber", { v0: span.turnIndex }));
  }
  if (span.kind === "execution" && span.buddy) parts.push(buddyLabel(span.buddy));
  if (span.kind === "host" && span.summary) parts.push(span.summary);
  // A terminal span without a recorded end never borrows "now": it says the
  // end time is missing, exactly like a missing start.
  parts.push(startMs === null ? i18n.t("console:timeNotRecorded") : endMs === null ? i18n.t("console:toMissingEndTime", { v0: clockTime(startMs) }) : rangeText(startMs, endMs));
  const duration = durationText(endMs !== null && startMs !== null ? endMs - startMs : null);
  if (duration) parts.push(duration);
  parts.push(outcomeLabel(span, outcome));
  if (typeof span.error === "string" && span.error.trim()) parts.push(span.error.trim());
  // The detail locator states only the recorded fact — round and time — never
  // the micro task title, which the detail already shows once (0.16 T4).
  const roundPrefix = span.turnIndex != null ? i18n.t("console:turnNumber", { v0: span.turnIndex }) : spanHead(span.kind);
  const timeFact = startMs === null ? i18n.t("console:timeNotRecorded")
    : endMs === null ? i18n.t("console:sinceEndTimeMissing", { v0: clockTime(startMs) })
      : `${clockTime(startMs)}–${clockTime(endMs)}`;
  const locator = span.kind === "host"
    ? (startMs === null ? i18n.t("console:itemTimeNotRecorded", { v0: head })
      : endMs === null ? i18n.t("console:itemSince", { v0: head, v1: clockTime(startMs) }) : `${head} ${clockTime(startMs)}–${clockTime(endMs)}`)
    : span.kind === "queue"
      ? (startMs === null ? i18n.t("console:queueTimeNotRecorded") : endMs === null ? i18n.t("console:queuedSince", { v0: clockTime(startMs) }) : i18n.t("console:queueRange", { v0: clockTime(startMs), v1: clockTime(endMs) }))
      : `${roundPrefix} ${timeFact}`;
  return {
    item: {
      key: `span:${span.spanId}`,
      runId: row.runId,
      ...(span.kind === "routing" ? { decisionId: span.decisionId || undefined, ariaLabel: routingSpanLabel(span, buddies) } : {}),
      section: SPAN_SECTION[span.kind],
      head,
      parts,
      locator,
      atMs: startMs,
    },
    startMs,
    endMs,
    recordedEndMs,
    outcome,
  };
}

export function rowLabelItem(row: TimelineRow): TimelineItem {
  const state = rowStateInfo(row);
  const level = row.depth + 1;
  const hierarchy = row.kind === "helper" ? i18n.t("console:levelHelper", { v0: level }) : "";
  const title = displayTitle(row.titleSource, row.title);
  return {
    key: `row:${row.runId}`,
    runId: row.runId,
    section: "overview",
    head: i18n.t("console:microTask"),
    parts: [title.text, `${hierarchy}${state.label}`, i18n.t("console:titleSource", { v0: TITLE_SOURCE_LABEL[row.titleSource] || row.titleSource })],
    locator: "",
    atMs: null,
  };
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
  const atMs = toMs(settledAt);
  if (atMs === null) return null;
  const executions = spans
    .filter(span => span.kind === "execution")
    .map(span => ({ span, endMs: toMs(span.endAt) }));
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

/** The acceptance-wait sentence shared by the dashed line and the inspector. */
export function acceptanceWaitText(row: TimelineRow, spans: readonly TimelineSpan[]): string | null {
  const wait = acceptanceWaitMs(row, spans);
  if (wait === null) return null;
  return !row.acceptedAt && row.reviewRejectedAt
    ? i18n.t("console:waitingForAcceptanceDecision", { v0: durationText(wait) ?? "" }).trim()
    : i18n.t("console:waitingForAcceptance", { v0: durationText(wait) ?? "" }).trim();
}

export function settleItem(row: TimelineRow): TimelineItem | null {
  const settledAt = row.acceptedAt ?? row.reviewRejectedAt;
  if (!settledAt) return null;
  const rejected = !row.acceptedAt && !!row.reviewRejectedAt;
  const at = toMs(settledAt);
  return {
    key: `settle:${row.runId}`,
    runId: row.runId,
    section: "artifacts",
    head: rejected ? i18n.t("console:acceptanceIssue") : i18n.t("console:accepted"),
    parts: [rejected ? i18n.t("console:acceptanceIssue") : i18n.t("console:accepted"), row.title, dayClock(settledAt)],
    locator: i18n.t("console:joined", { v0: rejected ? i18n.t("console:acceptanceIssue") : i18n.t("console:accepted"), v1: clockTime(settledAt) }),
    atMs: at,
  };
}

export function eventItem(event: TimelineEvent, rowsById: Map<string, TimelineRow>): TimelineItem {
  const vocab = eventVocab(event.kind);
  const row = rowsById.get(event.runId);
  const title = row ? displayTitle(row.titleSource, row.title).text : i18n.t("console:microTaskWithId", { v0: event.runId });
  return {
    key: `event:${event.seq}`,
    runId: event.runId,
    section: eventSection(event.kind),
    head: i18n.t("console:hostEventHeading", { v0: event.label || vocab.label }),
    parts: [title, dayClock(event.at), ...(event.summary ? [event.summary] : [])],
    locator: `Host ${event.label || vocab.label} ${clockTime(event.at)}`,
    atMs: toMs(event.at),
    guide: true,
  };
}

/* ---- chronological list flattening ---- */

export type ChronoEntry =
  | { kind: "span"; atMs: number; item: TimelineItem; span: TimelineSpan; row: TimelineRow; outcome: SpanOutcome; endMs: number | null }
  | { kind: "event"; atMs: number; item: TimelineItem; event: TimelineEvent; row: TimelineRow | null }
  | { kind: "gap"; startMs: number; endMs: number };

/**
 * Spans and Host events in one start-time-ordered column. Idle separators
 * appear only for gaps the frozen layout itself considers trustworthy
 * (`canFold`); an incomplete or filtered scope never claims idle time.
 */
/** Entries that carry a recorded instant; idle separators are added later. */
export type TimedEntry = Exclude<ChronoEntry, { kind: "gap" }>;

export function flattenChronology(
  timeline: MacroTaskTimeline,
  factsBySpan: Map<string, { item: TimelineItem; endMs: number | null; outcome: SpanOutcome }>,
): TimedEntry[] {
  const rowsById = new Map(timeline.rows.map(row => [row.runId, row]));
  const entries: TimedEntry[] = [];
  for (const row of timeline.rows) {
    for (const span of timeline.spans.filter(candidate => candidate.runId === row.runId)) {
      const facts = factsBySpan.get(span.spanId);
      if (!facts || facts.item.atMs === null) continue;
      entries.push({ kind: "span", atMs: facts.item.atMs, item: facts.item, span, row, outcome: facts.outcome, endMs: facts.endMs });
    }
  }
  for (const event of timeline.events) {
    const atMs = toMs(event.at);
    if (atMs === null) continue;
    entries.push({ kind: "event", atMs, item: eventItem(event, rowsById), event, row: rowsById.get(event.runId) ?? null });
  }
  entries.sort((left, right) => left.atMs - right.atMs || (left.kind === "span" ? -1 : 1) - (right.kind === "span" ? -1 : 1));
  return entries;
}

/** Full chronology including idle separators derived from the layout's gaps. */
export function buildChronology(
  timeline: MacroTaskTimeline,
  factsBySpan: Map<string, { item: TimelineItem; endMs: number | null; outcome: SpanOutcome }>,
  gaps: readonly TimelineGap[],
  canFold: boolean,
): ChronoEntry[] {
  const entries: TimedEntry[] = flattenChronology(timeline, factsBySpan);
  if (!canFold) return entries;
  const eligible = gaps.filter(gap => gap.endMs - gap.startMs > TIMELINE_FOLD_THRESHOLD_MS);
  const result: ChronoEntry[] = [];
  let gapIndex = 0;
  for (const entry of entries) {
    while (gapIndex < eligible.length && eligible[gapIndex]!.endMs <= entry.atMs) {
      const gap = eligible[gapIndex]!;
      result.push({ kind: "gap", startMs: gap.startMs, endMs: gap.endMs });
      gapIndex += 1;
    }
    result.push(entry);
  }
  while (gapIndex < eligible.length) {
    const gap = eligible[gapIndex]!;
    result.push({ kind: "gap", startMs: gap.startMs, endMs: gap.endMs });
    gapIndex += 1;
  }
  return result;
}

/** Describe the selected target, never the Router's own configuration. */
export function routingSpanLabel(span: TimelineSpan, buddies?: readonly FriendlyBuddy[] | null): string {
  const selected = span.routing?.selectedBuddy;
  return i18n.t("console:routingSelected", { v0: recordedRoutingMode(span.routing?.routingMode), v1: selected ? buddyLabel(selected, buddies) : i18n.t("console:notRecorded"), v2: outcomeLabel(span, spanOutcome(span)) });
}
