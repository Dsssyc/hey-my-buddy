import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Card, FamilyAnnotation, Preference, Profile, ProfilePage, Snapshot, WriterGrant } from "./types";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const retiredMax = "dsh:deepseek-official:retired-model:max";
const retiredLow = "dsh:deepseek-official:retired-model:low";
const retiredFamily = { adapter: "dsh", provider: "deepseek-official", model: "retired-model" };

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
  const familyAnnotations: FamilyAnnotation[] = [{ ...retiredFamily, text: "用户保留的历史意见", revision: 2, updatedAt: null }];
  const preferenceOverrides = options.stalePin
    ? [{ profileId: retiredMax, mode: "pin" as const, reason: "以前固定" }]
    : [];
  const preferences: Preference[] = options.stalePin
    ? [{ profileId: retiredMax, mode: "pin" as const, reason: "以前固定", source: "override" as const }]
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
      familyAnnotations: ids.has(retiredMax) || ids.has(retiredLow) ? familyAnnotations : [],
      preferences: preferences.filter(p => ids.has(p.profileId)),
      preferenceOverrides: preferenceOverrides.filter(p => ids.has(p.profileId)),
      familyPreferences: [],
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
    familyAnnotations: live.some(p => p.profileId === retiredMax || p.profileId === retiredLow) ? familyAnnotations : [],
    preferences: preferences.filter(p => live.some(x => x.profileId === p.profileId)),
    preferenceOverrides: preferenceOverrides.filter(p => live.some(x => x.profileId === p.profileId)),
    familyPreferences: [],
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
  await user.click(screen.getByRole("button", { name: "查看历史配置" }));
  await screen.findByRole("heading", { name: "模型 2" });
  await user.click(screen.getByRole("button", { name: /^retired-model/ }));
  await screen.findByRole("heading", { name: "retired-model" });
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("unavailable configurations", () => {
  it("hides them by default and keeps their history readable behind 查看历史配置", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    // The snapshot keeps retired identities only as a count, never silently dropped.
    expect(screen.getByText(/另有 2 个不可用配置保留在历史中/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^retired-model/ })).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看历史配置" }));
    await screen.findByRole("heading", { name: "模型 2" });
    expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100 }, "csrf");
    await user.click(await screen.findByRole("button", { name: /^retired-model/ }));
    await screen.findByRole("heading", { name: "retired-model" });
    expect(screen.getByText("不可用", { selector: ".badge" })).toBeTruthy();
    expect(screen.getAllByText(/退役前的历史评价/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/用户保留的历史意见/).length).toBeGreaterThan(0);
    expect(screen.getByText(/验证样本 9 个/)).toBeTruthy();
  });

  it("counts a listed offline decision selector once without double-counting loaded rows", async () => {
    const f = fixture({ staleDecision: true, pageSize: 2 });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    // The listed decision selector already makes "retired-model" a visible
    // family; only its other retired effort ("low") is still unlisted.
    await screen.findByRole("heading", { name: "模型 2" });
    // The snapshot lists the unavailable decision selector (retired-model · max)
    // and `unavailableProfileCount` counts it too, so exactly the one still
    // unloaded retained identity may be reported.
    expect(await screen.findByText(/另有 1 个不可用配置保留在历史中/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "查看历史配置" }));
    // The first retained page contains only rows the snapshot already lists.
    await user.click(await screen.findByRole("button", { name: "加载更多历史配置" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "加载更多历史配置" })).toBeNull());
    // Every unavailable row is loaded now: the "另有" note disappears.
    expect(screen.queryByText(/另有/)).toBeNull();
  });

  it("pages the retained history through the server cursor", async () => {
    const f = fixture({ pageSize: 2 });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("button", { name: "查看历史配置" }));
    await user.click(await screen.findByRole("button", { name: "加载更多历史配置" }));
    await waitFor(() => expect(f.operations.filter(op => op === "model_profiles")).toHaveLength(2));
    expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, after: expect.any(String) }, "csrf");
    // Both retired efforts are now readable, and the cursor is exhausted.
    await screen.findByRole("heading", { name: "模型 2" });
    expect(screen.queryByRole("button", { name: "加载更多历史配置" })).toBeNull();
  });

  it("allows disabling a retired configuration but never re-enabling it", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    // "max" is currently enabled, so the user may turn it off.
    const maxSwitch = screen.getByRole("switch", { name: "启用 max" });
    expect(maxSwitch).toHaveProperty("disabled", false);
    await user.click(maxSwitch);
    expect(screen.getByRole("switch", { name: "启用 max" }).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/有 1 项未保存修改/)).toBeTruthy();
    // "low" is disabled and unavailable: it cannot be switched on at all.
    const lowSwitch = screen.getByRole("switch", { name: "启用 low" });
    expect(lowSwitch).toHaveProperty("disabled", true);
    expect(lowSwitch).toHaveProperty("title", "该档位当前不可用，不能新启用");
    expect(f.operations).not.toContain("user_policy_publish");
  });

  it("publishes a disable and a family note as patches without program fields", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await user.click(screen.getByRole("switch", { name: "启用 max" }));
    await user.type(screen.getByLabelText("家族备注"), "，仍可用于历史对照");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.operations.slice(0, 3)).toEqual(["model_profiles", "evaluation_write_begin", "user_policy_publish"]);
    expect(f.published[0]).toEqual({
      commandId: expect.any(String),
      writerId: "writer",
      generation: 1,
      writerToken: "private",
      expectedRevision: 2,
      profileSettings: [{ profileId: retiredMax, enabled: false }],
      familyAnnotationChanges: [{ ...retiredFamily, text: "用户保留的历史意见，仍可用于历史对照" }],
    });
    const wire = JSON.stringify(f.published[0]);
    // Availability, provider and model are program-owned and are never uploaded.
    expect(wire).not.toContain("available");
    expect(wire).not.toContain("cards");
  });

  it("allows a reason-only edit of a retired pin while a new pin on an unavailable effort stays blocked", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await screen.findByText(/固定选择指向.*请启用该档位，或把它的偏好改回/);
    // The existing pin stays a reachable, selectable value on its own tag menu;
    // only its reason changes.
    await user.click(screen.getByRole("button", { name: "max 档位菜单" }));
    const reason = await screen.findByLabelText("覆盖理由");
    expect(reason).toHaveProperty("value", "以前固定");
    await user.clear(reason);
    await user.type(reason, "仍然适用");
    // "low" has no existing pin and is unavailable and disabled: a new pin there is blocked.
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "low 档位菜单" }));
    const lowMenu = screen.getByRole("dialog", { name: /low 档位设置/ });
    const lowPin = within(lowMenu).getByRole("radio", { name: "固定" });
    expect(lowPin).toHaveProperty("disabled", true);
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "保存" }));
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
    await user.click(screen.getByRole("switch", { name: "启用 max" }));
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新/);
    // The retired row is absent from the bounded V3 snapshot: the edit stays
    // unresolved instead of being dropped or rebased from the V2 cache.
    const banner = document.querySelector(".conflict-banner")!;
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("尚不能与 V3 核对");
    expect(screen.getByRole("switch", { name: "启用 max" }).getAttribute("aria-checked")).toBe("false");
    // No save of the disable under V3 while the conflict is unresolved.
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(/设置已在别处更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // The revision-bound history reload supplies the fresh V3 row; the pending
    // rebase resolves on its own and the same edit publishes at V3.
    await waitFor(() => expect(document.querySelector(".conflict-banner")).toBeNull());
    await user.click(screen.getByRole("button", { name: "保存" }));
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
    await user.click(screen.getByRole("button", { name: "max 档位菜单" }));
    await user.click(screen.getByRole("radio", { name: "跟随家族（无）" }));
    await user.keyboard("{Escape}");
    // A same-revision retained page still contains the published pin. Reading it
    // (here a search-filter reload) must not silently re-add the removed row.
    await user.type(screen.getByPlaceholderText("搜索模型、Harness、提供方"), "retired");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, query: "retired" }, "csrf"));
    await waitFor(() => expect(f.operations.filter(op => op === "model_profiles").length).toBeGreaterThan(1));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0].preferenceChanges).toEqual([
      { profileId: retiredMax, mode: null, reason: "" },
    ]);
  });

  it("sends the search text to the retained-history request", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("button", { name: "查看历史配置" }));
    await screen.findByRole("heading", { name: "模型 2" });
    expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100 }, "csrf");
    // A search is a bounded server query, so matching rows past the display
    // budget are reachable instead of a local-only filter.
    await user.type(screen.getByPlaceholderText("搜索模型、Harness、提供方"), "retired");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("model_profiles",
      { includeUnavailable: true, limit: 100, query: "retired" }, "csrf"));
  });
});

describe("stale settings and unrelated saves", () => {
  it("shows a stale decision setting as attention and still saves a family note elsewhere", async () => {
    const f = fixture({ staleDecision: true });
    const user = userEvent.setup();
    window.location.hash = "#buddy";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: "详情" }));
    expect(screen.getByText(/需要处理/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "只改这条意见");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    // The stale selector stays untouched and does not block the unrelated patch.
    expect(f.published[0].configuration).toBeUndefined();
    expect(f.published[0].preferenceChanges).toBeUndefined();
    expect(f.published[0].familyAnnotationChanges).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "只改这条意见" },
    ]);
  });

  it("keeps a stale pin visible without requiring a repair before saving", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await openRetired(f, user);
    await screen.findByText(/固定选择指向.*请启用该档位，或把它的偏好改回/);
    await user.click(screen.getByRole("button", { name: "返回模型列表" }));
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "，补充说明");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0]).not.toHaveProperty("preferenceChanges");
    expect(f.published[0].familyAnnotationChanges).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "，补充说明" },
    ]);
  });
});
