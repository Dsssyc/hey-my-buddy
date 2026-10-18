import type { ComponentProps, ReactElement } from "react";
import { createRef } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "i18next";
import { ApiError } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { MacroTasks } from "./MacroTasks";
import { MacroTaskDeleteDialog } from "./MacroTaskDeleteDialog";
import { TooltipProvider } from "./components/ui/tooltip";
import { createAuthorityLatch } from "./console-session";
import { macroTaskSummary, macroTaskTimelineFixture } from "./macro-task-fixtures";
import type { MacroTaskSummary, MacroTaskTimeline } from "./macro-task-types";
import type { DeletionBlocker, DeletionBlockerKind, MacroTaskDeletionPreview } from "./macro-task-deletion";

function snapshotFixture(): Snapshot {
  return { csrfToken: "synthetic-csrf", eventCursor: 0, consoleSession: { id: "session-fixture", canWrite: true, reason: null },
    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review", routingBudget: "standard" },
    buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], decisions: [], tasks: { pendingCount: 0 }, capabilities: {} };
}
function preview(extra: Partial<MacroTaskDeletionPreview> = {}): MacroTaskDeletionPreview {
  return { macroTaskId: "macro-1", microTaskCount: 73, unconcludedFailureCount: 2,
    scope: { artifacts: { count: 14, bytes: 6_500_000 }, sessions: { count: 11, bytes: 50_331_648 }, checkouts: 3, gitRefs: 12 },
    blockerTotal: 0, blockers: [], backupRetention: "until-backup-rotation", deletionState: null, ...extra };
}
function receipt(extra: Record<string, unknown> = {}) {
  return { deletionId: "del-fixture", macroTaskId: "macro-1", state: "deleted", deletedAt: "2026-10-18T08:00:00Z",
    microTaskCount: 73, duplicate: false, ...extra };
}
function blocker(kind: DeletionBlockerKind, taskId = "r1"): DeletionBlocker {
  return { taskId, runId: taskId, state: "completed", runState: "delivered", title: `作者标题 ${taskId}`, titleSource: "title",
    reason: { code: `macro-task-deletion.${kind}`, params: {} }, attemptIds: [] };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function wrap(ui: ReactElement) { return <TooltipProvider>{ui}</TooltipProvider>; }
function dialogHarness(overrides: Partial<ComponentProps<typeof MacroTaskDeleteDialog>> = {}) {
  const command = vi.fn(async (_operation: string, _params: unknown, _csrf: string): Promise<unknown> => preview());
  const onClose = vi.fn(), onDeleted = vi.fn();
  const props: ComponentProps<typeof MacroTaskDeleteDialog> = { summary: macroTaskSummary(),
    open: true, returnFocus: createRef<HTMLButtonElement>(), onClose, onDeleted, api: { command } as unknown as ConsoleApi,
    snapshot: snapshotFixture(), writesAvailable: true, ...overrides };
  const view = render(wrap(<MacroTaskDeleteDialog {...props}/>));
  return { ...view, command, onClose, onDeleted, props, user: userEvent.setup(),
    update: (next: Partial<typeof props>) => { Object.assign(props, next); view.rerender(wrap(<MacroTaskDeleteDialog {...props}/>)); } };
}
async function confirm() { await userEvent.click(await screen.findByRole("button", { name: "删除" })); }

beforeEach(() => { window.history.replaceState(null, "", "#tasks"); localStorage.clear(); });
afterEach(async () => { cleanup(); vi.restoreAllMocks(); await i18n.changeLanguage("zh"); });

describe("macro task deletion confirmation", () => {
  it("names the fixed target, reads complete backend counts and safely focuses cancel", async () => {
    const f = dialogHarness();
    const dialog = await screen.findByRole("dialog", { name: `删除「${f.props.summary.title}」` });
    await screen.findByText("73 个，其中 2 个取消或失败而没有结论");
    expect(f.command).toHaveBeenCalledWith("macro_task_deletion_preview", { macroTaskId: "macro-1" }, "synthetic-csrf");
    expect(dialog.querySelector("[data-slot=macro-delete-scope]")?.textContent).toContain("封存的产物14 份，共 6.2 MB会话11 个，共 48.0 MB检出与 Git 引用3 个检出，12 个引用");
    expect(dialog.textContent).toContain("备份轮换");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "取消" }));
    await f.user.keyboard("{Enter}");
    expect(f.onClose).toHaveBeenCalledOnce();
    expect(f.command).toHaveBeenCalledTimes(1);
  });

  it.each([
    { language: "zh", name: "zero", scope: { artifacts: { count: 0, bytes: 0 }, sessions: { count: 0, bytes: 0 }, checkouts: 0, gitRefs: 0 },
      expected: ["0 份，共 0 B", "0 个，共 0 B", "0 个检出，0 个引用"] },
    { language: "en", name: "zero", scope: { artifacts: { count: 0, bytes: 0 }, sessions: { count: 0, bytes: 0 }, checkouts: 0, gitRefs: 0 },
      expected: ["0; total 0 B", "0; total 0 B", "Checkouts: 0; Git refs: 0"] },
    { language: "zh", name: "unknown", scope: { artifacts: { count: null, bytes: null }, sessions: { count: null, bytes: null }, checkouts: null, gitRefs: null },
      expected: ["数量未知，体积未知", "数量未知，体积未知", "检出数量未知，引用数量未知"] },
    { language: "en", name: "unknown", scope: { artifacts: { count: null, bytes: null }, sessions: { count: null, bytes: null }, checkouts: null, gitRefs: null },
      expected: ["Count unknown; Size unknown", "Count unknown; Size unknown", "Checkout count unknown; Git ref count unknown"] },
    { language: "zh", name: "independently known", scope: { artifacts: { count: 0, bytes: null }, sessions: { count: null, bytes: 0 }, checkouts: 3, gitRefs: null },
      expected: ["0 份，体积未知", "数量未知，共 0 B", "3 个检出，引用数量未知"] },
  ])("shows $name scope values in $language", async ({ language, scope, expected }) => {
    await i18n.changeLanguage(language);
    dialogHarness({ api: { command: vi.fn(async () => preview({ scope })) } as unknown as ConsoleApi });
    const confirm = await screen.findByRole("button", { name: language === "zh" ? "删除" : "Delete" });
    const values = [...document.querySelectorAll("[data-slot=macro-delete-scope] dd")].slice(1).map(node => node.textContent);
    expect(values).toEqual(expected);
    expect(confirm.getAttribute("aria-disabled")).toBe("false");
  });

  it.each(["zh", "en"])("uses blocker title facts for unloaded tasks in %s", async language => {
    await i18n.changeLanguage(language);
    const authored = "未加载的作者标题 / original author title";
    const task = "任务首行内容 / original task line";
    const blockers: DeletionBlocker[] = [{ ...blocker("review", "unloaded-author"), title: authored, titleSource: "title" },
      { ...blocker("running", "unloaded-task"), title: task, titleSource: "task" },
      { ...blocker("stop-unconfirmed", "unloaded-unnamed"), title: null, titleSource: "none" }];
    dialogHarness({ api: { command: vi.fn(async () => preview({ blockerTotal: 3, blockers })) } as unknown as ConsoleApi });
    await screen.findByRole("dialog", { name: language === "zh" ? /还不能删除/ : /Cannot delete/ });
    const titles = [...document.querySelectorAll("[data-slot=macro-delete-blockers] li p.font-medium")].map(node => node.textContent);
    expect(titles).toEqual([authored, task, language === "zh" ? "未命名" : "Unnamed"]);
  });

  it("lists every current blocker instruction, grouping several reasons for the same task", async () => {
    const blockers = [blocker("queued"), blocker("running"), blocker("routing", "r2"), blocker("review", "r3"),
      blocker("awaiting-host", "r4"), blocker("stop-unconfirmed", "r5")];
    const f = dialogHarness({ api: { command: vi.fn(async () => preview({ blockerTotal: 6, blockers })) } as unknown as ConsoleApi });
    const dialog = await screen.findByRole("dialog", { name: /还不能删除/ });
    expect(dialog.textContent).toContain("名下还有 5 个微任务或路由计算没有了结");
    expect(dialog.querySelectorAll("[data-slot=macro-delete-blockers] > li")).toHaveLength(5);
    for (const text of ["排队中", "路由中", "进行中", "等待验收", "等待 Host", "停止未确认", "由 Host 验收、打回或给出结论"]) expect(dialog.textContent).toContain(text);
    expect(within(dialog).queryByRole("button", { name: "删除" })).toBeNull();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "知道了" }));
    await f.user.keyboard("{Escape}");
    expect(f.onClose).toHaveBeenCalled();
  });

  it("explains the server's fifty-reason limit without claiming a complete task count", async () => {
    dialogHarness({ api: { command: vi.fn(async () => preview({ blockerTotal: 51,
      blockers: Array.from({ length: 50 }, (_, index) => blocker("review", `r${index}`)) })) } as unknown as ConsoleApi });
    expect((await screen.findByRole("dialog", { name: /还不能删除/ })).textContent).toContain("前 50 条拦截原因，共 51 条");
    expect(screen.queryByText("名下还有 50 个微任务或路由计算没有了结。")).toBeNull();
  });

  it("prevents duplicate submission and dismissal while deleting", async () => {
    const f = dialogHarness();
    await screen.findByRole("button", { name: "删除" });
    const pending = deferred<unknown>();
    f.command.mockImplementationOnce(async () => pending.promise);
    await f.user.dblClick(screen.getByRole("button", { name: "删除" }));
    expect(f.command).toHaveBeenCalledTimes(2);
    expect(f.command).toHaveBeenLastCalledWith("macro_task_delete", { macroTaskId: "macro-1", microTaskCount: 73, confirm: true }, "synthetic-csrf");
    await f.user.click(screen.getByRole("button", { name: "取消" }));
    await f.user.keyboard("{Escape}");
    fireEvent.pointerDown(document.querySelector("[data-slot=dialog-overlay]")!);
    expect(f.onClose).not.toHaveBeenCalled();
    await act(async () => pending.resolve(receipt()));
    expect(f.onDeleted).toHaveBeenCalledWith("macro-1", 0);
  });

  it("retries the identical confirmed scope after a physical failure", async () => {
    const f = dialogHarness();
    await screen.findByRole("button", { name: "删除" });
    f.command.mockResolvedValueOnce(receipt({ state: "deleting", deletedAt: null, objectKind: "native-session",
      reason: { code: "macro-task-deletion.physical-failed", params: { errorCode: "IO_ERROR" } } }));
    await confirm();
    expect(await screen.findByText(/原生会话未能删除/)).toBeTruthy();
    expect(f.onDeleted).not.toHaveBeenCalled();
    f.command.mockResolvedValueOnce(receipt({ duplicate: true }));
    await f.user.click(screen.getByRole("button", { name: "重试删除" }));
    expect(f.command.mock.calls[2]).toEqual(f.command.mock.calls[1]);
    expect(f.onDeleted).toHaveBeenCalledWith("macro-1", 0);
  });

  it.each([new ApiError("NETWORK", "raw transport text"), new ApiError("INVALID_RESPONSE", "raw reply text")])(
    "keeps a lost or malformed reply unresolved and replays its identity", async error => {
      const f = dialogHarness();
      await screen.findByRole("button", { name: "删除" });
      f.command.mockRejectedValueOnce(error);
      await confirm();
      expect(await screen.findByText(/删除结果尚未确认/)).toBeTruthy();
      expect(screen.queryByText(error.message)).toBeNull();
      expect(f.onDeleted).not.toHaveBeenCalled();
      f.command.mockResolvedValueOnce(receipt({ duplicate: true }));
      await f.user.click(screen.getByRole("button", { name: "重试删除" }));
      expect(f.command.mock.calls[2]).toEqual(f.command.mock.calls[1]);
      expect(f.onDeleted).toHaveBeenCalledOnce();
    });

  it("does not accept a receipt naming another macro task", async () => {
    const f = dialogHarness();
    await screen.findByRole("button", { name: "删除" });
    f.command.mockResolvedValueOnce(receipt({ macroTaskId: "macro-other" }));
    await confirm();
    expect(await screen.findByText(/删除结果尚未确认/)).toBeTruthy();
    expect(f.onDeleted).not.toHaveBeenCalled();
  });

  it("requires a fresh preview and explicit confirmation after the count changes", async () => {
    const f = dialogHarness();
    await screen.findByRole("button", { name: "删除" });
    f.command.mockRejectedValueOnce(new ApiError("REVISION_CONFLICT", "raw count message"));
    await confirm();
    expect(await screen.findByText(/微任务数已变化/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    f.command.mockResolvedValueOnce(preview({ microTaskCount: 74 }));
    await f.user.click(screen.getByRole("button", { name: "重新读取" }));
    await screen.findByText("74 个，其中 2 个取消或失败而没有结论");
    expect(f.onDeleted).not.toHaveBeenCalled();
    f.command.mockResolvedValueOnce(receipt({ microTaskCount: 74 }));
    await confirm();
    expect(f.command).toHaveBeenLastCalledWith("macro_task_delete", { macroTaskId: "macro-1", microTaskCount: 74, confirm: true }, "synthetic-csrf");
  });

  it("shows new blockers after an admission race without retrying deletion automatically", async () => {
    const f = dialogHarness();
    await screen.findByRole("button", { name: "删除" });
    f.command.mockRejectedValueOnce(new ApiError("MACRO_TASK_NOT_SETTLED", "untranslated backend message"));
    await confirm();
    await screen.findByText(/还有未了结的微任务/);
    f.command.mockResolvedValueOnce(preview({ blockerTotal: 1, blockers: [blocker("review")] }));
    await f.user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("dialog", { name: /还不能删除/ })).toBeTruthy();
    expect(f.command.mock.calls.filter(call => call[0] === "macro_task_delete")).toHaveLength(1);
  });

  it("fails closed on a malformed preview and allows reading it again", async () => {
    const f = dialogHarness({ api: { command: vi.fn().mockResolvedValueOnce({ ...preview(), macroTaskId: "macro-wrong" }).mockResolvedValueOnce(preview()) } as unknown as ConsoleApi });
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "删除" })).toBeNull();
    await f.user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("button", { name: "删除" })).toBeTruthy();
  });

  it("rechecks live write authority before dispatch and latches an expired session", async () => {
    const authority = createAuthorityLatch();
    const f = dialogHarness({ authority });
    await screen.findByRole("button", { name: "删除" });
    f.update({ writesAvailable: false });
    await confirm();
    expect(f.command).toHaveBeenCalledTimes(1);
    f.update({ writesAvailable: true });
    f.command.mockRejectedValueOnce(new ApiError("CONSOLE_SESSION_EXPIRED", "raw session text"));
    await confirm();
    await screen.findByText(/浏览器授权已失效/);
    expect(authority.writable(f.props.snapshot)).toBe(false);
    await f.user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(f.command).toHaveBeenCalledTimes(2);
  });

  it("preserves an earlier unknown result when an expired session refuses a replay", async () => {
    const f = dialogHarness({ authority: createAuthorityLatch() });
    await screen.findByRole("button", { name: "删除" });
    f.command.mockRejectedValueOnce(new ApiError("NETWORK", "lost"));
    await confirm();
    await screen.findByText(/删除结果尚未确认/);
    f.command.mockRejectedValueOnce(new ApiError("CONSOLE_SESSION_EXPIRED", "expired"));
    await f.user.click(screen.getByRole("button", { name: "重试删除" }));
    expect(await screen.findByText(/删除结果尚未确认/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试删除" }).getAttribute("aria-disabled")).toBe("true");
    expect(f.onDeleted).not.toHaveBeenCalled();
  });

  it("resumes a deletion already started in another browser and recognizes an already deleted target", async () => {
    const command = vi.fn().mockResolvedValueOnce(preview({ deletionState: "deleting" })).mockResolvedValueOnce(receipt({ duplicate: true }));
    const f = dialogHarness({ api: { command } as unknown as ConsoleApi });
    await f.user.click(await screen.findByRole("button", { name: "重试删除" }));
    expect(command).toHaveBeenLastCalledWith("macro_task_delete", { macroTaskId: "macro-1", microTaskCount: 73, confirm: true }, "synthetic-csrf");
    expect(f.onDeleted).toHaveBeenCalledOnce();
    f.unmount();
    const other = dialogHarness({ api: { command: vi.fn(async () => preview({ deletionState: "deleted", unconcludedFailureCount: 0 })) } as unknown as ConsoleApi });
    await waitFor(() => expect(other.onDeleted).toHaveBeenCalledWith("macro-1", 0));
  });

  it("ignores a preview completing after its confirmation has been cancelled", async () => {
    const pending = deferred<unknown>();
    const f = dialogHarness({ api: { command: vi.fn(async () => pending.promise) } as unknown as ConsoleApi });
    await screen.findByText("正在读取删除范围…");
    f.unmount();
    await act(async () => pending.resolve(preview({ deletionState: "deleted", unconcludedFailureCount: 0 })));
    expect(f.onDeleted).not.toHaveBeenCalled();
  });

  it("updates its fixed words when the language changes while retaining original task content", async () => {
    const f = dialogHarness();
    await screen.findByRole("button", { name: "删除" });
    await act(async () => { await i18n.changeLanguage("en"); });
    expect(screen.getByRole("dialog", { name: `Delete “${f.props.summary.title}”` })).toBeTruthy();
    expect(screen.getByText("73 total; 2 cancelled or failed without a conclusion")).toBeTruthy();
    expect([...document.querySelectorAll("[data-slot=macro-delete-scope] dd")].slice(1).map(node => node.textContent))
      .toEqual(["14; total 6.2 MB", "11; total 48.0 MB", "Checkouts: 3; Git refs: 12"]);
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.getByText("Copies in rolling backups remain until backup rotation.")).toBeTruthy();
  });
});

