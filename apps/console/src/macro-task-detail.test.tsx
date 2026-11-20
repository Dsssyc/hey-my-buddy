import { act, cleanup, fireEvent, render as baseRender, screen, waitFor, within } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";
import { TooltipProvider } from "./components/ui/tooltip";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTasks } from "./MacroTasks";
import { setLanguageChoice } from "./i18n";
import { ApiError, type ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import { listSummary, macroTaskGroups, macroTaskSummary, macroTaskTimelineFixture } from "./macro-task-fixtures";

function render(ui: ReactElement, options?: RenderOptions) {
  return baseRender(ui, { wrapper: TooltipProvider, ...options });
}

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", runtimeIdentity: "fixture-runtime", eventCursor: 0, consoleSession: { id: "session-a" },

    configuration: { explorationEvery: 5, routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, modelResponseWaitSeconds: 60, profileBuddyId: null},
    buddies: [], preferences: [], models: [], modelCorrespondences: [], decisions: [],
 modelServiceConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: {},
  };
}

function taskFixture(runId: string): Task {
  const base: Task = { runId, task: `目标 ${runId}`, state: "completed", owner: "fixture", cwd: `/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-26T01:12:00Z", updatedAt: "2026-09-26T01:12:00Z", acceptedAt: null, reviewRejectedAt: null, reviewRejectionNote: null,
    resultAvailable: true, shutdownConfirmed: true,
    lineage: { kind: "micro-task", parentRunId: null, rootRunId: runId, rootLeadRunId: runId === "r3" ? "r2" : null,
      project: { kind: "git" as const, id: "p1", label: "hey-my-buddy", path: "~/projects/hey-my-buddy" },
      buddy: { harness: "zcode", provider: "bigmodel-api", model: "glm-5", effort: "high" }, routingTask: null } };
  // Every micro task of the macro task has a collaboration record; r2 waits for its Host.
  base.workflow = { checkout: "worktree", projectKind: "git", state: runId === "r2" ? "awaiting-host" : "delivered", awaitingHost: runId === "r2", revision: 1, macroTaskId: "macro-1" };
  return base;
}

/** The compact collaboration record as `workflow_get` sends it. */
function workflowFixture(task: Task) {
  return { checkout: "worktree", project: { kind: "git" }, runId: task.runId, revision: 1, state: task.workflow!.state, buddy: null, delegationAuthority: false,
    routing: null, turns: [], currentTurn: null, activeRequest: null, requests: [], counts: { turns: 0, openRequests: 0 },
    ownedMicroTasks: task.runId === "r2" ? [{ runId: "r3" }] : [], workspaceConflicts: [],
    artifacts: [], integrations: [], finalArtifactId: null, hostConclusion: null, shutdown: null, workspace: { path: task.cwd },
    task: { revision: task.revision, lineage: task.lineage } };
}

type Pending = { promise: Promise<unknown>; resolve: (value: unknown) => void };
function deferred(): Pending {
  let resolve!: (value: unknown) => void;
  const promise = new Promise<unknown>(done => { resolve = done; });
  return { promise, resolve };
}

type HarnessOptions = { writesAvailable?: boolean; secondMacroTask?: boolean; title?: string };

function harness(options: HarnessOptions = {}) {
  const snapshot = snapshotFixture();
  const timeline = macroTaskTimelineFixture();
  if (options.title!) timeline.macroTask.title = options.title!;
  const listed = options.secondMacroTask
    ? [listSummary(timeline.macroTask), macroTaskSummary({ macroTaskId: "run:3f0e", kind: "standalone",
        title: "修复标题回退在 CRLF 输入下的显示", titleSource: "task", lastActivitySeq: 20,
        lastActivityAt: "2026-09-25T10:22:00Z", state: "ended",
        counts: { roots: 1, accepted: 0, failed: 0, active: 0, host: 0, review: 0, ended: 1,
          unfinished: 0, delivered: 0 } })]
    : [listSummary(timeline.macroTask)];
  const tasks = new Map(["r1", "r2", "r3", "r4", "r7"].map(id => [id, taskFixture(id)]));
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    if (operation === "workflow_get") {
      const task = tasks.get(String(params.runId));
      if (!task) throw new Error(`Unknown run: ${String(params.runId)}`);
      return params.includeAudit
        ? { ...workflowFixture(task), audit: { turns: [], continuations: [], requests: [], hostConclusions: [] },
          routingHistory: { entries: [{ routerIndex: 0, exploration: { quota: false, reselected: false, preferredBuddyId: null }, decisionId: "decision-r2-first", status: "completed", source: "model-selection", selectedBuddy: null, routerBuddy: null, routingMode: "fast", reason: "第一轮的路由依据" }], nextCursor: null, total: 1 } }
        : workflowFixture(task);
    }
    if (operation === "message_list") return { messages: [], cursor: 41 };
    throw new Error(`Unexpected command: ${operation}`);
  });
  const timelineReader = vi.fn(async () => ({ timeline: timeline, verifiedAtMs: null }));
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command,
    task: vi.fn(async (runId: string) => structuredClone(tasks.get(runId) ?? taskFixture(runId))),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ groups: macroTaskGroups(listed), totals: { all: listed.length, active: listed.length, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 41, changed: false })),
    macroTaskTimeline: timelineReader,
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<MacroTasks snapshot={snapshot} api={api} refresh={refresh}
    active writesAvailable={options.writesAvailable ?? true} />);
  return { ...view, api, refresh, tasks, timeline, user, listed };
}

async function openMacroMenu(f: ReturnType<typeof harness>) {
  if (!screen.queryByRole("region", { name: "宏任务列表" })) {
    await f.user.click(screen.getByRole("button", { name: "展开宏任务列表" }));
  }
  const row = document.querySelector('[data-macro-task-id][data-selected="true"]') as HTMLElement;
  within(row).getByRole("button", { name: "更多" }).focus();
  await f.user.keyboard("{Enter}");
}

async function openPauseMenu(f: ReturnType<typeof harness>) {
  await openMacroMenu(f);
  return screen.findByRole("menuitem", { name: "暂停" });
}

const openMacroTask = async (f: ReturnType<typeof harness>) => {
  await f.user.click(await screen.findByRole("button", { name: new RegExp(f.listed[0]!.title!.slice(0, 10)) }));
  await waitFor(() => expect(document.querySelector("[data-slot=timeline-axis]")).toBeTruthy());
};
// Layer mode opens details with Enter or a double click; a single click only pins.
const openSpan = async (f: ReturnType<typeof harness>, key: string) => {
  const surface = "[data-slot=timeline-tracks]";
  await f.user.dblClick(document.querySelector(`${surface} [data-key="span:${key}"]`) as SVGElement);
  await screen.findByRole("group", { name: "微任务步骤" });
};

/** jsdom ships no matchMedia: stub the ≤760px viewport decision per test. */
function stubViewport(narrow: boolean) {
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: narrow, addEventListener: () => {}, removeEventListener: () => {} })) as unknown as typeof window.matchMedia;
}

afterEach(() => { cleanup(); setLanguageChoice("browser"); delete (window as { matchMedia?: unknown }).matchMedia; window.history.replaceState(null, "", "#"); });

const detailOf = () => screen.getByRole("region", { name: "微任务详情" });
const stripOf = () => screen.getByRole("group", { name: "微任务步骤" });
const selectedStep = () => (detailOf().querySelector("[data-slot=detail-pane] h3") ?? detailOf()).textContent!;
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
    // The macro task's header stays; the lanes make way for the detail, which carries its own step strip.
    expect(screen.getByRole("heading", { name: "宏任务时间轴：设计、接口与实现" })).toBeTruthy();
    expect(document.querySelector("[data-slot=timeline-tracks]")).toBeNull();
    expect(detailOf().closest("[data-slot=task-work]")).toBeTruthy();
  });

  it("opens from a row of the timeline on its first choice, and from a marker on the step of that marker", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await f.user.click(document.querySelector('[data-key="row:r1"]') as HTMLButtonElement);
    await screen.findByRole("group", { name: "微任务步骤" });
    expect(hashNow()).toMatch(/^#tasks\/macro-1\/r1\/\d+$/);
    expect(selectedStep()).toContain("回合 1");
    fireEvent.keyDown(document.body, { key: "Escape" });
    await f.user.dblClick(document.querySelector('[data-key="span:s-r2-w"]') as SVGElement);
    await screen.findByRole("group", { name: "微任务步骤" });
    expect(hashNow()).toBe("#tasks/macro-1/r2/4");
    expect(document.querySelector('[data-slot=detail-strip] [data-key="span:s-r2-w"]')!.getAttribute("aria-pressed")).toBe("true");
  });

  it.each([false, true])("collapses by its back button and Escape, returning focus to that row (narrow %s)", async narrow => {
    stubViewport(narrow);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r1-e");
    await f.user.click(screen.getByRole("button", { name: "收起详情" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "微任务步骤" })).toBeNull());
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.activeElement?.getAttribute("data-key")).toBe("row:r1");
    await openSpan(f, "s-r1-e");
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("group", { name: "微任务步骤" })).toBeNull());
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.activeElement?.getAttribute("data-key")).toBe("row:r1");
  });

  it("goes back and forward through the browser's history: each opening and each return is a step, choosing a step is not", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    const length = window.history.length;
    await openSpan(f, "s-r2-e1");
    expect(window.history.length).toBe(length + 1);
    expect(hashNow()).toBe("#tasks/macro-1/r2/3");
    await f.user.click(within(stripOf()).getByRole("button", { name: "委派" }));
    expect(hashNow()).toBe("#tasks/macro-1/r2/1");
    expect(window.history.length).toBe(length + 1);
    await traverse("back");
    await waitFor(() => expect(screen.queryByRole("group", { name: "微任务步骤" })).toBeNull());
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.querySelector("[data-slot=timeline-tracks]")).toBeTruthy();
    await traverse("forward");
    await screen.findByRole("group", { name: "微任务步骤" });
    expect(hashNow()).toBe("#tasks/macro-1/r2/1");
    expect(selectedStep()).toContain("委派");
  });

  it("opens from an address on load: the macro task, the micro task and the step", async () => {
    stubViewport(false);
    window.history.replaceState(null, "", "#tasks/macro-1/r2/3");
    const f = harness();
    await screen.findByRole("group", { name: "微任务步骤" });
    expect(f.api.macroTaskTimeline).toHaveBeenCalledWith("macro-1", expect.anything(), expect.anything());
    expect(selectedStep()).toContain("回合 1");
    expect(hashNow()).toBe("#tasks/macro-1/r2/3");
    // A step that does not exist is replaced by the first choice, a micro task that is not read is a failure with the way back.
    cleanup();
    window.history.replaceState(null, "", "#tasks/macro-1/r2/90");
    harness();
    await screen.findByRole("group", { name: "微任务步骤" });
    await waitFor(() => expect(hashNow()).not.toBe("#tasks/macro-1/r2/90"));
    expect(hashNow()).toMatch(/^#tasks\/macro-1\/r2\/\d+$/);
  });

  it("says a micro task whose read fails so, with the way back, and lists the delegation alone for a row the timeline lacks", async () => {
    stubViewport(false);
    window.history.replaceState(null, "", "#tasks/macro-1/r-gone");
    harness();
    expect((await screen.findByRole("alert")).textContent).toContain("Unknown run: r-gone");
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(hashNow()).toBe("#tasks/macro-1");
    cleanup();
    window.history.replaceState(null, "", "#tasks/macro-1/r7");
    harness();
    await screen.findByRole("region", { name: "微任务详情" });
    expect(document.querySelector("[data-slot=detail-strip]")).toBeNull();
    await waitFor(() => expect(detailOf().textContent).toContain("缺少此微任务的记录行"));
  });

  it("keeps the detail mounted while a list refresh temporarily drops the selected row", async () => {
    stubViewport(true);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r1-q");
    // The next list read returns an empty page (a refresh or filter gap); the
    // timeline's own summary keeps the detail alive.
    (f.api.macroTasks as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ groups: [], totals: { all: 0, active: 0, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 41, changed: false });
    // The existing bounded periodic read is the trigger; there is no shell broadcast.
    await waitFor(() => expect(f.api.macroTasks).toHaveBeenCalledTimes(2), { timeout: 4500 });
    expect(screen.getByRole("group", { name: "微任务步骤" })).toBeTruthy();
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
    expect(screen.getByRole("group", { name: "微任务步骤" })).toBeTruthy();
    f.rerender(<MacroTasks snapshot={snapshot} api={f.api} refresh={f.refresh} active />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("group", { name: "微任务步骤" })).toBeNull());
  });

  it("keeps the macro task when the detail goes to a Lead-owned micro task, each at its own address", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await openSpan(f, "s-r2-e1");
    await f.user.click(within(stripOf()).getByRole("button", { name: "委派" }));
    await f.user.click(within(detailOf()).getByRole("button", { name: "补充 schema 升级离线副本的验证测试" }));
    await waitFor(() => expect(f.api.task).toHaveBeenCalledWith("r3", expect.any(AbortSignal)));
    await waitFor(() => expect(hashNow()).toMatch(/^#tasks\/macro-1\/r3\/\d+$/));
    expect(screen.getByRole("heading", { name: "宏任务时间轴：设计、接口与实现" })).toBeTruthy();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(hashNow()).toBe("#tasks/macro-1");
    expect(document.querySelector("[data-slot=timeline-tracks]")).toBeTruthy();
  });

  it("keeps every read working in a read-only session with pause disabled, and asks the board for reads alone", async () => {
    stubViewport(true);
    const f = harness({ writesAvailable: false });
    await openMacroTask(f);
    // A read-only session cannot use the macro-task actions.
    const pause = await openPauseMenu(f);
    expect(pause.getAttribute("aria-disabled")).toBe("true");
    expect(pause.getAttribute("aria-description")).toBe("当前会话只读");
    pause.focus();
    expect(document.activeElement).toBe(pause);
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
    await f.user.click(within(stripOf()).getByRole("button", { name: "委派" }));
    fireEvent.keyDown(document.body, { key: "Escape" });
    await openSpan(f, "s-r2-r");
    expect(selectedStep()).toContain("路由");
    expect(hashNow()).toBe("#tasks/macro-1/r2/2");
  });

  it("opens a row with one click and switches rows while detail is open", async () => {
    stubViewport(false);
    const f = harness();
    await openMacroTask(f);
    await f.user.click(document.querySelector('[data-key="row:r1"]')!);
    await screen.findByRole("group", { name: "微任务步骤" });
    expect(hashNow()).toMatch(/^#tasks\/macro-1\/r1\/\d+$/);
    // The list beside the detail switches micro tasks with one click; the open row is where the reader is.
    await f.user.click(document.querySelector('[data-key="row:r2"]')!);
    await waitFor(() => expect(hashNow()).toMatch(/^#tasks\/macro-1\/r2\/\d+$/));
    expect(document.querySelector('[data-key="row:r1"]')!.getAttribute("aria-current")).toBeNull();
    expect(document.querySelector('[data-key="row:r2"]')!.getAttribute("aria-current")).toBe("true");
  });

});

describe("macro task pause, resume and end", () => {
  const menu = async (f: ReturnType<typeof harness>) => {
    const row = document.querySelector('[data-macro-task-id="macro-1"]')!;
    const button = within(row as HTMLElement).getByRole("button", { name: "更多" });
    button.focus(); await f.user.keyboard("{Enter}");
  };
  const reply = { macroTaskId: "macro-1", duplicate: false };

  it("pauses and resumes without a dialog and reads pause state only from the view", async () => {
    const f = harness(); await openMacroTask(f);
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockResolvedValue(reply);
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "暂停" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(command).toHaveBeenCalledWith("macro_task_pause", { macroTaskId: "macro-1", commandId: expect.any(String) }, "csrf"));
    expect(document.querySelector('[data-slot="macro-overview-header"] [data-kind="paused"]')).toBeNull();
    expect(document.querySelector('[data-slot="macro-work-state"]')).toBeNull();
    cleanup();
    const g = harness();
    g.timeline.macroTask.paused = { pausedAt: "2026-11-11T09:00:00Z", shutdownConfirmed: true };
    g.listed[0]!.paused = g.timeline.macroTask.paused;
    await openMacroTask(g);
    const resume = g.api.command as ReturnType<typeof vi.fn>;
    resume.mockResolvedValue(reply);
    await menu(g); await g.user.click(screen.getByRole("menuitem", { name: "继续" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(resume).toHaveBeenCalledWith("macro_task_resume", { macroTaskId: "macro-1", commandId: expect.any(String) }, "csrf"));
  });

  it("keeps end confirmation busy until the receipt, then closes before the view read completes without a result banner", async () => {
    const f = harness(); await openMacroTask(f);
    f.timeline.macroTask.counts.unfinished = 2;
    const pending = deferred(), pageRead = deferred(), timelineRead = deferred();
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockImplementationOnce(() => pending.promise);
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "结束" }));
    let dialog = await screen.findByRole("dialog", { name: "结束宏任务" });
    expect(within(dialog).getByText("结束 2 个微任务")).toBeTruthy();
    expect(within(dialog).getByText("保留 1 个已交付微任务")).toBeTruthy();
    expect(command).not.toHaveBeenCalled();
    await f.user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect((document.activeElement as HTMLElement).getAttribute("data-slot")).toBe("macro-menu-button");
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "结束" }));
    dialog = await screen.findByRole("dialog", { name: "结束宏任务" });
    const confirm = within(dialog).getByRole("button", { name: "确认结束" }) as HTMLButtonElement;
    await f.user.click(confirm);
    expect(confirm.disabled).toBe(true);
    expect(confirm.getAttribute("aria-busy")).toBe("true");
    expect(confirm.querySelector('[data-kind="live"]')).toBeTruthy();
    expect(confirm.textContent).toBe("确认结束");
    expect((within(dialog).getByRole("button", { name: "取消" }) as HTMLButtonElement).disabled).toBe(true);
    await f.user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(command).toHaveBeenCalledTimes(1);
    (f.api.macroTasks as ReturnType<typeof vi.fn>).mockImplementationOnce(() => pageRead.promise);
    (f.api.macroTaskTimeline as ReturnType<typeof vi.fn>).mockImplementationOnce(() => timelineRead.promise);
    f.refresh.mockRejectedValueOnce(new Error("snapshot offline"));
    await act(async () => pending.resolve(reply));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector('[data-slot="macro-work-state"]')).toBeNull();
    const fresh = { ...f.listed[0]!, counts: { ...f.listed[0]!.counts, unfinished: 0, active: 0, ended: 5 } };
    await act(async () => pageRead.resolve({ groups: macroTaskGroups([fresh]), totals: { all: 1, active: 0, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 42, changed: false }));
    await act(async () => timelineRead.resolve({ timeline: { ...f.timeline, macroTask: { ...f.timeline.macroTask, counts: fresh.counts } }, verifiedAtMs: null }));
    await menu(f);
    expect(screen.queryByRole("menuitem", { name: "结束" })).toBeNull();
  });

  it("uses the menu for each new end, confirming current counts and minting a new identity", async () => {
    const f = harness(); await openMacroTask(f);
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockImplementation(async () => {
      const counts = { ...f.timeline.macroTask.counts, unfinished: 5, delivered: 3 };
      f.listed[0] = { ...f.listed[0]!, counts };
      (f.api.macroTaskTimeline as ReturnType<typeof vi.fn>).mockResolvedValue({ timeline: { ...f.timeline, macroTask: { ...f.timeline.macroTask, counts } }, verifiedAtMs: null });
      return reply;
    });
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "结束" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "结束宏任务" })).getByRole("button", { name: "确认结束" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(f.api.macroTasks).toHaveBeenCalledTimes(2));
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "结束" }));
    const again = await screen.findByRole("dialog", { name: "结束宏任务" });
    expect(within(again).getByText("结束 5 个微任务")).toBeTruthy();
    expect(within(again).getByText("保留 3 个已交付微任务")).toBeTruthy();
    expect(command).toHaveBeenCalledTimes(1);
    await f.user.click(within(again).getByRole("button", { name: "确认结束" }));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2));
    expect(command.mock.calls[0]![1].commandId).not.toBe(command.mock.calls[1]![1].commandId);
  });

  it("shows one unknown notice and replays its original request without another confirmation", async () => {
    const f = harness(); await openMacroTask(f);
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockRejectedValueOnce(new Error("lost reply"));
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "结束" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "结束宏任务" })).getByRole("button", { name: "确认结束" }));
    await screen.findByText("结束的结果未知，可能已生效；可以重试同一请求或刷新核对。");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelectorAll('[data-slot="macro-work-state"] p')).toHaveLength(1);
    command.mockResolvedValueOnce({ ...reply, duplicate: true });
    await f.user.click(screen.getByRole("button", { name: "结束" }));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector('[data-slot="macro-work-state"]')).toBeNull();
  });

  it("shows only the reason of a refused end", async () => {
    const f = harness(); await openMacroTask(f);
    (f.api.command as ReturnType<typeof vi.fn>).mockRejectedValue(new ApiError("REVISION_CONFLICT", "raw"));
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "结束" }));
    await f.user.click(within(await screen.findByRole("dialog", { name: "结束宏任务" })).getByRole("button", { name: "确认结束" }));
    const notice = await screen.findByRole("alert");
    expect(notice.textContent).toBe("记录已更新；请刷新核对后重试。");
    expect(document.querySelectorAll('[data-slot="macro-work-state"] p')).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("uses fresh timeline pause facts when the list read fails", async () => {
    const f = harness(); await openMacroTask(f);
    (f.api.command as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      const fresh = { ...f.timeline, macroTask: { ...f.timeline.macroTask,
        paused: { pausedAt: "2026-11-11T09:00:00Z", shutdownConfirmed: true } } };
      (f.api.macroTaskTimeline as ReturnType<typeof vi.fn>).mockResolvedValue({ timeline: fresh, verifiedAtMs: null });
      (f.api.macroTasks as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("list offline"));
      return reply;
    });
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "暂停" }));
    await waitFor(() => expect(document.querySelector('[data-slot="macro-overview-count-paused"] [data-kind="paused"]')).toBeTruthy());
    expect(f.listed[0]!.paused).toBeNull();
  });

  it("keeps the header with the retained timeline after failure while the entry menu reflects the list, then retries only the timeline read", async () => {
    const f = harness(); await openMacroTask(f);
    const original = structuredClone(f.timeline);
    const paused = { pausedAt: "2026-11-11T09:00:00Z", shutdownConfirmed: true };
    (f.api.command as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      f.listed[0] = { ...f.listed[0]!, title: "列表的新标题", paused, counts: { ...f.listed[0]!.counts, unfinished: 0 } };
      (f.api.macroTaskTimeline as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("timeline offline"));
      return reply;
    });
    await menu(f); await f.user.click(screen.getByRole("menuitem", { name: "暂停" }));
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(document.querySelector('[data-slot="macro-entry"] [data-kind="paused"]')).toBeTruthy());
    const header = document.querySelector('[data-slot="macro-overview-header"]') as HTMLElement;
    expect(header.querySelector('[data-slot="macro-menu-button"]')).toBeNull();
    expect(within(header).getByRole("heading", { name: original.macroTask.title! })).toBeTruthy();
    expect(header.querySelector('[data-kind="paused"]')).toBeNull();
    await menu(f);
    expect(screen.getByRole("menuitem", { name: "继续" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "暂停" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "结束" })).toBeNull();
    await f.user.keyboard("{Escape}");
    const listReads = vi.mocked(f.api.macroTasks).mock.calls.length;
    const timelineReads = vi.mocked(f.api.macroTaskTimeline).mock.calls.length;
    vi.mocked(f.api.macroTaskTimeline).mockResolvedValue({ timeline: { ...original, macroTask: { ...original.macroTask, paused } }, verifiedAtMs: null });
    await f.user.click(within(alert).getByRole("button", { name: "重试连接" }));
    await waitFor(() => expect(header.querySelector('[data-kind="paused"]')).toBeTruthy());
    expect(f.api.macroTaskTimeline).toHaveBeenCalledTimes(timelineReads + 1);
    expect(f.api.macroTasks).toHaveBeenCalledTimes(listReads);
  });
});
