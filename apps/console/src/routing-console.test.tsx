import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { DecisionDetails } from "./DecisionDetails";
import { RoutingDetails } from "./RoutingDetails";
import { ApiError, type ConsoleApi } from "./api";
import type { Decision, Profile, Snapshot, Task } from "./types";
import type { RoutingHistory, Workflow } from "./workflow-types";

const worker: Profile = {
  profileId: "dsh:deepseek-official:deepseek-flash:max",
  label: "历史工作模型 · max", adapter: "dsh", provider: "deepseek-official",
  model: "deepseek-flash", effort: "max", available: true, enabled: true,
  capabilities: ["execution:dsh"], contextWindow: 1000000, source: "fixture", description: "",
};
const selector = { provider: "deepseek-official", model: "deepseek-flash", effort: "off" };

function snapshot(records: Task[] = []): Snapshot {
  return {
    csrfToken: "csrf", tableRevision: 99,
    gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
    configuration: { revision: 9, decisionProfileId: worker.profileId },
    profiles: [{ ...worker, label: "现在已改名的模型 · max" }],
    preferences: [{ profileId: worker.profileId, mode: "exclude", reason: "当前已改为排除" }],
    cards: [], annotations: [], evidence: [], decisions: [], sampleCounts: {},
    tasks: { runs: records, total: records.length },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
}
function decision(id: string, overrides: Partial<Decision> = {}) {
  return {
    decisionId: id, kind: "select" as const, status: "completed", task: `原始任务 ${id}`,
    profileId: worker.profileId, selectedProfile: { ...worker }, tableRevision: 3,
    configurationRevision: 2, reason: `${id} 的持久选择依据`, evidenceIds: ["evidence-at-selection"],
    createdAt: "2026-09-24T08:00:00Z", runId: `calculation-${id}`,
    decisionModel: { requested: { provider: selector.provider, model: selector.model, reasoningEffort: "off" }, resolved: null, observed: null },
    requested: { constraints: { adapter: "dsh" } },
    input: {
      operation: overrides.kind || "select", tableRevision: overrides.tableRevision ?? 3, profile: selector, profiles: [{ ...worker }],
      cards: [], preferences: [{ profileId: worker.profileId, mode: "prefer", reason: "当时偏好有边界的测试工作" }],
      evidence: [{ evidenceId: "evidence-at-selection", summary: "当时已验收的测试证据" }],
    },
    inputSha256: `hash-${id}`, output: { status: "completed" },
    ...overrides,
  };
}
function task(runId = "goal"): Task {
  return {
    runId, task: `完成 ${runId} 状态内核`, status: "running", owner: "host:source", cwd: "/repo",
    revision: 2, createdAt: "2026-09-24T08:00:00Z", acceptedAt: null, acceptanceVerdict: null,
    workflow: { state: "executing", awaitingHost: false, hostId: "source", ownerGeneration: 1, revision: 4 },
    delegation: { kind: "goal", sourceHostId: "source", currentHostId: "source", parentRunId: null, rootRunId: runId,
      project: { id: "repo", path: "/repo", label: "repo" }, configuration: { ...worker } },
  };
}
function workflow(record = task()): Workflow {
  return {
    governed: true, runId: record.runId, hostId: "source", ownerGeneration: 1, revision: 4,
    state: "executing", awaitingHost: false, waitReason: "", continuationCount: 0,
    executionConfiguration: { ...worker },
    routing: { status: "completed", decisionId: "decision-current", tableRevision: 3, configurationRevision: 2,
      reason: "已记录当前配置的选择依据" },
    workspace: { path: "/repo", kind: "worktree", access: "write", inputCommit: "input", manifestSha256: "manifest" },
    currentTurn: { turnId: "turn-current", turnIndex: 2, attemptId: "attempt-current", resumeMode: "native-session" },
    activeRequest: null, children: [], artifacts: [], finalArtifactId: null, finalAttemptId: null, task: record,
  };
}
function apiFor(state: Snapshot, command: ReturnType<typeof vi.fn>): ConsoleApi {
  return {
    snapshot: vi.fn(async () => structuredClone(state)), command,
    tasks: vi.fn(async () => ({ ...state.tasks, nextCursor: null })),
    task: vi.fn(async (runId: string) => state.tasks.runs.find(record => record.runId === runId)),
  } as unknown as ConsoleApi;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => { cleanup(); window.location.hash = ""; });

describe("routing configuration", () => {
  it("keeps a large decision history out of the settings page and performs no decision request", async () => {
    const state = snapshot();
    state.decisions = Array.from({ length: 50 }, (_, i) => decision(`history-${i}`, {
      task: "不应出现在配置页的长任务说明。".repeat(200),
    }));
    const command = vi.fn();
    window.location.hash = "#settings";
    render(<App suppliedApi={apiFor(state, command)} />);
    expect(await screen.findByLabelText("决策模型配置")).toHaveProperty("disabled", true);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.getByText(/用于在智能路由中比较候选执行配置/)).toBeTruthy();
    expect(screen.queryByText(/整理经验/)).toBeNull();
    expect(screen.queryByRole("heading", { name: "最近决策" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "试算一次推荐" })).toBeNull();
    expect(screen.queryByRole("button", { name: "请求整理" })).toBeNull();
    expect(screen.queryByText(/不应出现在配置页的长任务说明/)).toBeNull();
    expect(command).not.toHaveBeenCalled();
  });
});

