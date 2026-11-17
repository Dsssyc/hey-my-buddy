import { act, cleanup, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { useEditor } from "../use-editor";
import { useProfileActions } from "./use-profile-actions";
import { ApiError, errorText } from "../api";
import { setLanguageChoice } from "../i18n";
import type { ProfileRun } from "../types";
import { fixture, snapshot, MEDIUM_ID } from "./roles.testing";

const identity = { harness: "claude", modelId: "claude-sonnet-5", effort: "medium" };
const stats = { samples: 8, successes: 6, failures: 2, firstPasses: 4, reworkRounds: 3, firstPassRate: 0.5, failureRate: 0.25, medianDeliverySeconds: 240,
  usage: { samples: 7, medianInputTokens: 1200, medianCachedInputTokens: 300, medianOutputTokens: 500 } };
function state() {
  const data = snapshot({ configuration: { profileBuddyId: MEDIUM_ID, profileUnavailableReason: null, maintenanceBuddyId: MEDIUM_ID } });
  data.kindEvaluations = [{ ...identity, modelDisplayName: "claude-sonnet-5", modelNames: data.models.find(row => row.modelId === identity.modelId)!.names,
    previousGroups: [], summary: { text: "Good at bounded changes." }, statistics: { overall: stats, byDomain: {}, byDomainTaskType: {}, infrastructureFailures: [] } }];
  data.maintenance = { pendingCount: 3, runs: [] };
  return data;
}
function run(state: ProfileRun["state"]): ProfileRun {
  return { profileRunId: "profile-run", taskId: "profile-task", buddyId: MEDIUM_ID, buddy: { harness: "claude", provider: "anthropic", model: "claude-sonnet-5", effort: "medium" },
    state, reason: state === "failed" ? { code: "router-not-responding", params: { seconds: 60 } } : null,
    startedAt: "2026-11-01T00:00:00Z", finishedAt: state === "running" ? null : "2026-11-01T00:01:00Z", correctionRounds: 0, toolSummary: [],
    usage: { attempts: [{ attemptId: "attempt", elapsedMs: 60000, toolCalls: 0, tokenUsage: { inputTokens: 100, cachedInputTokens: 20, outputTokens: 10, source: "native", scope: "attempt" } }] } };
}
async function open(f: ReturnType<typeof fixture>, suffix = "") {
  window.location.hash = `#buddies/evaluation/claude/claude-sonnet-5/medium${suffix}`;
  render(createElement(App, { suppliedApi: f.api }));
  await screen.findByRole("region", { name: "评价" });
}
const list = () => document.querySelector<HTMLElement>('[data-slot="evaluation-list"]')!;
const detail = () => document.querySelector<HTMLElement>('[data-slot="evaluation-detail"]')!;
afterEach(() => { cleanup(); window.location.hash = ""; window.localStorage.clear(); vi.useRealTimers(); });

describe("evaluation navigation and gates", () => {
  it("groups by harness, filters in-use kinds, searches, and excludes a prior group from All", async () => {
    const data = state(); const current = data.kindEvaluations![0];
    current.previousGroups = [{ modelId: "previous", modelDisplayName: "Prior generation", names: [{ ...current.modelNames[0], since: "2026-11-01T00:00:00Z" }] }];
    data.models.push({ modelId: "previous", displayName: "Prior generation", names: [], profile: null, latestProfileRun: null });
    data.kindEvaluations!.push({ ...current, modelId: "previous", modelDisplayName: "Prior generation", previousGroups: [] });
    const f = fixture(data); await open(f);
    const links = () => within(list()).getAllByRole("link");
    expect(links().some(link => link.textContent?.includes(" / high"))).toBe(false);
    expect(within(detail()).getByRole("region", { name: "之前的样本" })).toBeTruthy();
    await userEvent.click(within(list()).getByRole("checkbox", { name: "全部" }));
    expect(links().some(link => link.textContent?.includes(" / high"))).toBe(true);
    expect(links().some(link => link.textContent?.includes("Prior generation"))).toBe(false);
    await userEvent.type(within(list()).getByRole("searchbox"), "gpt-6-sol");
    expect(links().every(link => link.textContent?.includes("gpt-6-sol"))).toBe(true);
    expect(f.operations).toEqual([]);
  });
  it("keeps names and search within the selected harness when a model is shared", async () => {
    const data = state();
    data.kindEvaluations![0].modelNames.push({ harness: "codex", provider: "other", model: "different-harness-name" });
    const f = fixture(data); await open(f);
    const names = document.querySelector<HTMLElement>('[data-slot="evaluation-names"]')!;
    expect(names.textContent).toContain("claude-sonnet-5");
    expect(names.textContent).not.toContain("different-harness-name");
    await userEvent.type(within(list()).getByRole("searchbox"), "different-harness-name");
    expect(within(list()).queryByRole("link")).toBeNull();
  });
  it("refuses profile and maintenance without the published Router assignment", async () => {
    const data = state(); data.configuration!.profileBuddyId = null; data.configuration!.maintenanceBuddyId = null;
    const f = fixture(data); await open(f);
    const build = within(list()).getByRole("button", { name: "批量构建" });
    expect(build).toHaveProperty("disabled", true);
    expect(build.getAttribute("aria-description")).toContain("Router 的画像");
    const maintain = within(list()).getByRole("button", { name: "立即维护" });
    expect(maintain).toHaveProperty("disabled", true);
    expect(maintain.getAttribute("aria-description")).toContain("Router 的维护");
    await userEvent.click(build); await userEvent.click(maintain);
    expect(f.operations).toEqual([]);
  });
  it("runs maintenance once through its existing operation", async () => {
    const f = fixture(state(), { maintenance_start: () => ({ started: true }) }); await open(f);
    await userEvent.click(within(list()).getByRole("button", { name: "立即维护" }));
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("maintenance_start", {}, "csrf"));
    expect(f.operations).toEqual(["maintenance_start"]);
  });
  it("confirms quota, unique models and count before one profile_start per missing model", async () => {
    const data = state(); const starts: string[] = [];
    const f = fixture(data, { profile_start: params => { starts.push(params.modelId); return { status: "running", profileRunId: `run-${params.modelId}`, runId: `task-${params.modelId}`, buddyId: MEDIUM_ID }; } });
    await open(f); await userEvent.click(within(list()).getByRole("button", { name: "批量构建" }));
    const dialog = screen.getByRole("dialog", { name: "确认构建画像" });
    expect(dialog.querySelector("[data-slot=dialog-description]")?.textContent).toContain("claude / anthropic / claude-sonnet-5 / medium");
    const models = within(dialog).getAllByRole("listitem").map(item => item.textContent!);
    expect(new Set(models).size).toBe(models.length);
    expect(within(dialog).getByText(`为以上每个模型各构建一次，共 ${models.length} 次。`)).toBeTruthy();
    expect(starts).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "确认构建" }));
    await waitFor(() => expect(starts).toHaveLength(models.length));
    expect(f.operations.every(operation => operation === "profile_start")).toBe(true);
    expect(new Set(starts).size).toBe(starts.length);
    expect(f.command).toHaveBeenCalledWith("profile_start", { modelId: expect.any(String) }, "csrf");
  });
  it("shows an unavailable or malformed start as short feedback and never reports a successful run", async () => {
    const f = fixture(state(), { profile_start: () => ({ status: "running", profileRunId: 42 }) }); await open(f, "/profile");
    await userEvent.click(within(detail()).getByRole("button", { name: "构建画像" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认构建" }));
    expect(await screen.findByText("画像操作的回复格式不正确")).toBeTruthy();
    expect(document.querySelectorAll('[data-slot="profile-notice"]')).toHaveLength(1);
    expect(f.operations).toEqual(["profile_start"]);
  });
  it("stops a batch after session expiry and does not retry", async () => {
    const f = fixture(state(), { profile_start: () => { throw new ApiError("CONSOLE_SESSION_EXPIRED", "expired"); } }); await open(f);
    await userEvent.click(within(list()).getByRole("button", { name: "批量构建" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "确认构建" }));
    expect(await screen.findByText(errorText(new ApiError("CONSOLE_SESSION_EXPIRED", "expired")))).toBeTruthy();
    expect(f.operations).toEqual(["profile_start"]);
  });
  it("observes one terminal transition per run and expires the notice without repeating it", () => {
    vi.useFakeTimers();
    const data = state(); data.models.find(model => model.modelId === identity.modelId)!.latestProfileRun = run("running");
    const f = fixture(data);
    const refresh = vi.fn(async () => data);
    const { result, rerender } = renderHook(({ snapshot }) => {
      const editor = useEditor(f.api, snapshot, refresh, true, "");
      return useProfileActions({ snapshot, editor, api: f.api, refresh, mutationsAvailable: true });
    }, { initialProps: { snapshot: structuredClone(data) } });
    expect(result.current.notices).toHaveLength(0);
    data.models.find(model => model.modelId === identity.modelId)!.latestProfileRun = run("failed");
    rerender({ snapshot: structuredClone(data) });
    expect(result.current.notices).toHaveLength(1);
    expect(result.current.notices[0].run).toMatchObject({ state: "failed", profileRunId: "profile-run", usage: { attempts: [{ tokenUsage: { inputTokens: 100 } }] } });
    rerender({ snapshot: structuredClone(data) });
    expect(result.current.notices).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(6001); });
    expect(result.current.notices).toHaveLength(0);
    rerender({ snapshot: structuredClone(data) });
    expect(result.current.notices).toHaveLength(0);
  });
});

