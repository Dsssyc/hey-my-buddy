import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ApiError, type ConsoleApi } from "../api";
import type { HarnessHealth, Buddy, Snapshot } from "../types";
import {
  effortCell, foldHandle, level, levelPreference, levelSwitch, listed, modelButton, modelRow, catalogue, note, pane, search, selectModel,
} from "./page.testing";

/**
 * The parts of the models view that the other files do not reach: its empty
 * states, which row the side column follows, a buddy's own preference,
 * what a session without write access leaves usable, and what the view says
 * while it reads unavailable buddies or when a read or a discovery fails.
 */

const sonnet = { harness: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { harness: "codex", provider: "openai", model: "gpt-6-sol" };
const SONNET = "claude / anthropic / claude-sonnet-5";
const SOL = "codex / openai / gpt-6-sol";

function buddy(modelService: typeof sonnet, effort: string, extra: Partial<Buddy> = {}): Buddy {
  return { effortRank: null,
    buddyId: `${modelService.harness}:${modelService.provider}:${modelService.model}:${effort}`, label: `${modelService.model} · ${effort}`, ...modelService, effort,
    available: true, enabled: false, capabilities: [], contextWindow: null, description: "", source: "catalog:fixture", ...extra,
  };
}

function snapshot(extra: Partial<Snapshot> = {}): Snapshot & { harnesses: HarnessHealth[] } {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review", routingBudget: "standard" },
    buddies: [buddy(sonnet, "medium", { enabled: true }), buddy(sonnet, "high"), buddy(sol, "medium"), buddy(sol, "low")],
    preferences: [], modelServiceNotes: [], decisions: [],
 modelServiceConcurrency: [{ ...sonnet, limit: 2, active: 0 }],
    tasks: { pendingCount: 0 }, capabilities: {},
    harnesses: [
      { harness: "claude", status: "ready", available: true, revision: 1, manualPath: null },
      { harness: "codex", status: "ready", available: true, revision: 1, manualPath: null },
    ],
    ...extra,
  };
}

type Reply = (params: Record<string, any>) => unknown;

function fixture(state: Snapshot = snapshot(), replies: Record<string, Reply> = {}) {
  const published: Record<string, any>[] = [];

  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    if (replies[operation]) return replies[operation](params);

    if (operation === "user_policy_publish") { published.push(params); return { published: true,}; }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)), command, task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, published };
}

function open(f: ReturnType<typeof fixture>) {
  window.location.hash = "#buddies";
  render(<App suppliedApi={f.api} />);
}

/** One page of retained rows, as `buddy_list` returns it. */
function page(buddies: Buddy[], _state: Snapshot) {
  return { buddies, preferences: [], modelServiceNotes: [],
 modelServiceConcurrency: [], nextCursor: null };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  window.location.hash = "";
});

describe("an empty catalogue", () => {
  it("says that there are no models yet and how to find some, and opens no drawer", async () => {
    const f = fixture(snapshot({ buddies: [], modelServiceConcurrency: [] }));
    open(f);
    await listed(0, 0);
    expect(screen.getByText("还没有模型")).toBeTruthy();
    expect(screen.getByText("点“发现模型”，查找本机的 Harness 能用的模型。")).toBeTruthy();
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    expect(catalogue()).toBeTruthy();
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
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
  });
});

describe("the harness sections", () => {
  it("keeps an unavailable harness, its state and account when filters hide every model", async () => {
    const state = snapshot();
    state.buddies = state.buddies.map(entry => entry.harness === "claude"
      ? { ...entry, available: false, unavailableReason: { code: "harness.probe-failed", params: {} } } : entry);
    state.harnesses[0] = { ...state.harnesses[0], status: "login-required", available: false };
    const f = fixture(state, { buddy_list: () => page(state.buddies.filter(entry => !entry.available), state) });
    const user = userEvent.setup();
    open(f);
    await listed(0, 2);
    const section = within(catalogue()).getByRole("region", { name: "Claude Code" });
    const header = within(section.querySelector<HTMLElement>("[data-slot=harness-header]")!);
    expect(document.querySelector("[data-slot=harness-section]:last-of-type")).toBe(section);
    expect(header.getByText("未登录")).toBeTruthy();
    expect(within(section).getByText("anthropic")).toBeTruthy();
    expect(foldHandle("Claude Code").getAttribute("aria-expanded")).toBe("false");
    expect(section.textContent).toContain("1 个模型");
    expect(section.textContent).toContain("启用 1 / 2");
    expect(section.querySelector("[data-slot=model-row]")).toBeNull();
    await user.click(within(section).getByRole("button", { name: "Claude Code 账户" }));
    expect(within(section).getByRole("group", { name: "Claude Code 设置" })).toBeTruthy();
    await user.type(search(), "no-such-model");
    await listed(0, 0);
    expect(within(catalogue()).getByRole("region", { name: "Claude Code" })).toBe(section);
    expect(within(section).getByRole("group", { name: "Claude Code 设置" })).toBeTruthy();
    await user.clear(search());
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));
    await listed(0, 0);
    expect(header.getByText("未登录")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(screen.getByRole("checkbox", { name: /^显示不可用/ }));
    await listed(1, 2);
    expect(foldHandle("Claude Code").getAttribute("aria-expanded")).toBe("false");
    await user.click(foldHandle("Claude Code"));
    expect(modelButton(SONNET)).toBeTruthy();
    expect(f.command.mock.calls.every(([operation]) => operation === "buddy_list")).toBe(true);
  });
});

