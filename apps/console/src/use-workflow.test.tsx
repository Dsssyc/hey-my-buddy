import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ConsoleApi } from "./api";
import { useWorkflow } from "./use-workflow";
import { createAuthorityLatch } from "./console-session";
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
    consoleSession: { id: "session-a", canWrite: true, reason: null },
  } as Snapshot;
  const failures: unknown[] = [];
  const calls: [string, Record<string, unknown>][] = [];
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    calls.push([operation, params]);
    if (operation === "workflow_get") return structuredClone(workflow);
    const failure = failures.shift();
    if (failure) throw failure;
    return {};
  });
  const api = { command } as unknown as ConsoleApi;
  const refresh = vi.fn(async () => snapshot);
  const mutations = () => calls.filter(([operation]) => operation === "workflow_continue");
  return { api, command, calls, failures, mutations, refresh, snapshot, task, workflow };
}

describe("workflow command authority", () => {
  it("checks a shared refusal before a sibling hook has rerendered", async () => {
    const f = fixture();
    const authority = createAuthorityLatch();
    const { result } = renderHook(() => useWorkflow(f.api, f.task, f.snapshot, f.refresh, true, true, authority));
    await waitFor(() => expect(result.current.value).not.toBeNull());
    const staleCommand = result.current.command;
    authority.lose("session-a");
    await act(async () => { await staleCommand("workflow_continue", { input: "x" }); });
    expect(f.mutations()).toHaveLength(0);
    expect(result.current.error).toContain("新窗口已取得写权限");
  });

  it("keeps an ambiguous command unknown when its retry is refused and never replays it", async () => {
    const f = fixture();
    f.failures.push(
      new ApiError("NETWORK", "lost reply"),
      new ApiError("CONSOLE_READ_ONLY", "board refused the older session"),
    );
    const { result } = renderHook(() => useWorkflow(f.api, f.task, f.snapshot, f.refresh));
    await waitFor(() => expect(result.current.value).not.toBeNull());
    // Captured before any refusal: a stale closure must not dispatch either.
    const staleCommand = result.current.command;

    await act(async () => { await staleCommand("workflow_continue", { input: "Host 补充" }); });
    expect(f.mutations()).toHaveLength(1);
    expect(result.current.uncertain).toBe(true);
    const first = f.mutations()[0][1];

    // The retry reuses the exact command identity, but the board refuses it
    // because a newer window now owns authority. That refusal proves only that
    // this request was denied: the earlier attempt may have committed.
    await act(async () => { await result.current.command(""); });
    expect(f.mutations()).toHaveLength(2);
    expect(f.mutations()[1][1]).toEqual(first);
    expect(result.current.uncertain).toBe(true);
    expect(result.current.error).toContain("可能已经生效");
    expect(result.current.sessionWritable).toBe(false);
    expect(result.current.writable).toBe(false);

    // The snapshot prop still says canWrite:true, but the refusal latched the
    // session: neither the live nor the stale closure dispatches again.
    await act(async () => { await staleCommand(""); });
    expect(f.mutations()).toHaveLength(2);
    expect(result.current.error).toContain("可能已经生效");

    // `workflow_get` polling remains available read-only.
    expect(f.calls.some(([operation]) => operation === "workflow_get")).toBe(true);
  });

  it("blocks every mutation when the polling snapshot reports a read-only session", async () => {
    const f = fixture();
    const { result, rerender } = renderHook(
      ({ snapshot }: { snapshot: Snapshot }) => useWorkflow(f.api, f.task, snapshot, f.refresh),
      { initialProps: { snapshot: f.snapshot } },
    );
    await waitFor(() => expect(result.current.value).not.toBeNull());
    rerender({ snapshot: { ...f.snapshot, consoleSession: { id: "session-a", canWrite: false, reason: "superseded" } } });
    await act(async () => { await result.current.command("workflow_continue", { input: "x" }); });
    expect(f.mutations()).toHaveLength(0);
    expect(result.current.sessionWritable).toBe(false);
    expect(result.current.error).toContain("新窗口已取得写权限");
  });

  it("blocks mutations when the authenticated poll failed while the snapshot is still writable", async () => {
    const f = fixture();
    const { result } = renderHook(
      ({ writesAvailable }: { writesAvailable: boolean }) =>
        useWorkflow(f.api, f.task, f.snapshot, f.refresh, true, writesAvailable),
      { initialProps: { writesAvailable: false } },
    );
    await waitFor(() => expect(result.current.value).not.toBeNull());
    await act(async () => { await result.current.command("workflow_continue", { input: "x" }); });
    expect(f.mutations()).toHaveLength(0);
    expect(result.current.sessionWritable).toBe(true);
    expect(result.current.writable).toBe(false);
    expect(result.current.error).toContain("连接已中断");
    // Reading the workflow record keeps working.
    expect(f.calls.some(([operation]) => operation === "workflow_get")).toBe(true);
  });
});
