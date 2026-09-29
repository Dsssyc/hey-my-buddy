import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RoutingStatusBar } from "./RoutingStatusBar";
import type { Editor } from "./use-editor";
import type { ConsoleView, Profile, RoutingHealth, Snapshot } from "./types";

const editor = {
  editing: false, configurationDirty: false, sessionWritable: true, mode: false, draft: null,
  update: vi.fn(),
} as unknown as Editor;

const router: Profile = {
  profileId: "codex:openai:test:high", label: "Test", adapter: "codex",
  provider: "openai", model: "test", effort: "high", available: true, enabled: true,
  capabilities: ["execution:codex", "decision"], contextWindow: null, description: "", source: "catalog",
};

function snapshot(routingHealth: RoutingHealth | undefined, extra: Partial<Snapshot> = {}): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "s", canWrite: false, reason: null }, tableRevision: 1,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: router.profileId },
    profiles: [router], cards: [], preferences: [], familyPreferences: [], preferenceOverrides: [],
    familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { runs: [], total: 0 }, capabilities: {},
    routingHealth,
    ...extra,
  };
}
const healthy: RoutingHealth = {
  windowSize: 20, sampleCount: 5, failureCount: 0, consecutiveFailures: 0,
  abstentionCount: 0, cancelledCount: 0, staleCount: 0,
  lastSuccessAt: "2026-09-27T07:00:00Z", lastSuccessDecisionId: null, recentFailures: [],
};

function renderBar(state: Snapshot, onShowRouter = vi.fn()) {
  const view = render(<RoutingStatusBar data={state as unknown as ConsoleView} snapshot={state} editor={editor} onShowRouter={onShowRouter} />);
  return { ...view, onShowRouter };
}
const health = () => screen.getByLabelText("路由健康");
const bar = () => screen.getByRole("region", { name: "路由状态" });

afterEach(() => cleanup());

describe("routing health details (R4)", () => {
  it("says the summary is unavailable when the snapshot carries no routingHealth", () => {
    renderBar(snapshot(undefined));
    expect(health().textContent).toContain("路由摘要暂不可用");
    // Missing data is never rendered as a zero-failure claim.
    expect(health().textContent).not.toContain("失败 0 次");
    expect(bar().textContent).toContain("状态：未知");
  });

  it("reports an empty window as no recorded samples rather than zero failures", () => {
    renderBar(snapshot({ ...healthy, sampleCount: 0, lastSuccessAt: null }));
    expect(health().textContent).toContain("窗口内暂无已记录样本（窗口上限 20 次）");
    expect(health().textContent).not.toContain("失败 0 次");
    expect(bar().textContent).toContain("状态：暂无样本");
  });

  it("shows the window counts, separated non-failures, last success and bounded recent failures", () => {
    renderBar(snapshot({
      windowSize: 20, sampleCount: 18, failureCount: 4, consecutiveFailures: 2,
      abstentionCount: 3, cancelledCount: 1, staleCount: 2,
      lastSuccessAt: "2026-09-25T08:02:00Z", lastSuccessDecisionId: "dec-41",
      recentFailures: [
        { decisionId: "dec-48", runId: "run-a", at: "2026-09-27T09:41:00Z", code: "call-failed" },
        { decisionId: "dec-47", runId: null, at: "2026-09-27T09:12:00Z", code: "needs-host" },
        { decisionId: "dec-46", runId: "run-b", at: "2026-09-27T08:44:00Z", code: "timeout" },
      ],
    }));
    const status = health();
    expect(status.textContent).toContain("最近 18 次中失败 4 次（窗口上限 20 次）");
    expect(status.textContent).toContain("连续失败 2 次");
    expect(status.textContent).toContain("弃权 3 次");
    expect(status.textContent).toContain("取消 1 次");
    expect(status.textContent).toContain("过期 2 次");
    expect(status.textContent).toContain("不计为失败");
    expect(status.textContent).not.toContain("无成功记录");
    expect(status.textContent).toMatch(/最后一次成功：09-25 \d{2}:\d{2} · dec-41/);
    expect(status.querySelectorAll(".routing-failures li").length).toBe(3);
    expect(status.textContent).toContain("call-failed");
    expect(status.textContent).toContain("needs-host");
    expect(status.textContent).toContain("委派未记录");
    // The read-only note lives in the `?` tooltip; no retry or routing control appears.
    expect(within(status).getByRole("tooltip", { hidden: true }).textContent).toContain("读取不触发模型");
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });

  it("reports recorded Router outcomes separately without changing legacy failure semantics", () => {
    renderBar(snapshot({
      windowSize: 20, sampleCount: 12, failureCount: 2, consecutiveFailures: 0,
      abstentionCount: 1, cancelledCount: 1, staleCount: 1,
      budgetExhaustedCount: 3, boundsRejectedCount: 2, inputChangedCount: 1,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    }));
    expect(health().textContent).toContain("最近 12 次中失败 2 次");
    expect(health().textContent).toContain("弃权 1 次 · 取消 1 次 · 过期 1 次，不计为失败");
    expect(within(health()).getByText("预算耗尽 3 次 · 边界检查拒绝 2 次 · 输入已变化 1 次")).toBeTruthy();
  });

  it("distinguishes recorded zero outcomes from missing optional counters", () => {
    const state = snapshot({ ...healthy, sampleCount: 1, lastSuccessAt: null });
    const view = renderBar(state);
    expect(health().textContent).not.toMatch(/预算耗尽|边界检查拒绝|输入已变化/);
    const next = { ...state, routingHealth: { ...state.routingHealth!, budgetExhaustedCount: 0 } };
    view.rerender(<RoutingStatusBar data={next as unknown as ConsoleView} snapshot={next} editor={editor} onShowRouter={vi.fn()} />);
    expect(health().textContent).toContain("预算耗尽 0 次");
    expect(health().textContent).not.toMatch(/边界检查拒绝|输入已变化/);
  });
});

