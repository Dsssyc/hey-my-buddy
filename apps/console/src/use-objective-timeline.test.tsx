import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type { ObjectiveTimeline } from "./objective-types";
import { objectiveTimelineFixture } from "./objective-fixtures";
import { useObjectiveTimeline } from "./use-objective-timeline";

type TimelineRead = { timeline: ObjectiveTimeline; verifiedAtMs: number | null };

function harness(initial: ObjectiveTimeline | null = objectiveTimelineFixture(), verifiedAtMs: number | null = null) {
  const reads: { resolve: (value: TimelineRead) => void; reject: (error: Error) => void }[] = [];
  const objectiveTimeline = vi.fn(() => new Promise<TimelineRead>((resolve, reject) => {
    reads.push({ resolve, reject });
  }));
  const api = { objectiveTimeline } as unknown as ConsoleApi;
  const hook = renderHook(({ objectiveId, active }: { objectiveId: string | null; active: boolean }) => useObjectiveTimeline(api, objectiveId, active),
    { initialProps: { objectiveId: (initial ? initial.objective.objectiveId : "obj-1") as string | null, active: true } });
  return { ...hook, reads, objectiveTimeline };
}

/** One queued read as a `{timeline, verifiedAtMs}` pair for the hook. */
const readWith = (timeline: ObjectiveTimeline, verifiedAtMs: number | null = null) => ({ timeline, verifiedAtMs });

afterEach(() => cleanup());

/** Fakes the browser's Page Visibility; the real browser check belongs to the Host. */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, "visibilityState", { value: hidden ? "hidden" : "visible", configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}
afterEach(() => setHidden(false));

