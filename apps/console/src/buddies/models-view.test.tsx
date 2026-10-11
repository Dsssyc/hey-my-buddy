import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ApiError, type ConsoleApi } from "../api";
import type { HarnessHealth, Profile, Snapshot, WriterGrant } from "../types";
import {
  defaultPreference, foldHandle, levelPreference, levelSwitch, listed, modelButton, modelRow, modelsTable, note, pane, search, selectModel,
} from "./page.testing";

/**
 * The parts of the models view that the other files do not reach: its empty
 * states, which row the side column follows, the model's default preference,
 * what a session without write access leaves usable, and what the view says
 * while it reads unavailable buddies or when a read or a discovery fails.
 */

const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { adapter: "codex", provider: "openai", model: "gpt-6-sol" };
const SONNET = "claude / anthropic / claude-sonnet-5";
const SOL = "codex / openai / gpt-6-sol";

function profile(family: typeof sonnet, effort: string, extra: Partial<Profile> = {}): Profile {
  return {
    profileId: `${family.adapter}:${family.provider}:${family.model}:${effort}`, label: `${family.model} · ${effort}`, ...family, effort,
    available: true, enabled: false, capabilities: [], contextWindow: null, description: "", source: "catalog:fixture", ...extra,
  };
}

function snapshot(extra: Partial<Snapshot> = {}): Snapshot & { harnesses: HarnessHealth[] } {
  return {
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review", routingBudget: "standard" },
    profiles: [profile(sonnet, "medium", { enabled: true }), profile(sonnet, "high"), profile(sol, "medium"), profile(sol, "low")],
    preferences: [], familyPreferences: [], preferenceOverrides: [], cards: [], familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [{ ...sonnet, limit: 2, active: 0 }],
    tasks: { pendingCount: 0 }, capabilities: { evaluationWriteGate: true },
    harnesses: [
      { adapter: "claude", status: "ready", available: true, revision: 1, manualPath: null },
      { adapter: "codex", status: "ready", available: true, revision: 1, manualPath: null },
    ],
    ...extra,
  };
}

type Reply = (params: Record<string, any>) => unknown;

function fixture(state: Snapshot = snapshot(), replies: Record<string, Reply> = {}) {
  const published: Record<string, any>[] = [];
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing", state: "active",
    tableRevision: state.tableRevision, expiresAt: new Date(Date.now() + 120_000).toISOString() };
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    if (replies[operation]) return replies[operation](params);
    if (operation === "evaluation_write_begin") return grant;
    if (operation === "user_policy_publish") { published.push(params); return { published: true, tableRevision: state.tableRevision + 1 }; }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)), command, task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, published };
}

function open(f: ReturnType<typeof fixture>) {
  window.location.hash = "#buddies";
  render(<App suppliedApi={f.api} />);
}

/** One page of retained rows, as `model_profiles` returns it. */
function page(profiles: Profile[], state: Snapshot) {
  return { profiles, cards: [], preferences: [], preferenceOverrides: [], familyPreferences: [], familyAnnotations: [],
    sampleCounts: {}, modelConcurrency: [], tableRevision: state.tableRevision, nextCursor: null };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("an empty table", () => {
  it("says that there are no models yet and how to find some, and opens no side column", async () => {
    const f = fixture(snapshot({ profiles: [], modelConcurrency: [] }));
    open(f);
    await listed(0, 0);
    expect(screen.getByText("还没有模型")).toBeTruthy();
    expect(screen.getByText("点“发现模型”，查找本机的 Harness 能用的模型。")).toBeTruthy();
    expect(screen.queryByRole("complementary")).toBeNull();
    // The table keeps its header, so the page does not change shape.
    expect(within(modelsTable()).getAllByRole("columnheader").map(cell => cell.textContent))
      .toEqual(["Buddy", "Harness", "提供方", "模型", "推理强度", "并发"]);
  });

  it("says that nothing matches when a search or a filter leaves no row", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await user.type(search(), "no-such-model");
    await listed(0, 0);
    expect(screen.getByText("没有匹配的模型")).toBeTruthy();
    expect(screen.getByText("换一个搜索词，或者去掉筛选。")).toBeTruthy();
    expect(screen.queryByText("还没有模型")).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
  });
});

