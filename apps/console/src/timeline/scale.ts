import type { MacroTaskTimeline } from "../macro-task-types";

const MINUTE = 60_000;
const BREAK_PX = 54;
const MIN_REAL_PX = 64;
const TICK_MINUTES = [1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440];
const OPEN_STATES = new Set([
  "queued", "admitted", "pending", "starting", "executing", "finalizing", "uncertain",
  "running", "active", "open", "in-progress", "in_progress", "paused",
  "waiting", "waiting-host", "waiting_host", "awaiting-host", "awaiting_host",
  "cancelling", "canceling", "reconciliation-needed",
]);

export type TimeInterval = { startMs: number; endMs: number };

export type ScaleInput = {
  domain: TimeInterval | null;
  busy: TimeInterval[];
  instants: number[];
  canFold: boolean;
  nowMs: number;
};

function instant(value: string | null): number | null {
  if (value === null) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/** Recorded time bounds use the reply's observedAt; nowMs locates the display clock. */
export function timelineScaleInput(timeline: MacroTaskTimeline, nowMs: number): ScaleInput {
  const observedAtMs = instant(timeline.observedAt);
  const instants: number[] = [];
  const busy: TimeInterval[] = [];
  let trustworthy = true;
  let ongoing = false;

  for (const span of timeline.spans) {
    const startMs = instant(span.startAt);
    const recordedEndMs = instant(span.endAt);
    if (startMs !== null) instants.push(startMs);
    if (recordedEndMs !== null) instants.push(recordedEndMs);
    const open = span.shutdownConfirmed !== true && (
      span.uncertain || (span.endAt === null && OPEN_STATES.has(span.state.trim().toLowerCase()))
    );
    const endMs = open ? observedAtMs : recordedEndMs;
    const ordered = startMs !== null && endMs !== null && endMs >= startMs
      && (recordedEndMs === null || (recordedEndMs >= startMs && endMs >= recordedEndMs));
    // Missing evidence disables folding; it never becomes a fabricated interval.
    if (!ordered || span.clockSkew || (span.endAt !== null && recordedEndMs === null)) trustworthy = false;
    if (open && ordered) {
      ongoing = true;
      instants.push(endMs);
    }
    if (ordered && (span.kind === "routing" || span.kind === "execution")) {
      busy.push({ startMs, endMs });
    }
  }
  for (const event of timeline.events) {
    const ms = instant(event.at);
    if (ms === null) trustworthy = false;
    else instants.push(ms);
  }

  const sorted = [...new Set(instants)].sort((a, b) => a - b);
  const domain = sorted.length === 0 ? null : {
    startMs: sorted[0]!,
    endMs: ongoing ? observedAtMs! : sorted[sorted.length - 1]!,
  };
  return {
    domain, busy, instants: sorted, nowMs,
    canFold: domain !== null && timeline.scopeComplete && trustworthy && !timeline.filtered
      && !Object.values(timeline.truncated).some(Boolean),
  };
}

export type TimelineBreak = TimeInterval & { startX: number; endX: number };
export type TimelineTick = { atMs: number; labeled: boolean };
export type TimelineScale = {
  widthPx: number;
  pixelsPerMinute: number;
  breaks: TimelineBreak[];
  ticks: TimelineTick[];
  toX(atMs: number): number | null;
  toTime(x: number): number | null;
};

type Unit = TimeInterval & { idle: boolean };

function unitsFor(input: ScaleInput, domain: TimeInterval): Unit[] {
  const bounds = [...new Set([domain.startMs, domain.endMs, ...input.instants])]
    .filter(ms => ms >= domain.startMs && ms <= domain.endMs).sort((a, b) => a - b);
  const busy = [...input.busy].sort((a, b) => a.startMs - b.startMs);
  const units: Unit[] = [];
  let cursor = 0, busyUntil = domain.startMs;
  for (let i = 0; i + 1 < bounds.length; i += 1) {
    const startMs = bounds[i]!, endMs = bounds[i + 1]!;
    while (cursor < busy.length && busy[cursor]!.startMs <= startMs) {
      busyUntil = Math.max(busyUntil, busy[cursor]!.endMs);
      cursor += 1;
    }
    units.push({ startMs, endMs, idle: busyUntil < endMs });
  }
  return units;
}

function fitScale(units: Unit[], durationMs: number, viewportWidth: number, canFold: boolean): number {
  if (!canFold) return Math.max(MIN_REAL_PX, viewportWidth) * MINUTE / durationMs;
  const idle = units.filter(unit => unit.idle)
    .sort((a, b) => (a.endMs - a.startMs) - (b.endMs - b.startMs));
  let realMs = durationMs - idle.reduce((sum, unit) => sum + unit.endMs - unit.startMs, 0);
  let breakCount = idle.length;
  // With no proportional time, all waits may fold. Choose a finite scale
  // strictly above the shortest gap's threshold; there is no real track to reserve.
  if (realMs === 0) return BREAK_PX * (1 + Number.EPSILON) * MINUTE / (idle[0]!.endMs - idle[0]!.startMs);
  let ppm = Math.max(MIN_REAL_PX, viewportWidth - breakCount * BREAK_PX) * MINUTE / realMs;
  // Start with all candidates folded and unfold shortest first. Removed gaps
  // remain below the threshold; the first wider gap proves all remaining ones fold.
  for (const unit of idle) {
    const duration = unit.endMs - unit.startMs;
    if (duration / MINUTE * ppm > BREAK_PX) break;
    realMs += duration;
    breakCount -= 1;
    ppm = Math.max(MIN_REAL_PX, viewportWidth - breakCount * BREAK_PX) * MINUTE / realMs;
  }
  return ppm;
}

function ticksFor(
  domain: TimeInterval, breaks: TimelineBreak[], ppm: number,
  toX: (atMs: number) => number | null, nowMs: number,
): TimelineTick[] {
  const step = TICK_MINUTES.find(minutes => minutes * ppm >= 56);
  const ticks: TimelineTick[] = [];
  const nowX = nowMs === domain.endMs ? toX(nowMs)! : Number.POSITIVE_INFINITY;
  let previousLabelX = Number.NEGATIVE_INFINITY;
  let startMs = domain.startMs;
  for (const endMs of [...breaks.map(gap => gap.startMs), domain.endMs]) {
    // Section starts always name their time, including a zero-length edge section.
    ticks.push({ atMs: startMs, labeled: true });
    previousLabelX = toX(startMs)!;
    if (step !== undefined) {
      const first = new Date(startMs);
      const day = new Date(first.getFullYear(), first.getMonth(), first.getDate());
      while (day.getTime() <= endMs) {
        for (let minute = 0; minute < 1440; minute += step) {
          const date = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, minute);
          // A daylight-saving jump may remove a local wall-clock instant.
          if (date.getHours() * 60 + date.getMinutes() !== minute) continue;
          const atMs = date.getTime();
          if (atMs <= startMs || atMs > endMs) continue;
          const x = toX(atMs)!;
          const labeled = x - previousLabelX >= 72 && nowX - x >= 72;
          ticks.push({ atMs, labeled });
          if (labeled) previousLabelX = x;
        }
        day.setDate(day.getDate() + 1);
      }
    }
    const gap = breaks.find(item => item.startMs === endMs);
    if (gap !== undefined) startMs = gap.endMs;
  }
  return ticks;
}

