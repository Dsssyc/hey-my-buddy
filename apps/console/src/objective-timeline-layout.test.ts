import { describe, expect, it } from "vitest";
import type { ObjectiveTimeline, TimelineEvent, TimelineSpan } from "./objective-types";
import type { TimelineGap } from "./objective-timeline-layout";
import { TIMELINE_FOLD_THRESHOLD_MS, createTimelineLayout } from "./objective-timeline-layout";

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const BASE = Date.parse("2026-09-26T00:00:00.000Z");

const at = (offsetMs: number): string => new Date(BASE + offsetMs).toISOString();

type TimelineSlice = Pick<ObjectiveTimeline, "spans" | "events" | "observedAt" | "scopeComplete">;

const span = (overrides: Partial<TimelineSpan> = {}): TimelineSpan => ({
  spanId: "span-1", runId: "run-1", kind: "execution",
  startAt: at(0), endAt: at(10 * MINUTE), state: "completed",
  attemptId: "attempt-1", turnId: null, turnIndex: null, requestId: null,
  configuration: null, shutdownConfirmed: true, uncertain: false, clockSkew: false,
  ...overrides,
});

const event = (offsetMs: number, overrides: Partial<TimelineEvent> = {}): TimelineEvent => ({
  seq: 1, runId: "run-1", kind: "host-note", at: at(offsetMs), label: "标记", summary: "Host 记录",
  actor: "host", attemptId: null, requestId: null, artifactId: null,
  ...overrides,
});

const timeline = (overrides: Partial<TimelineSlice> = {}): TimelineSlice => ({
  spans: [], events: [], observedAt: at(0), scopeComplete: true,
  ...overrides,
});

/** Gap bounds relative to BASE, so assertions read in minutes/hours. */
const bounds = (gap: TimelineGap): number[] => [gap.startMs - BASE, gap.endMs - BASE];
const width = (gap: TimelineGap): number => gap.toPercent - gap.fromPercent;

/** Two recorded attempts with an exact empty interval between them. */
const twoAttempts = (gapMs: number, remaining: Partial<TimelineSlice> = {}): TimelineSlice => timeline({
  spans: [
    span({ spanId: "first", startAt: at(0), endAt: at(10 * MINUTE) }),
    span({ spanId: "second", startAt: at(10 * MINUTE + gapMs), endAt: at(20 * MINUTE + gapMs) }),
  ],
  ...remaining,
});

describe("objective timeline layout domain", () => {
  it("derives the domain from recorded span endpoints and Host marker instants", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ startAt: at(10 * MINUTE), endAt: at(20 * MINUTE) })],
      events: [event(60 * MINUTE)],
      observedAt: at(9 * HOUR),
    }));
    expect(layout.startMs).toBe(BASE + 10 * MINUTE);
    expect(layout.endMs).toBe(BASE + 60 * MINUTE);
    expect(layout.position(BASE)).toBe(0);
    expect(layout.position(at(9 * HOUR))).toBe(100);
  });

  it("never invents idle time before the first or after the last recorded item", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ startAt: at(100 * MINUTE), endAt: at(110 * MINUTE) })],
      events: [event(300 * MINUTE)],
      observedAt: at(48 * HOUR),
    }));
    expect(layout.startMs).toBe(BASE + 100 * MINUTE);
    expect(layout.endMs).toBe(BASE + 300 * MINUTE);
    expect(layout.gaps.map(bounds)).toEqual([[110 * MINUTE, 300 * MINUTE]]);
    expect(layout.position(at(47 * HOUR))).toBe(100);
  });

  it("returns nullable bounds, no gaps and null positions without usable time", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ startAt: null, endAt: null, state: "queued", shutdownConfirmed: null })],
      events: [event(0, { at: "not a timestamp" })],
    }));
    expect(layout.startMs).toBeNull();
    expect(layout.endMs).toBeNull();
    expect(layout.gaps).toEqual([]);
    expect(layout.canFold).toBe(false);
    expect(layout.position(at(0))).toBeNull();
  });

  it("maps a point-only domain to one finite coordinate without dividing by zero", () => {
    const layout = createTimelineLayout(timeline({ events: [event(0)], observedAt: at(24 * HOUR) }));
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE);
    expect(layout.gaps).toEqual([]);
    expect(layout.position(at(0))).toBe(50);
    expect(layout.position(at(-1))).toBe(0);
    expect(layout.position(at(1))).toBe(100);
    expect(Number.isFinite(layout.position(at(0)) ?? Number.NaN)).toBe(true);
    expect(layout.endMs).not.toBe(BASE + 24 * HOUR);

    const zeroLengthSpan = createTimelineLayout(timeline({
      spans: [span({ spanId: "point", startAt: at(5 * MINUTE), endAt: at(5 * MINUTE) })],
      observedAt: at(24 * HOUR),
    }));
    expect(zeroLengthSpan.startMs).toBe(BASE + 5 * MINUTE);
    expect(zeroLengthSpan.endMs).toBe(BASE + 5 * MINUTE);
    expect(zeroLengthSpan.position(at(5 * MINUTE))).toBe(50);
    expect(zeroLengthSpan.gaps).toEqual([]);
  });
});

