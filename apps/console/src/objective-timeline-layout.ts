/**
 * Pure, DOM-free layout for the work-objective timeline (ADR-012 section X).
 *
 * The console receives recorded facts only: spans with recorded start/end
 * timestamps and Host markers with one instant. This module turns those facts
 * into a one-dimensional 0..100 coordinate so narrow and wide layouts can share
 * the same mapping without DOM measurements, and it folds long empty intervals
 * into explicit expandable breaks.
 *
 * Nothing here invents evidence: an interval that was not recorded is never
 * repaired into a duration, a terminal record with missing timing never becomes
 * an active span, and automatic folding is refused whenever omitted data could
 * occupy the apparent gap (incomplete scope, clock skew, unusable or reversed
 * timestamps, an unknown extent).
 */

import type { ObjectiveTimeline, TimelineSpan } from "./objective-types";

/** Empty intervals strictly longer than this fold when the view is eligible. */
export const TIMELINE_FOLD_THRESHOLD_MS = 30 * 60 * 1000;
const HOST_MARKER_PADDING_MS = 60 * 1000;

/** Width budget of one collapsed break, and of all collapsed breaks together, in percent. */
const COLLAPSED_GAP_PERCENT = 3;
const COLLAPSED_TOTAL_PERCENT = 40;

/** A timeline whose whole domain is a single instant still needs one coordinate. */
const POINT_POSITION_PERCENT = 50;

/**
 * Accepted instant grammar: a calendar date, `T` or a space, wall-clock time to
 * the second, optional fractional seconds, and a mandatory explicit offset.
 * A missing offset is unusable here because it would parse as the viewer's local
 * time and make the same layout differ between machines.
 */
const ISO_INSTANT = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?(Z|z|[+-]\d{2}:?\d{2})$/;

/**
 * Recorded states that mean the span is still open, so a null end may honestly
 * extend to observedAt. Any other state (including an unknown future one) is
 * treated as unresolved: it occupies nothing and disables folding rather than
 * being guessed into an active tail.
 */
const OPEN_SPAN_STATES = new Set([
  "queued", "claimed", "pending", "starting", "executing", "finalizing", "uncertain",
  "running", "active", "open", "in-progress", "in_progress", "paused",
  "waiting", "waiting-host", "waiting_host", "awaiting-host", "awaiting_host",
  "cancelling", "canceling", "reconciliation-needed",
]);

/**
 * One empty interval of the objective timeline. A gap is bounded by recorded
 * facts (an occupied span end, a Host marker instant or the recorded domain
 * edge), never by an idle guess.
 */
export type TimelineGap = {
  /** Deterministic from the original boundaries (`gap-<startMs>-<endMs>`), so a refresh keeps expanded ids. */
  id: string;
  /** Original boundary instants in epoch milliseconds. */
  startMs: number;
  endMs: number;
  /** True when the break renders at its small fixed share instead of its real duration. */
  collapsed: boolean;
  /** Normalized 0..100 coordinate of the rendered break; `toPercent - fromPercent` is its visible width. */
  fromPercent: number;
  toPercent: number;
};

/** Layout of one recorded objective timeline over a normalized 0..100 axis. */
export type TimelineLayout = {
  /** Earliest usable recorded instant, or null when the view has no usable time at all. */
  startMs: number | null;
  /** Latest usable recorded instant (an open span's observedAt tail counts), or null. */
  endMs: number | null;
  /** Empty intervals in chronological order; empty when there is no usable domain. */
  gaps: TimelineGap[];
  /**
   * Whether this view is *eligible* for automatic folding: the scope is complete
   * (no truncation flag when the caller passes them) and every needed timestamp
   * is usable and non-reversed. It does not by itself mean a gap exists or that
   * one was folded; `gaps[].collapsed` carries the per-gap decision.
   */
  canFold: boolean;
  /**
   * Maps a timestamp to a finite 0..100 coordinate, or null for an unusable
   * value or a view without usable time. Numbers are epoch milliseconds;
   * strings use the ISO grammar above. Instants outside the domain clamp to
   * 0 or 100. The mapping is monotonic and continuous across gap boundaries.
   */
  position(timestamp: string | number | null): number | null;
};

type Interval = { startMs: number; endMs: number };

type SpanReading = {
  /** Usable instants recorded on the span, including an open tail's observedAt end. */
  instants: number[];
  /** Occupied extent when it is known and non-reversed. */
  interval: Interval | null;
  /** True when the extent cannot be placed, so no gap may be inferred around it. */
  unknown: boolean;
};