describe("recorded decision details", () => {
  it("keeps other candidate preferences collapsed while showing the selected preference", async () => {
    const audit = decision("preference-scope");
    audit.input.preferences.push({ profileId: "another-profile", mode: "prefer", reason: "另一候选的偏好" });
    const command = vi.fn(async () => ({ decision: audit }));
    render(<DecisionDetails decisionId={audit.decisionId} api={apiFor(snapshot(), command)} csrfToken="csrf" />);
    const detail = await screen.findByRole("region", { name: "决策依据详情" });
    const selected = within(detail).getByText(/当时偏好有边界的测试工作/, { selector: "li" });
    expect(selected.closest("details")).toBeNull();
    const others = within(detail).getByText("其他候选偏好（1 条）").closest("details")!;
    expect(others.open).toBe(false);
    await userEvent.setup().click(within(others).getByText("其他候选偏好（1 条）"));
    expect(others.open).toBe(true);
    expect(within(others).getByText(/另一候选的偏好/)).toBeTruthy();
  });

  it("ignores a late response from the previously selected decision", async () => {
    const previous = deferred<{ decision: ReturnType<typeof decision> }>();
    const command = vi.fn(async (_operation: string, params: { decisionId: string }) =>
      params.decisionId === "previous" ? previous.promise : { decision: decision("current") });
    const api = apiFor(snapshot(), command);
    const { rerender } = render(<DecisionDetails decisionId="previous" api={api} csrfToken="csrf" />);
    await waitFor(() => expect(command).toHaveBeenCalledWith("selection_get", { decisionId: "previous", includeAudit: true }, "csrf"));
    rerender(<DecisionDetails decisionId="current" api={api} csrfToken="csrf" />);
    await screen.findByText("current 的持久选择依据");
    await act(async () => { previous.resolve({ decision: decision("previous") }); });
    expect(screen.getByText("current 的持久选择依据")).toBeTruthy();
    expect(screen.queryByText("previous 的持久选择依据")).toBeNull();
    expect(command.mock.calls.every(([operation]) => operation === "selection_get")).toBe(true);
  });

  it("reports a missing historical record without fabricating a reason or running a model", async () => {
    const command = vi.fn(async () => { throw new ApiError("NOT_FOUND", "Unknown decisionId"); });
    render(<DecisionDetails decisionId="missing" api={apiFor(snapshot(), command)} csrfToken="csrf" />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/当时偏好有边界的测试工作/)).toBeNull();
    expect(command).toHaveBeenCalledExactlyOnceWith("selection_get", { decisionId: "missing", includeAudit: true }, "csrf");
  });

  it("keeps a long recorded reason and the raw snapshots collapsed until requested", async () => {
    const reason = "这是保留在黑板中的具体选择依据。".repeat(60);
    const command = vi.fn(async () => ({ decision: decision("long", { reason }) }));
    const user = userEvent.setup();
    render(<DecisionDetails decisionId="long" api={apiFor(snapshot(), command)} csrfToken="csrf" />);
    const disclosure = (await screen.findByText("展开完整依据")).closest("details")!;
    expect(disclosure.open).toBe(false);
    expect(screen.getByText(reason.slice(0, 360) + "…")).toBeTruthy();
    expect(screen.getByText("记录标识与原始快照").closest("details")).toHaveProperty("open", false);
    await user.click(within(disclosure).getByText("展开完整依据"));
    expect(disclosure.open).toBe(true);
    expect(within(disclosure).getByText(reason)).toBeTruthy();
    expect(command).toHaveBeenCalledTimes(1);
  });
});

