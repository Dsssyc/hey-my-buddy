import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, type ConsoleApi } from "./api";
import type { Profile, Snapshot, WriterGrant } from "./types";

/** One family with an enabled and a disabled effort, plus a second enabled family. */
function catalog(): Profile[] {
  const entries = [
    { model: "deepseek-flash", effort: "off", enabled: true },
    { model: "deepseek-flash", effort: "high", enabled: false },
    { model: "deepseek-v4-pro", effort: "max", enabled: true },
  ];
  return entries.map(({ model, effort, enabled }) => ({
    profileId: `dsh:deepseek-official:${model}:${effort}`,
    label: `${model} · ${effort}`,
    adapter: "dsh", provider: "deepseek-official", model, effort,
    available: true, enabled, capabilities: [], contextWindow: 1000000,
    source: "catalog:fixture", description: "",
  }));
}
const flashOff = "dsh:deepseek-official:deepseek-flash:off";
/** A configuration that only a completed program discovery can add. */
const discovered = "dsh:deepseek-official:deepseek-v5:medium";

type Outcome = "begin" | "renew" | "publish" | "abort";
type Script = Partial<Record<Outcome, ("network" | "conflict" | "queued" | "active" | "drift" | "forbidden")[]>>;

function fixture(script: Script = {}, options: { queuedForever?: boolean; discoveryAnnotation?: string } = {}) {
  const profiles = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: flashOff },
    profiles,
    cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, summary: `原评价 ${p.model} ${p.effort}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    annotations: [{ profileId: flashOff, text: "原人工意见", revision: 1, updatedAt: null }],
    preferences: [], evidence: [], decisions: [],
    sampleCounts: { [flashOff]: 5, "dsh:deepseek-official:deepseek-flash:high": 2 },
    modelConcurrency: [
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 },
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-v4-pro", limit: 5, active: 3 },
    ],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing",
    state: "active", tableRevision: 2, expiresAt: new Date(Date.now() + 120000).toISOString() };
  const queue: Record<Outcome, string[]> = { begin: [], renew: [], publish: [], abort: [] };
  for (const key of Object.keys(queue) as Outcome[]) queue[key] = [...(script[key] || [])];
  const take = (key: Outcome) => queue[key].shift();
  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const aborted: Record<string, any>[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (operation === "evaluation_write_begin") {
      const scripted = take("begin");
      if (scripted === "network") throw new ApiError("NETWORK", "lost begin reply");
      if (scripted === "conflict") throw new ApiError("REVISION_CONFLICT", "stale revision");
      if (scripted === "queued" || scripted === "drift") {
        state = { ...state, gate: { phase: "draining", readers: 1, waitingWriters: 1, writer: null } };
        return { ...grant, state: "waiting", phase: "draining", queuePosition: 2,
          ...(scripted === "drift" ? { tableRevision: 3 } : {}) };
      }
      state = { ...state, gate: { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } } };
      return grant;
    }
    if (operation === "evaluation_write_renew") {
      const scripted = take("renew");
      if (scripted === "network") throw new ApiError("NETWORK", "lost renew reply");
      if (options.queuedForever || scripted === "queued")
        return { ...grant, state: "waiting", phase: "draining", queuePosition: 2 };
      state = { ...state, gate: { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } } };
      return { ...grant, state: "active", phase: "writing" };
    }
    if (operation === "user_policy_publish") {
      published.push(params);
      const scripted = take("publish");
      if (scripted === "network") throw new ApiError("NETWORK", "lost publish reply");
      if (scripted === "conflict") {
        state = { ...state, tableRevision: state.tableRevision + 1,
          annotations: [...state.annotations.filter(a => a.profileId !== flashOff),
            { profileId: flashOff, text: "他人发布的人工意见 V3", revision: 2, updatedAt: null }] };
        throw new ApiError("REVISION_CONFLICT", "stale revision");
      }
      state = { ...state, tableRevision: state.tableRevision + 1,
        gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null } };
      return { published: true, tableRevision: state.tableRevision };
    }
    if (operation === "evaluation_write_abort") {
      aborted.push(params);
      const scripted = take("abort");
      if (scripted === "network") throw new ApiError("NETWORK", "lost abort reply");
      if (scripted === "forbidden") throw new ApiError("FORBIDDEN", "console session refused");
      return { aborted: true };
    }
    if (operation === "model_catalog_refresh") {
      // The program publishes the directory itself; the page only re-reads it.
      // A discovery can also publish another writer's human field, which must
      // never be silently overwritten by a stale local draft.
      state = { ...state, tableRevision: state.tableRevision + 1,
        ...(options.discoveryAnnotation ? {
          annotations: [...state.annotations.filter(a => a.profileId !== flashOff),
            { profileId: flashOff, text: options.discoveryAnnotation, revision: 2, updatedAt: null }],
        } : {}),
        profiles: [...state.profiles, { profileId: discovered, label: "deepseek-v5 · medium", adapter: "dsh",
          provider: "deepseek-official", model: "deepseek-v5", effort: "medium", available: true, enabled: false,
          capabilities: [], contextWindow: null, source: "catalog:refresh", description: "" }] };
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
  return { api, command, operations, published, aborted,
    snapshot: () => state,
    setSnapshot: (next: Snapshot) => { state = next; } };
}

async function openModels(api: ConsoleApi, user: ReturnType<typeof userEvent.setup>) {
  window.location.hash = "#models";
  render(<App suppliedApi={api} />);
  await screen.findByRole("heading", { name: "模型 2" });
  await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("edit mode and the write lease", () => {
  it("enters edit mode locally and only takes the grant on save", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    const editSwitch = screen.getByRole("switch", { name: "编辑设置" });
    expect(editSwitch.getAttribute("aria-checked")).toBe("false");
    await user.click(editSwitch);
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("编辑中")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    const summary = await screen.findByLabelText("我的意见");
    await user.clear(summary);
    await user.type(summary, "本地草稿，未发布");
    expect(screen.getByText(/编辑中 · 未保存 · 1 个配置/)).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await waitFor(() => expect(f.operations).toEqual(["evaluation_write_begin", "user_policy_publish"]));
    expect(f.published[0]).toMatchObject({ expectedRevision: 2, writerId: "writer" });
    // Only the changed human opinion is published: no profiles table and no card.
    expect(f.published[0].annotationChanges).toEqual([{ profileId: flashOff, text: "本地草稿，未发布" }]);
    expect(f.published[0]).not.toHaveProperty("profiles");
    expect(f.published[0]).not.toHaveProperty("cards");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("false");
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
  });

  it("leaves edit mode directly when nothing changed and returns to read text", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    expect(await screen.findByLabelText("我的意见")).toBeTruthy();
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByLabelText("我的意见")).toBeNull();
    expect(screen.getByText("开启右上角“编辑设置”后可以写下或清除人工意见；已发布的评价内容始终只读。")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("never publishes an empty patch when the draft matches the published table", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("没有需要保存的用户修改。");
    expect(f.command).not.toHaveBeenCalled();
    expect(f.published).toHaveLength(0);
  });

  it("reuses the same begin request when the grant reply was lost", async () => {
    const f = fixture({ begin: ["network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "补充");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/尚未确认编辑资格请求的结果/);
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await waitFor(() => expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(2));
    const begins = f.command.mock.calls.filter(([op]) => op === "evaluation_write_begin");
    expect(begins[1][1]).toEqual(begins[0][1]);
    expect(f.operations.at(-1)).toBe("user_policy_publish");
  });

  it("waits behind another writer and releases the queued intent on cancel", async () => {
    const f = fixture({ begin: ["queued"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "排队中的草稿");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await waitFor(() => expect(f.operations).toContain("evaluation_write_abort"), { timeout: 4000 });
    await screen.findByText("已取消等待，编辑资格已释放；草稿保持不变。");
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见排队中的草稿");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("true");
    expect(f.operations).not.toContain("user_policy_publish");
  });

  it("publishes after the queued intent becomes active", async () => {
    const f = fixture({ begin: ["queued"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "等待后发布");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。", {}, { timeout: 5000 });
    expect(f.operations).toEqual(["evaluation_write_begin", "evaluation_write_renew", "user_policy_publish"]);
    expect(f.published[0]).toMatchObject({ writerId: "writer", generation: 1 });
  });

  it("releases a superseded intent before requesting a fresh one", async () => {
    const f = fixture({ begin: ["drift"], renew: ["network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "补充");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/尚未确认编辑资格状态/, {}, { timeout: 4000 });
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await waitFor(() => expect(f.operations.filter(op => op === "evaluation_write_abort")).toHaveLength(1));
    const begins = f.operations.reduce<number[]>((all, op, index) => op === "evaluation_write_begin" ? [...all, index] : all, []);
    expect(begins).toHaveLength(2);
    expect(f.operations.indexOf("evaluation_write_abort")).toBeLessThan(begins[1]);
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
  });

  it("keeps the same publication identity when the publish reply was lost", async () => {
    const f = fixture({ publish: ["network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "结果不明");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/保存结果未确认/);
    // The draft and its staged payload survive an edit freeze until confirmed.
    expect(screen.getAllByText("原人工意见结果不明").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/编辑中 · 未保存/).length).toBeGreaterThan(0);
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published).toHaveLength(2);
    expect(f.published[1]).toEqual(f.published[0]);
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(1);
  });

  it("keeps a conflicting draft and offers a deliberate reload or discard", async () => {
    const f = fixture({ publish: ["conflict"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    const summary = await screen.findByLabelText("我的意见");
    await user.clear(summary);
    await user.type(summary, "基于 V2 的草稿");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    const banner = (await screen.findAllByRole("alert")).find(node => node.className.includes("conflict-banner"))!;
    expect(banner).toBeTruthy();
    expect(within(banner).getByText(/设置已在别处更新/)).toBeTruthy();
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "基于 V2 的草稿");
    expect(f.operations).toContain("evaluation_write_abort");
    // Deliberate reload adopts the latest publication and keeps editing.
    await user.click(within(banner).getByRole("button", { name: "重新加载最新版本" }));
    await screen.findByText("已加载评价表 V3。请核对后重新保存。");
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "他人发布的人工意见 V3");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("true");
    await user.type(screen.getByLabelText("我的意见"), "（复核）");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published.at(-1)).toMatchObject({ expectedRevision: 3 });
  });

  it("asks before leaving edit mode with unsaved changes", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "未保存的补充");
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    const dialog = await screen.findByRole("dialog", { name: "有未保存的修改" });
    await user.click(within(dialog).getByRole("button", { name: "继续编辑" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见未保存的补充");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("true");
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(await within(await screen.findByRole("dialog")).findByRole("button", { name: "放弃修改" }));
    await screen.findByText("已放弃未发布的修改。");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByLabelText("我的意见")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();

    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "保存并退出");
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(await within(await screen.findByRole("dialog")).findByRole("button", { name: "保存并退出" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("false");
  });

  it("keeps drafts editable offline while save refuses and explains why", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), capabilities: { evaluationWriteGate: false } });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "离线草稿");
    const save = screen.getByRole("button", { name: "保存更改" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    await user.click(save);
    expect(f.command).not.toHaveBeenCalled();
    expect((await screen.findAllByText(/没有评价表写入资格/)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见离线草稿");
  });

  it("explains a guarded save from the keyboard without sending a request", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), capabilities: { evaluationWriteGate: false } });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    const save = screen.getByRole("button", { name: "保存更改" });
    save.focus();
    expect(document.activeElement).toBe(save);
    await user.keyboard("{Enter}");
    expect(f.command).not.toHaveBeenCalled();
    expect((await screen.findAllByText(/没有评价表写入资格/)).length).toBeGreaterThan(0);
  });

  it("refreshes program directory facts on discovery without uploading a draft", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新：新增 1 个配置/);
    expect(f.operations).toEqual(["model_catalog_refresh"]);
    expect(f.published).toHaveLength(0);
    // The program publication is visible without entering edit mode, starts
    // disabled and never uploads the local table.
    expect(await screen.findByRole("heading", { name: "模型 3" })).toBeTruthy();
    const added = f.snapshot().profiles.find(p => p.profileId === discovered);
    expect(added).toBeTruthy();
    expect(added!.enabled).toBe(false);
  });

  it("keeps a conflicting draft at its own revision when discovery finds another value", async () => {
    const f = fixture({}, { discoveryAnnotation: "他人发现期间发布" });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "，我的草稿");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新/);
    // Field-level three-way check: C was published for the same field our draft
    // changes, so the draft keeps B and its own expectedRevision.
    const banner = document.querySelector<HTMLElement>(".conflict-banner")!;
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("已被其他发布修改");
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见，我的草稿");
    expect(f.published).toHaveLength(0);
    // Saving B under V3 is refused without an explicit conflict resolution.
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect((await screen.findAllByText(/设置已在别处更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // Deliberate reload is the explicit resolution: it adopts the other value.
    await user.click(within(banner).getByRole("button", { name: "重新加载最新版本" }));
    await screen.findByText("已加载评价表 V3。请核对后重新保存。");
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "他人发现期间发布");
  });

  it("keeps a dirty old-version draft for CAS conflict when a poll finds a publication", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "，未保存");
    // Another writer published V3; the 3s poll (here the refresh button) only
    // re-reads state and must not rebase, rewrite or discard the local draft.
    f.setSnapshot({ ...f.snapshot(), tableRevision: 3,
      annotations: [{ profileId: flashOff, text: "他人 V3", revision: 2, updatedAt: null }] });
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    const banner = document.querySelector<HTMLElement>(".conflict-banner")!;
    expect(banner).toBeTruthy();
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见，未保存");
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("true");
    expect(f.published).toHaveLength(0);
    // No save of the stale draft under V3 without an explicit resolution.
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    expect((await screen.findAllByText(/设置已在别处更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // Discard is the deliberate resolution; nothing was published implicitly.
    await user.click(within(banner).getByRole("button", { name: "放弃修改" }));
    await screen.findByText("已放弃未发布的修改。");
    expect(f.operations).not.toContain("user_policy_publish");
  });

  it("keeps an unsaved opinion while discovery refreshes the directory", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "，保留的意见");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText(/目录已更新：新增 1 个配置/);
    expect(f.operations).toEqual(["model_catalog_refresh"]);
    expect(f.published).toHaveLength(0);
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见，保留的意见");
    // The rebased draft publishes only the human patch at the refreshed revision.
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published[0]).toMatchObject({ expectedRevision: 3 });
    expect(f.published[0].annotationChanges).toEqual([{ profileId: flashOff, text: "原人工意见，保留的意见" }]);
    expect(f.published[0]).not.toHaveProperty("profiles");
  });

  it("keeps evidence read-only in edit mode and never records an observation", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), evidence: [{ evidenceId: "ev-1", profileId: flashOff, kind: "observation",
      summary: "单次观察", project: "fixture", conditions: ["React"], source: "user", runId: null,
      createdAt: "2026-09-22T00:00:00Z" }] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "证据" }));
    const section = await screen.findByRole("region", { name: "评价证据（只读）" });
    expect(within(section).getByText("单次观察")).toBeTruthy();
    expect(within(section).queryByRole("textbox")).toBeNull();
    expect(within(section).queryByRole("checkbox")).toBeNull();
    expect(within(section).queryByRole("button")).toBeNull();
    expect(f.operations).not.toContain("evaluation_evidence_record");
  });

  it("shows the required muted message when no evidence exists", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("tab", { name: "证据" }));
    const empty = await screen.findByText("暂无评价证据。你可以让已配置 hey-my-buddy skill 的 Harness 执行一次模型评价更新，或在该 Harness 中设置定时更新任务。");
    expect(empty.className).toContain("muted");
  });

  it("keeps enabled and inspected efforts independent and labels both states", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    const enabled = screen.getByRole("button", { name: "非思考，已启用，正在查看" });
    expect(enabled.getAttribute("aria-pressed")).toBe("true");
    expect(enabled.textContent).toContain("已启用");
    const disabled = screen.getByRole("button", { name: "high，未启用" });
    expect(disabled.getAttribute("aria-pressed")).toBe("false");
    expect(disabled.textContent).not.toContain("已启用");
    expect(screen.getByText(/5 个验证样本/)).toBeTruthy();
    expect(screen.getByText(/已设为决策模型/)).toBeTruthy();
    await user.click(disabled);
    expect(screen.getByRole("button", { name: "high，未启用，正在查看" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "非思考，已启用" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByText(/2 个验证样本/)).toBeTruthy();
    expect(screen.getByText(/当前查看：deepseek-flash \/ high/)).toBeTruthy();
  });

  it("marks draft enablement as unsaved until publication without claiming tasks changed", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    await user.click(await screen.findByLabelText("允许后续选择使用此配置"));
    await screen.findByText("启用状态未保存；发布前正在运行的任务仍按原配置执行。");
    // An enablement edit is reported even though this profile already has a card.
    await screen.findByText(/编辑中 · 未保存 · 1 个配置/);
    expect(screen.getByRole("button", { name: /^非思考，未启用，未保存/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(screen.queryByText(/启用状态未保存/)).toBeNull();
    // A disable publishes one enablement patch and nothing program-owned.
    expect(f.published.at(-1)!.profileSettings).toEqual([{ profileId: flashOff, enabled: false }]);
    expect(f.published.at(-1)).not.toHaveProperty("profiles");
    expect(f.published.at(-1)).not.toHaveProperty("cards");
  });

  it("shows the publication history read-only and pages it without model calls", async () => {
    const f = fixture();
    const recent = Array.from({ length: 20 }, (_, index) => ({ revision: 40 - index, kind: "human",
      actor: "codex-buddy", counts: { profiles: 12, cards: 8, preferences: 2 }, createdAt: "2026-09-24T10:00:00Z" }));
    const history = vi.fn(async (_operation: string, params: { before?: number }) => params.before === 21
      ? { revisions: [{ revision: 20, kind: "maintenance", actor: "dsh",
          counts: { profiles: 9, cards: 6, preferences: 1, provided: ["profiles", "cards"] }, createdAt: "2026-09-01T10:00:00Z" }],
        nextCursor: null, total: 41 }
      : { revisions: recent, nextCursor: 21, total: 41 });
    const api = { snapshot: f.api.snapshot, tasks: f.api.tasks, task: f.api.task, command: history } as unknown as ConsoleApi;
    const user = userEvent.setup();
    await openModels(api, user);
    await user.click(screen.getByRole("button", { name: "更新记录" }));
    await screen.findByText(/已发布版本 · 41/);
    expect(screen.getAllByText(/配置 12 · 卡片 8 · 偏好 2/)).toHaveLength(20);
    expect(history).toHaveBeenCalledWith("evaluation_history", { limit: 20 }, "csrf");
    expect(screen.queryByRole("button", { name: "请求整理" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "加载更早记录" }));
    await screen.findByText(/配置 9 · 卡片 6 · 偏好 1 · 提供：profiles、cards/);
    expect(history).toHaveBeenCalledWith("evaluation_history", { limit: 20, before: 21 }, "csrf");
    expect(history.mock.calls.every(([operation]) => operation === "evaluation_history")).toBe(true);
  });

  it("renders human patch counts from the new publication history", async () => {
    const f = fixture();
    const history = vi.fn(async () => ({ revisions: [{ revision: 12, kind: "human", actor: "console",
      counts: { profileSettings: 1, preferenceChanges: 1, annotationChanges: 2,
        provided: ["annotationChanges", "preferenceChanges", "profileSettings"] },
      createdAt: "2026-09-25T10:00:00Z" }], nextCursor: null, total: 1 }));
    const api = { snapshot: f.api.snapshot, tasks: f.api.tasks, task: f.api.task, command: history } as unknown as ConsoleApi;
    const user = userEvent.setup();
    await openModels(api, user);
    await user.click(screen.getByRole("button", { name: "更新记录" }));
    await screen.findByText(/已发布版本 · 1/);
    // Human publications count their patch fields; nothing is rendered as undefined.
    expect(screen.getByText("启用补丁 1 · 偏好补丁 1 · 意见补丁 2 · 提供：annotationChanges、preferenceChanges、profileSettings")).toBeTruthy();
    expect(history).toHaveBeenCalledExactlyOnceWith("evaluation_history", { limit: 20 }, "csrf");
  });

  it("renders a real switch track with read-only/editing state and scopes the tint to model and settings", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("link", { name: "委派记录" }));
    await user.click(screen.getByRole("link", { name: "路由配置" }));
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    // An open gate is normal, so no "评价表可读" badge is needed.
    expect(screen.queryByText("评价表可读")).toBeNull();
    expect(screen.queryByText("独占编辑中")).toBeNull();
    const editSwitch = screen.getByRole("switch", { name: "编辑设置" });
    expect(editSwitch.querySelector(".switch-track .switch-thumb")).toBeTruthy();
    expect(editSwitch.getAttribute("aria-checked")).toBe("false");
    // No edit/read-only state text renders while the switch is off (P1.2).
    expect(screen.queryByText("只读")).toBeNull();
    expect(screen.queryByText("草稿只在本页保存；发布前不会影响正在运行的任务；发现模型由程序发布目录事实。")).toBeNull();
    await user.click(editSwitch);
    expect(editSwitch.getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText("编辑中")).toBeTruthy();
    expect(screen.getByText("草稿只在本页保存；发布前不会影响正在运行的任务；发现模型由程序发布目录事实。")).toBeTruthy();
    expect(screen.getByRole("region", { name: "模型卡片" }).className).toContain("edit-mode");
    // Hidden sibling panels are queried structurally: their accessible name is
    // empty while hidden, so role+name would not resolve them.
    const panel = (label: string) => document.querySelector(`section[aria-label="${label}"]`)!;
    expect(panel("路由配置").className).toContain("edit-mode");
    // Delegation detail panels are never tinted by the model/settings edit mode.
    expect(panel("委派记录").className).not.toContain("edit-mode");
    expect(document.querySelector(".app-shell")!.className).not.toContain("edit-mode");
  });

  it("shows only an actionable gate state instead of a readable badge", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), gate: { phase: "draining", readers: 1, waitingWriters: 1, writer: null } });
    const user = userEvent.setup();
    await openModels(f.api, user);
    expect(screen.getByText("写入排队中")).toBeTruthy();
    expect(screen.queryByText("评价表可读")).toBeNull();
  });

  it("traps focus in the exit dialog, closes on Escape and restores focus to the switch", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openModels(f.api, user);
    const editSwitch = screen.getByRole("switch", { name: "编辑设置" });
    await user.click(editSwitch);
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "未保存的补充");
    await user.click(editSwitch);
    const dialog = await screen.findByRole("dialog", { name: "有未保存的修改" });
    const header = document.querySelector("header.app-header")!;
    const main = document.querySelector("main")!;
    // Focus starts on the safe choice: Enter must not publish by accident.
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "继续编辑" }));
    expect(header.hasAttribute("inert")).toBe(true);
    expect(main.hasAttribute("inert")).toBe(true);
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "保存并退出" }));
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "继续编辑" }));
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "保存并退出" }));
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "放弃修改" }));
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "继续编辑" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(header.hasAttribute("inert")).toBe(false);
    expect(main.hasAttribute("inert")).toBe(false);
    await waitFor(() => expect(document.activeElement).toBe(editSwitch));
  });

  it("resolves an unresolved begin with its original request before discarding", async () => {
    const f = fixture({ begin: ["network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "未确认的编辑资格");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/尚未确认编辑资格请求的结果/);
    expect(f.operations).toEqual(["evaluation_write_begin"]);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(await within(await screen.findByRole("dialog")).findByRole("button", { name: "放弃修改" }));
    await screen.findByText("已放弃未发布的修改。");
    const begins = f.command.mock.calls.filter(([op]) => op === "evaluation_write_begin");
    expect(begins).toHaveLength(2);
    // The retry reuses the request ID, so the board resolves the same intent.
    expect(begins[1][1]).toEqual(begins[0][1]);
    expect(f.operations).toEqual(["evaluation_write_begin", "evaluation_write_begin", "evaluation_write_abort"]);
    expect(f.aborted[0]).toMatchObject({ writerId: "writer", generation: 1 });
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("false");
  });

  it("keeps an unresolved begin for a later retry when the exit reply is also lost", async () => {
    const f = fixture({ begin: ["network", "network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    const summary = await screen.findByLabelText("我的意见");
    await user.type(summary, "补充");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/尚未确认编辑资格请求的结果/);
    // Reverting the draft makes leaving edit mode take the clean exit path.
    const restored = await screen.findByLabelText("我的意见");
    await user.clear(restored);
    await user.type(restored, "原人工意见");
    expect(screen.queryByText(/编辑中 · 未保存/)).toBeNull();
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await screen.findByText(/退出前未能确认编辑资格已释放/);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("switch", { name: "编辑设置" }).getAttribute("aria-checked")).toBe("false");
    expect(f.operations).toEqual(["evaluation_write_begin", "evaluation_write_begin"]);
    // The request identity survives the exit, so the next save resolves it.
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "再次编辑");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    const begins = f.command.mock.calls.filter(([op]) => op === "evaluation_write_begin");
    expect(begins).toHaveLength(3);
    expect(begins[1][1]).toEqual(begins[0][1]);
    expect(begins[2][1]).toEqual(begins[0][1]);
  });

  it("never reports an unconfirmed abort as a release and retries it with the same identity", async () => {
    const f = fixture({ begin: ["queued"], abort: ["network"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "排队后取消");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText(/未能确认编辑资格已释放/);
    expect(screen.queryByText("已取消等待，编辑资格已释放；草稿保持不变。")).toBeNull();
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "原人工意见排队后取消");
    // The next save retries the retained abort with its original command ID.
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    const aborts = f.command.mock.calls.filter(([op]) => op === "evaluation_write_abort");
    expect(aborts).toHaveLength(2);
    expect(aborts[1][1]).toEqual(aborts[0][1]);
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(2);
  });

  it("never claims a confirmed release when the board refuses the abort", async () => {
    const f = fixture({ begin: ["queued"], abort: ["forbidden"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "被拒绝的释放");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText(/未能确认编辑资格已释放/);
    expect(screen.queryByText("已取消等待，编辑资格已释放；草稿保持不变。")).toBeNull();
    expect(f.aborted[0]).toMatchObject({ writerId: "writer", generation: 1 });
  });

  it("bounds an unconfirmed release by the lease expiry instead of retrying forever", async () => {
    const f = fixture({ begin: ["queued"], abort: ["network"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "过期后继续");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText(/未能确认编辑资格已释放/);
    const realNow = Date.now();
    const now = vi.spyOn(Date, "now").mockReturnValue(realNow + 130000);
    try {
      await user.click(screen.getByRole("button", { name: "保存更改" }));
      await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    } finally {
      now.mockRestore();
    }
    // The dead lease was not aborted again, and nothing was claimed as released.
    expect(f.operations.filter(op => op === "evaluation_write_abort")).toHaveLength(1);
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(2);
    expect(f.published.at(-1)!.annotationChanges.find((c: any) => c.profileId === flashOff).text).toContain("过期后继续");
  });

  it("keeps the draft editable after a cancelled wait and publishes the next attempt", async () => {
    const f = fixture({ begin: ["queued"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "排队");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText("已取消等待，编辑资格已释放；草稿保持不变。");
    // A confirmed cancellation clears the uncertain state: typing stays possible.
    const summary = screen.getByLabelText("我的意见") as HTMLTextAreaElement;
    expect(summary.disabled).toBe(false);
    await user.type(summary, "后继续编辑");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published.at(-1)!.annotationChanges.find((c: any) => c.profileId === flashOff).text).toContain("后继续编辑");
  });

  it("clears uncertainty after a definitive begin failure so editing continues", async () => {
    const f = fixture({ begin: ["conflict"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "首次冲突");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/记录已更新，此操作未提交/);
    const summary = screen.getByLabelText("我的意见") as HTMLTextAreaElement;
    expect(summary.disabled).toBe(false);
    await user.type(summary, "后重试");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(f.published.at(-1)!.annotationChanges.find((c: any) => c.profileId === flashOff).text).toContain("后重试");
  });

  it("stops renewing an idle writer while a publish outcome is unknown", async () => {
    const f = fixture({ publish: ["network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "结果不明");
    vi.useFakeTimers();
    try {
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "保存更改" }));
      });
      for (let i = 0; i < 50 && !screen.queryByText(/保存结果未确认/); i++) {
        await act(async () => { await Promise.resolve(); });
      }
      expect(screen.getByText(/保存结果未确认/)).toBeTruthy();
      const renews = () => f.operations.filter(op => op === "evaluation_write_renew").length;
      const seen = renews();
      await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
      expect(renews()).toBe(seen);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not repeat an unresolved begin while it waits for the user", async () => {
    const f = fixture({ begin: ["network"] });
    const user = userEvent.setup();
    await openModels(f.api, user);
    await user.click(screen.getByRole("switch", { name: "编辑设置" }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "补充");
    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText(/尚未确认编辑资格请求的结果/);
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(1);
    expect(f.operations).not.toContain("evaluation_write_renew");
  });
});
