import { StrictMode } from "react";
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

function harness(strict = false) {
  let releaseFirst: ((value: Snapshot) => void) | undefined;
  const firstRead = new Promise<Snapshot>(resolve => { releaseFirst = resolve; });
  const snapshots = vi.fn((_signal?: AbortSignal): Promise<Snapshot> => firstRead)
    .mockImplementationOnce(() => firstRead)
    .mockImplementation(async () => snapshot);
  const api = { snapshot: snapshots } as unknown as ConsoleApi;
  const hook = renderHook(() => useConsole(api), strict ? { wrapper: StrictMode } : undefined);
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

  // D2-O3: the former hidden-mount assertion now requires the bootstrap read.
  it("reads the first snapshot while mounted hidden, then reads once on becoming visible", async () => {
    setHidden(true);
    const f = harness();
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    expect(f.snapshots.mock.calls[0][0]).toBeInstanceOf(AbortSignal);
    expect(f.snapshots.mock.calls[0][0]!.aborted).toBe(false);
    f.releaseFirst(snapshot);
    await act(async () => {});
    expect(f.result.current.snapshot).toBe(snapshot);
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    expect(f.snapshots.mock.calls[0][0]!.aborted).toBe(true);
    expect(f.snapshots.mock.calls[1][0]).not.toBe(f.snapshots.mock.calls[0][0]);
    expect(f.snapshots.mock.calls[1][0]!.aborted).toBe(false);
    // Repeating the foreground event cannot create another immediate read.
    act(() => setHidden(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(2999); });
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(f.snapshots).toHaveBeenCalledTimes(3);
    f.unmount();
    expect(f.snapshots.mock.calls[1][0]!.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
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
    expect(vi.getTimerCount()).toBe(0);
    f.unmount();
  });

  it("does not reschedule behind a read that was in flight when the page hid", async () => {
    const f = harness();
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    // The read is still in flight when the page hides: hiding aborts its
    // controller, so its continuation must not schedule a hidden read.
    act(() => setHidden(true));
    expect(f.snapshots.mock.calls[0][0]!.aborted).toBe(true);
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

  // D2-N1: even an API that ignores AbortSignal cannot publish after unmount.
  it.each([false, true])("aborts the pending bootstrap and ignores its reply after unmount (hidden=%s)", async hidden => {
    setHidden(hidden);
    const f = harness();
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    const signal = f.snapshots.mock.calls[0][0]!;
    expect(signal.aborted).toBe(false);
    f.unmount();
    expect(signal.aborted).toBe(true);
    f.releaseFirst(snapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(f.result.current.snapshot).toBeNull();
    expect(f.result.current.updatedAt).toBeNull();
    expect(f.snapshots).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  // D2-N2: cleanup invalidates the sequence even when hidden effects stay mounted.
  it("ignores an aborted reply while hidden and a superseded reply after foreground refresh", async () => {
    const f = harness();
    act(() => setHidden(true));
    expect(f.snapshots.mock.calls[0][0]!.aborted).toBe(true);
    f.releaseFirst({ stale: true } as unknown as Snapshot);
    await act(async () => {});
    expect(f.result.current.snapshot).toBeNull();
    expect(f.result.current.updatedAt).toBeNull();
    act(() => setHidden(false));
    await act(async () => {});
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    expect(f.result.current.snapshot).toBe(snapshot);
    f.unmount();

    setHidden(true);
    const next = harness();
    act(() => setHidden(false));
    await act(async () => {});
    expect(next.snapshots).toHaveBeenCalledTimes(2);
    expect(next.result.current.snapshot).toBe(snapshot);
    const updatedAt = next.result.current.updatedAt;
    next.releaseFirst({ stale: true } as unknown as Snapshot);
    await act(async () => {});
    expect(next.result.current.snapshot).toBe(snapshot);
    expect(next.result.current.updatedAt).toBe(updatedAt);
    next.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  // D2-N3: StrictMode aborts its discarded read and owns exactly one live chain.
  it.each([false, true])("keeps StrictMode controllers and polling isolated (hidden=%s)", async hidden => {
    setHidden(hidden);
    const f = harness(true);
    expect(f.snapshots).toHaveBeenCalledTimes(2);
    const discarded = f.snapshots.mock.calls[0][0]!;
    const current = f.snapshots.mock.calls[1][0]!;
    expect(discarded).not.toBe(current);
    expect(discarded.aborted).toBe(true);
    expect(current.aborted).toBe(false);
    await act(async () => {});
    expect(f.result.current.snapshot).toBe(snapshot);
    f.releaseFirst({ stale: true } as unknown as Snapshot);
    await act(async () => {});
    expect(f.result.current.snapshot).toBe(snapshot);
    expect(vi.getTimerCount()).toBe(hidden ? 0 : 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(f.snapshots).toHaveBeenCalledTimes(hidden ? 2 : 5);
    if (hidden) {
      act(() => setHidden(false));
      await act(async () => {});
      expect(f.snapshots).toHaveBeenCalledTimes(3);
      expect(current.aborted).toBe(true);
      act(() => setHidden(false));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(f.snapshots).toHaveBeenCalledTimes(4);
    }
    f.unmount();
    expect(f.snapshots.mock.calls.at(-1)![0]!.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  // D2-N4: explicit refreshes keep the existing sequence and error fencing.
  it("ignores stale failures and abort errors but reports the current hidden bootstrap failure", async () => {
    setHidden(true);
    const reads: { signal?: AbortSignal; resolve: (value: Snapshot) => void; reject: (reason: Error) => void }[] = [];
    const api = { snapshot: vi.fn((signal?: AbortSignal) => new Promise<Snapshot>((resolve, reject) => {
      reads.push({ signal, resolve, reject });
    })) } as unknown as ConsoleApi;
    const f = renderHook(() => useConsole(api));
    await act(async () => { reads[0].reject(new Error("bootstrap failed")); });
    expect(f.result.current.error).toBe("bootstrap failed");
    expect(vi.getTimerCount()).toBe(0);
    let older!: Promise<Snapshot | null>, newer!: Promise<Snapshot | null>;
    act(() => { older = f.result.current.refresh(); newer = f.result.current.refresh(); });
    await act(async () => { reads[2].resolve(snapshot); await newer; });
    expect(f.result.current.snapshot).toBe(snapshot);
    expect(f.result.current.error).toBe("");
    await act(async () => { reads[1].reject(new Error("stale failure")); await older; });
    expect(f.result.current.error).toBe("");
    let aborted!: Promise<Snapshot | null>;
    act(() => { aborted = f.result.current.refresh(undefined, true); });
    await act(async () => { reads[3].reject(new DOMException("cancelled", "AbortError")); expect(await aborted).toBeNull(); });
    expect(f.result.current.error).toBe("");
    expect(api.snapshot).toHaveBeenCalledTimes(4);
    expect(reads[0].signal).toBeInstanceOf(AbortSignal);
    expect(reads.slice(1).every(read => read.signal === undefined)).toBe(true);
    f.unmount();
    expect(reads[0].signal!.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
});
