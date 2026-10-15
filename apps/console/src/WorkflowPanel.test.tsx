import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowPanel } from "./WorkflowPanel";
import type { IntegrationRecord, Workflow } from "./workflow-types";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";

afterEach(cleanup);

/** One verified integration binding, as `workflow_get` returns it. */
function verifiedIntegration(artifactId: string, overrides: Partial<IntegrationRecord> = {}): IntegrationRecord {
  return {
    integrationId: "int-" + artifactId, runId: "parent", artifactId, attemptId: "attempt-final",
    state: "verified", strategy: "cherry-pick",
    target: { kind: "checkout", path: "/target-repo", ref: "main", repositoryId: "repo-1", checkoutId: "co-1" },
    sourceCommit: "source-commit", sourceTree: "source-tree",
    beforeCommit: "before-commit", afterCommit: "after-commit", beforeTree: "before-tree", afterTree: "after-tree",
    verification: { verified: true, summary: "目标仓库集成测试通过" },
    notRequired: false, reason: "", actor: { role: "host", id: "host" }, createdAt: "2026-09-25",
    ...overrides,
  };
}

function fixture() {
  const task: Task = {
    runId: "parent", task: "完成状态内核", status: "waiting-host", owner: "host-a", cwd: "/repo",
    revision: 4, createdAt: "2026-09-22", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
    lineage: { kind: "micro-task", sourceHostId: "host-a", currentHostId: "host-a", parentRunId: null, rootRunId: "parent", rootLeadRunId: null,
      project: { id: "repo", path: "/repo", label: "repo" }, buddy: null },
    spec: { workspace: false }, shutdownConfirmed: true,
    workflow: { state: "awaiting-host", awaitingHost: true, hostId: "host-a", ownerGeneration: 2, revision: 7 },
  };
  const workflow: Workflow = {
    runId: task.runId, hostId: "host-a", ownerGeneration: 2, revision: 7,
    state: "awaiting-host", awaitingHost: true, waitReason: "assistance", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: "/repo", kind: "existing", access: "write", inputCommit: "commit-a", manifestSha256: "manifest-a" },
    currentTurn: { turnId: "turn-a", turnIndex: 1, attemptId: "attempt-a", resumeMode: "initial", summary: "需要补充测试" },
    activeRequest: { requestId: "request-a", kind: "assistance", state: "open", summary: "补充并发测试", attempted: "已跑基础测试", neededWork: ["并发测试"], expectedArtifacts: ["tests/concurrent.py"], acceptance: "测试覆盖竞争" },
    children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task,
  };
  const snapshot = {
    csrfToken: "fixture-csrf", eventCursor: 0,
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    buddies: [{ buddyId: "flash-max", label: "Flash 工作", enabled: true, available: true, harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max", capabilities: ["execution:dsh", "effort:max"] }],
  } as Snapshot;
  const command = vi.fn(async (op: string) => {
    if (op !== "workflow_get") throw new Error(`Unexpected mutation: ${op}`);
    return structuredClone(workflow);
  });
  const api = { command } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => snapshot);
  const props = { task, snapshot, api, refresh, selectTask: vi.fn() };
  return { props, workflow, command };
}

const REMOVED_CONTROLS = [
  "决定理由", "添加协助任务", "批准所列协助", "拒绝协助并记录理由",
  "交给下一回合的输入", "提交接续输入", "仍在执行的协助任务",
  "新的 Host ID", "移交控制权", "取消目标及协助任务",
  "实际检查依据", "接受最终交付", "记录验收问题",
  "填入已启用 buddy", "使用此 buddy 接续", "重试同一目标的路由",
];