type Unit = {
  startMs: number;
  endMs: number;
  /** Present for an empty interval; `collapsed` is filled once eligibility is known. */
  gap: { id: string; collapsed: boolean } | null;
  fromPercent: number;
  toPercent: number;
};

function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year: number, month: number): number {
  switch (month) {
    case 2: return isLeapYear(year) ? 29 : 28;
    case 4: case 6: case 9: case 11: return 30;
    default: return 31;
  }
}

/**
 * A strict instant parser. It rejects unparseable text, calendar dates that do
 * not exist (for example February 30), impossible clock values, and timestamps
 * without an explicit offset, because none of those can be placed on a shared
 * axis without inventing a reading.
 */
export function parseTimelineInstant(value: string | number | null | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const text = value.trim();
  const match = ISO_INSTANT.exec(text);
  if (match === null) return null;
  const [, year, month, day, hour, minute, second, , zone] = match;
  if (!isCalendarDate(Number(year), Number(month), Number(day))) return null;
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null;
  const offset = zone.replace(":", "");
  if (offset !== "Z" && offset !== "z" && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(3, 5)) > 59)) return null;
  const normalizedOffset = offset === "Z" || offset === "z" ? "Z" : `${offset.slice(0, 3)}:${offset.slice(3, 5)}`;
  const plain = text.slice(0, text.length - zone.length).replace(/^(\d{4}-\d{2}-\d{2})[Tt ]/, "$1T");
  const parsed = Date.parse(`${plain}${normalizedOffset}`);
  return Number.isFinite(parsed) ? parsed : null;
}

function isCalendarDate(year: number, month: number, day: number): boolean {
  if (month < 1 || month > 12 || day < 1) return false;
  return day <= daysInMonth(year, month);
}

function isOpenOrUncertain(span: TimelineSpan): boolean {
  if (span.uncertain === true) return true;
  const state = typeof span.state === "string" ? span.state.trim().toLowerCase() : "";
  return OPEN_SPAN_STATES.has(state);
}

/**
 * Reads one span into occupancy, recorded instants and an unknown-extent flag.
 * Only an active/open or explicitly uncertain span with a usable start and a
 * null end may extend to observedAt; a terminal record with missing timing stays
 * unknown instead of becoming an active span.
 */
function readSpan(span: TimelineSpan, observedAtMs: number | null): SpanReading {
  const startMs = parseTimelineInstant(span.startAt);
  const endMs = parseTimelineInstant(span.endAt);
  const instants: number[] = [];
  if (startMs !== null) instants.push(startMs);
  if (endMs !== null) instants.push(endMs);

  // A confirmed stop is terminal evidence: a missing end is unresolved, not an active tail.
  const stopped = span.shutdownConfirmed === true;
  const hasNoEnd = span.endAt === null || span.endAt === undefined;
  const openEnded = hasNoEnd && startMs !== null && !stopped && isOpenOrUncertain(span);

  if (startMs !== null && endMs !== null) {
    if (endMs >= startMs) {
      // A finish timestamp alone does not prove the process stopped. Keep the
      // recorded endpoint for rendering, but reserve its unknown tail so an
      // idle break cannot hide potentially live work after that timestamp.
      if (span.uncertain === true && !stopped) {
        if (observedAtMs === null || observedAtMs < endMs) {
          return { instants, interval: null, unknown: true };
        }
        instants.push(observedAtMs);
        return { instants, interval: { startMs, endMs: observedAtMs }, unknown: false };
      }
      return { instants, interval: { startMs, endMs }, unknown: false };
    }
    // Reversed timestamps are recorded facts but are never repaired into an invented duration.
    return { instants, interval: null, unknown: true };
  }
  if (openEnded && observedAtMs !== null && observedAtMs >= startMs) {
    instants.push(observedAtMs);
    return { instants, interval: { startMs, endMs: observedAtMs }, unknown: false };
  }
  return { instants, interval: null, unknown: true };
}

/** Unions overlapping and nested occupancy so no gap can appear inside one span's shadow. */
function mergeIntervals(intervals: readonly Interval[]): Interval[] {
  const sorted = [...intervals].sort((left, right) => left.startMs - right.startMs || left.endMs - right.endMs);
  const merged: Interval[] = [];
  for (const interval of sorted) {
    const last = merged[merged.length - 1];
    if (last !== undefined && interval.startMs <= last.endMs) {
      if (interval.endMs > last.endMs) last.endMs = interval.endMs;
      continue;
    }
    merged.push({ startMs: interval.startMs, endMs: interval.endMs });
  }
  return merged;
}

