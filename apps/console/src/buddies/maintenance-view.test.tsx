import { cleanup, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { snapshotReadSchema } from "../types";
import { fixture, snapshot, openRoles, MEDIUM_ID, FLASH_ID } from "./roles.testing";

function state() {
  const data = snapshot({ configuration: { maintenanceBuddyId: MEDIUM_ID, maintenanceIntervalSeconds: 3600 } });
  data.harnesses = data.harnesses.map(h => ({ ...h, permissions: h.harness === "codex" ? { read: "enforced" } : { none: "convention" } }));
  data.domainNodes = [
    { nodeId: "root", name: "工程", definition: "工程工作", parentId: null, mergedIntoId: null },
    { nodeId: "child", name: "前端", definition: "浏览器界面", parentId: "root", mergedIntoId: null },
    { nodeId: "other", name: "存储", definition: "持久化工作", parentId: null, mergedIntoId: null },
  ];
  data.maintenance = { pendingCount: 3, runs: [] };
  return data;
}
afterEach(() => { cleanup(); window.location.hash = ""; window.localStorage.clear(); });

describe("maintenance controls", () => {
  it("rejects malformed native usage at the console read boundary", () => {
    const data = state();
    const tokenUsage = { inputTokens: 100, cachedInputTokens: 20, outputTokens: 10, source: "native", scope: "attempt" };
    const run = { maintenanceRunId: "m1", triggerType: "manual", buddyId: MEDIUM_ID,
      buddy: { harness: "claude", provider: "anthropic", model: "sonnet-4-6", effort: "medium" }, taskId: "maintain-1", state: "completed", reason: null,
      startedAt: "2026-10-28T00:00:00Z", finishedAt: "2026-10-28T00:01:00Z" };
    const read = (usage: unknown) => snapshotReadSchema.safeParse({ ...data, maintenance: { pendingCount: 0, runs: [{ ...run,
      usage: { attempts: [{ attemptId: "a1", elapsedMs: 60000, toolCalls: 0, tokenUsage: usage }] } }] } }).success;
    expect(read(tokenUsage)).toBe(true);
    for (const usage of [42, { ...tokenUsage, inputTokens: "100" }, { ...tokenUsage, outputTokens: -1 }, { ...tokenUsage, cachedInputTokens: 101 }]) expect(read(usage)).toBe(false);
  });
  it("keeps a confirmed domain write visible when its subsequent read fails", async () => {
    const f = fixture(state(), { domain_rename: () => ({ nodeId: "root" }) });
    await openRoles(f); const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: "改名" })[0]);
    vi.mocked(f.api.snapshot).mockRejectedValueOnce(new Error("read interrupted"));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const tree = document.querySelector('[data-slot="domain-tree"]') as HTMLElement;
    expect(within(tree).getByRole("status").textContent).toBe("已保存");
    expect(within(tree).getByRole("alert").textContent).toBe("操作已提交，读取未刷新");
    expect(f.operations).toEqual(["domain_rename"]);
  });
  it("offers only enabled available no-tool buddies and publishes maintenance edits through the shared draft", async () => {
    const f = fixture(state()); await openRoles(f); const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "选择 buddy" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/dsh \/ deepseek-official/)).toBeTruthy();
    expect(within(dialog).queryByText(/codex/)).toBeNull();
    expect(within(dialog).queryByText(/sonnet-4-6 \/ high/)).toBeNull();
    await user.click(within(dialog).getByText(/dsh \/ deepseek-official/));
    const interval = screen.getByRole("spinbutton", { name: "维护间隔" });
    await user.clear(interval); await user.type(interval, "1800");
    expect(screen.getByRole("button", { name: "立即维护" }).hasAttribute("disabled")).toBe(true);
    await user.click(screen.getByRole("button", { name: /^保存/ }));
    await waitFor(() => expect(f.published).toHaveLength(1));
    expect(f.published[0].configuration).toEqual({ maintenanceBuddyId: FLASH_ID, maintenanceIntervalSeconds: 1800 });
    expect(f.operations).toEqual(["user_policy_publish"]);
  });
  it("blocks invalid interval drafts without publishing a previous valid prefix", async () => {
    const f = fixture(state()); await openRoles(f); const user = userEvent.setup();
    const interval = screen.getByRole("spinbutton", { name: "维护间隔" });
    await user.clear(interval); await user.type(interval, "0");
    expect(screen.getByText("维护间隔需要是正整数秒数")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^保存/ }));
    expect(f.published).toHaveLength(0);
  });
  for (const [result, feedback] of [
    [{ status: "no-new-evidence" }, "没有新证据"], [{ status: "not-configured" }, "没有设维护 buddy"],
    [{ status: "running", runId: "maintain-1" }, "已开始"],
    [{ status: "unavailable", reason: { code: "router-disabled", params: {} } }, "不可用："],
    [{ status: "settings-changed" }, "设置已变化"],
  ] as const) it(`reports ${result.status} without inventing a start`, async () => {
    const f = fixture(state(), { maintenance_start: () => result }); await openRoles(f);
    await userEvent.click(screen.getByRole("button", { name: "立即维护" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain(feedback));
    expect(f.operations).toEqual(["maintenance_start"]);
  });
  it("keeps an unsuccessful domain edit open and shows its error inside the dialog", async () => {
    const f = fixture(state(), { domain_rename: () => { throw new Error("Duplicate domain name"); } });
    await openRoles(f); const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: "改名" })[0]);
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toBe("Duplicate domain name"));
  });
  it("edits definitions and merges an explicit source into a selected active domain", async () => {
    const calls: object[] = [];
    const f = fixture(state(), { domain_rename: params => { calls.push(params); return {}; }, domain_merge: params => { calls.push(params); return {}; } });
    await openRoles(f); const user = userEvent.setup();
    const row = screen.getByText("前端").closest('[data-slot="domain-row"]') as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "改名" }));
    const rename = screen.getByRole("dialog");
    await user.clear(within(rename).getByLabelText("名称")); await user.type(within(rename).getByLabelText("名称"), "界面工程");
    await user.clear(within(rename).getByLabelText("定义")); await user.type(within(rename).getByLabelText("定义"), "浏览器排版与交互");
    await user.click(within(rename).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls[0]).toEqual({ nodeId: "child", name: "界面工程", definition: "浏览器排版与交互" });
    await user.click(within(row).getByRole("button", { name: "合并" }));
    const merge = screen.getByRole("dialog"); await user.click(within(merge).getByRole("button", { name: "存储" }));
    await user.click(within(merge).getByRole("button", { name: /^合并$/ }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toEqual({ sourceId: "child", targetId: "other" });
  });
});
