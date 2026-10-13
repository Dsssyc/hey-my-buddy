import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import {
  catalogue, chip, choosePreference, effortCell, foldHandle, level, levelPreference, listed, modelButton, modelRow, pane,
  queryFoldHandle, queryModel, search, selectModel,
} from "./buddies/page.testing";
import type { ConsoleApi, HarnessHealth } from "./api";
import type {
  Preference,
  Profile,
  Snapshot,
} from "./types";

/**
 * The models table of the Buddy page: the two filters and the search, the
 * harnesses that fold, each buddy drawn as a chip, a level's own preference
 * (including 不设), the preference written
 * in words instead of colour, and the settings page that carries none of this.
 */

const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { adapter: "codex", provider: "openai", model: "gpt-6-sol" };
const glm = { adapter: "zcode", provider: "zhipu", model: "glm-5" };

const mediumId = "claude:anthropic:claude-sonnet-5:medium";
const highId = "claude:anthropic:claude-sonnet-5:high";
const solMediumId = "codex:openai:gpt-6-sol:medium";
const solLowId = "codex:openai:gpt-6-sol:low";
const glmId = "zcode:zhipu:glm-5:default";

/** The rows are named by the first three parts of a buddy's identity. */
const SONNET = "claude / anthropic / claude-sonnet-5";
const SOL = "codex / openai / gpt-6-sol";
const GLM = "zcode / zhipu / glm-5";

function profile(entry: Partial<Profile> & Pick<Profile, "profileId" | "label" | "adapter" | "provider" | "model" | "effort">): Profile {
  return {
    available: true, enabled: false, capabilities: [], contextWindow: null,
    description: "", source: "catalog:fixture", ...entry,
  };
}

/** Three harnesses: one available model service, one all-disabled model service, one offline model service. */
function profiles(): Profile[] {
  return [
    profile({ profileId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium",
      enabled: true, capabilities: ["execution:claude", "routing:fast", "decision"], contextWindow: 200_000 }),
    profile({ profileId: highId, label: "Claude Sonnet 5 · high", ...sonnet, effort: "high",
      capabilities: ["execution:claude", "decision"], contextWindow: 200_000 }),
    profile({ profileId: solMediumId, label: "GPT-6 Sol · medium", ...sol, effort: "medium",
      capabilities: ["execution:codex"] }),
    profile({ profileId: solLowId, label: "GPT-6 Sol · low", ...sol, effort: "low",
      capabilities: ["execution:codex"] }),
    profile({ profileId: glmId, label: "GLM-5 · default", ...glm, effort: "default", available: false,
      unavailableReason: "本机未检测到 zcode CLI" }),
  ];
}

type Options = { configurationUnavailable?: boolean; routerProfileId?: string | null; preferences?: Preference[] };

/**
 * The snapshot's harness health (ADR-017 §15): the Claude harness used by the
 * available model service is ready, and the ZCode harness behind the unavailable
 * model service is recorded as missing with an attempted location and a remedy.
 */
function harnesses(): HarnessHealth[] {
  return [
    { adapter: "claude", status: "ready", available: true, revision: 2, manualPath: null,
      executable: "/Users/fixture/.local/bin/claude", version: "2.0.1",
      source: "版本管理器（nvm 默认版本）", checkedAt: "2026-09-28T09:30:00.000Z", candidates: [] },
    { adapter: "zcode", status: "missing", available: false, revision: 1, manualPath: null,
      reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI，或填写可执行文件的绝对路径。",
      checkedAt: "2026-09-28T09:30:00.000Z",
      candidates: [{ path: "/opt/homebrew/bin/zcode", source: "Homebrew", status: "missing" }] },
  ];
}

type HarnessSnapshot = Snapshot & { harnesses: HarnessHealth[] };