function workspaceHarness(extraRows: MacroTaskSummary[] = []) {
  const snapshot = snapshotFixture();
  const timeline = macroTaskTimelineFixture();
  let rows = [timeline.macroTask, ...extraRows];
  const command = vi.fn(async (operation: string, params: unknown): Promise<unknown> => {
    if (operation === "macro_task_deletion_preview") return preview();
    if (operation === "macro_task_delete") { rows = rows.filter(row => row.macroTaskId !== "macro-1"); return receipt(); }
    throw new Error(`Unexpected fixture operation: ${operation} ${JSON.stringify(params)}`);
  });
  const api = { command, macroTasks: vi.fn(async () => ({ macroTasks: rows, totals: { all: rows.length, active: rows.length, host: 0, review: 0 }, nextCursor: null, cursor: 41, changed: false })),
    macroTaskTimeline: vi.fn(async (id: string) => ({ timeline: id === "macro-1" ? timeline : { ...timeline, macroTask: rows.find(row => row.macroTaskId === id)!, rows: [], spans: [], events: [] }, verifiedAtMs: null })) } as unknown as ConsoleApi;
  const refresh = vi.fn(async (): Promise<Snapshot | null> => structuredClone(snapshot));
  const onAddress = vi.fn();
  const view = render(wrap(<MacroTasks api={api} snapshot={snapshot} refresh={refresh} onAddress={onAddress}/>));
  return { ...view, api, command, timeline, refresh, onAddress, user: userEvent.setup(),
    setRows: (next: MacroTaskSummary[]) => { rows = next; } };
}
async function openDelete() {
  await userEvent.click(await screen.findByRole("button", { name: "更多" }));
  await userEvent.click(await screen.findByRole("button", { name: "删除宏任务…" }));
  return screen.findByRole("dialog");
}

