import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Objectives } from "./Objectives";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task, TaskQuery } from "./types";
import { objectiveTimelineFixture } from "./objective-fixtures";

function snapshotFixture(): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "session-a", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, fastRouterProfileId: null, reviewRouterProfileId: null , defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles: [], cards: [], preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { runs: [], total: 0 },
    capabilities: { evaluationWriteGate: true },
  };
}

const rawRecord = (): Task => ({ runId: "cmd-9", task: "外部命令记录", status: "completed", owner: "fixture",
  cwd: "/worktrees/cmd-9", revision: 1, createdAt: "2026-09-26T05:00:00Z", acceptedAt: null, acceptanceVerdict: null,
  delegation: { kind: "execution", sourceHostId: null, currentHostId: null, parentRunId: null, rootRunId: "cmd-9",
    project: { id: "px", label: "外部", path: null }, configuration: null } });

function harness() {
  const snapshot = snapshotFixture();
  const timeline = objectiveTimelineFixture();
  const api = {
    snapshot: vi.fn(async () => structuredClone(snapshot)),
    command: vi.fn(),
    task: vi.fn(async () => rawRecord()),
    tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : [rawRecord()], total: 1, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [timeline.objective], total: 1, nextCursor: null, cursor: 41, changed: false })),
    objectiveTimeline: vi.fn(async () => timeline),
  } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => structuredClone(snapshot));
  const user = userEvent.setup();
  const view = render(<Objectives snapshot={snapshot} api={api} refresh={refresh} active />);
  return { ...view, api, refresh, user, timeline };
}

afterEach(() => cleanup());

describe("委派记录 tab views", () => {
  it("opens on the work-objective view with the unselected placeholder", async () => {
    const f = harness();
    await screen.findByRole("heading", { name: "选择一个工作目标" });
    expect(screen.getByRole("region", { name: "工作目标列表" })).toBeTruthy();
    expect(screen.getByText(/选择一个工作目标/)).toBeTruthy();
    await waitFor(() => expect(f.api.objectives).toHaveBeenCalledTimes(1));
    expect(f.api.tasks).not.toHaveBeenCalled();
  });

  it("keeps the raw, non-governed history reachable through the labelled secondary switch", async () => {
    const f = harness();
    const user = f.user;
    await screen.findByRole("heading", { name: "选择一个工作目标" });
    await user.click(screen.getByRole("button", { name: "全部执行记录" }));
    // The existing records view: its toolbar, checkbox and rows stay as before.
    expect(await within(screen.getByRole("region", { name: "委派列表" })).findByRole("heading", { name: "委派记录" })).toBeTruthy();
    expect(screen.getByLabelText("显示协助任务与内部执行")).toHaveProperty("checked", false);
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    expect(await screen.findByRole("button", { name: /外部命令记录/ })).toBeTruthy();
    expect(f.api.tasks).toHaveBeenCalledWith(expect.objectContaining({ rootsOnly: false }), expect.any(AbortSignal));
    // Switching back keeps the objectives data and hides the records view.
    await user.click(screen.getByRole("button", { name: "工作目标" }));
    expect(await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ })).toBeTruthy();
    const checkbox = screen.getByLabelText("显示协助任务与内部执行") as HTMLInputElement;
    expect(checkbox.closest("[hidden]")).not.toBeNull();
    expect(checkbox.checked).toBe(true);
  });

  it("keeps the selected detail mounted while an idle reorder refreshes the list", async () => {
    const f = harness();
    const selectedRow = await screen.findByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    fireEvent.click(selectedRow);
    await waitFor(() => expect(f.api.objectiveTimeline).toHaveBeenCalled());
    const objective = f.timeline.objective;
    const newer = { ...objective, objectiveId: "new-objective", title: "刚有活动的目标", lastActivitySeq: objective.lastActivitySeq + 1 };
    vi.mocked(f.api.objectives).mockResolvedValueOnce({ objectives: [newer, objective], total: 2,
      nextCursor: null, cursor: 42, changed: true });
    let finishRefresh!: (value: Awaited<ReturnType<ConsoleApi["objectives"]>>) => void;
    vi.mocked(f.api.objectives).mockImplementationOnce(() => new Promise(resolve => { finishRefresh = resolve; }));
    await waitFor(() => expect(f.api.objectives).toHaveBeenCalledTimes(3), { timeout: 4500 });
    expect(screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("heading", { name: "选择一个工作目标" })).toBeNull();
    expect(screen.getByRole("complementary", { name: "工作目标详情" })).toBeTruthy();
    finishRefresh({ objectives: [newer, objective], total: 2, nextCursor: null, cursor: 42, changed: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /刚有活动的目标/ })).toBeTruthy());
    expect(screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ }).getAttribute("aria-pressed")).toBe("true");
  });
});