/** Fit to the viewport when ppm is absent; manual zoom uses exactly the supplied scale. */
export function createTimelineScale(input: ScaleInput, viewportWidth: number, pixelsPerMinute?: number): TimelineScale {
  const domain = input.domain;
  if (domain === null || domain.startMs === domain.endMs) {
    return {
      widthPx: 0, pixelsPerMinute: pixelsPerMinute ?? 0, breaks: [],
      ticks: domain === null ? [] : [{ atMs: domain.startMs, labeled: true }],
      toX: () => domain === null ? null : 0,
      toTime: () => domain === null ? null : domain.startMs,
    };
  }
  const units = unitsFor(input, domain);
  const ppm = pixelsPerMinute ?? fitScale(units, domain.endMs - domain.startMs, viewportWidth, input.canFold);
  const breaks: TimelineBreak[] = [];
  let widthPx = 0;
  const pieces = units.map(unit => {
    const folded = input.canFold && unit.idle && (unit.endMs - unit.startMs) / MINUTE * ppm > BREAK_PX;
    const startX = widthPx;
    widthPx += folded ? BREAK_PX : (unit.endMs - unit.startMs) / MINUTE * ppm;
    const piece = { ...unit, startX, endX: widthPx };
    if (folded) breaks.push({ startMs: unit.startMs, endMs: unit.endMs, startX, endX: widthPx });
    return piece;
  });
  function toX(atMs: number): number {
    if (atMs <= domain!.startMs) return 0;
    if (atMs >= domain!.endMs) return widthPx;
    const piece = pieces.find(unit => atMs <= unit.endMs)!;
    return piece.startX + (atMs - piece.startMs) / (piece.endMs - piece.startMs) * (piece.endX - piece.startX);
  }
  function toTime(x: number): number {
    if (x <= 0) return domain!.startMs;
    if (x >= widthPx) return domain!.endMs;
    const piece = pieces.find(unit => x <= unit.endX)!;
    return piece.startMs + (x - piece.startX) / (piece.endX - piece.startX) * (piece.endMs - piece.startMs);
  }
  return { widthPx, pixelsPerMinute: ppm, breaks, ticks: ticksFor(domain, breaks, ppm, toX, input.nowMs), toX, toTime };
}
