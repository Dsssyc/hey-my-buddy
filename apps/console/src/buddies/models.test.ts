import { reasonText } from "../i18n/reason";
import i18n from "i18next";
import { describe, expect, it } from "vitest";
import { historyView } from "../draft";
import type { ConsoleView, HarnessHealth, Buddy, Snapshot } from "../types";
import { harnessBlocks, harnessStateText, listedModels, modelIdentity, reasonTexts } from "./models";

/**
 * What the models table is made of, apart from how it is drawn: which models
 * the filters leave, how they are grouped by harness and in what order, what
 * starts folded, and how a recorded reason is worded.
 */

const t = i18n.getFixedT(null, "buddies");

function buddy(harness: string, provider: string, model: string, effort: string, extra: Partial<Buddy> = {}): Buddy {
  return { effortRank: null,
    buddyId: `${harness}:${provider}:${model}:${effort}`, label: `${model.toUpperCase()} · ${effort}`, harness, provider, model, effort,
    available: true, enabled: false, capabilities: [], contextWindow: null, description: "", source: "catalog:fixture", ...extra,
  };
}

/** A working harness with a used and an unused model, an unused harness, and one that is not logged in. */
function buddies(): Buddy[] {
  return [
    buddy("dsh", "deepseek-official", "flash", "off", { enabled: true }),
    buddy("dsh", "deepseek-official", "flash", "max"),
    buddy("dsh", "second-provider", "pro", "high"),
    buddy("codex", "openai", "sol", "high"),
    buddy("claude", "anthropic", "opus", "high", { enabled: true, available: false, unavailableReason: { code: "harness.probe-failed", params: {} } }),
    buddy("claude", "anthropic", "opus", "max", { available: false, unavailableReason: { code: "harness.probe-failed", params: {} } }),
  ];
}

function snapshot(list: Buddy[] = buddies()): Snapshot {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "s", canWrite: true, reason: null },

    configuration: null, buddies: list, preferences: [],  modelServiceNotes: [], decisions: [],
    modelServiceConcurrency: [{ harness: "dsh", provider: "deepseek-official", model: "flash", limit: 3, active: 1 }],
    tasks: { pendingCount: 0 }, capabilities: {},
  };
}

const harnesses: HarnessHealth[] = [
  { harness: "dsh", status: "ready", revision: 1, manualPath: null },
  { harness: "codex", status: "ready", revision: 1, manualPath: null },
  { harness: "claude", status: "login-required", revision: 1, manualPath: null },
];

function blocks(options: { recorded?: Snapshot; data?: ConsoleView; folded?: Record<string, boolean>; fresh?: string[]; health?: HarnessHealth[] } = {}) {
  const recorded = options.recorded ?? snapshot();
  const data = options.data ?? historyView(recorded, null);
  return harnessBlocks({
    listed: listedModels(data.buddies, { query: "", enabledOnly: false, showUnavailable: true }),
    data, recorded, harnesses: options.health ?? harnesses, folded: options.folded ?? {}, fresh: new Set(options.fresh ?? []),
  });
}

describe("which models are listed", () => {
  const names = (filters: Partial<Parameters<typeof listedModels>[1]>) =>
    listedModels(buddies(), { query: "", enabledOnly: false, showUnavailable: false, ...filters }).map(modelIdentity);

  it("hides a model none of whose buddies is available until the filter asks for it", () => {
    expect(names({})).toEqual(["codex / openai / sol", "dsh / deepseek-official / flash", "dsh / second-provider / pro"]);
    expect(names({ showUnavailable: true })).toContain("claude / anthropic / opus");
  });

  it("keeps the models with an enabled buddy among those shown", () => {
    expect(names({ enabledOnly: true })).toEqual(["dsh / deepseek-official / flash"]);
    // The unavailable model's enabled buddy counts once unavailable buddies are shown.
    expect(names({ enabledOnly: true, showUnavailable: true })).toEqual(["claude / anthropic / opus", "dsh / deepseek-official / flash"]);
  });

  it("searches the label, the model, the provider, the harness and the harness's name without regard to case", () => {
    expect(names({ query: "  SOL " })).toEqual(["codex / openai / sol"]);
    expect(names({ query: "second-prov" })).toEqual(["dsh / second-provider / pro"]);
    expect(names({ query: "claude code", showUnavailable: true })).toEqual(["claude / anthropic / opus"]);
    expect(names({ query: "nothing-like-this" })).toEqual([]);
  });
});

