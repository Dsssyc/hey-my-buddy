/**
 * Pure display vocabulary for the work-objective timeline. Everything here is
 * derived from recorded facts only: no cost or token claims, no invented
 * durations, and 结束未确认 is never presented as stopped. Positioning and
 * folding stay in the frozen objective-timeline-layout module; this file only
 * names things, assigns configuration colours and flattens the chronological
 * list view.
 */

import type {
  ObjectiveCategory,
  ObjectiveSummary,
  ObjectiveTimeline,
  TimelineConfiguration,
  TimelineEvent,
  TimelineRow,
  TimelineSpan,
} from "./objective-types";
import { TIMELINE_FOLD_THRESHOLD_MS, parseTimelineInstant } from "./objective-timeline-layout";
import type { TimelineGap } from "./objective-timeline-layout";
import { excerpt } from "./task-state";

export type SectionId = "overview" | "routing" | "assistance" | "artifacts" | "execution";

/** Recorded execution states that keep a null-ended span visually open. */
const OPEN_EXECUTION_STATES = new Set(["starting", "executing", "finalizing", "running", "active"]);

export type SpanOutcome =
  | "open" | "claimed" | "resolved" | "running" | "failed"
  | "cancelled" | "unknown" | "finished" | "unplaced";

export function spanOutcome(span: TimelineSpan): SpanOutcome {
  const state = typeof span.state === "string" ? span.state.trim().toLowerCase() : "";
  if (span.kind === "queue") return state === "queued" && span.endAt == null ? "open" : "claimed";
  if (span.kind === "host") return state === "open" && span.endAt == null ? "open" : "resolved";
  // Unknown shutdown outranks a recorded failure disposition: an unconfirmed
  // stop is never labelled plain failed/cancelled, though the failure text stays.
  if (span.uncertain === true || state === "uncertain") return "unknown";
  if (span.resultStatus === "cancelled") return "cancelled";
  if (span.resultStatus === "failed") return "failed";
  if (span.resultStatus !== "ok" && typeof span.error === "string" && span.error.trim()) return "failed";
  if (span.endAt == null) {
    return OPEN_EXECUTION_STATES.has(state) && span.shutdownConfirmed !== true ? "running" : "unplaced";
  }
  return "finished";
}

export const OUTCOME_LABEL: Record<SpanOutcome, string> = {
  open: "仍在等待", claimed: "已认领", resolved: "已决定", running: "执行中",
  failed: "执行失败", cancelled: "已取消", unknown: "结束未确认", finished: "执行完成",
  unplaced: "时间缺失",
};

/** Host-request decision labels use the recorded request state, not a guess. */
const HOST_STATE_LABEL: Record<string, string> = {
  approved: "已批准", declined: "已拒绝", superseded: "已被取代", cancelled: "已取消",
};

export function outcomeLabel(span: TimelineSpan, outcome: SpanOutcome): string {
  if (span.kind === "queue") return outcome === "open" ? "仍在排队" : "已认领";
  if (span.kind === "host" && outcome === "resolved") {
    const state = typeof span.state === "string" ? span.state.trim().toLowerCase() : "";
    return HOST_STATE_LABEL[state] || span.state || "已决定";
  }
  return OUTCOME_LABEL[outcome];
}

export function spanHead(kind: TimelineSpan["kind"]): string {
  return kind === "queue" ? "排队" : kind === "routing" ? "路由" : kind === "host" ? "等待 Host" : "执行片段";
}

export const SPAN_SECTION: Record<TimelineSpan["kind"], SectionId> = {
  queue: "overview", routing: "routing", execution: "execution", host: "assistance",
};

const EVENT_VOCAB: Record<string, { label: string; glyph: string }> = {
  dispatch: { label: "派发", glyph: "▶" }, decide: { label: "决定", glyph: "◆" },
  continue: { label: "续接", glyph: "↻" }, integrate: { label: "整合", glyph: "⊕" },
  accept: { label: "验收", glyph: "✓" }, reject: { label: "验收问题", glyph: "!" },
  cancel: { label: "取消", glyph: "✕" }, takeover: { label: "接管", glyph: "⇄" },
};

