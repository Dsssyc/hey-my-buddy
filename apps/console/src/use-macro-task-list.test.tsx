import { StrictMode } from "react";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { createApi } from "./api";
import { MacroTaskList } from "./MacroTaskList";
import { TooltipProvider } from "./components/ui/tooltip";
import { macroTaskGroups } from "./macro-task-fixtures";
import * as formatting from "./i18n/format";
import type { MacroTaskPage, MacroTaskQuery, MacroTaskSummary } from "./macro-task-types";
import { useMacroTaskList } from "./use-macro-task-list";
import { VIEW_PREFERENCE_PREFIX } from "./view-preferences";

const summary = (macroTaskId: string, lastActivitySeq: number, overrides: Partial<MacroTaskSummary> = {}): MacroTaskSummary => ({
  macroTaskId, kind: "macro-task", title: macroTaskId, titleSource: "macro-task", summary: null,
  project: { id: "p1", label: "项目一", path: "/p1" }, sourceHostId: "host-a", currentHostIds: [],
  createdAt: "2026-09-26T01:00:00Z", lastActivityAt: "2026-09-26T01:00:00Z", lastActivitySeq,
  state: "active", counts: { roots: 1, helpers: 0, accepted: 0, failed: 0, active: 1, host: 0, review: 0, ended: 0,
    stoppableRoots: 1, stoppableHelpers: 0, delivered: 0, awaitingHost: 0 },
  matchingRuns: 1, rootRunIds: [macroTaskId], ...overrides,
});
const project = (id: string) => ({ id, label: id, path: `/${id}` });
const page = (rows: MacroTaskSummary[], nextCursor: string | null = null, changed = false, groupTotal?: number): MacroTaskPage =>
  ({ groups: macroTaskGroups(rows, nextCursor).map(group => groupTotal === undefined ? group : { ...group, total: groupTotal }),
    totals: { all: groupTotal ?? rows.length, active: groupTotal ?? rows.length, host: 0, review: 0 }, cursor: 10, changed });
const query: MacroTaskQuery = { query: "", hostId: "", filter: "all" };

function harness(readVerifiedAt?: (value: unknown) => number | null, active = true, strict = false) {
  const requests: { query: MacroTaskQuery; signal?: AbortSignal; resolve: (value: MacroTaskPage) => void; reject: (error: Error) => void }[] = [];
  const macroTasks = vi.fn((params: MacroTaskQuery, signal?: AbortSignal) => new Promise<MacroTaskPage>((resolve, reject) => {
    requests.push({ query: params, signal, resolve, reject });
  }));
  const api = { macroTasks, readVerifiedAt } as unknown as ConsoleApi;
  const hook = renderHook(({ query, active }) => useMacroTaskList(api, query, active), { initialProps: { query, active }, wrapper: strict ? StrictMode : undefined });
  return { ...hook, requests, macroTasks };
}
const startRead = () => act(async () => { await vi.advanceTimersByTimeAsync(500); });
const ids = (f: ReturnType<typeof harness>) => f.result.current.rows.map(row => row.macroTaskId);
function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { value: hidden ? "hidden" : "visible", configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => { cleanup(); vi.useRealTimers(); setHidden(false); localStorage.clear(); });