function snapshot(options: Options = {}): HarnessSnapshot {
  const base: Snapshot = {
    csrfToken: "csrf", eventCursor: 0,
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: options.configurationUnavailable ? null : { routerProfileIds: options.routerProfileId === undefined ? [mediumId] : options.routerProfileId === null ? [] : [options.routerProfileId],
      routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard" },
    configurationError: options.configurationUnavailable ? { code: "ROUTER_SETTINGS_UPGRADE_REQUIRED", message: "请先升级设置",} : null,
    profiles: profiles(),
    preferences: options.preferences ?? [],

    modelServiceNotes: [],

    decisions: [],

    modelServiceConcurrency: [
      { ...sonnet, limit: 2, active: 0 },
      { ...sol, limit: 2, active: 0 },
      { ...glm, limit: 2, active: 0 },
    ],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };
  return { ...base, harnesses: harnesses() };
}

function applyPublication(state: HarnessSnapshot, params: Record<string, any>): HarnessSnapshot {
  const next: HarnessSnapshot = {
    ...state,

    preferences: [...state.preferences],
    modelServiceNotes: [...state.modelServiceNotes],
    configuration: state.configuration ? { ...state.configuration, ...(params.configuration ?? {}) } : null,
  };
  for (const setting of params.profileSettings ?? []) {
    next.profiles = next.profiles.map(entry => entry.profileId === setting.profileId ? { ...entry, enabled: setting.enabled } : entry);
  }
  const key = (entry: { adapter: string; provider: string; model: string }) => JSON.stringify([entry.adapter, entry.provider, entry.model]);
  for (const change of params.preferenceChanges ?? []) {
    next.preferences = next.preferences.filter(entry => entry.profileId !== change.profileId);
    if (change.mode !== null) {
      next.preferences.push({ profileId: change.profileId, mode: change.mode, reason: change.reason });
    }
  }
  for (const change of params.modelServiceNoteChanges ?? []) {
    next.modelServiceNotes = next.modelServiceNotes.filter(entry => key(entry) !== key(change));
    next.modelServiceNotes.push({ ...change, updatedAt: null });
  }
  return next;
}

function fixture(options: Options = {}) {
  let state = snapshot(options);
  const operations: string[] = [];
  const published: Record<string, any>[] = [];

  const command = vi.fn(async (operation: string, params: Record<string, any>, _csrfToken?: string) => {
    operations.push(operation);
    if (operation === "model_profiles") {
      // Only a checked 显示不可用 asks for this page; it mirrors the board's
      // bounded contract and just returns the current rows.
      return { profiles: state.profiles, modelServiceNotes: state.modelServiceNotes,
        preferences: state.preferences,
        modelServiceConcurrency: state.modelServiceConcurrency, nextCursor: null };
    }

    if (operation === "user_policy_publish") {
      published.push(params);
      state = applyPublication(state, params);
      return { published: true,};
    }
    if (operation === "storage_plan") {
      return { planId: "plan-1", createdAt: "2026-09-28T12:00:00Z",
        expiresAt: new Date(Date.now() + 600_000).toISOString(),
        categories: [{ id: "runtimes", label: "", bytes: 1_000_000, reclaimableBytes: 1_000_000, count: 3, eligibleCount: 3, reasons: [] }],
        candidates: [], orphanProcesses: [] };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
    storagePlan: vi.fn(async (csrfToken: string) => command("storage_plan", {}, csrfToken)),
    storageApply: vi.fn(),
    runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: "0.29.0", contractVersion: "fixture", schemaVersion: 15, sourceCommit: null, installedAt: null }, installed: null })),
  } as unknown as ConsoleApi;
  return { api, command, operations, published };
}

async function openBuddy(api: ConsoleApi) {
  window.location.hash = "#buddies";
  render(<App suppliedApi={api} />);
  // The unavailable ZCode model is hidden by default, so two models with four buddies show.
  await listed(1, 4);
}

