import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ConsoleApi, type StorageApplyResult, type StoragePlan } from "./api";
import type { BackupPreflight } from "./types";
import { StoragePanel } from "./StoragePanel";

afterEach(() => cleanup());

/** A contract-typed backup preflight fixture; the same wire shape the snapshot used to carry. */
function preflight(overrides: Partial<BackupPreflight> = {}): BackupPreflight {
  const inventory = (count: number, entries: { path: string; reason: string }[]) =>
    ({ count, paths: entries.map(entry => entry.path), entries });
  return {
    policy: "attempt-evidence",
    ok: true,
    needsAttention: false,
    copied: inventory(4, []),
    skipped: inventory(0, []),
    rejected: inventory(0, []),
    ...overrides,
  };
}

/** A contract-typed plan fixture mirroring operations.md's wire shape. */
function plan(overrides: Partial<StoragePlan> = {}): StoragePlan {
  return {
    planId: "plan-1",
    createdAt: "2026-09-27T12:00:00Z",
    expiresAt: new Date(Date.now() + 14 * 60_000).toISOString(),
    categories: [
      { id: "zcode", label: "", bytes: 3_500_000_000, reclaimableBytes: 2_900_000_000, count: 71, eligibleCount: 60, reasons: ["continuation-supported", "grace-period"] },
      { id: "workspaces", label: "", bytes: 3_400_000_000, reclaimableBytes: 1_200_000_000, count: 9, eligibleCount: 4, reasons: [] },
      { id: "runtimes", label: "", bytes: 2_100_000_000, reclaimableBytes: 2_100_000_000, count: 30, eligibleCount: 30, reasons: [] },
      { id: "backup", label: "", bytes: 200_000_000, reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: ["durable-record"] },
      { id: "durable", label: "", bytes: 213_000_000, reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: ["durable-record"] },
    ],
    candidates: [
      { id: "c1", category: "zcode", path: "/private/zcode/one", bytes: 1_000_000_000, eligible: true, reasons: [] },
    ],
    orphanProcesses: [
      { pid: 4321, kind: "daemon", stateDir: "/tmp/private-state-a", runtimeDir: "/runtime/root/76c43f9" },
      { pid: 8764, kind: "supervisor", stateDir: null, runtimeDir: "/runtime/root/ea4554c" },
    ],
    ...overrides,
  };
}

function applyResult(overrides: Partial<StorageApplyResult> = {}): StorageApplyResult {
  return { planId: "plan-1", removedBytes: 2_700_000_000, complete: true,
    removed: [
      { id: "c1", path: "/private/zcode/one", bytes: 1_500_000_000 },
      { id: "c2", path: "/private/zcode/two", bytes: 1_200_000_000 },
    ],
    skipped: [{ id: "c3", path: "/private/runtimes/old", reasons: ["candidate-changed"] }], ...overrides };
}

type Script = {
  plan?: "ok" | "refused" | "lost";
  apply?: "ok" | "refused" | "lost" | "incomplete";
  planValue?: StoragePlan;
  preflight?: "ok" | "attention" | "blocked" | "refused";
};

function fixture(script: Script = {}) {
  const calls: { operation: string; params: unknown }[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    calls.push({ operation, params: structuredClone(params) });
    if (operation === "storage_plan") {
      if (script.plan === "refused") throw new ApiError("FORBIDDEN", "refused");
      if (script.plan === "lost") throw new ApiError("NETWORK", "lost");
      return structuredClone(script.planValue ?? plan());
    }
    if (operation === "storage_apply") {
      if (script.apply === "refused") throw new ApiError("CONFLICT", "changed");
      if (script.apply === "lost") throw new ApiError("NETWORK", "lost");
      if (script.apply === "incomplete") throw new ApiError("STORAGE_INCOMPLETE", "Removal is incomplete");
      return applyResult();
    }
    throw new Error(`unexpected ${operation}`);
  });
  const backupPreflight = vi.fn(async (_signal?: AbortSignal, { refresh = false }: { refresh?: boolean } = {}) => {
    if (script.preflight === "refused") throw new ApiError("FORBIDDEN", "refused");
    if (script.preflight === "blocked") {
      // The route's own domain-negative assessment: HTTP 200, ok:false with
      // per-class reasons — data the panel shows, never a read failure.
      return preflight({ ok: false, needsAttention: true,
        skipped: { count: 2, paths: ["/private/state/a", "/private/state/b"],
          entries: [{ path: "/private/state/a", reason: "not-evidence-directory" },
            { path: "/private/state/b", reason: "not-evidence-file" }] },
        rejected: { count: 1, paths: ["/private/state/odd"], entries: [{ path: "/private/state/odd", reason: "linked-path" }] } });
    }
    return preflight({ needsAttention: script.preflight === "attention", rejected: script.preflight === "attention"
      ? { count: 1, paths: ["/private/state/odd"], entries: [{ path: "/private/state/odd", reason: "linked-path" }] } : undefined });
  });
  const api = {
    command,
    storagePlan: async () => command("storage_plan", {}),
    storageApply: async (planId: string, commandId: string) => command("storage_apply", { planId, commandId, confirm: true }),
    backupPreflight,
  } as unknown as ConsoleApi;
  return { api, calls, command, backupPreflight };
}

