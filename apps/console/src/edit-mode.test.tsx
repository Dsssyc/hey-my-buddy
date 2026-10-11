import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, type ConsoleApi } from "./api";
import type { FamilyAnnotation, Profile, Snapshot, WriterGrant } from "./types";
import { chip, conflictNotice, level, levelSwitch, listed, modelRow, pane, selectModel } from "./buddies/page.testing";

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
    available: true, enabled, capabilities: ["execution:dsh", "decision"], contextWindow: 1000000,
    source: "catalog:fixture", description: "",
  }));
}
const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashFamily = { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
const FLASH = "dsh / deepseek-official / deepseek-flash";
const flashNote = (text: string, revision = 1): FamilyAnnotation => ({ ...flashFamily, text, revision, updatedAt: null });
/** A configuration that only a completed program discovery can add. */
const discovered = "dsh:deepseek-official:deepseek-v5:medium";

type Outcome = "begin" | "renew" | "publish" | "abort";
type Script = Partial<Record<Outcome, ("network" | "conflict" | "queued" | "active" | "drift" | "forbidden")[]>>;

function fixture(script: Script = {}, options: { queuedForever?: boolean; discoveryNote?: string } = {}) {
  const profiles = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [flashOff], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles,
    cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, summary: `原评价 ${p.model} ${p.effort}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    familyAnnotations: [flashNote("原备注")],
    preferences: [], familyPreferences: [], preferenceOverrides: [], evidence: [], decisions: [],
    sampleCounts: { [flashOff]: 5, "dsh:deepseek-official:deepseek-flash:high": 2 },
    modelConcurrency: [
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 },
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-v4-pro", limit: 5, active: 3 },
    ],
    tasks: { pendingCount: 0 },
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
      if (scripted === "forbidden") throw new ApiError("FORBIDDEN", "stale CSRF after restart");
      if (scripted === "conflict") {
        state = { ...state, tableRevision: state.tableRevision + 1, familyAnnotations: [flashNote("他人发布的备注 V3", 2)] };
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
        ...(options.discoveryNote ? { familyAnnotations: [flashNote(options.discoveryNote, 2)] } : {}),
        profiles: [...state.profiles, { profileId: discovered, label: "deepseek-v5 · medium", adapter: "dsh",
          provider: "deepseek-official", model: "deepseek-v5", effort: "medium", available: true, enabled: false,
          capabilities: [], contextWindow: null, source: "catalog:refresh", description: "" }] };
      return { tableRevision: state.tableRevision };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
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

async function openBuddy(api: ConsoleApi, user: ReturnType<typeof userEvent.setup>) {
  window.location.hash = "#buddies";
  render(<App suppliedApi={api} />);
  await listed(2, 3);
  await selectModel(user, FLASH, "DSH");
}
const note = () => within(pane()).getByRole("textbox", { name: "备注" }) as HTMLTextAreaElement;

const saveBar = () => screen.queryByRole("region", { name: "未保存的修改" });
const save = () => within(saveBar()!).getByRole("button", { name: /^(保存|重试同一保存)$/ });
const PUBLISHED = "已发布新版本";

/** Foregrounding uses the existing visibility-gated snapshot read, without a global refresh. */
async function readSnapshotOnForeground(snapshot: ConsoleApi["snapshot"]) {
  const calls = vi.mocked(snapshot).mock.calls.length;
  const visibility = vi.spyOn(document, "visibilityState", "get");
  try {
    visibility.mockReturnValue("hidden");
    await act(async () => { fireEvent(document, new Event("visibilitychange")); });
    visibility.mockReturnValue("visible");
    await act(async () => { fireEvent(document, new Event("visibilitychange")); });
    expect(vi.mocked(snapshot).mock.calls.length).toBeGreaterThan(calls);
  } finally {
    visibility.mockRestore();
  }
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("direct editing and the save bar", () => {
  it("edits directly without a switch, shows the save bar and only takes the grant on save", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    expect(saveBar()).toBeNull();
    await user.clear(note());
    await user.type(note(), "本地草稿，未发布");
    expect(within(saveBar()!).getByText("1 项未保存")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(save());
    await waitFor(() => expect(f.operations).toEqual(["evaluation_write_begin", "user_policy_publish"]));
    expect(f.published[0]).toMatchObject({ expectedRevision: 2, writerId: "writer" });
    // Only the changed family note is published: no profiles table and no card.
    expect(f.published[0].familyAnnotationChanges).toEqual([{ ...flashFamily, text: "本地草稿，未发布" }]);
    expect(f.published[0]).not.toHaveProperty("annotationChanges");
    expect(f.published[0]).not.toHaveProperty("profiles");
    expect(f.published[0]).not.toHaveProperty("cards");
    await screen.findByText(PUBLISHED);
    expect(saveBar()).toBeNull();
  });

  it("drops the save bar when an edit is reverted, without any request", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "x");
    expect(saveBar()).toBeTruthy();
    await user.type(note(), "{Backspace}");
    expect(note().value).toBe("原备注");
    await waitFor(() => expect(saveBar()).toBeNull());
    expect(f.command).not.toHaveBeenCalled();
  });

  it("counts every unsaved change in the save bar", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "，补充");
    await user.click(levelSwitch("high"));
    await user.click(within(pane()).getByRole("radio", { name: "优先" }));
    expect(within(saveBar()!).getByText("3 项未保存")).toBeTruthy();
    // The records page shows neither the save bar nor any draft state.
    await user.click(screen.getByRole("link", { name: "任务" }));
    expect(saveBar()).toBeNull();
    // The navigation still says a draft is waiting on the Buddy page.
    await user.click(screen.getByRole("link", { name: /^Buddy\s*有未保存的修改$/ }));
    expect(within(saveBar()!).getByText("3 项未保存")).toBeTruthy();
  });

  it("discards from the save bar and returns to the published values", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "未保存的补充");
    await user.click(within(saveBar()!).getByRole("button", { name: "放弃" }));
    await screen.findByText("草稿已放弃");
    expect(saveBar()).toBeNull();
    expect(note().value).toBe("原备注");
    expect(f.command).not.toHaveBeenCalled();
  });

  it("reuses the same begin request when the grant reply was lost", async () => {
    const f = fixture({ begin: ["network"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "补充");
    await user.click(save());
    await screen.findByText(/编辑资格未知/);
    await user.click(save());
    await waitFor(() => expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(2));
    const begins = f.command.mock.calls.filter(([op]) => op === "evaluation_write_begin");
    expect(begins[1][1]).toEqual(begins[0][1]);
    expect(f.operations.at(-1)).toBe("user_policy_publish");
  });

  it("waits behind another writer and releases the queued intent on cancel", async () => {
    const f = fixture({ begin: ["queued"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "排队中的草稿");
    await user.click(save());
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    expect(within(saveBar()!).getByRole("button", { name: "等待其他保存完成…" })).toBeTruthy();
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await waitFor(() => expect(f.operations).toContain("evaluation_write_abort"), { timeout: 4000 });
    await screen.findByText("已取消等待；草稿已保留。");
    expect(note().value).toBe("原备注排队中的草稿");
    expect(saveBar()).toBeTruthy();
    expect(f.operations).not.toContain("user_policy_publish");
  });

  it("publishes after the queued intent becomes active", async () => {
    const f = fixture({ begin: ["queued"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "等待后发布");
    await user.click(save());
    await screen.findByText(PUBLISHED, {}, { timeout: 5000 });
    expect(f.operations).toEqual(["evaluation_write_begin", "evaluation_write_renew", "user_policy_publish"]);
    expect(f.published[0]).toMatchObject({ writerId: "writer", generation: 1 });
  });

  it("releases a superseded intent before requesting a fresh one", async () => {
    const f = fixture({ begin: ["drift"], renew: ["network"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "补充");
    await user.click(save());
    await screen.findByText(/编辑资格未知/, {}, { timeout: 4000 });
    await user.click(save());
    await waitFor(() => expect(f.operations.filter(op => op === "evaluation_write_abort")).toHaveLength(1));
    const begins = f.operations.reduce<number[]>((all, op, index) => op === "evaluation_write_begin" ? [...all, index] : all, []);
    expect(begins).toHaveLength(2);
    expect(f.operations.indexOf("evaluation_write_abort")).toBeLessThan(begins[1]);
    await screen.findByText(PUBLISHED);
  });

  it("keeps the same publication identity when the publish reply was lost", async () => {
    const f = fixture({ publish: ["network"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "结果不明");
    await user.click(save());
    await screen.findByText(/保存结果未知/);
    // The draft and its staged payload survive an edit freeze until confirmed;
    // the field stays read-only rather than disabled, so it is still copyable.
    expect(note().value).toBe("原备注结果不明");
    expect(note().readOnly).toBe(true);
    expect(note().disabled).toBe(false);
    expect(within(saveBar()!).getByText("1 项未保存")).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await screen.findByText(PUBLISHED);
    expect(f.published).toHaveLength(2);
    expect(f.published[1]).toEqual(f.published[0]);
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(1);
  });

  it("retains an unknown publication across a later CSRF refusal", async () => {
    const f = fixture({ publish: ["network", "forbidden"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "保留原请求");
    await user.click(save());
    await screen.findByText(/保存结果未知/);
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await waitFor(() => expect(f.published).toHaveLength(2));
    await screen.findByText(/保存结果未知/);
    expect(f.aborted).toHaveLength(0);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "重试同一保存" })[0]!.getAttribute("aria-disabled")).not.toBe("true"));
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await screen.findByText(PUBLISHED);
    expect(f.published).toHaveLength(3);
    expect(f.published[1]).toEqual(f.published[0]);
    expect(f.published[2]).toEqual(f.published[0]);
  });

  it("keeps a conflicting draft and offers a deliberate reload or discard", async () => {
    const f = fixture({ publish: ["conflict"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.clear(note());
    await user.type(note(), "基于 V2 的草稿");
    await user.click(save());
    const banner = await waitFor(() => { const found = conflictNotice(); expect(found).toBeTruthy(); return found!; });
    expect(within(banner).getByText(/设置已更新/)).toBeTruthy();
    expect(note().value).toBe("基于 V2 的草稿");
    expect(f.operations).toContain("evaluation_write_abort");
    // Deliberate reload adopts the latest publication.
    await user.click(within(banner).getByRole("button", { name: "重新加载最新版本" }));
    await screen.findByText("已加载 V3；请核对后保存。");
    expect(note().value).toBe("他人发布的备注 V3");
    await user.type(note(), "（复核）");
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(f.published.at(-1)).toMatchObject({ expectedRevision: 3 });
  });

  it("keeps drafts editable offline while save refuses and explains why", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), capabilities: { evaluationWriteGate: false } });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "离线草稿");
    expect(save().getAttribute("aria-disabled")).toBe("true");
    await user.click(save());
    expect(f.command).not.toHaveBeenCalled();
    expect((await screen.findAllByText(/无写入资格/)).length).toBeGreaterThan(0);
    expect(note().value).toBe("原备注离线草稿");
  });

  it("explains a guarded save from the keyboard without sending a request", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), capabilities: { evaluationWriteGate: false } });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "x");
    const button = save();
    button.focus();
    expect(document.activeElement).toBe(button);
    await user.keyboard("{Enter}");
    expect(f.command).not.toHaveBeenCalled();
    expect((await screen.findAllByText(/无写入资格/)).length).toBeGreaterThan(0);
  });

  it("refreshes program directory facts on discovery without uploading a draft", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText("新增 1 个 buddy，默认不启用");
    expect(f.operations).toEqual(["model_catalog_refresh"]);
    expect(f.published).toHaveLength(0);
    // The new model is listed, marked 新 and starts disabled.
    await listed(2, 4);
    const added = modelRow("dsh / deepseek-official / deepseek-v5");
    expect(within(added).getByText("新")).toBeTruthy();
    expect(chip("dsh / deepseek-official / deepseek-v5", "medium").textContent).toBe("medium未启用");
    expect(saveBar()).toBeNull();
  });

  it("keeps a conflicting draft at its own revision when discovery finds another value", async () => {
    const f = fixture({}, { discoveryNote: "他人发现期间发布" });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "，我的草稿");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText("新增 1 个 buddy，默认不启用");
    // Field-level three-way check: C was published for the same field our draft
    // changes, so the draft keeps B and its own expectedRevision.
    const banner = conflictNotice()!;
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain("已在 V");
    expect(note().value).toBe("原备注，我的草稿");
    expect(f.published).toHaveLength(0);
    // Saving B under V3 is refused without an explicit conflict resolution.
    await user.click(save());
    expect((await screen.findAllByText(/设置已更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    await user.click(within(banner).getByRole("button", { name: "重新加载最新版本" }));
    await screen.findByText("已加载 V3；请核对后保存。");
    expect(note().value).toBe("他人发现期间发布");
  });

  it("keeps a dirty old-version draft for CAS conflict when a poll finds a publication", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "，未保存");
    // Another writer published V3; the foreground snapshot read only
    // re-reads state and must not rebase, rewrite or discard the local draft.
    f.setSnapshot({ ...f.snapshot(), tableRevision: 3, familyAnnotations: [flashNote("他人 V3", 2)] });
    await readSnapshotOnForeground(f.api.snapshot);
    const banner = await waitFor(() => { const found = conflictNotice(); expect(found).toBeTruthy(); return found!; });
    expect(note().value).toBe("原备注，未保存");
    expect(f.published).toHaveLength(0);
    await user.click(save());
    expect((await screen.findAllByText(/设置已更新/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    // Discard is the deliberate resolution; nothing was published implicitly.
    await user.click(within(banner).getByRole("button", { name: "放弃修改" }));
    await screen.findByText("草稿已放弃");
    expect(f.operations).not.toContain("user_policy_publish");
    expect(note().value).toBe("他人 V3");
  });

  it("keeps an unsaved note while discovery refreshes the directory", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "，保留的备注");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText("新增 1 个 buddy，默认不启用");
    expect(f.operations).toEqual(["model_catalog_refresh"]);
    expect(note().value).toBe("原备注，保留的备注");
    // The rebased draft publishes only the human patch at the refreshed revision.
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(f.published[0]).toMatchObject({ expectedRevision: 3 });
    expect(f.published[0].familyAnnotationChanges).toEqual([{ ...flashFamily, text: "原备注，保留的备注" }]);
    expect(f.published[0]).not.toHaveProperty("profiles");
  });

  it("shows each level's recorded samples and offers nothing that records evidence", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), evidence: [{ evidenceId: "ev-1", profileId: flashOff, kind: "observation",
      summary: "单次观察", project: "fixture", conditions: ["React"], source: "user", runId: null,
      createdAt: "2026-09-22T00:00:00Z" }] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    expect(within(level("off")).getByText("5")).toBeTruthy();
    expect(within(level("high")).getByText("2")).toBeTruthy();
    // The side column holds settings only: the two reason-less levels offer a switch and a preference, nothing else.
    expect(within(level("off")).getAllByRole("switch")).toHaveLength(1);
    expect(within(level("off")).getAllByRole("combobox")).toHaveLength(1);
    expect(within(level("off")).queryByRole("textbox")).toBeNull();
    expect(within(level("off")).queryByRole("button")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("toggles a level from its switch and marks it unsaved until publication", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    expect(levelSwitch("off").getAttribute("aria-checked")).toBe("true");
    expect(levelSwitch("high").getAttribute("aria-checked")).toBe("false");
    expect(chip(FLASH, "off").textContent).toBe("off已启用");
    await user.click(levelSwitch("off"));
    expect(levelSwitch("off").getAttribute("aria-checked")).toBe("false");
    expect(within(level("off")).getByRole("img", { name: "未保存" })).toBeTruthy();
    expect(within(saveBar()!).getByText("1 项未保存")).toBeTruthy();
    // The table's row follows the draft immediately, with the same mark.
    expect(chip(FLASH, "off").textContent).toBe("off未启用");
    expect(within(chip(FLASH, "off")).getByRole("img", { name: "未保存" })).toBeTruthy();
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(within(level("off")).queryByRole("img", { name: "未保存" })).toBeNull();
    expect(within(chip(FLASH, "off")).queryByRole("img", { name: "未保存" })).toBeNull();
    // A disable publishes one enablement patch and nothing program-owned.
    expect(f.published.at(-1)!.profileSettings).toEqual([{ profileId: flashOff, enabled: false }]);
    expect(f.published.at(-1)).not.toHaveProperty("profiles");
    expect(f.published.at(-1)).not.toHaveProperty("cards");
  });

  it("uses the three-page navigation without any global edit switch", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    const nav = screen.getByRole("navigation", { name: "主要导航" });
    expect(within(nav).getAllByRole("link").map(link => link.textContent)).toEqual(["任务", "Buddy", "设置"]);
    expect(screen.queryByText("模型卡片")).toBeNull();
    expect(screen.queryByText("路由配置")).toBeNull();
    expect(screen.queryByText("编辑设置")).toBeNull();
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    // An open gate is normal, so no "评价表可读" badge is needed.
    expect(screen.queryByText("评价表可读")).toBeNull();
    expect(screen.queryByText("独占编辑中")).toBeNull();
    for (const page of ["任务", "设置", "Buddy"]) {
      await user.click(within(nav).getByRole("link", { name: page }));
      expect(screen.queryByText("编辑设置")).toBeNull();
    }
  });

  it("shows only an actionable gate state instead of a readable badge", async () => {
    const f = fixture();
    f.setSnapshot({ ...f.snapshot(), gate: { phase: "draining", readers: 1, waitingWriters: 1, writer: null } });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    expect(screen.getByText("写入排队中")).toBeTruthy();
    expect(screen.queryByText("评价表可读")).toBeNull();
  });

  it("resolves an unresolved begin with its original request before discarding", async () => {
    const f = fixture({ begin: ["network"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "未确认的编辑资格");
    await user.click(save());
    await screen.findByText(/编辑资格未知/);
    expect(f.operations).toEqual(["evaluation_write_begin"]);
    await user.click(within(saveBar()!).getByRole("button", { name: "放弃" }));
    await screen.findByText("草稿已放弃");
    const begins = f.command.mock.calls.filter(([op]) => op === "evaluation_write_begin");
    expect(begins).toHaveLength(2);
    // The retry reuses the request ID, so the board resolves the same intent.
    expect(begins[1][1]).toEqual(begins[0][1]);
    expect(f.operations).toEqual(["evaluation_write_begin", "evaluation_write_begin", "evaluation_write_abort"]);
    expect(f.aborted[0]).toMatchObject({ writerId: "writer", generation: 1 });
    expect(saveBar()).toBeNull();
  });

  it("keeps an unresolved begin for a later retry when the discard reply is also lost", async () => {
    const f = fixture({ begin: ["network", "network"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "补充");
    await user.click(save());
    await screen.findByText(/编辑资格未知/);
    // Reverting the text leaves nothing unsaved, but the unknown begin keeps
    // the save bar so the user can resolve it deliberately.
    await user.clear(note());
    await user.type(note(), "原备注");
    expect(within(saveBar()!).getByText("保存结果待核对")).toBeTruthy();
    await user.click(within(saveBar()!).getByRole("button", { name: "放弃" }));
    await screen.findByText(/编辑资格释放未知/);
    expect(f.operations).toEqual(["evaluation_write_begin", "evaluation_write_begin"]);
    // The request identity survives, so the next save resolves it.
    await user.type(note(), "再次编辑");
    await user.click(save());
    await screen.findByText(PUBLISHED);
    const begins = f.command.mock.calls.filter(([op]) => op === "evaluation_write_begin");
    expect(begins).toHaveLength(3);
    expect(begins[1][1]).toEqual(begins[0][1]);
    expect(begins[2][1]).toEqual(begins[0][1]);
  });

  it("never reports an unconfirmed abort as a release and retries it with the same identity", async () => {
    const f = fixture({ begin: ["queued"], abort: ["network"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "排队后取消");
    await user.click(save());
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText(/编辑资格释放未知/);
    expect(screen.queryByText("已取消等待；草稿已保留。")).toBeNull();
    expect(note().value).toBe("原备注排队后取消");
    // The next save retries the retained abort with its original command ID.
    await user.click(save());
    await screen.findByText(PUBLISHED);
    const aborts = f.command.mock.calls.filter(([op]) => op === "evaluation_write_abort");
    expect(aborts).toHaveLength(2);
    expect(aborts[1][1]).toEqual(aborts[0][1]);
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(2);
  });

  it("never claims a confirmed release when the board refuses the abort", async () => {
    const f = fixture({ begin: ["queued"], abort: ["forbidden"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "被拒绝的释放");
    await user.click(save());
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText(/编辑资格释放未知/);
    expect(screen.queryByText("已取消等待；草稿已保留。")).toBeNull();
    expect(f.aborted[0]).toMatchObject({ writerId: "writer", generation: 1 });
  });

  it("bounds an unconfirmed release by the lease expiry instead of retrying forever", async () => {
    const f = fixture({ begin: ["queued"], abort: ["network"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "过期后继续");
    await user.click(save());
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText(/编辑资格释放未知/);
    const realNow = Date.now();
    const now = vi.spyOn(Date, "now").mockReturnValue(realNow + 130000);
    try {
      await user.click(save());
      await screen.findByText(PUBLISHED);
    } finally {
      now.mockRestore();
    }
    // The dead lease was not aborted again, and nothing was claimed as released.
    expect(f.operations.filter(op => op === "evaluation_write_abort")).toHaveLength(1);
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(2);
    expect(f.published.at(-1)!.familyAnnotationChanges[0].text).toContain("过期后继续");
  });

  it("keeps the draft editable after a cancelled wait and publishes the next attempt", async () => {
    const f = fixture({ begin: ["queued"] }, { queuedForever: true });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "排队");
    await user.click(save());
    const cancel = await screen.findByRole("button", { name: "取消等待" });
    await waitFor(() => expect(f.operations).toContain("evaluation_write_renew"), { timeout: 4000 });
    await user.click(cancel);
    await screen.findByText("已取消等待；草稿已保留。");
    // A confirmed cancellation clears the uncertain state: typing stays possible.
    expect(note().disabled).toBe(false);
    await user.type(note(), "后继续编辑");
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(f.published.at(-1)!.familyAnnotationChanges[0].text).toContain("后继续编辑");
  });

  it("clears uncertainty after a definitive begin failure so editing continues", async () => {
    const f = fixture({ begin: ["conflict"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "首次冲突");
    await user.click(save());
    await screen.findByText(/记录已更新/);
    expect(note().disabled).toBe(false);
    await user.type(note(), "后重试");
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(f.published.at(-1)!.familyAnnotationChanges[0].text).toContain("后重试");
  });

  it("stops renewing an idle writer while a publish outcome is unknown", async () => {
    const f = fixture({ publish: ["network"] });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "结果不明");
    vi.useFakeTimers();
    try {
      await act(async () => {
        fireEvent.click(save());
      });
      for (let i = 0; i < 50 && !screen.queryByText(/保存结果未知/); i++) {
        await act(async () => { await Promise.resolve(); });
      }
      expect(screen.getAllByText(/保存结果未知/).length).toBeGreaterThan(0);
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
    await openBuddy(f.api, user);
    await user.type(note(), "补充");
    await user.click(save());
    await screen.findByText(/编辑资格未知/);
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations.filter(op => op === "evaluation_write_begin")).toHaveLength(1);
    expect(f.operations).not.toContain("evaluation_write_renew");
  });
});
