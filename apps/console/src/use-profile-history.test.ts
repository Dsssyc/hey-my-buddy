import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import {
  FILTER_DEBOUNCE_MS,
  MAX_QUERY_CHARS,
  MAX_QUERY_TERMS,
  boundProfileQuery,
  mergePage,
  useProfileHistory,
} from "./use-profile-history";
import type { Profile, ProfilePage } from "./types";

function profile(profileId: string, available = true): Profile {
  return {
    profileId,
    label: profileId,
    adapter: "dsh",
    provider: "deepseek-official",
    model: profileId,
    effort: "off",
    available,
    enabled: true,
    capabilities: [],
    contextWindow: null,
    description: "",
    source: "fixture",
  };
}

function page(
  ids: string[],
  tableRevision = 2,
  nextCursor: string | null = null,
): ProfilePage {
  return {
    profiles: ids.map((id) => profile(id)),
    cards: [],
    preferences: [],
    sampleCounts: {},
    modelConcurrency: [],
    tableRevision,
    nextCursor,
  };
}

function fakeApi(handler: (params: Record<string, unknown>) => ProfilePage | Promise<ProfilePage>) {
  const command = vi.fn(async (_operation: string, params: Record<string, unknown>) => handler(params));
  return {
    api: { command, snapshot: vi.fn(), task: vi.fn(), tasks: vi.fn() } as unknown as ConsoleApi,
    command,
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("bounded server search", () => {
  it("keeps at most eight terms and two hundred characters", () => {
    expect(boundProfileQuery("  deepseek   flash  ")).toBe("deepseek flash");
    expect(boundProfileQuery("a b c d e f g h i j")).toBe("a b c d e f g h");
    const many = boundProfileQuery(Array.from({ length: 20 }, (_, index) => `term${index}`).join(" "));
    expect(many.split(" ")).toHaveLength(MAX_QUERY_TERMS);
    // One oversized term is truncated to the budget instead of dropping the query.
    const long = boundProfileQuery("x".repeat(MAX_QUERY_CHARS + 50));
    expect(long).toHaveLength(MAX_QUERY_CHARS);
    expect(boundProfileQuery("y".repeat(MAX_QUERY_CHARS + 50) + " tail")).toHaveLength(MAX_QUERY_CHARS);
    expect(boundProfileQuery("  ")).toBe("");
  });
});

describe("one revision per retained page set", () => {
  it("never merges or relabels pages from another tableRevision", () => {
    const current = page(["a"], 2, "b");
    const other = page(["c"], 3, null);
    const merged = mergePage(current, other);
    expect(merged.tableRevision).toBe(2);
    expect(merged.profiles.map((item) => item.profileId)).toEqual(["a"]);
    // Pages of the same revision still append in order.
    const appended = mergePage(current, page(["c"], 2, null));
    expect(appended.profiles.map((item) => item.profileId)).toEqual(["a", "c"]);
    expect(appended.nextCursor).toBeNull();
  });
});

describe("useProfileHistory", () => {
  it("fetches nothing until the retained history is opened", async () => {
    const { api, command } = fakeApi(() => page(["retired"]));
    const { result, rerender } = renderHook(
      ({ query }: { query: string }) =>
        useProfileHistory(api, "csrf", 2, { query, adapter: "", enabled: true }),
      { initialProps: { query: "" } },
    );
    rerender({ query: "retired" });
    await sleep(FILTER_DEBOUNCE_MS + 120);
    expect(command).not.toHaveBeenCalled();
    expect(result.current.page).toBeNull();
  });

  it("sends the bounded search and harness filter before pagination", async () => {
    const { api, command } = fakeApi((params) => page(["retired"], 2, null));
    const { result } = renderHook(() =>
      useProfileHistory(api, "csrf", 2, { query: "  retired   flash ", adapter: "dsh", enabled: true }),
    );
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page).not.toBeNull());
    expect(command).toHaveBeenCalledWith(
      "model_profiles",
      { includeUnavailable: true, limit: 100, query: "retired flash", adapter: "dsh" },
      "csrf",
    );
  });

  it("resets the cursor when the filter changes and never reuses the old one", async () => {
    const calls: Record<string, unknown>[] = [];
    const handler = (params: Record<string, unknown>): ProfilePage => {
      calls.push(params);
      if (params.query) return page(["matching"], 2);
      return params.after ? page(["b"], 2, null) : page(["a"], 2, "cursor-a");
    };
    const { api } = fakeApi(handler);
    const { result, rerender } = renderHook(
      ({ query }: { query: string }) =>
        useProfileHistory(api, "csrf", 2, { query, adapter: "", enabled: true }),
      { initialProps: { query: "" } },
    );
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.nextCursor).toBe("cursor-a"));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.page?.profiles).toHaveLength(2));
    expect(calls.at(-1)).toMatchObject({ after: "cursor-a", limit: 100 });

    rerender({ query: "matching" });
    // The old cursor is not exposed for the new filter.
    expect(result.current.page).toBeNull();
    await waitFor(() => expect(result.current.page?.profiles.map((p) => p.profileId)).toEqual(["matching"]));
    const filtered = calls.at(-1)!;
    expect(filtered.query).toBe("matching");
    expect(filtered.after).toBeUndefined();
  });

  it("hides an already-loaded page synchronously when the revision advances", async () => {
    const { api } = fakeApi(() => page(["old"], 2, null));
    const { result, rerender } = renderHook(
      ({ revision }: { revision: number }) =>
        useProfileHistory(api, "csrf", revision, { query: "", adapter: "", enabled: true }),
      { initialProps: { revision: 2 } },
    );
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page?.tableRevision).toBe(2));
    // The render before the reload effect must not hand a v2 page to a v3 view
    // (or to a new editor baseline); it is hidden as soon as the prop changes.
    rerender({ revision: 3 });
    expect(result.current.page).toBeNull();
    expect(result.current.hasMore).toBe(false);
  });

  it("drops a queued reply from a previous revision before it can seed the view", async () => {
    let resolveOld!: (value: ProfilePage) => void;
    const oldReply = new Promise<ProfilePage>((resolve) => { resolveOld = resolve; });
    let calls = 0;
    const { api, command } = fakeApi(() => {
      calls += 1;
      return calls === 1 ? oldReply : page(["fresh"], 3);
    });
    const { result, rerender } = renderHook(
      ({ revision }: { revision: number }) =>
        useProfileHistory(api, "csrf", revision, { query: "", adapter: "", enabled: true }),
      { initialProps: { revision: 2 } },
    );
    act(() => result.current.open());
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1));
    // The table advances while the v2 page is still in flight. The render before
    // the reload effect already stops exposing the stale page.
    rerender({ revision: 3 });
    expect(result.current.page).toBeNull();
    await act(async () => { resolveOld(page(["stale"], 2, null)); });
    expect(result.current.page).toBeNull();
    await waitFor(() => expect(command).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.page?.profiles.map((p) => p.profileId)).toEqual(["fresh"]));
    expect(result.current.page?.tableRevision).toBe(3);
  });

  it("stays at one bounded request for the first page of a stable filter", async () => {
    const { api, command } = fakeApi(() => page(["retired"], 2, null));
    const { result, rerender } = renderHook(
      ({ query }: { query: string }) =>
        useProfileHistory(api, "csrf", 2, { query, adapter: "", enabled: true }),
      { initialProps: { query: "" } },
    );
    act(() => result.current.open());
    await waitFor(() => expect(result.current.page).not.toBeNull());
    rerender({ query: "" });
    await sleep(FILTER_DEBOUNCE_MS + 120);
    act(() => result.current.open());
    await sleep(FILTER_DEBOUNCE_MS + 120);
    expect(command).toHaveBeenCalledTimes(1);
  });
});