async function checkUsage(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "检查占用" }));
  await screen.findByRole("button", { name: "重新检查" });
}

describe("storage panel (0.16 wire-shape contract)", () => {
  it("does not auto-check on mount and checks only on the explicit button", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "检查占用" })).toBeTruthy();
    await checkUsage(user);
    expect(f.calls[0]).toEqual({ operation: "storage_plan", params: {} });
  });

  it("renders the fixed-order category table with formatted bytes and exact tooltips", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    const table = screen.getByRole("table");
    const rows = [...table.querySelectorAll<HTMLElement>("tbody tr")];
    expect(rows.map(row => row.querySelector("th")!.textContent)).toEqual([
      "ZCode 私有主目录", "受管检出", "运行时", "备份", "看板与记录", "合计",
    ]);
    expect(within(rows[0]!).getByText("3.3 GB")).toBeTruthy();
    expect(within(rows[0]!).getByText("2.7 GB")).toBeTruthy();
    expect(rows[0]!.querySelector('[title="3,500,000,000 字节"]')).toBeTruthy();
    // Structurally protected categories show — with their own tooltip.
    expect(within(rows[3]!).getByText("—")).toBeTruthy();
    expect(within(rows[4]!).getByText("—")).toBeTruthy();
    expect(rows[4]!.querySelector('[title="看板、记录及当前备份受保护"]')).toBeTruthy();
  });

  it("keeps unknown category and reason codes visible instead of dropping them", async () => {
    const custom = plan({ categories: [...plan().categories,
      { id: "future-thing", label: "", bytes: 5, reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: ["never-seen-code"] }] });
    const f = fixture({ planValue: custom });
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    expect(screen.getByText("future-thing")).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: /为何保留 ▸/ })[0]!);
    expect(screen.getByText("仍可能续接")).toBeTruthy();
    expect(screen.getByText("宽限期未满")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /为何保留 ▾/ }));
    await user.click([...screen.getAllByRole("button", { name: /为何保留 ▸/ })].at(-1)!);
    expect(screen.getByText("never-seen-code")).toBeTruthy();
  });

  it("lists orphan processes without any stop control", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    const orphans = screen.getByRole("heading", { name: /游离进程（2）/ }).closest<HTMLElement>(".storage-orphans")!;
    expect(orphans.textContent).toContain("清理不会停止这些进程。");
    expect(orphans.textContent).toContain("服务");
    expect(orphans.textContent).toContain("执行进程");
    expect(orphans.textContent).toContain("pid 4321");
    expect(within(orphans).queryByRole("button", { name: /停止/ })).toBeNull();
  });

  it("requires the modal confirmation; cancel is the initial focus and Esc cancels", async () => {
    const f = fixture();
    const user = userEvent.setup();
    const view = render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    const clean = screen.getByRole("button", { name: /清理可回收数据（5\.8 GB）/ }) as HTMLButtonElement;
    expect(clean.className).toContain("danger");
    await user.click(clean);
    const dialog = screen.getByRole("dialog", { name: "清理可回收数据" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // The panel behind the modal is inert; the dialog itself stays usable.
    const panelResult = view.container.querySelector<HTMLElement>(".storage-result")!;
    expect(panelResult.hasAttribute("inert")).toBe(true);
    expect(dialog.hasAttribute("inert")).toBe(false);
    expect(dialog.textContent).toContain("ZCode 私有主目录：2.7 GB");
    expect(dialog.textContent).toContain("看板、记录和当前备份不会删除。");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "取消" }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(panelResult.hasAttribute("inert")).toBe(false));
    expect(f.command.mock.calls.filter(([operation]) => operation === "storage_apply")).toHaveLength(0);
  });

  it("confirms the exact plan with one command identity and reports skips", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: /清理可回收数据/ }));
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    expect(await screen.findByText(/已回收 2\.5 GB（2 项）/)).toBeTruthy();
    expect(screen.getByText("跳过 1 项 · 数据已变化")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "查看逐项原因" }));
    expect(screen.getByText(/\/private\/runtimes\/old：数据已变化/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "查看已删项目" }));
    expect(screen.getByText(/\/private\/zcode\/one：1\.4 GB/)).toBeTruthy();
    const applies = f.calls.filter(call => call.operation === "storage_apply");
    expect(applies).toHaveLength(1);
    expect(applies[0]!.params).toEqual({ planId: "plan-1", commandId: expect.any(String), confirm: true });
    // The applied plan is marked expired and the panel never re-checks itself.
    expect(screen.getByText("已过期 · 请重新检查")).toBeTruthy();
  });

  it("shows an unknown apply result with 重试同一请求 reusing the command identity", async () => {
    const first = fixture({ apply: "lost" });
    const user = userEvent.setup();
    render(<StoragePanel api={first.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: /清理可回收数据/ }));
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    expect(await screen.findByText("清理结果未知，可能已执行")).toBeTruthy();
    const firstCommandId = (first.calls.find(call => call.operation === "storage_apply")!.params as { commandId: string }).commandId;
    // A retry replays the same command and the same plan.
    first.command.mockImplementation(async (operation: string) => {
      if (operation === "storage_apply") return applyResult();
      return plan();
    });
    await user.click(screen.getByRole("button", { name: "重试同一请求" }));
    await screen.findByText(/已回收/);
    // vi.fn's own call record survives the mockImplementation swap.
    const applies = first.command.mock.calls.filter(([operation]) => operation === "storage_apply");
    expect(applies).toHaveLength(2);
    expect(applies[1]![1]).toMatchObject({ commandId: firstCommandId, planId: "plan-1" });
  });

  it("keeps the shown plan after a refused check and offers retry", async () => {
    const shown = plan();
    let refused = false;
    const command = vi.fn(async (operation: string, _params?: unknown) => {
      if (operation === "storage_plan") {
        if (refused) throw new ApiError("CONFLICT", "refused");
        return structuredClone(shown);
      }
      return applyResult();
    });
    const api = {
      command,
      storagePlan: async () => command("storage_plan", {}),
      storageApply: async (planId: string, commandId: string) => command("storage_apply", { planId, commandId, confirm: true }),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<StoragePanel api={api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    refused = true;
    await user.click(screen.getByRole("button", { name: "重新检查" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("记录冲突");
    expect(screen.getByText("ZCode 私有主目录")).toBeTruthy();
  });

  it("disables actions with a reason while the connection is interrupted", () => {
    const f = fixture();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="与本地黑板的连接已中断" />);
    const check = screen.getByRole("button", { name: "检查占用" }) as HTMLButtonElement;
    expect(check.disabled).toBe(true);
    expect(check.getAttribute("title")).toContain("连接已中断");
    expect(f.command).not.toHaveBeenCalled();
  });

  it("resumes an interrupted STORAGE_INCOMPLETE apply with the same identity and blocks a fresh plan", async () => {
    const f = fixture({ apply: "incomplete" });
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: /清理可回收数据/ }));
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    // Neither a failure nor a claim that nothing ran.
    expect(await screen.findByText("清理未完成 · 已开始删除")).toBeTruthy();
    const retry = screen.getByRole("button", { name: "重试同一请求" }) as HTMLButtonElement;
    expect(retry.disabled).toBe(false);
    // A fresh plan would discard the unresolved operation, so re-checking is refused.
    const recheck = screen.getByRole("button", { name: "重新检查" }) as HTMLButtonElement;
    expect(recheck.disabled).toBe(true);
    expect(recheck.getAttribute("title")).toContain("请先重试同一请求");
    expect(f.calls.filter(call => call.operation === "storage_plan")).toHaveLength(1);
    // The retry replays the same planId/commandId and then completes.
    f.command.mockImplementation(async (operation: string) => operation === "storage_plan" ? plan() : applyResult());
    await user.click(retry);
    expect(await screen.findByText(/已回收/)).toBeTruthy();
    const applies = f.command.mock.calls.filter(([operation]) => operation === "storage_apply");
    expect(applies).toHaveLength(2);
    expect(applies[1]![1]).toMatchObject({ planId: "plan-1", commandId: (applies[0]![1] as { commandId: string }).commandId, confirm: true });
  });

  it("keeps the unresolved identity retryable after plan expiry instead of disabling it", async () => {
    // First dispatch loses its reply; by the time the user retries, the plan's
    // 15-minute window has passed. The durable receipt still replays.
    const soon = plan({ expiresAt: new Date(Date.now() + 200).toISOString() });
    const f = fixture({ apply: "lost", planValue: soon });
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: /清理可回收数据/ }));
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    expect(await screen.findByText("清理结果未知，可能已执行")).toBeTruthy();
    const firstCommandId = (f.calls.find(call => call.operation === "storage_apply")!.params as { commandId: string }).commandId;
    await new Promise(resolve => setTimeout(resolve, 400));
    // The plan's window has passed, but the recovery retry stays enabled —
    // the durable receipt replays regardless of plan expiry.
    const retry = screen.getByRole("button", { name: "重试同一请求" }) as HTMLButtonElement;
    expect(retry.disabled).toBe(false);
    expect(retry.getAttribute("title")).toContain("重试同一请求可继续或核对清理");
    const recheck = screen.getByRole("button", { name: "重新检查" }) as HTMLButtonElement;
    expect(recheck.disabled).toBe(true);
    f.command.mockImplementation(async (operation: string) => operation === "storage_plan" ? plan() : applyResult());
    await user.click(retry);
    expect(await screen.findByText(/已回收/)).toBeTruthy();
    const applies = f.command.mock.calls.filter(([operation]) => operation === "storage_apply");
    expect(applies[1]![1]).toMatchObject({ planId: "plan-1", commandId: firstCommandId, confirm: true });
  });

  it("expires the confirm path once the plan's window passes", async () => {
    const expiredPlan = plan({ expiresAt: new Date(Date.now() - 60_000).toISOString() });
    const f = fixture({ planValue: expiredPlan });
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    expect(screen.getByText("计划已过期 · 请重新检查")).toBeTruthy();
    const clean = screen.getByRole("button", { name: /清理可回收数据/ }) as HTMLButtonElement;
    expect(clean.disabled).toBe(true);
    expect(clean.getAttribute("title")).toContain("计划已过期");
    // A second, live plan in another window stays usable (multi-window).
    const live = plan();
    const g = fixture({ planValue: live });
    render(<StoragePanel api={g.api} csrfToken="csrf" connectionError="" />);
    const panel2 = screen.getAllByRole("region", { name: "存储" })[1]!;
    await user.click(within(panel2).getByRole("button", { name: "检查占用" }));
    const clean2 = await within(panel2).findByRole("button", { name: /清理可回收数据/ }) as HTMLButtonElement;
    expect(clean2.disabled).toBe(false);
  });
});