describe("grouped macro task list hook", () => {
  it("keeps the 304 envelope reference and does not redraw entries on verification", async () => {
    const body = page([summary("first", 3)]);
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(body), { headers: { ETag: '"list"', Date: "Thu, 01 Jan 1970 00:00:01 GMT" } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: "Thu, 01 Jan 1970 00:00:04 GMT" } }));
    const api = createApi("", fetcher);
    const view = renderHook(() => useMacroTaskList(api, query, true));
    await startRead();
    const groups = view.result.current.groups;
    expect(groups).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1]![1]!.headers).toEqual({ "If-None-Match": '"list"' });
    expect(view.result.current.groups).toBe(groups);
  });

  it("pages one project using its own cursor and keeps the other projects and standalone group", async () => {
    const f = harness(); await startRead();
    const first = summary("first", 9), second = summary("second", 8, { project: project("p2") });
    const standalone = summary("loose", 7, { kind: "standalone", project: null });
    const listing = page([first, second, standalone], "older");
    listing.groups[1]!.total = 2;
    await act(async () => f.requests[0]!.resolve(listing));
    expect(f.result.current.groups.map(group => group.id)).toEqual(["project:p1", "project:p2", "standalone"]);
    act(() => f.result.current.more("project:p2"));
    expect(f.requests[1]!.query).toMatchObject({ group: "project:p2", before: "older", limit: 20 });
    await act(async () => f.requests[1]!.resolve(page([summary("third", 3, { project: project("p2") })], null, false, 2)));
    expect(f.result.current.groups.map(group => group.macroTasks.map(row => row.macroTaskId))).toEqual([["first"], ["second", "third"], ["loose"]]);
    expect(f.result.current.groups[1]!.total).toBe(2);
    expect(f.result.current.groups[1]!.nextCursor).toBeNull();
    expect(f.result.current.groups[0]!.nextCursor).toBe("older");
    act(() => f.result.current.more("project:p2"));
    expect(f.macroTasks).toHaveBeenCalledTimes(2);
  });

  it("replays a loaded group's extent during refresh and preserves its entries", async () => {
    const f = harness(); await startRead();
    const first = Array.from({ length: 5 }, (_, index) => summary(`item-${index}`, 10 - index));
    await act(async () => f.requests[0]!.resolve(page(first, "older", false, 7)));
    act(() => f.result.current.more("project:p1"));
    await act(async () => f.requests[1]!.resolve(page([summary("item-5", 5), summary("item-6", 4)], null, false, 7)));
    expect(ids(f)).toHaveLength(7);
    expect(f.result.current.groups[0]!.total).toBe(7);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.requests[2]!.query).not.toHaveProperty("group");
    await act(async () => f.requests[2]!.resolve(page(first.map(row => ({ ...row, lastActivitySeq: row.lastActivitySeq + 1 })), "fresh-tail", false, 7)));
    expect(f.requests[3]!.query).toMatchObject({ group: "project:p1", before: "fresh-tail", limit: 2 });
    await act(async () => f.requests[3]!.resolve(page([summary("item-5", 5), summary("item-6", 4)], null, false, 7)));
    expect(ids(f)).toEqual(first.map(row => row.macroTaskId).concat(["item-5", "item-6"]));
    expect(f.result.current.groups[0]!.macroTasks).toHaveLength(7);
    expect(f.result.current.groups[0]!.total).toBe(7);
  });

  it("shows a reorder notice when activity changes between a poll's first page and group replay", async () => {
    const f = harness(); await startRead();
    const first = Array.from({ length: 5 }, (_, index) => summary(`item-${index}`, 10 - index));
    await act(async () => f.requests[0]!.resolve(page(first, "older", false, 7)));
    act(() => f.result.current.more("project:p1"));
    const tail = [summary("item-5", 5), summary("item-6", 4)];
    await act(async () => f.requests[1]!.resolve(page(tail, null, false, 7)));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[2]!.resolve(page(first, "poll-tail", false, 7)));
    expect(f.requests[3]!.query).toMatchObject({ group: "project:p1", before: "poll-tail", limit: 2 });
    await act(async () => f.requests[3]!.resolve(page(tail, null, true, 7)));
    expect(ids(f)).toEqual(first.map(row => row.macroTaskId).concat(tail.map(row => row.macroTaskId)));
    expect(f.result.current.reorder).toEqual({ count: null });
  });

  it("preserves loaded counts across sorting and reset", async () => {
    const f = harness(); await startRead();
    const first = Array.from({ length: 5 }, (_, index) => summary(`item-${index}`, 10 - index));
    await act(async () => f.requests[0]!.resolve(page(first, "older", false, 7)));
    act(() => f.result.current.more("project:p1"));
    await act(async () => f.requests[1]!.resolve(page([summary("item-5", 5), summary("item-6", 4)], null, false, 7)));
    act(() => f.result.current.changeSort("project:p1", "created-asc"));
    expect(ids(f)).toHaveLength(7);
    await startRead();
    expect(f.requests[2]!.query).not.toHaveProperty("sort");
    await act(async () => f.requests[2]!.resolve(page(first, "sorted-tail", false, 7)));
    expect(f.requests[3]!.query).toMatchObject({ group: "project:p1", limit: 5, sort: "created-asc" });
    await act(async () => f.requests[3]!.resolve(page(first, "sorted-tail", false, 7)));
    expect(f.requests[4]!.query).toMatchObject({ group: "project:p1", before: "sorted-tail", limit: 2, sort: "created-asc" });
    await act(async () => f.requests[4]!.resolve(page([summary("item-5", 5), summary("item-6", 4)], null, false, 7)));
    expect(ids(f)).toHaveLength(7);
    expect(f.result.current.groups[0]!.total).toBe(7);
    act(() => f.result.current.reset());
    await startRead();
    await act(async () => f.requests[5]!.resolve(page(first, "reset-tail", false, 7)));
    expect(f.requests[6]!.query).toMatchObject({ group: "project:p1", limit: 5, sort: "created-asc" });
    await act(async () => f.requests[6]!.resolve(page(first, "reset-tail", false, 7)));
    expect(f.requests[7]!.query).toMatchObject({ group: "project:p1", before: "reset-tail", limit: 2, sort: "created-asc" });
    await act(async () => f.requests[7]!.resolve(page([summary("item-5", 5), summary("item-6", 4)], null, false, 7)));
    expect(ids(f)).toHaveLength(7);
    expect(f.result.current.groups[0]!.total).toBe(7);
  });

  it("persists independent orders for projects and standalone and uses each order for more", async () => {
    const setup = harness(undefined, false);
    act(() => {
      setup.result.current.changeSort("project:p1", "created-asc");
      setup.result.current.changeSort("project:p2", "activity-asc");
      setup.result.current.changeSort("standalone", "created-desc");
    });
    expect(setup.result.current.sortFor("project:p1")).toBe("created-asc");
    expect(setup.result.current.sortFor("project:p2")).toBe("activity-asc");
    expect(setup.result.current.sortFor("standalone")).toBe("created-desc");
    expect(localStorage.getItem(VIEW_PREFERENCE_PREFIX + "macro-task-sort:project:p1")).toBe("created-asc");
    setup.unmount();

    const f = harness(); await startRead();
    expect(f.result.current.sortFor("project:p1")).toBe("created-asc");
    expect(f.result.current.sortFor("project:p2")).toBe("activity-asc");
    expect(f.result.current.sortFor("standalone")).toBe("created-desc");
    expect(f.result.current.sortFor("project:p3")).toBe("activity-desc");
    expect(f.requests[0]!.query).not.toHaveProperty("group");
    const p1 = summary("one", 3), p2 = summary("two", 2, { project: project("p2") });
    const loose = summary("loose", 1, { kind: "standalone", project: null });
    await act(async () => f.requests[0]!.resolve(page([p1, p2, loose], "older")));
    expect(f.requests.slice(1, 4).map(request => request.query)).toEqual([
      expect.objectContaining({ group: "project:p1", sort: "created-asc", limit: 5 }),
      expect.objectContaining({ group: "project:p2", sort: "activity-asc", limit: 5 }),
      expect.objectContaining({ group: "standalone", sort: "created-desc", limit: 5 }),
    ]);
    await act(async () => {
      f.requests[1]!.resolve(page([p1], null));
      f.requests[2]!.resolve(page([p2], "older", false, 2));
      f.requests[3]!.resolve(page([loose], null));
    });
    expect(ids(f)).toEqual(["one", "two", "loose"]);
    act(() => f.result.current.more("project:p2"));
    expect(f.requests[4]!.query).toMatchObject({ group: "project:p2", sort: "activity-asc", before: "older", limit: 20 });
    await act(async () => f.requests[4]!.resolve(page([summary("three", 0, { project: project("p2") })], null, false, 2)));
    expect(f.result.current.groups.map(group => group.macroTasks.map(row => row.macroTaskId))).toEqual([["one"], ["two", "three"], ["loose"]]);
  });

  it("keeps an open view's cursor bound to its sort when another window saves a different order", async () => {
    const f = harness(); await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("first", 3)], "older", false, 2)));
    localStorage.setItem(VIEW_PREFERENCE_PREFIX + "macro-task-sort:project:p1", "created-asc");
    expect(f.result.current.sortFor("project:p1")).toBe("activity-desc");
    act(() => f.result.current.more("project:p1"));
    expect(f.requests[1]!.query).toMatchObject({ group: "project:p1", sort: "activity-desc", before: "older" });
    await act(async () => f.requests[1]!.resolve(page([summary("second", 2)], null, false, 2)));
    expect(ids(f)).toEqual(["first", "second"]);
    f.unmount();
    const fresh = harness(undefined, false);
    expect(fresh.result.current.sortFor("project:p1")).toBe("created-asc");
  });

  it("inherits saved group order after filtering and ignores an obsolete read", async () => {
    const f = harness(); await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("old", 4)])));
    act(() => f.result.current.changeSort("project:p1", "created-desc"));
    await startRead();
    expect(f.requests[1]!.query).not.toHaveProperty("group");
    f.rerender({ query: { ...query, filter: "host" }, active: true });
    expect(f.requests[1]!.signal?.aborted).toBe(true);
    expect(ids(f)).toEqual([]);
    await startRead();
    await act(async () => f.requests[1]!.resolve(page([summary("stale", 9)])));
    expect(ids(f)).toEqual([]);
    expect(f.requests).toHaveLength(3);
    const filtered = f.requests.find(request => request.query.filter === "host" && !request.query.group)!;
    expect(filtered.query).not.toHaveProperty("sort");
    await act(async () => filtered.resolve(page([summary("current", 2)])));
    const ordered = f.requests.find(request => request.query.filter === "host" && request.query.group === "project:p1")!;
    expect(ordered.query).toMatchObject({ sort: "created-desc", limit: 5 });
    await act(async () => ordered.resolve(page([summary("current", 2)])));
    expect(ids(f)).toEqual(["current"]);
  });

  it("resets loaded groups when a filter or search changes", async () => {
    const f = harness(); await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("old", 4)], "older")));
    f.rerender({ query: { ...query, filter: "host", query: "new" }, active: true });
    expect(ids(f)).toEqual([]);
    expect(f.result.current.groups).toEqual([]);
    await startRead();
    expect(f.requests[1]!.query).toMatchObject({ filter: "host", query: "new" });
    expect(f.requests[1]!.query).not.toHaveProperty("before");
    await act(async () => f.requests[1]!.resolve(page([summary("new", 8)])));
    expect(ids(f)).toEqual(["new"]);
  });

  it("defers new activity until the reader applies the reorder notice", async () => {
    const f = harness(); await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("quiet", 3), summary("busy", 2)])));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[1]!.resolve(page([summary("fresh", 9), summary("busy", 7), summary("quiet", 3)])));
    expect(ids(f)).toEqual(["quiet", "busy"]);
    expect(f.result.current.reorder).toEqual({ count: null });
    act(() => f.result.current.applyReorder());
    expect(f.result.current.reorder).toBeNull();
    await act(async () => f.requests[2]!.resolve(page([summary("fresh", 9), summary("busy", 7), summary("quiet", 3)])));
    expect(ids(f)).toEqual(["fresh", "busy", "quiet"]);
  });

  it("keeps a notice and loaded entries when a group page arrives during polling", async () => {
    const f = harness(); await startRead();
    await act(async () => f.requests[0]!.resolve(page([summary("first", 3)], "older", false, 2)));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    act(() => f.result.current.more("project:p1"));
    expect(f.requests[1]!.signal?.aborted).toBe(true);
    expect(f.requests[2]!.query).toMatchObject({ group: "project:p1", before: "older" });
    await act(async () => f.requests[1]!.resolve(page([summary("stale", 99)])));
    await act(async () => f.requests[2]!.resolve(page([summary("older", 1)], null, false, 2)));
    expect(ids(f)).toEqual(["first", "older"]);
    expect(f.result.current.groups[0]!.total).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[3]!.resolve(page([summary("new", 10), summary("first", 3), summary("older", 1)])));
    expect(ids(f)).toEqual(["first", "older"]);
    expect(f.result.current.reorder).toBeTruthy();
  });

  it("keeps entries through a failed refresh and retries without shrinking the group", async () => {
    const f = harness(); await startRead();
    const first = Array.from({ length: 5 }, (_, index) => summary(`item-${index}`, 10 - index));
    await act(async () => f.requests[0]!.resolve(page(first, "older", false, 6)));
    act(() => f.result.current.more("project:p1"));
    await act(async () => f.requests[1]!.resolve(page([summary("item-5", 5)], null, false, 6)));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await act(async () => f.requests[2]!.reject(new Error("黑板暂时没有响应")));
    expect(f.result.current.error).toContain("黑板暂时没有响应");
    expect(ids(f)).toHaveLength(6);
    act(() => f.result.current.retry());
    await act(async () => f.requests[3]!.resolve(page(first, "retry-tail", false, 6)));
    expect(f.requests[4]!.query).toMatchObject({ group: "project:p1", before: "retry-tail", limit: 1 });
    await act(async () => f.requests[4]!.resolve(page([summary("item-5", 5)], null, false, 6)));
    expect(f.result.current.error).toBe("");
    expect(ids(f)).toHaveLength(6);
    expect(f.result.current.groups[0]!.total).toBe(6);
  });
});

