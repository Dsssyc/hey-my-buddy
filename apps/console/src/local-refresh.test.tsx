import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { readVerificationText } from "./api";
import type { Snapshot, Task } from "./types";
import { EvaluationHistory, HISTORY_PAGE_SIZE } from "./EvaluationHistory";
import { TaskDetails } from "./TaskDetails";
import { RunDetailPane } from "./RunDetailPane";
import { Tasks } from "./Tasks";

const snapshot = { csrfToken: "csrf" } as Snapshot;
const task = (runId = "command-1", extra: Partial<Task> = {}): Task => ({
  runId, task: `原始任务 ${runId}`, status: "completed", owner: "host", cwd: "~/project",
  revision: 1, createdAt: "2026-10-01T00:00:00Z", updatedAt: "2026-10-09T00:00:00Z",
  acceptedAt: null, acceptanceVerdict: null,
  selectedAttempt: { result: { result: { stdout: `结果 ${runId}` } } }, ...extra,
});
const historyPage = (revision: number, nextCursor: number | null = null) => ({
  revisions: [{ revision, kind: "human", actor: null, counts: { profiles: 1 }, createdAt: "2026-10-01T00:00:00Z" }],
  total: 42, nextCursor,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function fixture() {
  const api = {
    task: vi.fn(async () => task()), command: vi.fn(async () => historyPage(42, 22)),
    snapshot: vi.fn(), tasks: vi.fn(), objectives: vi.fn(), objectiveTimeline: vi.fn(),
    readVerifiedAt: vi.fn(() => null as number | null),
  } as unknown as ConsoleApi;
  return { api, refresh: vi.fn(async () => null), select: vi.fn() };
}
function Detail({ api, refresh, initial = task() }: { api: ConsoleApi; refresh: () => Promise<Snapshot | null>; initial?: Task }) {
  const [value, setValue] = useState(initial);
  return <TaskDetails task={value} snapshot={snapshot} api={api} refresh={refresh} selectTask={() => {}}
    active onTaskUpdate={setValue} />;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("configuration history local refresh", () => {
  it("refreshes only the bounded current cursor and preserves page, scroll, and server verification", async () => {
    const f = fixture(); const user = userEvent.setup();
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockResolvedValueOnce(historyPage(42, 22)).mockResolvedValueOnce(historyPage(21, 1)).mockResolvedValueOnce(historyPage(20, 1));
    const serverAt = Date.parse("2026-10-02T06:07:08Z");
    (f.api.readVerifiedAt as ReturnType<typeof vi.fn>).mockReturnValue(serverAt);
    const view = render(<EvaluationHistory snapshot={snapshot} api={f.api} active onBack={vi.fn()} />);
    await screen.findByText("V42");
    await user.click(screen.getByRole("button", { name: "加载更早记录" }));
    await screen.findByText("V21");
    const body = view.container.querySelector(".detail-body")!; body.scrollTop = 87;
    await user.click(screen.getByRole("button", { name: "刷新配置更新记录" }));
    await waitFor(() => expect(screen.queryByText("V20")).not.toBeNull());
    expect(command.mock.calls).toEqual([
      ["evaluation_history", { limit: HISTORY_PAGE_SIZE }, "csrf"],
      ["evaluation_history", { limit: HISTORY_PAGE_SIZE, before: 22 }, "csrf"],
      ["evaluation_history", { limit: HISTORY_PAGE_SIZE, before: 22 }, "csrf"],
    ]);
    expect(body.scrollTop).toBe(87);
    expect(screen.getByRole("button", { name: "较新记录" })).toBeTruthy();
    expect(screen.queryByText(readVerificationText(serverAt))).not.toBeNull();
    for (const name of ["task", "tasks", "snapshot", "objectives", "objectiveTimeline"] as const) expect(f.api[name]).not.toHaveBeenCalled();
  });

  it("retains a failed page and timestamp and retries only that page", async () => {
    const f = fixture(); const user = userEvent.setup();
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockResolvedValueOnce(historyPage(42)).mockRejectedValueOnce(new Error("离线更新记录")).mockResolvedValueOnce(historyPage(41));
    render(<EvaluationHistory snapshot={snapshot} api={f.api} active onBack={vi.fn()} />);
    await screen.findByText("V42");
    expect(screen.getByText("核对时间未记录")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "刷新配置更新记录" }));
    await screen.findByRole("alert");
    expect(screen.getByText("V42")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await screen.findByText("V41");
    expect(command.mock.calls.every(([operation, params]) => operation === "evaluation_history" && params.limit === HISTORY_PAGE_SIZE)).toBe(true);
  });

  it("ignores a late refresh after leaving and reentering the page", async () => {
    const f = fixture(); const user = userEvent.setup(); const late = deferred<ReturnType<typeof historyPage>>();
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockResolvedValueOnce(historyPage(42)).mockReturnValueOnce(late.promise).mockResolvedValueOnce(historyPage(40));
    const view = render(<EvaluationHistory snapshot={snapshot} api={f.api} active onBack={vi.fn()} />);
    await screen.findByText("V42"); await user.click(screen.getByRole("button", { name: "刷新配置更新记录" }));
    view.rerender(<EvaluationHistory snapshot={snapshot} api={f.api} active={false} onBack={vi.fn()} />);
    view.rerender(<EvaluationHistory snapshot={snapshot} api={f.api} active onBack={vi.fn()} />);
    await screen.findByText("V40"); await act(async () => { late.resolve(historyPage(99)); });
    expect(screen.queryByText("V99")).toBeNull(); expect(screen.getByText("V40")).toBeTruthy();
  });
});

describe("opened microtask local refresh", () => {
  it("refreshes the current plain command without resetting the detail or reading other APIs", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    const fresh = task("command-1", { selectedAttempt: { result: { result: { stdout: "更新后的结果" } } } });
    reader.mockResolvedValueOnce(task()).mockResolvedValueOnce(fresh);
    const serverAt = Date.parse("2026-10-03T06:07:08Z");
    (f.api.readVerifiedAt as ReturnType<typeof vi.fn>).mockReturnValue(serverAt);
    const view = render(<Detail api={f.api} refresh={f.refresh} />);
    await screen.findByText("结果 command-1");
    const disclosure = screen.getByText("原始任务").closest("details")!; fireEvent.click(screen.getByText("原始任务")); disclosure.open = true;
    const body = view.container.querySelector(".detail-body")!; body.scrollTop = 73;
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    await waitFor(() => expect(screen.queryByText("更新后的结果")).not.toBeNull());
    expect(reader).toHaveBeenCalledTimes(2);
    expect(reader).toHaveBeenLastCalledWith("command-1", expect.any(AbortSignal));
    expect(disclosure.open).toBe(true); expect(body.scrollTop).toBe(73);
    expect(screen.queryByText(readVerificationText(serverAt))).not.toBeNull();
    for (const name of ["command", "tasks", "snapshot", "objectives", "objectiveTimeline"] as const) expect(f.api[name]).not.toHaveBeenCalled();
    expect(f.refresh).not.toHaveBeenCalled();
  });

  it("keeps the previous result on malformed response and retries the current task", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    reader.mockResolvedValueOnce(task()).mockResolvedValueOnce(task("wrong-id")).mockResolvedValueOnce(task());
    render(<Detail api={f.api} refresh={f.refresh} />); await screen.findByText("结果 command-1");
    expect(screen.getByText("核对时间未记录")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeNull());
    expect(screen.getByRole("alert").textContent).toContain("委派详情不完整");
    expect(screen.getByText("结果 command-1")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(reader).toHaveBeenCalledTimes(3); expect(reader.mock.calls.every(([runId]) => runId === "command-1")).toBe(true);
  });

  it("retains the displayed result on a network refresh failure and retries only the task", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    reader.mockResolvedValueOnce(task()).mockRejectedValueOnce(new Error("微任务网络中断")).mockResolvedValueOnce(task());
    render(<Detail api={f.api} refresh={f.refresh} />); await screen.findByText("结果 command-1");
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    expect((await screen.findByRole("alert")).textContent).toContain("微任务网络中断");
    expect(screen.getByText("结果 command-1")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(reader).toHaveBeenCalledTimes(3); expect(f.api.command).not.toHaveBeenCalled(); expect(f.refresh).not.toHaveBeenCalled();
  });

  it("aborts the previous record and ignores its late reply after selection changes", async () => {
    const f = fixture(); const user = userEvent.setup(); const late = deferred<Task>();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    reader.mockResolvedValueOnce(task()).mockReturnValueOnce(late.promise).mockResolvedValueOnce(task("command-2"));
    const update = vi.fn();
    const view = render(<TaskDetails task={task()} snapshot={snapshot} api={f.api} refresh={f.refresh} selectTask={f.select} active onTaskUpdate={update} />);
    await screen.findByText("结果 command-1");
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    const signal = reader.mock.calls[1][1] as AbortSignal;
    view.rerender(<TaskDetails task={task("command-2")} snapshot={snapshot} api={f.api} refresh={f.refresh} selectTask={f.select} active onTaskUpdate={update} />);
    await screen.findByText("结果 command-2"); expect(signal.aborted).toBe(true);
    await act(async () => { late.resolve(task()); });
    expect(screen.queryByText("结果 command-1")).toBeNull(); expect(update.mock.calls.map(([value]) => value.runId)).toEqual(["command-1", "command-2"]);
  });

  it("makes the same local refresh available to timeline detail without a snapshot or history refresh", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    reader.mockResolvedValue(task());
    render(<RunDetailPane objectiveTitle="工作目标" target={{ runId: "command-1" }} snapshot={snapshot} api={f.api}
      refresh={f.refresh} active onBack={vi.fn()} onNavigate={vi.fn()} />);
    await screen.findByRole("button", { name: "刷新微任务详情" });
    await screen.findByText("结果 command-1");
    const before = reader.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    await waitFor(() => expect(reader.mock.calls.length).toBe(before + 1));
    expect(f.refresh).not.toHaveBeenCalled(); expect(f.api.tasks).not.toHaveBeenCalled(); expect(f.api.command).not.toHaveBeenCalled();
  });

  it("refreshes a governed detail at a newer revision while preserving its tab and the workflow read schedule", async () => {
    const f = fixture(); const user = userEvent.setup();
    const initial = task("governed-1", { workflow: { state: "executing", awaitingHost: false, hostId: "host", ownerGeneration: 1, revision: 1 } });
    const next = { ...initial, revision: 2, task: "更新后的微任务", workflow: { ...initial.workflow!, revision: 2 } };
    const command = f.api.command as ReturnType<typeof vi.fn>;
    command.mockResolvedValue({ governed: true, runId: initial.runId, revision: 1, state: "executing", awaitingHost: false,
      hostId: "host", ownerGeneration: 1, continuationCount: 0, currentTurn: null, activeRequest: null,
      children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task: initial });
    const reader = f.api.task as ReturnType<typeof vi.fn>; reader.mockResolvedValue(next);
    const timeout = vi.spyOn(globalThis, "setTimeout");
    render(<Detail api={f.api} refresh={f.refresh} initial={initial} />);
    await screen.findByRole("tab", { name: "执行记录" });
    await user.click(screen.getByRole("tab", { name: "执行记录" }));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1));
    expect(reader).not.toHaveBeenCalled();
    const periodicBefore = timeout.mock.calls.filter(([, delay]) => delay === 3000).length;
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    await screen.findByRole("heading", { name: /更新后的微任务/ });
    expect(reader).toHaveBeenCalledTimes(1); expect(command).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("tab", { name: "执行记录" }).getAttribute("aria-selected")).toBe("true");
    expect(timeout.mock.calls.filter(([, delay]) => delay === 3000)).toHaveLength(periodicBefore);
    expect(f.refresh).not.toHaveBeenCalled(); expect(f.api.tasks).not.toHaveBeenCalled();
  });

  it("does not attach an older task verification time to newer workflow facts", async () => {
    const f = fixture(); const user = userEvent.setup();
    const original = task("governed-1", { workflow: { state: "executing", awaitingHost: false, hostId: "host", ownerGeneration: 1, revision: 1 } });
    const serverAt = Date.parse("2026-10-03T06:07:08Z");
    (f.api.task as ReturnType<typeof vi.fn>).mockResolvedValue(original);
    (f.api.command as ReturnType<typeof vi.fn>).mockResolvedValue({ governed: true, runId: original.runId, revision: 1, state: "executing", awaitingHost: false,
      hostId: "host", ownerGeneration: 1, continuationCount: 0, currentTurn: null, activeRequest: null,
      children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task: original });
    const update = vi.fn();
    const view = render(<TaskDetails task={original} snapshot={snapshot} api={f.api} refresh={f.refresh} selectTask={f.select} active onTaskUpdate={update} />);
    await screen.findByRole("tab", { name: "概览" });
    (f.api.readVerifiedAt as ReturnType<typeof vi.fn>).mockImplementation(value => value === original ? serverAt : null);
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    await waitFor(() => expect(screen.queryByText(readVerificationText(serverAt))).not.toBeNull());
    const next = { ...original, revision: 2, workflow: { ...original.workflow!, revision: 2 } };
    view.rerender(<TaskDetails task={next} snapshot={snapshot} api={f.api} refresh={f.refresh} selectTask={f.select} active onTaskUpdate={update} />);
    expect(screen.queryByText(readVerificationText(serverAt))).toBeNull();
    expect(screen.getByText("核对时间未记录")).toBeTruthy();
  });

  it("releases busy state when newer workflow facts cancel a pending task refresh", async () => {
    const f = fixture(); const user = userEvent.setup(); const pending = deferred<Task>();
    const original = task("governed-1", { workflow: { state: "executing", awaitingHost: false, hostId: "host", ownerGeneration: 1, revision: 1 } });
    (f.api.task as ReturnType<typeof vi.fn>).mockReturnValue(pending.promise);
    (f.api.command as ReturnType<typeof vi.fn>).mockResolvedValue({ governed: true, runId: original.runId, revision: 1, state: "executing", awaitingHost: false,
      hostId: "host", ownerGeneration: 1, continuationCount: 0, currentTurn: null, activeRequest: null,
      children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task: original });
    const update = vi.fn();
    const view = render(<TaskDetails task={original} snapshot={snapshot} api={f.api} refresh={f.refresh} selectTask={f.select} active onTaskUpdate={update} />);
    await screen.findByRole("tab", { name: "概览" });
    await user.click(screen.getByRole("button", { name: "刷新微任务详情" }));
    const signal = (f.api.task as ReturnType<typeof vi.fn>).mock.calls[0][1] as AbortSignal;
    expect(screen.getByRole("button", { name: "刷新微任务详情" })).toHaveProperty("disabled", true);
    const next = { ...original, revision: 2, workflow: { ...original.workflow!, revision: 2 } };
    view.rerender(<TaskDetails task={next} snapshot={snapshot} api={f.api} refresh={f.refresh} selectTask={f.select} active onTaskUpdate={update} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新微任务详情" })).toHaveProperty("disabled", false));
    expect(signal.aborted).toBe(true);
    const count = update.mock.calls.length;
    await act(async () => { pending.resolve(original); });
    expect(update).toHaveBeenCalledTimes(count);
  });

  it("refreshes a selected raw command list at a newer revision without reading its detail", async () => {
    const f = fixture(); const user = userEvent.setup();
    const original = task(); const next = { ...original, revision: 2, task: "更新后的原始命令" };
    let listReads = 0;
    const lists = f.api.tasks as ReturnType<typeof vi.fn>;
    lists.mockImplementation(async (query: { rootsOnly?: boolean }) => {
      if (query.rootsOnly) return { runs: [], total: 0, nextCursor: null };
      ++listReads;
      return { runs: [listReads === 1 ? original : next], total: 1, nextCursor: null };
    });
    (f.api.task as ReturnType<typeof vi.fn>).mockResolvedValue(original);
    render(<Tasks snapshot={snapshot} api={f.api} refresh={f.refresh} active />);
    await screen.findByText("没有匹配的委派");
    await user.click(screen.getByRole("checkbox", { name: "显示协助任务与内部执行" }));
    await user.click(await screen.findByRole("button", { name: /原始任务 command-1/ }));
    await screen.findByText("结果 command-1");
    const before = (f.api.task as ReturnType<typeof vi.fn>).mock.calls.length;
    const pagesBefore = lists.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "刷新全部执行记录" }));
    await screen.findByRole("heading", { name: /更新后的原始命令/ });
    expect(lists).toHaveBeenCalledTimes(pagesBefore + 1);
    expect(lists.mock.calls.at(-1)?.[0]).toMatchObject({ rootsOnly: false, limit: 50 });
    expect(f.api.task).toHaveBeenCalledTimes(before);
    expect(f.api.command).not.toHaveBeenCalled(); expect(f.refresh).not.toHaveBeenCalled();
    expect(document.querySelector(".task-row.selected")?.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("结果 command-1")).toBeTruthy();
  });

  it.each([null, "older-page"])("retries the list's failed first page with cursor %s while retaining command selection and scroll", async cursor => {
    const f = fixture(); const user = userEvent.setup();
    const original = task(); const fresh = task("command-1", { revision: 2, task: "重试后的原始命令" });
    const lists = f.api.tasks as ReturnType<typeof vi.fn>;
    lists.mockResolvedValueOnce({ runs: [original], total: 1, nextCursor: cursor })
      .mockRejectedValueOnce(new Error("列表本地刷新失败"))
      .mockResolvedValueOnce({ runs: [fresh], total: 1, nextCursor: "server-first-cursor" });
    (f.api.task as ReturnType<typeof vi.fn>).mockResolvedValue(original);
    const view = render(<Tasks snapshot={snapshot} api={f.api} refresh={f.refresh} active />);
    await user.click(await screen.findByRole("button", { name: /原始任务 command-1/ }));
    await screen.findByText("结果 command-1");
    const body = view.container.querySelector(".list-scroll")!; body.scrollTop = 87;
    const detailReads = (f.api.task as ReturnType<typeof vi.fn>).mock.calls.length;
    await user.click(screen.getByRole("button", { name: "刷新全部执行记录" }));
    expect((await screen.findByRole("alert")).textContent).toContain("列表本地刷新失败");
    expect(body.scrollTop).toBe(87);
    expect(screen.getByText("结果 command-1")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(lists).toHaveBeenCalledTimes(3));
    await screen.findByRole("heading", { name: /重试后的原始命令/ });
    expect(lists).toHaveBeenCalledTimes(3);
    expect(lists.mock.calls[2][0]).toEqual(lists.mock.calls[1][0]);
    expect(lists.mock.calls[2][0]).not.toHaveProperty("before");
    expect(body.scrollTop).toBe(87);
    expect(document.querySelector(".task-row.selected")?.getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByRole("button", { name: "加载更早记录" }) !== null).toBe(cursor !== null);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(f.api.task).toHaveBeenCalledTimes(detailReads);
    for (const name of ["command", "snapshot", "objectives", "objectiveTimeline"] as const) expect(f.api[name]).not.toHaveBeenCalled();
    expect(f.refresh).not.toHaveBeenCalled();
  });

  it("starts no periodic reads for command details or configuration history", async () => {
    const f = fixture();
    const interval = vi.spyOn(globalThis, "setInterval"); const timeout = vi.spyOn(globalThis, "setTimeout");
    render(<><Detail api={f.api} refresh={f.refresh} /><EvaluationHistory snapshot={snapshot} api={f.api} active onBack={vi.fn()} /></>);
    await act(async () => {});
    expect(interval).not.toHaveBeenCalled(); expect(timeout).not.toHaveBeenCalled();
  });
});
