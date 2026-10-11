import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApi, HarnessHealth } from "../api";
import type { Profile, Snapshot } from "../types";
import { level, listed, modelButton, note, pane, queryModel, search, selectModel } from "./page.testing";

/**
 * The Buddy page's own navigation: two sections, 目录 and 角色, and inside the
 * catalogue its two tables, 模型 and Harness. Every view has an address; deep
 * links, hashchange and the browser's back action land in the same state, and a
 * deep link cuts through search and filters. A mark on a section or a table says
 * where something waits for the user. Geometry is checked in a real browser.
 */

const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { adapter: "codex", provider: "openai", model: "gpt-6-sol" };
const flash = { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
const glm = { adapter: "zcode", provider: "zhipu", model: "glm-5" };

const mediumId = "claude:anthropic:claude-sonnet-5:medium";
const highId = "claude:anthropic:claude-sonnet-5:high";
const solMediumId = "codex:openai:gpt-6-sol:medium";
const flashMaxId = "dsh:deepseek-official:deepseek-flash:max";
const glmId = "zcode:zhipu:glm-5:default";
const CHECKED_AT = "2026-09-29T09:30:00.000Z";
const SONNET = "claude / anthropic / claude-sonnet-5";
const SOL = "codex / openai / gpt-6-sol";
const GLM = "zcode / zhipu / glm-5";
const modelsRegion = () => screen.queryByRole("region", { name: "模型" });

function profile(entry: Partial<Profile> & Pick<Profile, "profileId" | "label" | "adapter" | "provider" | "model" | "effort">): Profile {
  return {
    available: true, enabled: false, capabilities: [], contextWindow: null,
    description: "", source: "catalog:fixture", ...entry,
  };
}

/** One enabled family per harness plus an offline family hidden by default. */
function profiles(): Profile[] {
  return [
    profile({ profileId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium",
      enabled: true, capabilities: ["execution:claude", "routing:fast", "decision"], contextWindow: 200_000 }),
    profile({ profileId: highId, label: "Claude Sonnet 5 · high", ...sonnet, effort: "high",
      capabilities: ["execution:claude", "decision"], contextWindow: 200_000 }),
    profile({ profileId: solMediumId, label: "GPT-6 Sol · medium", ...sol, effort: "medium",
      enabled: true, capabilities: ["execution:codex"] }),
    profile({ profileId: flashMaxId, label: "DeepSeek Flash · max", ...flash, effort: "max",
      enabled: true, capabilities: ["execution:dsh", "routing:fast"] }),
    profile({ profileId: glmId, label: "GLM-5 · default", ...glm, effort: "default", available: false,
      unavailableReason: "本机未检测到 zcode CLI" }),
  ];
}

/** Codex lacks local review eligibility; DSH carries a near-limit quota window. */
function harnesses(): HarnessHealth[] {
  return [
    { adapter: "claude", status: "ready", available: true, revision: 2, manualPath: null,
      executable: "/usr/local/bin/claude", version: "2.0.1", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [] },
    { adapter: "codex", status: "ready", available: true, revision: 2, manualPath: null,
      executable: "/opt/bin/codex", version: "0.159.0", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [],
      readOnlyStructured: { implemented: true, eligible: false, systemSandbox: true, sameAttemptContinuation: false, reasonCode: "READ_ONLY_RESOURCE_UNAVAILABLE", reason: "本地只读运行资源不可用" } },
    { adapter: "dsh", status: "ready", available: true, revision: 3, manualPath: null,
      executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [],
      quota: { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", provider: "deepseek-official",
        stale: false, windows: [
          { name: "5h", usedPercent: 93, resetsAt: "2026-09-29T13:00:00.000Z" },
          { name: "weekly", usedPercent: 41, resetsAt: null },
        ] } },
    { adapter: "zcode", status: "missing", available: false, revision: 1, manualPath: null,
      reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI，或填写可执行文件的绝对路径。",
      checkedAt: CHECKED_AT, candidates: [] },
  ];
}

type SnapshotWithHarnesses = Snapshot & { harnesses: HarnessHealth[] };

function snapshot(routerProfileId: string | null = solMediumId): SnapshotWithHarnesses {
  const base: Snapshot = {
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: routerProfileId ? [routerProfileId] : [], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "review" as const, routingBudget: "standard" },
    profiles: profiles(),
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [],
    cards: [], evidence: [], decisions: [], sampleCounts: {},
    modelConcurrency: [
      { ...sonnet, limit: 2, active: 0 }, { ...sol, limit: 2, active: 0 },
      { ...flash, limit: 2, active: 0 }, { ...glm, limit: 2, active: 0 },
    ],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  return { ...base, harnesses: harnesses() };
}

function fixture(routerProfileId: string | null = solMediumId, extra: Partial<Snapshot> = {}) {
  let state = { ...snapshot(routerProfileId), ...extra };
  const command = vi.fn(async (operation: string, _params: Record<string, any>, _csrfToken?: string) => {
    if (operation === "model_profiles") {
      // Only the checked 显示不可用 asks for this page; the fixture keeps
      // every recorded row on one bounded page.
      return { profiles: state.profiles, cards: state.cards, familyAnnotations: state.familyAnnotations,
        preferences: state.preferences, preferenceOverrides: state.preferenceOverrides,
        familyPreferences: state.familyPreferences, sampleCounts: state.sampleCounts,
        modelConcurrency: state.modelConcurrency, tableRevision: state.tableRevision, nextCursor: null };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command };
}

function renderBuddy(hash: string, f: ReturnType<typeof fixture>) {
  window.location.hash = hash;
  render(<App suppliedApi={f.api} />);
}

const sectionsNav = () => screen.getByRole("navigation", { name: "Buddy 的分区" });
const tablesNav = () => screen.getByRole("navigation", { name: "目录的内容" });
const sectionLink = (name: string | RegExp) => within(sectionsNav()).getByRole("link", { name });
const tableLink = (name: string | RegExp) => within(tablesNav()).getByRole("link", { name });
/** The labels of the links marked as the current one, without the words a mark adds for a screen reader. */
const current = (nav: HTMLElement) => within(nav).getAllByRole("link")
  .filter(link => link.getAttribute("aria-current") === "page").map(link => link.firstChild?.textContent);

afterEach(() => {
  cleanup();
  window.location.hash = "";
  sessionStorage.clear();
  document.documentElement.dataset.theme = "";
  Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
});

describe("the Buddy page's sections and tables", () => {
  it("opens the models table by default and moves between sections and tables", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(current(sectionsNav())).toEqual(["目录"]);
    expect(current(tablesNav())).toEqual(["模型"]);
    expect(modelsRegion()).toBeTruthy();
    expect(screen.queryByRole("region", { name: "角色" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Harness" })).toBeNull();

    await user.click(sectionLink(/^角色/));
    expect(window.location.hash).toBe("#buddies/roles");
    await waitFor(() => expect(screen.getByRole("region", { name: "角色" })).toBeTruthy());
    expect(modelsRegion()).toBeNull();
    expect(current(sectionsNav())).toEqual(["角色"]);
    // The two tables belong to the catalogue; the roles section does not offer them.
    expect(screen.queryByRole("navigation", { name: "目录的内容" })).toBeNull();
    expect(screen.getByRole("region", { name: "角色" })).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "审阅预算" })).toBeTruthy();

    await user.click(sectionLink(/^目录/));
    expect(window.location.hash).toBe("#buddies/models");
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(current(sectionsNav())).toEqual(["目录"]);

    await user.click(tableLink(/^Harness/));
    expect(window.location.hash).toBe("#buddies/harness");
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness" })).toBeTruthy());
    expect(modelsRegion()).toBeNull();
    expect(current(sectionsNav())).toEqual(["目录"]);
    expect(current(tablesNav())).toEqual(["Harness"]);

    await user.click(tableLink("模型"));
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Harness" })).toBeNull();
    expect(current(tablesNav())).toEqual(["模型"]);
  });

  it("keeps the selected model, the search and the draft across sections and tables", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    await user.type(search(), "gpt");
    await listed(1, 1);
    await selectModel(user, SOL, "Codex");
    await user.type(note(), "分区间保留");
    expect(screen.getByText("1 项未保存")).toBeTruthy();

    await user.click(sectionLink(/^角色/));
    await waitFor(() => expect(screen.getByRole("region", { name: "角色" })).toBeTruthy());
    await user.click(sectionLink(/^目录/));
    await user.click(tableLink(/^Harness/));
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness" })).toBeTruthy());

    await user.click(tableLink("模型"));
    expect(search()).toHaveProperty("value", "gpt");
    await listed(1, 1);
    expect(pane(SOL)).toBeTruthy();
    expect(note()).toHaveProperty("value", "分区间保留");
    expect(screen.getByText("1 项未保存")).toBeTruthy();
  });
});

describe("marks for what waits", () => {
  it("marks the roles section and the harness table from every view, and the Buddy tab of the top bar", async () => {
    // The fixture's Router cannot review, and DSH's quota is at 93%.
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(sectionLink(/^角色\s*Router 不可用$/)).toBeTruthy();
    expect(tableLink(/^Harness\s*额度接近上限：DSH$/)).toBeTruthy();
    // Inside the catalogue the mark sits on the table, so the section itself carries none.
    expect(sectionLink("目录")).toBeTruthy();
    const topBar = screen.getByRole("navigation", { name: "主要导航" });
    expect(within(topBar).getByRole("link", { name: /^Buddy\s*Router 不可用$/ })).toBeTruthy();

    await user.click(tableLink(/^Harness/));
    expect(sectionLink(/^角色\s*Router 不可用$/)).toBeTruthy();
    expect(tableLink(/^Harness\s*额度接近上限：DSH$/)).toBeTruthy();

    // Outside the catalogue the table is not on screen, and the section carries its mark.
    await user.click(sectionLink(/^角色/));
    expect(sectionLink(/^目录\s*额度接近上限：DSH$/)).toBeTruthy();
    expect(sectionLink(/^角色\s*Router 不可用$/)).toBeTruthy();
  });

  it("keeps the mark while the service reports no current Router, even when the head of the list could work", async () => {
    const f = fixture(mediumId, { routingHealth: {
      available: false, currentRouterProfileId: null, reasonCode: "router-unavailable",
      windowSize: 20, sampleCount: 2, failureCount: 2, consecutiveFailures: 2,
      abstentionCount: 0, cancelledCount: 0, staleCount: 0,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    } });
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(sectionLink(/^角色\s*Router 不可用$/)).toBeTruthy();
  });

  it("carries no mark on the roles section once a working Router holds the role", async () => {
    const f = fixture(mediumId);
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(sectionLink("角色")).toBeTruthy();
  });
});

describe("deep links", () => {
  it("opens and focuses the encoded profile from #buddies/models/<profileId>", async () => {
    const f = fixture();
    renderBuddy(`#buddies/models/${encodeURIComponent(mediumId)}`, f);
    await listed(3, 4);
    expect(modelsRegion()).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Harness" })).toBeNull();
    // The model's row is selected, so the buddy is on screen in the side column and focused.
    await waitFor(() => pane(SONNET));
    expect(modelButton(SONNET).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(level("medium")));
    expect(current(tablesNav())).toEqual(["模型"]);
  });

  it("focuses the named Router line from #buddies/roles/current", async () => {
    const f = fixture();
    renderBuddy("#buddies/roles/current", f);
    await screen.findByRole("region", { name: "角色" });
    expect(screen.getByRole("region", { name: "角色" })).toBeTruthy();
    expect(modelsRegion()).toBeNull();
    const line = document.getElementById("router-current")!;
    expect(line.textContent).toContain("Router");
    await waitFor(() => expect(document.activeElement).toBe(line));
  });

  it("opens the models table for an address that names no view of the page", async () => {
    const f = fixture();
    renderBuddy("#buddies/router", f);
    await listed(3, 4);
    expect(current(sectionsNav())).toEqual(["目录"]);
    expect(current(tablesNav())).toEqual(["模型"]);
  });

  it("follows hashchange and the browser back action between views", async () => {
    const f = fixture();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    await act(async () => { window.location.hash = "#buddies/harness"; });
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness" })).toBeTruthy());
    expect(current(tablesNav())).toEqual(["Harness"]);
    await act(async () => { window.history.back(); });
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Harness" })).toBeNull();
    expect(current(tablesNav())).toEqual(["模型"]);
  });

  it("goes from the roles section to the Router's model and focuses that buddy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies/roles", f);
    await screen.findByRole("region", { name: "角色" });
    const reviewLine = document.getElementById("router-current")!;
    await user.click(within(reviewLine).getByRole("button", { name: "查看所在家族" }));
    expect(window.location.hash).toBe(`#buddies/models/${encodeURIComponent(solMediumId)}`);
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    await waitFor(() => pane(SOL));
    await waitFor(() => expect(document.activeElement).toBe(level("medium")));
  });

  it("cuts through search and the enabled filter when an address names a buddy they hide", async () => {
    const f = fixture(highId);
    const user = userEvent.setup();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    await user.type(search(), "gpt");
    await listed(1, 1);
    expect(queryModel(SONNET)).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));

    await act(async () => { window.location.hash = `#buddies/models/${encodeURIComponent(highId)}`; });
    await waitFor(() => expect(search()).toHaveProperty("value", ""));
    expect(screen.getByRole("checkbox", { name: "只看已启用" }).getAttribute("aria-checked")).toBe("false");
    await listed(3, 4);
    await waitFor(() => pane(SONNET));
    await waitFor(() => expect(document.activeElement).toBe(level("high")));
    // Following an address is a local change of view: the snapshot already holds the model.
    expect(f.command).not.toHaveBeenCalled();
  });

  it("turns on the unavailable filter and unfolds the harness when an address names an offline buddy", async () => {
    const f = fixture();
    renderBuddy(`#buddies/models/${encodeURIComponent(glmId)}`, f);
    const box = await screen.findByRole("checkbox", { name: "显示不可用 1" });
    await waitFor(() => expect(box.getAttribute("aria-checked")).toBe("true"));
    await listed(3, 5);
    // A harness that cannot work starts folded; the address opens it.
    expect(modelButton(GLM).getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(level("default")));
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("a narrow viewport", () => {
  it("keeps the same navigation usable and the draft intact at a 390px width", async () => {
    const f = fixture();
    const user = userEvent.setup();
    Object.defineProperty(window, "innerWidth", { value: 390, configurable: true });
    renderBuddy("#buddies", f);
    await listed(3, 4);
    await selectModel(user, SONNET, "Claude Code");
    await user.type(note(), "窄屏草稿");
    expect(within(sectionsNav()).getAllByRole("link").map(link => link.firstChild?.textContent)).toEqual(["目录", "角色"]);
    expect(within(tablesNav()).getAllByRole("link").map(link => link.firstChild?.textContent)).toEqual(["模型", "Harness"]);
    await user.click(tableLink(/^Harness/));
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness" })).toBeTruthy());
    await user.click(tableLink("模型"));
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(pane(SONNET)).toBeTruthy();
    expect(note()).toHaveProperty("value", "窄屏草稿");
    // Once a row is opened, the side column offers the way back to the table.
    expect(within(pane()).getByRole("button", { name: "模型" })).toBeTruthy();
    expect(screen.getByText("1 项未保存")).toBeTruthy();
  });
});