const saveButton = () => within(screen.getByRole("region", { name: "未保存的修改" })).getByRole("button", { name: /^(保存|重试同一保存)$/ });
const enabledOnly = () => screen.getByRole("checkbox", { name: "只看已启用" });
const showUnavailable = () => screen.getByRole("checkbox", { name: "显示不可用 1" });

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("the harness directory", () => {
  it("filters to models with an enabled buddy and restores the full list", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api);
    // A harness without an enabled buddy starts folded; the filter still counts its buddies.
    expect(modelButton(SONNET)).toBeTruthy();
    expect(foldHandle("Codex").getAttribute("aria-expanded")).toBe("false");
    expect(queryModel(SOL)).toBeNull();
    // The unavailable filter hides models, while the recorded harness stays in view.
    expect(queryFoldHandle("ZCode")).toBeTruthy();
    expect(queryModel(GLM)).toBeNull();
    expect(showUnavailable()).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(enabledOnly());
    // GPT-6 Sol has no enabled buddy; the whole ZCode harness stays unusable.
    await listed(1, 2);
    expect(queryFoldHandle("Codex")).toBeTruthy();
    expect(queryFoldHandle("ZCode")).toBeTruthy();
    expect(queryModel(SOL)).toBeNull();
    expect(queryModel(GLM)).toBeNull();
    expect(modelButton(SONNET)).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(enabledOnly());
    await listed(1, 4);
    expect(foldHandle("Codex")).toBeTruthy();
    // The two filters combine: with 显示不可用 on, the offline model is listed,
    // and 只看已启用 still hides it because none of its buddies is enabled.
    await user.click(showUnavailable());
    await listed(1, 5);
    expect(foldHandle("ZCode").getAttribute("aria-expanded")).toBe("false");
    await user.click(enabledOnly());
    await listed(1, 2);
    expect(queryFoldHandle("ZCode")).toBeTruthy();
    expect(queryModel(GLM)).toBeNull();
    await user.click(enabledOnly());
    await listed(1, 5);
  });

  it("folds a harness without enabled buddies into one row of counts, and says why a harness cannot work", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api);
    await user.click(showUnavailable());
    await listed(1, 5);
    expect(foldHandle("Claude Code").getAttribute("aria-expanded")).toBe("true");
    const codex = foldHandle("Codex");
    expect(codex.getAttribute("aria-expanded")).toBe("false");
    expect(codex.closest("[data-slot=harness-section]")!.textContent).toContain("1 个模型");
    expect(codex.closest("[data-slot=harness-section]")!.textContent).toContain("启用 0 / 2");
    expect(queryModel(SOL)).toBeNull();
    await user.click(codex);
    expect(modelButton(SOL)).toBeTruthy();
    // Folding a harness that has an enabled buddy keeps that count in sight.
    await user.click(foldHandle("Claude Code"));
    expect(foldHandle("Claude Code").closest("[data-slot=harness-section]")!.textContent).toContain("启用 1 / 2");
    expect(queryModel(SONNET)).toBeNull();
    // The harness that cannot work is written with its recorded state; the
    // recorded reason is its explanation, on hover and for a screen reader.
    const zcode = foldHandle("ZCode");
    expect(zcode.getAttribute("aria-expanded")).toBe("false");
    const state = within(zcode.closest("[data-slot=harness-section]")!).getByRole("button", { name: "ZCode 账户" });
    expect(within(state).getByText("未找到")).toBeTruthy();
    expect(state.getAttribute("aria-description")).toContain("本机未检测到 zcode CLI");
    await user.hover(state);
    expect((await screen.findByRole("tooltip")).textContent).toContain("本机未检测到 zcode CLI");
    // A folded harness hides its models; unfolded, the model says nothing the harness already said.
    expect(queryModel(GLM)).toBeNull();
    await user.click(zcode);
    expect(within(modelRow(GLM)).queryByText("不可用")).toBeNull();
    // Its side column says once that it cannot work, and why.
    await user.click(modelButton(GLM));
    expect(within(pane(GLM)).getByText("不可用").getAttribute("aria-description")).toBe("ZCode 未找到 · 本机未检测到 zcode CLI");
  });

  it("shows only enabled reasoning efforts in rows and opens a single drawer without naming roles", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api);
    expect(pane()).toBeNull();
    expect(chip(SONNET, "medium").textContent).toBe("medium");
    expect(within(modelRow(SONNET)).queryByText("high")).toBeNull();
    await user.click(foldHandle("Codex"));
    expect(within(modelRow(SOL)).queryByText("low")).toBeNull();
    expect(within(catalogue()).queryByText("Router")).toBeNull();
    await user.click(modelButton(SOL));
    expect(modelButton(SOL).getAttribute("aria-expanded")).toBe("true");
    expect(modelButton(SONNET).getAttribute("aria-expanded")).toBe("false");
    expect(pane(SOL)).toBeTruthy();
    expect(effortCell("low").getAttribute("aria-pressed")).toBe("true");
    expect(effortCell("medium").getAttribute("aria-pressed")).toBe("false");
  });

  it("matches adapter id, harness display name, provider and model name, case-insensitively", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api);
    // Harness display name ("Claude Code"), not only the `claude` adapter id.
    await user.type(search(), "claude code");
    await listed(1, 2);
    expect(modelButton(SONNET)).toBeTruthy();
    expect(queryFoldHandle("Codex")).toBeTruthy();
    expect(queryModel(SOL)).toBeNull();
    // While the unavailable filter is off, searching stays a local filter.
    expect(f.command).not.toHaveBeenCalled();
    // Provider, uppercase: case does not matter. The offline ZCode model is
    // searched once 显示不可用 is on; its own search then also reaches the
    // server so retained rows beyond the snapshot stay findable.
    await user.clear(search());
    await user.click(showUnavailable());
    await user.type(search(), "ZHIPU");
    await listed(0, 1);
    expect(foldHandle("ZCode")).toBeTruthy();
    expect(queryFoldHandle("Claude Code")).toBeTruthy();
    expect(queryModel(SONNET)).toBeNull();
    // The model's display label, lowercase, still finds the row that shows its id.
    await user.clear(search());
    await user.type(search(), "gpt-6 sol");
    await listed(0, 2);
    await user.click(foldHandle("Codex"));
    expect(modelButton(SOL)).toBeTruthy();
    // The recorded adapter id matches as before.
    await user.clear(search());
    await user.type(search(), "codex");
    await listed(0, 2);
    expect(modelButton(SOL)).toBeTruthy();
  });
});