describe("the one-line routing status", () => {
  it("shows Router, budget and health in one line and no warning when nothing needs handling", () => {
    renderBar(snapshot(healthy));
    expect(bar().className).not.toContain("warning");
    expect(bar().textContent).toContain("Router：Test · high");
    expect(bar().textContent).toContain("预算：标准");
    expect(bar().textContent).toContain("状态：正常");
    expect(bar().querySelector(".routing-warning")).toBeNull();
  });

  it("turns into a warning with the resolving action when the Router is not verified", () => {
    const unverified = { ...router, capabilities: ["execution:codex"] };
    const alternative = { ...router, profileId: "claude:anthropic:verified:medium", adapter: "claude", provider: "anthropic", model: "verified" };
    renderBar(snapshot(healthy, { profiles: [unverified, alternative] }));
    expect(bar().className).toContain("warning");
    expect(bar().querySelector(".routing-warning")!.textContent)
      .toContain("当前 Router Test · high 未验证路由能力：请在另一个具备 decision 能力的档位菜单中选择“设为 Router”");
  });

  it("explains the Host boundary when no verified Router is available", async () => {
    const user = userEvent.setup();
    const unverified = { ...router, capabilities: ["execution:codex"] };
    renderBar(snapshot(healthy, { profiles: [unverified] }));
    const warning = bar().querySelector(".routing-warning")!.textContent;
    expect(warning).toContain("暂无已验证的 Router，默认路由会停在 Host 边界；委派时请指定配置");
    expect(warning).not.toContain("设为 Router");
    await user.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText(/目录中没有同时满足已启用、当前可用且具备已验证 decision 能力的档位/)).toBeTruthy();
  });

  it("warns when no Router is set and when routing keeps failing", () => {
    const view = renderBar(snapshot(healthy, { configuration: { revision: 1, decisionProfileId: null } }));
    expect(bar().textContent).toContain("Router：尚未指定");
    expect(bar().querySelector(".routing-warning")!.textContent).toContain("尚未指定 Router");
    view.unmount();
    renderBar(snapshot({ ...healthy, failureCount: 3, consecutiveFailures: 3 }));
    expect(bar().textContent).toContain("状态：连续失败 3 次");
    expect(bar().querySelector(".routing-warning")!.textContent).toContain("请在“详情”中查看失败明细");
  });

  it("gathers the Router, the budget and health under 详情 and jumps to the Router's family", async () => {
    const user = userEvent.setup();
    const { onShowRouter } = renderBar(snapshot(healthy));
    const details = screen.getByRole("button", { name: "详情" });
    expect(details.getAttribute("aria-expanded")).toBe("false");
    expect(document.getElementById("routing-details")!.hidden).toBe(true);
    await user.click(details);
    expect(details.getAttribute("aria-expanded")).toBe("true");
    const panel = document.getElementById("routing-details")!;
    expect(panel.hidden).toBe(false);
    expect(within(panel).getByText("可担任")).toBeTruthy();
    const budget = within(panel).getByRole("radiogroup", { name: /路由预算/ });
    expect(within(budget).getAllByRole("radio").map(radio => radio.parentElement!.textContent)).toEqual(["快速", "标准", "深入"]);
    expect(within(budget).getByRole("radio", { name: "标准" })).toHaveProperty("checked", true);
    // A read-only editor keeps the choice visible but disabled.
    expect(within(budget).getByRole("radio", { name: "深入" })).toHaveProperty("disabled", true);
    await user.click(within(panel).getByRole("button", { name: "查看所在家族" }));
    expect(onShowRouter).toHaveBeenCalledWith(router.profileId);
  });
});
