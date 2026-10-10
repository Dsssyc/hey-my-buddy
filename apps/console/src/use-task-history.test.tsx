import { StrictMode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type { Task, TaskPage, TaskQuery } from "./types";
import { useTaskHistory } from "./use-task-history";

const task = (runId: string, revision = 1): Task => ({ runId, revision, task: runId, status: "queued", owner: "host",
  cwd: "/allocated/checkout", createdAt: "2026-09-24T12:00:00Z", acceptedAt: null, acceptanceVerdict: null });
/** Rows in the route's real stable order: createdAt strictly descending with position. */
const descendingRow = (runId: string, position: number): Task => ({
  ...task(runId),
  createdAt: new Date(Date.parse("2026-09-24T12:00:00Z") - position * 1000).toISOString(),
});
const descending = (prefix: string, count: number): Task[] =>
  Array.from({ length: count }, (_, index) => descendingRow(`${prefix}-${index}`, index));
const page = (runs: Task[], nextCursor: string | null = null): TaskPage => ({ runs, total: 3, nextCursor });
const query: TaskQuery = { rootsOnly: true, query: "", projectId: "project-a", hostId: "host-a", filter: "all" };
function harness(readVerifiedAt?: (value: unknown) => number | null, active = true, strict = false) {
  const requests: { query: TaskQuery; signal?: AbortSignal; resolve: (value: TaskPage) => void; reject: (error: Error) => void }[] = [];
  const tasks = vi.fn((params: TaskQuery, signal?: AbortSignal) => new Promise<TaskPage>((resolve, reject) => {
    requests.push({ query: params, signal, resolve, reject });
  }));
  const api = { tasks, readVerifiedAt } as unknown as ConsoleApi;
  const hook = renderHook(({ query, active }) => useTaskHistory(api, query, active), { initialProps: { query, active }, wrapper: strict ? StrictMode : undefined });
  return { ...hook, requests, tasks };
}
const startRead = () => act(async () => { await vi.advanceTimersByTimeAsync(500); });

/** Fakes the browser's Page Visibility; the real browser check belongs to the Host. */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { value: hidden ? "hidden" : "visible", configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); setHidden(false); });

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

  it("keeps loaded history while hidden, re-reads its own first page once on return, and aborts pending work on hide and unmount", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("kept")], "next")));
    act(() => { void f.result.current.more(); });
    f.rerender({ query, active: false });
    expect(f.requests[1].signal?.aborted).toBe(true);
    await act(async () => f.requests[1].resolve(page([task("late-hidden")])));
    f.rerender({ query, active: true });
    // Returning to the view reads its own first page once (no cursor), never
    // dropping the loaded history.
    expect(f.requests[2].query).toMatchObject({ rootsOnly: true, limit: 50 });
    expect(f.requests[2].query).not.toHaveProperty("before");
    await act(async () => f.requests[2].resolve(page([task("kept", 2), task("fresh")], "polled")));
    // The one loaded row is inside the polled page (its last row sits at the
    // window's floor in keyset order), so the page itself proves coverage and
    // the in-place refresh lands without any continuation.
    expect(f.result.current.runs.map(t => [t.runId, t.revision])).toEqual([["kept", 2]]);
    expect(f.result.current.nextCursor).toBe("next");
    act(() => { void f.result.current.more(); });
    expect(f.requests[3].query.before).toBe("next");
    f.unmount();
    expect(f.requests[3].signal?.aborted).toBe(true);
  });

  it("keeps the filter choices from its own reads while a scope change reloads the rows", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([{ ...task("kept"), cwd: "/p", delegation: { kind: "goal", sourceHostId: "host-a", currentHostId: "host-a", parentRunId: null, rootRunId: "kept", project: { id: "project-a", label: "A", path: "/a" }, configuration: null } }], "next")));
    expect(f.result.current.known.map(t => t.runId)).toEqual(["kept"]);
    // A scope change clears the displayed rows; the choices memory survives.
    f.rerender({ query: { ...query, filter: "host" }, active: true });
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.known.map(t => t.runId)).toEqual(["kept"]);
  });

  it("polls its own first page, refreshing loaded rows and collecting unseen records for 回到最新", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("kept")], "cursor-1")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The poll reads the records route's newest page, never a snapshot.
    expect(f.requests[1].query).toMatchObject({ rootsOnly: true, limit: 50 });
    expect(f.requests[1].query).not.toHaveProperty("before");
    await act(async () => f.requests[1].resolve({ ...page([task("kept", 2), task("newer")]), total: 2 }));
    // Loaded rows refresh in place without inserting or reordering; the
    // unseen record waits behind the 回到最新 notice with the live total.
    expect(f.result.current.runs.map(t => [t.runId, t.revision])).toEqual([["kept", 2]]);
    expect([...f.result.current.newIds]).toEqual(["newer"]);
    expect(f.result.current.total).toBe(2);
    // 回到最新 rebuilds from a fresh first page and clears the notice.
    act(() => { f.result.current.reset(); });
    await startRead();
    await act(async () => f.requests[2].resolve(page([task("newer"), task("kept", 2)])));
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["newer", "kept"]);
    expect(f.result.current.newIds.size).toBe(0);
  });

  it("invalidates a scope changed while hidden and reloads the new first page on return", async () => {
    const f = harness(() => 1234);
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("kept")], "old-cursor")));
    expect(f.result.current.verifiedAtMs).toBe(1234);
    act(() => setHidden(true));
    const nextQuery: TaskQuery = { ...query, filter: "host" };
    f.rerender({ query: nextQuery, active: true });
    // Hidden: the old range leaves at once instead of posing as the new filter.
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.newIds.size).toBe(0);
    expect(f.result.current.nextCursor).toBeNull();
    expect(f.result.current.verifiedAtMs).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.tasks).toHaveBeenCalledTimes(2);
    await startRead();
    expect(f.requests[1].query).toMatchObject({ ...nextQuery, limit: 50 });
    expect(f.requests[1].query).not.toHaveProperty("before");
    await act(async () => f.requests[1].resolve(page([task("waiting-host")], "new-cursor")));
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["waiting-host"]);
    expect(f.result.current.nextCursor).toBe("new-cursor");
    f.unmount();
  });

  it("drops rows a complete poll proves out of scope, and revalidates a beyond-window member through its own cursor chain", async () => {
    const f = harness();
    const pair = descending("pair", 2);
    await startRead();
    await act(async () => f.requests[0].resolve(page(pair)));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // A complete poll page for the same filter that no longer returns
    // "pair-1" proves it left the scope: it leaves the list instead of
    // lingering forever behind the loaded window.
    await act(async () => f.requests[1].resolve({ ...page([pair[0]]), total: 1, nextCursor: null }));
    expect(f.result.current.runs.map(t => [t.runId, t.revision])).toEqual([[pair[0].runId, 1]]);
    expect(f.result.current.total).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // An incomplete first page ends before the loaded window's floor: the
    // poll walks its own cursor chain until the floor is reached. The
    // continuation still returns "pair-0", so the beyond-window member is
    // proven matching and stays, while the unseen record waits behind the
    // notice.
    const brandNew = { ...descendingRow("brand-new", -1) };
    await act(async () => f.requests[2].resolve({ ...page([brandNew]), total: 2, nextCursor: "cursor-2" }));
    expect(f.requests[3].query.before).toBe("cursor-2");
    await act(async () => f.requests[3].resolve({ ...page([{ ...pair[0], revision: 2 }]), total: 2 }));
    expect(f.result.current.runs.map(t => [t.runId, t.revision])).toEqual([[pair[0].runId, 2]]);
    expect([...f.result.current.newIds]).toEqual(["brand-new"]);
    expect(f.result.current.total).toBe(2);
    f.unmount();
  });

  it("evicts a departed row proven out by the continuation walk across a multi-page loaded history, keeping order", async () => {
    const f = harness();
    const seventy = descending("host", 70);
    await startRead();
    await act(async () => f.requests[0].resolve({ ...page(seventy.slice(0, 50)), nextCursor: "page2" }));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1].resolve(page(seventy.slice(50))));
    expect(f.result.current.runs).toHaveLength(70);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The poll's first page covers 50 of the 69 still-matching rows; its
    // cursor chain continues through the loaded window. "host-0" has left the
    // filter and is evicted; everything else keeps its committed order.
    await act(async () => f.requests[2].resolve({ ...page(seventy.slice(1, 51)), total: 69, nextCursor: "walk2" }));
    expect(f.requests[3].query.before).toBe("walk2");
    await act(async () => f.requests[3].resolve({ ...page(seventy.slice(51)), total: 69, nextCursor: null }));
    expect(f.result.current.runs.map(t => t.runId)).toEqual(seventy.slice(1).map(t => t.runId));
    expect(f.result.current.runs).toHaveLength(69);
    f.unmount();
  });

  it("evicts a departed member of a large filtered scope without enumerating the hundreds of unloaded matches", async () => {
    const f = harness();
    const fourHundred = descending("large-host", 400);
    let changed = false;
    f.tasks.mockImplementation(async (query: TaskQuery) => {
      const rows = changed ? fourHundred.slice(1) : fourHundred;
      const start = query.before ? Number(query.before) : 0;
      return { runs: rows.slice(start, start + 50), total: rows.length,
        nextCursor: rows.length > start + 50 ? String(start + 50) : null };
    });
    await startRead();
    await act(async () => { await f.result.current.more(); });
    expect(f.result.current.runs).toHaveLength(100);
    changed = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The walk stops at the loaded window's floor: one continuation past the
    // first page proves the window — the hundreds of unloaded matches below
    // it are never enumerated.
    expect(f.result.current.runs.map(t => t.runId)).not.toContain("large-host-0");
    expect(f.result.current.runs.map(t => t.runId)).toContain("large-host-99");
    expect(f.result.current.runs).toHaveLength(99);
    expect(f.tasks.mock.calls.length).toBeLessThanOrEqual(5);
    f.unmount();
  });

  it("evicts a departed member from the middle of the loaded window and keeps both neighbours", async () => {
    const f = harness();
    const hundred = descending("deep-host", 100);
    let changed = false;
    f.tasks.mockImplementation(async (query: TaskQuery) => {
      const rows = changed ? hundred.filter(row => row.runId !== "deep-host-50") : hundred;
      const start = query.before ? Number(query.before) : 0;
      return { runs: rows.slice(start, start + 50), total: rows.length,
        nextCursor: rows.length > start + 50 ? String(start + 50) : null };
    });
    await startRead();
    await act(async () => { await f.result.current.more(); });
    expect(f.result.current.runs).toHaveLength(100);
    changed = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.result.current.runs.map(t => t.runId)).not.toContain("deep-host-50");
    expect(f.result.current.runs.map(t => t.runId)).toContain("deep-host-49");
    expect(f.result.current.runs.map(t => t.runId)).toContain("deep-host-51");
    expect(f.result.current.runs).toHaveLength(99);
    f.unmount();
  });

  it("stops the walk at a keyset tie boundary and keeps the members a complete tie page covers", async () => {
    const f = harness();
    // Three loaded members share one createdAt; the route orders the tie by
    // task id descending: t-3, t-2, t-1. The floor is t-1.
    const tied = ["t-3", "t-2", "t-1"].map(runId => task(runId));
    await startRead();
    await act(async () => f.requests[0].resolve(page(tied)));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The polled page stops inside the tie before the floor; the continuation
    // ends exactly at the floor (same createdAt, same task id) while older
    // rows still exist below it: the tie comparison must prove coverage right
    // there — without walking the older rows.
    await act(async () => f.requests[1].resolve({ ...page([tied[0]]), total: 4, nextCursor: "inside-tie" }));
    expect(f.requests[2].query.before).toBe("inside-tie");
    await act(async () => f.requests[2].resolve({ ...page([tied[1], tied[2]]), total: 4, nextCursor: "below-tie" }));
    // The tie comparison proved coverage right at the floor; the older rows
    // below it are never walked.
    expect(f.result.current.runs.map(t => t.runId)).toEqual(["t-3", "t-2", "t-1"]);
    expect(f.tasks.mock.calls.length).toBe(3);
    f.unmount();
  });

  it("keeps the loaded rows honestly when a huge new-top flood keeps the walk from reaching the floor", async () => {
    const f = harness();
    const hundred = descending("flood-host", 100);
    // A contract-compliant global order: 300 new rows above, then the loaded
    // window, then everything older. The floor (flood-host-99) sits at
    // position 399 — past what the bounded walk may cover.
    const newest = (index: number): Task => ({
      ...task(`new-row-${index}`),
      createdAt: new Date(Date.parse("2026-09-24T12:00:00Z") + (300 - index) * 1000).toISOString(),
    });
    const scope = [...Array.from({ length: 300 }, (_, index) => newest(index)), ...hundred];
    let changed = false;
    f.tasks.mockImplementation(async (query: TaskQuery) => {
      const rows = changed ? scope : hundred;
      const start = query.before ? Number(query.before) : 0;
      return { runs: rows.slice(start, start + 50), total: rows.length,
        nextCursor: rows.length > start + 50 ? String(start + 50) : null };
    });
    await startRead();
    await act(async () => { await f.result.current.more(); });
    expect(f.result.current.runs).toHaveLength(100);
    changed = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The bounded walk covers the flood's first pages and runs out long
    // before the floor: no proof, no eviction — flood-host-7 has in fact
    // left the filter, and claiming its absence here would be a guess, so
    // every loaded row is kept and the newcomers wait behind the notice.
    expect(f.result.current.runs.map(t => t.runId)).toContain("flood-host-7");
    expect(f.result.current.runs).toHaveLength(100);
    expect([...f.result.current.newIds].length).toBeGreaterThan(0);
    f.unmount();
  });

  it("does not start a continuation read when the first-page reply arrives hidden", async () => {
    const f = harness();
    // The loaded window's floor (loaded-49) is older than every row of the
    // polled first page, so the walk must continue past the first page.
    const loaded = descending("loaded", 50);
    const fresher = Array.from({ length: 50 }, (_, index) => ({
      ...task(`fresher-${index}`),
      createdAt: new Date(Date.parse("2026-09-24T12:00:00Z") + (50 - index) * 1000).toISOString(),
    }));
    await startRead();
    await act(async () => f.requests[0].resolve({ ...page(loaded), total: 400, nextCursor: "50" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.requests).toHaveLength(2);
    // The page hides before the visibilitychange cleanup lands; the first
    // page's reply arrives inside that gap. Its in-place refresh stands, but
    // the walk must not issue the next GET, and without proof it must not
    // evict the loaded rows either.
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    await act(async () => f.requests[1].resolve({ ...page(fresher), total: 400, nextCursor: "50" }));
    expect(f.requests).toHaveLength(2);
    expect(f.result.current.runs.map(t => t.runId)).toEqual(loaded.map(t => t.runId));
    expect([...f.result.current.newIds]).toEqual(fresher.map(t => t.runId));
    f.unmount();
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  it("D2-H00 allows the initial mount debounce when the document hides before cleanup", async () => {
    const tasks = vi.fn(async () => ({ runs: [], total: 0, nextCursor: null }));
    const api = { tasks } as unknown as ConsoleApi;
    renderHook(() => useTaskHistory(api, { rootsOnly: true, query: "", projectId: "", hostId: "", filter: "all" }, true));
    // The document hides before the visibilitychange cleanup lands; the
    // initial mount debounce still supplies the one allowed first GET.
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(tasks).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  it("gates the scope-change debounce when the document hides before cleanup", async () => {
    const tasks = vi.fn(async () => ({ runs: [], total: 0, nextCursor: null }));
    const api = { tasks } as unknown as ConsoleApi;
    const hook = renderHook(({ query }: { query: TaskQuery }) => useTaskHistory(api, query, true),
      { initialProps: { query: { rootsOnly: true, query: "", projectId: "", hostId: "", filter: "all" as const } } });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(tasks).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    hook.rerender({ query: { rootsOnly: true, query: "", projectId: "p2", hostId: "", filter: "all" as const } });
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    expect(tasks).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  it("keeps loaded rows untouched when the continuation walk cannot prove coverage", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("waiting"), task("settled")], "cursor-1")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    // The polled page carries a row the loaded window does not hold, so the
    // coverage target sits past the first page; every continuation stays
    // incomplete and the bounded walk runs out without proof.
    await act(async () => f.requests[1].resolve(page([task("brand-new"), task("waiting", 2)], "walk-1")));
    // The first page publishes at once (in-place refresh, live total, notice),
    // but the continuation never lands: without coverage the poll proves
    // nothing, so "settled" — absent from the first page — is not evicted.
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.result.current.runs.map(t => [t.runId, t.revision])).toEqual([["waiting", 2], ["settled", 1]]);
    expect([...f.result.current.newIds]).toEqual(["brand-new"]);
    expect(f.result.current.total).toBe(3);
    f.unmount();
  });

  it("keeps newly read projects in the bounded choices memory after it filled", async () => {
    const filler = Array.from({ length: 100 }, (_, index) => task(`fill-${String(index).padStart(3, "0")}`));
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page(filler)));
    expect(f.result.current.known).toHaveLength(100);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    const newcomer = { ...task("new-project"), cwd: "/new-project",
      delegation: { kind: "goal" as const, sourceHostId: "new-host", currentHostId: "new-host",
        parentRunId: null, rootRunId: "new-project",
        project: { id: "project-new", label: "New", path: "/new-project" }, configuration: null } };
    // A complete poll page: the whole loaded window is covered in one read.
    await act(async () => f.requests[1].resolve(page([newcomer])));
    // The bounded memory must keep the new read's project and host for the
    // filter dropdowns; a full memory must evict its oldest facts, not them.
    expect(f.result.current.known.some(t => t.runId === "new-project")).toBe(true);
    f.unmount();
  });

  it("stops the records poll while the page is hidden and polls once immediately on return", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("kept")], "cursor-1")));
    act(() => setHidden(true));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(f.tasks).toHaveBeenCalledTimes(2);
    expect(f.requests[1].query).not.toHaveProperty("before");
    f.unmount();
  });
});

