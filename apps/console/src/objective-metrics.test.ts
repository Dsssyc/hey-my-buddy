import { describe, expect, it } from "vitest";
import { objectiveMetrics, relatedEventsForRun, relatedEventsForSpan, rootRollups, runRollup, latestExecutionResult } from "./objective-metrics";
import { objectiveTimelineFixture, objectiveSummary } from "./objective-fixtures";
import type { ObjectiveTimeline, TimelineEvent, TimelineSpan } from "./objective-types";

const minutes = (count: number) => count * 60000;
const at = (base: string, offsetMs: number) => new Date(new Date(base).getTime() + offsetMs).toISOString();

function withSpans(spans: TimelineSpan[], overrides: Partial<ObjectiveTimeline> = {}): ObjectiveTimeline {
  const timeline = objectiveTimelineFixture(overrides);
  timeline.rows = timeline.rows.filter(row => spans.some(span => span.runId === row.runId));
  timeline.spans = spans;
  timeline.events = [];
  timeline.totals = { rows: timeline.rows.length, spans: spans.length, events: 0, allRows: timeline.rows.length };
  return timeline;
}

describe("objective metrics", () => {
  it("keeps the union below the cumulative sum under concurrency, and both below the total span", () => {
    // Two overlapping 10-minute executions in one 30-minute wall-clock window.
    const timeline = withSpans([
      { ...objectiveTimelineFixture().spans[2]!, spanId: "a", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:10:00Z", shutdownConfirmed: true, state: "finished" },
      { ...objectiveTimelineFixture().spans[2]!, spanId: "b", runId: "r2", kind: "execution",
        startAt: "2026-09-26T01:05:00Z", endAt: "2026-09-26T01:15:00Z", shutdownConfirmed: true, state: "finished" },
    ]);
    const metrics = objectiveMetrics(timeline);
    expect(metrics.unionMs).toBe(minutes(15));
    expect(metrics.sumMs).toBe(minutes(20));
    expect(metrics.segmentCount).toBe(2);
    expect(metrics.totalSpanMs).toBe(minutes(15));
    expect(metrics.limitations.map(entry => entry.key)).not.toContain("unconfirmed-end");
  });

  it("counts only execution: queue, routing and waiting-Host never contribute", () => {
    const timeline = withSpans([
      { ...objectiveTimelineFixture().spans[0]!, spanId: "q", runId: "r1", kind: "queue",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:05:00Z", state: "claimed" },
      { ...objectiveTimelineFixture().spans[3]!, spanId: "r", runId: "r1", kind: "routing",
        startAt: "2026-09-26T01:05:00Z", endAt: "2026-09-26T01:08:00Z", state: "finished", decisionTaskId: "d1" },
      { ...objectiveTimelineFixture().spans[5]!, spanId: "w", runId: "r1", kind: "host",
        startAt: "2026-09-26T01:08:00Z", endAt: "2026-09-26T01:20:00Z", state: "approved", requestId: "req-1" },
      { ...objectiveTimelineFixture().spans[2]!, spanId: "e", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:04:00Z", shutdownConfirmed: true, state: "finished" },
    ]);
    const metrics = objectiveMetrics(timeline);
    expect(metrics.unionMs).toBe(minutes(4));
    expect(metrics.sumMs).toBe(minutes(4));
    expect(metrics.totalSpanMs).toBe(minutes(20));
  });

  it("extends a running execution only through observedAt and says so", () => {
    const timeline = withSpans([
      { ...objectiveTimelineFixture().spans[2]!, spanId: "run", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: null, state: "executing", shutdownConfirmed: false },
    ]);
    // observedAt is 08:12.
    const metrics = objectiveMetrics(timeline);
    expect(metrics.unionMs).toBe(new Date("2026-09-26T08:12:00Z").getTime() - new Date("2026-09-26T01:00:00Z").getTime());
    expect(metrics.runningCount).toBe(1);
    expect(metrics.totalSpanMs).toBe(new Date("2026-09-26T08:12:00Z").getTime() - new Date("2026-09-26T01:00:00Z").getTime());
  });

  it("excludes an unconfirmed end from the confirmed numbers and reports it separately", () => {
    const timeline = withSpans([
      { ...objectiveTimelineFixture().spans[2]!, spanId: "u", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:10:00Z", state: "uncertain",
        uncertain: true, shutdownConfirmed: false },
    ]);
    const metrics = objectiveMetrics(timeline);
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
      { ...objectiveTimelineFixture().spans[2]!, spanId: "g", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:00:00Z", endAt: "2026-09-26T01:30:00Z", state: "finished", shutdownConfirmed: true },
      { ...objectiveTimelineFixture().spans[2]!, spanId: "m", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:40:00Z", endAt: null, state: "finished", shutdownConfirmed: true },
      { ...objectiveTimelineFixture().spans[2]!, spanId: "rv", runId: "r1", kind: "execution",
        startAt: "2026-09-26T02:00:00Z", endAt: "2026-09-26T01:30:00Z", shutdownConfirmed: true, state: "finished" },
      { ...objectiveTimelineFixture().spans[2]!, spanId: "cs", runId: "r1", kind: "execution",
        startAt: "2026-09-26T03:00:00Z", endAt: "2026-09-26T09:00:00Z", shutdownConfirmed: true, state: "finished", clockSkew: true },
    ]);
    const metrics = objectiveMetrics(timeline);
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
    const base = objectiveTimelineFixture().spans[2]!;
    const timeline = withSpans([
      { ...base, kind: "execution", spanId: "missing", startAt: null, endAt: null, state: "uncertain", uncertain: true, shutdownConfirmed: false },
      { ...base, kind: "execution", spanId: "skewed", state: "uncertain", uncertain: true, shutdownConfirmed: false, clockSkew: true },
    ]);
    const metrics = objectiveMetrics(timeline);
    expect(metrics.unknownEndCount).toBe(2);
    expect(metrics.unionMs).toBeNull();
    expect(metrics.sumMs).toBeNull();
  });

  it("treats legitimately open queue, host and running spans as live, not missing time", () => {
    const timeline = withSpans([
      { ...objectiveTimelineFixture().spans[0]!, spanId: "q", runId: "r1", kind: "queue",
        startAt: "2026-09-26T01:00:00Z", endAt: null, state: "queued" },
      { ...objectiveTimelineFixture().spans[5]!, spanId: "w", runId: "r1", kind: "host",
        startAt: "2026-09-26T01:05:00Z", endAt: null, state: "open", requestId: "req-1" },
      { ...objectiveTimelineFixture().spans[2]!, spanId: "e", runId: "r1", kind: "execution",
        startAt: "2026-09-26T01:10:00Z", endAt: null, state: "executing", shutdownConfirmed: false },
    ]);
    const metrics = objectiveMetrics(timeline);
    expect(metrics.limitations.map(entry => entry.key)).not.toContain("missing-time");
    expect(metrics.runningCount).toBe(1);
    expect(metrics.segmentCount).toBe(1);
  });

  it("falls back to the objective creation time when no span has a usable start", () => {
    const timeline = withSpans([], { objective: objectiveSummary({ createdAt: "2026-09-26T01:12:00Z" }) });
    timeline.events = [{
      seq: 1, runId: "r1", kind: "dispatch", at: "2026-09-26T02:00:00Z", label: "派发", summary: "", actor: null,
      attemptId: null, requestId: null, artifactId: null,
    }];
    const metrics = objectiveMetrics(timeline);
    expect(metrics.totalSpanMs).toBe(minutes(48));
    expect(metrics.unionMs).toBeNull();
  });

  it("reports recorded-part limitations for filters and truncation without repairing numbers", () => {
    const timeline = objectiveTimelineFixture({ filtered: true, scopeComplete: false, truncated: { rows: true, spans: true, events: true } });
    const metrics = objectiveMetrics(timeline);
    const keys = metrics.limitations.map(entry => entry.key);
    expect(keys).toContain("scope");
    expect(keys).toContain("rows");
    expect(keys).toContain("spans");
    expect(keys).toContain("events");
    // Existing recorded numbers stay untouched rather than being prefixed or dropped.
    expect(metrics.unionMs).not.toBeNull();
  });
});

describe("related Host events by recorded identity", () => {
  const events: TimelineEvent[] = [
    { seq: 1, runId: "r1", kind: "dispatch", at: "2026-09-26T01:00:00Z", label: "派发", summary: "", actor: null,
      attemptId: "att-1", requestId: null, artifactId: null },
    { seq: 2, runId: "r2", kind: "decide", at: "2026-09-26T01:01:00Z", label: "决定", summary: "", actor: null,
      attemptId: null, requestId: "req-1", artifactId: null },
    { seq: 3, runId: "r3", kind: "dispatch", at: "2026-09-26T01:02:00Z", label: "派发", summary: "", actor: null,
      attemptId: "att-1", requestId: null, artifactId: null },
    { seq: 4, runId: "r4", kind: "dispatch", at: "2026-09-26T01:03:00Z", label: "派发", summary: "", actor: null,
      attemptId: "", requestId: "", artifactId: null },
  ];

  it("matches spans only through non-empty attempt or request keys on the same run", () => {
    const byAttempt = { ...objectiveTimelineFixture().spans[2]!, spanId: "x", runId: "r1", attemptId: "att-1", requestId: null } as TimelineSpan;
    expect(relatedEventsForSpan(byAttempt, events).map(event => event.seq)).toEqual([1]);
    const byRequest = { ...objectiveTimelineFixture().spans[5]!, spanId: "y", runId: "r2", attemptId: null, requestId: "req-1" } as TimelineSpan;
    expect(relatedEventsForSpan(byRequest, events).map(event => event.seq)).toEqual([2]);
  });

  it("never lets null, undefined or empty-string keys match each other", () => {
    const empty = { ...objectiveTimelineFixture().spans[2]!, spanId: "z", attemptId: null, requestId: null } as TimelineSpan;
    expect(relatedEventsForSpan(empty, events)).toEqual([]);
    const undefinedKeys = { ...empty, attemptId: undefined, requestId: undefined } as unknown as TimelineSpan;
    expect(relatedEventsForSpan(undefinedKeys, events)).toEqual([]);
    // An empty string is not a key either.
    const blank = { ...empty, runId: "r4", attemptId: "", requestId: "" } as TimelineSpan;
    expect(relatedEventsForSpan(blank, events)).toEqual([]);
    // A non-empty key on only one side never matches.
    const oneSided = { ...empty, runId: "r1", attemptId: "att-9" } as TimelineSpan;
    expect(relatedEventsForSpan(oneSided, events)).toEqual([]);
  });

  it("requires the same run even when the record identity is shared", () => {
    // seq 3 carries r3 with the same attemptId as the r1 span: no match.
    const span = { ...objectiveTimelineFixture().spans[2]!, spanId: "c", runId: "r1", attemptId: "att-1", requestId: null } as TimelineSpan;
    expect(relatedEventsForSpan(span, events).map(event => event.seq)).toEqual([1]);
  });

  it("matches whole runs by runId only", () => {
    expect(relatedEventsForRun("r1", events).map(event => event.seq)).toEqual([1]);
    expect(relatedEventsForRun("missing", events)).toEqual([]);
  });
});

describe("per-delegation rollups", () => {
  it("orders root cards by recorded creation time, not by list order", () => {
    const timeline = objectiveTimelineFixture();
    // r5 was created after r4 but appears earlier in the tree; creation order rules.
    timeline.rows = [...timeline.rows];
    const roots = rootRollups(timeline).map(rollup => rollup.row.runId);
    expect(roots).toEqual(["r1", "r2", "r5", "r4", "r6"]);
  });

  it("gathers rounds, helpers and pending requests for one run", () => {
    const timeline = objectiveTimelineFixture();
    const rollup = rootRollups(timeline).find(entry => entry.row.runId === "r2")!;
    expect(rollup.executions.map(span => span.turnIndex)).toEqual([1, 2]);
    expect(rollup.helpers.map(row => row.runId)).toEqual(["r3"]);
    expect(rollup.pending).toEqual([]);
    expect(rollup.executionMs).not.toBeNull();
    // A run with an open host request reports it as pending.
    const openWait = rootRollups({ ...timeline, spans: [...timeline.spans, {
      ...timeline.spans[5]!, spanId: "s-r2-w2", runId: "r2", kind: "host", startAt: "2026-09-26T04:00:00Z", endAt: null,
      state: "open", requestId: "req-2", requestKind: "continue", summary: "等待续接决定",
    }] as TimelineSpan[] }).find(entry => entry.row.runId === "r2")!;
    expect(openWait.pending.map(span => span.requestId)).toEqual(["req-2"]);
  });

  it("counts a host request as pending only while its recorded state is open", () => {
    const timeline = objectiveTimelineFixture();
    const decided = runRollup(timeline.rows.find(row => row.runId === "r2")!, { ...timeline, spans: [
      ...timeline.spans.filter(span => span.spanId !== "s-r2-w"),
      // A decided request without an end timestamp is not still waiting.
      { ...timeline.spans[5]!, spanId: "s-r2-w", runId: "r2", kind: "host",
        startAt: "2026-09-26T02:10:00Z", endAt: null, state: "approved", requestId: "req-schema" },
    ] });
    expect(decided.pending).toEqual([]);
  });

  it("propagates read-scope limitations into a rollup instead of claiming absence", () => {
    const timeline = objectiveTimelineFixture({
      truncated: { rows: true, spans: true, events: true }, scopeComplete: false, filtered: true,
    });
    const rollup = rootRollups(timeline).find(entry => entry.row.runId === "r4")!;
    const keys = rollup.limitations;
    expect(keys).toContain("scope");
    expect(keys).toContain("rows");
    expect(keys).toContain("spans");
    expect(keys).toContain("events");
  });

  it("reports a null latest result as unrecorded, never as success", () => {
    const timeline = objectiveTimelineFixture();
    const r4 = timeline.rows.find(row => row.runId === "r4")!;
    const noResult = runRollup(r4, { ...timeline, spans: timeline.spans.filter(span => span.runId === "r4") });
    // r4's only execution is running without a receipt.
    expect(latestExecutionResult(noResult)?.label).toBe("未记录");
    const r1 = timeline.rows.find(row => row.runId === "r1")!;
    const none = runRollup(r1, { ...timeline, spans: [] });
    expect(latestExecutionResult(none)).toBeNull();
    const r3 = timeline.rows.find(row => row.runId === "r3")!;
    const helper = runRollup(r3, timeline);
    expect(latestExecutionResult(helper)?.label).toBe("成功");
  });

  it("orders the latest result by recorded turn index, not by whichever span has a start", () => {
    const timeline = objectiveTimelineFixture();
    const r2 = timeline.rows.find(row => row.runId === "r2")!;
    // Turn 2 has no usable start; turn order must still crown it, and its
    // missing receipt reads 未记录 instead of resurrecting turn 1's success.
    const newer = runRollup(r2, { ...timeline, spans: [
      timeline.spans[0]!,
      ...timeline.spans.filter(span => span.runId === "r2" && span.spanId === "s-r2-e1"),
      { ...timeline.spans[6]!, spanId: "s-r2-e2", runId: "r2", kind: "execution", startAt: null, endAt: null,
        state: "finished", turnIndex: 2, attemptId: "att-r2b", configuration: null, shutdownConfirmed: true },
    ] });
    const latest = latestExecutionResult(newer)!;
    expect(latest.span.turnIndex).toBe(2);
    expect(latest.label).toBe("未记录");
  });

  it("labels the latest result unknown when no ordering can identify it", () => {
    const timeline = objectiveTimelineFixture();
    const r1 = timeline.rows.find(row => row.runId === "r1")!;
    // One span has a start, the other has neither turn index nor time: the
    // older success may not speak for the run.
    const unordered = runRollup(r1, { ...timeline, spans: [
      timeline.spans[1]!,
      { ...timeline.spans[1]!, spanId: "s-r1-e9", runId: "r1", kind: "execution", startAt: null, endAt: null,
        state: "finished", turnIndex: null, attemptId: "att-r1b", configuration: null, shutdownConfirmed: true },
    ] });
    expect(latestExecutionResult(unordered)?.label).toBe("未记录");
  });
});
