import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { ProfileRun, TokenUsage } from "../types";
import { fixture, snapshot, openRoles, MEDIUM, MEDIUM_ID, saveButton } from "./roles.testing";
import { routerUsageTotal, sumRouterUsage } from "./RouterUsage";

afterEach(() => { cleanup(); window.location.hash = ""; window.localStorage.clear(); });

function usage(inputTokens: number | null, cachedInputTokens: number | null, outputTokens: number | null): TokenUsage {
  return { inputTokens, cachedInputTokens, outputTokens, source: "native", scope: "attempt", inputBasis: "includes-cached", completeness: "complete" };
}

function run(state: ProfileRun["state"], startedAt: string): ProfileRun {
  return {
    profileRunId: `profile-${state}`, taskId: `task-${state}`, buddyId: MEDIUM_ID,
    buddy: { harness: "claude", provider: "anthropic", model: "claude-sonnet-5", effort: "medium" },
    state, reason: state === "failed" ? { code: "profile-answer-invalid", params: { errors: ["Claims lack original sources"] } } : null,
    startedAt, finishedAt: state === "running" ? null : "2026-11-08T16:30:00Z", correctionRounds: state === "failed" ? 1 : 0,
    toolSummary: state === "failed" ? [
      { toolName: "Web search", calls: 6, failed: 0, unknown: 0 },
      { toolName: "Page fetch", calls: 8, failed: 8, unknown: 0 },
    ] : null,
    usage: state === "running" ? null : { attempts: [
      { attemptId: "first", elapsedMs: 138000, toolCalls: 6, tokenUsage: usage(12480, 2048, 1620) },
      { attemptId: "second", elapsedMs: 102000, toolCalls: 8, tokenUsage: usage(9200, 4096, 840) },
    ] },
  };
}