describe("a level's own preference", () => {
  it("clears one buddy's preference with 不设 and sends mode null", async () => {
    const f = fixture({ preferences: [{ profileId: mediumId, mode: "prefer", reason: "常用" }] });
    const user = userEvent.setup();
    await openBuddy(f.api);
    await selectModel(user, SONNET, "Claude Code");
    expect(within(pane()).queryByRole("radiogroup", { name: "默认偏好" })).toBeNull();
    expect(chip(SONNET, "medium").textContent).toBe("medium优先");
    expect(within(levelPreference("medium")).getByRole("radio", { name: "优先" }).getAttribute("aria-checked")).toBe("true");
    await choosePreference(user, "medium", "不设");
    expect(chip(SONNET, "medium").textContent).toBe("medium");
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(f.published.at(-1)!.preferenceChanges).toEqual([{ profileId: mediumId, mode: null, reason: "" }]);
    expect(f.published.at(-1)).not.toHaveProperty("familyPreferenceChanges");
  });

  it("writes enabled preferences as words and gives them the same styling", async () => {
    const f = fixture({ preferences: [
      { profileId: highId, mode: "prefer", reason: "" },
      { profileId: solMediumId, mode: "pin", reason: "" },
      { profileId: solLowId, mode: "exclude", reason: "" },
    ] });
    const user = userEvent.setup();
    await openBuddy(f.api);
    await selectModel(user, SONNET, "Claude Code");
    await user.click(effortCell("high"));
    await user.click(within(level("high")).getByRole("switch"));
    await selectModel(user, SOL, "Codex");
    await user.click(effortCell("medium"));
    await user.click(within(level("medium")).getByRole("switch"));
    await user.click(effortCell("low"));
    await user.click(within(level("low")).getByRole("switch"));
    const chips = [chip(SONNET, "high"), chip(SOL, "medium"), chip(SOL, "low")];
    expect(chips.map(item => item.textContent)).toEqual(["high优先", "medium固定", "low排除"]);
    expect(new Set(chips.map(item => item.className)).size).toBe(1);
    expect(new Set(chips.map(item => item.lastElementChild!.className)).size).toBe(1);
  });

  it("has no detached preference list to survive leaving and returning to the page", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api);
    await selectModel(user, SOL, "Codex");
    await user.click(effortCell("medium"));
    expect(levelPreference("medium")).toBeTruthy();
    expect(screen.queryByRole("listbox")).toBeNull();
    window.location.hash = "#settings";
    await waitFor(() => expect(screen.queryByRole("radiogroup", { name: "medium 的偏好" })).toBeNull());
    window.location.hash = "#buddies/catalog";
    await waitFor(() => expect(screen.queryByRole("region", { name: SOL })).toBeNull());
    expect(screen.queryByRole("listbox")).toBeNull();
  });

});

describe("the settings page", () => {
  it("offers the three theme choices and the storage panel without any buddy settings", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    const themes = screen.getByRole("radiogroup", { name: "主题" });
    const settings = screen.getByRole("heading", { name: "显示" }).closest(".settings-panel")!;
    expect(settings.classList.contains("panel")).toBe(true);
    expect(screen.getByRole("region", { name: "存储" }).classList.contains("panel")).toBe(true);
    expect(within(themes).getAllByRole("radio").map(radio => radio.closest("label")!.textContent))
      .toEqual(["浅色", "深色", "跟随系统"]);
    const storage = screen.getByRole("region", { name: "存储" });
    expect(within(storage).getByRole("heading", { name: "存储" })).toBeTruthy();
    const overview = within(storage).getByRole("group", { name: "占用概览" });
    expect(overview.textContent).toContain("总占用尚未检查");
    expect(overview.textContent).toContain("可回收尚未检查");
    // The panel keeps its explicit check: mounting the page calls nothing.
    expect(f.command).not.toHaveBeenCalled();
    await user.click(within(storage).getByRole("button", { name: "检查占用" }));
    await waitFor(() => expect(f.operations).toEqual(["storage_plan"]));
    expect(within(storage).getByRole("table")).toBeTruthy();
    expect(overview.textContent).toContain("总占用977 KB");
    expect(overview.textContent).toContain("可回收977 KB");
    // No buddy settings leak into the system settings page.
    expect(screen.queryByRole("checkbox", { name: "只看已启用" })).toBeNull();
    expect(screen.queryByRole("table", { name: "模型" })).toBeNull();
  });
});
