import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import type { ObjectiveTimeline } from "./objective-types";
import { objectiveTimelineFixture } from "./objective-fixtures";
import { useObjectiveTimeline } from "./use-objective-timeline";

function harness(initial: ObjectiveTimeline | null = objectiveTimelineFixture()) {
  const reads: { resolve: (value: ObjectiveTimeline) => void; reject: (error: Error) => void }[] = [];
  const objectiveTimeline = vi.fn(() => new Promise<ObjectiveTimeline>((resolve, reject) => {
    reads.push({ resolve, reject });
  }));
  const api = { objectiveTimeline } as unknown as ConsoleApi;
  const hook = renderHook(({ objectiveId, active }: { objectiveId: string | null; active: boolean }) => useObjectiveTimeline(api, objectiveId, active),
    { initialProps: { objectiveId: (initial ? initial.objective.objectiveId : "obj-1") as string | null, active: true } });
  return { ...hook, reads, objectiveTimeline };
}

afterEach(() => cleanup());

describe("work-objective timeline hook", () => {
  it("reads the bounded timeline for the selection without marking a fresh view new", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(fixture));
    expect(f.objectiveTimeline).toHaveBeenCalledWith("obj-1", { limit: 200 }, expect.any(AbortSignal));
    expect(f.result.current.timeline?.rows.map(row => row.runId)).toEqual(["r1", "r2", "r3", "r5", "r4", "r6"]);
    expect(f.result.current.loading).toBe(false);
    expect([...f.result.current.newRunIds]).toEqual([]);
  });

  it("appends refreshed delegations at the end and labels only them as new", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(fixture));
    const refresh = objectiveTimelineFixture();
    refresh.rows = [...refresh.rows.map(row => ({ ...row, title: `${row.title}（更新）` }))];
    const appendedRow = { ...refresh.rows[0]!, runId: "r9", title: "后来追加的委派" };
    refresh.rows = [...fixture.rows.map(row => ({ ...row, title: `${row.title}（更新）` })), appendedRow];
    await act(async () => { void f.result.current.retry(); await f.reads[1]!.resolve(refresh); });
    const rows = f.result.current.timeline?.rows.map(row => row.runId);
    expect(rows).toEqual(["r1", "r2", "r3", "r5", "r4", "r6", "r9"]);
    expect(f.result.current.timeline?.rows[0]!.title).toContain("（更新）");
    expect([...f.result.current.newRunIds]).toEqual(["r9"]);
    expect(f.result.current.error).toBe("");
  });

  it("refuses a reply whose objective identity does not match the request", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(fixture));
    const mismatch = objectiveTimelineFixture();
    mismatch.objective = { ...mismatch.objective, objectiveId: "obj-other" };
    await act(async () => { void f.result.current.retry(); await f.reads[1]!.resolve(mismatch); });
    expect(f.result.current.error).toContain("标识与请求不符");
    // The last accepted data is retained; no partial rows leak in.
    expect(f.result.current.timeline?.objective.objectiveId).toBe("obj-1");
    expect(f.result.current.timeline?.rows.map(row => row.runId)).not.toContain("r9");
  });

  it("keeps the last good data with its observation instant when a read fails", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(fixture));
    await act(async () => { void f.result.current.retry(); await f.reads[1]!.reject(new Error("黑板暂时没有响应")); });
    expect(f.result.current.error).toBe("黑板暂时没有响应");
    expect(f.result.current.stale).toBe(true);
    expect(f.result.current.observedAt).toBe(fixture.observedAt);
    expect(f.result.current.timeline?.rows).toHaveLength(fixture.rows.length);
    act(() => { void f.result.current.retry(); });
    await act(async () => f.reads[2]!.resolve(fixture));
    expect(f.result.current.error).toBe("");
    expect(f.result.current.stale).toBe(false);
  });

  it("clears data on deselection and stops reading while inactive", async () => {
    const fixture = objectiveTimelineFixture();
    const f = harness(fixture);
    await act(async () => f.reads[0]!.resolve(fixture));
    f.rerender({ objectiveId: null, active: true });
    await waitFor(() => expect(f.result.current.timeline).toBeNull());
    f.rerender({ objectiveId: "obj-1", active: false });
    expect(f.result.current.timeline).toBeNull();
    f.unmount();
  });
});
