import type { MacroTaskTimeline, TimelineSpan } from "../macro-task-types";

const MINUTE = 60_000;
const BREAK_PX = 54;
const MIN_REAL_PX = 64;
const TICK_MINUTES = [1, 2, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440];
const ACTIVE_EXECUTION_STATES = new Set(["starting", "executing", "finalizing", "uncertain"]);

export type TimeInterval = { startMs: number; endMs: number };

export type ScaleInput = {
  domain: TimeInterval | null;
  busy: TimeInterval[];
  instants: number[];
  canFold: boolean;
  nowAtEnd: boolean;
};

function instant(value: string | null): number | null {
  if (value === null) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

function hasBusyDuration(busy: TimeInterval[]): boolean {
  return busy.some(interval => interval.endMs > interval.startMs);
}

/** An acceptance wait starts only after the last execution has proven its stop. */
function confirmedExecutionEnd(spans: readonly TimelineSpan[]): number | null {
  if (spans.length === 0) return null;
  let last: { atMs: number; confirmed: boolean } | null = null;
  for (const span of spans) {
    const atMs = instant(span.endAt);
    if (atMs === null || span.clockSkew) return null;
    if (last === null || atMs > last.atMs) last = { atMs, confirmed: span.shutdownConfirmed === true };
  }
  return last?.confirmed ? last.atMs : null;
}

/** The service's open states are exact and differ by span kind. */
function isOpenState(kind: MacroTaskTimeline["spans"][number]["kind"], state: string): boolean {
  switch (kind) {
    case "queue": return state === "queued";
    case "routing":
    case "execution": return ACTIVE_EXECUTION_STATES.has(state);
    case "host": return state === "open";
  }
}

/** Recorded time bounds and open tails use the reply's observedAt. */
export function timelineScaleInput(timeline: MacroTaskTimeline): ScaleInput {
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
      span.uncertain || (span.endAt === null && isOpenState(span.kind, span.state))
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
  for (const row of timeline.rows) {
    for (const at of [row.acceptedAt, row.reviewRejectedAt]) {
      if (at === null) continue;
      const ms = instant(at);
      if (ms === null) trustworthy = false;
      else instants.push(ms);
    }
    if (row.state !== "delivered" || row.acceptedAt !== null || row.reviewRejectedAt !== null) continue;
    const endMs = confirmedExecutionEnd(timeline.spans.filter(span => span.runId === row.runId && span.kind === "execution"));
    if (endMs === null || observedAtMs === null || observedAtMs < endMs) {
      trustworthy = false;
      continue;
    }
    ongoing = true;
    instants.push(observedAtMs);
  }
  for (const event of timeline.events) {
    const ms = instant(event.at);
    if (ms === null) trustworthy = false;
    else instants.push(ms);
  }

  const sorted = [...new Set(instants)].sort((a, b) => a - b);
  const domain = sorted.length === 0 ? null : {
    startMs: sorted[0]!,
    endMs: sorted[sorted.length - 1]!,
  };
  return {
    domain, busy, instants: sorted,
    nowAtEnd: ongoing && domain?.endMs === observedAtMs,
    canFold: domain !== null && hasBusyDuration(busy)
      && timeline.scopeComplete && trustworthy && !timeline.filtered
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
  toX: (atMs: number) => number | null, nowAtEnd: boolean,
): TimelineTick[] {
  const step = TICK_MINUTES.find(minutes => minutes * ppm >= 56);
  const ticks: TimelineTick[] = [];
  const nowX = nowAtEnd ? toX(domain.endMs)! : Number.POSITIVE_INFINITY;
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
  const canFold = input.canFold && hasBusyDuration(input.busy);
  const ppm = pixelsPerMinute ?? fitScale(units, domain.endMs - domain.startMs, viewportWidth, canFold);
  const breaks: TimelineBreak[] = [];
  let widthPx = 0;
  const pieces = units.map(unit => {
    const folded = canFold && unit.idle && (unit.endMs - unit.startMs) / MINUTE * ppm > BREAK_PX;
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
  return { widthPx, pixelsPerMinute: ppm, breaks, ticks: ticksFor(domain, breaks, ppm, toX, input.nowAtEnd), toX, toTime };
}
