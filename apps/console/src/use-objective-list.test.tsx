import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type { ObjectivePage, ObjectiveQuery, ObjectiveSummary } from "./objective-types";
import { useObjectiveList } from "./use-objective-list";

const summary = (objectiveId: string, lastActivitySeq: number, overrides: Partial<ObjectiveSummary> = {}): ObjectiveSummary => ({
  objectiveId, kind: "objective", title: objectiveId, titleSource: "objective",
  project: { id: "p1", label: "项目一", path: "/p1" }, sourceHostId: "host-a", currentHostIds: [],
  createdAt: "2026-09-26T01:00:00Z", lastActivityAt: "2026-09-26T01:00:00Z", lastActivitySeq,
  state: "active", counts: { roots: 1, helpers: 0, active: 1, host: 0, review: 0, ended: 0 },
  matchingRuns: 1, rootRunIds: [objectiveId], ...overrides,
});
const page = (objectives: ObjectiveSummary[], nextCursor: string | null = null, changed = false): ObjectivePage =>
  ({ objectives, total: objectives.length, nextCursor, cursor: 10, changed });
const query: ObjectiveQuery = { query: "", projectId: "p1", hostId: "", filter: "all" };

function harness() {
  const requests: { query: ObjectiveQuery; signal?: AbortSignal; resolve: (value: ObjectivePage) => void; reject: (error: Error) => void }[] = [];
  const objectives = vi.fn((params: ObjectiveQuery, signal?: AbortSignal) => new Promise<ObjectivePage>((resolve, reject) => {
    requests.push({ query: params, signal, resolve, reject });
  }));
  const api = { objectives } as unknown as ConsoleApi;
  const hook = renderHook(({ query, active }) => useObjectiveList(api, query, active), { initialProps: { query, active: true } });
  return { ...hook, requests, objectives };
}
const startRead = () => act(async () => { await vi.advanceTimersByTimeAsync(500); });

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("work-objective list hook", () => {
  it("keeps committed order and defers newcomers even when a poll returns the complete scope", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("first", 3), summary("second", 2)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("new", 10), summary("second", 9), summary("first", 3)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["first", "second"]);
    expect(f.result.current.total).toBe(3);
    expect(f.result.current.reorder).toEqual({ count: 2 });
  });

  it("removes proven nonmatching rows without reordering the surviving filtered rows", async () => {
    const f = harness();
    f.rerender({ query: { ...query, filter: "active" }, active: true });
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("gone", 5), summary("kept", 4), summary("busy", 3)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("busy", 9), summary("kept", 4)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["kept", "busy"]);
    expect(f.result.current.total).toBe(2);
    expect(f.result.current.reorder).toEqual({ count: 1 });
  });

  it("sends the filters and resets to a fresh first page when they change", async () => {
    const f = harness();
    await startRead();
    expect(f.requests[0]!.query).toMatchObject(query);
    expect(f.requests[0]!.query).not.toHaveProperty("before");
    await act(async () => f.requests[0]!.resolve(page([summary("a", 3), summary("b", 2)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["a", "b"]);
    const nextQuery: ObjectiveQuery = { ...query, filter: "host", projectId: "" };
    f.rerender({ query: nextQuery, active: true });
    expect(f.requests[0]!.signal?.aborted).toBe(true);
    await startRead();
    expect(f.requests[1]!.query).toMatchObject(nextQuery);
    await act(async () => f.requests[1]!.resolve(page([summary("c", 9)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["c"]);
  });

  it("pages older groups through the opaque cursor and deduplicates them", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("newer", 5)], "opaque-2")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1]!.resolve(page([summary("newer", 6), summary("older", 1)])));
    expect(f.requests[1]!.query).toMatchObject({ before: "opaque-2" });
    expect(f.result.current.rows.map(row => [row.objectiveId, row.lastActivitySeq])).toEqual([["newer", 6], ["older", 1]]);
    expect(f.result.current.nextCursor).toBeNull();
    act(() => { void f.result.current.more(); });
    expect(f.objectives).toHaveBeenCalledTimes(2);
  });

  it("updates counts, times and totals in place on the poll cadence without reordering", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("first", 3), summary("second", 2)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.requests[1]!.query).not.toHaveProperty("before");
    // A complete page (no cursor) returning both rows proves the whole scope.
    await act(async () => f.requests[1]!.resolve(page([summary("first", 3), summary("second", 2, { state: "ended" })])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["first", "second"]);
    expect(f.result.current.rows[1]!.state).toBe("ended");
    expect(f.result.current.total).toBe(2);
    expect(f.result.current.reorder).toBeNull();
  });

  it("updates the reported total even when an incomplete poll defers its order", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("first", 3), summary("second", 2)], "more-exists")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(({ ...page([summary("second", 2, { state: "ended" })], "more-exists"), total: 7 })));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["first", "second"]);
    expect(f.result.current.rows[1]!.state).toBe("ended");
    expect(f.result.current.total).toBe(7);
  });

  it("defers a real reorder behind the notice instead of moving a reader's rows", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("quiet", 3), summary("busy", 2)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The page is incomplete, so the unseen group defers behind the notice.
    await act(async () => f.requests[1]!.resolve(page([summary("busy", 7), summary("fresh", 9)], "more-exists")));
    // Order and membership stay exactly as committed until the notice is applied.
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["quiet", "busy"]);
    expect(f.result.current.reorder).toEqual({ count: 2 });
    act(() => { f.result.current.applyReorder(); });
    expect(f.result.current.reorder).toBeNull();
    // Applying rebuilds from a fresh first-page read rather than the old pages.
    await startRead();
    expect(f.requests[2]!.query).not.toHaveProperty("before");
    await act(async () => f.requests[2]!.resolve(page([summary("fresh", 9), summary("busy", 7), summary("quiet", 3)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["fresh", "busy", "quiet"]);
  });

  it("keeps a failed poll's rows and retries as an in-place refresh", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("kept", 1)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.reject(new Error("黑板暂时没有响应")));
    expect(f.result.current.error).toBe("黑板暂时没有响应");
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["kept"]);
    act(() => { void f.result.current.retry(); });
    expect(f.requests[2]!.query).not.toHaveProperty("before");
    await act(async () => f.requests[2]!.resolve(page([summary("kept", 2)])));
    expect(f.result.current.error).toBe("");
    expect(f.result.current.rows[0]!.lastActivitySeq).toBe(2);
  });

  it("adopts the first read after an empty poll, including its cursor", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The empty display adopts the read directly, including its cursor.
    await act(async () => f.requests[1]!.resolve(page([summary("fresh-a", 9), summary("fresh-b", 8)], "adopted-cursor")));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["fresh-a", "fresh-b"]);
    expect(f.result.current.nextCursor).toBe("adopted-cursor");
    expect(f.result.current.reorder).toBeNull();
    // A later incomplete poll with an unseen group defers behind the notice.
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[2]!.resolve(page([summary("fresh-b", 8), summary("unseen", 10)], "more-exists")));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["fresh-a", "fresh-b"]);
    expect(f.result.current.reorder).toEqual({ count: 1 });
  });

  it("removes rows a complete filtered page proves no longer match, keeping incomplete reads honest", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("gone", 6), summary("kept", 5)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // Incomplete page: "gone" sorts above the returned floor, so its absence is
    // ambiguous — it stays listed behind an explicit notice.
    await act(async () => f.requests[1]!.resolve(page([summary("kept", 5)], "more-exists")));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["gone", "kept"]);
    expect(f.result.current.reorder).toEqual({ count: null });
    act(() => { f.result.current.applyReorder(); });
    await startRead();
    // A complete page that omits "gone" proves it no longer matches the filter.
    await act(async () => f.requests[2]!.resolve(page([summary("kept", 5)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["kept"]);
    expect(f.result.current.reorder).toBeNull();
  });

  it("keeps the notice when the service reports changed without a local delta", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("still", 3)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("still", 3)], "more-exists", true)));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["still"]);
    expect(f.result.current.reorder).toEqual({ count: null });
    act(() => { f.result.current.applyReorder(); });
    expect(f.result.current.reorder).toBeNull();
    // A paged read that reports changed also keeps offering the refresh.
    await startRead();
    await act(async () => f.requests[2]!.resolve(page([summary("still", 3), summary("older", 1)], "page-2")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[3]!.resolve(page([summary("oldest", 0)], null, true)));
    expect(f.result.current.reorder).toEqual({ count: null });
  });

  it("does not discard a pending reorder notice by loading an unchanged older page", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("quiet", 3), summary("busy", 2)], "page-2")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("busy", 7)], "more-exists")));
    expect(f.result.current.reorder).toEqual({ count: 1 });
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[2]!.resolve(page([summary("older", 1)], null, false)));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["quiet", "busy", "older"]);
    expect(f.result.current.reorder).toEqual({ count: 1 });
  });

  it("breaks activity ties by objective identifier descending like the service", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("aaa", 5), summary("zzz", 5)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["zzz", "aaa"]);
  });

  it("keeps poll-freshened rows when a later page load rebuilds the map", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("top", 4)], "cursor-2")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("top", 9, { state: "ended" })], "cursor-2")));
    expect(f.result.current.rows[0]!.state).toBe("ended");
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[2]!.resolve(page([summary("older", 1)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["top", "older"]);
    expect(f.result.current.rows[0]!.lastActivitySeq).toBe(9);
    expect(f.result.current.rows[0]!.state).toBe("ended");
  });

  it("rebuilds paging from a fresh first-page read after applying the reorder notice", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("quiet", 3), summary("busy", 2)], "stale-cursor")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("busy", 7)], "more-exists")));
    expect(f.result.current.reorder).toEqual({ count: 1 });
    act(() => { f.result.current.applyReorder(); });
    // The stale keyset cursor is discarded by a fresh first-page read.
    await startRead();
    expect(f.requests[2]!.query).not.toHaveProperty("before");
    await act(async () => f.requests[2]!.resolve(page([summary("busy", 7), summary("quiet", 3)])));
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["busy", "quiet"]);
  });

  it("stops polling while hidden and resumes without dropping loaded rows", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("kept", 1)])));
    f.rerender({ query, active: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.objectives).toHaveBeenCalledTimes(1);
    f.rerender({ query, active: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.result.current.rows.map(row => row.objectiveId)).toEqual(["kept"]);
    expect(f.requests[1]!.query).not.toHaveProperty("before");
    f.unmount();
  });
});
