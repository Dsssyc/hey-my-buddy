import { cleanup, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { snapshotReadSchema } from "../types";
import type { Maintenance } from "../types";
import { formatShortDateTime } from "../i18n/format";
import { fixture, snapshot, openRoles as openRouter, MEDIUM_ID, FLASH_ID } from "./roles.testing";

const openRoles = (f: ReturnType<typeof fixture>) => openRouter(f, "#buddies/router/maintenance");

function state() {
  const data = snapshot({ configuration: { maintenanceBuddyId: MEDIUM_ID, maintenanceIntervalSeconds: 3600 } });
  data.harnesses = data.harnesses.map(h => ({ ...h, permissions: h.harness === "codex" ? { read: "enforced" } : { none: "convention" } }));
  data.domainNodes = [
    { nodeId: "root", name: "工程", definition: "工程工作", parentId: null, mergedIntoId: null, directCount: 1 },
    { nodeId: "child", name: "前端", definition: "浏览器界面", parentId: "root", mergedIntoId: null, directCount: 2 },
    { nodeId: "other", name: "存储", definition: "持久化工作", parentId: null, mergedIntoId: null, directCount: 1 },
  ];
  data.maintenance = { pendingCount: 3, runs: [] };
  return data;
}
afterEach(() => { cleanup(); window.location.hash = ""; window.localStorage.clear(); });

function maintenanceRun(state: "running" | "failed"): Maintenance["runs"][number] {
  return { maintenanceRunId: "m1", taskId: "maintain-1", triggerType: "manual", buddyId: MEDIUM_ID,
    buddy: { harness: "claude", provider: "anthropic", model: "claude-sonnet-5", effort: "medium" }, state,
    startedAt: "2026-10-28T00:00:00Z", finishedAt: state === "failed" ? "2026-10-28T00:05:01Z" : null,
    reason: state === "failed" ? { code: "router-not-responding", params: { seconds: 60 } } : null,
    usage: { attempts: [{ attemptId: "a1", elapsedMs: null, toolCalls: null,
      tokenUsage: state === "failed" ? { inputTokens: 120, cachedInputTokens: null, outputTokens: 7, source: "native", scope: "attempt" } : null }] } };
}

describe("maintenance controls", () => {
  it("reads recorded native activity and stops the execution through the existing cancel operation", async () => {
    const data = state(); data.maintenance!.runs = [maintenanceRun("running")];
    const f = fixture(data, { task_cancel: () => ({ alreadyTerminal: false }) });
    vi.mocked(f.api.task).mockResolvedValue({ runId: "maintain-1", activity: {
      phase: "streaming-model", observedAt: new Date().toISOString(), lastNativeActivityAt: new Date().toISOString(), eventSeq: 12,
    } });
    await openRoles(f);
    expect(await screen.findByText("模型输出中")).toBeTruthy();
    expect(f.api.task).toHaveBeenCalledWith("maintain-1", expect.any(AbortSignal));
    await userEvent.click(screen.getByRole("button", { name: "停止" }));
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("task_cancel", { runId: "maintain-1" }, "csrf"));
    expect((screen.getByRole("button", { name: "正在停止" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("已请求停止")).toBeTruthy();
    const signal = vi.mocked(f.api.task).mock.calls[0][1]!;
    cleanup(); expect(signal.aborted).toBe(true);
  });
  it("places the next automatic run beside the interval and preserves partial usage without a retry action", async () => {
    const data = state(); data.maintenance!.runs = [maintenanceRun("failed")];
    const f = fixture(data); await openRoles(f);
    const row = document.querySelector('[data-slot="maintenance-record"]') as HTMLElement;
    expect(screen.getByText(`下次自动运行 ${formatShortDateTime(Date.parse("2026-10-28T01:05:01Z"))}`)).toBeTruthy();
    const usage = within(row).getByRole("button", { name: /维护记录.*用量/ });
    expect(usage.getAttribute("aria-expanded")).toBe("false");
    await userEvent.click(usage);
    expect(usage.getAttribute("aria-expanded")).toBe("true");
    expect(within(row).getByText(/缓存输入 未知; 其他输入 未知; 输出 7/)).toBeTruthy();
    expect(within(row).getByText("Router 的模型在 60 秒内没有开始作答。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /立即运行|立即维护/ })).toBeNull();
    expect(f.operations).toEqual([]);
  });
  it("shows maintenance validation in plain language and keeps the original identifiers on hover", async () => {
    const data = state();
    const failed = maintenanceRun("failed");
    failed.reason = { code: "maintenance-answer-invalid", params: { errors: ["summary-citation-outside-batch", "future-validation-code"] } };
    data.maintenance!.runs = [failed];
    await openRoles(fixture(data));
    const reason = within(document.querySelector<HTMLElement>('[data-slot="maintenance-record"]')!).getByText(/维护的回答最终仍不合格/);
    expect(reason.textContent).toContain("摘要引用了本批次之外的微任务");
    expect(reason.textContent).toContain("维护回答未通过校验");
    expect(reason.textContent).not.toContain("summary-citation-outside-batch");
    expect(reason.getAttribute("aria-description")).toBe("summary-citation-outside-batch; future-validation-code");
  });
  it("keeps an older failure as history when a newer batch completed", async () => {
    const data = state();
    const failed = maintenanceRun("failed");
    const completed = { ...failed, maintenanceRunId: "m2", taskId: "maintain-2", state: "completed" as const,
      reason: null, startedAt: "2026-10-28T01:00:00Z", finishedAt: "2026-10-28T01:05:00Z" };
    data.maintenance!.runs = [completed, failed];
    await openRoles(fixture(data));
    const older = within(document.querySelectorAll('[data-slot="maintenance-record"]')[1] as HTMLElement);
    expect(older.getByText("Router 的模型在 60 秒内没有开始作答。")).toBeTruthy();
    const usage = older.getByRole("button", { name: /维护记录.*用量/ });
    expect(usage.getAttribute("aria-expanded")).toBe("false");
    await userEvent.click(usage);
    expect(older.getByText(/缓存输入 未知; 其他输入 未知; 输出 7/)).toBeTruthy();
    expect(older.queryByText(/下次自动运行/)).toBeNull();
    expect(older.queryByRole("button", { name: "立即运行" })).toBeNull();
  });
  it("keeps progress unknown when only the phase has been reported", async () => {
    const data = state(); data.maintenance!.runs = [maintenanceRun("running")];
    const f = fixture(data);
    vi.mocked(f.api.task).mockResolvedValue({ runId: "maintain-1", activity: { phase: "waiting-model", observedAt: new Date().toISOString() } });
    await openRoles(f);
    await waitFor(() => expect(f.api.task).toHaveBeenCalled());
    expect(screen.getByText("进展未知")).toBeTruthy();
    expect(f.operations).toEqual([]);
  });
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
    await user.hover(document.querySelector<HTMLElement>('[data-node="root"].domain-tree-row')!);
    await user.click(screen.getByRole("button", { name: "改名" }));
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
    await user.click(within(document.querySelector<HTMLElement>('[data-slot="maintenance-plane"]')!).getByRole("button", { name: "选择 buddy" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/dsh \/ deepseek-official/)).toBeTruthy();
    expect(within(dialog).queryByText(/codex/)).toBeNull();
    expect(within(dialog).queryByText(/sonnet-4-6 \/ high/)).toBeNull();
    await user.click(within(dialog).getByText(/dsh \/ deepseek-official/));
    const interval = screen.getByRole("spinbutton", { name: "维护间隔" });
    await user.clear(interval); await user.type(interval, "1800");
    expect(screen.queryByRole("button", { name: "立即维护" })).toBeNull();
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
  it("keeps an unsuccessful domain edit open and shows its error inside the dialog", async () => {
    const f = fixture(state(), { domain_rename: () => { throw new Error("Duplicate domain name"); } });
    await openRoles(f); const user = userEvent.setup();
    await user.hover(document.querySelector<HTMLElement>('[data-node="root"].domain-tree-row')!);
    await user.click(screen.getByRole("button", { name: "改名" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toBe("Duplicate domain name"));
  });
  it("edits definitions and merges an explicit source into a selected active domain", async () => {
    const calls: object[] = [];
    const f = fixture(state(), { domain_rename: params => { calls.push(params); return {}; }, domain_merge: params => { calls.push(params); return {}; } });
    await openRoles(f); const user = userEvent.setup();
    await user.click(document.querySelector<HTMLElement>('[data-node="root"].domain-tree-row')!);
    const row = document.querySelector<HTMLElement>('[data-node="child"].domain-tree-row')!;
    await user.hover(row);
    await user.click(screen.getByRole("button", { name: "改名" }));
    const rename = screen.getByRole("dialog");
    await user.clear(within(rename).getByLabelText("名称")); await user.type(within(rename).getByLabelText("名称"), "界面工程");
    await user.clear(within(rename).getByLabelText("定义")); await user.type(within(rename).getByLabelText("定义"), "浏览器排版与交互");
    await user.click(within(rename).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls[0]).toEqual({ nodeId: "child", name: "界面工程", definition: "浏览器排版与交互" });
    await user.hover(row);
    await user.click(screen.getByRole("button", { name: "合并" }));
    const merge = screen.getByRole("dialog"); await user.click(within(merge).getByRole("button", { name: "存储" }));
    await user.click(within(merge).getByRole("button", { name: /^合并$/ }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toEqual({ sourceId: "child", targetId: "other" });
  });
});