export function eventVocab(kind: string): { label: string; glyph: string } {
  return EVENT_VOCAB[kind] ?? { label: kind, glyph: "•" };
}

const EVENT_SECTION: Record<string, SectionId> = {
  dispatch: "overview", decide: "assistance", continue: "execution", integrate: "artifacts",
  accept: "artifacts", reject: "artifacts", cancel: "overview", takeover: "overview",
};

export function eventSection(kind: string): SectionId {
  return EVENT_SECTION[kind] ?? "overview";
}

/** Timeline categories are disjoint and already priority-ordered by the service. */
export const CATEGORY_LABEL: Record<ObjectiveCategory, string> = {
  host: "待决定", active: "进行中", review: "待验收", ended: "已结束",
};

export function categoryTone(category: ObjectiveCategory): "green" | "amber" | "neutral" {
  return category === "host" ? "amber" : category === "active" ? "green" : "neutral";
}

/** Fixed-order count chips; zero entries are omitted by the caller. */
export const COUNT_ORDER: readonly { key: keyof ObjectiveSummary["counts"]; glyph: string; label: string }[] = [
  { key: "active", glyph: "●", label: "进行中" },
  { key: "host", glyph: "◆", label: "待决定" },
  { key: "review", glyph: "✓", label: "待验收" },
  { key: "ended", glyph: "■", label: "已结束" },
];

export function totalDelegations(counts: ObjectiveSummary["counts"]): number {
  return counts.roots + counts.helpers;
}

const ROW_STATE_STYLE: Record<string, { tone: "green" | "amber" | "red" | "neutral"; glyph: string; glyphClass: string }> = {
  executing: { tone: "green", glyph: "▸", glyphClass: "running" },
  "awaiting-host": { tone: "amber", glyph: "◆", glyphClass: "warn" },
  "waiting-helpers": { tone: "green", glyph: "▸", glyphClass: "running" },
  delivered: { tone: "neutral", glyph: "✓", glyphClass: "muted" },
  accepted: { tone: "neutral", glyph: "✓", glyphClass: "ok" },
  cancelled: { tone: "neutral", glyph: "⊘", glyphClass: "muted" },
  failed: { tone: "red", glyph: "✕", glyphClass: "bad" },
  "reconciliation-needed": { tone: "amber", glyph: "?", glyphClass: "warn" },
};

const STATE_LABELS: Record<string, string> = {
  queued: "排队中", running: "执行中", cancelling: "正在取消", completed: "执行完成",
  cancelled: "已取消", failed: "执行失败", "reconciliation-needed": "等待核对",
  executing: "执行中", "awaiting-host": "等待 Host", "waiting-helpers": "协助执行中",
  delivered: "等待验收", accepted: "已验收",
};

export function rowStateInfo(row: TimelineRow): { label: string; tone: "green" | "amber" | "red" | "neutral"; glyph: string; glyphClass: string } {
  const key = row.state || row.status;
  const style = ROW_STATE_STYLE[key];
  return {
    label: STATE_LABELS[key] || key || "未知",
    tone: style?.tone ?? "neutral",
    glyph: style?.glyph ?? "■",
    glyphClass: style?.glyphClass ?? "muted",
  };
}

/** `title` is always the intent under 0.15; the nullable `summary` is a separate result line. */
export const TITLE_SOURCE_LABEL: Record<string, string> = {
  objective: "工作目标标题", title: "Host 标题", task: "原始任务首行", none: "未命名委派",
};

/* ---- one-line title presentation (0.15.1 U3) ---- */

/** `titleSource: task` labels cap at roughly this many characters on one line. */
export const TASK_TITLE_CHARS = 40;
/** Annotation shown wherever a task-first-line title is displayed. */
export const TASK_SOURCE_NOTE = "取自任务首行";

export type TitleLine = {
  /** The text lists, labels and cards show; single line. */
  text: string;
  /** True when the recorded title was longer than the display cap. */
  clipped: boolean;
  /** True for `titleSource: "task"`; callers add the 取自任务首行 note. */
  fromTask: boolean;
};