describe("the selected model", () => {
  it("opens no model until chosen and restores that drawer after filtering or folding", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    await selectModel(user, SOL, "Codex");
    await user.type(search(), "claude");
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    await user.clear(search());
    await waitFor(() => pane(SOL));
    expect(modelButton(SOL).getAttribute("aria-expanded")).toBe("true");
    await user.click(foldHandle("Codex"));
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    await user.click(foldHandle("Codex"));
    await waitFor(() => pane(SOL));
    await user.click(modelButton(SOL));
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
  });

  it("writes an unknown occupancy as unknown in the model row and drawer, never as 0", async () => {
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

describe("a buddy's own preference", () => {
  it("keeps the model row free of preference controls and retains a buddy control", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SOL, "Codex");
    expect(within(pane()).queryByRole("radiogroup", { name: "默认偏好" })).toBeNull();
    await user.click(effortCell("medium"));
    expect(within(levelPreference("medium")).getByRole("radio", { name: "不设" })).toBeTruthy();
    const pin = within(levelPreference("medium")).getByRole("radio", { name: "固定" });
    expect(pin.getAttribute("aria-disabled")).toBe("true");
    await user.click(levelSwitch("medium"));
    expect(pin.hasAttribute("aria-disabled")).toBe(false);
    await user.click(within(levelPreference("medium")).getByRole("radio", { name: "优先" }));
    await user.type(within(level("medium")).getByRole("textbox", { name: "medium 的偏好理由" }), "机械改动");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].preferenceChanges).toEqual([{ buddyId: "codex:openai:gpt-6-sol:medium", mode: "prefer", reason: "机械改动" }]);
    expect(f.published[0]).not.toHaveProperty("familyPreferenceChanges");
  });
});