describe("objective timeline layout occupancy", () => {
  it("keeps an unconfirmed finished attempt occupied through observation", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ startAt: at(0), endAt: at(10 * MINUTE), state: "finished", shutdownConfirmed: false, uncertain: true }),
        span({ spanId: "later", startAt: at(2 * HOUR), endAt: at(130 * MINUTE) }),
      ],
      observedAt: at(3 * HOUR),
    }));
    expect(layout.endMs).toBe(BASE + 3 * HOUR);
    expect(layout.gaps).toEqual([]);
    expect(layout.position(at(10 * MINUTE))!).toBeLessThan(layout.position(at(3 * HOUR))!);
  });

  it("refuses folding when an uncertain recorded end is newer than observation", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ endAt: at(10 * MINUTE), state: "finished", shutdownConfirmed: false, uncertain: true }),
        span({ spanId: "later", startAt: at(2 * HOUR), endAt: at(130 * MINUTE) }),
      ],
      observedAt: at(5 * MINUTE),
    }));
    expect(layout.canFold).toBe(false);
    expect(layout.gaps.every(gap => !gap.collapsed)).toBe(true);
  });

  it("unions parallel and nested spans before detecting a gap", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "long", startAt: at(0), endAt: at(2 * HOUR) }),
        span({ spanId: "nested", startAt: at(30 * MINUTE), endAt: at(90 * MINUTE) }),
        span({ spanId: "parallel", startAt: at(150 * MINUTE), endAt: at(160 * MINUTE) }),
      ],
    }));
    // The nested and overlapping spans leave no empty interval inside 0..120min.
    expect(layout.gaps.map(bounds)).toEqual([[120 * MINUTE, 150 * MINUTE]]);
    expect(layout.position(at(30 * MINUTE))!).toBeGreaterThan(layout.position(at(29 * MINUTE))!);
    expect(layout.position(at(90 * MINUTE))!).toBeGreaterThan(layout.position(at(89 * MINUTE))!);
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE + 160 * MINUTE);
  });

  it("splits an otherwise-empty interval at a Host marker instant", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "first", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "second", startAt: at(2 * HOUR), endAt: at(2 * HOUR + 10 * MINUTE) }),
      ],
      events: [event(60 * MINUTE)],
    }));
    expect(layout.gaps.map(bounds)).toEqual([[10 * MINUTE, 60 * MINUTE], [60 * MINUTE, 2 * HOUR]]);
    expect(layout.gaps.map(gap => gap.collapsed)).toEqual([true, true]);
    expect(layout.gaps[0]!.toPercent).toBeCloseTo(layout.gaps[1]!.fromPercent, 9);
  });

  it("does not create a gap for a Host marker inside an occupied span", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ spanId: "long", startAt: at(0), endAt: at(2 * HOUR) })],
      events: [event(HOUR)],
    }));
    expect(layout.gaps).toEqual([]);
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE + 2 * HOUR);
  });

  it("unions adjacent spans and lets a zero-length span split without occupying", () => {
    const adjacent = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "b", startAt: at(10 * MINUTE), endAt: at(20 * MINUTE) }),
      ],
    }));
    expect(adjacent.gaps).toEqual([]);
    expect(adjacent.position(at(10 * MINUTE))).toBeCloseTo(50, 6);

    const pointSpan = createTimelineLayout(timeline({
      spans: [span({ spanId: "point", startAt: at(HOUR), endAt: at(HOUR) })],
      events: [event(0), event(2 * HOUR)],
    }));
    expect(pointSpan.gaps.map(bounds)).toEqual([[0, HOUR], [HOUR, 2 * HOUR]]);
    expect(pointSpan.canFold).toBe(true);
    // Nothing keeps a real width here, so both empty stretches stay at real scale.
    expect(pointSpan.gaps.every(gap => !gap.collapsed)).toBe(true);
  });

  it("extends an open waiting-Host span to the observation instant", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "work", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "wait", kind: "host", startAt: at(20 * MINUTE), endAt: null, state: "waiting-host", shutdownConfirmed: null }),
      ],
      observedAt: at(3 * HOUR),
    }));
    expect(layout.endMs).toBe(BASE + 3 * HOUR);
    expect(layout.gaps.map(bounds)).toEqual([[10 * MINUTE, 20 * MINUTE]]);
    expect(layout.position(at(3 * HOUR))).toBe(100);
  });

  it("treats an explicitly uncertain span with a known start and null end as occupied", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "work", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "cancelling", startAt: at(20 * MINUTE), endAt: null, state: "finished", uncertain: true, shutdownConfirmed: false }),
      ],
      observedAt: at(120 * MINUTE),
    }));
    expect(layout.endMs).toBe(BASE + 120 * MINUTE);
    expect(layout.gaps.map(bounds)).toEqual([[10 * MINUTE, 20 * MINUTE]]);
  });

  it("treats an omitted end key like a null end for an open span", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ spanId: "wait", startAt: at(0), endAt: undefined, state: "running", shutdownConfirmed: false })],
      observedAt: at(HOUR),
    }));
    expect(layout.endMs).toBe(BASE + HOUR);
    expect(layout.gaps).toEqual([]);
  });

  it("does not extend a confirmed-stopped attempt that is missing its end", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ startAt: at(0), endAt: null, state: "executing", uncertain: true, shutdownConfirmed: true })],
      observedAt: at(120 * MINUTE),
    }));
    expect(layout.endMs).toBe(BASE);
    expect(layout.gaps).toEqual([]);
    expect(layout.canFold).toBe(false);
  });

  it("does not extend an open span when observedAt is unusable or precedes the start", () => {
    const unusable = createTimelineLayout(timeline({
      spans: [span({ startAt: at(0), endAt: null, state: "running", shutdownConfirmed: false })],
      observedAt: "yesterday",
    }));
    expect(unusable.startMs).toBe(BASE);
    expect(unusable.endMs).toBe(BASE);
    expect(unusable.canFold).toBe(false);

    const backwards = createTimelineLayout(timeline({
      spans: [span({ startAt: at(60 * MINUTE), endAt: null, state: "running", shutdownConfirmed: false })],
      observedAt: at(0),
    }));
    expect(backwards.startMs).toBe(BASE + 60 * MINUTE);
    expect(backwards.endMs).toBe(BASE + 60 * MINUTE);
    expect(backwards.position(at(0))).toBe(0);
    expect(Number.isFinite(backwards.position(at(60 * MINUTE)) ?? Number.NaN)).toBe(true);
  });

  it("keeps an all-terminal timeline at its last recorded end", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "first", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "second", startAt: at(20 * MINUTE), endAt: at(30 * MINUTE) }),
      ],
      observedAt: at(7 * 24 * HOUR),
    }));
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE + 30 * MINUTE);
    expect(layout.gaps.map(bounds)).toEqual([[10 * MINUTE, 20 * MINUTE]]);
    expect(layout.canFold).toBe(true);
    expect(layout.gaps[0]!.collapsed).toBe(false);
  });
});

