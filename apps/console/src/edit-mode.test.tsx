import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, type ConsoleApi } from "./api";
import type { FamilyAnnotation, Profile, Snapshot } from "./types";
import { chip, effortCell, level, levelSwitch, listed, modelRow, pane, selectModel } from "./buddies/page.testing";

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
const flashNote = (text: string): FamilyAnnotation => ({ ...flashFamily, text, updatedAt: null });
/** A configuration that only a completed program discovery can add. */
const discovered = "dsh:deepseek-official:deepseek-v5:medium";

type Script = { publish?: ("network" | "forbidden")[] };

function fixture(script: Script = {}, options: { discoveryNote?: string } = {}) {
  const profiles = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerProfileIds: [flashOff], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles,

    familyAnnotations: [flashNote("原备注")],
    preferences: [], decisions: [],

    modelConcurrency: [
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 },
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-v4-pro", limit: 5, active: 3 },
    ],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };
  const publishQueue = [...(script.publish ?? [])];
  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (operation === "user_policy_publish") {
      published.push(params);
      const scripted = publishQueue.shift();
      if (scripted === "network") throw new ApiError("NETWORK", "lost publish reply");
      if (scripted === "forbidden") throw new ApiError("FORBIDDEN", "stale CSRF after restart");
      state = { ...state,
        familyAnnotations: params.familyAnnotationChanges
          ? params.familyAnnotationChanges.map((change: { text: string }) => flashNote(change.text))
          : state.familyAnnotations,
        profiles: params.profileSettings
          ? state.profiles.map(profile => ({ ...profile, enabled: params.profileSettings.find(
              (change: { profileId: string; enabled: boolean }) => change.profileId === profile.profileId)?.enabled ?? profile.enabled }))
          : state.profiles,
      };
      return { published: true };
    }
    if (operation === "model_catalog_refresh") {
      // The program publishes the directory itself; the page only re-reads it.
      // A discovery can coincide with another settings publication.
      state = { ...state,
        ...(options.discoveryNote ? { familyAnnotations: [flashNote(options.discoveryNote)] } : {}),
        profiles: [...state.profiles, { profileId: discovered, label: "deepseek-v5 · medium", adapter: "dsh",
          provider: "deepseek-official", model: "deepseek-v5", effort: "medium", available: true, enabled: false,
          capabilities: [], contextWindow: null, source: "catalog:refresh", description: "" }] };
      return { discovered: true };
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
  return { api, command, operations, published,
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
const PUBLISHED = "设置已保存";

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
  it("edits directly and publishes one settings patch on save", async () => {
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
    await waitFor(() => expect(f.operations).toEqual(["user_policy_publish"]));
    expect(f.published[0]).toMatchObject({ commandId: expect.any(String) });
    expect(f.published[0]).not.toHaveProperty("expectedRevision");
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
    await user.click(effortCell("high"));
    await user.click(levelSwitch("high"));
    await user.click(within(level("high")).getByRole("radio", { name: "优先" }));
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
    expect(f.operations.filter(op => op === "user_policy_publish")).toHaveLength(2);
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
    await waitFor(() => expect(screen.getAllByRole("button", { name: "重试同一保存" })[0]!.getAttribute("aria-disabled")).not.toBe("true"));
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await screen.findByText(PUBLISHED);
    expect(f.published).toHaveLength(3);
    expect(f.published[1]).toEqual(f.published[0]);
    expect(f.published[2]).toEqual(f.published[0]);
  });

  it("publishes a local note over a concurrent note and rereads the saved value", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.clear(note());
    await user.type(note(), "我的备注");
    f.setSnapshot({ ...f.snapshot(), familyAnnotations: [flashNote("其他窗口的备注")] });
    await readSnapshotOnForeground(f.api.snapshot);
    expect(note().value).toBe("我的备注");
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(f.published).toHaveLength(1);
    expect(f.published[0].familyAnnotationChanges).toEqual([{ ...flashFamily, text: "我的备注" }]);
    expect(note().value).toBe("我的备注");
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
    expect(within(added).queryByText("medium")).toBeNull();
    await selectModel(user, "dsh / deepseek-official / deepseek-v5", "DSH");
    expect(levelSwitch("medium").getAttribute("aria-checked")).toBe("false");
    expect(saveBar()).toBeNull();
  });

  it("keeps a local note during discovery when the recorded note also changes", async () => {
    const f = fixture({}, { discoveryNote: "其他窗口的备注" });
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    await user.type(note(), "，我的补充");
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText("新增 1 个 buddy，默认不启用");
    expect(note().value).toBe("原备注，我的补充");
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(f.published[0].familyAnnotationChanges).toEqual([{ ...flashFamily, text: "原备注，我的补充" }]);
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
    expect(f.published[0]).not.toHaveProperty("expectedRevision");
    expect(f.published[0].familyAnnotationChanges).toEqual([{ ...flashFamily, text: "原备注，保留的备注" }]);
    expect(f.published[0]).not.toHaveProperty("profiles");
  });

  it("toggles a level from its switch and marks it unsaved until publication", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f.api, user);
    expect(levelSwitch("off").getAttribute("aria-checked")).toBe("true");
    expect(effortCell("high").getAttribute("aria-pressed")).toBe("false");
    expect(chip(FLASH, "off").textContent).toBe("off");
    await user.click(levelSwitch("off"));
    expect(levelSwitch("off").getAttribute("aria-checked")).toBe("false");
    expect(within(level("off")).getByRole("img", { name: "未保存" })).toBeTruthy();
    expect(within(saveBar()!).getByText("1 项未保存")).toBeTruthy();
    // The table's row follows the draft immediately, with the same mark.
    expect(within(modelRow(FLASH)).queryByText("off")).toBeNull();
    expect(within(effortCell("off")).getByRole("img", { name: "未保存" })).toBeTruthy();
    await user.click(save());
    await screen.findByText(PUBLISHED);
    expect(within(level("off")).queryByRole("img", { name: "未保存" })).toBeNull();
    expect(within(effortCell("off")).queryByRole("img", { name: "未保存" })).toBeNull();
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

                    });