/**
 * One-line title for lists, timeline labels, chronology and cards. A task
 * first-line fallback is capped at ~40 characters so a whole task paragraph
 * never masquerades as a title; explicit objective/Host titles keep their
 * recorded bounded text. Full task/title content is only shown in detail, so
 * callers must not put the full text into a `title` tooltip either.
 */
export function displayTitle(titleSource: string, title: string): TitleLine {
  const fromTask = titleSource === "task";
  const text = fromTask ? excerpt(title, TASK_TITLE_CHARS) : title;
  return { text, clipped: text !== title, fromTask };
}

/* ---- configuration identity and colour assignment ---- */

export function configurationKey(config: TimelineConfiguration | null | undefined): string | null {
  if (!config) return null;
  return JSON.stringify([config.adapter ?? "", config.provider ?? "", config.model ?? ""]);
}

export function configurationLabel(config: TimelineConfiguration | null | undefined): string {
  if (!config) return "配置未记录";
  const head = config.adapter || config.provider || "未记录适配器";
  const model = config.model || config.provider || "未记录模型";
  return `${head} / ${model}`;
}

export type ConfigurationStyle = { key: string; label: string; color: number; striped: boolean };

/**
 * Stable execution-configuration colours. `assignments` maps a configuration
 * key to the slot it received when first seen for this objective and persists
 * across refreshes, so a newly appearing configuration — even in an earlier
 * row — never recolours an existing one; the palette lists the configurations
 * present in the current read by assignment order. Beyond six slots, colours
 * cycle and gain a stripe on both the legend and the execution bars.
 */
export function configurationPalette(
  spansByRun: Map<string, TimelineSpan[]>,
  assignments?: Map<string, number>,
): ConfigurationStyle[] {
  const slots = assignments ?? new Map<string, number>();
  const byKey = new Map<string, ConfigurationStyle>();
  for (const spans of spansByRun.values()) {
    for (const span of spans) {
      if (span.kind !== "execution") continue;
      const key = configurationKey(span.configuration);
      if (key === null || byKey.has(key)) continue;
      if (!slots.has(key)) slots.set(key, slots.size);
      const slot = slots.get(key)!;
      byKey.set(key, { key, label: configurationLabel(span.configuration), color: slot % 6 + 1, striped: slot >= 6 });
    }
  }
  return [...byKey.values()].sort((left, right) => (slots.get(left.key) ?? 0) - (slots.get(right.key) ?? 0));
}

export function paletteIndex(palette: ConfigurationStyle[], config: TimelineConfiguration | null | undefined): ConfigurationStyle | null {
  const key = configurationKey(config);
  return key === null ? null : palette.find(entry => entry.key === key) ?? null;
}

/* ---- time and duration formatting (display only) ---- */

function format(value: string | number | null | undefined, options: Intl.DateTimeFormatOptions): string {
  if (value === null || value === undefined || value === "") return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未记录" : new Intl.DateTimeFormat("zh-CN", options).format(date);
}