describe("evaluation correspondence drafts", () => {
  it("selects a name before explaining a generation and publishes only through Save", async () => {
    const data = state();
    const source = data.modelCorrespondences.find(row => row.modelId === identity.modelId)!;
    const alias = { ...source, model: "sonnet-latest", correspondenceId: "alias-current", source: "user" as const, canUndo: true };
    data.modelCorrespondences.push(alias);
    data.models.find(model => model.modelId === identity.modelId)!.names.push(alias);
    data.kindEvaluations![0].modelNames.push(alias);
    data.buddies.push({ ...data.buddies.find(buddy => buddy.buddyId === MEDIUM_ID)!, buddyId: "claude:anthropic:sonnet-latest:medium", model: "sonnet-latest" });
    const f = fixture(data); await open(f);
    const names = within(detail()).getByText("名称：").parentElement!;
    within(names).getByRole("button", { name: "模型操作" }).focus(); await userEvent.keyboard("{Enter}");
    await userEvent.click(await screen.findByRole("menuitem", { name: "标记新一代" }));
    const dialog = screen.getByRole("dialog", { name: "标记新一代" });
    expect(within(dialog).getAllByRole("radio")).toHaveLength(2);
    const next = within(dialog).getByRole("button", { name: "继续" });
    expect(next).toHaveProperty("disabled", true);
    await userEvent.click(within(dialog).getByRole("radio", { name: "sonnet-latest" }));
    await userEvent.click(next);
    expect(within(dialog).queryByRole("radio")).toBeNull();
    expect(within(dialog).getByText(/此后 sonnet-latest 的新结果计入新一代/)).toBeTruthy();
    expect(dialog.textContent).not.toMatch(/8 个样本/);
    expect(f.operations).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "记入修改" }));
    expect(screen.getByRole("region", { name: "未保存的修改" })).toBeTruthy();
    expect(f.operations).toEqual([]);
    expect(within(list()).getByRole("button", { name: "批量构建" })).toHaveProperty("disabled", true);
    await userEvent.click(screen.getByRole("button", { name: /^保存$/ }));
    await waitFor(() => expect(f.published).toHaveLength(1));
    expect(f.published[0].modelCorrespondenceChanges).toEqual([{ harness: "claude", provider: "anthropic", model: "sonnet-latest", action: "generation", displayName: "claude-sonnet-5" }]);
  });
  it("confirms a mapping, cancels it without a draft, and preserves the recorded undo identity", async () => {
    const data = state(); const correspondence = data.modelCorrespondences.find(row => row.modelId === identity.modelId)!;
    correspondence.source = "profile"; correspondence.canUndo = true;
    const f = fixture(data); await open(f);
    const menu = () => within(detail()).getByRole("button", { name: "模型操作" });
    menu().focus(); await userEvent.keyboard("{Enter}"); await userEvent.click(await screen.findByRole("menuitem", { name: "归入其他模型" }));
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByRole("radio")).toBeNull();
    expect(within(dialog).getByRole("button", { name: "记入修改" })).toHaveProperty("disabled", true);
    await userEvent.selectOptions(within(dialog).getByRole("combobox"), "gpt-6-sol");
    expect(within(dialog).getByText(/此后的新结果计入该模型/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    menu().focus(); await userEvent.keyboard("{Enter}"); await userEvent.click(await screen.findByRole("menuitem", { name: "撤回" }));
    dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/已累计的样本、显示名称与目录均不变/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "记入修改" }));
    await userEvent.click(screen.getByRole("button", { name: /^保存$/ }));
    await waitFor(() => expect(f.published).toHaveLength(1));
    expect(f.published[0].modelCorrespondenceChanges).toEqual([{ harness: correspondence.harness, provider: correspondence.provider, model: correspondence.model, action: "undo", correspondenceId: correspondence.correspondenceId }]);
    expect(f.operations).toEqual(["user_policy_publish"]);
  });
  it("blocks every action in a read-only session and never starts work on mount", async () => {
    const data = state(); data.consoleSession = undefined as unknown as typeof data.consoleSession;
    const f = fixture(data); await open(f, "/profile");
    expect(within(detail()).getByRole("button", { name: "构建画像" })).toHaveProperty("disabled", true);
    expect(within(list()).getByRole("button", { name: "批量构建" })).toHaveProperty("disabled", true);
    expect(within(list()).getByRole("button", { name: "立即维护" })).toHaveProperty("disabled", true);
    await userEvent.click(within(detail()).getByRole("link", { name: "领域评价" }));
    await userEvent.click(within(detail()).getByRole("button", { name: "模型操作" }));
    expect(screen.getAllByRole("menuitem").every(item => item.getAttribute("data-disabled") !== null)).toBe(true);
    expect(f.operations).toEqual([]);
  });
});