describe("macro task deletion workspace integration", () => {
  it("offers deletion in the existing menu, cancels safely and restores focus to More", async () => {
    const f = workspaceHarness();
    const dialog = await openDelete();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "取消" }));
    await f.user.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "更多" })));
    expect(f.command.mock.calls.every(call => call[0] === "macro_task_deletion_preview")).toBe(true);
  });

  it("removes only the deleted row, replaces its address and refreshes the list", async () => {
    const other = macroTaskSummary({ macroTaskId: "macro-2", title: "保留的宏任务", lastActivitySeq: 40 });
    const f = workspaceHarness([other]);
    await openDelete();
    await confirm();
    await waitFor(() => expect(window.location.hash).toBe("#tasks/macro-2"));
    expect(screen.queryByRole("button", { name: new RegExp(`^${f.timeline.macroTask.title}`) })).toBeNull();
    expect(await screen.findByRole("button", { name: /^保留的宏任务/ })).toBeTruthy();
    expect(f.onAddress).toHaveBeenLastCalledWith("#tasks/macro-2");
    expect(f.refresh).toHaveBeenCalledOnce();
    window.history.pushState(null, "", "#tasks/macro-1");
    fireEvent(window, new HashChangeEvent("hashchange"));
    await waitFor(() => expect(window.location.hash).toBe("#tasks/macro-2"));
  });

  it("returns to the empty list after deleting the last item and reports retained storage", async () => {
    const f = workspaceHarness();
    await openDelete();
    f.command.mockImplementationOnce(async () => {
      f.setRows([]);
      return receipt({ skipped: [{ objectKind: "private-root", path: "/synthetic/retained", reason: { code: "macro-task-deletion.owner-unproven", params: {} } }] });
    });
    await confirm();
    await waitFor(() => expect(window.location.hash).toBe("#tasks"));
    await screen.findByText(/1 项存储内容按检查结果留在原处/);
    expect(screen.getByRole("link", { name: "查看存储" }).getAttribute("href")).toBe("#settings");
    expect(await screen.findByRole("heading", { name: "还没有宏任务" })).toBeTruthy();
    expect(document.activeElement).toBe(document.querySelector("[data-slot=macro-delete-result]"));
  });

  it("keeps confirmed deletion true when subsequent reads fail", async () => {
    const f = workspaceHarness();
    await openDelete();
    f.refresh.mockRejectedValueOnce(new Error("synthetic refresh failure"));
    vi.mocked(f.api.macroTasks).mockRejectedValueOnce(new ApiError("NETWORK", "synthetic list failure"));
    await confirm();
    await screen.findByText("列表或看板刷新失败，请重新读取；删除已完成。");
    expect(screen.queryByRole("button", { name: new RegExp(`^${f.timeline.macroTask.title}`) })).toBeNull();
    expect(window.location.hash).toBe("#tasks");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps an unnamed deletion result localized after a language change", async () => {
    const f = workspaceHarness();
    Object.assign(f.timeline.macroTask, { title: "", titleSource: "none" });
    await openDelete();
    await confirm();
    await screen.findByText("已删除「未命名」。");
    await act(async () => { await i18n.changeLanguage("en"); });
    expect(screen.getByText("Deleted “Unnamed”.")).toBeTruthy();
  });

  it("does not replace another selected task when an earlier deletion completes", async () => {
    const f = workspaceHarness([macroTaskSummary({ macroTaskId: "macro-2", title: "保留的宏任务", lastActivitySeq: 40 })]);
    await openDelete();
    const pending = deferred<unknown>();
    f.command.mockImplementationOnce(async () => pending.promise);
    await confirm();
    window.history.pushState(null, "", "#tasks/macro-2");
    fireEvent(window, new HashChangeEvent("hashchange"));
    await waitFor(() => expect(window.location.hash).toBe("#tasks/macro-2"));
    f.setRows([macroTaskSummary({ macroTaskId: "macro-2", title: "保留的宏任务", lastActivitySeq: 40 })]);
    await act(async () => pending.resolve(receipt()));
    expect(window.location.hash).toBe("#tasks/macro-2");
  });

  it("keeps a micro task cancelled after console stop and allows deleting its whole macro task without a Host conclusion", async () => {
    const f = workspaceHarness();
    const row = { ...f.timeline.rows.find(row => row.runId === "r4")!, rootLeadRunId: null };
    const summary = macroTaskSummary({ counts: { roots: 1, helpers: 0, accepted: 0, failed: 0, active: 1, host: 0, review: 0, ended: 0,
      stoppableRoots: 1, stoppableHelpers: 0, delivered: 0, awaitingHost: 0 }, rootRunIds: [row.runId] });
    Object.assign(f.timeline, { macroTask: summary, rows: [row], spans: [], events: [], totals: { rows: 1, allRows: 1, events: 0, spans: 0 } } satisfies Partial<MacroTaskTimeline>);
    f.setRows([summary]);
    f.command.mockImplementation(async (operation: string): Promise<unknown> => {
      if (operation === "macro_task_stop") {
        Object.assign(row, { state: "cancelled", status: "cancelled", shutdownConfirmed: true });
        Object.assign(summary, { state: "ended", counts: { ...summary.counts, active: 0, ended: 1, stoppableRoots: 0 } });
        return { macroTaskId: "macro-1", duplicate: false, results: [{ runId: row.runId, state: "cancelled", stopped: true }] };
      }
      if (operation === "macro_task_deletion_preview") return preview({ microTaskCount: 1, unconcludedFailureCount: 1 });
      if (operation === "macro_task_delete") { f.setRows([]); return receipt({ microTaskCount: 1 }); }
      throw new Error(`Unexpected fixture operation: ${operation}`);
    });
    await f.user.click(await screen.findByRole("button", { name: "更多" }));
    await f.user.click(screen.getByRole("button", { name: "停止宏任务" }));
    const stop = await screen.findByRole("dialog", { name: "停止宏任务" });
    await f.user.click(within(stop).getByRole("button", { name: "确认停止宏任务" }));
    await waitFor(() => expect(document.querySelector("[data-slot=timeline-row-name]")?.getAttribute("aria-label")).toContain("已取消"));
    const dialog = await openDelete();
    await within(dialog).findByText("1 个，其中 1 个取消或失败而没有结论");
    await confirm();
    await waitFor(() => expect(window.location.hash).toBe("#tasks"));
    expect(f.command).toHaveBeenLastCalledWith("macro_task_delete", { macroTaskId: "macro-1", microTaskCount: 1, confirm: true }, "synthetic-csrf");
  });
});
