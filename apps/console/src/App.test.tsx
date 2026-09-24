import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { ApiError } from "./api";
import type { Snapshot, TaskQuery, WriterGrant } from "./types";

const initial = (): Snapshot => ({
  csrfToken: "fixture-csrf",
  tableRevision: 2,
  gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
  configuration: {
    revision: 1,
    decisionProfileId: "flash-off",
    autoMaintain: false,
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
      sampleCount: 0,
      updatedAt: null,
    },
  ],
  preferences: [],
  evidence: [],
  decisions: [],
  pendingEvidence: 0,
  tasks: { runs: [], total: 0 },
  capabilities: { selection: false, maintenance: false },
});

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("console interactions", () => {
  it("recovers the same editor intent when the committed grant response was lost", async () => {
    const state = initial();
    const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing", tableRevision: 2, expiresAt: new Date(Date.now() + 120000).toISOString() };
    let calls = 0;
    const command = vi.fn(async () => {
      state.gate = { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } };
      if (++calls === 1) throw new ApiError("INVALID_RESPONSE", "lost grant");
      return grant;
    });
    const api = { snapshot: vi.fn(async () => structuredClone(state)), command, task: vi.fn(),
      tasks: vi.fn(async () => ({ ...state.tasks, nextCursor: null })) } as unknown as ConsoleApi;
    window.location.hash = "#models";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "编辑评价表" }));
    await screen.findByText("lost grant");
    await user.click(screen.getByRole("button", { name: "编辑评价表" }));
    await screen.findByRole("button", { name: "发布新版本" });
    expect(command).toHaveBeenCalledTimes(2);
    expect(command.mock.calls[1]).toEqual(command.mock.calls[0]);
  });

  it("reuses the original decision request after an ambiguous network response", async () => {
    const state = initial();
    state.capabilities.selection = true;
    const command = vi
      .fn()
      .mockRejectedValueOnce(new ApiError("NETWORK", "lost response"))
      .mockResolvedValueOnce({ decisionId: "durable", runId: "run" });
    const api = {
      snapshot: vi.fn(async () => state),
      command,
      task: vi.fn(),
      tasks: vi.fn(async () => ({ ...state.tasks, nextCursor: null })),
    } as unknown as ConsoleApi;
    window.location.hash = "#settings";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.type(
      await screen.findByLabelText("任务与约束"),
      "只读检查状态管理边界",
    );
    await user.click(screen.getByRole("button", { name: "请求推荐" }));
    await user.click(
      await screen.findByRole("button", { name: "重试同一推荐请求" }),
    );
    await screen.findByText("请求已记录，可在本页的最近决策中查看进度。");
    expect(command).toHaveBeenCalledTimes(2);
    expect(command.mock.calls[1]).toEqual(command.mock.calls[0]);
    expect(command.mock.calls[0][1].task).toBe("只读检查状态管理边界");
  });

  it("records a scoped observation using the real evidence command contract without taking a writer", async () => {
    const state = initial();
    const command = vi.fn(async (operation: string, params: any) => {
      if (
        operation !== "evaluation_evidence_record" ||
        params.kind !== "observation"
      )
        throw new Error("unsupported evidence command");
      state.pendingEvidence = 1;
      return { verified: false, counted: false };
    });
    const api = {
      snapshot: vi.fn(async () => structuredClone(state)),
      command,
      task: vi.fn(),
      tasks: vi.fn(async () => ({ ...state.tasks, nextCursor: null })),
    } as unknown as ConsoleApi;
    window.location.hash = "#models";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: /Flash 决策/ }));
    await user.click(screen.getByRole("tab", { name: "证据" }));
    await user.type(
      screen.getByLabelText("补充观察"),
      "一次有边界的观察，不是验收成绩",
    );
    await user.type(screen.getByLabelText("项目来源（可选）"), "示例项目");
    await user.type(
      screen.getByLabelText("适用条件（每行一条）"),
      "React 状态管理{Enter}只读调查",
    );
    await user.click(screen.getByRole("button", { name: "记录待整理观察" }));
    await waitFor(() =>
      expect(screen.getByLabelText("补充观察")).toHaveProperty("value", ""),
    );
    expect(command).toHaveBeenCalledExactlyOnceWith(
      "evaluation_evidence_record",
      expect.objectContaining({
        profileId: "flash-off",
        kind: "observation",
        summary: "一次有边界的观察，不是验收成绩",
        source: "user",
        project: "示例项目",
        conditions: ["React 状态管理", "只读调查"],
      }),
      "fixture-csrf",
    );
  });

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

  it("viewing and switching pages never sends a write or model request", async () => {
    const api = {
      snapshot: vi.fn(async () => initial()),
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
    expect(
      await screen.findByRole("button", { name: "请求推荐" }),
    ).toHaveProperty("disabled", true);
    expect(api.command).not.toHaveBeenCalled();
  });

  it("keeps typed multiline text in the draft across refresh and publishes no fabricated counters", async () => {
    let state = initial();
    state.evidence = [
      {
        evidenceId: "ev-fixture",
        profileId: "flash-off",
        kind: "observation",
        summary: "单次 React 观察",
        project: "fixture",
        conditions: ["React"],
        source: "user",
        runId: null,
        createdAt: "2026-09-22T00:00:00Z",
      },
    ];
    let published: Record<string, any> | null = null;
    const grant: WriterGrant = {
      writerId: "fixture-writer",
      generation: 1,
      writerToken: "fixture-secret",
      phase: "writing",
      tableRevision: 2,
      expiresAt: new Date(Date.now() + 120000).toISOString(),
    };
    const command = vi.fn(async (operation: string, params: any) => {
      if (operation === "evaluation_write_begin") {
        state = {
          ...state,
          gate: {
            ...state.gate,
            phase: "writing",
            writer: { ...grant, kind: "human" },
          },
        };
        return grant;
      }
      if (operation === "evaluation_write_publish") {
        published = params;
        state = {
          ...state,
          tableRevision: 3,
          gate: { ...state.gate, phase: "open", writer: null },
        };
        return { tableRevision: 3 };
      }
      return grant;
    });
    const api = {
      snapshot: vi.fn(async () => structuredClone(state)),
      command,
      task: vi.fn(),
      tasks: vi.fn(async () => ({ ...state.tasks, nextCursor: null })),
    } as unknown as ConsoleApi;
    window.location.hash = "#models";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await user.click(await screen.findByRole("button", { name: "编辑评价表" }));
    await user.click(await screen.findByRole("button", { name: /Flash 决策/ }));
    await user.click(screen.getByRole("tab", { name: "能力评价" }));
    const input = await screen.findByLabelText("适用工作");
    await waitFor(() => expect(input).toHaveProperty("readOnly", false));
    await user.type(input, "状态管理{Enter}并发测试");
    await user.click(screen.getByRole("tab", { name: "证据" }));
    await user.click(screen.getByLabelText("作为卡片依据"));
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    await user.click(screen.getByRole("tab", { name: "能力评价" }));
    expect(screen.getByLabelText("适用工作")).toHaveProperty("value", "状态管理\n并发测试");
    await user.click(screen.getByRole("button", { name: "发布新版本" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(published!.cards[0].strengths).toEqual(["状态管理", "并发测试"]);
    expect(published!.cards[0].evidenceIds).toEqual(["ev-fixture"]);
    expect(published!.cards[0]).not.toHaveProperty("sampleCount");
    expect(published!.expectedRevision).toBe(2);
  });
});
