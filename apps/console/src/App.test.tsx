import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task, TaskQuery } from "./types";
import { listed, note, pane, selectModel } from "./buddies/page.testing";

const FLASH = "dsh / deepseek-official / deepseek-flash";

const initial = (): Snapshot => ({
  csrfToken: "fixture-csrf", eventCursor: 0,
  consoleSession: { id: "fixture-session", canWrite: true, reason: null },

  configuration: { routerBuddyIds: ["flash-off"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard",
  },
  buddies: [
    {
      buddyId: "flash-off",
      label: "Flash 决策",
      harness: "dsh",
      provider: "deepseek-official",
      model: "deepseek-flash",
      effort: "off",
      available: true,
      enabled: true,
      capabilities: ["text"],
      contextWindow: 1000000,
      source: "fixture",
      description: "",
    },
  ],

  preferences: [],
      modelServiceNotes: [],

  decisions: [],

  modelServiceConcurrency: [{ harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 0 }],
  tasks: { pendingCount: 0 },
  capabilities: { selection: false },
});

const emptyMacroTasks = () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("console interactions", () => {
  it("renders a never-claimed cancellation read-only, without retry or acceptance controls", async () => {
    const state = initial();
    const records: Task[] = [
      {
        runId: "cancelled-before-claim",
        task: "未执行的测试任务",
        status: "cancelled",
        owner: "fixture",
        cwd: "/fixture",
        revision: 2,
        createdAt: "2026-09-22T00:00:00Z",
        acceptedAt: null,
        acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
        activeAttemptId: null,
        selectedAttemptId: null,
        shutdownConfirmed: false,
        resultAvailable: false,
      },
    ];
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => state),
      command: vi.fn(async () => ({})),
      task: vi.fn(async () => records[0]),
      tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : records,
        total: rootsOnly ? 0 : records.length, nextCursor: null })),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await user.click(await screen.findByLabelText("显示协助任务与内部执行"));
    await user.click(
      await screen.findByRole("button", { name: /未执行的测试任务/ }),
    );
    // 0.15.1 U4: task_retry/task_cancel/task_acknowledge are gone from the browser.
    expect(screen.queryByRole("button", { name: "重新尝试" })).toBeNull();
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "记录验收" })).toBeNull();
    expect(api.command).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "等待验收" }));
    expect(
      await screen.findByRole("heading", { name: "没有匹配的微任务" }),
    ).toBeTruthy();
  });

  it("renders the persisted worker receipt and refuses retry when a process may survive", async () => {
    const state = initial();
    const records: Task[] = [
      {
        runId: "unconfirmed-run",
        task: "停止证据测试",
        status: "failed",
        owner: "fixture",
        cwd: "/fixture",
        revision: 3,
        createdAt: "2026-09-22T00:00:00Z",
        acceptedAt: null,
        acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
        activeAttemptId: "attempt",
        selectedAttemptId: "attempt",
        shutdownConfirmed: false,
        resultAvailable: true,
      },
    ];
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => state),
      command: vi.fn(),
      task: vi.fn(async () => ({
        ...records[0],
        selectedAttempt: {
          result: {
            status: "failed",
            result: { finalText: "真实回执中的产物说明" },
            shutdownConfirmed: false,
          },
        },
      })),
      tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : records,
        total: rootsOnly ? 0 : records.length, nextCursor: null })),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await user.click(await screen.findByLabelText("显示协助任务与内部执行"));
    await user.click(
      await screen.findByRole("button", { name: /停止证据测试/ }),
    );
    expect(await screen.findByText("真实回执中的产物说明")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重新尝试" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "记录验收" })).toBeNull();
    expect(api.command).not.toHaveBeenCalled();
  });

  it("viewing, editing locally and switching pages never sends a write or model request", async () => {
    const state = initial();
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(state)),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await screen.findByRole("heading", { name: "选择一项微任务" });
    await user.click(screen.getByRole("link", { name: /^Buddy/ }));
    await listed(1, 1);
    // There is no global edit switch: controls are directly editable, and no
    // save bar appears until the user actually changes something.
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "本地草稿");
    expect(await screen.findByRole("region", { name: "未保存的修改" })).toBeTruthy();
    await user.click(screen.getByRole("link", { name: "设置" }));
    // The save bar belongs to Buddy 配置 alone; the draft is kept without any
    // leave confirmation and no save happens by navigating away.
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    await user.click(screen.getByRole("link", { name: /Buddy/ }));
    expect(note()).toHaveProperty("value", "本地草稿");
    expect(screen.getByRole("region", { name: "未保存的修改" })).toBeTruthy();
    expect(within(pane()).getAllByRole("textbox")).toHaveLength(2);
    expect(within(pane()).getByRole("textbox", { name: "off 的偏好理由" })).toHaveProperty("readOnly", true);
    await user.click(screen.getByRole("button", { name: "放弃" }));
    expect(api.command).not.toHaveBeenCalled();
  });

  it("addresses each page by its own name and opens the tasks for any other hash", async () => {
    const state = initial();
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(state)),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    const first = render(<App suppliedApi={api} />);
    await listed(1, 1);
    expect(screen.getByRole("heading", { name: "Buddy" })).toBeTruthy();
    first.unmount();
    // A retired bookmark is a hash like any other the console does not know.
    window.location.hash = "#models";
    const second = render(<App suppliedApi={api} />);
    expect((await screen.findByRole("link", { name: "任务" })).getAttribute("aria-current")).toBe("page");
    second.unmount();
    window.location.hash = "#settings";
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "显示" });
    await user.click(screen.getByRole("link", { name: /^Buddy/ }));
    await listed(1, 1);
    await user.click(screen.getByRole("link", { name: "设置" }));
    await screen.findByRole("heading", { name: "显示" });
    expect(window.location.hash).toBe("#settings");
    expect(api.command).not.toHaveBeenCalled();
  });

  it("offers no maintenance or evidence-submission action anywhere", async () => {
    const state = initial();
    const command = vi.fn(async (operation: string) => {
      throw new Error(`Unexpected command: ${operation}`);
    });
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(state)),
      command,
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={api} />);
    await listed(1, 1);
    await selectModel(user, FLASH, "DSH");
    expect(screen.queryByRole("button", { name: "记录待整理观察" })).toBeNull();
    expect(screen.queryByRole("button", { name: "评价维护" })).toBeNull();
    // The drawer's only button selects its one native reasoning effort; it sends no request.
    expect(within(pane()).getAllByRole("button").map(button => button.getAttribute("aria-label"))).toEqual(["off"]);
    expect(command).not.toHaveBeenCalled();
  });

  // D2-A1/A2: a hidden bootstrap leaves startup or shows the existing error UI.
  it.each([false, true])("finishes the hidden bootstrap through the existing App presentation (failure=%s)", async failure => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const api = {
      snapshot: failure ? vi.fn(async () => { throw new Error("fixture offline"); }) : vi.fn(async () => initial()),
      command: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    render(<App suppliedApi={api} />);
    if (failure) {
      expect(await screen.findByRole("heading", { name: "暂时无法连接黑板" })).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toBe("fixture offline");
      expect(screen.getByRole("button", { name: "重新连接" })).toBeTruthy();
      expect(screen.queryByRole("navigation", { name: "主要导航" })).toBeNull();
    } else {
      expect(await screen.findByRole("navigation", { name: "主要导航" })).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    }
    expect(screen.queryByRole("heading", { name: "正在连接本地黑板" })).toBeNull();
    expect(screen.queryByText("正在连接…")).toBeNull();
    expect(api.snapshot).toHaveBeenCalledTimes(1);
    expect(api.command).not.toHaveBeenCalled();
  });
});