describe("a session that cannot write", () => {
  it("shows every setting and changes none, and says why on the switch", async () => {
    const f = fixture(snapshot({ consoleSession: { id: "fixture-session", canWrite: false, reason: null } }));
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SONNET, "Claude Code");
    const lock = levelSwitch("medium");
    expect(lock.getAttribute("aria-checked")).toBe("true");
    expect(lock.getAttribute("aria-disabled")).toBe("true");
    expect(lock.getAttribute("aria-description")).toBe("浏览器授权已失效，重新授权后可以修改");
    await user.click(lock);
    expect(levelSwitch("medium").getAttribute("aria-checked")).toBe("true");
    expect(within(levelPreference("medium")).getAllByRole("radio").every(radio => radio.getAttribute("aria-disabled") === "true")).toBe(true);
    expect(within(pane()).getByRole("spinbutton", { name: "并发上限" })).toHaveProperty("readOnly", true);
    // The note stays readable and copyable, never editable.
    expect(note()).toHaveProperty("readOnly", true);
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("what the view says about its own reads", () => {
  const retired = buddy({ harness: "claude", provider: "anthropic", model: "claude-old" }, "high", { available: false, unavailableReason: { code: "catalog.native-unavailable", params: {} } });

  it("says that it is reading unavailable buddies, and shows them when the read ends", async () => {
    const state = snapshot({ unavailableBuddyCount: 1 });
    let finish!: (value: unknown) => void;
    const f = fixture(state, { buddy_list: () => new Promise(resolve => { finish = resolve; }) });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 1" }));
    expect(await screen.findByText("正在读取不可用的 buddy")).toBeTruthy();
    finish(page([...state.buddies, retired], state));
    await listed(1, 5);
    expect(screen.queryByText("正在读取不可用的 buddy")).toBeNull();
  });

  it("reports a failed read as a failure with a retry, and reads again only when asked", async () => {
    const state = snapshot({ unavailableBuddyCount: 1 });
    let attempt = 0;
    const f = fixture(state, { buddy_list: () => {
      attempt += 1;
      if (attempt === 1) throw new ApiError("NETWORK", "读取不可用的 buddy 失败");
      return page([...state.buddies, retired], state);
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
    const state = snapshot({ unavailableBuddyCount: 700 });
    const many = Array.from({ length: 600 }, (_, index) => buddy({ harness: "claude", provider: "anthropic", model: "claude-old" }, `e${index}`,
      { available: false, unavailableReason: { code: "catalog.native-unavailable", params: {} } }));
    const f = fixture(state, { buddy_list: () => ({ ...page(many, state), nextCursor: "more" }) });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    expect(screen.queryByText(/只显示前/)).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 700" }));
    expect(await screen.findByText("只显示前 600 个不可用的 buddy；更早的请用搜索查找。")).toBeTruthy();
    // The cut is a hard stop: one bounded read, no further page.
    expect(f.command.mock.calls.filter(([operation]) => operation === "buddy_list")).toHaveLength(1);
  });

  it("says why the discovery button is closed while a discovery is under way, on the same button", async () => {
    let finish!: () => void;
    const f = fixture(snapshot(), { model_catalog_refresh: () => new Promise<void>(resolve => { finish = resolve; }) });
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    const discover = screen.getByRole("button", { name: "发现模型" });
    expect(discover.getAttribute("aria-description")).toBeNull();
    await user.click(discover);
    await waitFor(() => expect(discover.getAttribute("aria-disabled")).toBe("true"));
    expect(screen.getByRole("button", { name: "发现模型" })).toBe(discover);
    expect(discover.getAttribute("aria-description")).toBe("发现进行中");
    await act(async () => { finish(); });
    await waitFor(() => expect(discover.hasAttribute("aria-disabled")).toBe(false));
    expect(discover.getAttribute("aria-description")).toBeNull();
  });

  it("reports a failed discovery as a failure and leaves the catalogue and the button usable", async () => {
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

describe("the inline model drawer", () => {
  it("chooses the first enabled effort, and selecting another never toggles it", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SONNET, "Claude Code");
    expect(effortCell("medium").getAttribute("aria-pressed")).toBe("true");
    await user.click(effortCell("high"));
    expect(effortCell("high").getAttribute("aria-pressed")).toBe("true");
    expect(levelSwitch("high").getAttribute("aria-checked")).toBe("false");
    expect(within(pane()).queryByRole("group", { name: "medium" })).toBeNull();
    expect(within(pane()).getByText("claude / anthropic / claude-sonnet-5 /", { exact: false })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("chooses the first effort when none is enabled and opens only one model", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SOL, "Codex");
    expect(effortCell("low").getAttribute("aria-pressed")).toBe("true");
    await selectModel(user, SONNET, "Claude Code");
    expect(document.querySelectorAll("[data-slot=model-drawer]")).toHaveLength(1);
    expect(modelButton(SOL).getAttribute("aria-expanded")).toBe("false");
  });

  it("marks edited settings and the effort cell outside field labels and carries marks to a closed row", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SONNET, "Claude Code");
    await user.click(levelSwitch("medium"));
    expect(within(effortCell("medium")).getByRole("img", { name: "未保存" })).toBeTruthy();
    expect(within(pane()).getAllByRole("img", { name: "未保存" }).every(mark => mark.closest("label") === null)).toBe(true);
    await user.click(modelButton(SONNET));
    expect(within(modelRow(SONNET)).getByRole("img", { name: "未保存" })).toBeTruthy();
  });
});

describe("inherited preference changes", () => {
  it("marks changed effective preferences on the effort strip and closed model row", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    await selectModel(user, SONNET, "Claude Code");
    await user.click(within(levelPreference("medium")).getByRole("radio", { name: "优先" }));
    expect(effortCell("medium").textContent).toContain("优先");
    expect(within(effortCell("medium")).getByRole("img", { name: "未保存" })).toBeTruthy();
    await user.click(modelButton(SONNET));
    expect(within(modelRow(SONNET)).getByRole("img", { name: "未保存" })).toBeTruthy();
    expect(within(modelRow(SONNET)).getByText("优先")).toBeTruthy();
  });
});

describe("keyboard catalogue handles", () => {
  it("opens and closes the row and selects an effort through the keyboard", async () => {
    const f = fixture();
    const user = userEvent.setup();
    open(f);
    await listed(1, 4);
    modelButton(SONNET).focus();
    await user.keyboard("{Enter}");
    expect(modelButton(SONNET).getAttribute("aria-expanded")).toBe("true");
    effortCell("high").focus();
    await user.keyboard(" ");
    expect(effortCell("high").getAttribute("aria-pressed")).toBe("true");
    expect(levelSwitch("high").getAttribute("aria-checked")).toBe("false");
    modelButton(SONNET).focus();
    await user.keyboard(" ");
    expect(modelButton(SONNET).getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});