describe("U09 local records refresh", () => {
  it("refreshes only the bounded loaded window in place and keeps the paging cursor without adding a schedule", async () => {
    const f = harness();
    await startRead();
    const rows = descending("loaded", 100);
    await act(async () => f.requests[0].resolve(page(rows.slice(0, 50), "page-2")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1].resolve(page(rows.slice(50), "unloaded-page-3")));
    const timers = vi.getTimerCount();
    act(() => { void f.result.current.refresh(); });
    expect(f.result.current.refreshing).toBe(true);
    expect(f.requests[2].query).toEqual({ ...query, limit: 50 });
    await act(async () => f.requests[2].resolve({ ...page(rows.slice(0, 50).map(row => ({ ...row, revision: 2 })), "walk-2"), total: 200 }));
    expect(f.requests).toHaveLength(4);
    expect(f.requests[3].query).toEqual({ ...query, limit: 50, before: "walk-2" });
    await act(async () => f.requests[3].resolve({ ...page(rows.slice(50).map(row => ({ ...row, revision: 2 })), "unloaded-many"), total: 200 }));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(rows.map(row => row.runId));
    expect(f.result.current.runs.every(row => row.revision === 2)).toBe(true);
    expect(f.result.current.nextCursor).toBe("unloaded-page-3");
    expect(f.result.current.total).toBe(200);
    expect(f.requests).toHaveLength(4);
    expect(f.result.current.refreshing).toBe(false);
    expect(vi.getTimerCount()).toBe(timers);
  });

  it("keeps loaded records and cursor on a failed local refresh and retries the same scope", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("kept")], "older")));
    act(() => { void f.result.current.refresh(); });
    await act(async () => f.requests[1].reject(new Error("local read failed")));
    expect(f.result.current.error).toBe("local read failed");
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["kept"]);
    expect(f.result.current.nextCursor).toBe("older");
    expect(f.result.current.refreshing).toBe(false);
    act(() => { void f.result.current.refresh(); });
    expect(f.requests[2].query).toEqual(f.requests[1].query);
    await act(async () => f.requests[2].resolve(page([task("kept", 2)])));
    expect(f.result.current.error).toBe("");
    expect(f.result.current.runs[0].revision).toBe(2);
    expect(f.result.current.nextCursor).toBe("older");
  });

  it("aborts and ignores a local refresh response after the filter changes", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("old-scope")], "old-cursor")));
    act(() => { void f.result.current.refresh(); });
    const nextQuery = { ...query, projectId: "another-project" };
    f.rerender({ query: nextQuery, active: true });
    expect(f.requests[1].signal?.aborted).toBe(true);
    await startRead();
    await act(async () => f.requests[1].resolve(page([task("wrong-row")], "wrong-cursor")));
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.newIds.size).toBe(0);
    expect(f.result.current.known.some(row => row.runId === "wrong-row")).toBe(false);
    expect(f.result.current.nextCursor).toBeNull();
    expect(f.result.current.loading).toBe(true);
    await act(async () => f.requests[2].resolve(page([task("new-scope")], "new-cursor")));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["new-scope"]);
    expect(f.result.current.nextCursor).toBe("new-cursor");
  });

  it("does not carry a queued local refresh into a later filter scope", async () => {
    const f = harness();
    await startRead();
    act(() => { void f.result.current.refresh(); });
    const nextQuery = { ...query, hostId: "another-host" };
    f.rerender({ query: nextQuery, active: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(f.requests).toHaveLength(1);
    await startRead();
    expect(f.requests).toHaveLength(2);
    expect(f.requests[1].query).toMatchObject(nextQuery);
    await act(async () => f.requests[1].resolve(page([task("current")])));
    await act(async () => f.requests[0].resolve(page([task("late")])));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["current"]);
    expect(f.requests).toHaveLength(2);
  });

  it("reports actual page verification conservatively across loaded pages and never uses record timestamps", async () => {
    const checks = new WeakMap<object, number | null>();
    const f = harness();
    // The same hook API object is reachable through its read function; expose
    // a checked-response sidechannel without changing the page DTO.
    const api = { tasks: f.tasks, readVerifiedAt: (value: unknown) => checks.get(value as object) ?? null } as unknown as ConsoleApi;
    f.unmount();
    const h = renderHook(() => useTaskHistory(api, query, true));
    await startRead();
    const first = page([task("first")], "older"); checks.set(first, 1000);
    await act(async () => f.requests[0].resolve(first));
    expect(h.result.current.verifiedAtMs).toBe(1000);
    act(() => { void h.result.current.more(); });
    const older = page([task("older")], "more"); checks.set(older, 500);
    await act(async () => f.requests[1].resolve(older));
    expect(h.result.current.verifiedAtMs).toBe(500);
    act(() => { void h.result.current.refresh(); });
    const refreshed = page([task("first", 2), task("older", 2)]); checks.set(refreshed, 2000);
    await act(async () => f.requests[2].resolve(refreshed));
    expect(h.result.current.verifiedAtMs).toBe(2000);
    act(() => { void h.result.current.refresh(); });
    await act(async () => f.requests[3].resolve(page([task("first", 3), task("older", 3)])));
    expect(h.result.current.verifiedAtMs).toBeNull();
  });
});

