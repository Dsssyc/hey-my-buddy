import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Annotation, Card, Preference, Profile, ProfilePage, Snapshot, WriterGrant } from "./types";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const retiredMax = "dsh:deepseek-official:retired-model:max";
const retiredLow = "dsh:deepseek-official:retired-model:low";

function catalog(): Profile[] {
  const base = {
    adapter: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision:dsh"],
    contextWindow: null, source: "catalog:fixture", description: "",
  };
  return [
    { ...base, profileId: flashOff, model: "deepseek-flash", effort: "off", label: "deepseek-flash · off", available: true, enabled: true },
    { ...base, profileId: flashHigh, model: "deepseek-flash", effort: "high", label: "deepseek-flash · high", available: true, enabled: false },
    { ...base, profileId: retiredMax, model: "retired-model", effort: "max", label: "retired-model · max",
      available: false, enabled: true, unavailableReason: "provider paused" },
    { ...base, profileId: retiredLow, model: "retired-model", effort: "low", label: "retired-model · low",
      available: false, enabled: false, unavailableReason: "provider paused" },
  ];
}

/**
 * Mirrors the recorded snapshot: available configurations plus the current
 * decision profile. Retired identities are only reachable through the paged
 * `model_profiles` history, exactly like the board exposes them.
 */
function fixture(options: { staleDecision?: boolean; stalePin?: boolean; pageSize?: number } = {}) {
  const profiles = catalog();
  const decisionId = options.staleDecision ? retiredMax : flashOff;
  const cards: Card[] = [
    { profileId: flashOff, revision: 1, summary: "当前评价 flash off", strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null },
    { profileId: retiredMax, revision: 3, summary: "退役前的历史评价", strengths: ["曾稳定"], limitations: [], risks: [], evidenceIds: [], updatedAt: null },
  ];
  const annotations: Annotation[] = [{ profileId: retiredMax, text: "用户保留的历史意见", revision: 2, updatedAt: null }];
  const preferences: Preference[] = options.stalePin
    ? [{ profileId: retiredMax, mode: "pin", reason: "以前固定" }]
    : [];
  const sampleCounts = { [flashOff]: 4, [retiredMax]: 9 };
  const pages = (params: Record<string, any>): ProfilePage => {
    const includeUnavailable = params.includeUnavailable === true;
    const visible = includeUnavailable ? profiles : profiles.filter(p => p.available || p.profileId === decisionId);
    const limit = typeof params.limit === "number" ? params.limit : 100;
    const after = typeof params.after === "string" ? params.after : "";
    const ordered = [...visible].sort((a, b) => a.profileId.localeCompare(b.profileId)).filter(p => p.profileId > after);
    const slice = ordered.slice(0, limit);
    const ids = new Set(slice.map(p => p.profileId));
    return {
      profiles: slice,
      cards: cards.filter(c => ids.has(c.profileId)),
      annotations: annotations.filter(a => ids.has(a.profileId)),
      preferences: preferences.filter(p => ids.has(p.profileId)),
      sampleCounts: Object.fromEntries(Object.entries(sampleCounts).filter(([id]) => ids.has(id))),
      tableRevision: 2,
      nextCursor: ordered.length > slice.length ? slice[slice.length - 1].profileId : null,
    };
  };
  const live = profiles.filter(p => p.available || p.profileId === decisionId);
  let state: Snapshot = {
    csrfToken: "csrf", tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: decisionId },
    profiles: live,
    // The snapshot carries the retained identities only as a count.
    unavailableProfileCount: profiles.filter(p => !p.available).length,
    cards: cards.filter(c => live.some(p => p.profileId === c.profileId)),
    annotations: annotations.filter(a => live.some(p => p.profileId === a.profileId)),
    preferences: preferences.filter(p => live.some(p => p.profileId === p.profileId)),
    evidence: [], decisions: [],
    sampleCounts: Object.fromEntries(Object.entries(sampleCounts).filter(([id]) => live.some(p => p.profileId === id))),
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  const grant: WriterGrant = {
    writerId: "writer", generation: 1, writerToken: "private", phase: "writing", tableRevision: 2,
    expiresAt: new Date(Date.now() + 120000).toISOString(),
  };
  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (operation === "model_profiles") {
      const page = pages({ ...params, limit: options.pageSize ?? params.limit });
      if (options.pageSize) page.nextCursor = page.nextCursor;
      return page;
    }
    if (operation === "evaluation_write_begin") return grant;
    if (operation === "user_policy_publish") {
      published.push(params);
      state = { ...state, tableRevision: state.tableRevision + 1,
        gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null } };
      return { tableRevision: state.tableRevision };
    }
    if (operation === "evaluation_write_abort") return { aborted: true };
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
  } as unknown as ConsoleApi;
  return { api, command, published, operations, snapshot: () => state };
}

