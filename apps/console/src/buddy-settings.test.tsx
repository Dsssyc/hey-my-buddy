import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { PREFERENCE_ICON } from "./buddy-display";
import type { ConsoleApi, HarnessHealth } from "./api";
import type {
  FamilyPreference,
  Preference,
  PreferenceOverride,
  Profile,
  Snapshot,
  WriterGrant,
} from "./types";

/**
 * The Buddy settings page tests the requirements docs/design/buddy-settings.md
 * names explicitly: the enabled-only filter, the harness grouping with a
 * collapsed unavailable harness, effort overrides (including an explicit
 * "无偏好" and a return to "跟随家族"), the non-colour preference marker, the
 * Router refusal reason, and the storage panel on the settings page.
 */

const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { adapter: "codex", provider: "openai", model: "gpt-6-sol" };
const glm = { adapter: "zcode", provider: "zhipu", model: "glm-5" };

const mediumId = "claude:anthropic:claude-sonnet-5:medium";
const highId = "claude:anthropic:claude-sonnet-5:high";
const solMediumId = "codex:openai:gpt-6-sol:medium";
const solLowId = "codex:openai:gpt-6-sol:low";
const glmId = "zcode:zhipu:glm-5:default";

function profile(entry: Partial<Profile> & Pick<Profile, "profileId" | "label" | "adapter" | "provider" | "model" | "effort">): Profile {
  return {
    available: true, enabled: false, capabilities: [], contextWindow: null,
    description: "", source: "catalog:fixture", ...entry,
  };
}

/** Three harnesses: one available family, one all-disabled family, one offline family. */
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

/** The schema 13 `effective_preferences` view, mirrored independently of draft.ts. */
function effective(state: Snapshot): Preference[] {
  const overrides = new Map(state.preferenceOverrides.map(entry => [entry.profileId, entry]));
  const families = new Map(state.familyPreferences.map(entry => [JSON.stringify([entry.adapter, entry.provider, entry.model]), entry]));
  return state.profiles.flatMap((entry): Preference[] => {
    const override = overrides.get(entry.profileId);
    if (override) {
      return override.mode === "none"
        ? []
        : [{ profileId: entry.profileId, mode: override.mode, reason: override.reason, source: "override" as const }];
    }
    const family = families.get(JSON.stringify([entry.adapter, entry.provider, entry.model]));
    return family
      ? [{ profileId: entry.profileId, mode: family.mode, reason: family.reason, source: "family" as const }]
      : [];
  });
}

type Options = { configurationUnavailable?: boolean; routerProfileId?: string | null; familyPreferences?: FamilyPreference[]; preferenceOverrides?: PreferenceOverride[] };