function gapId(startMs: number, endMs: number): string {
  return `gap-${startMs}-${endMs}`;
}

/**
 * The required input slice reports completeness through `scopeComplete`. A
 * caller that also passes the read's per-collection truncation flags (the full
 * `ObjectiveTimeline`) gives a second, stricter signal: any truncated
 * collection could hold omitted occupancy, and folding must then be refused.
 */
function hasOmittedScope(timeline: object): boolean {
  if ("filtered" in timeline && timeline.filtered === true) return true;
  const truncated: unknown = (timeline as { truncated?: unknown }).truncated;
  if (truncated === null || typeof truncated !== "object") return false;
  const flags = truncated as Record<string, unknown>;
  return flags.rows === true || flags.spans === true || flags.events === true;
}

function isCollapsed(unit: Unit): boolean {
  return unit.gap !== null && unit.gap.collapsed;
}

/**
 * Cuts the domain at every recorded instant, classifies each atomic interval as
 * occupied or empty, decides folding per gap, and assigns 0..100 widths.
 *
 * Width allocation: each collapsed break takes `min(3%, 40% / count)`, the
 * remaining width is split linearly by real duration over the unfolded
 * intervals, and folded ids from `expandedGapIds` keep their full width.
 * Folding is skipped entirely when no interval would keep its real width (a
 * domain made only of empty stretches) and when the view is not eligible, so
 * the caps always hold and an all-empty axis keeps its real scale.
 */
function buildUnits(
  startMs: number,
  endMs: number,
  instants: readonly number[],
  occupied: readonly Interval[],
  canFold: boolean,
  expandedGapIds: ReadonlySet<string> | undefined,
): Unit[] {
  if (endMs === startMs) return [];
  const bounds = [...new Set(instants)].sort((left, right) => left - right);
  const units: Unit[] = [];
  let cursor = 0;
  for (let index = 0; index + 1 < bounds.length; index += 1) {
    const from = bounds[index]!;
    const to = bounds[index + 1]!;
    if (to === from) continue;
    while (cursor < occupied.length && occupied[cursor]!.endMs <= from) cursor += 1;
    const current = cursor < occupied.length ? occupied[cursor]! : null;
    const covered = current !== null && current.startMs <= from && current.endMs >= to;
    units.push({
      startMs: from,
      endMs: to,
      gap: covered ? null : { id: gapId(from, to), collapsed: false },
      fromPercent: 0,
      toPercent: 0,
    });
  }

  const candidates = new Set<Unit>();
  for (const unit of units) {
    if (unit.gap === null) continue;
    if (canFold && unit.endMs - unit.startMs > TIMELINE_FOLD_THRESHOLD_MS && !expandedGapIds?.has(unit.gap.id)) {
      candidates.add(unit);
    }
  }
  const unfoldedMs = units.reduce((total, unit) => total + (candidates.has(unit) ? 0 : unit.endMs - unit.startMs), 0);
  const folding = candidates.size > 0 && unfoldedMs > 0;
  if (folding) for (const unit of candidates) unit.gap!.collapsed = true;
  const collapsedCount = folding ? candidates.size : 0;
  const collapsedPercent = collapsedCount === 0 ? 0 : Math.min(COLLAPSED_GAP_PERCENT, COLLAPSED_TOTAL_PERCENT / collapsedCount);
  // Fold-free layouts keep every interval (candidate included) on the real scale.
  const realMs = folding ? unfoldedMs : units.reduce((total, unit) => total + (unit.endMs - unit.startMs), 0);
  const realPercent = folding ? 100 - collapsedPercent * collapsedCount : 100;

  let cumulative = 0;
  for (const unit of units) {
    const width = folding && isCollapsed(unit)
      ? collapsedPercent
      : (realPercent * (unit.endMs - unit.startMs)) / realMs;
    unit.fromPercent = Math.min(cumulative, 100);
    cumulative += width;
    unit.toPercent = Math.min(cumulative, 100);
  }
  // Pin negligible floating-point drift so the axis always ends exactly at 100.
  const last = units[units.length - 1];
  if (last !== undefined && last.fromPercent <= 100) last.toPercent = 100;
  return units;
}

