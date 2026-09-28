import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import type { Editor } from "./use-editor";
import type { Profile, Snapshot } from "./types";
import { createApi } from "./api";

const editor = {
  editing: false, configurationDirty: false, sessionWritable: true, mode: null, draft: null,
  setDraft: vi.fn(),
} as unknown as Editor;
const api = createApi("/test", vi.fn() as unknown as typeof fetch);

function snapshot(routingHealth: Snapshot["routingHealth"]): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "s", canWrite: false, reason: null }, tableRevision: 1,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: null },
    profiles: [], cards: [], preferences: [], annotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { runs: [], total: 0 }, capabilities: {},
    routingHealth,
  };
}

afterEach(() => cleanup());

describe("settings routing status (R4)", () => {
  it("says the summary is unavailable when the snapshot carries no routingHealth", () => {
    render(<Settings snapshot={snapshot(undefined)} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
    expect(status.textContent).toContain("路由摘要暂不可用");
    // Missing data is never rendered as a zero-failure claim.
    expect(status.textContent).not.toContain("失败 0 次");
  });

  it("reports an empty window as no recorded samples rather than zero failures", () => {
    render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 0, failureCount: 0, consecutiveFailures: 0,
      abstentionCount: 0, cancelledCount: 0, staleCount: 0,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    })} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
    expect(status.textContent).toContain("窗口内暂无已记录样本（窗口上限 20 次）");
    expect(status.textContent).not.toContain("失败 0 次");
  });

  it("shows the window counts, separated non-failures, last success and bounded recent failures", () => {
    render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 18, failureCount: 4, consecutiveFailures: 2,
      abstentionCount: 3, cancelledCount: 1, staleCount: 2,
      lastSuccessAt: "2026-09-25T08:02:00Z", lastSuccessDecisionId: "dec-41",
      recentFailures: [
        { decisionId: "dec-48", runId: "run-a", at: "2026-09-27T09:41:00Z", code: "call-failed" },
        { decisionId: "dec-47", runId: null, at: "2026-09-27T09:12:00Z", code: "needs-host" },
        { decisionId: "dec-46", runId: "run-b", at: "2026-09-27T08:44:00Z", code: "timeout" },
      ],
    })} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
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
    // Read-only note; no retry or routing control appears.
    expect(status.textContent).toContain("读取不触发模型");
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });

  it("stays visible for read-only sessions and never depends on the draft", () => {
    const view = render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 5, failureCount: 1, consecutiveFailures: 1,
      abstentionCount: 0, cancelledCount: 0, staleCount: 0,
      lastSuccessAt: "2026-09-27T07:00:00Z", lastSuccessDecisionId: null, recentFailures: [],
    })} editor={editor} api={api} />);
    expect(view.container.querySelector(".routing-status")).toBeTruthy();
    expect(screen.getByLabelText("路由状态").textContent).toContain("最近 5 次中失败 1 次");
  });

  it("reports recorded Router outcomes separately without changing legacy failure semantics", () => {
    render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 12, failureCount: 2, consecutiveFailures: 0,
      abstentionCount: 1, cancelledCount: 1, staleCount: 1,
      budgetExhaustedCount: 3, boundsRejectedCount: 2, inputChangedCount: 1,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    })} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
    expect(status.textContent).toContain("最近 12 次中失败 2 次");
    expect(status.textContent).toContain("弃权 1 次 · 取消 1 次 · 过期 1 次，不计为失败");
    expect(within(status).getByText("预算耗尽 3 次 · 边界检查拒绝 2 次 · 输入已变化 1 次")).toBeTruthy();
  });

  it("distinguishes recorded zero outcomes from missing optional counters", () => {
    const state = snapshot({
      windowSize: 20, sampleCount: 1, failureCount: 0, consecutiveFailures: 0,
      abstentionCount: 0, cancelledCount: 0, staleCount: 0,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    });
    const view = render(<Settings snapshot={state} editor={editor} api={api} />);
    expect(screen.getByLabelText("路由状态").textContent).not.toMatch(/预算耗尽|边界检查拒绝|输入已变化/);
    view.rerender(<Settings snapshot={{ ...state, routingHealth: {
      ...state.routingHealth!, budgetExhaustedCount: 0,
    } }} editor={editor} api={api} />);
    expect(screen.getByLabelText("路由状态").textContent).toContain("预算耗尽 0 次");
    expect(screen.getByLabelText("路由状态").textContent).not.toMatch(/边界检查拒绝|输入已变化/);
  });
});

describe("settings routing model", () => {
  const profile: Profile = {
    profileId: "codex:openai:test:high", label: "Test", adapter: "codex",
    provider: "openai", model: "test", effort: "high", available: true, enabled: true,
    capabilities: ["execution:codex"], contextWindow: null, description: "", source: "catalog",
  };

  it("shows verified routing capability candidates without inferring support from the adapter", () => {
    const verified = { ...profile, profileId: "zcode:verified:test:high", adapter: "zcode", capabilities: ["decision"] };
    const state = { ...snapshot(undefined), profiles: [profile, verified] };
    render(<Settings snapshot={state} editor={editor} api={api} />);
    expect(screen.getByRole("heading", { name: "路由模型" })).toBeTruthy();
    const options = within(screen.getByLabelText("路由模型配置")).getAllByRole("option");
    expect(options.map(option => (option as HTMLOptionElement).value)).toEqual(["", verified.profileId]);
    expect(screen.queryByText("还没有经过验证的路由模型。")).toBeNull();
  });

  it("retains a stale configured model for attention while explaining an empty verified candidate list", () => {
    const state = { ...snapshot(undefined), profiles: [profile],
      configuration: { revision: 1, decisionProfileId: profile.profileId } };
    render(<Settings snapshot={state} editor={editor} api={api} />);
    expect(screen.getByText("还没有经过验证的路由模型。")).toBeTruthy();
    const select = screen.getByLabelText("路由模型配置");
    expect(select).toHaveProperty("value", profile.profileId);
    expect(within(select).getByRole("option", { name: /需要处理/ })).toHaveProperty("disabled", true);
    expect(screen.getByRole("status").textContent).toContain("没有经过验证的路由能力；需要处理，但不影响保存其他修改");
    expect(screen.getByText("路由不可用")).toBeTruthy();
    expect(screen.queryByText("路由可用")).toBeNull();
  });
});