describe("delegation routing rationale", () => {
  it("opens the route from the executor header using frozen preferences and configuration", async () => {
    const record = task(), value = workflow(record), state = snapshot([record]);
    const audit = decision("decision-current");
    const command = vi.fn(async (operation: string, params: { decisionId?: string }) => {
      if (operation === "workflow_get") return value;
      if (operation === "selection_get" && params.decisionId === audit.decisionId) return { decision: audit };
      throw new Error(`Unexpected command: ${operation}`);
    });
    const user = userEvent.setup();
    render(<App suppliedApi={apiFor(state, command)} />);
    await user.click(await screen.findByRole("button", { name: /完成 goal 状态内核/ }));
    await screen.findByRole("tab", { name: "路由依据" });
    expect(command.mock.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
    await user.click(screen.getByRole("button", { name: "查看选择依据" }));
    expect(screen.getByRole("tab", { name: "路由依据" }).getAttribute("aria-selected")).toBe("true");
    const detail = await screen.findByRole("region", { name: "决策依据详情" });
    expect(within(detail).getByText("decision-current 的持久选择依据")).toBeTruthy();
    expect(within(detail).getByText(/当时偏好有边界的测试工作/, { selector: "li" })).toBeTruthy();
    expect(within(detail).getByText(/历史工作模型/, { selector: "strong" })).toBeTruthy();
    expect(within(detail).getByText("V3")).toBeTruthy();
    expect(within(detail).getByText("deepseek-official / deepseek-flash / 非思考")).toBeTruthy();
    expect(within(detail).queryByText(/现在已改名的模型|当前已改为排除/)).toBeNull();
    expect(command).toHaveBeenCalledWith("selection_get", { decisionId: "decision-current", includeAudit: true }, "csrf");
    expect(command.mock.calls.every(([operation]) => ["workflow_get", "selection_get"].includes(operation))).toBe(true);
  });

  it("identifies an explicit Host configuration without inventing an intelligent selection", () => {
    const value = workflow();
    value.routing = { status: "explicit", decisionId: null };
    const command = vi.fn();
    render(<RoutingDetails value={value} api={apiFor(snapshot(), command)} csrfToken="csrf" active />);
    expect(screen.getByText(/Host 指定.*未调用智能路由/)).toBeTruthy();
    expect(screen.queryByRole("region", { name: "决策依据详情" })).toBeNull();
    expect(command).not.toHaveBeenCalled();
  });

  it("shows native configuration rejection alongside the selector's completed rationale", async () => {
    const value = workflow();
    value.state = "awaiting-host";
    value.awaitingHost = true;
    value.routing = { ...value.routing!, status: "needs-host", reason: "CATALOG_UNAVAILABLE: 原生目录已移除所选配置" };
    const command = vi.fn(async () => ({ decision: decision("decision-current") }));
    render(<RoutingDetails value={value} api={apiFor(snapshot(), command)} csrfToken="csrf" active />);
    const execution = screen.getByRole("region", { name: "当前执行配置" });
    expect(within(execution).getByRole("status").textContent).toContain("CATALOG_UNAVAILABLE: 原生目录已移除所选配置");
    expect(within(execution).getByText("需要 Host 处理")).toBeTruthy();
    const rationale = await screen.findByRole("region", { name: "决策依据详情" });
    expect(within(rationale).getByText("decision-current 的持久选择依据")).toBeTruthy();
    expect(within(rationale).getByText("已完成")).toBeTruthy();
    expect(command).toHaveBeenCalledExactlyOnceWith("selection_get", { decisionId: "decision-current", includeAudit: true }, "csrf");
  });

  it("paginates the delegation's own routes and inspects the exact older decision", async () => {
    const value = workflow();
    const recent: RoutingHistory["entries"] = Array.from({ length: 20 }, (_, index) => ({
      decisionId: `route-${index}`, status: "completed", taskId: `calculation-${index}`, selectedProfile: { ...worker },
      tableRevision: 2, configurationRevision: 2, reason: `近期理由 ${index}`, ownerGeneration: 1,
      current: false, createdAt: "2026-09-24T08:00:00Z",
    }));
    const older = { ...recent[0], decisionId: "older-route", selectedProfile: { ...worker, model: "historical-model" } };
    const command = vi.fn(async (operation: string, params: { decisionId?: string; routingHistory?: { before?: number } }) => {
      if (operation === "selection_get") return { decision: decision(params.decisionId!) };
      if (operation === "workflow_get") return { ...value, routingHistory: {
        entries: params.routingHistory?.before === 31 ? [older] : recent,
        nextCursor: params.routingHistory?.before === 31 ? null : 31, total: 21,
      } };
      throw new Error(`Unexpected command: ${operation}`);
    });
    const user = userEvent.setup();
    render(<RoutingDetails value={value} api={apiFor(snapshot(), command)} csrfToken="csrf" active />);
    await screen.findByText("decision-current 的持久选择依据");
    expect(command.mock.calls.map(([operation]) => operation)).toEqual(["selection_get"]);
    const history = screen.getByText("此前的路由决定").closest("details")!;
    await user.click(within(history).getByText("此前的路由决定"));
    await waitFor(() => expect(within(history).getAllByRole("button", { name: /deepseek-flash/ })).toHaveLength(20));
    expect(command).toHaveBeenCalledWith("workflow_get", { runId: "goal", routingHistory: { limit: 20 } }, "csrf");
    await user.click(within(history).getByRole("button", { name: "加载更早决定" }));
    await user.click(await within(history).findByRole("button", { name: /historical-model/ }));
    await screen.findByText("older-route 的持久选择依据");
    expect(history.open).toBe(false);
    expect(document.activeElement).toBe(screen.getByRole("region", { name: "所选路由决定" }));
    expect(command).toHaveBeenCalledWith("workflow_get", { runId: "goal", routingHistory: { limit: 20, before: 31 } }, "csrf");
    expect(command).toHaveBeenCalledWith("selection_get", { decisionId: "older-route", includeAudit: true }, "csrf");
    expect(screen.queryByText("decision-current 的持久选择依据")).toBeNull();
    await user.click(screen.getByRole("button", { name: "返回当前配置" }));
    expect(await screen.findByText("decision-current 的持久选择依据")).toBeTruthy();
    expect(command.mock.calls.some(([operation]) => operation === "selection_list")).toBe(false);
  });

  it("uses recorded turn bindings and leaves an older unbound turn explicitly unknown", async () => {
    const value = workflow();
    value.turns = [
      { turnId: "old-unbound", turnIndex: 1, attemptId: "attempt-old", executionConfiguration: { ...worker } },
      { turnId: "bound", turnIndex: 2, attemptId: "attempt-bound", executionConfiguration: { ...worker },
        routing: { decisionId: "bound-decision", executionConfigurationRevision: 2 } },
    ];
    const command = vi.fn(async (_operation: string, params: { decisionId: string }) => ({ decision: decision(params.decisionId) }));
    const user = userEvent.setup();
    render(<RoutingDetails value={value} api={apiFor(snapshot(), command)} csrfToken="csrf" active />);
    await screen.findByText("decision-current 的持久选择依据");
    const turns = screen.getByText("执行回合与配置对应").closest("details")!;
    await user.click(within(turns).getByText("执行回合与配置对应"));
    const oldTurn = within(turns).getByText("第 1 回合").closest("li")!;
    expect(within(oldTurn).getByText("未记录此回合与路由的对应关系。")).toBeTruthy();
    expect(within(oldTurn).queryByRole("button")).toBeNull();
    await user.click(within(turns).getByRole("button", { name: "查看此回合的决定" }));
    await screen.findByText("bound-decision 的持久选择依据");
    expect(command).toHaveBeenCalledWith("selection_get", { decisionId: "bound-decision", includeAudit: true }, "csrf");
    expect(command.mock.calls.every(([operation]) => operation === "selection_get")).toBe(true);
  });

  it.each([
    { status: "queued", reason: "正在等待评价表发布", label: "等待中" },
    { status: "failed", reason: "路由服务不可用，没有选中执行配置", label: "执行失败" },
  ])("keeps a $status route inspectable before any executor is selected", async ({ status, reason, label }) => {
    const record = task();
    record.delegation!.configuration = null;
    const value = workflow(record);
    value.executionConfiguration = null;
    value.routing = { status, decisionId: "pending-or-failed", reason };
    const state = snapshot([record]);
    const command = vi.fn(async (operation: string) => {
      if (operation === "workflow_get") return value;
      if (operation === "selection_get") return { decision: { ...decision("pending-or-failed", { status, reason, profileId: null }), selectedProfile: null, input: null } };
      throw new Error(`Unexpected command: ${operation}`);
    });
    const user = userEvent.setup();
    render(<App suppliedApi={apiFor(state, command)} />);
    await user.click(await screen.findByRole("button", { name: /完成 goal 状态内核/ }));
    await user.click(screen.getByRole("button", { name: "查看选择依据" }));
    const detail = await screen.findByRole("region", { name: "决策依据详情" });
    expect(within(detail).getByText(reason)).toBeTruthy();
    expect(within(detail).getByText(label)).toBeTruthy();
    expect(within(detail).queryByText(/当时偏好有边界的测试工作/)).toBeNull();
    expect(command.mock.calls.every(([operation]) => ["workflow_get", "selection_get"].includes(operation))).toBe(true);
  });
});
