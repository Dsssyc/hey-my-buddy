import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowPanel } from "./WorkflowPanel";
import type { IntegrationRecord, Workflow } from "./workflow-types";
import type { ConsoleApi } from "./api";
import { ApiError } from "./api";
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
    notRequired: false, reason: "", actor: "host", createdAt: "2026-09-25",
    ...overrides,
  };
}

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
    children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task,
  };
  const snapshot = {
    csrfToken: "fixture-csrf",
    profiles: [{ profileId: "flash-max", label: "Flash 工作", enabled: true, available: true, adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max", capabilities: ["execution:dsh", "effort:max"] }],
  } as Snapshot;
  const mutation = vi.fn(async (_op: string, _params: unknown) => ({}));
  const command = vi.fn(async (op: string, params: unknown) => op === "workflow_get" ? structuredClone(workflow) : mutation(op, params));
  const api = { command } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => snapshot);
  const props = { task, snapshot, api, refresh, selectTask: vi.fn() };
  return { props, workflow, mutation, command };
}

describe("governed workflow console", () => {
  it("shows the recorded turn count without deriving a model-call count", async () => {
    const f = fixture();
    Object.assign(f.workflow, { counts: { turns: 3, openRequests: 1 }, continuationCount: 3 });
    render(<WorkflowPanel {...f.props} />);
    expect(await screen.findByText("共 3 个回合")).toBeTruthy();
    expect(screen.getByText(/接续次数来自持久轮次记录，不是模型调用次数/)).toBeTruthy();
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
    expect(view.textContent).toContain("工具调用 2");
    expect(view.textContent).toContain("模型回合未记录");
    expect(view.textContent).toContain("执行时限到期");
    expect(view.textContent).not.toMatch(/\d+\s*%/);
    expect(f.mutation).not.toHaveBeenCalled();
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
              adapter: "zcode", sessionId: "sess-z", captured: true, storageScope: "task-private",
              storageOwner: "buddy-attempt", nativeAppVisibility: "not-listed-in-native-app",
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
    expect(f.mutation).not.toHaveBeenCalled();
  });

  it("leaves helper model fields absent for automatic routing", async () => {
    const f = fixture();
    f.props.snapshot.profiles = [];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("决定理由"), "按能力选择协助配置");
    await user.click(screen.getByRole("button", { name: "添加协助任务" }));
    await user.type(screen.getByLabelText("工作内容与验收条件"), "实现有边界的测试");
    await user.type(screen.getByLabelText("允许写入的相对路径（每行一项）"), "tests/state.py");
    await user.click(screen.getByRole("button", { name: "批准所列协助" }));
    const params = f.mutation.mock.calls[0][1] as { helpers: Record<string, unknown>[] };
    for (const key of ["adapter", "provider", "model", "effort"]) expect(params.helpers[0]).not.toHaveProperty(key);
    expect(params.helpers[0]).toHaveProperty("executionWorkspace.kind", "worktree");
  });

  it("dispatches the selected ZCode helper without substituting a DSH identity", async () => {
    const f = fixture();
    Object.assign(f.props.snapshot.profiles[0], { adapter: "zcode", provider: "bigmodel-api", model: "GLM-5.3", effort: "high", capabilities: ["execution:zcode", "effort:high"] });
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("决定理由"), "使用已验证的 ZCode 配置");
    await user.click(screen.getByRole("button", { name: "添加协助任务" }));
    await user.selectOptions(screen.getByLabelText("执行配置"), "flash-max");
    await user.type(screen.getByLabelText("工作内容与验收条件"), "补充测试");
    await user.type(screen.getByLabelText("允许写入的相对路径（每行一项）"), "tests/state.py");
    await user.click(screen.getByRole("button", { name: "批准所列协助" }));
    expect(f.mutation).toHaveBeenCalledWith("workflow_decide", expect.objectContaining({
      helpers: [expect.objectContaining({ adapter: "zcode", provider: "bigmodel-api", model: "GLM-5.3", effort: "high" })],
    }));
  });

  it("admits a Codex helper from its declared execution capability, not a harness allowlist", async () => {
    const f = fixture();
    Object.assign(f.props.snapshot.profiles[0], {
      profileId: "codex-max", adapter: "codex", provider: "openai", model: "gpt-5", effort: "high",
      capabilities: ["execution:codex", "effort:high"],
    });
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("决定理由"), "使用已声明的 Codex 执行能力");
    await user.click(screen.getByRole("button", { name: "添加协助任务" }));
    await user.selectOptions(screen.getByLabelText("执行配置"), "codex-max");
    await user.type(screen.getByLabelText("工作内容与验收条件"), "运行 Codex 适配检查");
    await user.type(screen.getByLabelText("允许写入的相对路径（每行一项）"), "apps/console/src/x.ts");
    await user.click(screen.getByRole("button", { name: "批准所列协助" }));
    expect(f.mutation).toHaveBeenCalledWith("workflow_decide", expect.objectContaining({
      helpers: [expect.objectContaining({ adapter: "codex", provider: "openai", model: "gpt-5", effort: "high" })],
    }));
  });

  it("does not offer a profile whose capabilities lack its own adapter's execution token", async () => {
    const f = fixture();
    Object.assign(f.props.snapshot.profiles[0], { capabilities: ["decision"] });
    f.props.snapshot.profiles.push({
      profileId: "zcode-mismatch", label: "Mismatch", enabled: true, available: true, adapter: "zcode",
      provider: "bigmodel-api", model: "GLM-5.3", effort: "high", capabilities: ["execution:dsh"],
    } as (typeof f.props.snapshot.profiles)[number]);
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("决定理由"), "只保留自动路由");
    await user.click(screen.getByRole("button", { name: "添加协助任务" }));
    const select = screen.getByLabelText("执行配置") as HTMLSelectElement;
    expect([...select.options].map(option => option.textContent)).toEqual(["自动路由：由固定决策 Buddy 选择"]);
    expect(screen.getByText(/未指定配置时将请求自动路由/)).toBeTruthy();
  });

  it("recovers a helper routing boundary through the root owner without minting helper control", async () => {
    const f = fixture();
    f.workflow.activeRequest = { ...f.workflow.activeRequest!, kind: "attention", routing: true, childTaskId: "child-a", origin: { runId: "child-a", requestId: "route-a" } };
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.selectOptions(await screen.findByLabelText("填入已启用配置"), "flash-max");
    await user.click(screen.getByRole("button", { name: "使用此配置接续" }));
    expect(f.mutation).toHaveBeenCalledWith("workflow_continue", expect.objectContaining({
      runId: "parent", targetRunId: "child-a", expectedRevision: 7,
      configuration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max" },
    }));
    expect(screen.queryByRole("button", { name: "批准所列协助" })).toBeNull();
    expect(f.mutation.mock.calls[0][1]).not.toHaveProperty("controlToken");
  });

  it("retries routing on the same goal and freezes an uncertain response", async () => {
    const f = fixture();
    f.workflow.routing = { status: "needs-host", reason: "Configure the fixed selector" };
    f.workflow.activeRequest = { ...f.workflow.activeRequest!, kind: "routing" };
    f.mutation.mockRejectedValueOnce(new ApiError("NETWORK", "lost reply"));
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.click(await screen.findByRole("button", { name: "重试同一目标的路由" }));
    expect(screen.getByLabelText("Harness").closest("fieldset")).toHaveProperty("disabled", true);
    await user.click(await screen.findByRole("button", { name: "重试同一操作" }));
    await waitFor(() => expect(f.mutation).toHaveBeenCalledTimes(2));
    expect(f.mutation.mock.calls[0]).toEqual(f.mutation.mock.calls[1]);
    expect(f.mutation.mock.calls[0][1]).toMatchObject({ runId: "parent", reroute: true });
  });

  it("shows the actionable workspace conflict without a full audit fetch", async () => {
    const f = fixture();
    f.workflow.activeRequest = { ...f.workflow.activeRequest!, kind: "attention", preparationError: { code: "WORKSPACE_BASE_MISMATCH", message: "Checkout differs from the sealed output" } };
    render(<WorkflowPanel {...f.props} />);
    expect((await screen.findByRole("alert")).textContent).toContain("WORKSPACE_BASE_MISMATCH");
    expect(screen.getByRole("alert").textContent).toContain("Checkout differs from the sealed output");
    expect(f.mutation).not.toHaveBeenCalled();
  });

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

  it("binds final acceptance to the recorded final artifact, not an earlier helper", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final" });
    f.workflow.artifacts = [
      { artifactId: "old", attemptId: "attempt-old", sourceTaskId: "helper", kind: "output", manifestSha256: "old-hash" },
      { artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "final-hash" },
    ];
    f.workflow.integrations = [verifiedIntegration("final")];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("实际检查依据"), "检查 final diff 并运行并发测试通过");
    await user.click(screen.getByRole("button", { name: "接受最终交付" }));
    expect(f.mutation).toHaveBeenCalledWith("workflow_acknowledge", expect.objectContaining({
      artifactId: "final", integrationId: "int-final", verdict: "accepted",
    }));
  });

  it("accepts a resolved-output delivery through its own verified integration", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final-resolved" });
    f.workflow.artifacts = [
      { artifactId: "plain-output", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "plain-hash" },
      { artifactId: "final-resolved", attemptId: "attempt-final", sourceTaskId: "parent", kind: "resolved-output", manifestSha256: "resolved-hash" },
    ];
    f.workflow.integrations = [verifiedIntegration("final-resolved")];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    // The Host-resolution delivery is displayed with its target evidence.
    expect(await screen.findByText(/cherry-pick/)).toBeTruthy();
    expect(screen.getByText(/目标仓库集成测试通过/)).toBeTruthy();
    expect(screen.getByText(/before-commit/)).toBeTruthy();
    await user.type(screen.getByLabelText("实际检查依据"), "检查解析输出并运行集成测试");
    const accept = screen.getByRole("button", { name: "接受最终交付" });
    expect(accept).toHaveProperty("disabled", false);
    await user.click(accept);
    expect(f.mutation).toHaveBeenCalledWith("workflow_acknowledge", expect.objectContaining({
      artifactId: "final-resolved", integrationId: "int-final-resolved", verdict: "accepted",
    }));
  });

  it("keeps acceptance disabled until the final artifact has its own integration record", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final" });
    f.workflow.artifacts = [
      { artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "final-hash" },
      { artifactId: "other", attemptId: "attempt-other", sourceTaskId: "helper", kind: "output", manifestSha256: "other-hash" },
    ];
    // A record for another artifact never satisfies the final one.
    f.workflow.integrations = [verifiedIntegration("other")];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await user.type(await screen.findByLabelText("实际检查依据"), "Host 尚未完成整合");
    const accept = screen.getByRole("button", { name: "接受最终交付" });
    expect(accept).toHaveProperty("disabled", true);
    // The Host is pointed at the real recorded operation; nothing is auto-created.
    expect(screen.getByText(/workflow_integration_record/)).toBeTruthy();
    expect(f.mutation).not.toHaveBeenCalled();
    // Rejection stays a reviewed outcome that needs no integration record.
    await user.click(screen.getByRole("button", { name: "记录验收问题" }));
    expect(f.mutation).toHaveBeenCalledTimes(1);
    expect(f.mutation).toHaveBeenCalledWith("workflow_acknowledge", expect.objectContaining({ artifactId: "final", verdict: "rejected" }));
    expect(f.mutation.mock.calls[0][1]).not.toHaveProperty("integrationId");
  });

  it("accepts an explicit not-required integration record with its reason", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final" });
    f.workflow.artifacts = [{ artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "final-hash" }];
    // An older verified record stays on file; the newest not-required decision
    // is what both the artifact label and the acceptance binding must use.
    f.workflow.integrations = [verifiedIntegration("final", {
      integrationId: "int-not-required", state: "not-required", notRequired: true,
      strategy: "not-required", target: null, reason: "纯只读核查，无需整合",
    }), verifiedIntegration("final", { integrationId: "int-older-verified" })];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    expect(await screen.findByText(/纯只读核查，无需整合/)).toBeTruthy();
    expect(screen.getByText("整合：Host 记录无需整合")).toBeTruthy();
    await user.type(screen.getByLabelText("实际检查依据"), "只读核查通过");
    const accept = screen.getByRole("button", { name: "接受最终交付" });
    expect(accept).toHaveProperty("disabled", false);
    await user.click(accept);
    expect(f.mutation).toHaveBeenCalledWith("workflow_acknowledge", expect.objectContaining({
      integrationId: "int-not-required", verdict: "accepted",
    }));
  });

  it("keeps final acceptance disabled while a descendant has no stop proof", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final" });
    f.workflow.shutdown = { selfConfirmed: true, descendantsConfirmed: false, unconfirmedRunIds: ["child"], unconfirmedCount: 1, truncated: false };
    f.workflow.artifacts = [{ artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "hash" }];
    f.workflow.integrations = [verifiedIntegration("final")];
    const user = userEvent.setup();
    render(<WorkflowPanel {...f.props} />);
    await screen.findByLabelText("实际检查依据");
    expect(screen.getByRole("button", { name: "接受最终交付" }).closest("fieldset")).toHaveProperty("disabled", true);
    await user.click(screen.getByRole("button", { name: "接受最终交付" }));
    expect(f.mutation).not.toHaveBeenCalled();
    await user.click(screen.getByRole("tab", { name: "概览" }));
    expect(screen.getByText(/目标范围内共 1 项执行尚未核实/)).toBeTruthy();
  });

  it("keeps final acceptance disabled for a queued helper even before a new process starts", async () => {
    const f = fixture();
    Object.assign(f.workflow, { state: "delivered", awaitingHost: false, activeRequest: null, finalAttemptId: "attempt-final", finalArtifactId: "final" });
    f.workflow.children.push({ taskId: "resumed-child", state: "active", role: "helper", requestId: "request-a" });
    f.workflow.artifacts = [{ artifactId: "final", attemptId: "attempt-final", sourceTaskId: "parent", kind: "output", manifestSha256: "hash" }];
    f.workflow.integrations = [verifiedIntegration("final")];
    render(<WorkflowPanel {...f.props} />);
    await screen.findByLabelText("实际检查依据");
    expect(screen.getByRole("button", { name: "接受最终交付" }).closest("fieldset")).toHaveProperty("disabled", true);
  });
});
