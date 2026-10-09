import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Objectives } from "./Objectives";
import type { ConsoleApi } from "./api";
import { ApiError } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";
import { objectiveSummary, objectiveTimelineFixture } from "./objective-fixtures";
import type { ObjectiveStopResult, ObjectiveTimeline } from "./objective-types";

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "session-a", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles: [], cards: [], preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: { evaluationWriteGate: true },
  };
}

function taskFixture(runId: string): Task {
  const base: Task = { runId, task: `目标 ${runId}`, status: "completed", owner: "fixture", cwd: `/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-26T01:12:00Z", acceptedAt: null, acceptanceVerdict: null,
    resultAvailable: true, shutdownConfirmed: true,
    delegation: { kind: runId === "r3" ? "helper" : "goal", sourceHostId: "codex-desktop", currentHostId: "codex-desktop",
      parentRunId: runId === "r3" ? "r2" : null, rootRunId: runId === "r3" ? "r2" : runId,
      project: { id: "p1", label: "hey-my-buddy", path: "~/projects/hey-my-buddy" },
      configuration: { adapter: "zcode", provider: "bigmodel-api", model: "glm-5", effort: "high" } } };
  if (runId === "r2") {
    base.workflow = { state: "awaiting-host", awaitingHost: true, hostId: "codex-desktop", ownerGeneration: 2, revision: 1 };
  }
  return base;
}

function workflowFixture(task: Task): Workflow {
  return { governed: true, runId: task.runId, hostId: "codex-desktop", ownerGeneration: 2, revision: 1,
    state: "awaiting-host", awaitingHost: true, waitReason: "等待 Host 决定", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: task.cwd, kind: "worktree", access: "write", inputCommit: "input", manifestSha256: "hash" },
    currentTurn: null, activeRequest: null,
    children: task.runId === "r2" ? [{ taskId: "r3", role: "helper", state: "completed", requestId: "req-helper" }] : [],
    artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task };
}

type Pending = { promise: Promise<unknown>; resolve: (value: unknown) => void };
function deferred(): Pending {
  let resolve!: (value: unknown) => void;
  const promise = new Promise<unknown>(done => { resolve = done; });
  return { promise, resolve };
}

type StopOptions = { writesAvailable?: boolean; stopError?: Error; secondObjective?: boolean; secondStoppable?: boolean; stoppedTimeline?: ObjectiveTimeline };

function harness(options: StopOptions = {}) {
  const snapshot = snapshotFixture();
  const timeline = objectiveTimelineFixture();
  const listed = options.secondStoppable
    ? [timeline.objective, objectiveSummary({ objectiveId: "obj-2", title: "另一个进行中的目标",
        lastActivitySeq: 30, lastActivityAt: "2026-09-26T07:40:00Z", state: "active",
        counts: { roots: 2, helpers: 0, accepted: 0, active: 1, host: 0, review: 1, ended: 0 } })]
    : options.secondObjective
    ? [timeline.objective, objectiveSummary({ objectiveId: "run:3f0e", kind: "standalone",
        title: "修复标题回退在 CRLF 输入下的显示", titleSource: "task", description: null, lastActivitySeq: 20,
        lastActivityAt: "2026-09-25T10:22:00Z", state: "ended",
        counts: { roots: 1, helpers: 0, accepted: 0, active: 0, host: 0, review: 0, ended: 1 } })]
    : [timeline.objective];
  const tasks = new Map(["r1", "r2", "r3", "r4"].map(id => [id, taskFixture(id)]));
  let stopAttempts = 0;
  const stopReply: ObjectiveStopResult = {
    objectiveId: "obj-1", runIds: ["r4", "r6"], acceptedRunIds: ["r1", "r2"],
    results: [{ runId: "r4", result: "cancel-requested" }, { runId: "r6", result: "cancel-requested" }],
  };
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    if (operation === "workflow_get") {
      const task = tasks.get(String(params.runId));
      if (!task) throw new Error(`Unknown run: ${String(params.runId)}`);
      return workflowFixture(task);
    }
    if (operation === "selection_get") return { decision: {
      decisionId: params.decisionId, status: "completed", kind: "select", task: "路由合成数据", profileId: null,
      tableRevision: 1, reason: "第一轮的路由依据", evidence: [], createdAt: "2026-09-26T01:21:00Z",
    } };
    if (operation === "objective_stop") {
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
    objectives: vi.fn(async () => ({ objectives: listed, total: listed.length, nextCursor: null, cursor: 41, changed: false })),
    objectiveTimeline: timelineReader,
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<Objectives snapshot={snapshot} api={api} refresh={refresh}
    active writesAvailable={options.writesAvailable ?? true} />);
  return { ...view, api, refresh, tasks, timeline, user, listed, stopAttempts: () => stopAttempts };
}

const stopCalls = (f: ReturnType<typeof harness>) =>
  (f.api.command as unknown as ReturnType<typeof vi.fn>).mock.calls
    .filter(([operation]) => operation === "objective_stop") as [string, Record<string, unknown>][];

const openObjective = async (f: ReturnType<typeof harness>) => {
  await f.user.click(await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
  await screen.findByRole("group", { name: "工作目标时间轴" });
};
// Layer mode opens details with Enter or a double click; a single click only pins.
const openSpan = async (f: ReturnType<typeof harness>, key: string) => {
  const surface = window.matchMedia?.("(max-width: 760px)").matches ? ".tl-list" : ".tl-scroll";
  await f.user.dblClick(document.querySelector(`${surface} [data-key="span:${key}"]`) as HTMLButtonElement);
  await screen.findByRole("complementary", { name: "工作目标详情" });
  await waitFor(() => expect(document.querySelector(".locator")).toBeTruthy());
};

/** jsdom ships no matchMedia: stub the ≤760px viewport decision per test. */
function stubViewport(narrow: boolean) {
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: narrow, addEventListener: () => {}, removeEventListener: () => {} })) as unknown as typeof window.matchMedia;
}

afterEach(() => { cleanup(); delete (window as { matchMedia?: unknown }).matchMedia; });

describe("objective detail navigation (layer mode, ≤760px viewport)", () => {
  it("opens the read-only detail with the locator bar and preselected section", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    const locator = document.querySelector(".locator") as HTMLElement;
    expect(within(locator).getByRole("button", { name: "返回时间轴" })).toBeTruthy();
    // T4: the breadcrumb splits into two separately truncated crumbs.
    expect(within(locator).getAllByText("工作目标时间轴：设计、接口与实现").length).toBeGreaterThan(0);
    // T4: the span fact states only the round and time, never the title.
    expect(within(locator).getByText(/来自时间轴：第 1 轮 /)).toBeTruthy();
    // The timeline stays mounted (hidden) while the detail is open.
    expect(document.querySelector(".tl-grid")).toBeTruthy();
    expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(true);
    await waitFor(() => expect(within(document.querySelector(".run-view")!).getByRole("tab", { name: "执行记录" })
      .getAttribute("aria-selected")).toBe("true"));
    expect(f.api.task).toHaveBeenCalledWith("r2", expect.any(AbortSignal));
    // The delegation detail carries no Host write form anymore (U4).
    expect(document.querySelector(".workflow-controls")).toBeNull();
  });

  it("returns to the timeline by button and by Escape, restoring focus, outline and fixed idle blocks", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    // The fixed idle blocks survive the detail round-trip.
    const idleCount = document.querySelectorAll(".idle-block").length;
    expect(idleCount).toBeGreaterThan(0);
    await openSpan(f, "s-r1-e");
    const span = document.querySelector('.tl-list [data-key="span:s-r1-e"]') as HTMLButtonElement;
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "返回时间轴" }));
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    expect(document.activeElement).toBe(span);
    // Opening pinned the selection, so the outline survives the return.
    expect(span.className).toContain("selected");
    expect(document.querySelectorAll(".idle-block")).toHaveLength(idleCount);
    // Reopen and return with Escape while focus is outside any field.
    await openSpan(f, "s-r1-e");
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    expect(document.querySelector(".run-view")).toBeNull();
  });

  it("keeps the detail mounted while a list refresh temporarily drops the selected row", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r1-q");
    await screen.findByRole("heading", { name: "执行记录" });
    // The next list read returns an empty page (a refresh or filter gap); the
    // timeline's own summary keeps the detail alive.
    (f.api.objectives as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ objectives: [], total: 0, nextCursor: null, cursor: 41, changed: false });
    // The existing bounded periodic read is the trigger; there is no shell broadcast.
    await waitFor(() => expect(f.api.objectives).toHaveBeenCalledTimes(2), { timeout: 4500 });
    expect(document.querySelector(".run-view")).toBeTruthy();
    expect(document.querySelector(".locator")).toBeTruthy();
  });

  it("ignores Escape while the tab is not the active page", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r1-q");
    await screen.findByRole("heading", { name: "执行记录" });
    const snapshot = snapshotFixture();
    f.rerender(<Objectives snapshot={snapshot} api={f.api} refresh={f.refresh} active={false} />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(document.querySelector(".run-view")).toBeTruthy();
    f.rerender(<Objectives snapshot={snapshot} api={f.api} refresh={f.refresh} active />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(document.querySelector(".run-view")).toBeNull());
  });

  it("keeps the objective context when the detail navigates to a helper", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    await f.user.click(await screen.findByRole("tab", { name: "协作与待办" }));
    await f.user.click(await screen.findByRole("button", { name: "r3" }));
    await waitFor(() => expect(f.api.task).toHaveBeenCalledWith("r3", expect.any(AbortSignal)));
    const crumbs = document.querySelector(".locator .crumbs") as HTMLElement;
    expect(crumbs.textContent).toContain("补充 schema 升级离线副本的验证测试");
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "返回时间轴" }));
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    expect(document.querySelector(".tl-grid")).toBeTruthy();
  });

  it("keeps every read working in a read-only session with the stop action disabled", async () => {
    stubViewport(true);
    const f = harness({ writesAvailable: false });
    await openObjective(f);
    // The objective-level stop is the only write; a read-only session cannot use it.
    const stop = screen.getByRole("button", { name: "停止目标" });
    expect(stop).toHaveProperty("disabled", true);
    expect(stop.getAttribute("title")).toContain("登录已失效");
    await openSpan(f, "s-r1-q");
    await screen.findByRole("heading", { name: "执行记录" });
    expect(f.api.command).not.toHaveBeenCalled();
  });

  it("opens the routed delegation and locates the exact decision in its routing tab", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-r");
    await waitFor(() => expect(f.api.task).toHaveBeenCalledWith("r2", expect.any(AbortSignal)));
    expect(f.api.task).not.toHaveBeenCalledWith("run-d02b");
    expect(screen.getByRole("tab", { name: "路由依据" }).getAttribute("aria-selected")).toBe("true");
    await screen.findByText("第一轮的路由依据");
    const rationale = screen.getByRole("region", { name: "所选路由决定" });
    expect(rationale.getAttribute("data-decision-id")).toBe("decision-r2-first");
    expect(document.activeElement).toBe(rationale);
    expect(f.api.command).toHaveBeenCalledWith("selection_get", { decisionId: "decision-r2-first", includeAudit: true }, "csrf");
  });

  it("reopening the same routing span restores its exact decision and tab after browsing", async () => {
    stubViewport(false);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-r");
    await screen.findByText("第一轮的路由依据");
    await f.user.click(screen.getByRole("button", { name: "返回当前配置" }));
    expect(screen.queryByRole("region", { name: "所选路由决定" })).toBeNull();
    await f.user.click(screen.getByRole("tab", { name: "概览" }));
    await openSpan(f, "s-r2-r");
    await screen.findByText("第一轮的路由依据");
    expect(screen.getByRole("tab", { name: "路由依据" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("region", { name: "所选路由决定" }).getAttribute("data-decision-id")).toBe("decision-r2-first");
  });

  it("single clicks never open a detail; the inspector pins instead", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await f.user.click(document.querySelector('[data-key="span:s-r2-e1"]') as HTMLButtonElement);
    expect(document.querySelector(".run-view")).toBeNull();
    const inspector = document.querySelector(".inspector-dock-title") as HTMLElement;
    expect(inspector.textContent).toContain("执行片段");
    await f.user.click(document.querySelector('[data-key="row:r1"]') as HTMLButtonElement);
    expect(document.querySelector(".run-view")).toBeNull();
    expect(document.querySelector(".inspector-dock-title")!.textContent).toContain("整项委派");
  });
});

describe("objective-level stop (0.15.1 U4)", () => {
  it("requires the explicit confirmation naming the objective and its scope before dispatching", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    const dialog = await screen.findByRole("dialog", { name: "停止工作目标" });
    // The confirmation names the objective and the honest scope.
    expect(dialog.textContent).toContain("工作目标时间轴：设计、接口与实现");
    // 0.16 P1.3: the new confirmation copy names the unaccepted scope.
    expect(dialog.textContent).toContain("尚未验收的 3 个委派及其协助任务");
    expect(dialog.textContent).toContain("已验收的 2 个保留");
    expect(f.api.command).not.toHaveBeenCalled();
    // Cancel submits nothing.
    await f.user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(f.api.command).not.toHaveBeenCalled();
    // Confirm dispatches objective_stop with a fresh command identity.
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledWith("objective_stop",
      expect.objectContaining({ objectiveId: "obj-1" }), "csrf"));
    const params = (f.api.command as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1] as { commandId: string };
    expect(typeof params.commandId).toBe("string");
  });

  it("shows 正在停止 from refreshed evidence and settles once stops are confirmed", async () => {
    const stopped = objectiveTimelineFixture();
    const r4 = stopped.rows.find(row => row.runId === "r4")!;
    Object.assign(r4, { state: "cancelled", status: "cancelled", category: "ended", shutdownConfirmed: true });
    const r6 = stopped.rows.find(row => row.runId === "r6")!;
    Object.assign(r6, { state: "cancelled", status: "cancelled", category: "ended", shutdownConfirmed: true });
    const f = harness();
    // The read right after the stop still shows live, unconfirmed work; the
    // next poll carries the confirmed stop evidence.
    let readsAfterStop = 0;
    (f.api.objectiveTimeline as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      if (f.stopAttempts() > 0) {
        readsAfterStop += 1;
        return { timeline: readsAfterStop <= 1 ? f.timeline : stopped, verifiedAtMs: null };
      }
      return { timeline: f.timeline, verifiedAtMs: null };
    });
    stubViewport(true);
    await openObjective(f);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
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
      if (operation === "objective_stop") {
        attempts += 1;
        throw attempts === 1 ? new ApiError("NETWORK", "lost reply") : new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openObjective(f);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    await waitFor(() => expect(attempts).toBe(1));
    expect((await screen.findByText("停止未确认")).textContent).toContain("停止未确认");
    // The replay is refused, but that refusal proves nothing about attempt #1.
    await f.user.click(screen.getByRole("button", { name: "重试停止" }));
    await waitFor(() => expect(attempts).toBe(2));
    await waitFor(() => expect((f.api.command as ReturnType<typeof vi.fn>).mock.calls
      .filter(([operation]) => operation === "objective_stop").length).toBe(2));
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
      if (operation === "objective_stop") {
        refreshShouldFail = true;
        return { objectiveId: "obj-1", runIds: ["r4"], acceptedRunIds: ["r1", "r2"], results: [{ runId: "r4", result: "cancel-requested" }] };
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openObjective(f);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    // The reply arrived (cancellation intent known); the refresh failed after
    // it — the status must derive from the live rows, never claim a refusal.
    expect(await screen.findByText("正在停止")).toBeTruthy();
    expect(screen.queryByText("停止请求未提交")).toBeNull();
    expect(screen.queryByText(/停止请求的回复丢失/)).toBeNull();
    expect((f.api.command as ReturnType<typeof vi.fn>).mock.calls
      .filter(([operation]) => operation === "objective_stop").length).toBe(1);
  });

  it("treats a lost stop reply as unknown, replays the same command identity and never locks navigation", async () => {
    stubViewport(true);
    let fail = true;
    const f = harness();
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "objective_stop") {
        if (fail) throw new ApiError("NETWORK", "lost reply");
        return { objectiveId: "obj-1", runIds: ["r4"], acceptedRunIds: ["r1", "r2"], results: [{ runId: "r4", result: "cancel-requested" }] };
      }
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openObjective(f);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    const status = await screen.findByText("停止未确认", { exact: false });
    expect(status.textContent).toContain("停止未确认");
    // Navigation stays free while the reply is unknown: no lock note anywhere.
    expect(document.querySelector(".lock-note")).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "‹ 工作目标列表" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ })).toBeTruthy());
    await f.user.click(screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
    // Replay keeps the exact command identity; the server deduplicates it.
    fail = false;
    await f.user.click(await screen.findByRole("button", { name: "重试停止" }));
    await waitFor(() => expect(f.api.command).toHaveBeenCalledTimes(2));
    const calls = (f.api.command as ReturnType<typeof vi.fn>).mock.calls
      .filter(([operation]) => operation === "objective_stop") as [string, Record<string, unknown>][];
    expect(calls[1]![1]).toEqual(calls[0]![1]);
    await waitFor(() => expect(screen.getByText("正在停止")).toBeTruthy());
  });
});

  it("regression: two objectives stop concurrently — neither is skipped nor stuck", async () => {
    stubViewport(true);
    const f = harness({ secondStoppable: true });
    const summaries = new Map(f.listed.map(summary => [summary.objectiveId, summary]));
    // Each objective's stop reply is deferred independently.
    const pending = new Map<string, Pending>();
    (f.api.command as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (operation: string, params: Record<string, unknown>) => {
      if (operation === "workflow_get") return workflowFixture(f.tasks.get(String(params.runId)) ?? taskFixture(String(params.runId)));
      if (operation === "objective_stop") {
        const deferredReply = deferred();
        pending.set(String(params.commandId), deferredReply);
        return deferredReply.promise;
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    (f.api.objectiveTimeline as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (objectiveId: string) =>
      ({ timeline: { ...f.timeline, objective: summaries.get(objectiveId) ?? f.timeline.objective }, verifiedAtMs: null }));
    await openObjective(f);
    // Confirm the first objective's stop; its reply stays pending.
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(1));
    expect((await screen.findByText("正在停止")).textContent).toBe("正在停止");
    // Navigate to the second objective and stop it while the first is pending.
    await f.user.click(screen.getByRole("button", { name: "‹ 工作目标列表" }));
    await f.user.click(screen.getByRole("button", { name: /另一个进行中的目标/ }));
    await screen.findByText(/0 \/ 2 个委派已验收/);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(2));
    // Both dispatches carry their own objective and a distinct command identity.
    const [first, second] = stopCalls(f);
    expect((first![1] as { objectiveId: string }).objectiveId).toBe("obj-1");
    expect((second![1] as { objectiveId: string }).objectiveId).toBe("obj-2");
    expect((first![1] as { commandId: string }).commandId).not.toBe((second![1] as { commandId: string }).commandId);
    expect((await screen.findByText("正在停止")).textContent).toBe("正在停止");
    // Resolving both replies settles each objective without a stuck phase.
    pending.get(String((first![1] as { commandId: string }).commandId))!.resolve({
      objectiveId: "obj-1", runIds: ["r4"], acceptedRunIds: ["r1", "r2"], results: [{ runId: "r4", result: "cancel-requested" }],
    });
    pending.get(String((second![1] as { commandId: string }).commandId))!.resolve({
      objectiveId: "obj-2", runIds: ["r4"], acceptedRunIds: [], results: [{ runId: "r4", result: "cancel-requested" }],
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
      if (operation === "objective_stop") throw new ApiError("NETWORK", "lost reply");
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openObjective(f);
    // A brand-new request whose writer vanishes between dialog and confirm
    // becomes a plain refusal that was never dispatched.
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    const dialog = await screen.findByRole("dialog", { name: "停止工作目标" });
    const snapshot = snapshotFixture();
    f.rerender(<Objectives snapshot={{ ...snapshot, consoleSession: { id: "session-a", canWrite: false, reason: null } }}
      api={f.api} refresh={f.refresh} active writesAvailable={false} />);
    await f.user.click(within(dialog).getByRole("button", { name: "确认停止目标" }));
    expect(await screen.findByText("停止请求未提交")).toBeTruthy();
    expect(stopCalls(f).length).toBe(0);
    // Writer back: confirming mints a FRESH identity (the refusal's empty one
    // is never reused) and the lost reply keeps the entry unknown.
    f.rerender(<Objectives snapshot={snapshot} api={f.api} refresh={f.refresh} active writesAvailable />);
    await f.user.click(await screen.findByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
    await waitFor(() => expect(stopCalls(f).length).toBe(1));
    expect(typeof (stopCalls(f)[0]![1] as { commandId: string }).commandId).toBe("string");
    expect(((stopCalls(f)[0]![1] as { commandId: string }).commandId).length).toBeGreaterThan(0);
    expect(await screen.findByText("停止未确认")).toBeTruthy();
    // Losing the writer again with the dialog open must NOT overwrite the
    // unknown entry: its identity survives for the same-objective replay.
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    const secondDialog = await screen.findByRole("dialog", { name: "停止工作目标" });
    f.rerender(<Objectives snapshot={{ ...snapshot, consoleSession: { id: "session-a", canWrite: false, reason: null } }}
      api={f.api} refresh={f.refresh} active writesAvailable={false} />);
    await f.user.click(within(secondDialog).getByRole("button", { name: "确认停止目标" }));
    expect(screen.getByText("停止未确认")).toBeTruthy();
    expect(screen.queryByText("停止请求未提交")).toBeNull();
    expect(stopCalls(f).length).toBe(1);
    // Writer back once more: the retry replays the EXACT retained identity.
    f.rerender(<Objectives snapshot={snapshot} api={f.api} refresh={f.refresh} active writesAvailable />);
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
      if (operation === "objective_stop") {
        attempts += 1;
        if (attempts === 1) {
          // results do not cover runIds: the scope is incoherent.
          return { objectiveId: "obj-1", runIds: ["r4", "r6"], acceptedRunIds: ["r1"], results: [{ runId: "r4", result: "cancel-requested" }] };
        }
        return { objectiveId: "obj-1", runIds: ["r4", "r6"], acceptedRunIds: ["r1", "r2"],
          results: [{ runId: "r4", result: "cancel-requested" }, { runId: "r6", result: "cancel-requested" }] };
      }
      throw new Error(`Unexpected command: ${operation}`);
    });
    await openObjective(f);
    await f.user.click(screen.getByRole("button", { name: "停止目标" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "停止工作目标" })).getByRole("button", { name: "确认停止目标" }));
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

describe("objective detail docking (0.15.1 U1)", () => {
  it("docks the detail beside a visible timeline and collapses the list to the rail", async () => {
    stubViewport(false);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    // The viewport (not the pane) picks the layout: side dock whenever a
    // detail is open above 760px; there is no stacked mode anymore.
    const dock = document.querySelector(".right-dock.side") as HTMLElement;
    expect(dock).toBeTruthy();
    expect(dock.closest(".detail-panel")?.classList.contains("docked")).toBe(true);
    expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false);
    // The adjustable separator follows §2 and names both column widths.
    const separator = document.getElementById("timeline-detail-separator") as HTMLElement;
    expect(separator.getAttribute("role")).toBe("separator");
    expect(separator.getAttribute("aria-controls")).toBe("timeline-detail-column");
    expect(separator.getAttribute("aria-valuetext")).toMatch(/详情 \d+ 像素，时间轴 \d+ 像素/);
    // Opening the detail collapsed the objective list into the 48px rail.
    await waitFor(() => expect(document.querySelector(".workspace-grid.list-rail")).toBeTruthy());
    const rail = document.querySelector(".rail-panel") as HTMLElement;
    expect(rail.querySelector(".rail-expand")).toBeTruthy();
    expect(rail.querySelector(".rail-state")).toBeNull();
    // Clicking another item in the timeline only changes the inspector.
    await f.user.click(document.querySelector('[data-key="span:s-r1-e"]') as HTMLButtonElement);
    expect(f.api.task).toHaveBeenCalledTimes(1); // still r2's detail
    expect(document.querySelector(".inspector-dock-title")!.textContent).toContain("设计工作目标时间轴视图与交互规范");
    // Selection survives closing the detail, which also restores the full list.
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "关闭详情" }));
    expect(document.querySelector(".run-view")).toBeNull();
    expect(document.querySelector('[data-key="span:s-r1-e"]')!.className).toContain("selected");
    await waitFor(() => expect(document.querySelector(".workspace-grid.list-rail")).toBeNull());
    expect(document.querySelector(".panel-toolbar")).toBeTruthy();
  });

  it("the rail drawer overlays without moving the right column and switches objectives freely", async () => {
    stubViewport(false);
    const f = harness({ secondObjective: true });
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    await waitFor(() => expect(document.querySelector(".rail-panel")).toBeTruthy());
    // Expand into the overlay drawer: the dock geometry stays untouched.
    const dock = document.querySelector(".right-dock.side") as HTMLElement;
    const detailWidth = dock.style.getPropertyValue("--dock-detail-width");
    await f.user.click(document.querySelector(".rail-expand") as HTMLButtonElement);
    const drawer = document.querySelector(".list-drawer") as HTMLElement;
    expect(drawer).toBeTruthy();
    expect(drawer.querySelector(".list-filters")).toBeTruthy();
    expect((document.querySelector(".right-dock.side") as HTMLElement).style.getPropertyValue("--dock-detail-width")).toBe(detailWidth);
    // Selecting the SAME objective only closes the drawer.
    await f.user.click(within(drawer).getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
    await waitFor(() => expect(document.querySelector(".list-drawer")).toBeNull());
    expect(document.querySelector(".run-view")).toBeTruthy();
    expect(document.querySelector(".workspace-grid.list-rail")).toBeTruthy();
    // Selecting ANOTHER objective closes the detail and restores the list.
    await f.user.click(document.querySelector(".rail-expand") as HTMLButtonElement);
    const drawerAgain = await waitFor(() => {
      const node = document.querySelector(".list-drawer") as HTMLElement | null;
      if (!node) throw new Error("drawer not open");
      return node;
    });
    // The unarchived delegation is folded by default; expand it inside the drawer.
    await f.user.click(within(drawerAgain).getByRole("button", { name: /历史独立委派（1）/ }));
    await f.user.click(within(drawerAgain).getAllByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })[0]!);
    await waitFor(() => expect(document.querySelector(".run-view")).toBeNull());
    await waitFor(() => expect(document.querySelector(".workspace-grid.list-rail")).toBeNull());
    expect((f.api.command as ReturnType<typeof vi.fn>).mock.calls.every(([operation]) => operation !== "workflow_cancel")).toBe(true);
  });

  it("keeps the detail docked across pane resizes, only clamping the width; keyboard adjusts the separator", async () => {
    stubViewport(false);
    const observers: Array<{ callback: ResizeObserverCallback }> = [];
    vi.stubGlobal("ResizeObserver", class {
      callback: ResizeObserverCallback;
      observe = vi.fn(() => { this.callback([], {} as ResizeObserver); });
      disconnect = vi.fn(); unobserve = vi.fn();
      constructor(callback: ResizeObserverCallback) { this.callback = callback; observers.push(this); }
    });
    let paneWidth = 1300;
    const widthOriginal = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get() { return paneWidth; } });
    try {
      const f = harness();
      await openObjective(f);
      await f.user.dblClick(document.querySelector('[data-key="span:s-r2-e1"]') as HTMLButtonElement);
      await waitFor(() => expect(document.querySelector(".right-dock.side")).toBeTruthy());
      // Pin a selection while docked; it survives pane changes.
      await f.user.click(document.querySelector('[data-key="span:s-r1-e"]') as HTMLButtonElement);
      const separator = () => document.getElementById("timeline-detail-separator") as HTMLElement;
      const before = Number(separator().getAttribute("aria-valuenow"));
      // Keyboard: ArrowLeft widens the detail by 20px, Shift by 80px.
      fireEvent.keyDown(separator(), { key: "ArrowLeft" });
      await waitFor(() => expect(Number(separator().getAttribute("aria-valuenow"))).toBe(before + 20));
      fireEvent.keyDown(separator(), { key: "ArrowLeft", shiftKey: true });
      await waitFor(() => expect(Number(separator().getAttribute("aria-valuenow"))).toBe(before + 100));
      // A pane change only re-clamps; the dock never becomes stacked.
      paneWidth = 900;
      actResize(observers);
      await waitFor(() => expect(document.querySelector(".right-dock.side")).toBeTruthy());
      expect(document.querySelector(".right-dock.stack")).toBeNull();
      expect(document.querySelector('[data-key="span:s-r1-e"]')!.className).toContain("selected");
      const clamped = Number(separator().getAttribute("aria-valuenow"));
      expect(clamped).toBeLessThanOrEqual(Number(separator().getAttribute("aria-valuemax")));
      expect(clamped).toBeGreaterThanOrEqual(Number(separator().getAttribute("aria-valuemin")));
      // Enter resets to the viewport default formula.
      fireEvent.keyDown(separator(), { key: "Enter" });
      await waitFor(() => expect(Number(separator().getAttribute("aria-valuenow"))).toBe(Math.max(360, Math.min(600, Math.round((900 - 48) * 0.4)))));
    } finally {
      if (widthOriginal) Object.defineProperty(HTMLElement.prototype, "clientWidth", widthOriginal);
      else delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
      vi.unstubAllGlobals();
    }
  });
});

function actResize(observers: Array<{ callback: ResizeObserverCallback }>) {
  for (const observer of observers) observer.callback([], {} as ResizeObserver);
}
