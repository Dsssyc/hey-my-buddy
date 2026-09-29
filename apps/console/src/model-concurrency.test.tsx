import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { ModelConcurrencyEntry, Profile, Snapshot, WriterGrant } from "./types";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const retiredMax = "dsh:deepseek-official:retired-model:max";

function catalog(): Profile[] {
  const base = {
    adapter: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision"],
    contextWindow: null, source: "catalog:fixture", description: "",
  };
  return [
    { ...base, profileId: flashOff, model: "deepseek-flash", effort: "off", label: "deepseek-flash · off",
      available: true, enabled: true },
    { ...base, profileId: flashHigh, model: "deepseek-flash", effort: "high", label: "deepseek-flash · high",
      available: true, enabled: false },
    { ...base, profileId: retiredMax, model: "retired-model", effort: "max", label: "retired-model · max",
      available: false, enabled: true, unavailableReason: "provider paused" },
  ];
}

const flashEntry: ModelConcurrencyEntry = {
  adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1,
};
const retiredEntry: ModelConcurrencyEntry = {
  adapter: "dsh", provider: "deepseek-official", model: "retired-model", limit: 3, active: 0,
};

type LimitPatch = { adapter: string; provider: string; model: string; limit: number };

function fixture(options: { discoveryLimit?: number } = {}) {
  const profiles = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, fastRouterProfileId: null, reviewRouterProfileId: flashOff , defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles,
    cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, summary: `评价 ${p.model}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    familyAnnotations: [], preferences: [], familyPreferences: [], preferenceOverrides: [], evidence: [], decisions: [], sampleCounts: {},
    modelConcurrency: [flashEntry, retiredEntry],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing",
    state: "active", tableRevision: 2, expiresAt: new Date(Date.now() + 120000).toISOString() };
  const applyLimits = (patches: LimitPatch[]) => state.modelConcurrency.map((entry) => {
    const patch = patches.find((candidate) => candidate.adapter === entry.adapter
      && candidate.provider === entry.provider && candidate.model === entry.model);
    return patch ? { ...entry, limit: patch.limit } : entry;
  });
  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (operation === "user_policy_publish") {
      published.push(params);
      const limits = (params.modelConcurrency ?? []) as LimitPatch[];
      state = { ...state, modelConcurrency: limits.length ? applyLimits(limits) : state.modelConcurrency,
        tableRevision: state.tableRevision + 1,
        gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null } };
      return { published: true, tableRevision: state.tableRevision };
    }
    if (operation === "evaluation_write_begin") {
      state = { ...state, gate: { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } } };
      return grant;
    }
    if (operation === "evaluation_write_abort") return { aborted: true };
    if (operation === "model_profiles") {
      return { profiles, cards: state.cards, familyAnnotations: [], preferences: [], preferenceOverrides: [], familyPreferences: [], sampleCounts: {},
        modelConcurrency: structuredClone(state.modelConcurrency),
        tableRevision: state.tableRevision, nextCursor: null };
    }
    if (operation === "model_catalog_refresh") {
      // The program publishes directory facts itself. A discovery can also
      // carry another writer's concurrency limit for the flash family, which
      // a stale local draft must never silently overwrite.
      state = { ...state, tableRevision: state.tableRevision + 1,
        ...(options.discoveryLimit !== undefined
          ? { modelConcurrency: applyLimits([{ adapter: "dsh", provider: "deepseek-official",
              model: "deepseek-flash", limit: options.discoveryLimit }]) }
          : {}) };
      return { tableRevision: state.tableRevision };
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
  return { api, command, operations, published, snapshot: () => state };
}

async function openFlashCard(f: ReturnType<typeof fixture>, user: ReturnType<typeof userEvent.setup>) {
  window.location.hash = "#models";
  render(<App suppliedApi={f.api} />);
  await screen.findByRole("heading", { name: "模型 1" });
  await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
  await screen.findByRole("heading", { name: "deepseek-flash" });
}

function concurrencyInput() {
  return screen.getByRole("spinbutton", { name: "并发上限" }) as HTMLInputElement;
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("model family concurrency", () => {
  it("shows the shared limit as a directly editable field, with read-only occupancy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    // Directly editable: there is no global edit switch gating the field.
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    expect(concurrencyInput()).toHaveProperty("value", "2");
    expect(screen.getByText(/当前占用 1/)).toBeTruthy();
    expect(screen.getByText(/并发上限由同一模型的所有思考档位与路由、执行共用/)).toBeTruthy();
    expect(screen.getByText(/保存后立即对后续任务生效/)).toBeTruthy();
    expect(screen.getByText(/调低上限不会中断正在运行的任务/)).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("publishes a changed limit as a family patch and never the occupancy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    const input = concurrencyInput();
    await user.clear(input);
    await user.type(input, "6");
    await screen.findByText("未保存");
    // The single family setting is the only unsaved change.
    await screen.findByText(/有 1 项未保存修改/);
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published).toHaveLength(1);
    expect(f.published[0].modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const wire = JSON.stringify(f.published[0]);
    expect(wire).not.toContain("active");
    expect(wire).not.toContain("profileId");
    // The raise is visible immediately and the occupancy stays observation.
    expect(concurrencyInput()).toHaveProperty("value", "6");
    expect(screen.getByText(/当前占用 1/)).toBeTruthy();
  });

  it("edits one shared setting across the family's effort variants", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.clear(concurrencyInput());
    await user.type(concurrencyInput(), "6");
    await screen.findByText("未保存");
    // The draft limit is a single family-level field, not tied to one tag.
    expect(concurrencyInput()).toHaveProperty("value", "6");
    expect(screen.getByText(/当前占用 1/)).toBeTruthy();
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
    expect(screen.getByText(/需要输入 1–32 的整数，当前修改不会保存/)).toBeTruthy();
    // Blurring must not silently publish the intermediate valid prefix (3).
    await user.tab();
    expect(input).toHaveProperty("value", "33");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    await user.clear(input);
    await user.type(input, "4");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0].modelConcurrency[0].limit).toBe(4);
  });

  it("edits the concurrency limit of an unavailable family", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    // The retired family is hidden until 显示不可用配置 is checked.
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("checkbox", { name: "显示不可用配置（1）" }));
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^retired-model/ }));
    await screen.findByRole("heading", { name: "retired-model" });
    const input = concurrencyInput();
    expect(input).toHaveProperty("value", "3");
    expect(input.disabled).toBe(false);
    await user.clear(input);
    await user.type(input, "4");
    await screen.findByText("未保存");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0].modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "retired-model", limit: 4 },
    ]);
  });

  it("keeps a conflicting limit at its own revision when discovery finds another value", async () => {
    const f = fixture({ discoveryLimit: 4 });
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.clear(concurrencyInput());
    await user.type(concurrencyInput(), "6");
    await screen.findByText("未保存");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新/);
    const banner = document.querySelector<HTMLElement>(".conflict-banner")!;
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("并发上限");
    expect(banner.textContent).toContain("已被其他发布修改");
    expect(banner.textContent).toContain("dsh/deepseek-official/deepseek-flash");
    expect(concurrencyInput()).toHaveProperty("value", "6");
    // Saving the stale draft under V3 is refused without an explicit resolution.
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(/设置已在别处更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // Deliberate reload adopts the other writer's published limit.
    await user.click(within(banner).getByRole("button", { name: "重新加载最新版本" }));
    await screen.findByText("已加载评价表 V3。请核对后重新保存。");
    expect(concurrencyInput()).toHaveProperty("value", "4");
  });
});