describe("storage panel on-demand backup preflight", () => {
  it("reads the preflight once when the panel opens, never while closed, and once per reopen", async () => {
    const f = fixture();
    const view = render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" active={false} />);
    expect(f.backupPreflight).not.toHaveBeenCalled();
    view.rerender(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" active={true} />);
    await waitFor(() => expect(f.backupPreflight).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("备份预检正常")).toBeTruthy());
    // Staying open does not poll; closing stops reading.
    view.rerender(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" active={false} />);
    view.rerender(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" active={true} />);
    await waitFor(() => expect(f.backupPreflight).toHaveBeenCalledTimes(2));
  });

  it("shows the attention banner from the read report and refuses to claim a clean state when the read failed", async () => {
    const f = fixture({ preflight: "attention" });
    const attention = render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await screen.findByRole("status", { name: "备份预检提醒" });
    expect(screen.getByText(/备份预检需要处理/)).toBeTruthy();
    expect(f.backupPreflight).toHaveBeenCalledTimes(1);
    attention.unmount();

    const refused = fixture({ preflight: "refused" });
    render(<StoragePanel api={refused.api} csrfToken="csrf" connectionError="" />);
    await waitFor(() => expect(screen.getByText(/备份预检读取失败/)).toBeTruthy());
    expect(screen.queryByRole("status", { name: "备份预检提醒" })).toBeNull();
  });

  it("shows a legal domain-negative report as reasons, never as a read failure", async () => {
    const f = fixture({ preflight: "blocked" });
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    // The blocked assessment is domain data: the attention banner names the
    // skip/refuse counts and the paths, the status line never claims a read
    // failure.
    await screen.findByRole("status", { name: "备份预检提醒" });
    expect(screen.getByText(/将跳过 2 项，拒绝 1 项/)).toBeTruthy();
    expect(screen.getByText("/private/state/odd")).toBeTruthy();
    expect(screen.queryByText(/备份预检读取失败/)).toBeNull();
    expect(screen.queryByText("备份预检尚未读取")).toBeNull();
    // The 重新读取 action stays available for a fresh check.
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "重新读取备份预检" }));
    await waitFor(() => expect(f.backupPreflight).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("status", { name: "备份预检提醒" })).toBeTruthy();
  });

  it("re-reads only on the explicit 重新读取备份预检 action, passing the refresh option", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StoragePanel api={f.api} csrfToken="csrf" connectionError="" />);
    await waitFor(() => expect(f.backupPreflight).toHaveBeenCalledTimes(1));
    expect(f.backupPreflight.mock.calls[0][1]).toEqual({ refresh: false });
    await user.click(screen.getByRole("button", { name: "重新读取备份预检" }));
    await waitFor(() => expect(f.backupPreflight).toHaveBeenCalledTimes(2));
    expect(f.backupPreflight.mock.calls[1][1]).toEqual({ refresh: true });
  });
});

