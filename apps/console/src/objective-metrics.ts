/**
 * Pure derived metrics for the work-objective overview (0.15 C5/C6).
 *
 * Everything is computed from the recorded spans, events and summary of one
 * bounded timeline read: no extrapolation, no idle deduction, and no "≥"
 * prefixes. Wall-clock span, the union of confirmed execution intervals and
 * the cumulative sum are three different numbers; queue, routing and
 * waiting-Host time never counts as execution. An unknown end, a reversed or
 * missing timestamp and clock skew are reported as recorded parts / incomplete
 * reasons, never repaired into invented durations or lower bounds.
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
  "missing-time": "有片段缺少时间",
  reversed: "有片段时间颠倒",
  "clock-skew": "有片段存在时钟偏差（clockSkew）",
  "unconfirmed-end": "有片段结束未确认，尾段未计",
  running: "有片段仍在进行，计至读取时刻",
};

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
  /** Unconfirmed-end execution segments whose tails were not counted. */
  unconfirmedEndCount: number;
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
 * span counts through observedAt only; an unconfirmed end stops at its last
 * recorded instant and flags its tail as uncounted.
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
  if (outcome === "running" || outcome === "open") {
    if (observedAtMs === null || observedAtMs < startMs) return { interval: null, limitation: "missing-time" };
    return { interval: { startMs, endMs: observedAtMs }, limitation: "running" };
  }
  if (endMs === null) return { interval: null, limitation: "missing-time" };
  if (endMs < startMs) return { interval: null, limitation: "reversed" };
  if (outcome === "unknown") {
    // The recorded end is the last recorded instant; the unconfirmed tail
    // beyond it is not proven execution and stays uncounted.
    return { interval: { startMs, endMs }, limitation: "unconfirmed-end" };
  }
  return { interval: { startMs, endMs }, limitation: null };
}

/**
 * The three distinct duration numbers of one timeline read. Total span is
 * wall-clock from the earliest recorded start (objective creation when no span
 * has one) to the latest recorded instant — observedAt when anything is still
 * running or unconfirmed. Execution uses only `execution` spans: queue,
 * routing and waiting-Host never contribute.
 */
export function objectiveMetrics(timeline: ObjectiveTimeline): ObjectiveMetrics {
  const observedAtMs = parseTimelineInstant(timeline.observedAt);
  const starts: number[] = [];
  const latestInstants: number[] = [];
  const intervals: MetricInterval[] = [];
  const limitationKeys: MetricLimitation["key"][] = [];
  let segmentCount = 0;
  let runningCount = 0;
  let unconfirmedEndCount = 0;
  let anyOpenTail = false;

  for (const span of timeline.spans) {
    const startMs = parseTimelineInstant(span.startAt);
    const endMs = parseTimelineInstant(span.endAt);
    if (startMs !== null) starts.push(startMs);
    if (endMs !== null) latestInstants.push(endMs);
    if (span.clockSkew === true) limitationKeys.push("clock-skew");
    if (startMs !== null && endMs !== null && endMs < startMs) limitationKeys.push("reversed");
    if (startMs === null || endMs === null) limitationKeys.push("missing-time");
    const outcome = spanOutcome(span);
    if (outcome === "running" || outcome === "open" || outcome === "unknown") anyOpenTail = true;

    const reading = executionInterval(span, observedAtMs);
    if (reading.interval !== null) {
      intervals.push(reading.interval);
      segmentCount += 1;
      if (reading.limitation === "running") runningCount += 1;
      if (reading.limitation === "unconfirmed-end") {
        unconfirmedEndCount += 1;
        limitationKeys.push("unconfirmed-end");
      }
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
    unconfirmedEndCount,
    limitations: limitations(limitationKeys),
  };
}

/* ---- related Host events: identity match only, never time proximity ---- */

function sameKey(left: string | null | undefined, right: string | null | undefined): boolean {
  // null and undefined must never match each other: an empty key is not a key.
  const a = left ?? null;
  const b = right ?? null;
  return a !== null && b !== null && a === b;
}

/** Events related to one span by shared non-empty attempt or request identity. */
export function relatedEventsForSpan(span: TimelineSpan, events: readonly TimelineEvent[]): TimelineEvent[] {
  return events.filter(event => sameKey(event.attemptId, span.attemptId) || sameKey(event.requestId, span.requestId));
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
  /** Unfinished waiting-Host spans (open requests). */
  pending: TimelineSpan[];
  /** Direct helpers of this run, in tree order. */
  helpers: TimelineRow[];
  /** Union duration of the run's own execution spans, or null when none is confirmable. */
  executionMs: number | null;
  /** Limitations that keep `executionMs` a recorded-part number. */
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
 * requests. Missing recorded facts stay null/"未记录" upstream; a null result
 * is never turned into a success.
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
  const pending = own.filter(span => span.kind === "host" && span.endAt == null);
  const helpers = timeline.rows.filter(candidate => candidate.parentRunId === row.runId);
  const { ms, limitations } = runExecutionMs(executions, observedAtMs);
  return { row, executions, pending, helpers, executionMs: ms, limitations };
}

/** Root delegations in recorded creation order — the overview card order. */
export function rootRollups(timeline: ObjectiveTimeline): RunRollup[] {
  const roots = timeline.rows.filter(row => row.parentRunId === null);
  const withOrder = roots.map(row => ({ row, createdAtMs: parseTimelineInstant(row.createdAt) ?? Number.POSITIVE_INFINITY }));
  withOrder.sort((left, right) => left.createdAtMs - right.createdAtMs || left.row.runId.localeCompare(right.row.runId));
  return withOrder.map(entry => runRollup(entry.row, timeline));
}

/** Honest text of one delegation's latest execution result; null when unrecorded. */
export function latestExecutionResult(rollup: RunRollup): { span: TimelineSpan; label: string } | null {
  const withStarts = rollup.executions.filter(span => parseTimelineInstant(span.startAt) !== null);
  const latest = withStarts[withStarts.length - 1];
  if (!latest) return null;
  const outcome = spanOutcome(latest);
  if (outcome === "unknown") return { span: latest, label: "结束未确认" };
  if (latest.resultStatus === "ok") return { span: latest, label: "成功" };
  if (latest.resultStatus === "failed") return { span: latest, label: "失败" };
  if (latest.resultStatus === "cancelled") return { span: latest, label: "已取消" };
  return { span: latest, label: "未记录" };
}