describe("Router profile records", () => {
  it("shows the newest failed run expanded, with its validation reason, attempts and fully failed tool", async () => {
    const data = snapshot({ configuration: { profileBuddyId: MEDIUM_ID, profileUnavailableReason: null } });
    data.models = data.models.slice(0, 3);
    data.models[0] = { ...data.models[0], latestProfileRun: run("completed", "2026-11-07T11:20:00Z") };
    data.models[1] = { ...data.models[1], latestProfileRun: run("failed", "2026-11-08T16:26:00Z") };
    data.models[2] = { ...data.models[2], latestProfileRun: run("running", "2026-11-08T16:25:00Z") };
    await openRoles(fixture(data), "#buddies/router/profiles");
    const records = screen.getByRole("group", { name: "记录" });
    const failedButton = within(records).getByRole("button", { name: data.models[1].displayName });
    expect(failedButton.getAttribute("aria-expanded")).toBe("true");
    const failed = failedButton.closest("tr")!.nextElementSibling! as HTMLElement;
    expect(within(failed).getByText(/Claims lack original sources/)).toBeTruthy();
    expect(within(failed).getByRole("rowheader", { name: /Page fetch.*全部失败/ })).toBeTruthy();
    expect(within(failed).getByText("第 2 次尝试")).toBeTruthy();
    expect(within(records).getByLabelText("尚未结束")).toHaveProperty("textContent", "…");
    const toolBar = within(failed).getByRole("button", { name: /Page fetch:.*失败 8/ });
    expect(toolBar.getAttribute("aria-expanded")).toBe("false");
    expect(toolBar.querySelector('[data-kind="fail"]')).toBeTruthy();
    expect(within(failed).queryByText(/成功 0 · 失败 8/)).toBeNull();
    await userEvent.setup().click(toolBar);
    expect(toolBar.getAttribute("aria-expanded")).toBe("true");
    expect(within(failed).getByText(/成功 0 · 失败 8/)).toBeTruthy();
    const usageBar = within(failed).getByRole("button", { name: /第 2 次尝试 · 用量/ });
    expect(usageBar.getAttribute("aria-expanded")).toBe("false");
    await userEvent.setup().click(usageBar);
    expect(usageBar.getAttribute("aria-expanded")).toBe("true");
    expect(within(failed).getByText(/缓存输入 4,096/)).toBeTruthy();
  });

  it("keeps assignment restricted to buddies with enforced web permission", async () => {
    const data = snapshot();
    data.harnesses = data.harnesses.map(harness => ({ ...harness, permissions: harness.harness === "claude" ? { web: "enforced" } : { web: "convention" } }));
    const f = fixture(data);
    await openRoles(f, "#buddies/router/profiles");
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.getByText("未指定画像 buddy；画像构建不会运行。请选择 buddy。")).toBeTruthy();
    await userEvent.setup().click(within(screen.getByRole("group", { name: "谁担任" })).getByRole("button", { name: "选择 buddy" }));
    const dialog = screen.getByRole("dialog", { name: "选择 buddy" });
    expect(within(dialog).getByRole("button", { name: MEDIUM })).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: /gpt-6-sol/ })).toBeNull();
    expect(within(dialog).queryByRole("button", { name: /sonnet-5 \/ high/ })).toBeNull();
    await userEvent.setup().click(within(dialog).getByRole("button", { name: MEDIUM }));
    expect(within(screen.getByRole("group", { name: "谁担任" })).getByRole("img", { name: "未保存" })).toBeTruthy();
    await userEvent.setup().click(saveButton());
    await screen.findByText("设置已保存");
    expect(f.published).toHaveLength(1);
    expect(f.published[0].configuration).toEqual({ profileBuddyId: MEDIUM_ID });
  });

  it("shows a recorded readiness reason and locks assignment when configuration is unavailable", async () => {
    const configured = snapshot({ configuration: { profileBuddyId: MEDIUM_ID, profileUnavailableReason: { code: "profile-web-unsupported", params: {} } } });
    await openRoles(fixture(configured), "#buddies/router/profiles");
    expect(within(screen.getByRole("group", { name: "谁担任" })).getByRole("status").textContent).toBe("画像 buddy 的 harness 未声明强制只联网档位。");
    cleanup();
    await openRoles(fixture(snapshot({ configuration: null })), "#buddies/router/profiles");
    expect(within(screen.getByRole("group", { name: "谁担任" })).getByRole("button", { name: "选择 buddy" }).hasAttribute("disabled")).toBe(true);
  });

  it("keeps assignment read only without a browser session or during a save", async () => {
    const noSession = snapshot({ sessionPresent: false });
    await openRoles(fixture(noSession), "#buddies/router/profiles");
    expect(within(screen.getByRole("group", { name: "谁担任" })).getByRole("button", { name: "选择 buddy" }).hasAttribute("disabled")).toBe(true);
    cleanup();
    const data = snapshot();
    data.harnesses = data.harnesses.map(harness => ({ ...harness, permissions: { web: "enforced" } }));
    let finish!: (value: unknown) => void;
    const pending = new Promise(resolve => { finish = resolve; });
    const f = fixture(data, { user_policy_publish: () => pending });
    await openRoles(f, "#buddies/router/profiles");
    const user = userEvent.setup();
    const choose = within(screen.getByRole("group", { name: "谁担任" })).getByRole("button", { name: "选择 buddy" });
    await user.click(choose);
    await user.click(within(screen.getByRole("dialog", { name: "选择 buddy" })).getByRole("button", { name: MEDIUM }));
    await user.click(saveButton());
    await waitFor(() => expect(choose.hasAttribute("disabled")).toBe(true));
    expect(f.operations).toEqual(["user_policy_publish"]);
    finish({ published: true });
  });

  it("preserves unknown token fields across attempts and never counts cached input twice", () => {
    const total = sumRouterUsage([{ tokenUsage: usage(100, 20, 10) }, { tokenUsage: usage(50, 10, 5) }]);
    expect(total).toMatchObject({ inputTokens: 150, cachedInputTokens: 30, outputTokens: 15 });
    expect(routerUsageTotal(total)).toBe(165);
    expect(sumRouterUsage([{ tokenUsage: usage(100, null, 10) }, { tokenUsage: usage(50, 10, 5) }]))
      .toMatchObject({ inputTokens: 150, cachedInputTokens: null, outputTokens: 15 });
    expect(routerUsageTotal(sumRouterUsage([{ tokenUsage: usage(100, 20, null) }]))).toBeNull();
  });
});
