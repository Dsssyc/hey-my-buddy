import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { VIEW_PREFERENCE_PREFIX } from "./view-preferences";
import { formatDateTime } from "./i18n/format";
import { effortCell, levelPreference, levelSwitch, listed, modelButton, modelRow, pane, selectModel } from "./buddies/page.testing";
import type { Profile, ProfilePage, Snapshot } from "./types";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const plainMax = "dsh:deepseek-official:plain-model:max";
const legacyMax = "dsh:deepseek-official:legacy-model:max";
const ghostLow = "dsh:deepseek-official:ghost-model:low";
const retiredMax = "dsh:deepseek-official:retired-model:max";
const retiredLow = "dsh:deepseek-official:retired-model:low";
/** Two board-recorded first-absence times; formatting reuses the page's own formatter. */
const earlyMissing = "2026-10-07T01:23:45Z";
const lateMissing = "2026-10-07T03:00:00Z";
const early = formatDateTime(Date.parse(earlyMissing));
const late = formatDateTime(Date.parse(lateMissing));
const model = (name: string) => `dsh / deepseek-official / ${name}`;
/** The inline row that holds a model's name and whatever is said about its state. */
const nameCell = (name: string) => modelRow(model(name));

/**
 * One DSH catalog covering the ADR-027 §6 states: a pending model service whose
 * efforts record different first-absence times, a model service the board marks
 * catalog-available while harness health makes it unusable, a pre-ADR-027
 * fixture profile without the new fields, a pending model service that never recorded
 * a time, and a retired unavailable model service.
 */
function catalog(): Profile[] {
  const base = {
    adapter: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision"],
    contextWindow: null, source: "catalog:fixture", description: "",
  };
  return [
    { ...base, profileId: flashOff, model: "deepseek-flash", effort: "off", label: "deepseek-flash · off",
      available: true, enabled: true, catalogStatus: "pending" as const, pendingSince: earlyMissing },
    { ...base, profileId: flashHigh, model: "deepseek-flash", effort: "high", label: "deepseek-flash · high",
      available: true, enabled: false, catalogStatus: "pending" as const, pendingSince: lateMissing },
    // Catalog-available by the board, unusable by harness health: the two
    // facts must never merge into one state.
    { ...base, profileId: plainMax, model: "plain-model", effort: "max", label: "plain-model · max",
      available: false, enabled: true, catalogStatus: "available" as const,
      unavailableReason: "HARNESS_UNHEALTHY" },
    { ...base, profileId: legacyMax, model: "legacy-model", effort: "max", label: "legacy-model · max",
      available: true, enabled: true },
    { ...base, profileId: ghostLow, model: "ghost-model", effort: "low", label: "ghost-model · low",
      available: true, enabled: false, catalogStatus: "pending" as const, pendingSince: null },
    { ...base, profileId: retiredMax, model: "retired-model", effort: "max", label: "retired-model · max",
      available: false, enabled: false, unavailableReason: "provider paused" },
    { ...base, profileId: retiredLow, model: "retired-model", effort: "low", label: "retired-model · low",
      available: false, enabled: false, unavailableReason: "provider paused" },
  ];
}

function fixture() {
  const profiles = catalog();
  const live = profiles.filter(p => p.available);
  const state: Snapshot = {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerProfileIds: [flashOff], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "review" as const, routingBudget: "standard" },
    profiles: live,
    unavailableProfileCount: profiles.filter(p => !p.available).length,
 modelServiceNotes: [], preferences: [],  decisions: [],

    modelServiceConcurrency: [],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };

  const operations: string[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>): Promise<unknown> => {
    operations.push(operation);
    if (operation === "model_profiles") {
      const visible = params.includeUnavailable === true ? profiles : live;
      const after = typeof params.after === "string" ? params.after : "";
      const limit = typeof params.limit === "number" ? params.limit : 100;
      const slice = [...visible].sort((a, b) => a.profileId.localeCompare(b.profileId))
        .filter(p => p.profileId > after).slice(0, limit);
      const page: ProfilePage = {
        profiles: slice, preferences: [],         modelServiceNotes: [], modelServiceConcurrency: [],
        nextCursor: null,
      };
      return page;
    }


    if (operation === "user_policy_publish") return {};
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, operations };
}