describe("objective timeline idle folding", () => {
  it("folds only empty intervals strictly longer than 30 minutes", () => {
    const exactly = createTimelineLayout(twoAttempts(TIMELINE_FOLD_THRESHOLD_MS));
    expect(exactly.gaps).toHaveLength(1);
    expect(exactly.gaps[0]!.collapsed).toBe(false);
    expect(exactly.canFold).toBe(true);

    const longer = createTimelineLayout(twoAttempts(TIMELINE_FOLD_THRESHOLD_MS + 1));
    expect(longer.gaps).toHaveLength(1);
    expect(longer.gaps[0]!.collapsed).toBe(true);
  });

  it("restores full time width for an expanded gap id without moving the id", () => {
    const gapMs = 2 * HOUR;
    const folded = createTimelineLayout(twoAttempts(gapMs));
    const gap = folded.gaps[0]!;
    expect(gap.collapsed).toBe(true);

    const expanded = createTimelineLayout(twoAttempts(gapMs), new Set([gap.id]));
    expect(expanded.gaps[0]!.id).toBe(gap.id);
    expect(expanded.gaps[0]!.collapsed).toBe(false);
    expect(width(expanded.gaps[0]!)).toBeGreaterThan(width(gap));
    // Unfolded width follows real duration: 10min + 120min + 10min over the domain.
    expect(width(expanded.gaps[0]!)).toBeCloseTo((gapMs / (gapMs + 20 * MINUTE)) * 100, 6);
    expect(expanded.gaps[0]!.fromPercent).toBeGreaterThan(0);
  });

  it("caps each folded break at 3% and all folded breaks at 40%", () => {
    const spans: TimelineSpan[] = [];
    for (let index = 0; index < 21; index += 1) {
      spans.push(span({ spanId: `s${index}`, startAt: at(index * 61 * MINUTE), endAt: at(index * 61 * MINUTE + MINUTE) }));
    }
    const layout = createTimelineLayout(timeline({ spans }));
    const folded = layout.gaps.filter(gap => gap.collapsed);
    expect(folded).toHaveLength(20);
    for (const gap of folded) expect(width(gap)).toBeLessThanOrEqual(3 + 1e-9);
    const total = folded.reduce((sum, gap) => sum + width(gap), 0);
    expect(total).toBeCloseTo(40, 6);
    expect(layout.position(layout.endMs)).toBe(100);
  });

  it("uses the full 3% per break when the 40% budget is not binding", () => {
    const spans: TimelineSpan[] = [];
    for (let index = 0; index < 10; index += 1) {
      spans.push(span({ spanId: `t${index}`, startAt: at(index * 61 * MINUTE), endAt: at(index * 61 * MINUTE + MINUTE) }));
    }
    const layout = createTimelineLayout(timeline({ spans }));
    const folded = layout.gaps.filter(gap => gap.collapsed);
    expect(folded).toHaveLength(9);
    for (const gap of folded) expect(width(gap)).toBeCloseTo(3, 9);
    expect(folded.reduce((sum, gap) => sum + width(gap), 0)).toBeCloseTo(27, 6);
  });

  it("keeps one break expanded while its sibling stays folded", () => {
    const spans = [
      span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
      span({ spanId: "b", startAt: at(10 * MINUTE + 2 * HOUR), endAt: at(20 * MINUTE + 2 * HOUR) }),
      span({ spanId: "c", startAt: at(20 * MINUTE + 4 * HOUR), endAt: at(30 * MINUTE + 4 * HOUR) }),
    ];
    const folded = createTimelineLayout(timeline({ spans }));
    expect(folded.gaps.map(gap => gap.collapsed)).toEqual([true, true]);

    const expanded = createTimelineLayout(timeline({ spans }), new Set([folded.gaps[0]!.id]));
    expect(expanded.gaps[1]!.collapsed).toBe(true);
    expect(width(expanded.gaps[1]!)).toBeCloseTo(3, 9);
    // Domain 270min: 30min occupied + 120min expanded gap stay linear over the remaining 97%.
    expect(width(expanded.gaps[0]!)).toBeCloseTo((120 / 150) * 97, 6);
    expect(expanded.gaps[0]!.fromPercent).toBeCloseTo((10 / 150) * 97, 6);
  });

  it("keeps the real scale when nothing unfolded remains to fold against", () => {
    const layout = createTimelineLayout(timeline({ events: [event(0), event(2 * HOUR)] }));
    expect(layout.canFold).toBe(true);
    expect(layout.gaps).toHaveLength(1);
    expect(layout.gaps[0]!.collapsed).toBe(false);
    expect(layout.gaps[0]!.fromPercent).toBe(0);
    expect(layout.gaps[0]!.toPercent).toBe(100);
    expect(layout.position(at(HOUR))).toBeCloseTo(50, 6);

    const markers = createTimelineLayout(timeline({
      events: [event(0), event(HOUR), event(2 * HOUR)],
      observedAt: at(2 * HOUR),
    }));
    expect(markers.gaps.map(gap => gap.collapsed)).toEqual([false, false]);
    expect(markers.gaps.map(width)).toEqual([50, 50]);

    const hourly = createTimelineLayout(timeline({
      events: Array.from({ length: 21 }, (_, index) => event(index * HOUR, { seq: index })),
      observedAt: at(20 * HOUR),
    }));
    expect(hourly.gaps).toHaveLength(20);
    expect(hourly.gaps.every(gap => !gap.collapsed)).toBe(true);
    for (const gap of hourly.gaps) expect(width(gap)).toBeCloseTo(5, 6);
  });

  it("keeps folded positions monotonic and continuous at every gap boundary", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "b", startAt: at(20 * MINUTE), endAt: at(30 * MINUTE) }),
        span({ spanId: "c", startAt: at(200 * MINUTE), endAt: at(210 * MINUTE) }),
      ],
      events: [event(100 * MINUTE)],
    }));
    expect(layout.gaps).toHaveLength(3);
    let previous = -1;
    for (let offset = -60; offset <= 300; offset += 1) {
      const position = layout.position(at(offset * MINUTE));
      expect(position).not.toBeNull();
      expect(position!).toBeGreaterThanOrEqual(previous);
      expect(position!).toBeGreaterThanOrEqual(0);
      expect(position!).toBeLessThanOrEqual(100);
      previous = position!;
    }
    expect(layout.position(layout.startMs)).toBe(0);
    expect(layout.position(layout.endMs)).toBe(100);
    for (const gap of layout.gaps) {
      expect(layout.position(gap.startMs)).toBeCloseTo(gap.fromPercent, 9);
      expect(layout.position(gap.endMs)).toBeCloseTo(gap.toPercent, 9);
      const middle = layout.position((gap.startMs + gap.endMs) / 2);
      expect(middle!).toBeGreaterThanOrEqual(gap.fromPercent);
      expect(middle!).toBeLessThanOrEqual(gap.toPercent);
    }
  });
});

