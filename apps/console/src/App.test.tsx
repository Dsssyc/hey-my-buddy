import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Snapshot, TaskQuery } from "./types";

const initial = (): Snapshot => ({
  csrfToken: "fixture-csrf",
  tableRevision: 2,
  gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
  configuration: {
    revision: 1,
    decisionProfileId: "flash-off",
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
  evidence: [],
  decisions: [],
  sampleCounts: { "flash-off": 3 },
  tasks: { runs: [], total: 0 },
  capabilities: { selection: false, maintenance: false, evaluationWriteGate: true },
});

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("console interactions", () => {
  it("retries a never-claimed cancellation without asking to accept nonexistent output", async () => {
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
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByLabelText("显示协助任务与内部执行"));
    await user.click(
      await screen.findByRole("button", { name: /未执行的测试任务/ }),
    );
    const retry = await screen.findByRole("button", { name: "重新尝试" });
    expect(retry).toHaveProperty("disabled", false);
    expect(screen.queryByRole("heading", { name: "记录验收" })).toBeNull();
    await user.click(retry);
    expect(api.command).toHaveBeenCalledWith(
      "task_retry",
      { runId: "cancelled-before-claim" },
      "fixture-csrf",
    );
    await user.click(screen.getByRole("button", { name: "待验收" }));
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
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
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
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "选择一项委派" });
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("link", { name: "路由配置" }));
    expect(await screen.findByLabelText("决策模型配置")).toHaveProperty("disabled", true);
    expect(screen.queryByRole("button", { name: "请求推荐" })).toBeNull();
    expect(screen.queryByRole("button", { name: "请求整理" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "最近决策" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "自动采纳常规整理结果" })).toBeNull();
    expect(screen.queryByText(/整理经验/)).toBeNull();
    // Turning the switch on only creates a local draft shared across pages.
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    await user.click(await screen.findByRole("button", { name: /Flash 决策/ }));
    await user.click(screen.getByRole("tab", { name: "能力评价" }));
    await user.type(await screen.findByLabelText("当前评价"), "本地草稿");
    await user.click(screen.getByRole("link", { name: "路由配置" }));
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    expect(screen.getByLabelText("当前评价")).toHaveProperty("value", "待积累实际证据本地草稿");
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(await screen.findByRole("button", { name: "放弃修改" }));
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
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("button", { name: /Flash 决策/ }));
    await user.click(screen.getByRole("tab", { name: "证据" }));
    expect(await screen.findByText("一条只读证据")).toBeTruthy();
    expect(screen.queryByLabelText("补充观察")).toBeNull();
    expect(screen.queryByLabelText("作为卡片依据")).toBeNull();
    expect(screen.queryByRole("button", { name: "记录待整理观察" })).toBeNull();
    expect(screen.queryByRole("button", { name: "评价维护" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "更新记录" }));
    await screen.findByText("还没有已发布的评价版本。");
    expect(command.mock.calls.map(([operation]) => operation)).toEqual(["evaluation_history"]);
  });
});
