import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { useEditor } from "./use-editor";
import { FILTER_DEBOUNCE_MS, MAX_QUERY_CHARS, MAX_QUERY_TERMS, boundBuddyQuery, historySourceKey, mergePage, useBuddyHistory } from "./use-buddy-history";
import type { Buddy, BuddyPage, Snapshot } from "./types";

function buddy(buddyId: string): Buddy {
  return { effortRank: null, buddyId, label: buddyId, harness: "dsh", provider: "deepseek-official", model: buddyId,
    effort: "off", available: true, enabled: true, capabilities: [], contextWindow: null,
    description: "", source: "fixture" };
}
function page(ids: string[], nextCursor: string | null = null): BuddyPage {
  return { buddies: ids.map(buddy), preferences: [], modelServiceConcurrency: [], nextCursor };
}
function fakeApi(handler: (params: Record<string, unknown>) => BuddyPage | Promise<BuddyPage>) {
  const command = vi.fn(async (_operation: string, params: Record<string, unknown>) => handler(params));
  return { api: { command } as unknown as ConsoleApi, command };
}
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
afterEach(() => vi.restoreAllMocks());

describe("bounded retained model history", () => {
  it("invalidates retained history when an event changes outside the bounded snapshot", () => {
    const visible = { eventCursor: 12, buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], configuration: null } as unknown as Snapshot;
    expect(historySourceKey({ ...visible, eventCursor: 13 })).not.toBe(historySourceKey(visible));
  });
  it("bounds query terms and length", () => {
    expect(boundBuddyQuery("  deepseek   flash  ")).toBe("deepseek flash");
    expect(boundBuddyQuery("a b c d e f g h i j")).toBe("a b c d e f g h");
    expect(boundBuddyQuery("x".repeat(MAX_QUERY_CHARS + 50))).toHaveLength(MAX_QUERY_CHARS);
    expect(boundBuddyQuery("a b c d e f g h i").split(" ")).toHaveLength(MAX_QUERY_TERMS);
  });

  it("appends bounded pages without duplicating identities", () => {
    const merged = mergePage(page(["a", "b"], "b"), page(["b", "c"]));
    expect(merged.buddies.map(row => row.buddyId)).toEqual(["a", "b", "c"]);
    expect(merged.nextCursor).toBeNull();
  });

  it("does not read until opened and sends bounded search and cursor", async () => {
    const { api, command } = fakeApi(params => params.after ? page(["second"]) : page(["first"], "first"));
    const { result } = renderHook(() => useBuddyHistory(api, "csrf", "content-a",
      { query: "  first  ", harness: "dsh", enabled: true }));
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).not.toHaveBeenCalled();
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.buddies.map(row => row.buddyId)).toEqual(["first"]));
    expect(command).toHaveBeenCalledWith("buddy_list", {
      includeUnavailable: true, limit: 100, query: "first", harness: "dsh",
    }, "csrf");
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.page?.buddies.map(row => row.buddyId)).toEqual(["first", "second"]));
    expect(command).toHaveBeenLastCalledWith("buddy_list", {
      includeUnavailable: true, limit: 100, query: "first", harness: "dsh", after: "first",
    }, "csrf");
  });

  it("hides a page when snapshot content changes and rejects its queued reply", async () => {
    let resolveOld!: (value: BuddyPage) => void;
    const oldReply = new Promise<BuddyPage>(resolve => { resolveOld = resolve; });
    let reads = 0;
    const { api, command } = fakeApi(() => ++reads === 1 ? oldReply : page(["fresh"]));
    const { result, rerender } = renderHook(({ sourceKey }) => useBuddyHistory(api, "csrf", sourceKey,
      { query: "", harness: "", enabled: true }), { initialProps: { sourceKey: "content-a" } });
    act(() => result.current.open());
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1));
    rerender({ sourceKey: "content-b" });
    expect(result.current.page).toBeNull();
    await act(async () => resolveOld(page(["old"])));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.page?.buddies.map(row => row.buddyId)).toEqual(["fresh"]));
  });

  it("rereads a retained-only model service note when the board event cursor moves", async () => {
    const modelService = { harness: "dsh", provider: "deepseek-official", model: "retired" };
    let note = "older note";
    const { api, command } = fakeApi(() => ({ ...page(["retired"]),
      modelServiceNotes: [{ ...modelService, text: note, updatedAt: null }] }));
    const visible = { eventCursor: 8, buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], configuration: null } as unknown as Snapshot;
    const { result, rerender } = renderHook(({ snapshot }) => useBuddyHistory(api, "csrf", historySourceKey(snapshot),
      { query: "", harness: "", enabled: true }), { initialProps: { snapshot: visible } });
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.modelServiceNotes?.[0].text).toBe("older note"));
    note = "newer note";
    rerender({ snapshot: { ...visible, eventCursor: 9 } });
    expect(result.current.page).toBeNull();
    await waitFor(() => expect(result.current.page?.modelServiceNotes?.[0].text).toBe("newer note"));
    expect(command).toHaveBeenCalledTimes(2);
  });

  it("does not render an old retained page during the cursor-change render", () => {
    const visible = { eventCursor: 8, csrfToken: "csrf", consoleSession: { id: "session" },
      buddies: [], preferences: [], modelServiceNotes: [],
      modelServiceConcurrency: [], configuration: null, decisions: [], tasks: { pendingCount: 0 }, capabilities: {} } as Snapshot;
    const { api } = fakeApi(() => page([]));
    const { result, rerender } = renderHook(({ snapshot }) => useEditor(api, snapshot, async () => snapshot, true, ""),
      { initialProps: { snapshot: visible } });
    act(() => result.current.adoptHistory({ buddies: [buddy("retired")] }, historySourceKey(visible)));
    expect(result.current.view.buddies.map(row => row.buddyId)).toEqual(["retired"]);
    rerender({ snapshot: { ...visible, eventCursor: 9 } });
    expect(result.current.view.buddies).toEqual([]);
  });

  it("reads one first page for a stable content/filter generation", async () => {
    const { api, command } = fakeApi(() => page(["retired"]));
    const { result, rerender } = renderHook(({ enabled }) => useBuddyHistory(api, "csrf", "stable",
      { query: "", harness: "", enabled, autoOpen: true }), { initialProps: { enabled: false } });
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.page?.buddies).toHaveLength(1));
    rerender({ enabled: false }); rerender({ enabled: true });
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).toHaveBeenCalledTimes(1);
  });

  it("does not loop on a failed restored-history read", async () => {
    const { api, command } = fakeApi(() => { throw new Error("unavailable"); });
    const { result, rerender } = renderHook(({ enabled }) => useBuddyHistory(api, "csrf", "stable",
      { query: "", harness: "", enabled, autoOpen: true }), { initialProps: { enabled: false } });
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.error).toBe("unavailable"));
    rerender({ enabled: false }); rerender({ enabled: true });
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).toHaveBeenCalledTimes(1);
  });

  it("restarts at the first page when the filter changes", async () => {
    const { api, command } = fakeApi(params => page([String(params.query)]));
    const { result, rerender } = renderHook(({ query }) => useBuddyHistory(api, "csrf", "stable",
      { query, harness: "", enabled: true }), { initialProps: { query: "first" } });
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.buddies[0].buddyId).toBe("first"));
    rerender({ query: "second" });
    expect(result.current.page).toBeNull();
    await waitFor(() => expect(result.current.page?.buddies[0].buddyId).toBe("second"));
    expect(command.mock.calls.at(-1)?.[1]).toMatchObject({ query: "second" });
    expect(command.mock.calls.at(-1)?.[1]).not.toHaveProperty("after");
  });

  it("stops after the six-page display budget", async () => {
    let reads = 0;
    const { api, command } = fakeApi(() => {
      const index = reads++;
      return page(Array.from({ length: 100 }, (_, n) => `retained-${index * 100 + n}`), `cursor-${index + 1}`);
    });
    const { result } = renderHook(() => useBuddyHistory(api, "csrf", "stable",
      { query: "", harness: "", enabled: true }));
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.buddies).toHaveLength(100));
    for (let n = 2; n <= 6; n += 1) {
      act(() => result.current.loadMore());
      await waitFor(() => expect(result.current.page?.buddies).toHaveLength(n * 100));
    }
    expect(result.current.limitReached).toBe(true);
    expect(result.current.hasMore).toBe(false);
    act(() => result.current.loadMore());
    expect(command).toHaveBeenCalledTimes(6);
  });
});