describe("storage panel preflight in-flight lifecycle", () => {
  it("does not publish a preflight that was still in flight when the panel closed, and reads fresh on reopen", async () => {
    let release: ((value: BackupPreflight) => void) | undefined;
    const gate = new Promise<BackupPreflight>(resolve => { release = resolve; });
    const replies: Promise<BackupPreflight>[] = [];
    const calls: { signal?: AbortSignal }[] = [];
    const backupPreflight = vi.fn((signal?: AbortSignal) => {
      calls.push({ signal });
      return replies.shift()!.then(report => {
        if (signal?.aborted) { const aborted = new Error("aborted"); aborted.name = "AbortError"; throw aborted; }
        return report;
      });
    });
    const api = { backupPreflight } as unknown as ConsoleApi;
    replies.push(gate);
    const view = render(<StoragePanel api={api} csrfToken="csrf" connectionError="" active={true} />);
    await waitFor(() => expect(calls.length).toBe(1));
    view.rerender(<StoragePanel api={api} csrfToken="csrf" connectionError="" active={false} />);
    release!(preflight({ needsAttention: true, rejected: { count: 1, paths: ["/p"], entries: [{ path: "/p", reason: "linked-path" }] } }));
    await act(async () => { await Promise.resolve(); });
    // The late reply belongs to a closed panel: no banner, no report, no error.
    expect(screen.queryByRole("status", { name: "备份预检提醒" })).toBeNull();
    expect(screen.getByText("备份预检尚未读取")).toBeTruthy();
    // Reopening starts a fresh read whose own reply is the one that shows.
    replies.push(Promise.resolve(preflight()));
    view.rerender(<StoragePanel api={api} csrfToken="csrf" connectionError="" active={true} />);
    await waitFor(() => expect(calls.length).toBe(2));
    await waitFor(() => expect(screen.getByText("备份预检正常")).toBeTruthy());
    view.unmount();
  });

  it("stays bounded under a development StrictMode double effect (no production claim)", async () => {
    const f = fixture();
    render(<StrictMode><StoragePanel api={f.api} csrfToken="csrf" connectionError="" active={true} /></StrictMode>);
    await waitFor(() => expect(f.backupPreflight.mock.calls.length).toBeGreaterThanOrEqual(1));
    await waitFor(() => expect(screen.getByText("备份预检正常")).toBeTruthy());
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });
    // Bounded: a double-mounted effect reads at most twice, then settles.
    expect(f.backupPreflight.mock.calls.length).toBeLessThanOrEqual(2);
    expect(screen.queryByRole("status", { name: "备份预检提醒" })).toBeNull();
  });
});
