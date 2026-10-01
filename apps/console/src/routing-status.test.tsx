import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RoutingStatusBar } from "./RoutingStatusBar";
import type { Editor } from "./use-editor";
import type { ConsoleView, Profile, RoutingHealth, Snapshot } from "./types";
import { makeDraft } from "./draft";

const editor = {
  editing: false, configurationDirty: false, sessionWritable: true, mode: false, draft: null,
  update: vi.fn(),
} as unknown as Editor;

const router: Profile = {
  profileId: "codex:openai:test:high", label: "Test", adapter: "codex",
  provider: "openai", model: "test", effort: "high", available: true, enabled: true,
  capabilities: ["execution:codex", "routing:fast", "decision"], contextWindow: null, description: "", source: "catalog",
};

function snapshot(routingHealth: RoutingHealth | undefined, extra: Partial<Snapshot> = {}): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "s", canWrite: false, reason: null }, tableRevision: 1,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileId: router.profileId, defaultRoutingMode: "review", routingBudget: "standard" },
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
    expect(health().textContent).toContain("路由摘要未知");
    // Missing data is never rendered as a zero-failure claim.
    expect(health().textContent).not.toContain("失败 0 次");
    expect(bar().textContent).toContain("状态：未知");
  });

  it("reports an empty window as no recorded samples rather than zero failures", () => {
    renderBar(snapshot({ ...healthy, sampleCount: 0, lastSuccessAt: null }));
    expect(health().textContent).toContain("最近 20 次内暂无样本");
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
    expect(status.textContent).not.toContain("无成功记录");
    expect(status.textContent).toMatch(/最后一次成功：09-25 \d{2}:\d{2} · dec-41/);
    expect(status.querySelectorAll(".routing-failures li").length).toBe(3);
    expect(status.textContent).toContain("call-failed");
    expect(status.textContent).toContain("needs-host");
    expect(status.textContent).toContain("委派未记录");
    // The read-only note lives in the `?` tooltip, a fixed layer outside the
    // container and still wired to the button through aria-describedby.
    const help = within(status).getByRole("button", { name: "路由健康说明", hidden: true });
    const tip = document.getElementById(help.getAttribute("aria-describedby")!)!;
    expect(tip.className).toContain("help-tip");
    expect(tip.parentElement).toBe(document.body);
    expect(tip.textContent).toContain("弃权、取消和过期不计为失败");
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
    expect(health().textContent).toContain("弃权 1 次 · 取消 1 次 · 过期 1 次");
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
  it("edits the default mode and review budget while showing read-only limits and data flow", async () => {
    const state = snapshot(healthy, { configuration: {
      revision: 1, routerProfileId: router.profileId,
      defaultRoutingMode: "review", routingBudget: "standard",
      routingBudgetLimits: { preset: "standard", timeoutSeconds: 300, toolCalls: 24, bytesRead: 524288 },
    } });
    const changes: ReturnType<typeof makeDraft>[] = [];
    const writable = { ...editor, editing: true, update: vi.fn(fn => changes.push(fn(makeDraft(state)))) } as unknown as Editor;
    render(<RoutingStatusBar data={state as ConsoleView} snapshot={state} editor={writable} onShowRouter={vi.fn()} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "详情" }));
    await userEvent.setup().click(screen.getByRole("radio", { name: "快速" }));
    await userEvent.setup().click(screen.getByRole("radio", { name: "简要" }));
    expect(changes[0].configuration!.defaultRoutingMode).toBe("fast");
    expect(changes[1].configuration!.routingBudget).toBe("brief");
    const budgetHelp = screen.getByRole("button", { name: "路由预算说明", hidden: true });
    const budgetTip = document.getElementById(budgetHelp.getAttribute("aria-describedby")!)!;
    expect(budgetTip.textContent).toContain("标准 300 秒 / 24 次工具调用");
    expect(budgetTip.textContent).not.toContain("字节");
    const flowHelp = screen.getByRole("button", { name: "路由数据流向", hidden: true });
    const flowTip = document.getElementById(flowHelp.getAttribute("aria-describedby")!)!;
    expect(flowTip.textContent).toContain("任务包发送给 Router");
    expect(flowTip.textContent).toContain("冻结代码副本");
    expect(flowTip.textContent).toContain("DSH/ZCode 无系统沙盒，不能保证阻止副本外读取或外传");
  });
  it("shows Router, budget and health in one line and no warning when nothing needs handling", () => {
    renderBar(snapshot(healthy));
    expect(bar().className).not.toContain("warning");
    expect(bar().textContent).toContain("Router：Test · high");
    expect(bar().textContent).not.toMatch(/快速 Router|审阅 Router/);
    expect(bar().textContent).toContain("默认模式：审阅");
    expect(bar().textContent).toContain("审阅预算：标准");
    expect(bar().textContent).toContain("状态：正常");
    expect(bar().querySelector(".routing-warning")).toBeNull();
  });

  it("turns into a warning with the resolving action when the Router lacks local eligibility", () => {
    const unverified = { ...router, capabilities: ["execution:codex"] };
    const alternative = { ...router, profileId: "claude:anthropic:verified:medium", adapter: "claude", provider: "anthropic", model: "verified" };
    renderBar(snapshot(healthy, { profiles: [unverified, alternative] }));
    expect(bar().className).toContain("warning");
    expect(bar().querySelector(".routing-warning")!.textContent)
      .toContain("尚不具备本地只读路由资格");
    expect(bar().querySelector(".routing-warning")!.textContent).not.toMatch(/routing:fast|decision/);
  });

  it("explains the Host boundary when no eligible Router is available", async () => {
    const user = userEvent.setup();
    const unverified = { ...router, capabilities: ["execution:codex"] };
    renderBar(snapshot(healthy, { profiles: [unverified] }));
    const warning = bar().querySelector(".routing-warning")!.textContent;
    expect(warning).toContain("尚不具备本地只读路由资格");
    await user.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText(/审阅模式需具备本地只读资格/)).toBeTruthy();
  });

  it("warns when no Router is set and when routing keeps failing", () => {
    const view = renderBar(snapshot(healthy, { configuration: { revision: 1, routerProfileId: null , defaultRoutingMode: "review" as const, routingBudget: "standard"} }));
    expect(bar().textContent).toContain("Router：尚未指定，请选择");
    expect(bar().querySelector(".routing-warning")!.textContent).toContain("未指定 Router");
    view.unmount();
    renderBar(snapshot({ ...healthy, available: false, reasonCode: "ROUTER_UNAVAILABLE", failureCount: 3, consecutiveFailures: 3 }));
    expect(bar().textContent).toContain("状态：不可用");
    expect(bar().querySelector(".routing-warning")!.textContent).toContain("请查看详情");
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
    expect(within(panel).getAllByText("可担任")).toHaveLength(1);
    const budget = within(panel).getByRole("radiogroup", { name: "审阅预算" });
    expect(within(budget).getAllByRole("radio").map(radio => radio.parentElement!.textContent)).toEqual(["简要", "标准", "深入"]);
    expect(within(budget).getByRole("radio", { name: "标准" })).toHaveProperty("checked", true);
    // A read-only editor keeps the choice visible but disabled.
    expect(within(budget).getByRole("radio", { name: "深入" })).toHaveProperty("disabled", true);
    await user.click(within(panel).getAllByRole("button", { name: "查看所在家族" })[0]);
    expect(onShowRouter).toHaveBeenCalledWith(router.profileId);
  });
  it("shows upgrade unavailability and disables Router controls without inventing settings", async () => {
    const state = snapshot(healthy, { configuration: null, configurationError: { code: "router-settings-upgrade-required", message: "请先升级设置", revision: 1 } });
    const writable = { ...editor, editing: true } as Editor;
    render(<RoutingStatusBar data={state as ConsoleView} snapshot={state} editor={writable} onShowRouter={vi.fn()} expanded />);
    expect(bar().textContent).toContain("升级不可用");
    expect(screen.getByRole("status").textContent).toContain("请先升级设置");
    expect(screen.getAllByRole("radio").every(input => (input as HTMLInputElement).disabled)).toBe(true);
    expect(bar().textContent).not.toContain("默认模式：审阅");
  });
  it("does not expose a retained dirty Router draft as usable after an upgrade boundary", () => {
    const draft = snapshot(healthy);
    const state = { ...draft, configuration: null,
      configurationError: { code: "router-settings-upgrade-required", message: "请先升级设置", revision: 1 } };
    render(<RoutingStatusBar data={draft as ConsoleView} snapshot={state} editor={{ ...editor, editing: true } as Editor}
      onShowRouter={vi.fn()} expanded />);
    expect(screen.getByRole("status").textContent).toContain("请先升级设置");
    expect(screen.queryByText("可担任")).toBeNull();
    expect(screen.getAllByRole("radio").every(input => (input as HTMLInputElement).disabled)).toBe(true);
  });
  it("uses the service availability fact without deriving a failure threshold", () => {
    renderBar(snapshot({ ...healthy, available: true, consecutiveFailures: 4, failureCount: 4 }));
    expect(bar().textContent).toContain("状态：连续失败 4 次");
    expect(bar().textContent).not.toContain("状态：不可用");
  });

});