describe("objective timeline folding eligibility", () => {
  it("never folds an incomplete or filtered scope and keeps durations linear", () => {
    const layout = createTimelineLayout(timeline({
      scopeComplete: false,
      spans: [
        span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "b", startAt: at(20 * MINUTE), endAt: at(30 * MINUTE) }),
        span({ spanId: "c", startAt: at(60 * MINUTE), endAt: at(70 * MINUTE) }),
      ],
    }));
    expect(layout.canFold).toBe(false);
    expect(layout.gaps.map(gap => gap.collapsed)).toEqual([false, false]);
    expect(layout.gaps.map(bounds)).toEqual([[10 * MINUTE, 20 * MINUTE], [30 * MINUTE, 60 * MINUTE]]);
    expect(width(layout.gaps[1]!) / width(layout.gaps[0]!)).toBeCloseTo(3, 6);
    expect(layout.gaps[0]!.fromPercent).toBeCloseTo((10 / 70) * 100, 6);
  });

  it("refuses folding when the caller passes an explicitly truncated read", () => {
    const slice = twoAttempts(2 * HOUR);
    const truncated = { ...slice, truncated: { rows: false, spans: true, events: false } };
    const layout = createTimelineLayout(truncated as unknown as TimelineSlice);
    expect(layout.canFold).toBe(false);
    expect(layout.gaps[0]!.collapsed).toBe(false);
    expect(layout.position(layout.endMs)).toBe(100);

    const complete = { ...slice, truncated: { rows: false, spans: false, events: false } };
    const intact = createTimelineLayout(complete as unknown as TimelineSlice);
    expect(intact.canFold).toBe(true);
    expect(intact.gaps[0]!.collapsed).toBe(true);
  });

  it("refuses folding for a clock-skewed span but keeps its recorded occupancy", () => {
    const layout = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "skewed", startAt: at(0), endAt: at(10 * MINUTE), clockSkew: true }),
        span({ spanId: "second", startAt: at(2 * HOUR), endAt: at(2 * HOUR + 10 * MINUTE) }),
      ],
    }));
    expect(layout.canFold).toBe(false);
    expect(layout.gaps.map(bounds)).toEqual([[10 * MINUTE, 2 * HOUR]]);
    expect(layout.gaps[0]!.collapsed).toBe(false);
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE + 2 * HOUR + 10 * MINUTE);
  });

  it("refuses folding when any span extent or marker instant is unusable", () => {
    const malformedMarker = createTimelineLayout(timeline({
      spans: [span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) })],
      events: [event(0, { at: "2026-13-45T99:99:99Z" })],
    }));
    expect(malformedMarker.canFold).toBe(false);
    expect(malformedMarker.position("2026-13-45T99:99:99Z")).toBeNull();

    const finishedWithoutEnd = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "lost", startAt: at(20 * MINUTE), endAt: null, state: "completed", shutdownConfirmed: true }),
      ],
      observedAt: at(10 * HOUR),
    }));
    expect(finishedWithoutEnd.canFold).toBe(false);
    expect(finishedWithoutEnd.endMs).toBe(BASE + 20 * MINUTE);
    expect(finishedWithoutEnd.position(at(10 * HOUR))).toBe(100);
  });

  it("marks malformed, offset-free and nonexistent dates unusable", () => {
    const layout = createTimelineLayout(twoAttempts(2 * HOUR));
    expect(layout.position("not a timestamp")).toBeNull();
    expect(layout.position("")).toBeNull();
    expect(layout.position(null)).toBeNull();
    expect(layout.position(undefined as unknown as null)).toBeNull();
    expect(layout.position(Number.NaN)).toBeNull();
    expect(layout.position(Number.POSITIVE_INFINITY)).toBeNull();
    expect(layout.position("2026-09-26T00:00:00")).toBeNull();
    expect(layout.position("2026-02-30T00:00:00Z")).toBeNull();
    expect(layout.position("2026-09-26T24:00:00Z")).toBeNull();
  });

  it("treats reversed span endpoints as recorded instants without inventing a duration", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ startAt: at(10 * MINUTE), endAt: at(0), state: "completed", shutdownConfirmed: true })],
    }));
    expect(layout.canFold).toBe(false);
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE + 10 * MINUTE);
    expect(layout.gaps.map(bounds)).toEqual([[0, 10 * MINUTE]]);
    expect(layout.position(at(5 * MINUTE))).toBeCloseTo(50, 6);
  });

  it("does not turn a malformed end into an active tail even when uncertain", () => {
    const layout = createTimelineLayout(timeline({
      spans: [span({ startAt: at(0), endAt: "pending", state: "executing", uncertain: true, shutdownConfirmed: false })],
      observedAt: at(6 * HOUR),
    }));
    expect(layout.canFold).toBe(false);
    expect(layout.startMs).toBe(BASE);
    expect(layout.endMs).toBe(BASE);
    expect(layout.gaps).toEqual([]);
  });
});