// These cases start with default model filters and restore only the keys they own.
const modelViewKeys = ["models.enabledOnly", "models.showUnavailable"]
  .map(name => VIEW_PREFERENCE_PREFIX + name);
let savedModelViews: Array<string | null>;
beforeEach(() => {
  savedModelViews = modelViewKeys.map(key => window.localStorage.getItem(key));
  for (const key of modelViewKeys) window.localStorage.removeItem(key);
});

afterEach(() => {
  cleanup();
  modelViewKeys.forEach((key, index) => {
    const saved = savedModelViews[index];
    if (saved === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, saved);
  });
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("a model the catalogue no longer confirms (ADR-027 §6)", () => {
  it("marks the pending model once, with its recorded start as the explanation, and leaves other rows unmarked", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    // The pending model stays in the default view: it is available, so no
    // 显示不可用 is needed to see it.
    await listed(2, 4);
    const marks = within(nameCell("deepseek-flash")).getAllByText("待确认");
    expect(marks).toHaveLength(1);
    expect(nameCell("deepseek-flash").textContent).not.toContain("不可用");
    // A fixture without the new field shows no invented state.
    expect(within(nameCell("legacy-model")).queryByText("待确认")).toBeNull();
    // The unavailable model reads as 不可用, never as 待确认.
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 3" }));
    await listed(3, 7);
    expect(within(nameCell("retired-model")).getByText("不可用").getAttribute("aria-description")).toBe("provider paused");
    expect(within(nameCell("retired-model")).queryByText("待确认")).toBeNull();
  });

  it("explains the one mark of a model with the earliest recorded first-absence time", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 4);
    // Both levels are pending with different times; the model says it once, with the earliest.
    const mark = within(nameCell("deepseek-flash")).getByText("待确认");
    expect(mark.getAttribute("aria-description")).toBe(`待确认，自 ${early}`);
    await user.hover(mark);
    expect((await screen.findByRole("tooltip")).textContent).toBe(`待确认，自 ${early}`);
    expect(document.body.textContent).not.toContain(late);
  });

  it("keeps a pending buddy usable, and writes the state with its time in the drawer", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 4);
    await selectModel(user, model("deepseek-flash"), "DSH");
    // The model's state is pending since the recorded time; it is not unavailable.
    expect(within(pane()).getByText("待确认")).toBeTruthy();
    expect(within(pane()).getByText(early).getAttribute("datetime")).toBe(earlyMissing);
    expect(within(pane()).queryByText("不可用")).toBeNull();
    expect(within(pane()).queryByText("可用")).toBeNull();
    // A pending buddy still toggles like any available one.
    await user.click(effortCell("high"));
    expect(levelSwitch("high").hasAttribute("aria-disabled")).toBe(false);
    await user.click(levelSwitch("high"));
    expect(levelSwitch("high").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/1 项未保存/)).toBeTruthy();
    // And it can be pinned: pending records nothing against a buddy that is on.
    await user.click(effortCell("off"));
    expect(within(levelPreference("off")).getByRole("radio", { name: "固定" }).hasAttribute("aria-disabled")).toBe(false);
  });

  it("shows a pending model without a recorded time as plain 待确认, never an invented date", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 4);
    const mark = within(nameCell("ghost-model")).getByText("待确认");
    expect(mark.hasAttribute("aria-description")).toBe(false);
    await selectModel(user, model("ghost-model"), "DSH");
    expect(within(pane()).getByText("待确认")).toBeTruthy();
    expect(pane().querySelector("time")).toBeNull();
    expect(pane().textContent).not.toContain("未记录");
  });

  it("keeps a model unavailable when its harness cannot run it, whatever the catalogue lists", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 4);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 3" }));
    await listed(3, 7);
    // The board lists this model as present in the catalogue, yet harness health
    // makes it unusable now: it reads 不可用 with that reason, never 可用 or 待确认.
    const state = within(nameCell("plain-model")).getByText("不可用");
    expect(state.getAttribute("aria-description")).toBe("Harness 不可用");
    expect(within(nameCell("plain-model")).queryByText("待确认")).toBeNull();
    await user.click(modelButton(model("plain-model")));
    expect(within(pane(model("plain-model"))).getByText("不可用")).toBeTruthy();
    expect(within(pane()).getByText("不可用").getAttribute("aria-description")).toBe("Harness 不可用");
    expect(within(pane()).queryByText("可用")).toBeNull();
  });
});
