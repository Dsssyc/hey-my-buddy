import { cleanup, fireEvent, render as rtlRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ConsoleApi, type StorageApplyResult, type StoragePlan } from "../api";
import type { ReactElement } from "react";
import { TooltipProvider } from "../components/ui/tooltip";
import { StorageSection } from "./StorageSection";

const render = (ui: ReactElement) => rtlRender(ui, { wrapper: TooltipProvider });

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
      { pid: 8764, kind: "execution", stateDir: null, runtimeDir: "/runtime/root/ea4554c" },
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
  const api = {
    command,
    storagePlan: async () => command("storage_plan", {}),
    storageApply: async (planId: string, commandId: string) => command("storage_apply", { planId, commandId, confirm: true }),
  } as unknown as ConsoleApi;
  return { api, calls, command };
}

async function checkUsage(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "检查占用" }));
  await screen.findByRole("table");
}

describe("storage panel (0.16 wire-shape contract)", () => {
  it("does not auto-check on mount and checks only on the explicit button", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "检查占用" })).toBeTruthy();
    await checkUsage(user);
    expect(f.calls[0]).toEqual({ operation: "storage_plan", params: {} });
  });

  it("renders the fixed-order category table with formatted bytes and exact tooltips", async () => {
    const f = fixture({ planValue: plan({ categories: [...plan().categories].reverse() }) });
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    const table = screen.getByRole("table");
    const rows = [...table.querySelectorAll<HTMLElement>("tbody tr")].filter(row => row.querySelector("th"));
    expect(rows.map(row => row.querySelector("th")!.textContent)).toEqual([
      "ZCode 旧私有主目录", "受管检出", "运行时", "备份", "看板与记录",
    ]);
    expect(within(rows[0]!).getByText("3.3 GB")).toBeTruthy();
    expect(within(rows[0]!).getByText("2.7 GB")).toBeTruthy();
    expect(rows[0]!.querySelector('[aria-description="3,500,000,000 字节"]')).toBeTruthy();
    expect(within(rows[0]!).getByText("71")).toBeTruthy();
    // Structurally protected categories show — with their own tooltip.
    expect(within(rows[3]!).getByText("0 B")).toBeTruthy();
    expect(within(rows[4]!).getByText("0 B")).toBeTruthy();
    expect(rows[4]!.querySelector('[aria-description="看板、记录及上次升级之前的看板受保护"]')).toBeTruthy();
  });

  it("keeps unknown category and reason codes visible instead of dropping them", async () => {
    const custom = plan({ categories: [...plan().categories,
      { id: "future-thing", label: "", bytes: 5, reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: ["never-seen-code"] }] });
    const f = fixture({ planValue: custom });
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    expect(screen.getByText("future-thing")).toBeTruthy();
    expect(within(screen.getByText("future-thing").closest("tr")!).getByText("1")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "ZCode 旧私有主目录：为何保留" }));
    expect(screen.getByText("仍可能续接")).toBeTruthy();
    expect(screen.getByText("宽限期未满")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "ZCode 旧私有主目录：为何保留" }));
    await user.click(screen.getByRole("button", { name: "future-thing：为何保留" }));
    expect(within(screen.getByRole("table")).getByText("5 B")).toBeTruthy();
    expect(screen.getByText("never-seen-code")).toBeTruthy();
  });

  it("lists orphan processes without any stop control", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    const orphans = screen.getByRole("heading", { name: /游离进程（2）/ }).closest<HTMLElement>('[data-slot="storage-orphans"]')!;
    expect(within(orphans).getByRole("heading").getAttribute("aria-description")).toBe("清理不会停止这些进程。");
    expect(orphans.textContent).toContain("服务");
    expect(orphans.textContent).toContain("执行进程");
    expect(orphans.textContent).toContain("pid 4321");
    expect(within(orphans).queryByRole("button", { name: /停止/ })).toBeNull();
  });

  it("requires the modal confirmation; cancel is the initial focus and Esc cancels", async () => {
    const f = fixture();
    const user = userEvent.setup();
    const view = render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    const clean = screen.getByRole("button", { name: "回收…" }) as HTMLButtonElement;
    expect(clean.getAttribute("aria-description")).toContain("5.8 GB");
    await user.click(clean);
    const dialog = screen.getByRole("dialog", { name: "清理可回收数据" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // The panel behind the modal is inert; the dialog itself stays usable.
    const panelResult = view.container.firstElementChild as HTMLElement;
    expect(panelResult.closest('[aria-hidden="true"]')).toBeTruthy();
    expect(dialog.hasAttribute("inert")).toBe(false);
    expect(dialog.textContent).toContain("ZCode 旧私有主目录：2.7 GB");
    expect(dialog.textContent).toContain("看板、记录和上次升级之前的看板不会删除。");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "取消" }));
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "确认清理" }));
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "取消" }));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "回收…" })));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(panelResult.closest('[aria-hidden="true"]')).toBeNull());
    expect(f.command.mock.calls.filter(([operation]) => operation === "storage_apply")).toHaveLength(0);
  });

  it("confirms the exact plan with one command identity and reports skips", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: "回收…" }));
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
    render(<StorageSection api={first.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: "回收…" }));
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
    render(<StorageSection api={api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    refused = true;
    await user.click(screen.getByRole("button", { name: "检查占用" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("记录冲突");
    expect(screen.getByText("ZCode 旧私有主目录")).toBeTruthy();
  });

  it("disables actions with a reason while the connection is interrupted", () => {
    const f = fixture();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="与本地黑板的连接已中断" />);
    const check = screen.getByRole("button", { name: "检查占用" }) as HTMLButtonElement;
    expect(check.disabled).toBe(false);
    expect(check.getAttribute("aria-disabled")).toBe("true");
    expect(check.getAttribute("aria-description")).toContain("连接已中断");
    expect(f.command).not.toHaveBeenCalled();
  });

  it("resumes an interrupted STORAGE_INCOMPLETE apply with the same identity and blocks a fresh plan", async () => {
    const f = fixture({ apply: "incomplete" });
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: "回收…" }));
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    // Neither a failure nor a claim that nothing ran.
    expect(await screen.findByText("清理未完成 · 已开始删除")).toBeTruthy();
    const retry = screen.getByRole("button", { name: "重试同一请求" }) as HTMLButtonElement;
    expect(retry.getAttribute("aria-disabled")).toBe("false");
    // A fresh plan would discard the unresolved operation, so re-checking is refused.
    const recheck = screen.getByRole("button", { name: "检查占用" }) as HTMLButtonElement;
    expect(recheck.getAttribute("aria-disabled")).toBe("true");
    expect(recheck.getAttribute("aria-description")).toContain("请先重试同一请求");
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
    const soon = plan({ expiresAt: new Date(Date.now() + 1000).toISOString() });
    const f = fixture({ apply: "lost", planValue: soon });
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: "回收…" }));
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    expect(await screen.findByText("清理结果未知，可能已执行")).toBeTruthy();
    const firstCommandId = (f.calls.find(call => call.operation === "storage_apply")!.params as { commandId: string }).commandId;
    await new Promise(resolve => setTimeout(resolve, 1100));
    // The plan's window has passed, but the recovery retry stays enabled —
    // the durable receipt replays regardless of plan expiry.
    const retry = screen.getByRole("button", { name: "重试同一请求" }) as HTMLButtonElement;
    expect(retry.getAttribute("aria-disabled")).toBe("false");
    expect(retry.getAttribute("aria-description")).toContain("重试同一请求可继续或核对清理");
    const recheck = screen.getByRole("button", { name: "检查占用" }) as HTMLButtonElement;
    expect(recheck.getAttribute("aria-disabled")).toBe("true");
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
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    expect(screen.getByText("计划已过期 · 请重新检查")).toBeTruthy();
    const clean = screen.getByRole("button", { name: "回收…" }) as HTMLButtonElement;
    expect(clean.disabled).toBe(false);
    expect(clean.getAttribute("aria-disabled")).toBe("true");
    expect(clean.getAttribute("aria-description")).toContain("计划已过期");
    // A second, live plan in another window stays usable (multi-window).
    const live = plan();
    const g = fixture({ planValue: live });
    render(<StorageSection api={g.api} csrfToken="csrf" connectionError="" />);
    const panel2 = screen.getAllByRole("region", { name: "存储" })[1]!;
    await user.click(within(panel2).getByRole("button", { name: "检查占用" }));
    const clean2 = await within(panel2).findByRole("button", { name: "回收…" }) as HTMLButtonElement;
    expect(clean2.getAttribute("aria-disabled")).toBe("false");
  });

  it("keeps unavailable controls focusable and refuses their clicks", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="offline" />);
    const check = screen.getByRole("button", { name: "检查占用" });
    await user.tab();
    expect(document.activeElement).toBe(check);
    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("button", { name: "回收…" }));
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("refuses confirmation if the connection is interrupted while its dialog is open", async () => {
    const f = fixture();
    const user = userEvent.setup();
    const view = render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: "回收…" }));
    view.rerender(<StorageSection api={f.api} csrfToken="csrf" connectionError="offline" />);
    const confirm = within(screen.getByRole("dialog")).getByRole("button", { name: "确认清理" });
    expect(confirm.getAttribute("aria-disabled")).toBe("true");
    expect(confirm.getAttribute("aria-description")).toBe("offline");
    await user.click(confirm);
    expect(f.calls.filter(call => call.operation === "storage_apply")).toHaveLength(0);
  });

  it("checks the exact expiry at dispatch even before a display timer can run", async () => {
    const expiresAt = Date.now() + 60_000;
    const f = fixture({ planValue: plan({ expiresAt: new Date(expiresAt).toISOString() }) });
    const user = userEvent.setup();
    render(<StorageSection api={f.api} csrfToken="csrf" connectionError="" />);
    await checkUsage(user);
    await user.click(screen.getByRole("button", { name: "回收…" }));
    vi.spyOn(Date, "now").mockReturnValue(expiresAt);
    await user.click(screen.getByRole("button", { name: "确认清理" }));
    expect(f.calls.filter(call => call.operation === "storage_apply")).toHaveLength(0);
    expect(await screen.findByText("计划已过期；请关闭并重新检查")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认清理" }).getAttribute("aria-disabled")).toBe("true");
  });



});
