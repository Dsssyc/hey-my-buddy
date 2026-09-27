/**
 * Pure derived metrics for the work-objective overview (0.15 C5/C6).
 *
 * Everything is computed from the recorded spans, events and summary of one
 * bounded timeline read: no extrapolation, no idle deduction, and no "≥"
 * prefixes. Wall-clock span, the union of confirmed execution intervals and
 * the cumulative sum are three different numbers; queue, routing and
 * waiting-Host time never counts as execution. An unknown end is excluded
 * from the confirmed numbers and reported as its own count; reversed or
 * clock-skewed timestamps are corrupt and feed no number at all, not even
 * the total span; a legitimately open span (running execution, queued task,
 * open Host request) is live, not a missing-time error.
 */

import type { ObjectiveTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import { parseTimelineInstant } from "./objective-timeline-layout";
import { spanOutcome } from "./objective-display";

export type MetricInterval = { startMs: number; endMs: number };

/** Why the displayed numbers describe only the recorded part of the scope. */
export type MetricLimitation = {
  /** Machine-stable key so tests and the truncation banner can share wording. */
  key: "scope" | "rows" | "spans" | "events" | "missing-time" | "reversed" | "clock-skew" | "unconfirmed-end" | "running";
  label: string;
};

export const LIMITATION_LABEL: Record<MetricLimitation["key"], string> = {
  scope: "读取范围不完整（筛选或截断）",
  rows: "委派行已截断",
  spans: "执行片段已截断",
  events: "Host 事件已截断",
  "missing-time": "有片段缺少可用时间",
  reversed: "有片段时间颠倒",
  "clock-skew": "有片段存在时钟偏差（clockSkew）",
  "unconfirmed-end": "有片段结束未确认，未计入执行时长",
  running: "有片段仍在进行，计至读取时刻",
};

/** Scope-level limitation keys: the read itself cannot prove absence. */
const SCOPE_KEYS: ReadonlySet<MetricLimitation["key"]> = new Set(["scope", "rows", "spans", "events"]);

export function hasScopeLimitations(limitations: readonly MetricLimitation["key"][]): boolean {
  return limitations.some(key => SCOPE_KEYS.has(key));
}

export type ObjectiveMetrics = {
  /** Earliest usable start (objective creation when no span has one) to the latest recorded instant. */
  totalSpanMs: number | null;
  /** Union of execution intervals that are actually confirmable. */
  unionMs: number | null;
  /** Sum of the same execution intervals; differs from the union under concurrency. */
  sumMs: number | null;
  /** Confirmed execution intervals in chronological order (already unioned). */
  intervals: MetricInterval[];
  /** How many execution segments contributed to the union. */
  segmentCount: number;
  /** Running segments counted only through observedAt ("至今"). */
  runningCount: number;
  /** Unconfirmed-end execution segments, excluded from union/sum and reported separately. */
  unknownEndCount: number;
  /** Recorded-part notes, shared with the truncation banner wording. */
  limitations: MetricLimitation[];
};

function limitations(keys: MetricLimitation["key"][]): MetricLimitation[] {
  const seen = new Set<MetricLimitation["key"]>();
  const result: MetricLimitation[] = [];
  for (const key of keys) {
    if (seen.has(key)) continue;
    seen.add(key);
    result.push({ key, label: LIMITATION_LABEL[key] });
  }
  return result;
}

function mergeUnion(intervals: readonly MetricInterval[]): MetricInterval[] {
  const sorted = [...intervals].sort((left, right) => left.startMs - right.startMs || left.endMs - right.endMs);
  const merged: MetricInterval[] = [];
  for (const interval of sorted) {
    const last = merged[merged.length - 1];
    if (last !== undefined && interval.startMs <= last.endMs) {
      if (interval.endMs > last.endMs) last.endMs = interval.endMs;
      continue;
    }
    merged.push({ ...interval });
  }
  return merged;
}

function unionLength(intervals: readonly MetricInterval[]): number {
  return intervals.reduce((total, interval) => total + (interval.endMs - interval.startMs), 0);
}

/**
 * One execution span's confirmable interval, or why it has none. A running
 * span counts through observedAt with a note; an unconfirmed end is not
 * proven execution and is excluded entirely, reported by count only.
 */
function executionInterval(span: TimelineSpan, observedAtMs: number | null): {
  interval: MetricInterval | null;
  limitation: MetricLimitation["key"] | null;
} {
  if (span.kind !== "execution") return { interval: null, limitation: null };
  if (span.clockSkew === true) return { interval: null, limitation: "clock-skew" };
  const startMs = parseTimelineInstant(span.startAt);
  const endMs = parseTimelineInstant(span.endAt);
  if (startMs === null) return { interval: null, limitation: "missing-time" };
  const outcome = spanOutcome(span);
  // A live execution counts through the observation instant, with a note.
  if (outcome === "running") {
    if (observedAtMs === null || observedAtMs < startMs) return { interval: null, limitation: "missing-time" };
    return { interval: { startMs, endMs: observedAtMs }, limitation: "running" };
  }
  if (endMs === null) return { interval: null, limitation: "missing-time" };
  if (endMs < startMs) return { interval: null, limitation: "reversed" };
  if (outcome === "unknown") return { interval: null, limitation: "unconfirmed-end" };
  return { interval: { startMs, endMs }, limitation: null };
}

/**
 * The three distinct duration numbers of one timeline read. Total span is
 * wall-clock from the earliest recorded start (objective creation when no span
 * has one) to the latest recorded instant — observedAt when anything is still
 * running or unconfirmed. Execution uses only `execution` spans: queue,
 * routing and waiting-Host never contribute. Corrupt timestamps (reversed or
 * clock-skewed) feed no number, so they can never manufacture duration.
 */
export function objectiveMetrics(timeline: ObjectiveTimeline): ObjectiveMetrics {
  const observedAtMs = parseTimelineInstant(timeline.observedAt);
  const starts: number[] = [];
  const latestInstants: number[] = [];
  const intervals: MetricInterval[] = [];
  const limitationKeys: MetricLimitation["key"][] = [];
  let segmentCount = 0;
  let runningCount = 0;
  let unknownEndCount = 0;
  let anyOpenTail = false;

  for (const span of timeline.spans) {
    const outcome = spanOutcome(span);
    if (span.kind === "execution" && outcome === "unknown") {
      unknownEndCount += 1;
      limitationKeys.push("unconfirmed-end");
    }
    // Corrupt endpoints never feed any number, including the total span.
    if (span.clockSkew === true) {
      limitationKeys.push("clock-skew");
      continue;
    }
    const startMs = parseTimelineInstant(span.startAt);
    const endMs = parseTimelineInstant(span.endAt);
    if (startMs !== null && endMs !== null && endMs < startMs) {
      limitationKeys.push("reversed");
      continue;
    }
    if (startMs !== null) starts.push(startMs);
    if (endMs !== null) latestInstants.push(endMs);
    // A legitimately open span (running execution, queued task, open Host
    // request) is live, not a timing error; only a missing start, or a
    // missing end on a span that claims to be finished, is unusable time.
    if (startMs === null || (endMs === null && outcome !== "running" && outcome !== "open")) {
      limitationKeys.push("missing-time");
    }
    if (outcome === "running" || outcome === "open" || outcome === "unknown") anyOpenTail = true;

    const reading = executionInterval(span, observedAtMs);
    if (reading.interval !== null) {
      intervals.push(reading.interval);
      segmentCount += 1;
      if (reading.limitation === "running") runningCount += 1;
    } else if (reading.limitation !== null) {
      limitationKeys.push(reading.limitation);
    }
  }
  for (const event of timeline.events) {
    const atMs = parseTimelineInstant(event.at);
    if (atMs !== null) latestInstants.push(atMs);
  }
  if (anyOpenTail && observedAtMs !== null) latestInstants.push(observedAtMs);
  if (timeline.scopeComplete !== true) limitationKeys.push("scope");
  if (timeline.truncated.rows) limitationKeys.push("rows");
  if (timeline.truncated.spans) limitationKeys.push("spans");
  if (timeline.truncated.events) limitationKeys.push("events");

  const earliest = starts.length ? Math.min(...starts) : parseTimelineInstant(timeline.objective.createdAt);
  const latest = latestInstants.length ? Math.max(...latestInstants) : null;
  const merged = mergeUnion(intervals);
  return {
    totalSpanMs: earliest !== null && latest !== null && latest >= earliest ? latest - earliest : null,
    unionMs: merged.length ? unionLength(merged) : null,
    sumMs: intervals.length ? intervals.reduce((total, interval) => total + (interval.endMs - interval.startMs), 0) : null,
    intervals: merged,
    segmentCount,
    runningCount,
    unknownEndCount,
    limitations: limitations(limitationKeys),
  };
}

/* ---- related Host events: identity match only, never time proximity ---- */

function sameKey(left: string | null | undefined, right: string | null | undefined): boolean {
  // null, undefined and the empty string are not keys: they never match.
  const a = left ?? null;
  const b = right ?? null;
  return a !== null && b !== null && a !== "" && b !== "" && a === b;
}

/** Events related to one span: same run plus a shared non-empty attempt or request identity. */
export function relatedEventsForSpan(span: TimelineSpan, events: readonly TimelineEvent[]): TimelineEvent[] {
  return events.filter(event => event.runId === span.runId
    && (sameKey(event.attemptId, span.attemptId) || sameKey(event.requestId, span.requestId)));
}

/** Events recorded on one delegation's own run identity. */
export function relatedEventsForRun(runId: string, events: readonly TimelineEvent[]): TimelineEvent[] {
  return events.filter(event => event.runId === runId);
}

/* ---- per-delegation rollups for whole-run selection and overview cards ---- */

export type RunRollup = {
  row: TimelineRow;
  /** This run's own execution spans in start order (missing starts last). */
  executions: TimelineSpan[];
  /** Waiting-Host spans whose request is actually still open. */
  pending: TimelineSpan[];
  /** Direct helpers of this run, in tree order. */
  helpers: TimelineRow[];
  /** Union duration of the run's own execution spans, or null when none is confirmable. */
  executionMs: number | null;
  /** Limitations that keep `executionMs` a recorded-part number, including read scope. */
  limitations: MetricLimitation["key"][];
};

function runExecutionMs(spans: readonly TimelineSpan[], observedAtMs: number | null): {
  ms: number | null; limitations: MetricLimitation["key"][];
} {
  const intervals: MetricInterval[] = [];
  const keys: MetricLimitation["key"][] = [];
  for (const span of spans) {
    const reading = executionInterval(span, observedAtMs);
    if (reading.interval !== null) intervals.push(reading.interval);
    if (reading.limitation !== null) keys.push(reading.limitation);
  }
  const merged = mergeUnion(intervals);
  return { ms: merged.length ? unionLength(merged) : null, limitations: [...new Set(keys)] };
}

/**
 * Rolls up one delegation from the same bounded read: rounds, helpers, the
 * result of the latest execution span, acceptance fields and pending Host
 * requests. Read-scope limitations propagate so a truncated card can never
 * claim definitive absence; a null result is never turned into a success.
 */
export function runRollup(row: TimelineRow, timeline: ObjectiveTimeline): RunRollup {
  const observedAtMs = parseTimelineInstant(timeline.observedAt);
  const own = timeline.spans.filter(span => span.runId === row.runId);
  const executions = [...own.filter(span => span.kind === "execution")].sort((left, right) => {
    const a = parseTimelineInstant(left.startAt);
    const b = parseTimelineInstant(right.startAt);
    if (a === null) return 1;
    if (b === null) return -1;
    return a - b;
  });
  // A request is pending only while its recorded state is open; a decided
  // request without an end timestamp is not still waiting.
  const pending = own.filter(span => span.kind === "host" && spanOutcome(span) === "open");
  const helpers = timeline.rows.filter(candidate => candidate.parentRunId === row.runId);
  const { ms, limitations } = runExecutionMs(executions, observedAtMs);
  const scope: MetricLimitation["key"][] = [];
  if (timeline.scopeComplete !== true) scope.push("scope");
  if (timeline.truncated.rows) scope.push("rows");
  if (timeline.truncated.spans) scope.push("spans");
  if (timeline.truncated.events) scope.push("events");
  return { row, executions, pending, helpers, executionMs: ms, limitations: [...new Set([...scope, ...limitations])] };
}

/** Root delegations in recorded creation order — the overview card order. */
export function rootRollups(timeline: ObjectiveTimeline): RunRollup[] {
  const roots = timeline.rows.filter(row => row.parentRunId === null);
  const withOrder = roots.map(row => ({ row, createdAtMs: parseTimelineInstant(row.createdAt) ?? Number.POSITIVE_INFINITY }));
  withOrder.sort((left, right) => left.createdAtMs - right.createdAtMs || left.row.runId.localeCompare(right.row.runId));
  return withOrder.map(entry => runRollup(entry.row, timeline));
}

/**
 * Honest text of one delegation's latest execution result; null only when no
 * execution was recorded at all. Ordering prefers recorded turn indices, then
 * usable start times; when neither can identify the latest attempt the label
 * stays 未记录 instead of resurrecting an older span's outcome.
 */
export function latestExecutionResult(rollup: RunRollup): { span: TimelineSpan; label: string } | null {
  const executions = rollup.executions;
  if (!executions.length) return null;
  const labelFor = (span: TimelineSpan): string => {
    const outcome = spanOutcome(span);
    if (outcome === "unknown") return "结束未确认";
    if (span.resultStatus === "ok") return "成功";
    if (span.resultStatus === "failed") return "失败";
    if (span.resultStatus === "cancelled") return "已取消";
    return "未记录";
  };
  if (executions.every(span => span.turnIndex !== null && span.turnIndex !== undefined)) {
    const latest = executions.reduce((best, span) => (span.turnIndex! > best.turnIndex! ? span : best));
    return { span: latest, label: labelFor(latest) };
  }
  const withStarts = executions.filter(span => parseTimelineInstant(span.startAt) !== null);
  if (withStarts.length === executions.length) {
    const latest = withStarts.reduce((best, span) =>
      parseTimelineInstant(span.startAt)! >= parseTimelineInstant(best.startAt)! ? span : best);
    return { span: latest, label: labelFor(latest) };
  }
  // Some executions carry neither turn order nor usable time: which one is
  // latest is unknown, so no older outcome may speak for the run.
  return { span: executions[executions.length - 1]!, label: "未记录" };
}
