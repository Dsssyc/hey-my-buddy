import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Snapshot, TaskQuery } from "./types";

const initial = (): Snapshot => ({
  csrfToken: "fixture-csrf",
  consoleSession: { id: "fixture-session", canWrite: true, reason: null },
  tableRevision: 2,
  gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
  configuration: {
    revision: 1,
    fastRouterProfileId: null, reviewRouterProfileId: "flash-off", defaultRoutingMode: "review" as const, routingBudget: "standard",
  },
  profiles: [
    {
      profileId: "flash-off",
      label: "Flash 决策",
      adapter: "dsh",
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
  cards: [
    {
      profileId: "flash-off",
      revision: 2,
      summary: "待积累实际证据",
      strengths: [],
      limitations: [],
      risks: [],
      evidenceIds: [],
      updatedAt: null,
    },
  ],
  preferences: [],
  familyPreferences: [],
  preferenceOverrides: [],
  familyAnnotations: [],
  evidence: [],
  decisions: [],
  sampleCounts: { "flash-off": 3 },
  modelConcurrency: [{ adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 0 }],
  tasks: { runs: [], total: 0 },
  capabilities: { selection: false, maintenance: false, evaluationWriteGate: true },
});

const emptyObjectives = () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false });

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("console interactions", () => {
  it("renders a never-claimed cancellation read-only, without retry or acceptance controls", async () => {
    const state = initial();
    state.tasks = {
      total: 1,
      runs: [
        {
          runId: "cancelled-before-claim",
          task: "未执行的测试任务",
          status: "cancelled",
          owner: "fixture",
          cwd: "/fixture",
          revision: 2,
          createdAt: "2026-09-22T00:00:00Z",
          acceptedAt: null,
          acceptanceVerdict: null,
          activeAttemptId: null,
          selectedAttemptId: null,
          shutdownConfirmed: false,
          resultAvailable: false,
        },
      ],
    };
    const api = {
      snapshot: vi.fn(async () => state),
      command: vi.fn(async () => ({})),
      task: vi.fn(async () => state.tasks.runs[0]),
      tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : state.tasks.runs,
        total: rootsOnly ? 0 : state.tasks.total, nextCursor: null })),
      objectives: vi.fn(async () => emptyObjectives()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
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
      await screen.findByRole("heading", { name: "没有匹配的委派" }),
    ).toBeTruthy();
  });

  it("renders the persisted worker receipt and refuses retry when a process may survive", async () => {
    const state = initial();
    state.tasks = {
      total: 1,
      runs: [
        {
          runId: "unconfirmed-run",
          task: "停止证据测试",
          status: "failed",
          owner: "fixture",
          cwd: "/fixture",
          revision: 3,
          createdAt: "2026-09-22T00:00:00Z",
          acceptedAt: null,
          acceptanceVerdict: null,
          activeAttemptId: "attempt",
          selectedAttemptId: "attempt",
          shutdownConfirmed: false,
          resultAvailable: true,
        },
      ],
    };
    const api = {
      snapshot: vi.fn(async () => state),
      command: vi.fn(),
      task: vi.fn(async () => ({
        ...state.tasks.runs[0],
        selectedAttempt: {
          result: {
            status: "failed",
            result: { finalText: "真实回执中的产物说明" },
            shutdownConfirmed: false,
          },
        },
      })),
      tasks: vi.fn(async ({ rootsOnly }: TaskQuery) => ({ runs: rootsOnly ? [] : state.tasks.runs,
        total: rootsOnly ? 0 : state.tasks.total, nextCursor: null })),
      objectives: vi.fn(async () => emptyObjectives()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
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
      snapshot: vi.fn(async () => structuredClone(state)),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      objectives: vi.fn(async () => emptyObjectives()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await screen.findByRole("heading", { name: "选择一项委派" });
    await user.click(screen.getByRole("link", { name: "Buddy 配置" }));
    await screen.findByRole("heading", { name: "模型 1" });
    // There is no global edit switch: controls are directly editable, and no
    // save bar appears until the user actually changes something.
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: /Flash 决策/ }));
    await user.type(await screen.findByLabelText("家族备注"), "本地草稿");
    expect(await screen.findByRole("region", { name: "未保存的修改" })).toBeTruthy();
    await user.click(screen.getByRole("link", { name: "设置" }));
    // The save bar belongs to Buddy 配置 alone; the draft is kept without any
    // leave confirmation and no save happens by navigating away.
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "本地草稿");
    expect(screen.getByRole("region", { name: "未保存的修改" })).toBeTruthy();
    // The automatic assessment is program-owned and stays read-only.
    expect(screen.queryByLabelText("当前评价")).toBeNull();
    expect(screen.getAllByText("待积累实际证据").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "放弃" }));
    expect(api.command).not.toHaveBeenCalled();
  });

  it("opens Buddy 配置 from the retired #settings and #models bookmarks and keeps 设置 at #system", async () => {
    const state = initial();
    const api = {
      snapshot: vi.fn(async () => structuredClone(state)),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      objectives: vi.fn(async () => emptyObjectives()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    // The old 路由配置 bookmark opens Buddy 配置, where its settings moved.
    window.location.hash = "#settings";
    const first = render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    expect(screen.getByRole("heading", { name: "Buddy 配置" })).toBeTruthy();
    first.unmount();
    // The old 模型卡片 bookmark keeps landing on the same successor.
    window.location.hash = "#models";
    const second = render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    second.unmount();
    // The system settings page keeps a hash of its own, addressable on reload.
    window.location.hash = "#system";
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "显示" });
    await user.click(screen.getByRole("link", { name: "Buddy 配置" }));
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("link", { name: "设置" }));
    await screen.findByRole("heading", { name: "显示" });
    expect(window.location.hash).toBe("#system");
    expect(api.command).not.toHaveBeenCalled();
  });

  it("offers no maintenance or evidence-submission action anywhere", async () => {
    const state = initial();
    state.evidence = [{ evidenceId: "ev-1", profileId: "flash-off", kind: "observation",
      summary: "一条只读证据", project: null, conditions: [], source: "user", runId: null,
      createdAt: "2026-09-22T00:00:00Z" }];
    const command = vi.fn(async (operation: string) => {
      if (operation === "evaluation_history") return { revisions: [], nextCursor: null, total: 0 };
      throw new Error(`Unexpected command: ${operation}`);
    });
    const api = {
      snapshot: vi.fn(async () => structuredClone(state)),
      command,
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      objectives: vi.fn(async () => emptyObjectives()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("button", { name: /Flash 决策/ }));
    await user.click(screen.getAllByText("待积累实际证据", { exact: false })[0]);
    expect(await screen.findByText("一条只读证据")).toBeTruthy();
    expect(screen.queryByLabelText("补充观察")).toBeNull();
    expect(screen.queryByLabelText("作为卡片依据")).toBeNull();
    expect(screen.queryByRole("button", { name: "记录待整理观察" })).toBeNull();
    expect(screen.queryByRole("button", { name: "评价维护" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "更新记录" }));
    await screen.findByText("暂无已发布评价");
    expect(command.mock.calls.map(([operation]) => operation)).toEqual(["evaluation_history"]);
  });
});
