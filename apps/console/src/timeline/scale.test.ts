import { describe, expect, it } from "vitest";
import type { MacroTaskTimeline, TimelineEvent, TimelineSpan } from "../macro-task-types";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import type { ScaleInput } from "./scale";
import { createTimelineScale, scaleWithExpectedTimes, timelineScaleInput } from "./scale";

const MINUTE = 60_000;
const BASE = Date.parse("2026-10-17T00:00:00Z");
const ms = (minutes: number) => BASE + minutes * MINUTE;
const at = (minutes: number) => new Date(ms(minutes)).toISOString();
const span = (overrides: Partial<TimelineSpan> = {}): TimelineSpan => ({
  spanId: "span", runId: "run", kind: "execution", startAt: at(0), endAt: at(10),
  state: "finished", attemptId: null, turnId: null, turnIndex: null, requestId: null,
  buddy: null, expectedSeconds: 1800, reminders: null, inquiries: [],
  shutdownConfirmed: true, uncertain: false, clockSkew: false, ...overrides,
});
const event = (minutes: number): TimelineEvent => ({
  seq: 1, runId: "run", kind: "accept", at: at(minutes), label: "", summary: "",
  actor: null, attemptId: null, requestId: null, artifactId: null,
});
const timeline = (overrides: Partial<MacroTaskTimeline> = {}): MacroTaskTimeline => ({
  macroTask: macroTaskTimelineFixture().macroTask, observedAt: at(120), cursor: 0,
  rows: [], spans: [span()], events: [], totals: { rows: 0, allRows: 0, spans: 1, events: 0 },
  truncated: { rows: false, spans: false, events: false }, filtered: false, scopeComplete: true,
  ...overrides,
});

const interval = (start: number, end: number) => ({ startMs: ms(start), endMs: ms(end) });
const input = (end: number, busy: number[][], instants: number[] = [], canFold = true): ScaleInput => ({
  domain: interval(0, end), busy: busy.map(([start, end]) => interval(start!, end!)),
  instants: [...new Set([0, end, ...busy.flat(), ...instants])].map(ms), canFold, nowAtEnd: false,
});

