import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { VIEW_PREFERENCE_PREFIX } from "./view-preferences";
import type { ModelServiceConcurrencyEntry, Buddy, Snapshot } from "./types";
import { listed, modelRow, pane, selectModel } from "./buddies/page.testing";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const retiredMax = "dsh:deepseek-official:retired-model:max";
const FLASH = "dsh / deepseek-official / deepseek-flash";
const RETIRED = "dsh / deepseek-official / retired-model";

function catalog(): Buddy[] {
  const base = {
    harness: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision"],
    contextWindow: null, source: "catalog:fixture", description: "",
  };
  return [
    { ...base, buddyId: flashOff, model: "deepseek-flash", effort: "off", label: "deepseek-flash · off",
      available: true, enabled: true },
    { ...base, buddyId: flashHigh, model: "deepseek-flash", effort: "high", label: "deepseek-flash · high",
      available: true, enabled: false },
    { ...base, buddyId: retiredMax, model: "retired-model", effort: "max", label: "retired-model · max",
      available: false, enabled: true, unavailableReason: "provider paused" },
  ];
}

const flashEntry: ModelServiceConcurrencyEntry = {
  harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1,
};
const retiredEntry: ModelServiceConcurrencyEntry = {
  harness: "dsh", provider: "deepseek-official", model: "retired-model", limit: 3, active: 0,
};

type LimitPatch = { harness: string; provider: string; model: string; limit: number };

function fixture(options: { discoveryLimit?: number } = {}) {
  const buddies = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [flashOff], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies,

    modelServiceNotes: [], preferences: [], decisions: [],
    modelServiceConcurrency: [flashEntry, retiredEntry],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };

  const applyLimits = (patches: LimitPatch[]) => state.modelServiceConcurrency.map((entry) => {
    const patch = patches.find((candidate) => candidate.harness === entry.harness
      && candidate.provider === entry.provider && candidate.model === entry.model);
    return patch ? { ...entry, limit: patch.limit } : entry;
  });
  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (operation === "user_policy_publish") {
      published.push(params);
      const limits = (params.modelServiceConcurrency ?? []) as LimitPatch[];
      state = { ...state, modelServiceConcurrency: limits.length ? applyLimits(limits) : state.modelServiceConcurrency,
 };
      return { published: true,};
    }


    if (operation === "buddy_list") {
      return { buddies, modelServiceNotes: [], preferences: [],         modelServiceConcurrency: structuredClone(state.modelServiceConcurrency), nextCursor: null };
    }
    if (operation === "model_catalog_refresh") {
      // The program publishes directory facts itself. A discovery can also
      // carry another writer's concurrency limit for the flash model service.
      state = { ...state,
        ...(options.discoveryLimit !== undefined
          ? { modelServiceConcurrency: applyLimits([{ harness: "dsh", provider: "deepseek-official",
              model: "deepseek-flash", limit: options.discoveryLimit }]) }
          : {}) };
      return {};
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, operations, published, snapshot: () => state };
}

async function openFlashCard(f: ReturnType<typeof fixture>, user: ReturnType<typeof userEvent.setup>) {
  window.location.hash = "#buddies";
  render(<App suppliedApi={f.api} />);
  await listed(1, 2);
  await selectModel(user, FLASH, "DSH");
}
const unsaved = () => within(pane()).findByRole("img", { name: "未保存" });

function concurrencyInput() {
  return screen.getByRole("spinbutton", { name: "并发上限" }) as HTMLInputElement;
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

describe("a model's concurrency", () => {
  it("shows the shared limit as a directly editable field, with read-only occupancy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    // Directly editable: there is no global edit switch gating the field.
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    expect(concurrencyInput()).toHaveProperty("value", "2");
    expect(within(pane()).getByText("占用 1")).toBeTruthy();
    // The model row writes the same two numbers at its trailing edge.
    expect(within(modelRow(FLASH)).getByLabelText("占用 1，上限 2").textContent).toBe("1 / 2");
    expect(f.command).not.toHaveBeenCalled();
  });

  it("publishes a changed limit as a model service patch and never the occupancy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    const input = concurrencyInput();
    await user.clear(input);
    await user.type(input, "6");
    await unsaved();
    // The one setting of the model is the only unsaved change.
    await screen.findByText(/1 项未保存/);
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published).toHaveLength(1);
    expect(f.published[0].modelServiceConcurrency).toEqual([
      { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const wire = JSON.stringify(f.published[0]);
    expect(wire).not.toContain("active");
    expect(wire).not.toContain("buddyId");
    // The raise is visible immediately and the occupancy stays observation.
    expect(concurrencyInput()).toHaveProperty("value", "6");
    expect(within(pane()).getByText("占用 1")).toBeTruthy();
    expect(within(pane()).queryByRole("img", { name: "未保存" })).toBeNull();
  });

  it("edits one setting shared by all the model's reasoning efforts", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.clear(concurrencyInput());
    await user.type(concurrencyInput(), "6");
    await unsaved();
    // The draft limit belongs to the model; its collapsed row follows it.
    expect(within(pane()).getAllByRole("spinbutton")).toHaveLength(1);
    expect(concurrencyInput()).toHaveProperty("value", "6");
    expect(within(pane()).getByText("占用 1")).toBeTruthy();
    expect(within(modelRow(FLASH)).getByLabelText("占用 1，上限 6").textContent).toBe("1 / 6");
  });

  it("keeps an out-of-range input unsaved until it is corrected", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    const input = concurrencyInput();
    await user.clear(input);
    await user.type(input, "33");
    expect(input).toHaveProperty("value", "33");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(within(pane()).getByText("需要 1–32 的整数")).toBeTruthy();
    // Blurring must not silently publish the intermediate valid prefix (3).
    await user.tab();
    expect(input).toHaveProperty("value", "33");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(f.published).toHaveLength(0);
    await user.clear(input);
    await user.type(input, "4");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].modelServiceConcurrency[0].limit).toBe(4);
  });

  it("edits the concurrency limit of an unavailable model", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    // The retired model is hidden until 显示不可用 is checked.
    await listed(1, 2);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 1" }));
    await listed(2, 3);
    await selectModel(user, RETIRED, "DSH");
    const input = concurrencyInput();
    expect(input).toHaveProperty("value", "3");
    expect(input.disabled).toBe(false);
    expect(input.hasAttribute("aria-disabled")).toBe(false);
    await user.clear(input);
    await user.type(input, "4");
    await unsaved();
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].modelServiceConcurrency).toEqual([
      { harness: "dsh", provider: "deepseek-official", model: "retired-model", limit: 4 },
    ]);
  });

  it("saves a local limit over a changed discovery limit", async () => {
    const f = fixture({ discoveryLimit: 4 });
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.clear(concurrencyInput());
    await user.type(concurrencyInput(), "6");
    await unsaved();
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText("没有新的 buddy");
    expect(concurrencyInput()).toHaveProperty("value", "6");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].modelServiceConcurrency).toEqual([{ harness: "dsh", provider: "deepseek-official",
      model: "deepseek-flash", limit: 6 }]);
  });
});
