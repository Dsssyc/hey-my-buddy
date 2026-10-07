import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { useConsole } from "./use-console";

const snapshot = {} as Snapshot;

/** Fakes the browser's Page Visibility; the real browser check belongs to the Host. */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { value: hidden ? "hidden" : "visible", configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  setHidden(false);
});

function harness() {
  let releaseFirst: ((value: Snapshot) => void) | undefined;
  const firstRead = new Promise<Snapshot>(resolve => { releaseFirst = resolve; });
  const snapshots = vi.fn((_signal?: AbortSignal): Promise<Snapshot> => firstRead)
    .mockImplementationOnce(() => firstRead)
    .mockImplementation(async () => snapshot);
  const api = { snapshot: snapshots } as unknown as ConsoleApi;
  const hook = renderHook(() => useConsole(api));
  return { ...hook, snapshots, releaseFirst: releaseFirst! };
}

describe("the console snapshot read", () => {
  it("reads immediately and keeps the 3-second cadence while visible", async () => {
    const f = harness();
    f.releaseFirst(snapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.snapshots).toHaveBeenCalledTimes(3);
    f.unmount();
  });

  it("stops reading while the page is hidden and reads exactly once on return", async () => {
    const f = harness();
    f.releaseFirst(snapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    act(() => setHidden(true));
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2999); });
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(f.snapshots).toHaveBeenCalledTimes(3);
    f.unmount();
  });

  it("never starts the first read while mounted hidden, then reads once on becoming visible", async () => {
    setHidden(true);
    const f = harness();
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(f.snapshots).not.toHaveBeenCalled();
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    f.releaseFirst(snapshot);
    f.unmount();
  });

  it("does not start a scheduled snapshot in the visibility-event cleanup gap", async () => {
    // The document is already hidden but the visibilitychange state update has
    // not landed (no event dispatched): the scheduled callback fires inside
    // that gap and must consult the document itself instead of trusting the
    // timer arming.
    const f = harness();
    f.releaseFirst(snapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    f.unmount();
  });

  it("does not reschedule behind a read that was in flight when the page hid", async () => {
    const f = harness();
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    // The read is still in flight when the page hides: hiding aborts its
    // controller, so its continuation must not schedule a hidden read.
    act(() => setHidden(true));
    f.releaseFirst(snapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    // One immediate read on return plus the resumed cadence at 3s and 6s —
    // exactly one chain, nothing extra from the hidden interval.
    expect(f.snapshots).toHaveBeenCalledTimes(4);
    f.unmount();
  });
});
