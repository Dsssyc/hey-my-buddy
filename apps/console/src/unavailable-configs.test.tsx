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
    adapter: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision"],
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
  // The retained page is bound to the published revision it was read at.
  let revision = 2;
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
      modelConcurrency: [],
      tableRevision: revision,
      nextCursor: ordered.length > slice.length ? slice[slice.length - 1].profileId : null,
    };
  };
  const live = profiles.filter(p => p.available || p.profileId === decisionId);
  let state: Snapshot = {
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: revision,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: decisionId },
    profiles: live,
    // `unavailableProfileCount` counts every unavailable row in the table,
    // including a listed one such as the retained decision profile; the console
    // subtracts what it has loaded and pages the rest.
    unavailableProfileCount: profiles.filter(p => !p.available).length,
    cards: cards.filter(c => live.some(p => p.profileId === c.profileId)),
    annotations: annotations.filter(a => live.some(p => p.profileId === a.profileId)),
    preferences: preferences.filter(p => live.some(p => p.profileId === p.profileId)),
    evidence: [], decisions: [],
    sampleCounts: Object.fromEntries(Object.entries(sampleCounts).filter(([id]) => live.some(p => p.profileId === id))),
    modelConcurrency: [],
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
      revision = state.tableRevision;
      return { tableRevision: state.tableRevision };
    }
    if (operation === "model_catalog_refresh") {
      // A completed program discovery publishes directory facts and a new
      // revision; the bounded snapshot still omits retained identities.
      revision += 1;
      state = { ...state, tableRevision: revision };
      return { tableRevision: revision };
    }
    if (operation === "evaluation_write_abort") return { aborted: true };
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
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

  it("counts a listed offline decision selector once without double-counting loaded rows", async () => {
    const f = fixture({ staleDecision: true, pageSize: 2 });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    // The snapshot lists the unavailable decision selector (retired-model · max)
    // and `unavailableProfileCount` counts it too, so exactly the one still
    // unloaded retained identity may be reported.
    expect(await screen.findByText("目录中另有 1 个不可用配置保留在历史记录中；勾选“显示不可用配置”可按页查看。")).toBeTruthy();
    await user.click(screen.getByLabelText("显示不可用配置"));
    await screen.findByRole("heading", { name: "模型 2" });
    // The first retained page contains only rows the snapshot already lists.
    await user.click(await screen.findByRole("button", { name: "加载更多历史配置" }));
    await waitFor(() => expect(screen.getByText(/4 个执行配置/)).toBeTruthy());
    // Every unavailable row is loaded now; switching the toggle back off must
    // report none left over instead of counting the listed selector twice.
    await user.click(screen.getByLabelText("显示不可用配置"));
    expect(screen.queryByText(/目录中另有/)).toBeNull();
    expect(screen.getByText(/已隐藏 2 个不可用配置/)).toBeTruthy();
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
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
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
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
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

  it("allows a reason-only edit of a retired pin while new pins stay blocked", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await screen.findByText(/固定选择指向 .*需要处理，但不影响保存其他修改/);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    // The existing pin stays a reachable, selectable value; only its reason changes.
    const pinOption = within(screen.getByLabelText("用户偏好")).getByRole("option", { name: "固定选择" });
    expect(pinOption).toHaveProperty("disabled", false);
    const reason = await screen.findByLabelText("偏好依据");
    expect(reason).toHaveProperty("value", "以前固定");
    await user.clear(reason);
    await user.type(reason, "仍然适用");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0].preferenceChanges).toEqual([
      { profileId: retiredMax, mode: "pin", reason: "仍然适用" },
    ]);
    expect(f.published[0]).not.toHaveProperty("profileSettings");
  });

  it("keeps a retained disable unresolved until the fresh history is re-read", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    await user.click(await screen.findByLabelText("允许后续选择使用此配置"));
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新/);
    // The retired row is absent from the bounded V3 snapshot: the edit stays
    // unresolved instead of being dropped or rebased from the V2 cache.
    const banner = document.querySelector(".conflict-banner")!;
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("尚不能与 V3 核对");
    expect(screen.getByLabelText("允许后续选择使用此配置")).toHaveProperty("checked", false);
    // No save of the disable under V3 while the conflict is unresolved.
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect((await screen.findAllByText(/设置已在别处更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // The revision-bound history reload supplies the fresh V3 row; the pending
    // rebase resolves on its own and the same edit publishes at V3.
    await waitFor(() => expect(document.querySelector(".conflict-banner")).toBeNull());
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0]).toMatchObject({
      expectedRevision: 3,
      profileSettings: [{ profileId: retiredMax, enabled: false }],
    });
  });

  it("does not resurrect a preference the user removed when a page is re-read", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    const select = screen.getByLabelText("用户偏好");
    expect(select).toHaveProperty("value", "pin");
    await user.selectOptions(select, "");
    expect(select).toHaveProperty("value", "");
    // A same-revision retained page still contains the published pin. Reading it
    // (here a search-filter reload) must not silently re-add the removed row.
    await user.type(screen.getByPlaceholderText("搜索模型、Harness、提供方"), "retired");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, query: "retired" }, "csrf"));
    await waitFor(() => expect(f.operations.filter(op => op === "model_profiles").length).toBeGreaterThan(1));
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0].preferenceChanges).toEqual([
      { profileId: retiredMax, mode: null, reason: "" },
    ]);
  });

  it("sends the search and harness filter to the retained-history request", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByLabelText("显示不可用配置"));
    await screen.findByRole("heading", { name: "模型 2" });
    expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100 }, "csrf");
    // A search is a bounded server query, so matching rows past the 600-item
    // display budget are reachable instead of a local-only filter.
    await user.type(screen.getByPlaceholderText("搜索模型、Harness、提供方"), "retired");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, query: "retired" }, "csrf"));
    await user.selectOptions(screen.getByLabelText("Harness 筛选"), "dsh");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, query: "retired", adapter: "dsh" }, "csrf"));
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
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
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
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
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