describe("createTimelineScale", () => {
  it("is a straight line when overlapping and nested bars occupy the whole domain", () => {
    const scale = createTimelineScale(input(60, [[20, 60], [0, 30], [10, 15]]), 600);
    expect(scale.breaks).toEqual([]);
    expect(scale.widthPx).toBe(600);
    expect(scale.pixelsPerMinute).toBe(10);
    for (let minute = 0; minute <= 60; minute += 1) expect(scale.toX(ms(minute))).toBeCloseTo(minute * 10);
    expect(scale.toX(ms(0))).toBe(0);
    expect(scale.toX(ms(60))).toBe(scale.widthPx);
  });

  it("fits a long idle interval into a fixed 54px break", () => {
    const scale = createTimelineScale(input(1020, [[0, 10], [1010, 1020]]), 600);
    expect(scale.breaks).toEqual([{ ...interval(10, 1010), startX: 273, endX: 327 }]);
    expect(scale.widthPx).toBeCloseTo(600);
    expect(scale.pixelsPerMinute).toBeCloseTo(27.3);
  });

  it("uses pixel width, including strict equality, with no duration threshold", () => {
    const facts = input(102, [[0, 1], [101, 102]]);
    const exact = createTimelineScale(facts, 600, 0.54);
    expect(exact.breaks).toEqual([]);
    const wider = createTimelineScale(facts, 600, 0.541);
    expect(wider.breaks).toHaveLength(1);
    expect(wider.breaks[0]!.endX - wider.breaks[0]!.startX).toBe(54);
    const shortGap = createTimelineScale(input(2.5, [[0, 1], [1.5, 2.5]]), 600, 120);
    expect(shortGap.breaks).toHaveLength(1);
  });

  it("folds more intervals when zooming, keeps breaks fixed and grows monotonically", () => {
    const facts = input(123, [[0, 1], [101, 102], [122, 123]]);
    const small = createTimelineScale(facts, 600, 1);
    const large = createTimelineScale(facts, 600, 10);
    expect(small.breaks).toHaveLength(1);
    expect(large.breaks).toHaveLength(2);
    expect(large.pixelsPerMinute).toBe(10);
    expect(large.widthPx).toBeGreaterThan(small.widthPx);
    expect(large.breaks.map(gap => gap.endX - gap.startX)).toEqual([54, 54]);
    let previous = -1;
    for (let minute = 0; minute <= 123; minute += 0.25) {
      const x = large.toX(ms(minute))!;
      expect(x).toBeGreaterThan(previous);
      expect(x).toBeGreaterThanOrEqual(small.toX(ms(minute))!);
      previous = x;
    }
  });

  it("never folds when the input disallows it", () => {
    const facts = input(1020, [[0, 10], [1010, 1020]], [], false);
    for (const ppm of [undefined, 100]) {
      const scale = createTimelineScale(facts, 600, ppm);
      expect(scale.breaks).toEqual([]);
      expect(scale.toX(ms(510))).toBeCloseTo(scale.widthPx / 2);
    }
  });

  it("keeps a wait-only manual scale proportional even if folding was requested", () => {
    const scale = createTimelineScale(input(120, [], [30, 60]), 600, 100);
    expect(scale.breaks).toEqual([]);
    expect(scale.toX(ms(60))).toBeCloseTo(scale.widthPx / 2);
  });

  it("round-trips both coordinates through real intervals and break interiors", () => {
    const facts = input(10000, [[0, 1.7], [501.2, 505.4], [9997.3, 10000]], [800, 2000]);
    for (const ppm of [undefined, 0.1, 10, 100]) {
      const scale = createTimelineScale(facts, 700, ppm);
      for (let i = 0; i <= 1000; i += 1) {
        const time = ms(i * 10), x = scale.widthPx * i / 1000;
        expect(Math.abs(scale.toTime(scale.toX(time)!)! - time)).toBeLessThanOrEqual(1000);
        expect(Math.abs(scale.toX(scale.toTime(x)!)! - x)).toBeLessThanOrEqual(0.5);
      }
      for (const gap of scale.breaks) {
        expect(scale.toX((gap.startMs + gap.endMs) / 2)).toBeCloseTo((gap.startX + gap.endX) / 2);
      }
    }
  });

  it("keeps an ongoing wait-only domain proportional, preserving the wait's endpoint bindings", () => {
    const facts = timelineScaleInput(timeline({ spans: [span({
      kind: "host", startAt: at(10), endAt: null, state: "open", shutdownConfirmed: null,
    })] }));
    const scale = createTimelineScale(facts, 600);
    expect(facts.busy).toEqual([]);
    expect(facts.canFold).toBe(false);
    expect(facts.nowAtEnd).toBe(true);
    expect(scale.breaks).toEqual([]);
    expect(scale.widthPx).toBe(600);
    expect(scale.toX(ms(10))).toBe(0);
    expect(scale.toX(ms(120))).toBe(scale.widthPx);
    expect(scale.toTime(0)).toBe(ms(10));
    expect(scale.toTime(scale.widthPx)).toBe(ms(120));
    expect(scale.toX(ms(65))).toBe(300);
  });

  it("never folds queue and Host waits with a marker but no execution, even at manual zoom", () => {
    const facts = timelineScaleInput(timeline({
      spans: [
        span({ kind: "queue", state: "queued", startAt: at(0), endAt: at(30), shutdownConfirmed: null }),
        span({ kind: "host", state: "open", startAt: at(30), endAt: null, shutdownConfirmed: null }),
      ],
      events: [event(60)],
    }));
    expect(facts.busy).toEqual([]);
    expect(facts.canFold).toBe(false);
    for (const ppm of [undefined, 100]) {
      const scale = createTimelineScale(facts, 600, ppm);
      expect(scale.breaks).toEqual([]);
      expect(scale.toX(ms(60))).toBeCloseTo(scale.widthPx / 2);
    }
  });

  it("reserves 64px for proportional time when breaks overflow the window", () => {
    const bars = Array.from({ length: 6 }, (_, i) => [i * 101, i * 101 + 1]);
    const scale = createTimelineScale(input(506, bars), 100);
    expect(scale.breaks).toHaveLength(5);
    expect(scale.widthPx).toBeCloseTo(5 * 54 + 64);
    expect(scale.widthPx).toBeGreaterThan(100);
    expect(scale.pixelsPerMinute * 6).toBeCloseTo(64);
  });

  it("splits idle intervals at markers without hiding concurrent work", () => {
    const scale = createTimelineScale(input(1000, [[0, 5], [995, 1000]], [200, 500]), 600, 10);
    expect(scale.breaks.map(gap => [gap.startMs, gap.endMs])).toEqual([
      [ms(5), ms(200)], [ms(200), ms(500)], [ms(500), ms(995)],
    ]);
    const occupied = createTimelineScale(input(1000, [[0, 1000]], [200, 500]), 600, 10);
    expect(occupied.breaks).toEqual([]);
  });

  it("keeps fit decisions consistent with the final pixels-per-minute", () => {
    // Mixed durations force several rounds of scale growth, including the 64px floor.
    for (let count = 1; count <= 12; count += 1) {
      const bars: number[][] = [], gaps: number[][] = [];
      let cursor = 0;
      for (let i = 0; i < count; i += 1) {
        bars.push([cursor, cursor + 1]);
        cursor += 1;
        gaps.push([cursor, cursor + 3 ** i]);
        cursor += 3 ** i;
      }
      bars.push([cursor, cursor + 1]);
      for (const viewport of [40, 100, 600, 2000]) {
        const scale = createTimelineScale(input(cursor + 1, bars), viewport);
        for (const [start, end] of gaps) {
          const folded = scale.breaks.some(gap => gap.startMs === ms(start!));
          expect(folded).toBe((end! - start!) * scale.pixelsPerMinute > 54);
        }
        expect(scale.widthPx - scale.breaks.length * 54).toBeGreaterThanOrEqual(64 - 1e-7);
        if (scale.widthPx > viewport + 1e-7) {
          expect(scale.widthPx - scale.breaks.length * 54).toBeCloseTo(64);
        } else expect(scale.widthPx).toBeCloseTo(viewport);
      }
    }
  });

  it("defines empty and point domains without fabricated time", () => {
    const empty = createTimelineScale({ ...input(0, []), domain: null }, 600);
    expect(empty.widthPx).toBe(0);
    expect(empty.toX(ms(0))).toBeNull();
    expect(empty.toTime(0)).toBeNull();
    expect(empty.ticks).toEqual([]);
    const point = createTimelineScale(input(0, []), 600);
    expect(point.widthPx).toBe(0);
    expect(point.toX(ms(0))).toBe(0);
    expect(point.toTime(0)).toBe(ms(0));
    expect(point.ticks).toEqual([{ atMs: ms(0), labeled: true }]);
  });
});

