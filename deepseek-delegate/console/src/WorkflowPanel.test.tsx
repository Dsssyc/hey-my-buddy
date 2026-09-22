import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowPanel } from "./WorkflowPanel";
import type { Workflow } from "./workflow-types";
import type { ConsoleApi } from "./api";
import { ApiError } from "./api";
import type { Snapshot, Task } from "./types";

afterEach(cleanup);

function fixture() {
  const task: Task = {
    runId: "parent", task: "完成状态内核", status: "waiting-host", owner: "host-a", cwd: "/repo",
    revision: 4, createdAt: "2026-09-22", acceptedAt: null, acceptanceVerdict: null,
    spec: { workspace: false }, shutdownConfirmed: true,
    workflow: { state: "awaiting-host", awaitingHost: true, hostId: "host-a", ownerGeneration: 2, revision: 7 },
  };
  const workflow: Workflow = {
    governed: true, runId: task.runId, hostId: "host-a", ownerGeneration: 2, revision: 7,
    state: "awaiting-host", awaitingHost: true, waitReason: "assistance", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: "/repo", kind: "existing", access: "write", inputCommit: "commit-a", manifestSha256: "manifest-a" },
    currentTurn: { turnId: "turn-a", turnIndex: 1, attemptId: "attempt-a", resumeMode: "initial", summary: "需要补充测试" },
    activeRequest: { requestId: "request-a", kind: "assistance", state: "open", summary: "补充并发测试", attempted: "已跑基础测试", neededWork: ["并发测试"], expectedArtifacts: ["tests/concurrent.py"], acceptance: "测试覆盖竞争" },
    children: [], artifacts: [], finalArtifactId: null, finalAttemptId: null, task,
  };
  const snapshot = {
    csrfToken: "fixture-csrf",
    profiles: [{ profileId: "flash-max", label: "Flash 工作", enabled: true, available: true, adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max" }],
  } as Snapshot;
  const mutation = vi.fn(async (_op: string, _params: unknown) => ({}));
  const command = vi.fn(async (op: string, params: unknown) => op === "workflow_get" ? structuredClone(workflow) : mutation(op, params));
  const api = { command } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => snapshot);
  const props = { task, snapshot, api, refresh, selectTask: vi.fn() };
  return { props, workflow, mutation, command };
}

describe("governed workflow console", () => {
  it("pins helper configuration and workspace in an explicit user decision", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("决定理由"), "需要独立测试补充");
    await user.click(screen.getByRole("button", { name: "添加协助任务" }));
    await user.selectOptions(screen.getByLabelText("执行配置"), "flash-max");
    await user.type(screen.getByLabelText("工作内容与验收条件"), "补充并发测试并运行 pytest");
    await user.type(screen.getByLabelText("允许写入的相对路径（每行一项）"), "tests/concurrent.py");
    await user.click(screen.getByRole("button", { name: "批准所列协助" }));
    expect(f.mutation).toHaveBeenCalledTimes(1);
    const [op, params] = f.mutation.mock.calls[0] as [string, Record<string, any>];
    expect(op).toBe("workflow_decide");
    expect(params).toMatchObject({ runId: "parent", expectedRevision: 7, requestId: "request-a", decision: "approve", autoContinue: true });
    expect(params.helpers).toEqual([expect.objectContaining({
      task: "补充并发测试并运行 pytest", provider: "deepseek-official", model: "deepseek-flash", effort: "max", workspace: false,
      executionWorkspace: { kind: "worktree", cwd: "/repo", access: "write", base: { kind: "working-tree" }, includeUntracked: [], writeScope: ["tests/concurrent.py"], integrator: "parent" },
    })]);
    expect(params).not.toHaveProperty("controlToken");
    expect(params).not.toHaveProperty("userOverride");
    expect(params).not.toHaveProperty("consoleAuthority");
  });

  it("freezes an uncertain command and replays its original identity and version", async () => {
    const f = fixture();
    f.mutation.mockRejectedValueOnce(new ApiError("NETWORK", "lost reply"));
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("决定理由"), "Host 已完成测试");
    await user.click(screen.getByRole("button", { name: "拒绝协助并记录理由" }));
    expect(screen.getByLabelText("决定理由").closest("fieldset")).toHaveProperty("disabled", true);
    await user.click(await screen.findByRole("button", { name: "重试同一操作" }));
    await waitFor(() => expect(f.mutation).toHaveBeenCalledTimes(2));
    expect(f.mutation.mock.calls[1]).toEqual(f.mutation.mock.calls[0]);
  });

  it("requires an explicit policy before manually continuing with active helpers", async () => {
    const f = fixture();
    f.workflow.children.push({ taskId: "child-a", state: "active", role: "helper", requestId: "request-a" });
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("交给下一回合的输入"), "Host 提供新的固定提交");
    expect(screen.getByRole("button", { name: "提交接续输入" })).toHaveProperty("disabled", true);
    await user.selectOptions(screen.getByLabelText("仍在执行的协助任务"), "cancel");
    await user.click(screen.getByRole("button", { name: "提交接续输入" }));
    expect(f.mutation).toHaveBeenCalledWith("workflow_continue", expect.objectContaining({ expectedRevision: 7, helperPolicy: "cancel", input: "Host 提供新的固定提交" }));
    await user.click(screen.getByRole("button", { name: "child-a" }));
    expect(f.props.selectTask).toHaveBeenCalledWith("child-a");
  });

  it("binds final acceptance to the delivered attempt artifact, not an earlier helper", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", activeRequest: null, finalAttemptId: "attempt-final" });
    f.workflow.artifacts = [
      { artifactId: "old", attemptId: "attempt-old", sourceTaskId: "helper", kind: "output", manifestSha256: "old-hash" },
      { artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "final-hash" },
    ];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("实际检查依据"), "检查 final diff 并运行并发测试通过");
    await user.click(screen.getByRole("button", { name: "接受最终交付" }));
    expect(f.mutation).toHaveBeenCalledWith("workflow_acknowledge", expect.objectContaining({ artifactId: "final", verdict: "accepted" }));
  });

  it("keeps final acceptance disabled while a descendant has no stop proof", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", activeRequest: null, finalAttemptId: "attempt-final" });
    f.workflow.shutdown = { selfConfirmed: true, descendantsConfirmed: false, unconfirmedRunIds: ["child"], unconfirmedCount: 1, truncated: false };
    f.workflow.artifacts = [{ artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "hash" }];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await screen.findByLabelText("实际检查依据");
    expect(screen.getByRole("button", { name: "接受最终交付" }).closest("fieldset")).toHaveProperty("disabled", true);
    await user.click(screen.getByRole("button", { name: "接受最终交付" }));
    expect(f.mutation).not.toHaveBeenCalled();
    expect(screen.getByText(/目标范围内共 1 项执行尚未核实/)).toBeTruthy();
  });
});
