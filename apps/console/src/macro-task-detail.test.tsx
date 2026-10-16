import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTasks } from "./MacroTasks";
import type { ConsoleApi } from "./api";
import { ApiError } from "./api";
import type { Snapshot, Task } from "./types";
import { macroTaskSummary, macroTaskTimelineFixture } from "./macro-task-fixtures";
import type { MacroTaskStopResult, MacroTaskTimeline } from "./macro-task-types";

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "session-a", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies: [], preferences: [], modelServiceNotes: [], decisions: [],
 modelServiceConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: {},
  };
}

function taskFixture(runId: string): Task {
  const base: Task = { runId, task: `目标 ${runId}`, status: "completed", owner: "fixture", cwd: `/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-26T01:12:00Z", updatedAt: "2026-09-26T01:12:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
    resultAvailable: true, shutdownConfirmed: true,
    lineage: { kind: runId === "r3" ? "helper" : "micro-task", sourceHostId: "codex-desktop", currentHostId: "codex-desktop",
      parentRunId: runId === "r3" ? "r2" : null, rootRunId: runId === "r3" ? "r2" : runId, rootLeadRunId: null,
      project: { id: "p1", label: "hey-my-buddy", path: "~/projects/hey-my-buddy" },
      buddy: { harness: "zcode", provider: "bigmodel-api", model: "glm-5", effort: "high" } } };
  // Every micro task of the macro task has a collaboration record; r2 waits for its Host.
  base.workflow = { state: runId === "r2" ? "awaiting-host" : "delivered", awaitingHost: runId === "r2", hostId: "codex-desktop", ownerGeneration: 2, revision: 1, macroTaskId: "macro-1" };
  return base;
}

/** The compact collaboration record as `workflow_get` sends it. */
function workflowFixture(task: Task) {
  return { runId: task.runId, hostId: "codex-desktop", revision: 1, state: task.workflow!.state, buddy: null, buddyLocked: false, delegationAuthority: false,
    routing: null, turns: [], currentTurn: null, activeRequest: null, requests: [], counts: { turns: 0, openRequests: 0 },
    children: task.runId === "r2" ? [{ taskId: "r3", role: "helper", state: "succeeded" }] : [],
    artifacts: [], integrations: [], finalArtifactId: null, hostConclusion: null, shutdown: null, workspace: { path: task.cwd },
    task: { lineage: task.lineage } };
}

type Pending = { promise: Promise<unknown>; resolve: (value: unknown) => void };
function deferred(): Pending {
  let resolve!: (value: unknown) => void;
  const promise = new Promise<unknown>(done => { resolve = done; });
  return { promise, resolve };
}

type StopOptions = { writesAvailable?: boolean; stopError?: Error; secondMacroTask?: boolean; secondStoppable?: boolean; stoppedTimeline?: MacroTaskTimeline; title?: string };

function harness(options: StopOptions = {}) {
  const snapshot = snapshotFixture();
  const timeline = macroTaskTimelineFixture();
  if (options.title) timeline.macroTask.title = options.title;
  const listed = options.secondStoppable
    ? [timeline.macroTask, macroTaskSummary({ macroTaskId: "macro-2", title: "另一个进行中的目标",
        lastActivitySeq: 30, lastActivityAt: "2026-09-26T07:40:00Z", state: "active",
        counts: { roots: 2, helpers: 0, accepted: 0, active: 1, host: 0, review: 1, ended: 0,
          stoppableRoots: 1, stoppableHelpers: 0, delivered: 1, awaitingHost: 0 } })]
    : options.secondMacroTask
    ? [timeline.macroTask, macroTaskSummary({ macroTaskId: "run:3f0e", kind: "standalone",
        title: "修复标题回退在 CRLF 输入下的显示", titleSource: "task", description: null, lastActivitySeq: 20,
        lastActivityAt: "2026-09-25T10:22:00Z", state: "ended",
        counts: { roots: 1, helpers: 0, accepted: 0, active: 0, host: 0, review: 0, ended: 1,
          stoppableRoots: 0, stoppableHelpers: 0, delivered: 0, awaitingHost: 0 } })]
    : [timeline.macroTask];
  const tasks = new Map(["r1", "r2", "r3", "r4", "r7"].map(id => [id, taskFixture(id)]));
  let stopAttempts = 0;
  const stopReply: MacroTaskStopResult = {
    macroTaskId: "macro-1", duplicate: false,
    results: [{ runId: "r1", state: "accepted", stopped: false },
      { runId: "r2", state: "accepted", stopped: false },
      { runId: "r4", state: "cancelled", stopped: true },
      { runId: "r6", state: "delivered", stopped: false }],
  };
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    if (operation === "workflow_get") {
      const task = tasks.get(String(params.runId));
      if (!task) throw new Error(`Unknown run: ${String(params.runId)}`);
      return params.includeAudit
        ? { ...workflowFixture(task), audit: { turns: [], continuations: [], requests: [], hostConclusions: [] },
          routingHistory: { entries: [{ decisionId: "decision-r2-first", status: "completed", source: "model-selection", selectedBuddy: null, routerBuddy: null, routingMode: "fast", reason: "第一轮的路由依据" }], nextCursor: null, total: 1 } }
        : workflowFixture(task);
    }
    if (operation === "message_list") return { messages: [], cursor: 41 };
    if (operation === "macro_task_stop") {
      stopAttempts += 1;
      if (options.stopError) throw options.stopError;
      return structuredClone(stopReply);
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const timelineReader = vi.fn(async () => ({ timeline: options.stoppedTimeline ?? timeline, verifiedAtMs: null }));
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command,
    task: vi.fn(async (runId: string) => structuredClone(tasks.get(runId) ?? taskFixture(runId))),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: listed, total: listed.length, nextCursor: null, cursor: 41, changed: false })),
    macroTaskTimeline: timelineReader,
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<MacroTasks snapshot={snapshot} api={api} refresh={refresh}
    active writesAvailable={options.writesAvailable ?? true} />);
  return { ...view, api, refresh, tasks, timeline, user, listed, stopAttempts: () => stopAttempts };
}

const stopCalls = (f: ReturnType<typeof harness>) =>
  (f.api.command as unknown as ReturnType<typeof vi.fn>).mock.calls
    .filter(([operation]) => operation === "macro_task_stop") as [string, Record<string, unknown>][];

const stopReplyFor = (macroTaskId: string) => ({ macroTaskId, duplicate: false,
  results: [{ runId: "r4", state: "cancelled", stopped: true }] });

const openMacroTask = async (f: ReturnType<typeof harness>) => {
  await f.user.click(await screen.findByRole("button", { name: new RegExp(f.listed[0]!.title.slice(0, 10)) }));
  await screen.findByRole("group", { name: "宏任务时间轴" });
};
// Layer mode opens details with Enter or a double click; a single click only pins.
const openSpan = async (f: ReturnType<typeof harness>, key: string) => {
  const surface = window.matchMedia?.("(max-width: 760px)").matches ? ".tl-list" : ".tl-scroll";
  await f.user.dblClick(document.querySelector(`${surface} [data-key="span:${key}"]`) as HTMLButtonElement);
  await screen.findByRole("navigation", { name: "步骤" });
};

/** jsdom ships no matchMedia: stub the ≤760px viewport decision per test. */
function stubViewport(narrow: boolean) {
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: narrow, addEventListener: () => {}, removeEventListener: () => {} })) as unknown as typeof window.matchMedia;
}

afterEach(() => { cleanup(); delete (window as { matchMedia?: unknown }).matchMedia; window.history.replaceState(null, "", "#"); });

const detailOf = () => screen.getByRole("region", { name: "微任务详情" });
const railOf = () => screen.getByRole("navigation", { name: "步骤" });
const selectedStep = () => within(railOf()).getByRole("button", { current: "step" }).textContent!;
const hashNow = () => window.location.hash;
/** A step back or forward through the browser's history. */
const traverse = async (direction: "back" | "forward") => {
  await act(async () => { window.history[direction](); await new Promise(resolve => setTimeout(resolve, 20)); });
};

describe("the detail of a micro task in its macro task", () => {
  it("opens at an address of its own, on the step the span belongs to, under the macro task's own header", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r2-r");
    expect(hashNow()).toBe("#tasks/macro-1/r2/2");
    expect(selectedStep()).toContain("路由");
    expect(f.api.task).toHaveBeenCalledWith("r2", expect.any(AbortSignal));
    expect(f.api.command).toHaveBeenCalledWith("workflow_get", { runId: "r2" }, "csrf");
    expect(f.api.command).toHaveBeenCalledWith("message_list", { runId: "r2", limit: 200 }, "csrf");
    // The macro task's header stays, and so does the timeline, mounted under the detail.
    expect(screen.getByRole("heading", { name: "宏任务时间轴：设计、接口与实现" })).toBeTruthy();
    expect(document.querySelector(".tl-grid")).toBeTruthy();
    expect(document.querySelector(".timeline-view")!.className).toContain("hidden");
    expect(detailOf().closest(".timeline-view")).toBeTruthy();
  });

  it("opens from a row of the timeline on its first choice, and from a marker on the step of that marker", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await f.user.dblClick(document.querySelector('[data-key="row:r1"]') as HTMLButtonElement);
    await screen.findByRole("navigation", { name: "步骤" });
    expect(hashNow()).toMatch(/^#tasks\/macro-1\/r1\/\d+$/);
    expect(selectedStep()).toContain("回合 1");
    await f.user.click(within(detailOf()).getByRole("button", { name: "时间轴" }));
    await f.user.dblClick(document.querySelector('[data-key="span:s-r2-w"]') as HTMLButtonElement);
    await screen.findByRole("navigation", { name: "步骤" });
    expect(selectedStep()).toContain("决定");
  });

  it("returns to the timeline by button and by Escape, restoring focus, outline and the fixed idle blocks", async () => {
    stubViewport(true);
    const f = harness();
    await openMacroTask(f);
    const idleCount = document.querySelectorAll(".idle-block").length;
    expect(idleCount).toBeGreaterThan(0);
    await openSpan(f, "s-r1-e");
    const span = document.querySelector('.tl-list [data-key="span:s-r1-e"]') as HTMLButtonElement;
    await f.user.click(within(detailOf()).getByRole("button", { name: "时间轴" }));
    await waitFor(() => expect(screen.queryByRole("navigation", { name: "步骤" })).toBeNull());
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.querySelector(".timeline-view")!.className).not.toContain("hidden");
    expect(document.activeElement).toBe(span);
    // Opening pinned the selection, so the outline survives the return.
    expect(span.className).toContain("selected");
    expect(document.querySelectorAll(".idle-block")).toHaveLength(idleCount);
    // Reopen and return with Escape while focus is outside any field.
    await openSpan(f, "s-r1-e");
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("navigation", { name: "步骤" })).toBeNull());
    expect(hashNow()).toBe("#tasks/macro-1");
  });

  it("goes back and forward through the browser's history: each opening and each return is a step, choosing a step is not", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    const length = window.history.length;
    await openSpan(f, "s-r2-e1");
    expect(window.history.length).toBe(length + 1);
    expect(hashNow()).toBe("#tasks/macro-1/r2/3");
    await f.user.click(within(railOf()).getByRole("button", { name: /^委派/ }));
    expect(hashNow()).toBe("#tasks/macro-1/r2/1");
    expect(window.history.length).toBe(length + 1);
    await traverse("back");
    await waitFor(() => expect(screen.queryByRole("navigation", { name: "步骤" })).toBeNull());
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.querySelector(".tl-grid")).toBeTruthy();
    await traverse("forward");
    await screen.findByRole("navigation", { name: "步骤" });
    expect(hashNow()).toBe("#tasks/macro-1/r2/1");
    expect(selectedStep()).toContain("委派");
  });

  it("opens from an address on load: the macro task, the micro task and the step", async () => {
    stubViewport(false);
    window.history.replaceState(null, "", "#tasks/macro-1/r2/3");
    const f = harness();
    await screen.findByRole("navigation", { name: "步骤" });
    expect(f.api.macroTaskTimeline).toHaveBeenCalledWith("macro-1", expect.anything(), expect.anything());
    expect(selectedStep()).toContain("回合 1");
    expect(hashNow()).toBe("#tasks/macro-1/r2/3");
    // A step that does not exist is replaced by the first choice, a micro task that is not read is a failure with the way back.
    cleanup();
    window.history.replaceState(null, "", "#tasks/macro-1/r2/90");
    harness();
    await screen.findByRole("navigation", { name: "步骤" });
    await waitFor(() => expect(hashNow()).not.toBe("#tasks/macro-1/r2/90"));
    expect(hashNow()).toMatch(/^#tasks\/macro-1\/r2\/\d+$/);
  });

  it("says a micro task whose read fails so, with the way back, and lists the delegation alone for a row the timeline lacks", async () => {
    stubViewport(false);
    window.history.replaceState(null, "", "#tasks/macro-1/r-gone");
    const f = harness();
    expect((await screen.findByRole("alert")).textContent).toContain("Unknown run: r-gone");
    await f.user.click(within(detailOf()).getByRole("button", { name: "时间轴" }));
    expect(hashNow()).toBe("#tasks/macro-1");
    cleanup();
    window.history.replaceState(null, "", "#tasks/macro-1/r7");
    harness();
    await screen.findByRole("navigation", { name: "步骤" });
    expect(within(railOf()).getAllByRole("button")).toHaveLength(1);
    expect(railOf().textContent).toContain("没有这个微任务的一行");
  });

  it("keeps the detail mounted while a list refresh temporarily drops the selected row", async () => {
    stubViewport(true);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r1-q");
    // The next list read returns an empty page (a refresh or filter gap); the
    // timeline's own summary keeps the detail alive.
    (f.api.macroTasks as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ macroTasks: [], total: 0, nextCursor: null, cursor: 41, changed: false });
    // The existing bounded periodic read is the trigger; there is no shell broadcast.
    await waitFor(() => expect(f.api.macroTasks).toHaveBeenCalledTimes(2), { timeout: 4500 });
    expect(screen.getByRole("navigation", { name: "步骤" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "宏任务时间轴：设计、接口与实现" })).toBeTruthy();
  });

  it("ignores Escape while the tab is not the active page", async () => {
    stubViewport(true);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r1-q");
    const snapshot = snapshotFixture();
    f.rerender(<MacroTasks snapshot={snapshot} api={f.api} refresh={f.refresh} active={false} />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.getByRole("navigation", { name: "步骤" })).toBeTruthy();
    f.rerender(<MacroTasks snapshot={snapshot} api={f.api} refresh={f.refresh} active />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("navigation", { name: "步骤" })).toBeNull());
  });

  it("keeps the macro task when the detail goes to a helper, each at its own address", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r2-e1");
    await f.user.click(within(railOf()).getByRole("button", { name: /^委派/ }));
    await f.user.click(within(detailOf()).getByRole("button", { name: "补充 schema 升级离线副本的验证测试" }));
    await waitFor(() => expect(f.api.task).toHaveBeenCalledWith("r3", expect.any(AbortSignal)));
    await waitFor(() => expect(hashNow()).toMatch(/^#tasks\/macro-1\/r3\/\d+$/));
    expect(screen.getByRole("heading", { name: "宏任务时间轴：设计、接口与实现" })).toBeTruthy();
    await f.user.click(within(detailOf()).getByRole("button", { name: "时间轴" }));
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.querySelector(".tl-grid")).toBeTruthy();
  });

  it("keeps every read working in a read-only session with the stop action disabled, and asks the board for reads alone", async () => {
    stubViewport(true);
    const f = harness({ writesAvailable: false });
    await openMacroTask(f);
    // The macro-task-level stop is the only write; a read-only session cannot use it.
    const stop = screen.getByRole("button", { name: "停止宏任务" });
    expect(stop).toHaveProperty("disabled", true);
    expect(stop.getAttribute("title")).toContain("登录已失效");
    await openSpan(f, "s-r1-q");
    await waitFor(() => expect(f.api.command).toHaveBeenCalled());
    const operations = new Set((f.api.command as ReturnType<typeof vi.fn>).mock.calls.map(([operation]) => operation));
    expect([...operations].every(operation => ["workflow_get", "message_list"].includes(operation))).toBe(true);
    expect(detailOf().querySelectorAll("input, textarea, select, form")).toHaveLength(0);
  });

  it("reopens the same routing span on the same step after browsing elsewhere", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r2-r");
    expect(selectedStep()).toContain("路由");
    await f.user.click(within(railOf()).getByRole("button", { name: /^委派/ }));
    await f.user.click(within(detailOf()).getByRole("button", { name: "时间轴" }));
    await openSpan(f, "s-r2-r");
    expect(selectedStep()).toContain("路由");
    expect(hashNow()).toBe("#tasks/macro-1/r2/2");
  });

  it("never opens a detail by a single click: the inspector pins instead", async () => {
    stubViewport(true);
    const f = harness();
    await openMacroTask(f);
    await f.user.click(document.querySelector('[data-key="span:s-r2-e1"]') as HTMLButtonElement);
    expect(screen.queryByRole("navigation", { name: "步骤" })).toBeNull();
    expect(hashNow()).toBe("#tasks/macro-1");
    const inspector = document.querySelector(".inspector-dock-title") as HTMLElement;
    expect(inspector.textContent).toContain("执行片段");
    await f.user.click(document.querySelector('[data-key="row:r1"]') as HTMLButtonElement);
    expect(screen.queryByRole("navigation", { name: "步骤" })).toBeNull();
    expect(document.querySelector(".inspector-dock-title")!.textContent).toContain("整项微任务");
  });
});

describe("macro-task-level stop (0.15.1 U4)", () => {
  it("disables stop for delivered and awaiting-Host work without active descendants", async () => {
    stubViewport(true);
    const f = harness();
    Object.assign(f.timeline.macroTask.counts, { stoppableRoots: 0, stoppableHelpers: 0,
      delivered: 2, awaitingHost: 1 });
    await openMacroTask(f);
    const stop = screen.getByRole("button", { name: "停止宏任务" });
    expect(stop).toHaveProperty("disabled", true);
    expect(stop.getAttribute("title")).toContain("没有进行中的");
  });

  it("offers stop for an active helper below a preserved parent", async () => {
    stubViewport(true);
    const f = harness();
    Object.assign(f.timeline.macroTask.counts, { stoppableRoots: 0, stoppableHelpers: 1,
      delivered: 1, awaitingHost: 1 });
    await openMacroTask(f);
    const stop = screen.getByRole("button", { name: "停止宏任务" });
    expect(stop).toHaveProperty("disabled", false);
    await f.user.click(stop);
    expect((await screen.findByRole("dialog", { name: "停止宏任务" })).textContent)
      .toContain("停止 0 个微任务停止 1 个协助任务");
  });

  it("requires the explicit confirmation naming the macroTask and its scope before dispatching", async () => {
    stubViewport(true);
    const f = harness();
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    const dialog = await screen.findByRole("dialog", { name: "停止宏任务" });
    // The confirmation names the macroTask and the honest scope.
    expect(dialog.textContent).toContain("宏任务时间轴：设计、接口与实现");
    expect(dialog.querySelector(".stop-dialog-task-title")?.getAttribute("title")).toBe("宏任务时间轴：设计、接口与实现");
    expect(dialog.querySelector(".stop-dialog-task-title")?.textContent).toBe("宏任务时间轴：设计、接口与实现");
    expect(dialog.textContent).toContain("停止 1 个微任务");
    expect(dialog.textContent).toContain("停止 0 个协助任务");
    expect(dialog.textContent).toContain("保留 1 个已交付微任务");
    expect(dialog.textContent).toContain("保留 0 个等待 Host 的微任务");
    expect(dialog.textContent).toContain("保留 2 个已验收微任务");
    expect(f.api.command).not.toHaveBeenCalled();
    // Cancel submits nothing.
    await f.user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(f.api.command).not.toHaveBeenCalled();
    // Confirm dispatches macro_task_stop with a fresh command identity.
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith("macro_task_stop",
      expect.objectContaining({ macroTaskId: "macro-1" }), "csrf"));
    const params = (f.api.command as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1] as { commandId: string };
    expect(typeof params.commandId).toBe("string");
  });

  it("keeps a long macro task title in its own titled line", async () => {
    stubViewport(true);
    const title = "核对范围很长的宏任务标题：".repeat(12);
    const f = harness({ title });
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    const dialog = await screen.findByRole("dialog", { name: "停止宏任务" });
    expect(dialog.querySelector(".stop-dialog-task-title")?.getAttribute("title")).toBe(title);
    expect(dialog.getAttribute("aria-describedby")?.split(" ")).toContain("stop-macro-task-name");
    expect(dialog.querySelector("#stop-macro-task-body")?.textContent).not.toContain(title);
  });

  it("shows 正在停止 from refreshed evidence and settles once stops are confirmed", async () => {
    const stopped = macroTaskTimelineFixture();
    const r4 = stopped.rows.find(row => row.runId === "r4")!;
    Object.assign(r4, { state: "cancelled", status: "cancelled", category: "ended", shutdownConfirmed: true });
    const r6 = stopped.rows.find(row => row.runId === "r6")!;
    Object.assign(r6, { state: "cancelled", status: "cancelled", category: "ended", shutdownConfirmed: true });
    const f = harness();
    // The read right after the stop still shows live, unconfirmed work; the
    // next poll carries the confirmed stop evidence.
    let readsAfterStop = 0;
    (f.api.macroTaskTimeline as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      if (f.stopAttempts() > 0) {
        readsAfterStop += 1;
        return { timeline: readsAfterStop <= 1 ? f.timeline : stopped, verifiedAtMs: null };
      }
      return { timeline: f.timeline, verifiedAtMs: null };
    });
    stubViewport(true);
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(f.stopAttempts()).toBe(1));
    const status = await screen.findByText("正在停止");
    expect(status.textContent).toBe("正在停止");
    // The next refreshed read reports confirmed stops for the whole scope.
    await waitFor(() => expect(screen.getByText("已停止").textContent).toBe("已停止"), { timeout: 5000 });
  });

  it("regression: a replay refused with a 401 keeps the earlier unknown outcome", async () => {
    stubViewport(true);
    const f = harness();
    let attempts = 0;
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string) => {
      if (operation === "workflow_get") return workflowFixture(f.tasks.get("r2") ?? taskFixture("r2"));
      if (operation === "message_list") return { messages: [], cursor: 41 };
      if (operation === "macro_task_stop") {
        attempts += 1;
        throw attempts === 1 ? new ApiError("NETWORK", "lost reply") : new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(attempts).toBe(1));
    expect((await screen.findByText("停止未确认")).textContent).toContain("停止未确认");
    // The replay is refused, but that refusal proves nothing about attempt #1.
    await f.user.click(screen.getByRole("button", { name: "重试停止" }));
    await waitFor(() => expect(attempts).toBe(2));
    await waitFor(() => expect((f.api.command as ReturnType<typeof vi.fn>).mock.calls
      .filter(([operation]) => operation === "macro_task_stop").length).toBe(2));
    expect(screen.getByText("停止未确认").textContent).toContain("停止未确认");
    expect(screen.queryByText("停止请求未提交")).toBeNull();
  });

  it("regression: an acknowledged stop survives a failed refresh without becoming refused or unknown", async () => {
    stubViewport(true);
    const f = harness();
    let refreshShouldFail = false;
    f.refresh.mockImplementation(async () => {
      if (refreshShouldFail) throw new Error("refresh lost");
      return structuredClone(snapshotFixture());
    });
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      if (operation === "message_list") return { messages: [], cursor: 41 };
      if (operation === "macro_task_stop") {
        refreshShouldFail = true;
        return stopReplyFor("macro-1");
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    // The reply arrived (cancellation intent known); the refresh failed after
    // it — the status must derive from the live rows, never claim a refusal.
    expect(await screen.findByText("正在停止")).toBeTruthy();
    expect(screen.queryByText("停止请求未提交")).toBeNull();
    expect(screen.queryByText(/停止请求的回复丢失/)).toBeNull();
    expect((f.api.command as ReturnType<typeof vi.fn>).mock.calls
      .filter(([operation]) => operation === "macro_task_stop").length).toBe(1);
  });

  it("treats a lost stop reply as unknown, replays the same command identity and never locks navigation", async () => {
    stubViewport(true);
    let fail = true;
    const f = harness();
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "macro_task_stop") {
        if (fail) throw new ApiError("NETWORK", "lost reply");
        return stopReplyFor("macro-1");
      }
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      if (operation === "message_list") return { messages: [], cursor: 41 };
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    const status = await screen.findByText("停止未确认", { exact: false });
    expect(status.textContent).toContain("停止未确认");
    // Navigation stays free while the reply is unknown: no lock note anywhere.
    expect(document.querySelector(".lock-note")).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "‹ 宏任务列表" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ })).toBeTruthy());
    await f.user.click(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ }));
    // Replay keeps the exact command identity; the server deduplicates it.
    fail = false;
    await f.user.click(await screen.findByRole("button", { name: "重试停止" }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(2));
    const calls = (f.api.command as ReturnType<typeof vi.fn>).mock.calls
      .filter(([operation]) => operation === "macro_task_stop") as [string, Record<string, unknown>][];
    expect(calls[1]![1]).toEqual(calls[0]![1]);
    await waitFor(() => expect(screen.getByText("正在停止")).toBeTruthy());
  });
});

  it("regression: two macroTasks stop concurrently — neither is skipped nor stuck", async () => {
    stubViewport(true);
    const f = harness({ secondStoppable: true });
    const summaries = new Map(f.listed.map(summary => [summary.macroTaskId, summary]));
    // Each macroTask's stop reply is deferred independently.
    const pending = new Map<string, Pending>();
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      if (operation === "macro_task_stop") {
        const deferredReply = deferred();
        pending.set(String(params.commandId), deferredReply);
        return deferredReply.promise;
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    (f.api.macroTaskTimeline as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (macroTaskId: string) =>
      ({ timeline: { ...f.timeline, macroTask: summaries.get(macroTaskId) ?? f.timeline.macroTask }, verifiedAtMs: null }));
    await openMacroTask(f);
    // Confirm the first macroTask's stop; its reply stays pending.
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(1));
    expect((await screen.findByText("正在停止")).textContent).toBe("正在停止");
    // Navigate to the second macroTask and stop it while the first is pending.
    await f.user.click(screen.getByRole("button", { name: "‹ 宏任务列表" }));
    await f.user.click(screen.getByRole("button", { name: /另一个进行中的目标/ }));
    await screen.findByText(/0 \/ 2 个微任务已验收/);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(2));
    // Both dispatches carry their own macroTask and a distinct command identity.
    const [first, second] = stopCalls(f);
    expect((first![1] as { macroTaskId: string }).macroTaskId).toBe("macro-1");
    expect((second![1] as { macroTaskId: string }).macroTaskId).toBe("macro-2");
    expect((first![1] as { commandId: string }).commandId).not.toBe((second![1] as { commandId: string }).commandId);
    expect((await screen.findByText("正在停止")).textContent).toBe("正在停止");
    // Resolving both replies settles each macroTask without a stuck phase.
    pending.get(String((first![1] as { commandId: string }).commandId))!.resolve({
      ...stopReplyFor("macro-1"),
    });
    pending.get(String((second![1] as { commandId: string }).commandId))!.resolve({
      ...stopReplyFor("macro-2"),
    });
    await waitFor(() => expect(screen.queryByText("停止请求未提交")).toBeNull(), { timeout: 3000 });
    expect(stopCalls(f).length).toBe(2);
    expect(screen.getByText("正在停止")).toBeTruthy();
  });

  it("regression: losing the writer at confirmation keeps an unknown's identity; fresh refusals never reuse it", async () => {
    stubViewport(true);
    const f = harness();
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      if (operation === "macro_task_stop") throw new ApiError("NETWORK", "lost reply");
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openMacroTask(f);
    // A brand-new request whose writer vanishes between dialog and confirm
    // becomes a plain refusal that was never dispatched.
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    const dialog = await screen.findByRole("dialog", { name: "停止宏任务" });
    const snapshot = snapshotFixture();
    f.rerender(<MacroTasks snapshot={{ ...snapshot, consoleSession: { id: "session-a", canWrite: false, reason: null } }}
      api={f.api} refresh={f.refresh} active writesAvailable={false} />);
    await f.user.click(within(dialog).getByRole("button", { name: "确认停止宏任务" }));
    expect(await screen.findByText("停止请求未提交")).toBeTruthy();
    expect(stopCalls(f).length).toBe(0);
    // Writer back: confirming mints a FRESH identity (the refusal's empty one
    // is never reused) and the lost reply keeps the entry unknown.
    f.rerender(<MacroTasks snapshot={snapshot} api={f.api} refresh={f.refresh} active writesAvailable />);
    await f.user.click(await screen.findByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(1));
    expect(typeof (stopCalls(f)[0]![1] as { commandId: string }).commandId).toBe("string");
    expect(((stopCalls(f)[0]![1] as { commandId: string }).commandId).length).toBeGreaterThan(0);
    expect(await screen.findByText("停止未确认")).toBeTruthy();
    // Losing the writer again with the dialog open must NOT overwrite the
    // unknown entry: its identity survives for the same-macroTask replay.
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    const secondDialog = await screen.findByRole("dialog", { name: "停止宏任务" });
    f.rerender(<MacroTasks snapshot={{ ...snapshot, consoleSession: { id: "session-a", canWrite: false, reason: null } }}
      api={f.api} refresh={f.refresh} active writesAvailable={false} />);
    await f.user.click(within(secondDialog).getByRole("button", { name: "确认停止宏任务" }));
    expect(screen.getByText("停止未确认")).toBeTruthy();
    expect(screen.queryByText("停止请求未提交")).toBeNull();
    expect(stopCalls(f).length).toBe(1);
    // Writer back once more: the retry replays the EXACT retained identity.
    f.rerender(<MacroTasks snapshot={snapshot} api={f.api} refresh={f.refresh} active writesAvailable />);
    await f.user.click(screen.getByRole("button", { name: "重试停止" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(2));
    expect(stopCalls(f)[1]![1]).toEqual(stopCalls(f)[0]![1]);
  });

  it("regression: a scope-incoherent reply stays an unknown outcome and replays safely", async () => {
    stubViewport(true);
    const f = harness();
    let attempts = 0;
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      if (operation === "macro_task_stop") {
        attempts += 1;
        if (attempts === 1) {
          // A running row cannot be reported as preserved.
          return { macroTaskId: "macro-1", duplicate: false,
            results: [{ runId: "r4", state: "executing", stopped: false }] };
        }
        return stopReplyFor("macro-1");
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openMacroTask(f);
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止宏任务" })).getByRole("button", { name: "确认停止宏任务" }));
    // The malformed reply is refused client-side as an unknown outcome.
    await waitFor(() => expect(attempts).toBe(1));
    expect(await screen.findByText("停止未确认")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试停止" })).toBeTruthy();
    expect(screen.queryByText("停止请求未提交")).toBeNull();
    // The replay keeps the identity; the coherent reply settles it.
    await f.user.click(screen.getByRole("button", { name: "重试停止" }));
    await waitFor(() => expect(attempts).toBe(2));
    expect(stopCalls(f)[1]![1]).toEqual(stopCalls(f)[0]![1]);
    expect(await screen.findByText("正在停止")).toBeTruthy();
  });