describe("the table's blocks", () => {
  it("puts the harnesses that can work first and writes the state of the one that cannot", () => {
    const all = blocks();
    expect(all.map(block => [block.harness, block.state])).toEqual([["codex", null], ["dsh", null], ["claude", "login-required"]]);
    expect(all.map(block => block.name)).toEqual(["Codex", "DSH", "Claude Code"]);
  });

  it("starts a harness folded when none of its buddies is enabled, and when it cannot work at all", () => {
    const folded = Object.fromEntries(blocks().map(block => [block.harness, block.folded]));
    // Codex has nothing enabled; Claude Code has an enabled buddy but is not logged in.
    expect(folded).toEqual({ codex: true, dsh: false, claude: true });
    // What the user folded or unfolded by hand wins over both defaults.
    const chosen = Object.fromEntries(blocks({ folded: { codex: false, dsh: true, claude: false } }).map(block => [block.harness, block.folded]));
    expect(chosen).toEqual({ codex: false, dsh: true, claude: false });
  });

  it("counts a harness's models, providers and enabled buddies for its folded row", () => {
    const dsh = blocks().find(block => block.harness === "dsh")!;
    expect([dsh.models.length, dsh.providers, dsh.enabled, dsh.total]).toEqual([2, ["deepseek-official", "second-provider"], 1, 3]);
    const claude = blocks().find(block => block.harness === "claude")!;
    expect([claude.models.length, claude.providers, claude.enabled, claude.total]).toEqual([1, ["anthropic"], 1, 2]);
  });

  it("falls back to plain unavailability for a harness without a health record, and keeps an unknown state as recorded", () => {
    const without = blocks({ health: [] }).find(block => block.harness === "claude")!;
    expect(without.state).toBe("unavailable");
    const future = blocks({ health: [{ harness: "claude", status: "quarantined" as HarnessHealth["status"], revision: 1, manualPath: null }] })
      .find(block => block.harness === "claude")!;
    expect(future.state).toBe("quarantined");
    expect(harnessStateText("quarantined", t)).toBe("quarantined");
    expect(harnessStateText("login-required", t)).toBe("未登录");
    expect(harnessStateText("unavailable", t)).toBe("不可用");
  });

  it("reads the limit from the draft and the occupancy from what is published, unknown when unreported", () => {
    const recorded = snapshot();
    const data = { ...historyView(recorded, null), modelServiceConcurrency: [{ harness: "dsh", provider: "deepseek-official", model: "flash", limit: 7 }] };
    const dsh = blocks({ recorded, data }).find(block => block.harness === "dsh")!;
    const flash = dsh.models.find(model => model.modelService.buddies[0].model === "flash")!;
    expect([flash.limit, flash.occupancy]).toEqual([7, 1]);
    // A model the board has not reported keeps the default limit and an unknown occupancy, never 0.
    const pro = dsh.models.find(model => model.modelService.buddies[0].model === "pro")!;
    expect([pro.limit, pro.occupancy]).toEqual([2, null]);
  });

  it("marks a buddy whose switch or own preference differs from what is published, and nothing else", () => {
    const recorded = snapshot();
    const view = historyView(recorded, null);
    const data: ConsoleView = {
      ...view,
      buddies: view.buddies.map(entry => entry.buddyId === "dsh:deepseek-official:flash:max" ? { ...entry, enabled: true } : entry),
      preferences: [{ buddyId: "codex:openai:sol:high", mode: "exclude", note: null }],
    };
    const chips = blocks({ recorded, data }).flatMap(block => block.models).flatMap(model => model.chips);
    expect(chips.filter(chip => chip.unsaved).map(chip => chip.buddy.buddyId))
      .toEqual(["codex:openai:sol:high", "dsh:deepseek-official:flash:max"]);
    expect(chips.find(chip => chip.buddy.buddyId === "codex:openai:sol:high")!.preference).toBe("exclude");
    expect(chips.find(chip => chip.buddy.buddyId === "dsh:deepseek-official:flash:off")!.preference).toBeNull();
  });

  it("marks a model as new when this page discovered it", () => {
    const recorded = snapshot(buddies());
    const fresh = JSON.stringify(["dsh", "second-provider", "pro"]);
    const marked = blocks({ recorded, fresh: [fresh] }).flatMap(block => block.models).filter(model => model.fresh).map(model => modelIdentity(model.modelService));
    expect(marked).toEqual(["dsh / second-provider / pro"]);
  });
});

describe("recorded reasons in words", () => {
  const harness = { code: "harness.probe-failed", params: {} };
  const absent = { code: "catalog.model-absent", params: {} };
  it("formats structured reasons and deduplicates their text", () => {
    expect(reasonTexts([harness, absent, absent], t)).toEqual([reasonText(harness), reasonText(absent)]);
  });
  it("omits a harness cause when that harness has already said it", () => {
    expect(reasonTexts([harness, absent], t, true)).toEqual([reasonText(absent)]);
  });
});