describe("objective timeline layout coordinates", () => {
  it("accepts epoch milliseconds and ISO strings for the same instant", () => {
    const layout = createTimelineLayout(twoAttempts(2 * HOUR));
    expect(layout.position(BASE + 10 * MINUTE)).toBe(layout.position(at(10 * MINUTE)));
    expect(layout.position("2026-09-26T02:00:00+02:00")).toBe(layout.position(at(0)));
    expect(layout.position("2026-09-26 00:00:00.000Z")).toBe(0);
  });

  it("keeps gap ids deterministic from original boundaries across refreshes", () => {
    const make = () => timeline({
      spans: [
        span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
        span({ spanId: "b", startAt: at(2 * HOUR), endAt: at(2 * HOUR + 10 * MINUTE) }),
      ],
      events: [event(HOUR)],
    });
    const first = createTimelineLayout(make());
    const second = createTimelineLayout(make());
    expect(second.gaps.map(gap => gap.id)).toEqual(first.gaps.map(gap => gap.id));
    expect(new Set(first.gaps.map(gap => gap.id)).size).toBe(first.gaps.length);
    for (const gap of first.gaps) {
      expect(gap.id).toContain(String(gap.startMs));
      expect(gap.id).toContain(String(gap.endMs));
    }
    expect(first.gaps.map(bounds)).toEqual([[10 * MINUTE, HOUR], [HOUR, 2 * HOUR]]);

    const moved = createTimelineLayout(timeline({
      spans: [
        span({ spanId: "a", startAt: at(0), endAt: at(30 * MINUTE) }),
        span({ spanId: "b", startAt: at(2 * HOUR), endAt: at(2 * HOUR + 10 * MINUTE) }),
      ],
      events: [event(HOUR)],
    }));
    expect(moved.gaps[0]!.id).not.toBe(first.gaps[0]!.id);
    expect(moved.gaps[1]!.id).toBe(first.gaps[1]!.id);
  });

  it("keeps existing gap ids when a refresh appends a later record", () => {
    const spans = twoAttempts(2 * HOUR).spans;
    const before = createTimelineLayout(timeline({ spans }));
    expect(before.gaps).toHaveLength(1);
    const after = createTimelineLayout(timeline({ spans, events: [event(6 * HOUR)] }));
    expect(after.gaps).toHaveLength(2);
    expect(after.gaps[0]!.id).toBe(before.gaps[0]!.id);
    expect(after.gaps[1]!.collapsed).toBe(true);
  });

  it("produces the same layout whatever the input order", () => {
    const spans = [
      span({ spanId: "a", startAt: at(0), endAt: at(10 * MINUTE) }),
      span({ spanId: "b", startAt: at(2 * HOUR), endAt: at(2 * HOUR + 10 * MINUTE) }),
      span({ spanId: "c", startAt: at(2 * HOUR + 30 * MINUTE), endAt: at(2 * HOUR + 40 * MINUTE) }),
    ];
    const events = [event(HOUR)];
    const forward = createTimelineLayout(timeline({ spans, events }));
    const reversed = createTimelineLayout(timeline({ spans: [...spans].reverse(), events: [...events].reverse() }));
    expect(reversed.gaps.map(gap => [gap.id, gap.collapsed, gap.fromPercent, gap.toPercent]))
      .toEqual(forward.gaps.map(gap => [gap.id, gap.collapsed, gap.fromPercent, gap.toPercent]));
  });

  it("clamps out-of-range instants instead of producing NaN", () => {
    const layout = createTimelineLayout(twoAttempts(2 * HOUR));
    expect(layout.position(BASE - 10 * HOUR)).toBe(0);
    expect(layout.position(BASE + 10 * HOUR)).toBe(100);
    expect(layout.position(at(20 * MINUTE + 2 * HOUR))).toBe(100);
    for (const gap of layout.gaps) {
      expect(Number.isFinite(gap.fromPercent)).toBe(true);
      expect(Number.isFinite(gap.toPercent)).toBe(true);
      expect(gap.fromPercent).toBeGreaterThanOrEqual(0);
      expect(gap.toPercent).toBeLessThanOrEqual(100);
      expect(width(gap)).toBeGreaterThan(0);
    }
  });
});

