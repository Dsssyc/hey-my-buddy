import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import "./i18n";
import { stopScope, stopStatus, type MacroTaskStopEntry } from "./macro-task-stop";
import { errorOfStopReply } from "./macro-task-stop-reply";
import { macroTaskSummary, macroTaskTimelineFixture } from "./macro-task-fixtures";
import type { MacroTaskTimeline } from "./macro-task-types";

const entry = (overrides: Partial<MacroTaskStopEntry> = {}): MacroTaskStopEntry => ({
  commandId: "cmd-1", phase: "acknowledged",
  result: { macroTaskId: "macro-1", duplicate: false, results: [
    { runId: "r1", state: "accepted", stopped: false },
    { runId: "r4", state: "cancelled", stopped: true },
    { runId: "r6", state: "delivered", stopped: false },
  ] }, error: "", ...overrides,
});

function read(overrides: Record<string, Partial<MacroTaskTimeline["rows"][number]>> = {}, extra: Partial<MacroTaskTimeline> = {}) {
  const timeline = macroTaskTimelineFixture();
  timeline.rows = timeline.rows.map(row => ({ ...row, ...overrides[row.runId] }));
  return { ...timeline, ...extra };
}

afterEach(() => { void i18n.changeLanguage("zh"); });

describe("macro task stop scope and evidence", () => {
  it("counts exact active states across the group, including helpers under preserved roots", () => {
    const summary = macroTaskSummary({ counts: { ...macroTaskSummary().counts,
      stoppableRoots: 0, stoppableHelpers: 1, delivered: 2, awaitingHost: 1 } });
    expect(stopScope(summary)).toEqual({ roots: 0, helpers: 1, delivered: 2, awaitingHost: 1 });
  });

  it("does not mistake an acknowledgement or a preserved delivered row for a stop", () => {
    expect(stopStatus(entry(), read())?.label).toBe("正在停止");
    const stopped = read({ r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true } });
    expect(stopStatus(entry(), stopped)).toEqual({ phase: "stopped", label: "已停止",
      detail: "1 个微任务及 0 个协助任务已停止" });
  });

  it("trusts the cancelled workflow state with confirmed shutdown even after a late completed receipt", () => {
    const late = read({ r4: { state: "cancelled", status: "completed", shutdownConfirmed: true } });
    expect(stopStatus(entry(), late)?.label).toBe("已停止");
    const stale = read({ r4: { state: "executing", status: "cancelled", shutdownConfirmed: true } });
    expect(stopStatus(entry(), stale)?.label).toBe("正在停止");
  });

  it("checks a stopped helper under a preserved parent without requiring the parent to cancel", () => {
    const timeline = read();
    timeline.rows.push({ ...timeline.rows[0]!, runId: "r1-h", rootRunId: "r1", parentRunId: "r1",
      kind: "helper", depth: 1, state: "cancelled", status: "cancelled", shutdownConfirmed: true });
    const helper = entry({ result: { macroTaskId: "macro-1", duplicate: false, results: [
      { runId: "r1", state: "accepted", stopped: false },
      { runId: "r1-h", state: "cancelled", stopped: true },
    ] } });
    expect(stopStatus(helper, timeline)?.detail).toBe("0 个微任务及 1 个协助任务已停止");
  });

  it("reports an acknowledged no-op when work ended before the stop reached the board", () => {
    const result = { macroTaskId: "macro-1", duplicate: false, results: [
      { runId: "r1", state: "accepted", stopped: false },
      { runId: "r6", state: "delivered", stopped: false },
    ] };
    expect(stopStatus(entry({ result }), null)).toEqual({ phase: "acknowledged", label: "无需停止",
      detail: "本次没有微任务需要停止；清单内状态已保留。" });
  });

  it("requires a complete timeline and confirmed shutdown", () => {
    const cancelled = read({ r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: false } });
    expect(stopStatus(entry(), cancelled)?.label).toBe("停止未确认");
    const confirmed = read({ r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true } });
    expect(stopStatus(entry(), { ...confirmed, scopeComplete: false, truncated: { ...confirmed.truncated, rows: true } })?.label)
      .toBe("停止未确认");
    expect(stopStatus(entry(), null)?.label).toBe("停止未确认");
  });

  it("keeps refusals and lost replies distinct", () => {
    expect(stopStatus(entry({ phase: "refused", error: "无权限" }), null)?.detail).toBe("无权限");
    expect(stopStatus(entry({ phase: "unknown" }), null)?.detail).toContain("可重试同一请求");
  });

  it("validates the new result contract and preserves extra historical cancellation fields", () => {
    const good = { macroTaskId: "macro-1", duplicate: false, results: [
      { runId: "r1", state: "accepted", stopped: false },
      { runId: "r4", state: "cancelled", stopped: true, cancelled: true },
    ] };
    expect(errorOfStopReply("macro-1", good)).toEqual(good);
    for (const bad of [null, { ...good, macroTaskId: "other" }, { ...good, duplicate: undefined },
      { ...good, results: [{ runId: "r4", state: "executing", stopped: true }] },
      { ...good, results: [{ runId: "r4", state: "awaiting-host", stopped: true }] },
      { ...good, results: [{ runId: "r4", state: "executing", stopped: false }] },
      { ...good, results: [{ runId: "r4", state: "cancelled", stopped: "yes" }] },
      { ...good, results: [{ runId: "r4", state: "cancelled", stopped: true },
        { runId: "r4", state: "cancelled", stopped: true }] },
    ]) expect(() => errorOfStopReply("macro-1", bad)).toThrow();
  });

  it("localizes stop status in English", async () => {
    await i18n.changeLanguage("en");
    const confirmed = read({ r4: { state: "cancelled", status: "cancelled", shutdownConfirmed: true } });
    expect(stopStatus(entry(), confirmed)?.detail).toBe("Stopped 1 micro tasks and 0 helper tasks");
  });
});