/**
 * The snapshot's harness health (ADR-017 §15): the Claude harness used by the
 * available family is ready, and the ZCode harness behind the unavailable
 * family is recorded as missing with an attempted location and a remedy.
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
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: options.configurationUnavailable ? null : { revision: 1, routerProfileId: options.routerProfileId === undefined ? mediumId : options.routerProfileId, defaultRoutingMode: "review" as const, routingBudget: "standard" },
    configurationError: options.configurationUnavailable ? { code: "ROUTER_SETTINGS_UPGRADE_REQUIRED", message: "请先升级设置", revision: 1 } : null,
    profiles: profiles(),
    preferences: [],
    familyPreferences: options.familyPreferences ?? [],
    preferenceOverrides: options.preferenceOverrides ?? [],
    cards: [],
    familyAnnotations: [],
    evidence: [],
    decisions: [],
    sampleCounts: {},
    modelConcurrency: [
      { ...sonnet, limit: 2, active: 0 },
      { ...sol, limit: 2, active: 0 },
      { ...glm, limit: 2, active: 0 },
    ],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  return { ...base, preferences: effective(base), harnesses: harnesses() };
}

function applyPublication(state: HarnessSnapshot, params: Record<string, any>): HarnessSnapshot {
  const next: HarnessSnapshot = {
    ...state,
    tableRevision: state.tableRevision + 1,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    familyPreferences: [...state.familyPreferences],
    preferenceOverrides: [...state.preferenceOverrides],
    familyAnnotations: [...state.familyAnnotations],
    configuration: state.configuration ? { ...state.configuration, ...(params.configuration ?? {}) } : null,
  };
  for (const setting of params.profileSettings ?? []) {
    next.profiles = next.profiles.map(entry => entry.profileId === setting.profileId ? { ...entry, enabled: setting.enabled } : entry);
  }
  const key = (entry: { adapter: string; provider: string; model: string }) => JSON.stringify([entry.adapter, entry.provider, entry.model]);
  for (const change of params.familyPreferenceChanges ?? []) {
    next.familyPreferences = next.familyPreferences.filter(entry => key(entry) !== key(change));
    if (change.mode !== null) {
      next.familyPreferences.push({ adapter: change.adapter, provider: change.provider, model: change.model, mode: change.mode, reason: change.reason });
    }
  }
  for (const change of params.preferenceChanges ?? []) {
    next.preferenceOverrides = next.preferenceOverrides.filter(entry => entry.profileId !== change.profileId);
    if (change.mode !== null) {
      next.preferenceOverrides.push({ profileId: change.profileId, mode: change.mode, reason: change.reason });
    }
  }
  for (const change of params.familyAnnotationChanges ?? []) {
    next.familyAnnotations = next.familyAnnotations.filter(entry => key(entry) !== key(change));
    next.familyAnnotations.push({ ...change, revision: 1, updatedAt: null });
  }
  return { ...next, preferences: effective(next) };
}

function fixture(options: Options = {}) {
  let state = snapshot(options);
  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing",
    state: "active", tableRevision: 4, expiresAt: new Date(Date.now() + 120_000).toISOString() };
  const command = vi.fn(async (operation: string, params: Record<string, any>, _csrfToken?: string) => {
    operations.push(operation);
    if (operation === "model_profiles") {
      // Only a checked 显示不可用配置 asks for this page; it mirrors the board's
      // bounded contract and just returns the current rows.
      return { profiles: state.profiles, cards: state.cards, familyAnnotations: state.familyAnnotations,
        preferences: state.preferences, preferenceOverrides: state.preferenceOverrides,
        familyPreferences: state.familyPreferences, sampleCounts: state.sampleCounts,
        modelConcurrency: state.modelConcurrency, tableRevision: state.tableRevision, nextCursor: null };
    }
    if (operation === "evaluation_write_begin") {
      state = { ...state, gate: { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } } };
      return grant;
    }
    if (operation === "user_policy_publish") {
      published.push(params);
      state = applyPublication(state, params);
      return { published: true, tableRevision: state.tableRevision };
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
  } as unknown as ConsoleApi;
  return { api, command, operations, published };
}

async function openBuddy(api: ConsoleApi, user: ReturnType<typeof userEvent.setup>) {
  window.location.hash = "#buddy";
  render(<App suppliedApi={api} />);
  // The unavailable ZCode family is hidden by default, so two families show.
  await screen.findByRole("heading", { name: "模型 2" });
}

function familyRow(name: string) {
  return screen.getByRole("button", { name: new RegExp(`^${name}`) });
}

async function selectFamily(user: ReturnType<typeof userEvent.setup>, name: string) {
  if (name === "GPT-6 Sol" && !screen.queryByRole("button", { name: /^GPT-6 Sol/ })) {
    await user.click(screen.getByRole("button", { name: /^▸ Codex/ }));
  }
  await user.click(familyRow(name));
}

const saveButton = () => within(screen.getByRole("region", { name: "未保存的修改" })).getByRole("button", { name: /^(保存|重试同一保存)$/ });

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("the model family list", () => {
  it("filters to families with an enabled effort and restores the full list", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    // Families without an enabled effort start folded; the filter still counts them.
    expect(familyRow("Claude Sonnet 5")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^▸ Codex/ }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: /^GPT-6 Sol/ })).toBeNull();
    // The all-unavailable ZCode harness is hidden until the user asks for it,
    // and the checkbox names how many configurations that hides.
    expect(screen.queryByRole("button", { name: /^▸ ZCode/ })).toBeNull();
    expect(screen.getByRole("checkbox", { name: "显示不可用配置（1）" })).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));
    // GPT-6 Sol has no enabled effort; the whole ZCode harness stays unusable.
    await screen.findByRole("heading", { name: "模型 1" });
    expect(screen.queryByRole("button", { name: /^GPT-6 Sol/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^▸ ZCode/ })).toBeNull();
    expect(familyRow("Claude Sonnet 5")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));
    await screen.findByRole("heading", { name: "模型 2" });
    expect(screen.getByRole("button", { name: /^▸ Codex/ })).toBeTruthy();
    // The two filters combine: with 显示不可用配置 on, the offline family is
    // listed, and 只看已启用 still hides it once it has no enabled effort.
    await user.click(screen.getByRole("checkbox", { name: "显示不可用配置（1）" }));
    await screen.findByRole("heading", { name: "模型 3" });
    expect(screen.getByRole("button", { name: /^▸ ZCode（不可用）/ })).toBeTruthy();
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));
    await screen.findByRole("heading", { name: "模型 1" });
    expect(screen.queryByRole("button", { name: /^▸ ZCode/ })).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));
    await screen.findByRole("heading", { name: "模型 3" });
  });

  it("folds harnesses with no enabled efforts, shows enabled and total counts, and preserves the unavailable reason", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用配置（1）" }));
    const claude = screen.getByRole("button", { name: /^▾ Claude Code/ });
    expect(claude.getAttribute("aria-expanded")).toBe("true");
    expect(claude.textContent).toContain("已启用 1/2");
    const codex = screen.getByRole("button", { name: /^▸ Codex/ });
    expect(codex.getAttribute("aria-expanded")).toBe("false");
    expect(codex.textContent).toContain("已启用 0/2");
    expect(screen.queryByRole("button", { name: /^GPT-6 Sol/ })).toBeNull();
    await user.click(codex);
    expect(familyRow("GPT-6 Sol")).toBeTruthy();
    const zcode = screen.getByRole("button", { name: /^▸ ZCode（不可用）/ });
    expect(zcode.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("本机未检测到 zcode CLI")).toBeTruthy();
    // A folded harness hides its families but never drops its explanation.
    expect(screen.queryByRole("button", { name: /^GLM-5/ })).toBeNull();
    await user.click(zcode);
    expect(familyRow("GLM-5")).toBeTruthy();
    // The family row itself carries the recorded reason next to 不可用.
    expect(screen.getByText("不可用 · 本机未检测到 zcode CLI")).toBeTruthy();
  });

  it("counts enabled efforts and marks the Router's family", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    expect(screen.getByRole("button", { name: "Claude Sonnet 5，已启用 1/2，Router" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^▸ Codex/ }));
    expect(screen.getByRole("button", { name: "GPT-6 Sol，已启用 0/2" })).toBeTruthy();
    // The Router mark belongs to the model list; the hidden Router section's
    // own heading is a different element with the same word.
    expect(within(screen.getByRole("region", { name: "模型家族" })).getByText("Router")).toBeTruthy();
  });

  it("opens recorded harness health from the global status without calling anything", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.click(screen.getByRole("link", { name: "Harness 可用 1/2" }));
    const strip = screen.getByRole("region", { name: "Harness 状态" });
    expect(within(strip).getByText("可用 1/2")).toBeTruthy();
    expect(within(strip).getByText(/^找到：路径 \/Users\/fixture\/\.local\/bin\/claude · 版本 2\.0\.1/)).toBeTruthy();
    expect(within(strip).getByText("未找到可执行文件（已尝试 1 处）")).toBeTruthy();
    // The strip is read-only on entry: no command, no draft.
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
  });

  it("matches adapter id, harness display name, provider and model name, case-insensitively", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    const search = screen.getByLabelText("搜索模型");
    // Harness display name ("Claude Code"), not only the `claude` adapter id.
    await user.type(search, "claude code");
    await screen.findByRole("heading", { name: "模型 1" });
    expect(familyRow("Claude Sonnet 5")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^GPT-6 Sol/ })).toBeNull();
    // While the unavailable filter is off, searching stays a local filter.
    expect(f.command).not.toHaveBeenCalled();
    // Provider, uppercase: case does not matter. The offline ZCode family is
    // searched once 显示不可用配置 is on; its own search then also reaches the
    // server so retained rows beyond the snapshot stay findable.
    await user.clear(search);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用配置（1）" }));
    await user.type(search, "ZHIPU");
    await screen.findByRole("heading", { name: "模型 1" });
    expect(screen.getByRole("button", { name: /^▸ ZCode（不可用）/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Claude Sonnet 5/ })).toBeNull();
    // Model display name, lowercase.
    await user.clear(search);
    await user.type(search, "gpt-6 sol");
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("button", { name: /^▸ Codex/ }));
    expect(familyRow("GPT-6 Sol")).toBeTruthy();
    // The recorded adapter id matches as before.
    await user.clear(search);
    await user.type(search, "codex");
    await screen.findByRole("heading", { name: "模型 1" });
    expect(familyRow("GPT-6 Sol")).toBeTruthy();
  });
});

describe("effort tags and preference overrides", () => {
  const familyReason = "用于日常工作的稳定配置";

  it("shows the family default, sets an explicit 无偏好 override and publishes mode none", async () => {
    const f = fixture({ familyPreferences: [{ ...sonnet, mode: "prefer", reason: familyReason }] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "Claude Sonnet 5");
    const tag = screen.getByRole("group", { name: "medium 档位" });
    // The family default colours the tag, and the glyph and border carry the
    // same meaning without colour (see the styles.css check below).
    expect(tag.className).toContain("pref-prefer");
    expect(tag.className).not.toContain("override");
    expect(tag.querySelector(".pref-icon")!.textContent).toBe("▲");
    expect(tag.textContent).toContain("偏好：优先（来自家族）");
    expect(tag.getAttribute("title")).toBe(`优先：${familyReason}`);

    await user.click(within(tag).getByRole("button", { name: "medium 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "Claude Sonnet 5 · medium 档位设置" });
    expect(within(menu).getByRole("radio", { name: "跟随家族（优先）" })).toHaveProperty("checked", true);
    await user.click(within(menu).getByRole("radio", { name: "无偏好" }));

    const overridden = screen.getByRole("group", { name: "medium 档位" });
    expect(overridden.className).toContain("override");
    expect(overridden.className).not.toContain("pref-");
    expect(overridden.querySelector(".pref-icon")).toBeNull();
    expect(overridden.textContent).toContain("偏好：无偏好（档位覆盖）");
    expect(within(screen.getByRole("region", { name: "未保存的修改" })).getByText("1 项未保存")).toBeTruthy();
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(f.published.at(-1)!.preferenceChanges).toEqual([{ profileId: mediumId, mode: "none", reason: "" }]);
    expect(f.published.at(-1)).not.toHaveProperty("annotationChanges");
    expect(f.published.at(-1)).not.toHaveProperty("familyPreferenceChanges");
  });

  it("returns an overridden effort to 跟随家族 and publishes mode null", async () => {
    const f = fixture({
      familyPreferences: [{ ...sonnet, mode: "prefer", reason: familyReason }],
      preferenceOverrides: [{ profileId: mediumId, mode: "exclude", reason: "成本过高" }],
    });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "Claude Sonnet 5");
    const tag = screen.getByRole("group", { name: "medium 档位" });
    // The menu shows that this effort does not follow the family.
    expect(tag.className).toContain("pref-exclude");
    expect(tag.className).toContain("override");
    expect(tag.querySelector(".pref-icon")!.textContent).toBe("⊘");
    expect(tag.textContent).toContain("偏好：排除（档位覆盖）");
    // The other effort still follows the family default.
    const other = screen.getByRole("group", { name: "high 档位" });
    expect(other.className).toContain("pref-prefer");
    expect(other.className).not.toContain("override");

    await user.click(within(tag).getByRole("button", { name: "medium 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "Claude Sonnet 5 · medium 档位设置" });
    expect(within(menu).getByLabelText("覆盖理由")).toHaveProperty("value", "成本过高");
    expect(within(menu).getByRole("radio", { name: "排除" })).toHaveProperty("checked", true);
    await user.click(within(menu).getByRole("radio", { name: "跟随家族（优先）" }));

    const following = screen.getByRole("group", { name: "medium 档位" });
    expect(following.className).toContain("pref-prefer");
    expect(following.className).not.toContain("override");
    expect(following.textContent).toContain("偏好：优先（来自家族）");
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(f.published.at(-1)!.preferenceChanges).toEqual([{ profileId: mediumId, mode: null, reason: "" }]);
    expect(f.published.at(-1)).not.toHaveProperty("annotationChanges");
  });

  it("marks every preference with a glyph and a distinct border, never colour alone", () => {
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    const rule = (selector: string) => {
      const start = styles.indexOf(selector);
      expect(start, `missing ${selector}`).toBeGreaterThan(-1);
      return styles.slice(start, styles.indexOf("}", start));
    };
    // Each effort tag keeps the icon and its own border style/width weight.
    expect(rule(".effort-tag.pref-prefer")).toMatch(/border:\s*2px solid/);
    expect(rule(".effort-tag.pref-pin")).toMatch(/border:\s*3px double/);
    expect(rule(".effort-tag.pref-exclude")).toMatch(/border:\s*2px dashed/);
    // 排除 keeps the dashed border and the ⊘ icon as its non-colour cues; the
    // name is no longer struck through.
    expect(styles).not.toMatch(/\.effort-tag\.pref-exclude \.effort-name \{[^}]*line-through/);
    expect(styles).not.toContain("text-decoration: line-through");
    expect(styles).not.toContain(".effort-tag.override::after");
    // Every mode also has its own glyph, so the tag never depends on colour.
    expect(Object.values(PREFERENCE_ICON).every(icon => icon.length > 0)).toBe(true);
    expect(new Set(Object.values(PREFERENCE_ICON)).size).toBe(3);
  });
});

describe("the Router menu", () => {
  it("assigns the single Router through the enabled effort menu", async () => {
    const f = fixture({ routerProfileId: null });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "Claude Sonnet 5");
    await user.click(screen.getByRole("button", { name: "medium 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "Claude Sonnet 5 · medium 档位设置" });
    await user.click(within(menu).getByRole("button", { name: "设为 Router" }));
    expect(screen.getByRole("region", { name: "全局状态" }).textContent).toContain("Router：可用");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本");
    expect(f.published[0].configuration).toEqual({ routerProfileId: mediumId });
  });
  it("disables only Router setting actions when configuration requires upgrade", async () => {
    const f = fixture({ configurationUnavailable: true });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "Claude Sonnet 5");
    await user.click(screen.getByRole("button", { name: "medium 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "Claude Sonnet 5 · medium 档位设置" });
    expect(within(menu).getByRole("button", { name: "设为 Router" })).toHaveProperty("disabled", true);
    expect(within(menu).getByText("Router 设置升级不可用")).toBeTruthy();
    expect(within(menu).getByRole("radio", { name: "优先" })).toHaveProperty("disabled", false);
    await user.click(within(menu).getByRole("radio", { name: "优先" }));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本");
    expect(f.published[0]).not.toHaveProperty("configuration");
    expect(f.published[0].preferenceChanges).toEqual([{ profileId: mediumId, mode: "prefer", reason: "" }]);
  });
  it("closes an effort menu when its main tab becomes hidden, without reopening on return", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "GPT-6 Sol");
    const trigger = screen.getByRole("button", { name: "medium 档位菜单" });
    await user.click(trigger);
    expect(screen.getByRole("dialog", { name: "GPT-6 Sol · medium 档位设置" })).toBeTruthy();
    // A programmatic navigation has no outside pointer press to dismiss the portal.
    fireEvent.click(screen.getByRole("link", { name: "设置" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "GPT-6 Sol · medium 档位设置" })).toBeNull());
    fireEvent.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    expect(screen.queryByRole("dialog", { name: "GPT-6 Sol · medium 档位设置" })).toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("lays out native radios at their own width and shows current Router as status", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "Claude Sonnet 5");
    await user.click(screen.getByRole("button", { name: "medium 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "Claude Sonnet 5 · medium 档位设置" });
    expect(within(menu).getByText("当前 Router")).toBeTruthy();
    expect(within(menu).queryByRole("button", { name: "设为 Router" })).toBeNull();
    expect(within(menu).getAllByRole("radio")).toHaveLength(5);
    const mediumEvaluation = screen.getByText("medium", { selector: ".effort-evaluation summary strong" }).closest(".effort-evaluation")!;
    const capabilityName = [...mediumEvaluation.querySelectorAll("dt")].find(item => item.textContent === "能力")!;
    const capabilityText = capabilityName.nextElementSibling!.textContent!;
    expect(capabilityText).toContain("支持无工具路由调用");
    expect(capabilityText).toContain("具备本地只读路由资格");
    expect(capabilityText).not.toMatch(/routing:fast|\bdecision\b/);
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    expect(styles).toMatch(/\.effort-menu \.menu-radio input\[type="radio"\] \{[^}]*width: auto/);
    expect(styles).toMatch(/\.menu-radio \{[^}]*white-space: nowrap/);
  });
  it("disables 设为 Router without the decision capability and writes the reason", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "GPT-6 Sol");
    await user.click(screen.getByRole("button", { name: "medium 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "GPT-6 Sol · medium 档位设置" });
    const action = within(menu).getByRole("button", { name: "设为 Router" });
    expect((action as HTMLButtonElement).disabled).toBe(true);
    expect(within(menu).getByText("所属 Harness 尚不具备本地只读路由资格")).toBeTruthy();
    await user.click(action);
    action.focus();
    expect(document.activeElement).not.toBe(action);
    expect(screen.queryByRole("dialog", { name: "替换 Router" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "GPT-6 Sol · medium 档位设置" })).toBeTruthy();
    expect(f.published).toHaveLength(0);
  });

  it("disables 设为 Router for a disabled effort and names the switch to turn on", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await selectFamily(user, "Claude Sonnet 5");
    await user.click(screen.getByRole("button", { name: "high 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: "Claude Sonnet 5 · high 档位设置" });
    const action = within(menu).getByRole("button", { name: "设为 Router" });
    expect((action as HTMLButtonElement).disabled).toBe(true);
    expect(within(menu).getByText("该档位未启用：先打开它的开关")).toBeTruthy();
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
    expect(screen.queryByRole("button", { name: /设为 Router/ })).toBeNull();
  });
});
