import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSession } from "./use-session";

const page = (before: number, count: number) => ({ state: "running", omitted: [], before, after: before + count, hasEarlier: before > 10, events: Array.from({ length: count }, (_, index) => ({ sessionId: "attempt", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: `${before + index}` } } })) });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
async function flush() { await act(async () => { await Promise.resolve(); }); }

describe("a visible session's read lifetime", () => {
  it("reads the latest page first and drains every full after page without waiting another interval", async () => {
    vi.useFakeTimers();
    const session = vi.fn().mockResolvedValueOnce(page(10, 100)).mockResolvedValueOnce(page(110, 100)).mockResolvedValueOnce(page(210, 1));
    const client = { session };
    const { result, unmount } = renderHook(() => useSession(client, "task", "attempt", true));
    await flush();
    expect(session.mock.calls[0][2]).toEqual({ limit: 100 });
    expect(result.current.data?.events).toHaveLength(100);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(session.mock.calls.map(call => call[2])).toEqual([{ limit: 100 }, { after: 110, limit: 100 }, { after: 210, limit: 100 }]);
    expect(result.current.data?.events).toHaveLength(201);
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(session).toHaveBeenCalledTimes(3);
  });

  it("aborts hidden and switched attempts, and never applies a late reply to the next visible panel", async () => {
    vi.useFakeTimers();
    let visibility: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    const signals: AbortSignal[] = [];
    const replies: ((value: unknown) => void)[] = [];
    const client = { session: vi.fn((_task: string, _attempt: string, _params: unknown, signal: AbortSignal) => {
      signals.push(signal);
      return new Promise(resolve => replies.push(resolve));
    }) };
    const view = renderHook(({ attempt }) => useSession(client, "task", attempt, true), { initialProps: { attempt: "first" } });
    await act(async () => { visibility = "hidden"; document.dispatchEvent(new Event("visibilitychange")); });
    expect(signals.at(-1)?.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(client.session).toHaveBeenCalledTimes(1);
    await act(async () => { visibility = "visible"; document.dispatchEvent(new Event("visibilitychange")); });
    expect(client.session).toHaveBeenCalledTimes(2);
    view.rerender({ attempt: "second" });
    expect(signals.at(-2)?.aborted).toBe(true);
    expect(client.session.mock.calls.at(-1)?.[1]).toBe("second");
    await act(async () => { replies[0](page(10, 50)); replies[1](page(10, 50)); });
    expect(view.result.current.data).toBeNull();
    await act(async () => { replies[2](page(10, 1)); });
    expect(view.result.current.data?.events).toHaveLength(1);
  });
});
