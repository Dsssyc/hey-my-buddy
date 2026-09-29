import { describe, expect, it } from "vitest";
import { stopStatus, stoppableSummary, type ObjectiveStopEntry } from "./objective-stop";
import { errorOfStopReply } from "./objective-stop-reply";
import { objectiveSummary, objectiveTimelineFixture } from "./objective-fixtures";
import type { ObjectiveTimeline } from "./objective-types";

const entry = (overrides: Partial<ObjectiveStopEntry> = {}): ObjectiveStopEntry => ({
  commandId: "cmd-1", phase: "acknowledged",
  result: { objectiveId: "obj-1", runIds: ["r4", "r6"], acceptedRunIds: ["r1"], results: [{ runId: "r4", result: "cancel-requested" }] },
  error: "", ...overrides,
});

/** A complete read whose r4/r6 trees match the given row overrides. */
function read(overrides: Record<string, Partial<ObjectiveTimeline["rows"][number]>> = {}, extra: Partial<ObjectiveTimeline> = {}): ObjectiveTimeline {
  const timeline = objectiveTimelineFixture();
  const byId = new Map(timeline.rows.map(row => [row.runId, row]));
  for (const [runId, partial] of Object.entries(overrides)) {
    byId.set(runId, { ...byId.get(runId)!, ...partial });
  }
  timeline.rows = [...byId.values()];
  return { ...timeline, ...extra };
}

