import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { TaskDetails } from "./TaskDetails";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";

afterEach(cleanup);

const microTask: Task = {
  runId: "run-micro-task", task: "列表行的第一行\n不应出现在标题的第二行", status: "completed", owner: "host-a", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
  lineage: { kind: "micro-task", sourceHostId: "host-a", currentHostId: "host-a", parentRunId: null, rootRunId: "run-micro-task", rootLeadRunId: null,
    project: { id: "repo", path: "/repo", label: "repo" }, buddy: null },
  workflow: { state: "delivered", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 2,
    title: "修复标题错位", resultSummary: "已验证的整合结果摘要" },
};
const noTitle: Task = {
  runId: "run-plain", task: "普通执行任务\n第二行", status: "queued", owner: "host-a", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
  workflow: { state: "delivered", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 2,
    resultSummary: "只有结果没有标题" },
};
const unnamed: Task = {
  runId: "run-unnamed", task: "\n\t ", status: "queued", owner: "host-a", cwd: "/repo",
  revision: 1, createdAt: "2026-09-26T00:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
};

function workflowValue(task: Task, overrides: Partial<Workflow> = {}): Workflow {
  return {
    runId: task.runId, hostId: "host-a", ownerGeneration: 1, revision: task.workflow?.revision ?? 1,
    state: task.workflow?.state ?? "delivered", awaitingHost: false, waitReason: "", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: task.cwd, kind: "existing", access: "write", inputCommit: "input", manifestSha256: "manifest" },
    currentTurn: null, activeRequest: null, children: [], artifacts: [], integrations: [],
    finalArtifactId: null, finalAttemptId: null, task,
    ...overrides,
  };
}

function fixture(workflow: Workflow) {
  const snapshot = { csrfToken: "fixture-csrf", eventCursor: 0, buddies: [] } as unknown as Snapshot;
  const command = vi.fn(async (operation: string) => operation === "workflow_get" ? structuredClone(workflow) : {});
  const api = { command, task: vi.fn(async () => ({ result: null })) } as unknown as ConsoleApi;
  const onTaskUpdate = vi.fn();
  const props = {
    snapshot, api, refresh: vi.fn(async () => snapshot), selectTask: vi.fn(), active: true,
    onTaskUpdate, navigationLocked: false,
  };
  return { props, command, onTaskUpdate };
}

describe("micro task title rule in components (0.16 T1)", () => {
  it("shows delegation authority beside the assigned buddy only when granted", () => {
    const f = fixture(workflowValue(microTask));
    const withAuthority = { ...microTask,
      lineage: { ...microTask.lineage!, delegationAuthority: true,
        buddy: { harness: "codex", provider: "openai", model: "gpt-6", effort: "high" } } };
    const view = render(<TaskDetails {...f.props} task={withAuthority} />);
    const assignment = view.container.querySelector(".assignment-line")!;
    expect(assignment.textContent).toContain("codex · openai · gpt-6 / high");
    expect(assignment.textContent).toContain("带委派权");
    view.rerender(<TaskDetails {...f.props} task={{ ...withAuthority, delegationAuthority: true,
      lineage: { ...withAuthority.lineage, delegationAuthority: false } }} />);
    expect(view.container.querySelector(".assignment-line")!.textContent).toContain("带委派权");
    view.rerender(<TaskDetails {...f.props} task={{ ...withAuthority, delegationAuthority: false }} />);
    expect(view.container.querySelector(".assignment-line")!.textContent).not.toContain("带委派权");
    expect(view.container.querySelector(".assignment-line")!.textContent).toContain("codex · gpt-6 / high");
  });

  it("uses the explicit title in the detail heading; the Worker summary never becomes the title", async () => {
    const f = fixture(workflowValue(microTask));
    const { container } = render(<TaskDetails {...f.props} task={microTask} />);
    const heading = await screen.findByRole("heading", { name: /修复标题错位/ });
    expect(heading.textContent).toContain("修复标题错位");
    // The Worker's own summary appears only as a labelled result line.
    expect(container.textContent).toContain("结果：已验证的整合结果摘要");
    expect(container.textContent).toContain("Worker 自述");
    expect(heading.textContent).not.toContain("已验证的整合结果摘要");
    // The raw task text stays available through the 原始任务 disclosure.
    expect(container.textContent).toContain("不应出现在标题的第二行");
  });

  it("falls back to the first task line (labelled) and names unnamed microTasks", () => {
    const f = fixture(workflowValue(noTitle));
    f.props.api.task = vi.fn(async () => ({ result: null }));
    const first = render(<TaskDetails {...f.props} task={noTitle} />);
    expect(first.getByRole("heading", { name: /普通执行任务/ })).toBeTruthy();
    expect(first.container.querySelector("h2")!.textContent).toContain("普通执行任务");
    expect(first.container.textContent).toContain("任务首行");
    expect(first.container.querySelector("h2")!.textContent).not.toContain("只有结果没有标题");
    first.unmount();
    const second = render(<TaskDetails {...f.props} task={unnamed} />);
    expect(second.getByRole("heading", { name: /未命名微任务/ })).toBeTruthy();
    second.unmount();
  });

  it("keeps the explicit title across a bounded workflow refresh; only the result changes", async () => {
    const stale: Task = { ...microTask, workflow: { state: "executing", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 1, title: "修复标题错位" } };
    const refreshed = workflowValue(stale, { revision: 7, state: "delivered" });
    refreshed.task = { runId: stale.runId, revision: 7, status: "completed", lineage: stale.lineage,
      workflow: { state: "delivered", awaitingHost: false, hostId: "host-a", ownerGeneration: 1, revision: 7,
        title: "修复标题错位", resultSummary: "历史记录刷新后的最新结论" } };
    const f = fixture(refreshed);
    const view = render(<TaskDetails {...f.props} task={stale} />);
    await waitFor(() => expect(f.onTaskUpdate).toHaveBeenCalled());
    const merged = f.onTaskUpdate.mock.calls.at(-1)![0] as Task;
    expect(merged.workflow?.resultSummary).toBe("历史记录刷新后的最新结论");
    view.rerender(<TaskDetails {...f.props} task={merged} />);
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading.textContent).toContain("修复标题错位");
    expect(heading.textContent).not.toContain("历史记录刷新后的最新结论");
  });

});