describe("U09 retry the failed history read", () => {
  it.each([null, "older-page"])("retries a failed local first-page refresh in place with cursor %s", async cursor => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("selected")], cursor)));
    const timers = vi.getTimerCount();
    act(() => { void f.result.current.refresh(); });
    await act(async () => f.requests[1].reject(new Error("local first page unavailable")));
    expect(f.result.current.error).toBe("local first page unavailable");
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(3);
    expect(f.requests[2].query).toEqual({ ...query, limit: 50 });
    await act(async () => f.requests[2].resolve(page([task("selected", 2), task("new")], "server-first-page-cursor")));
    expect(f.result.current.runs.map(row => [row.runId, row.revision])).toEqual([["selected", 2]]);
    expect(f.result.current.newIds.has("new")).toBe(true);
    expect(f.result.current.nextCursor).toBe(cursor);
    expect(f.result.current.error).toBe("");
    expect(vi.getTimerCount()).toBe(timers);
  });

  it.each([null, "older-page"])("retries a failed scheduled first-page poll with cursor %s", async cursor => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("loaded")], cursor)));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1].reject(new Error("poll unavailable")));
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(3);
    expect(f.requests[2].query).toEqual({ ...query, limit: 50 });
    await act(async () => f.requests[2].resolve(page([task("loaded", 2)])));
    expect(f.result.current.runs.map(row => [row.runId, row.revision])).toEqual([["loaded", 2]]);
    expect(f.result.current.nextCursor).toBe(cursor);
    expect(f.result.current.error).toBe("");
  });

  it("preserves a failed append and its cursor through a successful first-page poll until append retry succeeds", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("loaded")], "failed-append-cursor")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1].reject(new Error("append unavailable")));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[2].resolve(page([task("loaded", 2)])));
    expect(f.result.current.error).toBe("append unavailable");
    expect(f.result.current.nextCursor).toBe("failed-append-cursor");
    act(() => { void f.result.current.retry(); void f.result.current.retry(); });
    expect(f.requests).toHaveLength(4);
    expect(f.requests[3].query).toEqual({ ...query, limit: 50, before: "failed-append-cursor" });
    await act(async () => f.requests[3].resolve(page([task("older")], "next-append-cursor")));
    expect(f.result.current.runs.map(row => [row.runId, row.revision])).toEqual([["loaded", 2], ["older", 1]]);
    expect(f.result.current.nextCursor).toBe("next-append-cursor");
    expect(f.result.current.error).toBe("");
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(4);
  });

  it("restarts a failed refresh continuation from page one and preserves the bounded loaded window", async () => {
    const f = harness();
    const rows = descending("loaded", 100);
    await startRead();
    await act(async () => f.requests[0].resolve(page(rows.slice(0, 50), "loaded-page-2")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1].resolve(page(rows.slice(50), "unloaded-page-3")));
    act(() => { void f.result.current.refresh(); });
    await act(async () => f.requests[2].resolve(page(rows.slice(0, 50).map(row => ({ ...row, revision: 2 })), "first-walk-cursor")));
    expect(f.requests).toHaveLength(4);
    await act(async () => f.requests[3].reject(new Error("continuation unavailable")));
    expect(f.result.current.runs).toHaveLength(100);
    expect(f.result.current.nextCursor).toBe("unloaded-page-3");
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(5);
    expect(f.requests[4].query).toEqual({ ...query, limit: 50 });
    await act(async () => f.requests[4].resolve(page(rows.slice(0, 50).map(row => ({ ...row, revision: 3 })), "fresh-walk-cursor")));
    expect(f.requests).toHaveLength(6);
    expect(f.requests[5].query).toEqual({ ...query, limit: 50, before: "fresh-walk-cursor" });
    await act(async () => f.requests[5].resolve(page(rows.slice(50).map(row => ({ ...row, revision: 3 })), "more-unloaded")));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(rows.map(row => row.runId));
    expect(f.result.current.runs.every(row => row.revision === 3)).toBe(true);
    expect(f.result.current.nextCursor).toBe("unloaded-page-3");
    expect(f.result.current.error).toBe("");
    expect(f.requests).toHaveLength(6);
  });

  it("discards the failed read when filters change and never retries its cursor in the new scope", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("old")], "old-append")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[1].reject(new Error("old append failed")));
    const nextQuery = { ...query, projectId: "another-project" };
    f.rerender({ query: nextQuery, active: true });
    expect(f.result.current.error).toBe("");
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(2);
    await startRead();
    expect(f.requests).toHaveLength(3);
    expect(f.requests[2].query).toEqual({ ...nextQuery, limit: 50 });
    await act(async () => f.requests[2].resolve(page([task("current")], "current-cursor")));
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(3);
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["current"]);
  });

  it("isolates a superseded retry response and late failure from the current scope's failed append", async () => {
    const f = harness();
    await startRead();
    await act(async () => f.requests[0].resolve(page([task("old")], "old-cursor")));
    act(() => { void f.result.current.refresh(); });
    await act(async () => f.requests[1].reject(new Error("old poll failed")));
    act(() => { void f.result.current.retry(); });
    const nextQuery = { ...query, hostId: "new-host" };
    f.rerender({ query: nextQuery, active: true });
    expect(f.requests[2].signal?.aborted).toBe(true);
    await startRead();
    await act(async () => f.requests[3].resolve(page([task("current")], "current-append")));
    act(() => { void f.result.current.more(); });
    await act(async () => f.requests[4].reject(new Error("current append failed")));
    await act(async () => f.requests[2].reject(new Error("late wrong failure")));
    expect(f.result.current.error).toBe("current append failed");
    act(() => { void f.result.current.retry(); });
    expect(f.requests).toHaveLength(6);
    expect(f.requests[5].query).toEqual({ ...nextQuery, limit: 50, before: "current-append" });
    await act(async () => f.requests[5].resolve(page([task("current-older")])));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["current", "current-older"]);
    expect(f.result.current.error).toBe("");
  });
});