describe("the language's list separators in the evaluation detail", () => {
  afterEach(() => setLanguageChoice("zh"));
  it("joins the names row and the providers with the language's own list form", async () => {
    const data = state();
    const source = data.modelCorrespondences.find(row => row.modelId === identity.modelId)!;
    const alias = { ...source, model: "sonnet-latest", correspondenceId: "alias-current", source: "user" as const };
    const other = { ...source, provider: "bedrock", model: "sonnet-bedrock", correspondenceId: "alias-bedrock", source: "user" as const };
    data.modelCorrespondences.push(alias, other);
    data.models.find(row => row.modelId === identity.modelId)!.names.push(alias, other);
    data.kindEvaluations![0].modelNames.push(alias, other);
    data.buddies.push({ ...data.buddies.find(buddy => buddy.buddyId === MEDIUM_ID)!, buddyId: "claude:bedrock:sonnet-bedrock:medium", alias: null, provider: "bedrock", model: "sonnet-bedrock" });
    const f = fixture(data); await open(f);
    const names = () => document.querySelector<HTMLElement>('[data-slot="evaluation-names"]')!;
    expect(names().textContent).toContain("claude-sonnet-5、sonnet-latest、sonnet-bedrock");
    expect(detail().textContent).toContain("anthropic、bedrock 共用");
    act(() => setLanguageChoice("en"));
    expect(names().textContent).toContain("claude-sonnet-5, sonnet-latest, and sonnet-bedrock");
    expect(detail().textContent).toContain("Shared by anthropic and bedrock");
    expect(f.operations).toEqual([]);
  });
});

it("keeps the accessibility description for a rich profile refusal", async () => {
  const data = state();
  data.configuration!.profileUnavailableReason = { code: "profile-not-configured", params: {} };
  await open(fixture(data));
  const button = within(list()).getByRole("button", { name: "批量构建" });
  expect(button).toHaveProperty("disabled", true);
  expect(button.getAttribute("aria-description")).toBe("尚未指定画像 buddy。");
});
