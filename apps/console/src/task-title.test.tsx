import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { TaskDetails } from "./TaskDetails";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";

afterEach(cleanup);

const governed: Task = {
  runId: "run-governed", task: "列表行的第一行\n不应出现在标题的第二行", status: "completed", owner: "host-a", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null,
  workflow: { state: "delivered", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 2,
    resultSummary: "已验证的整合结果摘要" },
};
const ungoverned: Task = {
  runId: "run-plain", task: "普通执行任务\n第二行", status: "queued", owner: "host-a", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null,
};
const unnamed: Task = {
  runId: "run-unnamed", task: "\n\t ", status: "queued", owner: "host-a", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null,
};

function workflowValue(task: Task, overrides: Partial<Workflow> = {}): Workflow {
  return {
    governed: true, runId: task.runId, hostId: "host-a", ownerGeneration: 1, revision: task.workflow?.revision ?? 1,
    state: task.workflow?.state ?? "delivered", awaitingHost: false, waitReason: "", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: task.cwd, kind: "existing", access: "write", inputCommit: "input", manifestSha256: "manifest" },
    currentTurn: null, activeRequest: null, children: [], artifacts: [], integrations: [],
    finalArtifactId: null, finalAttemptId: null, task,
    ...overrides,
  };
}

function fixture(workflow: Workflow) {
  const snapshot = { csrfToken: "fixture-csrf", profiles: [] } as unknown as Snapshot;
  const command = vi.fn(async (operation: string) => operation === "workflow_get" ? structuredClone(workflow) : {});
  const api = { command, task: vi.fn(async () => ({ result: null })) } as unknown as ConsoleApi;
  const onTaskUpdate = vi.fn();
  const props = {
    snapshot, api, refresh: vi.fn(async () => snapshot), selectTask: vi.fn(), active: true,
    onLockChange: vi.fn(), onTaskUpdate, navigationLocked: false,
  };
  return { props, command, onTaskUpdate };
}

describe("delegation title fallback in components", () => {
  it("renders the result summary in the detail heading while retaining the raw task", async () => {
    const f = fixture(workflowValue(governed));
    const { container } = render(<TaskDetails {...f.props} task={governed} />);
    const heading = await screen.findByRole("heading", { name: "已验证的整合结果摘要" });
    expect(heading.textContent).toBe("已验证的整合结果摘要");
    // The raw task text stays available through the heading tooltip and details.
    expect(screen.getByRole("heading", { level: 2 }).getAttribute("title")).toBe(governed.task);
    expect(container.textContent).toContain("不应出现在标题的第二行");
  });

  it("falls back to the first task line and names unnamed delegations", () => {
    const f = fixture(workflowValue(ungoverned));
    f.props.api.task = vi.fn(async () => ({ result: null }));
    const first = render(<TaskDetails {...f.props} task={ungoverned} />);
    expect(first.getByRole("heading", { name: "普通执行任务" })).toBeTruthy();
    expect(first.container.querySelector("h2")!.textContent).toBe("普通执行任务");
    first.unmount();
    const second = render(<TaskDetails {...f.props} task={unnamed} />);
    expect(second.getByRole("heading", { name: "未命名委派" })).toBeTruthy();
    second.unmount();
  });

  it("adopts the newest concluded result after a bounded workflow refresh of an older record", async () => {
    const stale: Task = { ...governed, workflow: { state: "executing", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 1 } };
    const refreshed = workflowValue(stale, { revision: 7, state: "delivered" });
    refreshed.task = { runId: stale.runId, revision: 7, status: "completed",
      workflow: { state: "delivered", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 7,
        resultSummary: "历史记录刷新后的最新结论" } };
    const f = fixture(refreshed);
    const view = render(<TaskDetails {...f.props} task={stale} />);
    await waitFor(() => expect(f.onTaskUpdate).toHaveBeenCalled());
    const merged = f.onTaskUpdate.mock.calls.at(-1)![0] as Task;
    expect(merged.workflow?.resultSummary).toBe("历史记录刷新后的最新结论");
    view.rerender(<TaskDetails {...f.props} task={merged} />);
    const heading = screen.getByRole("heading", { name: "历史记录刷新后的最新结论" });
    expect(heading.textContent).toBe("历史记录刷新后的最新结论");
  });
});
