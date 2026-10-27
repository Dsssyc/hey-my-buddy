import { describe, expect, it } from "vitest";
import { macroTaskMetrics } from "./macro-task-metrics";
import { macroTaskTimelineFixture, macroTaskSummary } from "./macro-task-fixtures";
import type { MacroTaskTimeline, TimelineSpan } from "./macro-task-types";

const minutes = (count: number) => count * 60000;

function withSpans(spans: TimelineSpan[], overrides: Partial<MacroTaskTimeline> = {}): MacroTaskTimeline {
  const timeline = macroTaskTimelineFixture(overrides);
  timeline.rows = timeline.rows.filter(row => spans.some(span => span.runId === row.runId));
  timeline.spans = spans;
  timeline.events = [];
  timeline.totals = { rows: timeline.rows.length, spans: spans.length, events: 0, allRows: timeline.rows.length };
  return timeline;
}

describe("macroTask metrics", () => {
  it("keeps the union below the cumulative sum under concurrency, and both below the total span", () => {
    // Two overlapping 10-minute executions in one 30-minute wall-clock window.
    const timeline = withSpans([
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "a", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:10:00Z", shutdownConfirmed: true, state: "finished" },
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "b", runId: "r2", kind: "execution",
        startAt: "2026-09-26T01:05:00Z", endAt: "2026-09-26T01:15:00Z", shutdownConfirmed: true, state: "finished" },
    ]);
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.unionMs).toBe(minutes(15));
    expect(metrics.sumMs).toBe(minutes(20));
    expect(metrics.segmentCount).toBe(2);
    expect(metrics.totalSpanMs).toBe(minutes(15));
    expect(metrics.limitations.map(entry => entry.key)).not.toContain("unconfirmed-end");
  });

  it("counts only execution: queue, routing and waiting-Host never contribute", () => {
    const timeline = withSpans([
      { ...macroTaskTimelineFixture().spans[0]!, spanId: "q", runId: "r1", kind: "queue",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:05:00Z", state: "admitted" },
      { ...macroTaskTimelineFixture().spans[3]!, spanId: "r", runId: "r1", kind: "routing",
        startAt: "2026-09-26T01:05:00Z", endAt: "2026-09-26T01:08:00Z", state: "finished", decisionTaskId: "d1" },
      { ...macroTaskTimelineFixture().spans[5]!, spanId: "w", runId: "r1", kind: "host",
        startAt: "2026-09-26T01:08:00Z", endAt: "2026-09-26T01:20:00Z", state: "approved", requestId: "req-1" },
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "e", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:04:00Z", shutdownConfirmed: true, state: "finished" },
    ]);
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.unionMs).toBe(minutes(4));
    expect(metrics.sumMs).toBe(minutes(4));
    expect(metrics.totalSpanMs).toBe(minutes(20));
  });

  it("extends a running execution only through observedAt and says so", () => {
    const timeline = withSpans([
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "run", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: null, state: "executing", shutdownConfirmed: false },
    ]);
    // observedAt is 08:12.
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.unionMs).toBe(new Date("2026-09-26T08:12:00Z").getTime() - new Date("2026-09-26T01:00:00Z").getTime());
    expect(metrics.runningCount).toBe(1);
    expect(metrics.totalSpanMs).toBe(new Date("2026-09-26T08:12:00Z").getTime() - new Date("2026-09-26T01:00:00Z").getTime());
  });

  it("excludes an unconfirmed end from the confirmed numbers and reports it separately", () => {
    const timeline = withSpans([
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "u", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:10:00Z", state: "uncertain",
        uncertain: true, shutdownConfirmed: false },
    ]);
    const metrics = macroTaskMetrics(timeline);
    // Not proven execution: excluded from union and sum alike.
    expect(metrics.unionMs).toBeNull();
    expect(metrics.sumMs).toBeNull();
    expect(metrics.segmentCount).toBe(0);
    expect(metrics.unknownEndCount).toBe(1);
    expect(metrics.limitations.map(entry => entry.key)).toContain("unconfirmed-end");
    // The wall-clock span still reaches observedAt because an unconfirmed tail may be live.
    expect(metrics.totalSpanMs).toBe(new Date("2026-09-26T08:12:00Z").getTime() - new Date("2026-09-26T01:00:00Z").getTime());
  });

  it("never invents durations for missing, reversed or clock-skewed execution time", () => {
    const timeline = withSpans([
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "g", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:30:00Z", state: "finished", shutdownConfirmed: true },
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "m", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:40:00Z", endAt: null, state: "finished", shutdownConfirmed: true },
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "rv", runId: "r1", kind: "execution",
        startAt: "2026-09-26T02:00:00Z", endAt: "2026-09-26T01:30:00Z", shutdownConfirmed: true, state: "finished" },
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "cs", runId: "r1", kind: "execution",
        startAt: "2026-09-26T03:00:00Z", endAt: "2026-09-26T09:00:00Z", shutdownConfirmed: true, state: "finished", clockSkew: true },
    ]);
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.unionMs).toBe(minutes(30));
    expect(metrics.sumMs).toBe(minutes(30));
    const keys = metrics.limitations.map(entry => entry.key);
    expect(keys).toContain("missing-time");
    expect(keys).toContain("reversed");
    expect(keys).toContain("clock-skew");
    expect(metrics.limitations.map(entry => entry.label).join("；")).not.toContain("undefined");
    // Corrupt endpoints are skipped entirely: the clock-skewed six-hour span
    // cannot manufacture total-span duration either.
    expect(metrics.totalSpanMs).toBe(minutes(30));
  });

  it("counts unknown execution ends even when their timestamp is absent or corrupt", () => {
    const base = macroTaskTimelineFixture().spans[2]!;
    const timeline = withSpans([
      { ...base, kind: "execution", spanId: "missing", startAt: null, endAt: null, state: "uncertain", uncertain: true, shutdownConfirmed: false },
      { ...base, kind: "execution", spanId: "skewed", state: "uncertain", uncertain: true, shutdownConfirmed: false, clockSkew: true },
    ]);
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.unknownEndCount).toBe(2);
    expect(metrics.unionMs).toBeNull();
    expect(metrics.sumMs).toBeNull();
  });

  it("treats legitimately open queue, host and running spans as live, not missing time", () => {
    const timeline = withSpans([
      { ...macroTaskTimelineFixture().spans[0]!, spanId: "q", runId: "r1", kind: "queue",
        startAt: "2026-09-26T01:00:00Z", endAt: null, state: "queued" },
      { ...macroTaskTimelineFixture().spans[5]!, spanId: "w", runId: "r1", kind: "host",
        startAt: "2026-09-26T01:05:00Z", endAt: null, state: "open", requestId: "req-1" },
      { ...macroTaskTimelineFixture().spans[2]!, spanId: "e", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:10:00Z", endAt: null, state: "executing", shutdownConfirmed: false },
    ]);
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.limitations.map(entry => entry.key)).not.toContain("missing-time");
    expect(metrics.runningCount).toBe(1);
    expect(metrics.segmentCount).toBe(1);
  });

  it("falls back to the macroTask creation time when no span has a usable start", () => {
    const timeline = withSpans([], { macroTask: macroTaskSummary({ createdAt: "2026-09-26T01:12:00Z" }) });
    timeline.events = [{
      seq: 1, runId: "r1", kind: "dispatch", at: "2026-09-26T02:00:00Z", decision: null, verdict: null, state: null, strategy: null, note: null, actor: null,
      attemptId: null, requestId: null, artifactId: null,
    }];
    const metrics = macroTaskMetrics(timeline);
    expect(metrics.totalSpanMs).toBe(minutes(48));
    expect(metrics.unionMs).toBeNull();
  });

  it("reports recorded-part limitations for filters and truncation without repairing numbers", () => {
    const timeline = macroTaskTimelineFixture({ filtered: true, scopeComplete: false, truncated: { rows: true, spans: true, events: true } });
    const metrics = macroTaskMetrics(timeline);
    const keys = metrics.limitations.map(entry => entry.key);
    expect(keys).toContain("scope");
    expect(keys).toContain("rows");
    expect(keys).toContain("spans");
    expect(keys).toContain("events");
    // Existing recorded numbers stay untouched rather than being prefixed or dropped.
    expect(metrics.unionMs).not.toBeNull();
  });
});
