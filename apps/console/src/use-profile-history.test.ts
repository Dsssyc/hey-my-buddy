import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { useEditor } from "./use-editor";
import { FILTER_DEBOUNCE_MS, MAX_QUERY_CHARS, MAX_QUERY_TERMS, boundProfileQuery, historySourceKey, mergePage, useProfileHistory } from "./use-profile-history";
import type { Profile, ProfilePage, Snapshot } from "./types";

function profile(profileId: string): Profile {
  return { profileId, label: profileId, adapter: "dsh", provider: "deepseek-official", model: profileId,
    effort: "off", available: true, enabled: true, capabilities: [], contextWindow: null,
    description: "", source: "fixture" };
}
function page(ids: string[], nextCursor: string | null = null): ProfilePage {
  return { profiles: ids.map(profile), preferences: [], modelConcurrency: [], nextCursor };
}
function fakeApi(handler: (params: Record<string, unknown>) => ProfilePage | Promise<ProfilePage>) {
  const command = vi.fn(async (_operation: string, params: Record<string, unknown>) => handler(params));
  return { api: { command } as unknown as ConsoleApi, command };
}
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
afterEach(() => vi.restoreAllMocks());

describe("bounded retained model history", () => {
  it("invalidates retained history when an event changes outside the bounded snapshot", () => {
    const visible = { eventCursor: 12, profiles: [], preferences: [], preferenceOverrides: [],
      familyPreferences: [], familyAnnotations: [], modelConcurrency: [], configuration: null } as unknown as Snapshot;
    expect(historySourceKey({ ...visible, eventCursor: 13 })).not.toBe(historySourceKey(visible));
  });
  it("bounds query terms and length", () => {
    expect(boundProfileQuery("  deepseek   flash  ")).toBe("deepseek flash");
    expect(boundProfileQuery("a b c d e f g h i j")).toBe("a b c d e f g h");
    expect(boundProfileQuery("x".repeat(MAX_QUERY_CHARS + 50))).toHaveLength(MAX_QUERY_CHARS);
    expect(boundProfileQuery("a b c d e f g h i").split(" ")).toHaveLength(MAX_QUERY_TERMS);
  });

  it("appends bounded pages without duplicating identities", () => {
    const merged = mergePage(page(["a", "b"], "b"), page(["b", "c"]));
    expect(merged.profiles.map(row => row.profileId)).toEqual(["a", "b", "c"]);
    expect(merged.nextCursor).toBeNull();
  });

  it("does not read until opened and sends bounded search and cursor", async () => {
    const { api, command } = fakeApi(params => params.after ? page(["second"]) : page(["first"], "first"));
    const { result } = renderHook(() => useProfileHistory(api, "csrf", "content-a",
      { query: "  first  ", adapter: "dsh", enabled: true }));
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).not.toHaveBeenCalled();
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.profiles.map(row => row.profileId)).toEqual(["first"]));
    expect(command).toHaveBeenCalledWith("model_profiles", {
      includeUnavailable: true, limit: 100, query: "first", adapter: "dsh",
    }, "csrf");
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.page?.profiles.map(row => row.profileId)).toEqual(["first", "second"]));
    expect(command).toHaveBeenLastCalledWith("model_profiles", {
      includeUnavailable: true, limit: 100, query: "first", adapter: "dsh", after: "first",
    }, "csrf");
  });

  it("hides a page when snapshot content changes and rejects its queued reply", async () => {
    let resolveOld!: (value: ProfilePage) => void;
    const oldReply = new Promise<ProfilePage>(resolve => { resolveOld = resolve; });
    let reads = 0;
    const { api, command } = fakeApi(() => ++reads === 1 ? oldReply : page(["fresh"]));
    const { result, rerender } = renderHook(({ sourceKey }) => useProfileHistory(api, "csrf", sourceKey,
      { query: "", adapter: "", enabled: true }), { initialProps: { sourceKey: "content-a" } });
    act(() => result.current.open());
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1));
    rerender({ sourceKey: "content-b" });
    expect(result.current.page).toBeNull();
    await act(async () => resolveOld(page(["old"])));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.page?.profiles.map(row => row.profileId)).toEqual(["fresh"]));
  });

  it("rereads a retained-only family note when the board event cursor moves", async () => {
    const family = { adapter: "dsh", provider: "deepseek-official", model: "retired" };
    let note = "older note";
    const { api, command } = fakeApi(() => ({ ...page(["retired"]),
      familyAnnotations: [{ ...family, text: note, updatedAt: null }] }));
    const visible = { eventCursor: 8, profiles: [], preferences: [], preferenceOverrides: [],
      familyPreferences: [], familyAnnotations: [], modelConcurrency: [], configuration: null } as unknown as Snapshot;
    const { result, rerender } = renderHook(({ snapshot }) => useProfileHistory(api, "csrf", historySourceKey(snapshot),
      { query: "", adapter: "", enabled: true }), { initialProps: { snapshot: visible } });
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.familyAnnotations?.[0].text).toBe("older note"));
    note = "newer note";
    rerender({ snapshot: { ...visible, eventCursor: 9 } });
    expect(result.current.page).toBeNull();
    await waitFor(() => expect(result.current.page?.familyAnnotations?.[0].text).toBe("newer note"));
    expect(command).toHaveBeenCalledTimes(2);
  });

  it("does not render an old retained page during the cursor-change render", () => {
    const visible = { eventCursor: 8, csrfToken: "csrf", consoleSession: { id: "session", canWrite: true, reason: null },
      profiles: [], preferences: [], preferenceOverrides: [], familyPreferences: [], familyAnnotations: [],
      modelConcurrency: [], configuration: null, decisions: [], tasks: { pendingCount: 0 }, capabilities: {} } as Snapshot;
    const { api } = fakeApi(() => page([]));
    const { result, rerender } = renderHook(({ snapshot }) => useEditor(api, snapshot, async () => snapshot, true, ""),
      { initialProps: { snapshot: visible } });
    act(() => result.current.adoptHistory({ profiles: [profile("retired")] }, historySourceKey(visible)));
    expect(result.current.view.profiles.map(row => row.profileId)).toEqual(["retired"]);
    rerender({ snapshot: { ...visible, eventCursor: 9 } });
    expect(result.current.view.profiles).toEqual([]);
  });

  it("reads one first page for a stable content/filter generation", async () => {
    const { api, command } = fakeApi(() => page(["retired"]));
    const { result, rerender } = renderHook(({ enabled }) => useProfileHistory(api, "csrf", "stable",
      { query: "", adapter: "", enabled, autoOpen: true }), { initialProps: { enabled: false } });
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.page?.profiles).toHaveLength(1));
    rerender({ enabled: false }); rerender({ enabled: true });
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).toHaveBeenCalledTimes(1);
  });

  it("does not loop on a failed restored-history read", async () => {
    const { api, command } = fakeApi(() => { throw new Error("unavailable"); });
    const { result, rerender } = renderHook(({ enabled }) => useProfileHistory(api, "csrf", "stable",
      { query: "", adapter: "", enabled, autoOpen: true }), { initialProps: { enabled: false } });
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.error).toBe("unavailable"));
    rerender({ enabled: false }); rerender({ enabled: true });
    await sleep(FILTER_DEBOUNCE_MS + 50);
    expect(command).toHaveBeenCalledTimes(1);
  });

  it("restarts at the first page when the filter changes", async () => {
    const { api, command } = fakeApi(params => page([String(params.query)]));
    const { result, rerender } = renderHook(({ query }) => useProfileHistory(api, "csrf", "stable",
      { query, adapter: "", enabled: true }), { initialProps: { query: "first" } });
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.profiles[0].profileId).toBe("first"));
    rerender({ query: "second" });
    expect(result.current.page).toBeNull();
    await waitFor(() => expect(result.current.page?.profiles[0].profileId).toBe("second"));
    expect(command.mock.calls.at(-1)?.[1]).toMatchObject({ query: "second" });
    expect(command.mock.calls.at(-1)?.[1]).not.toHaveProperty("after");
  });

  it("stops after the six-page display budget", async () => {
    let reads = 0;
    const { api, command } = fakeApi(() => {
      const index = reads++;
      return page(Array.from({ length: 100 }, (_, n) => `retained-${index * 100 + n}`), `cursor-${index + 1}`);
    });
    const { result } = renderHook(() => useProfileHistory(api, "csrf", "stable",
      { query: "", adapter: "", enabled: true }));
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.profiles).toHaveLength(100));
    for (let n = 2; n <= 6; n += 1) {
      act(() => result.current.loadMore());
      await waitFor(() => expect(result.current.page?.profiles).toHaveLength(n * 100));
    }
    expect(result.current.limitReached).toBe(true);
    expect(result.current.hasMore).toBe(false);
    act(() => result.current.loadMore());
    expect(command).toHaveBeenCalledTimes(6);
  });
});