describe("visibility and verification guards", () => {
  it("allows an initial hidden mount read once and pauses polling until visible", async () => {
    setHidden(true);
    const f = harness(() => 1234);
    await startRead();
    expect(f.macroTasks).toHaveBeenCalledTimes(1);
    await act(async () => f.requests[0]!.resolve(page([summary("initial", 1)], "older")));
    expect(f.result.current.verifiedAtMs).toBe(1234);
    expect(f.result.current.groups[0]!.nextCursor).toBe("older");
    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(f.macroTasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.macroTasks).toHaveBeenCalledTimes(2);
  });

  it("leaves an inactive list unread and initializes on hidden activation once in StrictMode", async () => {
    setHidden(true);
    const f = harness(() => 1234, false, true);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.macroTasks).not.toHaveBeenCalled();
    f.rerender({ query, active: true });
    await startRead();
    expect(f.macroTasks).toHaveBeenCalledTimes(1);
    await act(async () => f.requests[0]!.resolve(page([summary("initial", 1)])));
    expect(ids(f)).toEqual(["initial"]);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.macroTasks).toHaveBeenCalledTimes(1);
  });

  it("aborts hidden initial work on unmount and discards late data", async () => {
    setHidden(true);
    const f = harness(() => 9999, true, true);
    await startRead();
    const before = f.result.current;
    f.unmount();
    expect(f.requests[0]!.signal?.aborted).toBe(true);
    await act(async () => f.requests[0]!.resolve(page([summary("late", 9)])));
    expect(f.result.current).toBe(before);
    const g = harness(undefined, true, true);
    g.unmount(); await startRead();
    expect(g.macroTasks).not.toHaveBeenCalled();
  });

  it("fences a superseded hidden first read until visibility returns", async () => {
    setHidden(true);
    const f = harness(() => 4321);
    await startRead();
    const changed = { ...query, query: "different" };
    f.rerender({ query: changed, active: true });
    expect(f.requests[0]!.signal?.aborted).toBe(true);
    expect(ids(f)).toEqual([]);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.macroTasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.macroTasks).toHaveBeenCalledTimes(2);
    await act(async () => f.requests[0]!.resolve(page([summary("late", 9)])));
    expect(ids(f)).toEqual([]);
    await act(async () => f.requests[1]!.resolve(page([summary("fresh", 2)])));
    expect(ids(f)).toEqual(["fresh"]);
    expect(f.result.current.verifiedAtMs).toBe(4321);
  });

  it.each(["empty", "failure"])("resumes after a hidden initial %s without a background poll", async outcome => {
    setHidden(true);
    const f = harness(() => 1234);
    await startRead();
    await act(async () => {
      if (outcome === "empty") f.requests[0]!.resolve(page([]));
      else f.requests[0]!.reject(new Error("initial unavailable"));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(f.macroTasks).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    expect(f.macroTasks).toHaveBeenCalledTimes(2);
    await act(async () => f.requests[1]!.resolve(page([summary("fresh", 2)])));
    expect(ids(f)).toEqual(["fresh"]);
  });
});