describe("D2 hidden initialization (H)", () => {
  it("D2-H01 reads hidden content and verification once, pauses polling and reads immediately on return", async () => {
    setHidden(true);
    const f = harness(() => 1234);
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(1);
    expect(f.requests[0].query).toMatchObject({ ...query, limit: 50 });
    expect(f.requests[0].query).not.toHaveProperty("before");
    await act(async () => f.requests[0].resolve(page([task("initial")], "older")));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["initial"]);
    expect(f.result.current.verifiedAtMs).toBe(1234);
    expect(f.result.current.loading).toBe(false);
    expect(f.result.current.nextCursor).toBe("older");
    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.tasks).toHaveBeenCalledTimes(2);
    await act(async () => f.requests[1].resolve(page([task("initial")], "older")));
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(2);
    expect(f.result.current.nextCursor).toBe("older");
  });

  it("D2-H02 leaves an inactive list unread and initializes its first hidden activation once in StrictMode", async () => {
    setHidden(true);
    const f = harness(() => 1234, false, true);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).not.toHaveBeenCalled();
    const allRecords: TaskQuery = { ...query, rootsOnly: false };
    f.rerender({ query: allRecords, active: true });
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(1);
    expect(f.requests[0].query).toMatchObject({ ...allRecords, limit: 50 });
    await act(async () => f.requests[0].resolve(page([task("initial")])));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["initial"]);
    expect(f.result.current.verifiedAtMs).toBe(1234);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
  });

  it("D2-H03 cleans up the hidden mount debounce in StrictMode without duplicating or reading after unmount", async () => {
    setHidden(true);
    const f = harness(undefined, true, true);
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(1);
    f.unmount();
    expect(f.requests[0].signal?.aborted).toBe(true);
    const g = harness(undefined, true, true);
    g.unmount();
    await startRead();
    expect(g.tasks).not.toHaveBeenCalled();
  });

  it("D2-H04 aborts a hidden initial request on unmount and ignores a late response and verification", async () => {
    setHidden(true);
    const f = harness(() => 9999);
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(1);
    const before = f.result.current;
    f.unmount();
    expect(f.requests[0].signal?.aborted).toBe(true);
    await act(async () => f.requests[0].resolve(page([task("late", 9)], "late-cursor")));
    expect(f.result.current).toBe(before);
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.verifiedAtMs).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
  });

  it("D2-H05 fences a superseded hidden initial response and waits for visibility before reading its replacement", async () => {
    setHidden(true);
    const f = harness(() => 4321);
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(1);
    const changed = { ...query, projectId: "another-project" };
    f.rerender({ query: changed, active: true });
    expect(f.requests[0].signal?.aborted).toBe(true);
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.nextCursor).toBeNull();
    expect(f.result.current.verifiedAtMs).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.tasks).toHaveBeenCalledTimes(2);
    expect(f.requests[1].query).toMatchObject({ ...changed, limit: 50 });
    expect(f.requests[1].query).not.toHaveProperty("before");
    await act(async () => f.requests[0].resolve(page([task("late", 9)], "late-cursor")));
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.verifiedAtMs).toBeNull();
    expect(f.result.current.loading).toBe(true);
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(2);
    await act(async () => f.requests[1].resolve(page([task("fresh", 2)], "fresh-cursor")));
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["fresh"]);
    expect(f.result.current.verifiedAtMs).toBe(4321);
    expect(f.result.current.nextCursor).toBe("fresh-cursor");
  });

  it.each(["empty", "failure"])("D2-H06 pauses after a hidden initial %s and reads once immediately on return", async outcome => {
    setHidden(true);
    const f = harness(() => 1234);
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(1);
    await act(async () => {
      if (outcome === "empty") f.requests[0].resolve(page([]));
      else f.requests[0].reject(new Error("initial unavailable"));
    });
    expect(f.result.current.runs).toEqual([]);
    expect(f.result.current.verifiedAtMs).toBe(outcome === "empty" ? 1234 : null);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.tasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.tasks).toHaveBeenCalledTimes(2);
    await act(async () => f.requests[1].resolve(page([task("fresh", 2)])));
    await startRead();
    expect(f.tasks).toHaveBeenCalledTimes(2);
    expect(f.result.current.runs.map(row => row.runId)).toEqual(["fresh"]);
  });
});