describe("timeline ticks", () => {
  it.each([
    [60, 1], [28, 2], [12, 5], [6, 10], [4, 15], [2, 30],
    [1, 60], [0.5, 120], [0.25, 240], [0.125, 480], [0.08, 720], [0.04, 1440],
  ])("uses the smallest spacing >= 56px at %s pixels/minute", (ppm, step) => {
    const scale = createTimelineScale(input(3 * 1440, [[0, 3 * 1440]]), 600, ppm);
    const regular = scale.ticks.slice(1);
    expect(regular.length).toBeGreaterThan(1);
    for (let i = 1; i < regular.length; i += 1) {
      expect(regular[i]!.atMs - regular[i - 1]!.atMs).toBe(step * MINUTE);
    }
  });

  it("omits internal ticks when no listed interval reaches 56px", () => {
    const scale = createTimelineScale(input(10000, [[0, 10000]]), 100, 0.01);
    expect(scale.ticks).toEqual([{ atMs: ms(0), labeled: true }]);
  });

  it("labels every section start and puts no ticks inside a break", () => {
    const facts = input(1055, [[0, 10], [1000, 1010], [1050, 1055]]);
    const scale = createTimelineScale(facts, 600, 10);
    for (const start of [ms(0), ...scale.breaks.map(gap => gap.endMs)]) {
      expect(scale.ticks).toContainEqual({ atMs: start, labeled: true });
    }
    for (const gap of scale.breaks) {
      expect(scale.ticks.some(tick => tick.atMs > gap.startMs && tick.atMs < gap.endMs)).toBe(false);
    }
  });

  it("spaces labels by 72px and keeps regular labels 72px from now", () => {
    const scale = createTimelineScale({ ...input(20, [[0, 20]]), nowAtEnd: true }, 600, 30);
    const labels = scale.ticks.filter(tick => tick.labeled);
    expect(labels.map(tick => (tick.atMs - BASE) / MINUTE)).toEqual([0, 4, 8, 12, 16]);
    for (let i = 1; i < labels.length; i += 1) {
      expect(scale.toX(labels[i]!.atMs)! - scale.toX(labels[i - 1]!.atMs)!).toBeGreaterThanOrEqual(72);
      expect(scale.widthPx - scale.toX(labels[i]!.atMs)!).toBeGreaterThanOrEqual(72);
    }
    expect(scale.ticks).toContainEqual({ atMs: ms(18), labeled: false });
    const finished = createTimelineScale(input(20, [[0, 20]]), 600, 30);
    expect(finished.ticks).toContainEqual({ atMs: ms(20), labeled: true });
  });

  it("aligns integer multiples to local midnight, including fractional timezone offsets", () => {
    const old = process.env.TZ;
    process.env.TZ = "Asia/Kathmandu";
    try {
      const scale = createTimelineScale(input(2 * 1440, [[0, 2 * 1440]]), 600, 0.5);
      expect(scale.ticks[0]).toEqual({ atMs: BASE, labeled: true });
      for (const tick of scale.ticks.slice(1)) {
        const date = new Date(tick.atMs);
        expect((date.getHours() * 60 + date.getMinutes()) % 120).toBe(0);
        expect(date.getSeconds()).toBe(0);
      }
    } finally {
      if (old === undefined) delete process.env.TZ;
      else process.env.TZ = old;
    }
  });
});