describe("objective timeline layout invariants", () => {
  it("holds every documented invariant on generated timelines", () => {
    let seed = 20260926;
    const random = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    for (let round = 0; round < 120; round += 1) {
      const spans: TimelineSpan[] = [];
      const events: TimelineEvent[] = [];
      let cursor = 0;
      const spanCount = Math.floor(random() * 6);
      for (let index = 0; index < spanCount; index += 1) {
        const start = cursor + Math.floor(random() * 90) * MINUTE;
        const duration = Math.floor(random() * 90) * MINUTE;
        const open = random() < 0.25;
        spans.push(span({
          spanId: `r${round}-s${index}`,
          startAt: at(start),
          endAt: open ? null : at(start + duration),
          state: open ? "running" : "completed",
          shutdownConfirmed: open ? false : true,
          uncertain: random() < 0.2,
          clockSkew: random() < 0.1,
        }));
        cursor = start + duration + Math.floor(random() * 300) * MINUTE;
      }
      const eventCount = Math.floor(random() * 4);
      for (let index = 0; index < eventCount; index += 1) {
        events.push(event(Math.floor(random() * 900) * MINUTE, { seq: index }));
      }
      const scopeComplete = random() < 0.75;
      const slice = timeline({ spans, events, observedAt: at(900 * MINUTE), scopeComplete });
      const first = createTimelineLayout(slice);
      const expanded = new Set<string>();
      if (first.gaps.length > 0 && random() < 0.4) {
        expanded.add(first.gaps[Math.floor(random() * first.gaps.length)]!.id);
      }
      const layout = createTimelineLayout(slice, expanded);

      const { startMs, endMs } = layout;
      if (startMs === null || endMs === null) {
        expect(layout.gaps).toEqual([]);
        continue;
      }
      expect(startMs).toBeLessThanOrEqual(endMs);
      if (startMs === endMs) {
        expect(layout.position(startMs)).toBe(50);
      } else {
        expect(layout.position(startMs)).toBe(0);
        expect(layout.position(endMs)).toBe(100);
      }

      let previous = -1;
      for (let step = 0; step <= 60; step += 1) {
        const position = layout.position(startMs + ((endMs - startMs) * step) / 60);
        expect(Number.isFinite(position ?? Number.NaN)).toBe(true);
        expect(position!).toBeGreaterThanOrEqual(previous);
        expect(position!).toBeGreaterThanOrEqual(0);
        expect(position!).toBeLessThanOrEqual(100);
        previous = position!;
      }

      const ids = new Set<string>();
      const candidates = layout.gaps.filter(gap =>
        layout.canFold && !expanded.has(gap.id) && gap.endMs - gap.startMs > TIMELINE_FOLD_THRESHOLD_MS);
      // With no interval keeping its real width (an all-empty domain) folding is
      // skipped entirely, so folded breaks can never have to absorb the whole axis.
      const allEmpty = candidates.length > 0
        && candidates.length === layout.gaps.length
        && layout.gaps.reduce((sum, gap) => sum + (gap.endMs - gap.startMs), 0) === endMs - startMs;
      let collapsedTotal = 0;
      for (const gap of layout.gaps) {
        expect(ids.has(gap.id)).toBe(false);
        ids.add(gap.id);
        expect(gap.endMs).toBeGreaterThan(gap.startMs);
        expect(Number.isFinite(gap.fromPercent)).toBe(true);
        expect(Number.isFinite(gap.toPercent)).toBe(true);
        expect(gap.fromPercent).toBeGreaterThanOrEqual(0);
        expect(gap.toPercent).toBeLessThanOrEqual(100);
        expect(gap.toPercent).toBeGreaterThan(gap.fromPercent);
        expect(layout.position(gap.startMs)).toBeCloseTo(gap.fromPercent, 9);
        expect(layout.position(gap.endMs)).toBeCloseTo(gap.toPercent, 9);
        if (gap.collapsed) {
          expect(layout.canFold).toBe(true);
          expect(expanded.has(gap.id)).toBe(false);
          expect(allEmpty).toBe(false);
          expect(gap.endMs - gap.startMs).toBeGreaterThan(TIMELINE_FOLD_THRESHOLD_MS);
          expect(width(gap)).toBeLessThanOrEqual(3 + 1e-9);
          collapsedTotal += width(gap);
        } else if (candidates.includes(gap) && !allEmpty) {
          expect(gap.collapsed).toBe(true);
        }
      }
      for (let index = 1; index < layout.gaps.length; index += 1) {
        expect(layout.gaps[index]!.startMs).toBeGreaterThanOrEqual(layout.gaps[index - 1]!.endMs);
      }
      expect(collapsedTotal).toBeLessThanOrEqual(40 + 1e-9);
      if (allEmpty) expect(layout.gaps.every(gap => !gap.collapsed)).toBe(true);
      if (!layout.canFold) expect(layout.gaps.every(gap => !gap.collapsed)).toBe(true);
    }
  });
});
