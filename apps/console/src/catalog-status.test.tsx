import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { formatDate } from "./ui";
import type { Profile, ProfilePage, Snapshot, WriterGrant } from "./types";

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
const early = formatDate(earlyMissing);
const late = formatDate(lateMissing);

/**
 * One DSH catalog covering the ADR-027 §6 states: a pending family whose
 * efforts record different first-absence times, a family the board marks
 * catalog-available while harness health makes it unusable, a pre-ADR-027
 * fixture profile without the new fields, a pending family that never recorded
 * a time, and a retired unavailable family.
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
    // facts must never merge in the 目录状态 row.
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
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [flashOff], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "review" as const, routingBudget: "standard" },
    profiles: live,
    unavailableProfileCount: profiles.filter(p => !p.available).length,
    cards: [], familyAnnotations: [], preferences: [], preferenceOverrides: [], familyPreferences: [],
    evidence: [], decisions: [],
    sampleCounts: {},
    modelConcurrency: [],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  const grant: WriterGrant = {
    writerId: "writer", generation: 1, writerToken: "private", phase: "writing", tableRevision: 2,
    expiresAt: new Date(Date.now() + 120000).toISOString(),
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
        profiles: slice, cards: [], preferences: [], preferenceOverrides: [], familyPreferences: [],
        familyAnnotations: [], sampleCounts: {}, modelConcurrency: [], tableRevision: 2,
        nextCursor: null,
      };
      return page;
    }
    if (operation === "evaluation_write_begin") return grant;
    if (operation === "evaluation_write_abort") return { aborted: true };
    if (operation === "user_policy_publish") return { tableRevision: 3 };
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, operations };
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("catalog pending display (ADR-027 §6)", () => {
  it("marks the pending family with its recorded start time and leaves normal and unavailable rows unmarked", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    // The pending family stays in the default view: it is available, so no
    // 显示不可用配置 is needed to see it.
    await screen.findByRole("heading", { name: "模型 3" });
    const pendingRow = screen.getByRole("button",
      { name: `deepseek-flash，已启用 1/2，Router，待确认 · 自 ${early}` });
    expect(pendingRow.querySelectorAll(".family-pending-mark")).toHaveLength(1);
    expect(pendingRow.textContent).not.toContain(late);
    expect(pendingRow.textContent).not.toContain("不可用");
    // A fixture without the new field shows no invented state.
    const legacyRow = screen.getByRole("button", { name: /^legacy-model，已启用 1\/1/ });
    expect(legacyRow.textContent).not.toContain("待确认");
    // The unavailable family reads as 不可用, never as 待确认.
    await user.click(screen.getByRole("checkbox", { name: "显示不可用配置（3）" }));
    await screen.findByRole("heading", { name: "模型 5" });
    const retiredRow = screen.getByRole("button", { name: /^retired-model，已启用 0\/2，不可用（provider paused）/ });
    expect(retiredRow.textContent).not.toContain("待确认");
  });

  it("aggregates one pending mark per family at the earliest recorded first-absence time", async () => {
    const f = fixture();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 3" });
    // Both efforts are pending, but the family row carries exactly one mark
    // with the earliest board-recorded time.
    const pendingRow = screen.getByRole("button", { name: /^deepseek-flash，已启用 1\/2，Router，待确认/ });
    expect(pendingRow.querySelectorAll(".family-pending-mark")).toHaveLength(1);
    const mark = pendingRow.querySelector(".family-pending-mark");
    expect(mark?.textContent).toBe(`待确认 · 自 ${early}`);
  });

  it("keeps a pending effort usable and routable, and shows the time in its detail view", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash，已启用 1\/2，Router，待确认/ }));
    await screen.findByRole("heading", { name: "deepseek-flash" });
    // The header keeps the board-recorded availability and adds the pending
    // state with its start time; the two are never merged.
    expect(screen.getByText("可用", { selector: ".badge" })).toBeTruthy();
    expect(screen.getByText(`待确认 · 自 ${early}`, { selector: ".badge" })).toBeTruthy();
    expect(screen.queryByText("不可用", { selector: ".badge" })).toBeNull();
    // Every effort tag shows its own pending mark; the family records no
    // unavailability.
    expect(screen.getAllByText(`待确认 · 自 ${early}`).length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText(`待确认 · 自 ${late}`).length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("不可用", { selector: ".unavailable-mark" })).toBeNull();
    // Each effort's read-only facts name its catalog state beside 可用性,
    // pending included, so the three states never merge.
    const factStates = [...document.querySelectorAll(".effort-evaluation .facts dd")]
      .map(dd => dd.textContent);
    expect(factStates.filter(t => t === `待确认 · 自 ${early}`)).toHaveLength(1);
    expect(factStates.filter(t => t === `待确认 · 自 ${late}`)).toHaveLength(1);
    // A pending effort still toggles like any available one.
    const highSwitch = screen.getByRole("switch", { name: "启用 high" });
    expect(highSwitch).toHaveProperty("disabled", false);
    await user.click(highSwitch);
    expect(screen.getByRole("switch", { name: "启用 high" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/1 项未保存/)).toBeTruthy();
    // And it qualifies as Router: no refusal is recorded against pending.
    await user.click(screen.getByRole("button", { name: "high 档位菜单" }));
    const menu = screen.getByRole("dialog", { name: /high 档位设置/ });
    expect(menu.querySelector(".menu-reason")).toBeNull();
  });

  it("shows a pending family without a recorded time as plain 待确认, never an invented date", async () => {
    const f = fixture();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 3" });
    const ghostRow = screen.getByRole("button", { name: "ghost-model，已启用 0/1，待确认" });
    expect(ghostRow.textContent).toContain("待确认");
    expect(ghostRow.textContent).not.toContain("自 ");
    expect(ghostRow.textContent).not.toContain("未记录");
  });

  it("keeps the board's catalog-available state when harness health marks the profile unusable", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.click(screen.getByRole("checkbox", { name: "显示不可用配置（3）" }));
    await screen.findByRole("heading", { name: "模型 5" });
    await user.click(screen.getByRole("button", { name: /^plain-model，已启用 1\/1，不可用（HARNESS_UNHEALTHY）/ }));
    await screen.findByRole("heading", { name: "plain-model" });
    // The 目录状态 row reads the board's explicit catalogStatus: 可用, even
    // though harness health makes this configuration unusable right now.
    const facts = [...document.querySelectorAll(".effort-evaluation .facts")];
    const stateRow = facts.flatMap(fact => [...fact.querySelectorAll("dt")])
      .find(dt => dt.textContent === "目录状态")?.nextElementSibling;
    expect(stateRow?.textContent).toBe("可用");
    // The overall availability stays honest: the header badge and the 可用性
    // row both keep the recorded unavailability.
    expect(screen.getByText("不可用", { selector: ".badge" })).toBeTruthy();
    expect(screen.queryByText("可用", { selector: ".badge" })).toBeNull();
    const factValues = facts.flatMap(fact => [...fact.querySelectorAll("dd")]).map(dd => dd.textContent);
    expect(factValues).toContain("HARNESS_UNHEALTHY");
  });
});
