import { modelFacts } from "./model-fixtures";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApi, HarnessHealth } from "../api";
import type { Buddy, Snapshot } from "../types";
import { choosePreference, effortCell, level, levelPreference, listed, modelButton, pane, queryModel, search, selectModel } from "./page.testing";
import { modelServiceKey } from "../console-data";

/**
 * The Buddy page's own navigation: 目录, Router and 评价, and inside the
 * catalogue one directory in which a harness, a model or a buddy can be
 * selected. Every selection has an address; deep links, hashchange and the
 * browser's back action land in the same state, and a deep link cuts through
 * search and filters. A mark on a section says where something waits for the
 * user. Geometry is checked in a real browser.
 */

const sonnet = { harness: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { harness: "codex", provider: "openai", model: "gpt-6-sol" };
const flash = { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
const glm = { harness: "zcode", provider: "zhipu", model: "glm-5" };

const mediumId = "claude:anthropic:claude-sonnet-5:medium";
const highId = "claude:anthropic:claude-sonnet-5:high";
const solMediumId = "codex:openai:gpt-6-sol:medium";
const flashMaxId = "dsh:deepseek-official:deepseek-flash:max";
const glmId = "zcode:zhipu:glm-5:default";
const CHECKED_AT = "2026-09-29T09:30:00.000Z";
const SONNET = "claude / anthropic / claude-sonnet-5";
const SOL = "codex / openai / gpt-6-sol";
const GLM = "zcode / zhipu / glm-5";
const modelsRegion = () => screen.queryByRole("region", { name: "目录" });

function buddy(entry: Partial<Buddy> & Pick<Buddy, "buddyId" | "label" | "harness" | "provider" | "model" | "effort">): Buddy {
  return {
    effortRank: null, available: true, enabled: false, capabilities: [], contextWindow: null,
    description: "", source: "catalog:fixture", ...entry,
  };
}

/** One enabled model service per harness plus an offline model service hidden by default. */
function buddies(): Buddy[] {
  return [
    buddy({ effortRank: null, buddyId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium",
      enabled: true, capabilities: ["execution:claude", "routing:fast", "decision"], contextWindow: 200_000 }),
    buddy({ effortRank: null, buddyId: highId, label: "Claude Sonnet 5 · high", ...sonnet, effort: "high",
      capabilities: ["execution:claude", "decision"], contextWindow: 200_000 }),
    buddy({ effortRank: null, buddyId: solMediumId, label: "GPT-6 Sol · medium", ...sol, effort: "medium",
      enabled: true, capabilities: ["execution:codex"] }),
    buddy({ effortRank: null, buddyId: flashMaxId, label: "DeepSeek Flash · max", ...flash, effort: "max",
      enabled: true, capabilities: ["execution:dsh", "routing:fast"] }),
    buddy({ buddyId: glmId, label: "GLM-5 · default", ...glm, effort: "default", available: false,
      unavailableReason: { code: "harness.not-found", params: {} } }),
  ];
}

/** Codex lacks local review eligibility; DSH carries a provider cooldown. */
function harnesses(): HarnessHealth[] {
  return [
    { harness: "claude", status: "ready", revision: 2, manualPath: null, permissionsVerifiedVersion: null,
      executable: "/usr/local/bin/claude", version: "2.0.1", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [] },
    { harness: "codex", status: "ready", revision: 2, manualPath: null, permissionsVerifiedVersion: null,
      executable: "/opt/bin/codex", version: "0.159.0", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [],
      permissions: { write: "enforced" } },
    { harness: "dsh", status: "ready", revision: 3, manualPath: null, permissionsVerifiedVersion: null,
      executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [],
      providers: [{ provider: "deepseek-official", coolingUntil: new Date(Date.now() + 3600000).toISOString(), consecutiveRejections: 1 }] },
    { harness: "zcode", status: "missing", revision: 1, manualPath: null, permissionsVerifiedVersion: null,
      reason: { code: "harness.not-found", params: { operation: "probe" } },
      checkedAt: CHECKED_AT, candidates: [] },
  ];
}

type SnapshotWithHarnesses = Snapshot & { harnesses: HarnessHealth[] };

function snapshot(routerBuddyId: string | null = solMediumId): SnapshotWithHarnesses {
  const base: Snapshot = {
    csrfToken: "csrf", runtimeIdentity: "fixture-runtime", eventCursor: 0,
    consoleSession: { id: "fixture-session" },

    configuration: { explorationEvery: 5, routerBuddyIds: routerBuddyId ? [routerBuddyId] : [], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "review" as const, modelResponseWaitSeconds: 60, profileBuddyId: null },
    buddies: buddies(),
    preferences: [], models: [], modelCorrespondences: [],
    decisions: [],
    modelServiceConcurrency: [
      { ...sonnet, limit: 2, active: 0 }, { ...sol, limit: 2, active: 0 },
      { ...flash, limit: 2, active: 0 }, { ...glm, limit: 2, active: 0 },
    ],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };
  return { ...base, ...modelFacts(base.buddies), harnesses: harnesses() };
}

function fixture(routerBuddyId: string | null = solMediumId, extra: Partial<Snapshot> = {}) {
  let state = { ...snapshot(routerBuddyId), ...extra };
  const command = vi.fn(async (operation: string, _params: Record<string, any>, _csrfToken?: string) => {
    if (operation === "buddy_list") {
      // Only the checked 显示不可用 asks for this page; the fixture keeps
      // every recorded row on one bounded page.
      return { buddies: state.buddies, models: state.models,
        modelCorrespondences: state.modelCorrespondences,
        preferences: state.preferences,
        modelServiceConcurrency: state.modelServiceConcurrency, nextCursor: null };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ groups: [], totals: { all: 0, active: 0, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command };
}

function renderBuddy(hash: string, f: ReturnType<typeof fixture>) {
  window.location.hash = hash;
  render(<App suppliedApi={f.api} />);
}

const sectionsNav = () => screen.getByRole("navigation", { name: "Buddy 的分区" });
const sectionLink = (name: string | RegExp) => within(sectionsNav()).getByRole("link", { name });
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
  it("keeps one header, whole-directory counts and keyboard focus across filters and sections", async () => {
    const f = fixture(); const user = userEvent.setup();
    renderBuddy("#buddies/catalog", f); await listed(3, 4);
    const navigation = sectionsNav();
    const catalogLink = sectionLink(/^目录/);
    const routerLink = sectionLink(/^Router/);
    const evaluationLink = sectionLink("评价");
    expect(within(navigation).getAllByRole("link")).toHaveLength(3);
    const summary = document.querySelector('[data-slot="buddy-header"] [role="status"]')!;
    expect(summary.textContent).toBe("4 个 harness · 启用 3 / 5");

    await user.type(search(), "gpt");
    await listed(1, 1);
    await user.click(screen.getByRole("checkbox", { name: "仅显示已启用" }));
    expect(summary.textContent).toBe("4 个 harness · 启用 3 / 5");

    routerLink.focus();
    await user.keyboard("{Enter}");
    expect(window.location.hash).toBe("#buddies/router");
    expect(document.activeElement).toBe(routerLink);
    expect(sectionsNav()).toBe(navigation);
    expect(sectionLink(/^目录/)).toBe(catalogLink);
    expect(sectionLink(/^Router/)).toBe(routerLink);
    expect(summary.textContent).toBe("4 个 harness · 启用 3 / 5");

    evaluationLink.focus();
    await user.keyboard("{Enter}");
    expect(window.location.hash).toBe("#buddies/evaluation");
    expect(document.activeElement).toBe(evaluationLink);
    expect(sectionsNav()).toBe(navigation);
    expect(summary.textContent).toBe("4 个 harness · 启用 3 / 5");

    catalogLink.focus();
    await user.keyboard("{Enter}");
    expect(window.location.hash).toBe("#buddies/catalog");
    expect(document.activeElement).toBe(catalogLink);
    expect(sectionsNav()).toBe(navigation);
    expect(summary.textContent).toBe("4 个 harness · 启用 3 / 5");
  });

  it("opens one directory by default and moves among directory, Router and evaluation", async () => {
    const f = fixture(); const user = userEvent.setup();
    renderBuddy("#buddies", f); await listed(3, 4);
    expect(current(sectionsNav())).toEqual(["目录"]);
    expect(modelsRegion()).toBeTruthy();
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    expect(document.querySelectorAll("[data-slot=harness-sidebar] [data-slot=harness-details]")).toHaveLength(4);
    expect(screen.queryByRole("navigation", { name: "目录的内容" })).toBeNull();
    await user.click(sectionLink(/^Router/));
    expect(window.location.hash).toBe("#buddies/router");
    await screen.findByRole("region", { name: "Router" });
    expect(modelsRegion()).toBeNull(); expect(current(sectionsNav())).toEqual(["Router"]);
    expect(screen.getByRole("spinbutton", { name: "开始作答时限" })).toBeTruthy();
    await user.click(sectionLink("评价"));
    expect(window.location.hash).toBe("#buddies/evaluation");
    expect(current(sectionsNav())).toEqual(["评价"]);
    expect(screen.getByRole("navigation", { name: "评价列表" })).toBeTruthy();
    await user.click(sectionLink(/^目录/));
    expect(window.location.hash).toBe("#buddies/catalog");
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(current(sectionsNav())).toEqual(["目录"]);
  });

  it("keeps the selected model, the search and the draft across sections", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    await user.type(search(), "gpt");
    await listed(1, 1);
    await selectModel(user, SOL, "Codex");
    await choosePreference(user, "medium", "优先");
    expect(screen.getByText("1 项未保存")).toBeTruthy();

    await user.click(sectionLink(/^Router/));
    await waitFor(() => expect(screen.getByRole("region", { name: "Router" })).toBeTruthy());
    await user.click(sectionLink(/^目录/));
    expect(search()).toHaveProperty("value", "gpt");
    await listed(1, 1);
    expect(pane(SOL)).toBeTruthy();
    expect(within(levelPreference("medium")).getByRole("radio", { name: "优先" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("1 项未保存")).toBeTruthy();
  });
});

describe("marks for what waits", () => {
  it("marks the roles and directory sections, and the Buddy tab of the top bar", async () => {
    // The Router cannot review; provider cooling is an ordinary wait.
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(sectionLink(/^Router\s*Router 不可用$/)).toBeTruthy();
    expect(sectionLink(/^目录$/)).toBeTruthy();
    // Inside the catalogue the mark sits on the table, so the section itself carries none.
    expect(sectionLink(/^目录/)).toBeTruthy();
    const topBar = screen.getByRole("navigation", { name: "主要导航" });
    expect(within(topBar).getByRole("link", { name: /^Buddy\s*Router 不可用$/ })).toBeTruthy();

    // Outside the catalogue the table is not on screen, and the section carries its mark.
    await user.click(sectionLink(/^Router/));
    expect(sectionLink(/^目录$/)).toBeTruthy();
    expect(sectionLink(/^Router\s*Router 不可用$/)).toBeTruthy();
  });

  it("keeps the mark while the service reports no current Router, even when the head of the list could work", async () => {
    const f = fixture(mediumId, { routingHealth: {
      available: false, currentRouterBuddyId: null, reason: { code: "router-unavailable", params: {} },
      windowSize: 20, sampleCount: 2, failureCount: 2, consecutiveFailures: 2,
      abstentionCount: 0, cancelledCount: 0,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    } });
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(sectionLink(/^Router\s*Router 不可用$/)).toBeTruthy();
  });

  it("carries no mark on the Router section once a working Router holds the role", async () => {
    const f = fixture(mediumId);
    renderBuddy("#buddies", f);
    await listed(3, 4);
    expect(sectionLink("Router")).toBeTruthy();
  });
});

describe("deep links", () => {
  it("opens and focuses the encoded buddy from #buddies/catalog/buddy/<buddyId>", async () => {
    const f = fixture();
    renderBuddy(`#buddies/catalog/buddy/${encodeURIComponent(mediumId)}`, f);
    await listed(3, 4);
    expect(modelsRegion()).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Harness" })).toBeNull();
    // The model's row is selected, so the buddy is on screen in the side column and focused.
    await waitFor(() => pane(SONNET));
    expect(modelButton(SONNET).getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(level("medium")));
  });

  it("opens a whole model from #buddies/catalog/model/<key> on its first enabled effort, and a click on a row writes that address", async () => {
    const user = userEvent.setup();
    const f = fixture();
    const address = (index: number) => `#buddies/catalog/model/${encodeURIComponent(modelServiceKey(buddies()[index]))}`;
    renderBuddy(address(0), f);
    await listed(3, 4);
    await waitFor(() => pane(SONNET));
    expect(modelButton(SONNET).getAttribute("aria-expanded")).toBe("true");
    expect(effortCell("medium").getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(level("medium")));
    // Closing it returns to the bare address; opening another model writes that model's own.
    await user.click(modelButton(SONNET));
    expect(window.location.hash).toBe("#buddies/catalog");
    await user.click(modelButton(SOL));
    expect(window.location.hash).toBe(address(2));
  });

  it("opens routing from its own work address", async () => {
    const f = fixture();
    renderBuddy("#buddies/router/routing", f);
    await screen.findByRole("region", { name: "Router" });
    expect(within(screen.getByRole("navigation", { name: "Router 的工作" })).getByRole("link", { name: "路由" }).getAttribute("aria-current")).toBe("page");
    expect(modelsRegion()).toBeNull();
    expect(within(screen.getByRole("listitem", { name: `${SOL} / medium` })).getByText("当前")).toBeTruthy();
  });

  it("shows the directory without rewriting the removed roles address", async () => {
    const f = fixture();
    renderBuddy("#buddies/roles", f);
    await listed(3, 4);
    expect(current(sectionsNav())).toEqual(["目录"]);
    expect(window.location.hash).toBe("#buddies/roles");
  });

  it("follows hashchange and the browser back action between views", async () => {
    const f = fixture();
    renderBuddy("#buddies", f);
    await listed(3, 4);
    await act(async () => { window.location.hash = "#buddies/router"; });
    await waitFor(() => expect(screen.getByRole("region", { name: "Router" })).toBeTruthy());
    await act(async () => { window.history.back(); });
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Harness" })).toBeNull();
  });

  it("goes from the Router section to the Router's model and focuses that buddy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddies/router", f);
    await screen.findByRole("region", { name: "Router" });
    const reviewLine = screen.getByRole("listitem", { name: `${SOL} / medium` });
    await user.click(within(reviewLine).getByRole("link", { name: `${SOL} / medium` }));
    expect(window.location.hash).toBe(`#buddies/catalog/buddy/${encodeURIComponent(solMediumId)}`);
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
    await user.click(screen.getByRole("checkbox", { name: "仅显示已启用" }));

    await act(async () => { window.location.hash = `#buddies/catalog/buddy/${encodeURIComponent(highId)}`; });
    await waitFor(() => expect(search()).toHaveProperty("value", ""));
    expect(screen.getByRole("checkbox", { name: "仅显示已启用" }).getAttribute("aria-checked")).toBe("false");
    await listed(3, 4);
    await waitFor(() => pane(SONNET));
    await waitFor(() => expect(document.activeElement).toBe(level("high")));
    // Following an address is a local change of view: the snapshot already holds the model.
    expect(f.command).not.toHaveBeenCalled();
  });

  it("turns on the unavailable filter and unfolds the harness when an address names an offline buddy", async () => {
    const f = fixture();
    renderBuddy(`#buddies/catalog/buddy/${encodeURIComponent(glmId)}`, f);
    const box = await screen.findByRole("checkbox", { name: "显示不可用 1" });
    await waitFor(() => expect(box.getAttribute("aria-checked")).toBe("true"));
    await listed(3, 5);
    // A harness that cannot work starts folded; the address opens it.
    expect(modelButton(GLM).getAttribute("aria-expanded")).toBe("true");
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
    await choosePreference(user, "medium", "优先");
    expect(within(sectionsNav()).getAllByRole("link").map(link => link.firstChild?.textContent)).toEqual(["目录", "Router", "评价"]);
    await user.click(sectionLink(/^Router/));
    await screen.findByRole("region", { name: "Router" });
    await user.click(sectionLink(/^目录/));
    await waitFor(() => expect(modelsRegion()).toBeTruthy());
    expect(pane(SONNET)).toBeTruthy();
    expect(within(levelPreference("medium")).getByRole("radio", { name: "优先" }).getAttribute("aria-checked")).toBe("true");
    // Once a row is opened, the side column offers the way back to the table.
    expect(modelButton(SONNET).getAttribute("aria-expanded")).toBe("true");
    expect(within(pane()).queryByRole("button", { name: "模型" })).toBeNull();
    expect(screen.getByText("1 项未保存")).toBeTruthy();
  });
});