describe("work-objective timeline hook", () => {
  it("reads the bounded timeline for the selection without marking a fresh view new", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    expect(f.objectiveTimeline).toHaveBeenCalledWith("obj-1", { limit: 200 }, expect.any(AbortSignal));
    expect(f.result.current.timeline?.rows.map(row => row.runId)).toEqual(["r1", "r2", "r3", "r5", "r4", "r6"]);
    expect(f.result.current.loading).toBe(false);
    expect([...f.result.current.newRunIds]).toEqual([]);
  });

  it("appends refreshed delegations at the end and labels only them as new", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    const refresh = objectiveTimelineFixture();
    refresh.rows = [...refresh.rows.map(row => ({ ...row, title: `${row.title}（更新）` }))];
    const appendedRow = { ...refresh.rows[0]!, runId: "r9", title: "后来追加的委派" };
    refresh.rows = [...fixture.rows.map(row => ({ ...row, title: `${row.title}（更新）` })), appendedRow];
    await act(async () => { void f.result.current.retry(); await f.reads[1]!.resolve(readWith(refresh)); });
    const rows = f.result.current.timeline?.rows.map(row => row.runId);
    expect(rows).toEqual(["r1", "r2", "r3", "r5", "r4", "r6", "r9"]);
    expect(f.result.current.timeline?.rows[0]!.title).toContain("（更新）");
    expect([...f.result.current.newRunIds]).toEqual(["r9"]);
    expect(f.result.current.error).toBe("");
  });

  it("refuses a reply whose objective identity does not match the request", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    const mismatch = objectiveTimelineFixture();
    mismatch.objective = { ...mismatch.objective, objectiveId: "obj-other" };
    await act(async () => { void f.result.current.retry(); await f.reads[1]!.resolve(readWith(mismatch)); });
    expect(f.result.current.error).toContain("标识与请求不符");
    // The last accepted data is retained; no partial rows leak in.
    expect(f.result.current.timeline?.objective.objectiveId).toBe("obj-1");
    expect(f.result.current.timeline?.rows.map(row => row.runId)).not.toContain("r9");
  });

  it("keeps the last good data with its observation instant when a read fails", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    await act(async () => { void f.result.current.retry(); await f.reads[1]!.reject(new Error("黑板暂时没有响应")); });
    expect(f.result.current.error).toBe("黑板暂时没有响应");
    expect(f.result.current.stale).toBe(true);
    expect(f.result.current.observedAt).toBe(fixture.observedAt);
    expect(f.result.current.timeline?.rows).toHaveLength(fixture.rows.length);
    act(() => { void f.result.current.retry(); });
    await act(async () => f.reads[2]!.resolve(readWith(fixture)));
    expect(f.result.current.error).toBe("");
    expect(f.result.current.stale).toBe(false);
  });

  it("clears data on deselection and stops reading while inactive", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    f.rerender({ objectiveId: null, active: true });
    await waitFor(() => expect(f.result.current.timeline).toBeNull());
    f.rerender({ objectiveId: "obj-1", active: false });
    expect(f.result.current.timeline).toBeNull();
    f.unmount();
  });

  it("stops the schedule while the page is hidden and re-reads the kept timeline once on return", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    expect(f.objectiveTimeline).toHaveBeenCalledTimes(1);
    act(() => setHidden(true));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 3600)); });
    expect(f.objectiveTimeline).toHaveBeenCalledTimes(1);
    expect(f.result.current.timeline).not.toBeNull();
    act(() => setHidden(false));
    await act(async () => f.reads[1]!.resolve(readWith(fixture)));
    // The return read keeps the displayed rows; the cadence resumes after it.
    expect(f.objectiveTimeline).toHaveBeenCalledTimes(2);
    expect(f.result.current.timeline?.objective.objectiveId).toBe(fixture.objective.objectiveId);
    f.unmount();
  });

  it("clears a selection changed while hidden; a failed return read never shows the previous objective", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(readWith(fixture)));
    expect(f.result.current.timeline?.objective.objectiveId).toBe(fixture.objective.objectiveId);
    act(() => setHidden(true));
    f.rerender({ objectiveId: "obj-2", active: true });
    // The previous objective's data must vacate while hidden — a slow or
    // failed read of the new selection can never leave it standing.
    expect(f.result.current.timeline).toBeNull();
    act(() => setHidden(false));
    await act(async () => f.reads[1]!.reject(new Error("timeline read failed")));
    expect(f.result.current.timeline).toBeNull();
    expect(f.result.current.error).toContain("timeline read failed");
    act(() => { void f.result.current.retry(); });
    await act(async () => f.reads[2]!.resolve(readWith({ ...fixture, objective: { ...fixture.objective, objectiveId: "obj-2" } })));
    expect(f.result.current.timeline?.objective.objectiveId).toBe("obj-2");
    f.unmount();
  });

  it("advances the display clock on a successful unchanged read and freezes it on a failed poll", async () => {
    // Without a response date the hook falls back to the controlled client
    // clock at read completion (justified: the console is a local surface).
    // An unchanged (304-shaped) read names a fresh verification instant, so
    // the display clock advances; a failed read freezes it, because the
    // displayed data really is that old.
    vi.useFakeTimers();
    try {
      const fixture = objectiveTimelineFixture();
      const f = harness(fixture);
      await act(async () => f.reads[0]!.resolve(readWith(fixture)));
      const first = f.result.current.displayObservedAt;
      expect(first).not.toBeNull();
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      // The unchanged read: same body — the display clock nevertheless
      // advances by the elapsed 3 seconds of client time.
      await act(async () => f.reads[1]!.resolve(readWith(fixture)));
      const advanced = f.result.current.displayObservedAt!;
      expect(Date.parse(advanced) - Date.parse(first!)).toBe(3000);
      expect(f.result.current.timeline?.observedAt).toBe(fixture.observedAt);
      // A failed poll: the display clock freezes at the last successful
      // verification — open tails stop extending and the data age stays
      // honest.
      await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
      await act(async () => f.reads[2]!.reject(new Error("private failed read")));
      expect(f.result.current.displayObservedAt).toBe(advanced);
      expect(f.result.current.stale).toBe(true);
      f.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("anchors the display clock on the current verification time when the first successful body is an old cached snapshot", async () => {
    // Warm-cache shape: the served body was generated 15 minutes before the
    // read, and the response names the fresh verification instant. The display
    // clock starts at the verification time — never 15 minutes behind — while
    // the raw observedAt stays the old source fact.
    vi.useFakeTimers();
    try {
      const verified = Date.parse("2026-09-26T08:27:00Z");
      vi.setSystemTime(new Date(verified));
      const fixture = objectiveTimelineFixture();     // observedAt 08:12 — 15 min old
      const f = harness(fixture, verified);
      await act(async () => f.reads[0]!.resolve(readWith(fixture, verified)));
      expect(f.result.current.timeline?.observedAt).toBe(fixture.observedAt);
      expect(Date.parse(f.result.current.displayObservedAt!)).toBe(verified);
      // A later unchanged poll names a fresher verification instant: the
      // display clock follows the new verification time.
      const fresher = verified + 180_000;
      vi.setSystemTime(new Date(fresher - 3000));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      await act(async () => f.reads[1]!.resolve(readWith(fixture, fresher - 3000)));
      expect(Date.parse(f.result.current.displayObservedAt!)).toBe(fresher - 3000);
      f.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts no read for a selection mounted while the page is hidden", async () => {
    const fixture = objectiveTimelineFixture();
    act(() => setHidden(true));
    const reads: { resolve: (value: { timeline: ObjectiveTimeline; verifiedAtMs: number | null }) => void }[] = [];
    const objectiveTimeline = vi.fn(() => new Promise<{ timeline: ObjectiveTimeline; verifiedAtMs: number | null }>(resolve => { reads.push({ resolve }); }));
    const api = { objectiveTimeline } as unknown as ConsoleApi;
    const hook = renderHook(({ objectiveId, active }: { objectiveId: string | null; active: boolean }) =>
      useObjectiveTimeline(api, objectiveId, active), { initialProps: { objectiveId: fixture.objective.objectiveId, active: true } });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 3600)); });
    expect(objectiveTimeline).not.toHaveBeenCalled();
    act(() => setHidden(false));
    await act(async () => reads[0]!.resolve(readWith(fixture)));
    expect(objectiveTimeline).toHaveBeenCalledTimes(1);
    hook.unmount();
  });
});