describe("the row the side column follows", () => {
  it("opens the first row on screen, and returns to the chosen one when it is on screen again", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    // Nothing chosen yet: the first row that is drawn is the one being edited.
    expect(pane(SONNET)).toBeTruthy();
    expect(modelButton(SONNET).getAttribute("aria-pressed")).toBe("true");
    await selectModel(user, SOL, "Codex");
    // A search that hides the chosen row moves the side column to a row that is there.
    await user.type(search(), "claude");
    await waitFor(() => pane(SONNET));
    await user.clear(search());
    await waitFor(() => pane(SOL));
    expect(modelButton(SOL).getAttribute("aria-pressed")).toBe("true");
    // Folding its harness away does the same.
    await user.click(foldHandle("Codex"));
    await waitFor(() => pane(SONNET));
    await user.click(foldHandle("Codex"));
    await waitFor(() => pane(SOL));
  });

  it("writes an unknown occupancy as unknown in the table and in the side column, never as 0", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    expect(within(modelRow(SONNET)).getByLabelText("占用 0，上限 2").textContent).toBe("0 / 2");
    await selectModel(user, SOL, "Codex");
    // The board reported no entry for this model: the limit is the default, the occupancy unknown.
    expect(within(modelRow(SOL)).getByLabelText("占用未知").textContent).toBe("— / 2");
    expect(within(pane()).getByText("占用未知")).toBeTruthy();
  });
});

describe("a model's default preference", () => {
  it("offers pinning only to a model with a buddy that is available and on, and says so on the choice", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SOL, "Codex");
    expect(defaultPreference("不设").getAttribute("aria-checked")).toBe("true");
    // Each choice explains itself to a pointer, to the keyboard and to a screen reader.
    expect(defaultPreference("优先").getAttribute("aria-description")).toBe("路由时先考虑");
    expect(defaultPreference("排除").getAttribute("aria-description")).toBe("不参与路由");
    const pin = defaultPreference("固定");
    expect(pin.getAttribute("aria-disabled")).toBe("true");
    expect(pin.getAttribute("aria-description")).toBe("这个模型没有可用并且已启用的 buddy，不能固定");
    await user.click(pin);
    expect(defaultPreference("不设").getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    // Turning a buddy on makes the model pinnable.
    await user.click(levelSwitch("medium"));
    expect(defaultPreference("固定").hasAttribute("aria-disabled")).toBe(false);
    expect(defaultPreference("固定").getAttribute("aria-description")).toBe("路由时只在固定的 buddy 里选");
    await user.click(defaultPreference("固定"));
    expect(defaultPreference("固定").getAttribute("aria-checked")).toBe("true");
  });

  it("takes a reason once a default is set and publishes both as one patch of the model", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SOL, "Codex");
    expect(within(pane()).queryByRole("textbox", { name: "默认偏好的理由" })).toBeNull();
    await user.click(defaultPreference("优先"));
    await user.type(within(pane()).getByRole("textbox", { name: "默认偏好的理由" }), "机械改动");
    // The default shows on every buddy that follows it, and the field is marked until saved.
    expect(within(modelRow(SOL)).getAllByText("优先")).toHaveLength(2);
    expect(within(pane()).getAllByRole("img", { name: "未保存" })).toHaveLength(1);
    expect(levelPreference("medium").textContent).toBe("跟随模型");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本");
    expect(f.published[0].familyPreferenceChanges).toEqual([{ ...sol, mode: "prefer", reason: "机械改动" }]);
    expect(f.published[0]).not.toHaveProperty("preferenceChanges");
    // Back to 不设 removes the reason field with the default.
    await user.click(defaultPreference("不设"));
    expect(within(pane()).queryByRole("textbox", { name: "默认偏好的理由" })).toBeNull();
  });
});

