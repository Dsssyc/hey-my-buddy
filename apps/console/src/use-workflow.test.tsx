import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ConsoleApi } from "./api";
import { useWorkflow } from "./use-workflow";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";

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
    currentTurn: null,
    activeRequest: { requestId: "request-a", kind: "assistance", state: "open", summary: "补充并发测试", attempted: "已跑基础测试", neededWork: ["并发测试"], expectedArtifacts: [], acceptance: "测试覆盖竞争" },
    children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task,
  };
  const snapshot = {
    csrfToken: "csrf",
    consoleSession: { id: "session-a", canWrite: false, reason: "superseded" },
  } as Snapshot;
  const calls: [string, Record<string, unknown>][] = [];
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    calls.push([operation, params]);
    if (operation === "workflow_get") return structuredClone(workflow);
    throw new Error(`Unexpected mutation: ${operation}`);
  });
  const api = { command } as unknown as ConsoleApi;
  return { api, command, calls, snapshot, task, workflow };
}

describe("read-only workflow read (0.15.1 U4)", () => {
  it("polls workflow_get only and never exposes a mutation entry point", async () => {
    const f = fixture();
    const { result } = renderHook(() => useWorkflow(f.api, f.task, f.snapshot, true));
    await waitFor(() => expect(result.current.value).not.toBeNull());
    expect(result.current.value!.runId).toBe("parent");
    expect(f.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
    expect(Object.keys(result.current).sort()).toEqual(["error", "reload", "value"]);
  });

  it("keeps polling through a read-only session and surfaces read errors honestly", async () => {
    const f = fixture();
    let failures = 1;
    f.command.mockImplementation(async (operation: string) => {
      if (operation !== "workflow_get") throw new Error(`Unexpected mutation: ${operation}`);
      if (failures > 0) { failures -= 1; throw new ApiError("NETWORK", "连接中断"); }
      return structuredClone(f.workflow);
    });
    const { result } = renderHook(() => useWorkflow(f.api, f.task, f.snapshot, true));
    await waitFor(() => expect(result.current.error).toContain("连接中断"));
    // The 3-second poll retries the read and clears the error.
    await waitFor(() => expect(result.current.value).not.toBeNull(), { timeout: 4500 });
    expect(result.current.error).toBe("");
  });

  it("re-reads on demand through reload without any write", async () => {
    const f = fixture();
    const { result } = renderHook(() => useWorkflow(f.api, f.task, f.snapshot, true));
    await waitFor(() => expect(result.current.value).not.toBeNull());
    const before = f.calls.length;
    await act(async () => { result.current.reload(); });
    await waitFor(() => expect(f.calls.length).toBeGreaterThan(before));
    expect(f.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
  });
});