function createPosition(startMs: number, endMs: number, units: readonly Unit[]) {
  return (timestamp: string | number | null): number | null => {
    const atMs = parseTimelineInstant(timestamp);
    if (atMs === null) return null;
    // A zero-length domain has no direction: its single instant sits at the midpoint.
    if (startMs === endMs) return atMs === startMs ? POINT_POSITION_PERCENT : atMs < startMs ? 0 : 100;
    if (atMs <= startMs) return 0;
    if (atMs >= endMs) return 100;
    for (const unit of units) {
      if (atMs <= unit.startMs) return unit.fromPercent;
      if (atMs < unit.endMs) {
        const width = unit.endMs - unit.startMs;
        const fraction = width > 0 ? (atMs - unit.startMs) / width : 0;
        return unit.fromPercent + fraction * (unit.toPercent - unit.fromPercent);
      }
    }
    return 100;
  };
}

/**
 * Builds the documented timeline layout for the recorded spans, Host markers,
 * observation instant and scope flag of one objective read.
 *
 * Domain: the earliest and latest usable recorded instant across span endpoints
 * (including the observedAt tail of a legitimately open span) and Host markers.
 * There is never idle space before the first or after the last recorded item.
 *
 * Gaps: empty intervals between the unioned occupancy, split at every recorded
 * instant. Host markers reserve a one-minute neighborhood clipped to the domain;
 * this is conservative visual spacing, not a claim of Host execution duration.
 * Folding applies only to intervals strictly longer than 30 minutes,
 * only when `scopeComplete` is true (and, when present, no truncation flag is
 * set) and every needed timestamp is usable and non-reversed, and never to an
 * id passed in `expandedGapIds`. Folding also needs at least one interval that
 * keeps its real width to fold against.
 *
 * @param timeline Read-only slice of `ObjectiveTimeline` the layout may read.
 * @param expandedGapIds Gap ids whose breaks the user expanded; they keep full width.
 */
export function createTimelineLayout(
  timeline: Pick<ObjectiveTimeline, "spans" | "events" | "observedAt" | "scopeComplete">,
  expandedGapIds?: ReadonlySet<string>,
): TimelineLayout {
  const observedAtMs = parseTimelineInstant(timeline.observedAt);
  const instants: number[] = [];
  const markerInstants: number[] = [];
  const intervals: Interval[] = [];
  let timingTrustworthy = true;

  for (const span of timeline.spans) {
    const reading = readSpan(span, observedAtMs);
    for (const instant of reading.instants) instants.push(instant);
    if (reading.interval !== null) intervals.push(reading.interval);
    if (reading.unknown || span.clockSkew === true) timingTrustworthy = false;
  }

  for (const event of timeline.events) {
    const atMs = parseTimelineInstant(event.at);
    if (atMs === null) {
      timingTrustworthy = false;
      continue;
    }
    instants.push(atMs);
    markerInstants.push(atMs);
  }

  if (instants.length === 0) {
    return { startMs: null, endMs: null, gaps: [], canFold: false, position: () => null };
  }

  let startMs = instants[0]!;
  let endMs = instants[0]!;
  for (const instant of instants) {
    if (instant < startMs) startMs = instant;
    if (instant > endMs) endMs = instant;
  }

  // Markers reserve a one-minute visual neighborhood on either side. Clip it
  // to the recorded domain: padding must never invent leading/trailing time.
  for (const atMs of markerInstants) {
    const from = Math.max(startMs, atMs - HOST_MARKER_PADDING_MS);
    const to = Math.min(endMs, atMs + HOST_MARKER_PADDING_MS);
    instants.push(from, to);
    intervals.push({ startMs: from, endMs: to });
  }

  const canFold = timeline.scopeComplete === true && timingTrustworthy && !hasOmittedScope(timeline);
  const units = buildUnits(startMs, endMs, instants, mergeIntervals(intervals), canFold, expandedGapIds);
  const gaps: TimelineGap[] = [];
  for (const unit of units) {
    if (unit.gap === null) continue;
    gaps.push({
      id: unit.gap.id,
      startMs: unit.startMs,
      endMs: unit.endMs,
      collapsed: unit.gap.collapsed,
      fromPercent: unit.fromPercent,
      toPercent: unit.toPercent,
    });
  }
  return { startMs, endMs, gaps, canFold, position: createPosition(startMs, endMs, units) };
}