describe("a session that cannot write", () => {
  it("shows every setting and changes none, and says why on the switch", async () => {
    const f = fixture(snapshot({ consoleSession: { id: "fixture-session", canWrite: false, reason: null } }));
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    const lock = levelSwitch("medium");
    expect(lock.getAttribute("aria-checked")).toBe("true");
    expect(lock.getAttribute("aria-disabled")).toBe("true");
    expect(lock.getAttribute("aria-description")).toBe("登录已失效，重新登录后可以修改");
    await user.click(lock);
    expect(levelSwitch("medium").getAttribute("aria-checked")).toBe("true");
    expect(levelPreference("medium")).toHaveProperty("disabled", true);
    expect(within(pane()).getByRole("spinbutton", { name: "并发上限" })).toHaveProperty("disabled", true);
    // The note stays readable and copyable, never editable.
    expect(note()).toHaveProperty("readOnly", true);
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("what the view says about its own reads", () => {
  const retired = profile({ adapter: "claude", provider: "anthropic", model: "claude-old" }, "high", { available: false, unavailableReason: "provider paused" });

  it("says that it is reading unavailable buddies, and shows them when the read ends", async () => {
    const state = snapshot({ unavailableProfileCount: 1 });
    let finish!: (value: unknown) => void;
    const f = fixture(state, { model_profiles: () => new Promise(resolve => { finish = resolve; }) });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 1" }));
    expect(await screen.findByText("正在读取不可用的 buddy")).toBeTruthy();
    finish(page([...state.profiles, retired], state));
    await listed(1, 5);
    expect(screen.queryByText("正在读取不可用的 buddy")).toBeNull();
  });

  it("reports a failed read as a failure with a retry, and reads again only when asked", async () => {
    const state = snapshot({ unavailableProfileCount: 1 });
    let attempt = 0;
    const f = fixture(state, { model_profiles: () => {
      attempt += 1;
      if (attempt === 1) throw new ApiError("NETWORK", "读取不可用的 buddy 失败");
      return page([...state.profiles, retired], state);
    } });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 1" }));
    const failure = await screen.findByRole("alert");
    expect(failure.textContent).toContain("读取不可用的 buddy 失败");
    // The rows already on screen stay; nothing is retried behind the user's back.
    await listed(1, 4);
    await new Promise(resolve => setTimeout(resolve, 400));
    expect(attempt).toBe(1);
    await user.click(within(failure).getByRole("button", { name: "重试" }));
    await listed(1, 5);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(attempt).toBe(2);
  });

  it("says where the list of unavailable buddies was cut", async () => {
    const state = snapshot({ unavailableProfileCount: 700 });
    const many = Array.from({ length: 600 }, (_, index) => profile({ adapter: "claude", provider: "anthropic", model: "claude-old" }, `e${index}`,
      { available: false, unavailableReason: "provider paused" }));
    const f = fixture(state, { model_profiles: () => ({ ...page(many, state), nextCursor: "more" }) });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    expect(screen.queryByText(/只显示前/)).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 700" }));
    expect(await screen.findByText("只显示前 600 个不可用的 buddy；更早的请用搜索查找。")).toBeTruthy();
    // The cut is a hard stop: one bounded read, no further page.
    expect(f.command.mock.calls.filter(([operation]) => operation === "model_profiles")).toHaveLength(1);
  });

  it("reports a failed discovery as a failure and leaves the table and the button usable", async () => {
    let attempt = 0;
    const f = fixture(snapshot(), { model_catalog_refresh: () => {
      attempt += 1;
      throw new ApiError("NETWORK", "发现模型失败");
    } });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    const discover = screen.getByRole("button", { name: "发现模型" });
    expect(discover.hasAttribute("aria-disabled")).toBe(false);
    await user.click(discover);
    expect((await screen.findByRole("alert")).textContent).toBe("发现模型失败");
    await listed(1, 4);
    expect(discover.hasAttribute("aria-disabled")).toBe(false);
    await user.click(discover);
    await waitFor(() => expect(attempt).toBe(2));
  });
});
