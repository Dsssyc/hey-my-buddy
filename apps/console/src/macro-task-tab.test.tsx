import { TooltipProvider } from "./components/ui/tooltip";
import type { ReactElement } from "react";
import { act, cleanup, fireEvent, render as baseRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTasks } from "./MacroTasks";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task, TaskQuery } from "./types";
import { macroTaskTimelineFixture, OBSERVED_AT } from "./macro-task-fixtures";
import { clockTime } from "./macro-task-display";

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "session-a", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies: [], preferences: [], modelServiceNotes: [], decisions: [],
 modelServiceConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: {},
  };
}

const rawRecord = (): Task => ({ runId: "cmd-9", task: "外部命令记录", status: "completed", owner: "fixture",
  cwd: "/worktrees/cmd-9", revision: 1, createdAt: "2026-09-26T05:00:00Z", updatedAt: "2026-09-26T05:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
  lineage: { kind: "execution", sourceHostId: null, currentHostId: null, parentRunId: null, rootRunId: "cmd-9", rootLeadRunId: null,
    project: { id: "px", label: "外部", path: null }, buddy: null } });

function harness() {
  const snapshot = snapshotFixture();
  const timeline = macroTaskTimelineFixture();
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command: vi.fn(),
    task: vi.fn(async () => rawRecord()),
    tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : [rawRecord()], total: 1, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [timeline.macroTask], totals: { all: 1, active: 1, host: 0, review: 0 }, nextCursor: null, cursor: 41, changed: false })),
    macroTaskTimeline: vi.fn(async () => ({ timeline, verifiedAtMs: null })),
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<MacroTasks snapshot={snapshot} api={api} refresh={refresh} active />);
  return { ...view, api, refresh, user, timeline };
}

afterEach(() => cleanup());

describe("微任务记录 tab views", () => {
  it("opens on the work macro task view with the unselected placeholder", async () => {
    const f = harness();
    await screen.findByRole("heading", { name: "选择一个宏任务" });
    expect(screen.getByRole("region", { name: "宏任务列表" })).toBeTruthy();
    expect(screen.getByText(/选择一个宏任务/)).toBeTruthy();
    await waitFor(() => expect(f.api.macroTasks).toHaveBeenCalledTimes(1));
    expect(f.api.tasks).not.toHaveBeenCalled();
  });

  it("keeps the raw, raw task history reachable through the labelled secondary switch", async () => {
    const f = harness();
    const user = f.user;
    await screen.findByRole("heading", { name: "选择一个宏任务" });
    await user.click(screen.getByRole("button", { name: "记录" }));
    // The existing records view: its toolbar, checkbox and rows stay as before.
    expect(await within(screen.getByRole("region", { name: "微任务列表" })).findByRole("heading", { name: "全部执行记录" })).toBeTruthy();
    expect(screen.getByLabelText("显示协助任务与内部执行")).toHaveProperty("checked", false);
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    expect(await screen.findByRole("button", { name: /外部命令记录/ })).toBeTruthy();
    expect(f.api.tasks).toHaveBeenCalledWith(expect.objectContaining({ rootsOnly: false }), expect.any(AbortSignal));
    // Switching back keeps the macroTasks data and hides the records view.
    await user.click(screen.getByRole("button", { name: "宏任务" }));
    expect(await screen.findByRole("button", { name: /宏任务时间轴：设计、接口与实现/ })).toBeTruthy();
    const checkbox = screen.getByLabelText("显示协助任务与内部执行") as HTMLInputElement;
    expect(checkbox.closest("[hidden]")).not.toBeNull();
    expect(checkbox.checked).toBe(true);
  });

  it("advances the timeline's 现在 clock across an unchanged poll through the parent wiring", async () => {
    // The parent forwards the hook's displayObservedAt into MacroTaskTimeline;
    // an unchanged poll (same body, same source observedAt) must still move
    // the rendered 现在 marker forward. Real renderer + real parent + real
    // hook: removing the forwarding freezes the marker and fails this test.
    vi.useFakeTimers();
    // Align the controlled client clock with the source anchor at load: the
    // mock carries no response date, so the hook's justified local clock IS
    // this controlled time.
    vi.setSystemTime(new Date(OBSERVED_AT));
    try {
      const f = harness();
      await act(async () => { await vi.advanceTimersByTimeAsync(200); });
      const row = screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
      fireEvent.click(row);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      const chipAtLoad = document.querySelector(".now-chip")!.textContent!;
      expect(chipAtLoad).toContain(clockTime(OBSERVED_AT));
      // One unchanged poll 61 seconds later: the display clock advanced past
      // the source anchor's minute.
      await act(async () => { await vi.advanceTimersByTimeAsync(61_000); });
      const chipAdvanced = document.querySelector(".now-chip")!.textContent!;
      expect(chipAdvanced).not.toBe(chipAtLoad);
      expect(chipAdvanced).toContain(clockTime(new Date(Date.parse(OBSERVED_AT) + 61_000).toISOString()));
      f.unmount();
    } finally {
      vi.useRealTimers();
      cleanup();
    }
  });

  it("keeps the selected detail mounted while an idle reorder refreshes the list", async () => {
    const f = harness();
    const selectedRow = await screen.findByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
    fireEvent.click(selectedRow);
    await waitFor(() => expect(f.api.macroTaskTimeline).toHaveBeenCalled());
    const macroTask = f.timeline.macroTask;
    const newer = { ...macroTask, macroTaskId: "macro-new", title: "刚有活动的目标", lastActivitySeq: macroTask.lastActivitySeq + 1 };
    vi.mocked(f.api.macroTasks).mockResolvedValueOnce({ macroTasks: [newer, macroTask], totals: { all: 2, active: 2, host: 0, review: 0 },
      nextCursor: null, cursor: 42, changed: true });
    let finishRefresh!: (value: Awaited<ReturnType<ConsoleApi["macroTasks"]>>) => void;
    vi.mocked(f.api.macroTasks).mockImplementationOnce(() => new Promise(resolve => { finishRefresh = resolve; }));
    await waitFor(() => expect(f.api.macroTasks).toHaveBeenCalledTimes(3), { timeout: 4500 });
    expect(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("heading", { name: "选择一个宏任务" })).toBeNull();
    expect(screen.getByRole("complementary", { name: "宏任务详情" })).toBeTruthy();
    finishRefresh({ macroTasks: [newer, macroTask], totals: { all: 2, active: 2, host: 0, review: 0 }, nextCursor: null, cursor: 42, changed: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /刚有活动的目标/ })).toBeTruthy());
    expect(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ }).getAttribute("aria-pressed")).toBe("true");
  });
});

function render(ui: ReactElement) { return baseRender(ui, { wrapper: TooltipProvider }); }
