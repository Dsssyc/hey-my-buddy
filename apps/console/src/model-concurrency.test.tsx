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
    csrfToken: "csrf", tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: flashOff },
    profiles,
    cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, summary: `评价 ${p.model}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    annotations: [], preferences: [], evidence: [], decisions: [], sampleCounts: {},
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
      return { profiles, cards: state.cards, annotations: [], preferences: [], sampleCounts: {},
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
  } as unknown as ConsoleApi;
  return { api, command, operations, published, snapshot: () => state };
}

async function openFlashCard(f: ReturnType<typeof fixture>, user: ReturnType<typeof userEvent.setup>) {
  window.location.hash = "#models";
  render(<App suppliedApi={f.api} />);
  await screen.findByRole("heading", { name: "模型 1" });
  await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
  await screen.findByRole("heading", { name: "deepseek-flash" });
  await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
}

function concurrencyInput() {
  return screen.getByRole("spinbutton", { name: "并发任务上限" }) as HTMLInputElement;
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("model family concurrency", () => {
  it("shows the shared limit and read-only occupancy, with a field only while editing", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    // Read-only: the recorded limit and occupancy are facts, never controls.
    expect(screen.getByText("2 · 当前占用 1")).toBeTruthy();
    expect(screen.queryByRole("spinbutton", { name: "并发任务上限" })).toBeNull();
    expect(screen.getByText(/并发上限由同一模型的所有思考档位与路由、执行共用/)).toBeTruthy();
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    expect(concurrencyInput()).toHaveProperty("value", "2");
    expect(screen.getByText("当前占用 1（只读）")).toBeTruthy();
    expect(screen.getByText(/保存后立即对后续任务生效/)).toBeTruthy();
    expect(screen.getByText(/调低上限不会中断正在运行的任务/)).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("publishes a changed limit as a family patch and never the occupancy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    const input = concurrencyInput();
    await user.clear(input);
    await user.type(input, "6");
    await screen.findByText("并发上限未保存");
    // Both effort variants of the family share the single draft setting.
    await screen.findByText(/编辑中 · 未保存 · 2 个配置/);
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published).toHaveLength(1);
    expect(f.published[0].modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const wire = JSON.stringify(f.published[0]);
    expect(wire).not.toContain("active");
    expect(wire).not.toContain("profileId");
    // The raise is visible immediately and the occupancy stays observation.
    await screen.findByText("6 · 当前占用 1");
    expect(screen.queryByRole("spinbutton", { name: "并发任务上限" })).toBeNull();
  });

  it("edits one shared setting across the family's effort variants", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.clear(concurrencyInput());
    await user.type(concurrencyInput(), "6");
    await screen.findByText("并发上限未保存");
    // Switching to the high variant of the same family shows the same draft limit.
    await user.click(screen.getByRole("button", { name: "high，未启用" }));
    expect(concurrencyInput()).toHaveProperty("value", "6");
    await screen.findByText("并发上限未保存");
    expect(screen.getByText("当前占用 1（只读）")).toBeTruthy();
  });

  it("keeps an out-of-range input unsaved until it is corrected", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    const input = concurrencyInput();
    await user.clear(input);
    await user.type(input, "0");
    expect(input).toHaveProperty("value", "0");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText(/需要输入 1–32 的整数，当前修改不会保存/)).toBeTruthy();
    expect(screen.queryByText("并发上限未保存")).toBeNull();
    // Leaving the field restores the committed limit instead of the typed text.
    await user.tab();
    expect(input).toHaveProperty("value", "2");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("没有需要保存的用户修改。");
    expect(f.published).toHaveLength(0);
  });

  it("edits the concurrency limit of an unavailable family", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByLabelText("显示不可用配置"));
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^retired-model/ }));
    await screen.findByRole("heading", { name: "retired-model" });
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    const input = concurrencyInput();
    expect(input).toHaveProperty("value", "3");
    expect(input.disabled).toBe(false);
    await user.clear(input);
    await user.type(input, "4");
    await screen.findByText("并发上限未保存");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0].modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "retired-model", limit: 4 },
    ]);
  });

  it("keeps a conflicting limit at its own revision when discovery finds another value", async () => {
    const f = fixture({ discoveryLimit: 4 });
    const user = userEvent.setup();
    await openFlashCard(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.clear(concurrencyInput());
    await user.type(concurrencyInput(), "6");
    await screen.findByText("并发上限未保存");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新/);
    const banner = document.querySelector<HTMLElement>(".conflict-banner")!;
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("并发上限");
    expect(banner.textContent).toContain("已被其他发布修改");
    expect(banner.textContent).toContain("dsh/deepseek-official/deepseek-flash");
    expect(concurrencyInput()).toHaveProperty("value", "6");
    // Saving the stale draft under V3 is refused without an explicit resolution.
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect((await screen.findAllByText(/共享评价表已发布 V3，你的草稿基于 V2/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // Deliberate reload adopts the other writer's published limit.
    await user.click(within(banner).getByRole("button", { name: "重新加载最新版本" }));
    await screen.findByText("已加载评价表 V3。请核对后重新保存。");
    expect(concurrencyInput()).toHaveProperty("value", "4");
  });
});
