import { fixtureCoverage } from "./test-fixtures";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSession } from "./use-session";

const page = (before: number, count: number) => ({ state: "running", stopped: null, coverage: fixtureCoverage, before, after: before + count, hasEarlier: before > 10, events: Array.from({ length: count }, (_, index) => ({ sessionId: "attempt", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: `${before + index}` } } })) });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
async function flush() { await act(async () => { await Promise.resolve(); }); }

describe("a visible session's read lifetime", () => {
  it("reads the latest page first and drains every full after page without waiting another interval", async () => {
    vi.useFakeTimers();
    const failure = { code: "session-record-write-failed", params: { category: "write", message: "disk full" } };
    const session = vi.fn().mockResolvedValueOnce(page(10, 256)).mockResolvedValueOnce({ ...page(266, 1), state: "ended", stopped: failure });
    const client = { session };
    const { result, unmount } = renderHook(() => useSession(client, "task", "attempt", true));
    await flush();
    expect(session.mock.calls[0][2]).toEqual({ limit: 256 });
    expect(result.current.data?.events).toHaveLength(256);
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(session.mock.calls.map(call => call[2])).toEqual([{ limit: 256 }, { after: 266, limit: 256 }]);
    expect(result.current.data?.events).toHaveLength(257);
    expect(result.current.data?.stopped).toEqual(failure);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(session).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("backs off between empty forward pages and responds quickly after an event", async () => {
    vi.useFakeTimers();
    const session = vi.fn()
      .mockResolvedValueOnce(page(10, 1))
      .mockResolvedValueOnce(page(11, 0))
      .mockResolvedValueOnce(page(11, 0))
      .mockResolvedValueOnce(page(11, 0))
      .mockResolvedValueOnce(page(11, 1))
      .mockResolvedValue({ ...page(12, 0), state: "ended" });
    const client = { session };
    renderHook(() => useSession(client, "task", "attempt", true));
    await flush();
    for (const [index, interval] of [250, 500, 750, 1000, 250].entries()) {
      await act(async () => { await vi.advanceTimersByTimeAsync(interval - 1); });
      expect(session).toHaveBeenCalledTimes(index + 1);
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(session).toHaveBeenCalledTimes(index + 2);
    }
  });

  it("automatically joins one earlier page when the initial page starts mid-call", async () => {
    vi.useFakeTimers();
    const tool = (sessionUpdate: string) => ({ sessionId: "attempt", update: { sessionUpdate, toolCallId: "call" } });
    const session = vi.fn().mockResolvedValueOnce({ ...page(14, 1), state: "ended", events: [tool("tool_call_update")] })
      .mockResolvedValueOnce({ ...page(13, 1), events: [tool("tool_call")] });
    const client = { session };
    const { result } = renderHook(() => useSession(client, "task", "attempt", true));
    await flush();
    expect(session.mock.calls.map(call => call[2])).toEqual([{ limit: 256 }, { before: 14, limit: 256 }]);
    expect(result.current.data?.events.map(entry => entry.event.update.sessionUpdate)).toEqual(["tool_call", "tool_call_update"]);
    expect(result.current.data).toMatchObject({ state: "ended", hasEarlier: true, before: 13, after: 15 });
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(session).toHaveBeenCalledTimes(2);
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