describe("timelineScaleInput", () => {
  it("extends a delivered row's confirmed execution to observedAt for its open acceptance wait", () => {
    const row = { ...macroTaskTimelineFixture().rows[0]!, runId: "run", state: "delivered",
      acceptedAt: null, reviewRejectedAt: null };
    const facts = timelineScaleInput(timeline({ rows: [row] }));
    expect(facts.domain).toEqual(interval(0, 120));
    expect(facts.instants).toEqual([ms(0), ms(10), ms(120)]);
    expect(facts.nowAtEnd).toBe(true);
    expect(facts.canFold).toBe(true);
    expect(createTimelineScale(facts, 600).breaks).toEqual([
      { ...interval(10, 120), startX: 546, endX: 600 },
    ]);
  });

  it("includes recorded acceptance and rejection instants without extending a settled row to now", () => {
    const row = { ...macroTaskTimelineFixture().rows[0]!, runId: "run", state: "accepted",
      acceptedAt: at(80), reviewRejectedAt: at(40) };
    const facts = timelineScaleInput(timeline({ rows: [row] }));
    expect(facts.domain).toEqual(interval(0, 80));
    expect(facts.instants).toEqual([0, 10, 40, 80].map(ms));
    expect(facts.nowAtEnd).toBe(false);
  });

  it("does not extend delivery when its final execution lacks confirmed shutdown", () => {
    const row = { ...macroTaskTimelineFixture().rows[0]!, runId: "run", state: "delivered",
      acceptedAt: null, reviewRejectedAt: null };
    const facts = timelineScaleInput(timeline({ rows: [row], spans: [span({ shutdownConfirmed: false })] }));
    expect(facts.domain).toEqual(interval(0, 10));
    expect(facts.nowAtEnd).toBe(false);
    expect(facts.canFold).toBe(false);
  });

  it("keeps bars busy and waits as instants, including Host markers", () => {
    const input = timelineScaleInput(timeline({ spans: [
      span({ kind: "queue", startAt: at(0), endAt: at(1) }),
      span({ kind: "routing", startAt: at(1), endAt: at(2) }),
      span({ startAt: at(2), endAt: at(10) }),
      span({ kind: "host", startAt: at(10), endAt: at(30) }),
    ], events: [event(40)] }));
    expect(input.domain).toEqual({ startMs: ms(0), endMs: ms(40) });
    expect(input.busy).toEqual([
      { startMs: ms(1), endMs: ms(2) }, { startMs: ms(2), endMs: ms(10) },
    ]);
    expect(input.instants).toEqual([0, 1, 2, 10, 30, 40].map(ms));
    expect(input.canFold).toBe(true);
    expect(input.nowAtEnd).toBe(false);
  });

  it.each([
    ["queue", "queued"], ["routing", "starting"], ["routing", "executing"],
    ["routing", "finalizing"], ["routing", "uncertain"],
    ["execution", "starting"], ["execution", "executing"],
    ["execution", "finalizing"], ["execution", "uncertain"], ["host", "open"],
  ] as const)("ends ongoing %s in state %s at observedAt", (kind, state) => {
    const input = timelineScaleInput(timeline({ spans: [span({
      kind, endAt: null, state, shutdownConfirmed: null,
    })] }));
    expect(input.domain).toEqual({ startMs: ms(0), endMs: ms(120) });
    expect(input.instants).toEqual([ms(0), ms(120)]);
    expect(input.busy).toEqual(kind === "queue" || kind === "host" ? [] : [input.domain]);
    expect(input.nowAtEnd).toBe(true);
    expect(input.canFold).toBe(kind === "routing" || kind === "execution");
  });

  it.each([
    ["queue", "executing"], ["host", "queued"], ["execution", "open"],
    ["routing", "admitted"], ["execution", " Executing "], ["host", "OPEN"],
  ] as const)("does not guess that %s in state %s is open", (kind, state) => {
    const facts = timelineScaleInput(timeline({ spans: [span({
      kind, endAt: null, state, shutdownConfirmed: null,
    })] }));
    expect(facts.domain).toEqual(interval(0, 0));
    expect(facts.nowAtEnd).toBe(false);
    expect(facts.canFold).toBe(false);
  });

  it("reserves an uncertain execution through observedAt until shutdown is confirmed", () => {
    const input = timelineScaleInput(timeline({ spans: [span({ uncertain: true, shutdownConfirmed: false })] }));
    expect(input.busy).toEqual([{ startMs: ms(0), endMs: ms(120) }]);
    expect(input.instants).toEqual([ms(0), ms(10), ms(120)]);
    expect(input.nowAtEnd).toBe(true);
  });

  it.each([
    { scopeComplete: false }, { filtered: true },
    ...(["rows", "spans", "events"] as const).map(key => ({ truncated: { rows: false, spans: false, events: false, [key]: true } })),
    { spans: [span({ startAt: at(20), endAt: at(10) })] },
    { spans: [span({ clockSkew: true })] },
    { spans: [span({ startAt: null })] },
    { spans: [span({ endAt: null })] },
    { events: [{ ...event(20), at: "unavailable" }] },
    { spans: [span({ endAt: null, shutdownConfirmed: false, state: "unknown" })] },
    { spans: [span({ endAt: null, shutdownConfirmed: false, state: "executing" })], observedAt: at(-1) },
    { spans: [span({ endAt: null, shutdownConfirmed: false, state: "executing" })], observedAt: "unavailable" },
  ])("refuses folding with incomplete or untrustworthy facts: %j", overrides => {
    expect(timelineScaleInput(timeline(overrides)).canFold).toBe(false);
  });

  it("has no domain without available instants", () => {
    expect(timelineScaleInput(timeline({ spans: [] })).domain).toBeNull();
  });
});


describe("future expected time", () => {
  it("keeps observed time and real breaks intact while placing a future expected mark on the same ratio", () => {
    const live = span({ startAt: at(100), endAt: null, state: "executing", shutdownConfirmed: false, expectedSeconds: 2400 });
    const facts = timelineScaleInput(timeline({ spans: [span(), live] }));
    const base = createTimelineScale(facts, 600);
    const extended = scaleWithExpectedTimes(facts, 600, [live]);
    expect(extended.breaks).toEqual(base.breaks);
    expect(extended.toX(ms(120))).toBe(base.widthPx);
    expect(extended.widthPx - base.widthPx).toBeCloseTo(base.pixelsPerMinute * 20);
    expect(extended.toTime(extended.widthPx)).toBe(ms(140));
    expect(extended.toX(ms(130))! - extended.toX(ms(120))!).toBeCloseTo(base.pixelsPerMinute * 10);
  });
});