describe("micro task workflow console (0.15.1 read-only)", () => {
  it("names a Lead-owned decision boundary and a waiting Lead", async () => {
    const f = fixture();
    f.workflow.task.lineage!.rootLeadRunId = "lead-1";
    f.workflow.task.lineage!.leadOwnership = { leadRunId: "lead-1", leadTurnId: "turn-1", createdAt: f.props.task.createdAt };
    render(<WorkflowPanel {...f.props} />);
    expect(await screen.findByText(/等待 Lead 决定：补充并发测试/)).toBeTruthy();
    expect(screen.getByText("等待 Lead 决定", { exact: true })).toBeTruthy();
    await userEvent.setup().click(screen.getByText("记录细节（微任务与回合）"));
    expect(screen.getByText("等待 Lead")).toBeTruthy();
  });

  it("names the Lead for a helper with no direct Lead ownership", async () => {
    const f = fixture();
    Object.assign(f.workflow.task.lineage!, { kind: "helper", parentRunId: "root-1", rootRunId: "root-1",
      rootLeadRunId: "lead-1", leadOwnership: null });
    render(<WorkflowPanel {...f.props} />);
    expect(await screen.findByText(/等待 Lead 决定：补充并发测试/)).toBeTruthy();
    expect(screen.getByText("等待 Lead 决定", { exact: true })).toBeTruthy();
    await userEvent.setup().click(screen.getByText("记录细节（微任务与回合）"));
    expect(screen.getByText("等待 Lead")).toBeTruthy();
  });

  it("shows the recorded turn count without deriving a model-call count", async () => {
    const f = fixture();
    Object.assign(f.workflow, { counts: { turns: 3, openRequests: 1 }, continuationCount: 3 });
    render(<WorkflowPanel {...f.props} />);
    expect(await screen.findByText("共 3 个回合")).toBeTruthy();
    expect(screen.queryByText(/模型调用次数：/)).toBeNull();
  });

  it("renders only the recorded activity projection and never a completion estimate", async () => {
    const f = fixture();
    Object.assign(f.props.task, {
      activity: { phase: "tool-running", observedAt: "2026-09-25T10:00:00Z", lastToolActivityAt: "2026-09-25T09:59:00Z",
        toolName: "apply_patch", counts: { toolCalls: 2 } },
      terminationReason: "deadline",
      status: "cancelled",
    });
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(screen.getByRole("tab", { name: "概览" }));
    const view = screen.getByRole("region", { name: "执行活动（只读）" });
    expect(view.textContent).toContain("工具执行中");
    expect(view.textContent).toContain("apply_patch");
    expect(view.textContent).not.toMatch(/\d+\s*%/);
    expect(f.command.mock.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
  });

  it("shows the recorded native session evidence in the execution detail", async () => {
    const f = fixture();
    Object.assign(f.props.task, {
      selectedAttempt: {
        attemptId: "attempt-a", adapter: "zcode",
        result: {
          status: "ok", shutdownConfirmed: true,
          result: {
            nativeSession: {
              harness: "zcode", sessionId: "sess-z", captured: true, storageScope: "task-private",
              storageOwner: "hey-my-buddy-attempt", nativeAppVisibility: "not-listed-in-native-app",
              resumeMode: "native-session", bindingPresent: true, resumable: true,
            },
          },
        },
      },
    });
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(await screen.findByRole("tab", { name: "执行记录" }));
    const view = screen.getByRole("region", { name: "原生会话（只读）" });
    expect(view.textContent).toContain("sess-z");
    expect(view.textContent).toContain("未在原生 App 中列出");
  });

  it("absent Host controls: no write form anywhere in the microTask detail", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await screen.findByRole("tablist");
    for (const tab of ["概览", "路由依据", "协作与待办", "产物与验收", "执行记录"]) {
      await user.click(screen.getByRole("tab", { name: tab }));
      for (const label of REMOVED_CONTROLS) {
        const field = screen.queryByLabelText(label);
        if (field) throw new Error(`${tab} still exposes the removed control "${label}"`);
      }
    }
    for (const name of REMOVED_CONTROLS) {
      if (screen.queryByRole("button", { name })) throw new Error(`removed button "${name}" is still rendered`);
    }
    expect(f.command.mock.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
  });

  it("keeps the open assistance request readable and points the Host to its CLI flow", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(await screen.findByRole("tab", { name: "协作与待办" }));
    expect(screen.getByText("补充并发测试")).toBeTruthy();
    expect(screen.getByText("并发测试")).toBeTruthy();
    expect(screen.getByText("等待 Host 决定")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "批准所列协助" })).toBeNull();
  });

  it("shows a routing boundary read-only instead of the removed retry form", async () => {
    const f = fixture();
    f.workflow.routing = { status: "needs-host", reason: "Configure the fixed selector" };
    f.workflow.activeRequest = { ...f.workflow.activeRequest!, kind: "routing" };
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(await screen.findByRole("tab", { name: "路由依据" }));
    expect(await screen.findByText(/路由需要 Host 补充 buddy/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试同一目标的路由" })).toBeNull();
  });

  it("keeps artifacts and integration evidence readable with acceptance recorded by the Host", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final" });
    f.workflow.artifacts = [
      { artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "final-hash" },
    ];
    f.workflow.integrations = [verifiedIntegration("final")];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(await screen.findByRole("tab", { name: "产物与验收" }));
    expect(await screen.findByText(/cherry-pick/)).toBeTruthy();
    expect(screen.getByText(/目标仓库集成测试通过/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "接受最终交付" })).toBeNull();
    expect(f.command.mock.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
  });

  it("keeps shutdown uncertainty honest and records the acceptance verdict as a fact", async () => {
    const f = fixture();
    f.workflow.shutdown = { selfConfirmed: true, descendantsConfirmed: false, unconfirmedRunIds: ["child"], unconfirmedCount: 1, truncated: false };
    f.props.task.acceptanceVerdict = "accepted";
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(await screen.findByRole("tab", { name: "概览" }));
    expect(screen.getByText(/微任务范围内共 1 项执行尚未核实/)).toBeTruthy();
    expect(screen.getByText("已验收")).toBeTruthy();
  });

  it("hides the whole execution-buddy row for a command execution's record details and keeps a recorded model buddy", async () => {
    const f = fixture();
    // The board's buddy record for a command execution is the bare adapter
    // marker (protocol buddy_constraints), which names no model; the detail's
    // own spec carries no adapter to judge from.
    const command = async <T,>(operation: string, _params: unknown, _csrfToken: string): Promise<T> => {
      if (operation !== "workflow_get") throw new Error(`Unexpected mutation: ${operation}`);
      return { ...structuredClone(f.workflow), buddy: { adapter: "command" } } as T;
    };
    const user = userEvent.setup();
    const view = render(<WorkflowPanel {...f.props} api={{ ...f.props.api, command }} />);
    await user.click(await screen.findByText("记录细节（微任务与回合）"));
    const facts = view.container.querySelector(".record-facts")!;
    expect(facts.textContent).toContain("输入提交");
    expect(screen.queryByText("执行 buddy")).toBeNull();
    expect(facts.textContent).not.toMatch(/undefined|buddy 来源未记录/);
    view.unmount();

    const model = fixture();
    model.workflow.buddy = { harness: "codex", provider: "openai", model: "gpt-6", effort: "high" };
    const second = render(<WorkflowPanel {...model.props} />);
    await user.click(await screen.findByText("记录细节（微任务与回合）"));
    expect(screen.getByText("执行 buddy")).toBeTruthy();
    expect(second.container.querySelector(".record-facts")!.textContent).toContain("codex · gpt-6 / high");
  });

  it("shows a recorded review rejection without an acceptance verdict", async () => {
    const f = fixture();
    f.props.task.reviewRejectedAt = "2026-09-26T07:25:00Z";
    f.props.task.reviewRejectionNote = "需要补充验证";
    render(<WorkflowPanel {...f.props} initialSection="overview" />);
    expect(await screen.findByText("验收问题")).toBeTruthy();
  });
});

 it("switches the recorded turn summary in place and resets for a different turn", async () => {
  const f = fixture();
  const summary = "回合摘要正文。".repeat(70);
  f.workflow.currentTurn!.summary = summary;
  const user = userEvent.setup();
  render(<WorkflowPanel {...f.props} initialSection="overview" />);
  await screen.findByText("记录细节（微任务与回合）");
  await user.click(screen.getByText("记录细节（微任务与回合）"));
  const button = await screen.findByRole("button", { name: "展开" });
  const paragraph = button.closest("p")!;
  expect(paragraph.textContent).toBe(summary.slice(0, 360) + "… 展开");
  const details = paragraph.closest("details")!;
  expect(details.querySelectorAll(".task-description")).toHaveLength(1);
  expect(within(details).queryByText(summary)).toBeNull();
  await user.click(button);
  expect(paragraph.textContent).toBe(summary + " 收起");
  expect(within(details).getAllByText(summary)).toHaveLength(1);
  expect(screen.getByRole("button", { name: "收起" }).getAttribute("aria-expanded")).toBe("true");
  await user.click(screen.getByRole("button", { name: "收起" }));
  expect(paragraph.textContent).toBe(summary.slice(0, 360) + "… 展开");
  await user.click(screen.getByRole("button", { name: "展开" }));
  const readsBeforeTurnChange = f.command.mock.calls.length;
  f.workflow.currentTurn!.turnId = "turn-b";
  // Remote workflow changes arrive on its own 3-second read cadence.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 3000)); });
  expect(f.command).toHaveBeenCalledTimes(readsBeforeTurnChange + 1);
  await expect(f.command.mock.results.at(-1)!.value).resolves.toMatchObject({ currentTurn: { turnId: "turn-b" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "展开" }).getAttribute("aria-expanded")).toBe("false"));
 });