export function clockTime(value: string | number | null | undefined): string {
  return format(value, { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function clockSeconds(value: string | number | null | undefined): string {
  return format(value, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function dayClock(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未记录";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function relativeTime(value: string | null | undefined, nowMs: number): string {
  if (!value) return "未记录";
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return "未记录";
  const seconds = Math.max(0, Math.round((nowMs - at) / 1000));
  if (seconds < 45) return "刚刚";
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  if (seconds < 7 * 86400) return `${Math.round(seconds / 86400)} 天前`;
  return dayClock(at);
}

export function durationText(ms: number | null): string | null {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return null;
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return "不到 1 分钟";
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

export function durationShort(ms: number): string {
  const minutes = Math.round(ms / 60000);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return `${hours ? `${hours} 时 ` : ""}${rest} 分`;
}

/** Placement uses the layout module's strict parser; display formatting may stay permissive. */
export function toMs(value: string | number | null | undefined): number | null {
  return parseTimelineInstant(value);
}

export function rangeText(startMs: number | null, endMs: number | null): string {
  if (startMs === null) return "时间未记录";
  if (endMs === null) return `${clockTime(startMs)} 至 现在`;
  const sameDay = new Date(startMs).toDateString() === new Date(endMs).toDateString();
  const start = sameDay ? clockTime(startMs) : dayClock(startMs);
  const end = sameDay ? clockTime(endMs) : dayClock(endMs);
  return `${start} 至 ${end}`;
}

/* ---- item facts shared by the canvas, the list and the inspector ---- */

export type TimelineItem = {
  key: string;
  /** Run whose existing detail opens on Enter/click (a decision task for routing spans). */
  runId: string;
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
): { item: TimelineItem; startMs: number | null; endMs: number | null; recordedEndMs: number | null; outcome: SpanOutcome } {
  const outcome = spanOutcome(span);
  const { startMs, recordedEndMs, endMs } = spanPlacement(span, observedAtMs);
  const head = spanHead(span.kind);
  const parts: string[] = [displayTitle(row.titleSource, row.title).text];
  if (span.kind === "execution" || span.kind === "routing") {
    if (span.turnIndex !== null && span.turnIndex !== undefined) parts.push(`第 ${span.turnIndex} 轮`);
    if (span.kind === "routing" && span.decisionTaskId) parts.push(`内部路由计算 ${span.decisionTaskId}`);
  }
  if (span.kind === "execution" && span.configuration) parts.push(configurationLabel(span.configuration));
  if (span.kind === "host" && span.summary) parts.push(span.summary);
  // A terminal span without a recorded end never borrows "now": it says the
  // end time is missing, exactly like a missing start.
  parts.push(startMs === null ? "时间未记录" : endMs === null ? `${clockTime(startMs)} 至 结束时间缺失` : rangeText(startMs, endMs));
  const duration = durationText(endMs !== null && startMs !== null ? endMs - startMs : null);
  if (duration) parts.push(duration);
  parts.push(outcomeLabel(span, outcome));
  if (typeof span.error === "string" && span.error.trim()) parts.push(span.error.trim());
  const targetRun = span.kind === "routing" && span.decisionTaskId ? span.decisionTaskId : row.runId;
  const endText = endMs === null ? "结束时间缺失" : clockTime(endMs);
  const locatorBase = startMs === null ? head : `${head} ${clockTime(startMs)}–${endText}`;
  const locator = span.kind === "execution" && span.turnIndex != null
    ? `第 ${span.turnIndex} 轮 · 执行片段 ${startMs === null ? "?" : clockTime(startMs)}–${endText} · ${configurationLabel(span.configuration)}`
    : locatorBase;
  return {
    item: {
      key: `span:${span.spanId}`,
      runId: targetRun,
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
  const hierarchy = row.kind === "helper" ? `层级 ${level}，协助任务，` : "";
  const title = displayTitle(row.titleSource, row.title);
  return {
    key: `row:${row.runId}`,
    runId: row.runId,
    section: "overview",
    head: "委派",
    parts: [title.text, `${hierarchy}${state.label}`, `标题来源：${TITLE_SOURCE_LABEL[row.titleSource] || row.titleSource}`],
    locator: `委派 · ${title.text}`,
    atMs: null,
  };
}

export function settleItem(row: TimelineRow): TimelineItem | null {
  if (!row.acceptedAt) return null;
  const rejected = row.acceptanceVerdict === "rejected";
  const at = toMs(row.acceptedAt);
  return {
    key: `settle:${row.runId}`,
    runId: row.runId,
    section: "artifacts",
    head: "结算",
    parts: [rejected ? "验收问题" : "已验收", row.title, dayClock(row.acceptedAt)],
    locator: `结算 ${rejected ? "验收问题" : "已验收"} · ${clockTime(row.acceptedAt)}`,
    atMs: at,
  };
}

export function eventItem(event: TimelineEvent, rowsById: Map<string, TimelineRow>): TimelineItem {
  const vocab = eventVocab(event.kind);
  const row = rowsById.get(event.runId);
  const title = row ? displayTitle(row.titleSource, row.title).text : `委派 ${event.runId}`;
  return {
    key: `event:${event.seq}`,
    runId: event.runId,
    section: eventSection(event.kind),
    head: `Host 事件：${event.label || vocab.label}`,
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
  timeline: ObjectiveTimeline,
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
  timeline: ObjectiveTimeline,
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