describe("objective stop status (0.15.1 U4, Host-reviewed)", () => {
  it("offers the stop action only while unaccepted work remains", () => {
    expect(stoppableSummary(objectiveSummary())).toBe(true);
    expect(stoppableSummary(objectiveSummary({ state: "ended",
      counts: { roots: 2, helpers: 0, accepted: 2, active: 0, host: 0, review: 0, ended: 2 } }))).toBe(false);
    expect(stoppableSummary(null)).toBe(false);
  });

  it("an unconfirmed reply is unknown, never a failure or a stop claim", () => {
    const status = stopStatus(entry({ phase: "unknown" }), read());
    expect(status?.label).toBe("停止未确认");
    expect(status?.detail).toContain("结果未知");
    expect(status?.detail).toContain("重试同一请求");
  });

  it("a definite refusal reports itself without touching the stop scope", () => {
    const status = stopStatus(entry({ phase: "refused", error: "无权限" }), read());
    expect(status?.label).toBe("停止请求未提交");
    expect(status?.detail).toContain("无权限");
  });

  it("shows 正在停止 while an affected run is still executing", () => {
    const status = stopStatus(entry(), read()); // r4 runs unconfirmed in the fixture
    expect(status?.label).toBe("正在停止");
    expect(status?.detail).toMatch(/\d+ 项停止未确认/);
  });

  it("regression: a cancelled root with a helper still executing stays 正在停止", () => {
    const timeline = read({
      r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
      r6: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
    });
    timeline.rows.push({
      ...timeline.rows[0]!, runId: "r4-h1", parentRunId: "r4", rootRunId: "r4", depth: 1, kind: "helper",
      title: "仍在执行的协助任务", state: "executing", status: "running", shutdownConfirmed: false,
    });
    const status = stopStatus(entry(), timeline);
    expect(status?.label).toBe("正在停止");
    expect(status?.detail).toContain("1 项");
  });

  it("regression: a filtered or truncated read never claims the whole group stopped", () => {
    const allConfirmed = read({
      r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
      r6: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
    });
    const filtered = { ...allConfirmed, filtered: true, scopeComplete: false };
    const status = stopStatus(entry(), filtered);
    expect(status?.label).toBe("停止未确认");
    expect(status?.detail).toContain("不完整");
    const truncated = { ...allConfirmed, scopeComplete: false, truncated: { rows: true, spans: false, events: false } };
    expect(stopStatus(entry(), truncated)?.label).toBe("停止未确认");
    expect(stopStatus(entry(), null)?.label).toBe("停止未确认");
  });

  it("regression: an affected root that never read as cancelled stays 正在停止", () => {
    // Both trees have confirmed shutdown, but r6 still reads as delivered.
    const timeline = read({
      r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
      r6: { shutdownConfirmed: true },
    });
    const status = stopStatus(entry(), timeline);
    expect(status?.label).toBe("正在停止");
  });

  it("shows 停止未确认 once terminal runs lack stop evidence, counting missing runs unverified", () => {
    const timeline = read({
      r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: false },
      r6: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
    });
    const missing = { ...timeline, rows: timeline.rows.filter(row => row.runId !== "r6"),
      filtered: true, scopeComplete: false };
    const status = stopStatus(entry(), missing);
    expect(status?.label).toBe("停止未确认");
    expect(status?.detail).toContain("2 项停止未确认");
  });

  it("settles on 已停止 only from a complete read with every root cancelled and confirmed", () => {
    const timeline = read({
      r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
      r6: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
    });
    const status = stopStatus(entry(), timeline);
    expect(status?.label).toBe("已停止");
    // runIds mixes roots and helpers; the copy names the root count only.
    expect(status?.detail).toBe("2 个委派及协助任务已停止");
  });

  it("the stopped copy counts roots only, even when helpers share the reply scope", () => {
    const timeline = read({
      r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
      r6: { state: "cancelled", status: "cancelled", shutdownConfirmed: true },
    });
    timeline.rows.push({
      ...timeline.rows[0]!, runId: "r4-h1", parentRunId: "r4", rootRunId: "r4", depth: 1, kind: "helper",
      title: "已停的协助任务", state: "cancelled", status: "cancelled", shutdownConfirmed: true,
    });
    const withHelper: ObjectiveStopEntry = { ...entry(), result: {
      objectiveId: "obj-1", runIds: ["r4", "r4-h1", "r6"], acceptedRunIds: [],
      results: [
        { runId: "r4", result: "cancel-requested" },
        { runId: "r4-h1", result: "cancel-requested" },
        { runId: "r6", result: "cancel-requested" },
      ],
    } };
    const status = stopStatus(withHelper, timeline);
    expect(status?.label).toBe("已停止");
    expect(status?.detail).toBe("2 个委派及协助任务已停止");
  });

  it("a malformed reply is an unknown outcome, never a confirmed stop", () => {
    const good = { objectiveId: "obj-1", runIds: ["r4"], acceptedRunIds: [], results: [{ runId: "r4", result: "cancel-requested" }] };
    expect(errorOfStopReply("obj-1", structuredClone(good))).toEqual(good);
    for (const malformed of [
      { ...good, objectiveId: "obj-other" }, { ...good, runIds: "r4" },
      { ...good, acceptedRunIds: [7] }, { ...good, results: [{ runId: "", result: "x" }] },
      null,
      // Duplicate identifiers inside one list.
      { ...good, runIds: ["r4", "r4"] },
      { ...good, acceptedRunIds: ["r1", "r1"] },
      { ...good, results: [{ runId: "r4", result: "x" }, { runId: "r4", result: "y" }] },
      // The cancelled and retained scopes must stay disjoint.
      { ...good, runIds: ["r4", "r1"], acceptedRunIds: ["r1"] },
      // The per-root results must cover the cancellation scope exactly.
      { ...good, results: [] },
      { ...good, results: [{ runId: "r-other", result: "x" }] },
      { ...good, runIds: ["r4", "r5"], results: [{ runId: "r4", result: "x" }] },
    ]) {
      expect(() => errorOfStopReply("obj-1", malformed)).toThrowError(/不完整|响应/);
    }
    // Helper ids in runIds stay valid when the results cover them exactly.
    const helperReply = { objectiveId: "obj-1", runIds: ["r4", "r4-h1"], acceptedRunIds: [],
      results: [{ runId: "r4", result: "cancel-requested" }, { runId: "r4-h1", result: "cancel-requested" }] };
    expect(errorOfStopReply("obj-1", helperReply)).toEqual(helperReply);
  });
});
