import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Objectives } from "./Objectives";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";
import { objectiveSummary, objectiveTimelineFixture } from "./objective-fixtures";

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "session-a", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: null },
    profiles: [], cards: [], preferences: [], annotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { runs: [], total: 0 },
    capabilities: { evaluationWriteGate: true },
  };
}

function taskFixture(runId: string): Task {
  const base: Task = { runId, task: `目标 ${runId}`, status: "completed", owner: "fixture", cwd: `/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-26T01:12:00Z", acceptedAt: null, acceptanceVerdict: null,
    resultAvailable: true, shutdownConfirmed: true,
    delegation: { kind: runId === "r3" ? "helper" : "goal", sourceHostId: "codex-desktop", currentHostId: "codex-desktop",
      parentRunId: runId === "r3" ? "r2" : null, rootRunId: runId === "r3" ? "r2" : runId,
      project: { id: "p1", label: "hey-my-buddy", path: "~/Desktop/codespace/mememe/hey-my-buddy" },
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

/** jsdom ships no matchMedia: stub the ≤760px viewport decision per test. */
function stubViewport(narrow: boolean) {
  const listeners: Array<() => void> = [];
  window.matchMedia = vi.fn().mockImplementation(() => ({
    matches: narrow,
    addEventListener: (_: string, callback: () => void) => { listeners.push(callback); },
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
  return { set(next: boolean) { narrow = next; for (const callback of listeners) callback(); } };
}

function harness(options: { writesAvailable?: boolean; pendingContinue?: Pending; secondObjective?: boolean } = {}) {
  const snapshot = snapshotFixture();
  const timeline = objectiveTimelineFixture();
  const listed = options.secondObjective
    ? [timeline.objective, objectiveSummary({ objectiveId: "run:3f0e", kind: "standalone",
        title: "修复标题回退在 CRLF 输入下的显示", titleSource: "task", lastActivitySeq: 20,
        lastActivityAt: "2026-09-25T10:22:00Z", state: "ended",
        counts: { roots: 1, helpers: 0, active: 0, host: 0, review: 0, ended: 1 } })]
    : [timeline.objective];
  const tasks = new Map(["r1", "r2", "r3", "r4"].map(id => [id, taskFixture(id)]));
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    if (operation === "workflow_get") {
      const task = tasks.get(String(params.runId));
      if (!task) throw new Error(`Unknown run: ${String(params.runId)}`);
      return workflowFixture(task);
    }
    if (operation === "workflow_continue") return options.pendingContinue?.promise ?? { ok: true };
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command,
    task: vi.fn(async (runId: string) => structuredClone(tasks.get(runId) ?? taskFixture(runId))),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: listed, total: listed.length, nextCursor: null, cursor: 41, changed: false })),
    objectiveTimeline: vi.fn(async () => timeline),
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<Objectives snapshot={snapshot} api={api} refresh={refresh}
    active writesAvailable={options.writesAvailable ?? true} />);
  return { ...view, api, refresh, tasks, timeline, user };
}

const openObjective = async (f: ReturnType<typeof harness>) => {
  await f.user.click(await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
  await screen.findByRole("group", { name: "工作目标时间轴" });
};
// Layer mode opens details with Enter or a double click; a single click only pins.
const openSpan = async (f: ReturnType<typeof harness>, key: string) => {
  await f.user.dblClick(document.querySelector(`[data-key="span:${key}"]`) as HTMLButtonElement);
  await screen.findByRole("complementary", { name: "工作目标详情" });
  await waitFor(() => expect(document.querySelector(".locator")).toBeTruthy());
};

afterEach(() => { cleanup(); delete (window as { matchMedia?: unknown }).matchMedia; });

describe("objective detail navigation (layer mode, ≤760px viewport)", () => {
  it("opens the existing detail with the locator bar and preselected section", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    const locator = document.querySelector(".locator") as HTMLElement;
    expect(within(locator).getByRole("button", { name: "‹ 返回时间轴" })).toBeTruthy();
    expect(within(locator).getByText(/工作目标时间轴：设计、接口与实现 › /)).toBeTruthy();
    expect(within(locator).getByText(/来自时间轴：第 1 轮 · 执行片段/)).toBeTruthy();
    // The timeline stays mounted (hidden) while the detail is open.
    expect(document.querySelector(".tl-grid")).toBeTruthy();
    expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(true);
    await waitFor(() => expect(within(document.querySelector(".run-view")!).getByRole("tab", { name: "执行记录" })
      .getAttribute("aria-selected")).toBe("true"));
    expect(f.api.task).toHaveBeenCalledWith("r2");
  });

  it("returns to the timeline by button and by Escape, restoring focus, outline and expanded gaps", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    // Expand one folded break first; it must survive the detail round-trip.
    await f.user.click((await screen.findAllByRole("button", { name: /已折叠，展开/ }))[0]!);
    await openSpan(f, "s-r1-e");
    const span = document.querySelector('[data-key="span:s-r1-e"]') as HTMLButtonElement;
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "‹ 返回时间轴" }));
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    expect(document.activeElement).toBe(span);
    // Opening pinned the selection, so the outline survives the return.
    expect(span.className).toContain("selected");
    expect(screen.getByRole("button", { name: /^收起空闲/ })).toBeTruthy();
    // Reopen and return with Escape while focus is outside any field.
    await openSpan(f, "s-r1-e");
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    expect(document.querySelector(".run-view")).toBeNull();
  });

  it("keeps Escape inside inputs so typing is never discarded", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    await screen.findByRole("tab", { name: "协作与待办" });
    await f.user.click(screen.getByRole("tab", { name: "协作与待办" }));
    const input = await screen.findByLabelText("交给下一回合的输入");
    await f.user.type(input, "补充输入");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(document.querySelector(".run-view")).toBeTruthy();
    expect(input).toHaveProperty("value", "补充输入");
  });

  it("locks navigation while a command result is unconfirmed and unlocks after", async () => {
    stubViewport(true);
    const pending = deferred();
    const f = harness({ pendingContinue: pending, secondObjective: true });
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    await f.user.click(await screen.findByRole("tab", { name: "协作与待办" }));
    const input = await screen.findByLabelText("交给下一回合的输入");
    await f.user.type(input, "等待中的接续");
    await f.user.click(screen.getByRole("button", { name: "提交接续输入" }));
    await waitFor(() => expect(document.querySelector(".lock-note")).toBeTruthy());
    const back = within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "‹ 返回时间轴" });
    expect(back.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(back);
    expect(document.querySelector(".run-view")).toBeTruthy();
    // Switching to any other objective stays disabled until the result is
    // confirmed; the unarchived section must be expanded first.
    await f.user.click(screen.getByRole("button", { name: /未归档委派（已加载 1）/ }));
    const otherObjective = screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ });
    expect(otherObjective).toHaveProperty("disabled", true);
    // Re-clicking the selected objective must not unmount the unconfirmed detail.
    await f.user.click(screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }));
    expect(document.querySelector(".run-view")).toBeTruthy();
    expect(document.querySelector(".lock-note")).toBeTruthy();
    pending.resolve({ ok: true });
    await waitFor(() => expect(document.querySelector(".lock-note")).toBeNull());
    const unlockedBack = within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "‹ 返回时间轴" });
    expect(unlockedBack.getAttribute("aria-disabled")).toBeNull();
  });

  it("keeps the detail mounted while a list refresh temporarily drops the selected row", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r1-q");
    await screen.findByLabelText("检查依据");
    // The next list read returns an empty page (a refresh or filter gap); the
    // timeline's own summary keeps the detail alive.
    (f.api.objectives as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ objectives: [], total: 0, nextCursor: null, cursor: 41, changed: false });
    await f.user.click(screen.getByRole("button", { name: "刷新记录" }));
    await waitFor(() => expect(f.api.objectives).toHaveBeenCalledTimes(2));
    expect(document.querySelector(".run-view")).toBeTruthy();
    expect(document.querySelector(".locator")).toBeTruthy();
  });

  it("ignores Escape while the tab is not the active page", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r1-q");
    await screen.findByLabelText("检查依据");
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
    await waitFor(() => expect(f.api.task).toHaveBeenCalledWith("r3"));
    const crumbs = document.querySelector(".locator .crumbs") as HTMLElement;
    expect(crumbs.textContent).toContain("补充 schema 升级离线副本的验证测试");
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "‹ 返回时间轴" }));
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    expect(document.querySelector(".tl-grid")).toBeTruthy();
  });

  it("keeps drafts when navigating between timeline and details", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r1-q");
    const note = await screen.findByLabelText("检查依据");
    await f.user.type(note, "核对了离线副本的测试");
    await f.user.click(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "‹ 返回时间轴" }));
    await waitFor(() => expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false));
    await openSpan(f, "s-r1-q");
    expect(await screen.findByLabelText("检查依据")).toHaveProperty("value", "核对了离线副本的测试");
  });

  it("keeps every read working in a read-only session with existing controls disabled", async () => {
    stubViewport(true);
    const f = harness({ writesAvailable: false });
    await openObjective(f);
    await openSpan(f, "s-r1-q");
    const note = await screen.findByLabelText("检查依据");
    expect(note).toHaveProperty("readOnly", true);
    expect(screen.getByRole("button", { name: "接受交付" })).toHaveProperty("disabled", true);
    expect(within(document.querySelector(".locator") as HTMLElement).getByRole("button", { name: "‹ 返回时间轴" })
      .getAttribute("aria-disabled")).toBeNull();
    expect(f.api.command).not.toHaveBeenCalled();
  });

  it("routes a routing span to the decision task's own detail", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-r");
    await waitFor(() => expect(f.api.task).toHaveBeenCalledWith("run-d02b"));
    expect((document.querySelector(".locator .crumbs") as HTMLElement).textContent).toContain("run-d02b");
  });

  it("single clicks never open a detail; the inspector pins instead", async () => {
    stubViewport(true);
    const f = harness();
    await openObjective(f);
    await f.user.click(document.querySelector('[data-key="span:s-r2-e1"]') as HTMLButtonElement);
    expect(document.querySelector(".run-view")).toBeNull();
    const inspector = document.querySelector(".timeline-inspector") as HTMLElement;
    expect(inspector.textContent).toContain("执行片段");
    await f.user.click(document.querySelector('[data-key="row:r1"]') as HTMLButtonElement);
    expect(document.querySelector(".run-view")).toBeNull();
    expect(document.querySelector(".timeline-inspector")!.textContent).toContain("整项委派");
  });
});

describe("objective detail docking (C4)", () => {
  it("stacks the detail under a visible timeline on a medium-wide pane", async () => {
    stubViewport(false);
    const f = harness();
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    // jsdom has no ResizeObserver: the pane width stays unknown, so the detail
    // docks stacked below the timeline instead of replacing it.
    const dock = document.querySelector(".right-dock.stack") as HTMLElement;
    expect(dock).toBeTruthy();
    expect(document.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false);
    const locator = document.querySelector(".locator") as HTMLElement;
    expect(within(locator).getByRole("button", { name: "× 关闭详情" })).toBeTruthy();
    // Clicking another item in the timeline only changes the inspector.
    await f.user.click(document.querySelector('[data-key="span:s-r1-e"]') as HTMLButtonElement);
    expect(f.api.task).toHaveBeenCalledTimes(1); // still r2's detail
    expect(document.querySelector(".timeline-inspector")!.textContent).toContain("设计工作目标时间轴视图与交互规范");
    // Selection survives closing the detail.
    await f.user.click(within(locator).getByRole("button", { name: "× 关闭详情" }));
    expect(document.querySelector(".run-view")).toBeNull();
    expect(document.querySelector('[data-key="span:s-r1-e"]')!.className).toContain("selected");
  });

  it("docks side by side on a wide pane and switches to stacked without losing state", async () => {
    stubViewport(false);
    const observers: Array<{ callback: ResizeObserverCallback }> = [];
    vi.stubGlobal("ResizeObserver", class {
      callback: ResizeObserverCallback;
      observe = vi.fn(() => { this.callback([], {} as ResizeObserver); });
      disconnect = vi.fn(); unobserve = vi.fn();
      constructor(callback: ResizeObserverCallback) { this.callback = callback; observers.push(this); }
    });
    let paneWidth = 1200;
    const widthOriginal = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get() { return paneWidth; } });
    try {
      const f = harness();
      await openObjective(f);
      // The list pane also reports 1200; the detail panel is the observed one.
      await f.user.dblClick(document.querySelector('[data-key="span:s-r2-e1"]') as HTMLButtonElement);
      await waitFor(() => expect(document.querySelector(".right-dock.side")).toBeTruthy());
      const dock = document.querySelector(".right-dock.side") as HTMLElement;
      expect(dock.querySelector(".timeline-view")!.hasAttribute("hidden")).toBe(false);
      expect(dock.querySelectorAll(".run-view").length).toBe(1);
      // Pin a selection while docked side by side.
      await f.user.click(document.querySelector('[data-key="span:s-r1-e"]') as HTMLButtonElement);
      // Narrow the pane below the dock threshold: stacked, state preserved.
      paneWidth = 800;
      actResize(observers);
      await waitFor(() => expect(document.querySelector(".right-dock.stack")).toBeTruthy());
      expect(document.querySelector(".right-dock.side")).toBeNull();
      expect(document.querySelector('[data-key="span:s-r1-e"]')!.className).toContain("selected");
      expect(document.querySelector(".run-view")).toBeTruthy();
      // Widen again: the side dock returns with the same detail.
      paneWidth = 1200;
      actResize(observers);
      await waitFor(() => expect(document.querySelector(".right-dock.side")).toBeTruthy());
      expect((document.querySelector(".locator .crumbs") as HTMLElement).textContent).toContain("实现 objectives 表");
    } finally {
      if (widthOriginal) Object.defineProperty(HTMLElement.prototype, "clientWidth", widthOriginal);
      else delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
      vi.unstubAllGlobals();
    }
  });

  it("locked details allow the same run's sections but block other runs and closing", async () => {
    stubViewport(false);
    const pending = deferred();
    const f = harness({ pendingContinue: pending });
    await openObjective(f);
    await openSpan(f, "s-r2-e1");
    await f.user.click(await screen.findByRole("tab", { name: "协作与待办" }));
    const input = await screen.findByLabelText("交给下一回合的输入");
    await f.user.type(input, "等待中的接续");
    await f.user.click(screen.getByRole("button", { name: "提交接续输入" }));
    await waitFor(() => expect(document.querySelector(".lock-note")).toBeTruthy());
    // Selection stays available while locked.
    await f.user.click(document.querySelector('[data-key="span:s-r1-e"]') as HTMLButtonElement);
    expect(document.querySelector(".timeline-inspector")!.textContent).toContain("设计工作目标时间轴视图与交互规范");
    // Opening another run's detail is disabled in the pinned card.
    const card = document.querySelector(".inspector-card") as HTMLElement;
    const open = within(card).getByRole("button", { name: "打开详情" });
    expect(open.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(open);
    await waitFor(() => expect(f.api.task).toHaveBeenCalledTimes(1));
    expect((document.querySelector(".locator .crumbs") as HTMLElement).textContent).toContain("实现 objectives 表");
    // The same delegation's other sections stay reachable: dblclick r2's own span.
    await f.user.dblClick(document.querySelector('[data-key="span:s-r2-e2"]') as HTMLButtonElement);
    await waitFor(() => expect(document.querySelector(".lock-note")).toBeTruthy());
    expect((document.querySelector(".locator .crumbs") as HTMLElement).textContent).toContain("实现 objectives 表");
    pending.resolve({ ok: true });
    await waitFor(() => expect(document.querySelector(".lock-note")).toBeNull());
  });
});

function actResize(observers: Array<{ callback: ResizeObserverCallback }>) {
  for (const observer of observers) observer.callback([], {} as ResizeObserver);
}
