import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type { Task, TaskPage, TaskQuery } from "./types";
import { mergeLiveTasks, useTaskHistory } from "./use-task-history";

const task = (runId: string, revision = 1): Task => ({ runId, revision, task: runId, status: "queued", owner: "host",
  cwd: "/allocated/checkout", createdAt: "2026-09-24T12:00:00Z", acceptedAt: null, acceptanceVerdict: null });
const page = (runs: Task[], nextCursor: string | null = null): TaskPage => ({ runs, total: 3, nextCursor });
const query: TaskQuery = { rootsOnly: true, query: "", projectId: "project-a", hostId: "host-a", filter: "all" };
function harness() {
  const requests: { query: TaskQuery; signal?: AbortSignal; resolve: (value: TaskPage) => void; reject: (error: Error) => void }[] = [];
  const tasks = vi.fn((params: TaskQuery, signal?: AbortSignal) => new Promise<TaskPage>((resolve, reject) => {
    requests.push({ query: params, signal, resolve, reject });
  }));
  const api = { tasks } as unknown as ConsoleApi;
  const hook = renderHook(({ query, active }) => useTaskHistory(api, query, active), { initialProps: { query, active: true } });
  return { ...hook, requests, tasks };
}
const startRead = () => act(async () => { await vi.advanceTimersByTimeAsync(500); });

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("delegation history", () => {
  it("preserves backend filters, guards concurrent pagination and deduplicates overlapping pages", async () => {
    const f = harness();
    await startRead();
    expect(f.requests[0].query).toMatchObject(query);
    expect(f.requests[0].signal).toBeInstanceOf(AbortSignal);
    await act(async () => f.requests[0].resolve(page([task("newer"), task("overlap")], "opaque-page-2")));
    act(() => { void f.result.current.more(); void f.result.current.more(); });
    expect(f.tasks).toHaveBeenCalledTimes(2);
    expect(f.requests[1].query).toMatchObject({ ...query, before: "opaque-page-2" });
    await act(async () => f.requests[1].resolve(page([task("overlap", 2), task("older"), task("older", 3)])));
    expect(f.result.current.runs.map(t => [t.runId, t.revision])).toEqual([["newer", 1], ["overlap", 2], ["older", 3]]);
    expect(f.result.current.total).toBe(3);
    expect(f.result.current.nextCursor).toBeNull();
    act(() => { void f.result.current.more(); });
    expect(f.tasks).toHaveBeenCalledTimes(2);
  });

  it("retries a failed next page without losing loaded history or advancing its cursor", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("existing")], "retry-this-cursor")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1].reject(new Error("history unavailable")));
    expect(f.result.current.error).toBe("history unavailable");
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["existing"]);
    expect(f.result.current.nextCursor).toBe("retry-this-cursor");
    act(() => { void f.result.current.retry(); });
    expect(f.requests[2].query).toEqual(f.requests[1].query);
    await act(async () => f.requests[2].resolve(page([task("older")])));
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["existing", "older"]);
    expect(f.result.current.error).toBe("");
  });

  it("retries an initial failure as a first-page read", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].reject(new Error("first read failed")));
    act(() => { void f.result.current.retry(); });
    expect(f.requests[1].query).not.toHaveProperty("before");
    await act(async () => f.requests[1].resolve(page([task("recovered")])));
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["recovered"]);
    expect(f.result.current.error).toBe("");
  });

  it("aborts a superseded filter and ignores its late reply without unlocking the new request", async () => {
    const f = harness();
    await startRead();
    const nextQuery: TaskQuery = { ...query, rootsOnly: false, projectId: "project-b", query: "changed", filter: "host" };
    f.rerender({ query: nextQuery, active: true });
    expect(f.requests[0].signal?.aborted).toBe(true);
    await startRead();
    expect(f.requests[1].query).toMatchObject(nextQuery);
    await act(async () => f.requests[0].resolve(page([task("stale")], "stale-cursor")));
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.loading).toBe(true);
    act(() => { void f.result.current.retry(); });
    expect(f.tasks).toHaveBeenCalledTimes(2);
    await act(async () => f.requests[1].resolve(page([task("current")])));
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["current"]);
    expect(f.result.current.error).toBe("");
  });

  it("keeps loaded history while hidden and aborts pending work on hide and unmount", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("kept")], "next")));
    act(() => { void f.result.current.more(); });
    f.rerender({ query, active: false });
    expect(f.requests[1].signal?.aborted).toBe(true);
    await act(async () => f.requests[1].resolve(page([task("late-hidden")])));
    f.rerender({ query, active: true });
    await startRead();
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["kept"]);
    expect(f.tasks).toHaveBeenCalledTimes(2);
    act(() => { void f.result.current.more(); });
    expect(f.requests[2].query.before).toBe("next");
    f.unmount();
    expect(f.requests[2].signal?.aborted).toBe(true);
  });

  it("refreshes loaded rows from live data without inserting or reordering new history", () => {
    const history = [task("first"), task("second")];
    expect(mergeLiveTasks(history, [task("newest"), task("second", 4)]).map(t => [t.runId, t.revision]))
      .toEqual([["first", 1], ["second", 4]]);
    expect(history[1].revision).toBe(1);
  });
});