async function openRetired(f: ReturnType<typeof fixture>, user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "模型 1" });
  await user.click(screen.getByLabelText("显示不可用配置"));
  await screen.findByRole("heading", { name: "模型 2" });
  await user.click(screen.getByRole("button", { name: /^retired-model/ }));
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("unavailable configurations", () => {
  it("hides them by default and keeps their history readable behind the toggle", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    expect(screen.getByText(/2 个执行配置/)).toBeTruthy();
    // The snapshot keeps retired identities only as a count, never silently dropped.
    expect(screen.getByText("目录中另有 2 个不可用配置保留在历史记录中；勾选“显示不可用配置”可按页查看。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^retired-model/ })).toBeNull();

    await user.click(screen.getByLabelText("显示不可用配置"));
    await screen.findByRole("heading", { name: "模型 2" });
    expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100 }, "csrf");
    await user.click(await screen.findByRole("button", { name: /^retired-model/ }));
    const detail = screen.getByRole("complementary", { name: "评价卡片详情" });
    expect(within(detail).getByText("目录不可用")).toBeTruthy();
    await user.click(within(detail).getByRole("tab", { name: "评价与意见" }));
    expect(within(detail).getAllByText("退役前的历史评价").length).toBeGreaterThan(0);
    expect(within(detail).getAllByText("用户保留的历史意见").length).toBeGreaterThan(0);
    expect(within(detail).getByText(/9 个验证样本/)).toBeTruthy();
    // Inspecting a retired variant never marks it enabled by itself.
    expect(within(detail).getByRole("button", { name: "max，已启用，目录不可用，正在查看" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/4 个执行配置/)).toBeTruthy();
  });

  it("pages the retained history through the server cursor", async () => {
    const f = fixture({ pageSize: 2 });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByLabelText("显示不可用配置"));
    await user.click(await screen.findByRole("button", { name: "加载更多历史配置" }));
    await waitFor(() => expect(f.operations.filter(op => op === "model_profiles")).toHaveLength(2));
    expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, after: expect.any(String) }, "csrf");
    // Both retired efforts are now readable, and the cursor is exhausted.
    await screen.findByRole("heading", { name: "模型 2" });
    expect(screen.getByText(/4 个执行配置/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "加载更多历史配置" })).toBeNull();
  });

  it("allows disabling a retired configuration but never re-enabling it", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    // It is currently enabled, so the user may turn it off.
    const toggle = await screen.findByLabelText("允许后续选择使用此配置");
    expect(toggle).toHaveProperty("disabled", false);
    expect(screen.getByText(/你仍可以停用它、修改意见或偏好依据/)).toBeTruthy();
    await user.click(toggle);
    await screen.findByText("启用状态未保存；发布前正在运行的任务仍按原配置执行。");
    // The disabled variant cannot be switched on, pinned or selected at all.
    await user.click(screen.getByRole("button", { name: "low，未启用，目录不可用" }));
    expect(screen.getByLabelText("允许后续选择使用此配置")).toHaveProperty("disabled", true);
    expect(screen.getByText(/此配置当前不在目录中，不能新启用/)).toBeTruthy();
    const pinOption = within(screen.getByLabelText("用户偏好")).getByRole("option", { name: "固定选择" });
    expect(pinOption).toHaveProperty("disabled", true);
    expect(f.operations).not.toContain("user_policy_publish");
  });

  it("publishes a disable and an opinion as patches without program fields", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    await user.click(await screen.findByLabelText("允许后续选择使用此配置"));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(screen.getByLabelText("我的意见"), "，仍可用于历史对照");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    // The toggle reads history first; the post-publication revision re-reads it.
    expect(f.operations.slice(0, 3)).toEqual(["model_profiles", "evaluation_write_begin", "user_policy_publish"]);
    expect(f.published[0]).toEqual({
      commandId: expect.any(String),
      writerId: "writer",
      generation: 1,
      writerToken: "private",
      expectedRevision: 2,
      profileSettings: [{ profileId: retiredMax, enabled: false }],
      annotationChanges: [{ profileId: retiredMax, text: "用户保留的历史意见，仍可用于历史对照" }],
    });
    const wire = JSON.stringify(f.published[0]);
    // Availability, provider and model are program-owned and are never uploaded.
    expect(wire).not.toContain("available");
    expect(wire).not.toContain("provider");
    expect(wire).not.toContain("\"model\"");
    expect(wire).not.toContain("cards");
  });
});

describe("stale settings and unrelated saves", () => {
  it("shows a stale decision setting as attention and still saves an opinion elsewhere", async () => {
    const f = fixture({ staleDecision: true });
    const user = userEvent.setup();
    window.location.hash = "#settings";
    render(<App suppliedApi={f.api} />);
    const select = await screen.findByLabelText("决策模型配置");
    expect(select).toHaveProperty("value", retiredMax);
    const staleOption = within(select).getByRole("option", { name: /retired-model · max（需要处理）/ });
    expect(staleOption).toHaveProperty("disabled", true);
    await screen.findByText(/需要处理，但不影响保存其他修改/);
    // The selector is disabled while read-only; entering edit mode never takes a grant.
    expect(select).toHaveProperty("disabled", true);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await waitFor(() => expect(screen.getByLabelText("决策模型配置")).toHaveProperty("disabled", false));
    expect(f.command).not.toHaveBeenCalled();

    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    await user.click(await screen.findByRole("button", { name: /^deepseek-flash/ }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "只改这条意见");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    // The stale selector stays untouched and does not block the unrelated patch.
    expect(f.published[0].configuration).toBeUndefined();
    expect(f.published[0].preferenceChanges).toBeUndefined();
    expect(f.published[0].annotationChanges).toEqual([{ profileId: flashOff, text: "只改这条意见" }]);
  });

  it("keeps a stale pin visible without requiring a repair before saving", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await screen.findByText(/固定选择指向 .*需要处理，但不影响保存其他修改/);
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(screen.getByLabelText("我的意见"), "，补充说明");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0]).not.toHaveProperty("preferenceChanges");
    expect(f.published[0].annotationChanges).toEqual([
      { profileId: retiredMax, text: "用户保留的历史意见，补充说明" },
    ]);
  });
});
