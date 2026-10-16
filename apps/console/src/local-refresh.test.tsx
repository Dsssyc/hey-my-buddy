import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { useState } from "react";
import { Records } from "./Records";
import { TaskDetail } from "./task-detail/TaskDetail";
import { createTaskDetailClient } from "./task-detail/client";
import type { Snapshot, Task } from "./types";

const snapshot = { csrfToken: "csrf" } as Snapshot;
/** The attempt the task view reports on, as the board writes it. */
const attempt = (attemptId: string, result: Record<string, unknown>) => ({ attemptId, startedAt: "2026-10-01T00:00:00Z", finishedAt: "2026-10-01T00:01:00Z", error: null,
  shutdownConfirmed: true, tokenUsage: null, reminders: { overdue: false, possiblyStuck: false, lastNativeEventAt: null, toolInProgress: false }, result });
/** A command execution: it has no collaboration record, so the detail has the one task read to refresh. */
const task = (runId = "command-1", extra: Partial<Task> = {}): Task => ({
  runId, task: `原始任务 ${runId}`, status: "completed", owner: "host", cwd: "~/project",
  revision: 1, createdAt: "2026-10-01T00:00:00Z", updatedAt: "2026-10-09T00:00:00Z",
  acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
  lineage: { kind: "execution", sourceHostId: "host", currentHostId: "host", parentRunId: null, rootRunId: runId, rootLeadRunId: null, routingTask: null,
    project: { id: "project", path: "~/project", label: "project" }, buddy: null },
  selectedAttempt: attempt(`att-${runId}`, { result: { stdout: `结果 ${runId}` } }), ...extra,
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function fixture() {
  const api = {
    task: vi.fn(async () => task()), command: vi.fn(),
    snapshot: vi.fn(), tasks: vi.fn(), macroTasks: vi.fn(), macroTaskTimeline: vi.fn(),
    readVerifiedAt: vi.fn(() => null as number | null),
  } as unknown as ConsoleApi;
  return { api, refresh: vi.fn(async () => null) };
}
const detail = (api: ConsoleApi, runId = "command-1") => <TaskDetail key={runId} client={createTaskDetailClient(api, "csrf")} runId={runId} timeline={null}
  timelineLoading={false} nowMs={Date.parse("2026-10-09T00:00:00Z")} step={1} origin={null} active backTo="records" onStep={() => undefined}
  onBack={() => undefined} onOpenRun={() => undefined} />;
const refreshButton = () => screen.getByRole("button", { name: "重新读取" });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("opened micro task local refresh", () => {
  it("refreshes the current plain command without resetting the detail or reading other APIs", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    const fresh = task("command-1", { selectedAttempt: attempt("att-command-1", { result: { stdout: "更新后的结果" } }) });
    reader.mockResolvedValueOnce(task()).mockResolvedValueOnce(fresh);
    const serverAt = Date.parse("2026-10-03T06:07:08Z");
    (f.api.readVerifiedAt as ReturnType<typeof vi.fn>).mockReturnValue(serverAt);
    render(detail(f.api));
    await screen.findByText("结果 command-1");
    await user.hover(refreshButton());
    expect((await screen.findAllByText(/核对时间 \d{2}:\d{2}:\d{2}/))[0]).toBeTruthy();
    await user.click(refreshButton());
    await waitFor(() => expect(screen.queryByText("更新后的结果")).not.toBeNull());
    expect(reader).toHaveBeenCalledTimes(2);
    expect(reader).toHaveBeenLastCalledWith("command-1", expect.any(AbortSignal));
    for (const name of ["command", "tasks", "snapshot", "macroTasks", "macroTaskTimeline"] as const) expect(f.api[name]).not.toHaveBeenCalled();
    expect(f.refresh).not.toHaveBeenCalled();
  });

  it("keeps the previous result on a malformed response and retries the current task", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    reader.mockResolvedValueOnce(task()).mockResolvedValueOnce(task("wrong-id")).mockResolvedValueOnce(task());
    render(detail(f.api)); await screen.findByText("结果 command-1");
    await user.click(refreshButton());
    expect((await screen.findByRole("alert")).textContent).toContain("读取的内容不完整");
    expect(screen.getByText("结果 command-1")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(reader).toHaveBeenCalledTimes(3); expect(reader.mock.calls.every(([runId]) => runId === "command-1")).toBe(true);
  });

  it("retains the displayed result on a network refresh failure and retries only the task", async () => {
    const f = fixture(); const user = userEvent.setup();
    const reader = f.api.task as ReturnType<typeof vi.fn>;
    reader.mockResolvedValueOnce(task()).mockRejectedValueOnce(new Error("微任务网络中断")).mockResolvedValueOnce(task());
    render(detail(f.api)); await screen.findByText("结果 command-1");
    await user.click(refreshButton());
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
    const view = render(detail(f.api));
    await screen.findByText("结果 command-1");
    await user.click(refreshButton());
    const signal = reader.mock.calls[1][1] as AbortSignal;
    view.rerender(detail(f.api, "command-2"));
    await screen.findByText("结果 command-2"); expect(signal.aborted).toBe(true);
    await act(async () => { late.resolve(task()); });
    expect(screen.queryByText("结果 command-1")).toBeNull();
  });

  it("starts no periodic reads for command details", async () => {
    const f = fixture();
    const interval = vi.spyOn(globalThis, "setInterval"); const timeout = vi.spyOn(globalThis, "setTimeout");
    render(detail(f.api));
    await screen.findByText("结果 command-1");
    expect(interval.mock.calls.filter(([, delay]) => delay === 3000)).toHaveLength(0);
    expect(timeout.mock.calls.filter(([, delay]) => delay === 3000)).toHaveLength(0);
    expect(f.api.command).not.toHaveBeenCalled();
  });
});

function RecordHarness({ api, onOpenMicroTask = vi.fn() }: { api: ConsoleApi; onOpenMicroTask?: (macroTaskId: string, runId: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null);
  return <Records snapshot={snapshot} api={api} active selected={selected} onSelect={setSelected}
    onOpenMicroTask={onOpenMicroTask} onCollapseList={vi.fn()} onExpandList={vi.fn()} />;
}

describe("the list of records and the open detail", () => {
  it("polls its first page, refreshes a selected command row and reads no unrelated APIs", async () => {
    const f = fixture(); const user = userEvent.setup();
    const original = task(); const next = { ...original, revision: 2, task: "更新后的原始命令" };
    const lists = f.api.tasks as ReturnType<typeof vi.fn>;
    lists.mockResolvedValueOnce({ runs: [original], total: 1, nextCursor: null })
      .mockResolvedValue({ runs: [next], total: 1, nextCursor: null });
    (f.api.task as ReturnType<typeof vi.fn>).mockResolvedValue(original);
    render(<RecordHarness api={f.api} />);
    await user.click(await screen.findByRole("button", { name: /原始任务 command-1/ }));
    await screen.findByText("结果 command-1");
    const before = (f.api.task as ReturnType<typeof vi.fn>).mock.calls.length;
    await waitFor(() => expect(lists.mock.calls.length).toBeGreaterThan(1), { timeout: 4500 });
    expect(lists.mock.calls.at(-1)?.[0]).toMatchObject({ rootsOnly: false, limit: 50 });
    await screen.findByText("更新后的原始命令", { selector: "[data-slot=record-title]" });
    await waitFor(() => expect(f.api.task).toHaveBeenCalledTimes(before + 1));
    expect(document.querySelector("[data-slot=record-entry][aria-pressed=true]")).toBeTruthy();
    for (const name of ["command", "snapshot", "macroTasks", "macroTaskTimeline"] as const) expect(f.api[name]).not.toHaveBeenCalled();
    expect(f.refresh).not.toHaveBeenCalled();
  }, 7000);

  it("retains a selected command and scroll when a poll fails, then retries the same first page", async () => {
    const f = fixture(); const user = userEvent.setup();
    const original = task(); const fresh = task("command-1", { revision: 2, task: "重试后的原始命令" });
    const lists = f.api.tasks as ReturnType<typeof vi.fn>;
    lists.mockResolvedValueOnce({ runs: [original], total: 1, nextCursor: "older-page" })
      .mockRejectedValueOnce(new Error("列表本地刷新失败"))
      .mockResolvedValue({ runs: [fresh], total: 1, nextCursor: "server-first-cursor" });
    (f.api.task as ReturnType<typeof vi.fn>).mockResolvedValue(original);
    render(<RecordHarness api={f.api} />);
    await user.click(await screen.findByRole("button", { name: /原始任务 command-1/ }));
    await screen.findByText("结果 command-1");
    const body = screen.getByLabelText("记录条目"); body.scrollTop = 87;
    expect((await screen.findByRole("alert", {}, { timeout: 4500 })).textContent).toContain("读取失败");
    expect(body.scrollTop).toBe(87);
    expect(screen.getByText("结果 command-1")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试连接" }));
    await screen.findByText("重试后的原始命令", { selector: "[data-slot=record-title]" });
    expect(lists.mock.calls.at(-1)?.[0]).toMatchObject({ rootsOnly: false, limit: 50 });
    expect(lists.mock.calls.at(-1)?.[0]).not.toHaveProperty("before");
    expect(body.scrollTop).toBe(87);
    expect(document.querySelector("[data-slot=record-entry][aria-pressed=true]")).toBeTruthy();
    expect(screen.getByRole("button", { name: "更早" })).toBeTruthy();
    expect(f.refresh).not.toHaveBeenCalled();
  }, 7000);

  it("distinguishes an empty history from a search with no matches", async () => {
    const f = fixture(); const user = userEvent.setup();
    (f.api.tasks as ReturnType<typeof vi.fn>).mockResolvedValue({ runs: [], total: 0, nextCursor: null });
    render(<RecordHarness api={f.api} />);
    expect(await screen.findByText("还没有记录。")).toBeTruthy();
    await user.type(screen.getByLabelText("搜索标题、项目、Host 或编号"), "不存在");
    expect(await screen.findByText("没有匹配的记录。")).toBeTruthy();
  });

  it("opens micro tasks in their macro task and command executions in the records detail", async () => {
    const f = fixture(); const user = userEvent.setup();
    const micro = task("micro-1", { workflow: { state: "delivered", awaitingHost: false, hostId: "host", ownerGeneration: 1, revision: 1, macroTaskId: "macro-7" },
      lineage: { kind: "micro-task", sourceHostId: "host", currentHostId: "host", parentRunId: null, rootRunId: "micro-1", rootLeadRunId: null, routingTask: null,
        project: { id: "project", path: "~/project", label: "project" }, buddy: null } });
    const standalone = task("micro-2", { workflow: { state: "delivered", awaitingHost: false, hostId: "host", ownerGeneration: 1, revision: 1 },
      lineage: { ...micro.lineage!, rootRunId: "micro-2" } });
    (f.api.tasks as ReturnType<typeof vi.fn>).mockResolvedValue({ runs: [micro, standalone, task("command-9")], total: 3, nextCursor: null });
    (f.api.task as ReturnType<typeof vi.fn>).mockImplementation(async (runId: string) => task(runId));
    const open = vi.fn();
    render(<RecordHarness api={f.api} onOpenMicroTask={open} />);
    await user.click(await screen.findByRole("button", { name: /原始任务 micro-1/ }));
    expect(open).toHaveBeenLastCalledWith("macro-7", "micro-1");
    await user.click(screen.getByRole("button", { name: /原始任务 micro-2/ }));
    expect(open).toHaveBeenLastCalledWith("run:micro-2", "micro-2");
    expect(f.api.task).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /原始任务 command-9/ }));
    expect(open).toHaveBeenCalledTimes(2);
    await screen.findByText("结果 command-9");
  });
});
