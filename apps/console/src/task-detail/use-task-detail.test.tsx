import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { parseTask } from "./parse";
import { client, inquiry, routing, task, workflow } from "./task-detail.fixtures";
import type { DetailRead } from "./use-task-detail";
import { useTaskDetail } from "./use-task-detail";

let latest!: DetailRead;
function Probe({ reads, active = true, runId = "m1", decisionIds = [] }: { reads: ReturnType<typeof client>; active?: boolean; runId?: string; decisionIds?: string[] }) {
  latest = useTaskDetail(reads, runId, active, decisionIds);
  return null;
}
const calls = (fn: unknown) => (fn as ReturnType<typeof vi.fn>).mock.calls.length;
const settle = async (ms = 0) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

function visibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); visibility("visible"); });

describe("what the detail of a micro task reads", () => {
  it("reads the task, the record, the audit and the inquiries once when it opens, and only reads", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    expect(latest.view?.task.runId).toBe("m1");
    expect(latest.workflow?.revision).toBe(9);
    expect(latest.audit?.turns).toHaveLength(2);
    expect(latest.routing).toHaveLength(1);
    expect(latest.inquiries).toEqual({ items: [], more: false });
    expect(latest.verifiedAtMs).not.toBeNull();
    expect(latest.error).toBe("");
    expect([calls(reads.task), calls(reads.audit), calls(reads.inquiries)]).toEqual([1, 1, 1]);
    expect(Object.keys(reads).filter(key => key !== "held").sort()).toEqual(["audit", "basis", "inquiries", "task", "workflow"]);
  });

  it("reads the record every three seconds, and the audit again only when the record's revision moves", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    const first = calls(reads.workflow);
    await settle(3000);
    await settle(3000);
    expect(calls(reads.workflow)).toBe(first + 2);
    expect(calls(reads.audit)).toBe(1);
    reads.held.workflow = workflow({ revision: 10 });
    await settle(3000);
    expect(latest.workflow?.revision).toBe(10);
    expect(calls(reads.audit)).toBe(2);
    // The task moves with the record: it is read again when the record moved.
    expect(calls(reads.task)).toBe(2);
  });

  it("reads a micro task that is not running once, for its inquiries, and a running one on the same beat as its record", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle(9000);
    expect(calls(reads.inquiries)).toBe(1);
    cleanup();
    const running = client({ task: task({ state: "running", activeAttemptId: "att-2" }), inquiries: { items: [inquiry()], more: false } });
    render(<Probe reads={running} />);
    await settle();
    const taskReads = calls(running.task), inquiryReads = calls(running.inquiries);
    await settle(3000);
    await settle(3000);
    expect(calls(running.task)).toBe(taskReads + 2);
    expect(calls(running.inquiries)).toBe(inquiryReads + 2);
    // When it stops running, the beat of its inquiries stops with it, after one last read.
    running.held.task = task({ state: "completed", activeAttemptId: null });
    await settle(3000);
    const after = calls(running.inquiries);
    expect(after).toBeGreaterThan(inquiryReads + 2);
    await settle(9000);
    expect(calls(running.inquiries)).toBe(after);
    expect(calls(running.task)).toBe(taskReads + 3);
  });

  it("keeps what it read when a read fails, says which failed, and clears it when the read works again", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    (reads.workflow as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new ApiError("NETWORK", "无法连接本地黑板"));
    await settle(3000);
    expect(latest.error).toContain("无法连接本地黑板");
    expect(latest.workflow?.revision).toBe(9);
    expect(latest.view?.task.runId).toBe("m1");
    await settle(3000);
    expect(latest.error).toBe("");
  });

  it("reads again on demand, everything at once", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    const before = [calls(reads.task), calls(reads.workflow), calls(reads.audit), calls(reads.inquiries)];
    await act(async () => { latest.refresh(); });
    await settle();
    expect([calls(reads.task), calls(reads.workflow), calls(reads.audit), calls(reads.inquiries)].map((count, index) => count - before[index]!)).toEqual([1, 1, 1, 1]);
  });

  it("stops while the page is hidden, and reads at once on its return", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    visibility("hidden");
    await settle();
    const hidden = calls(reads.workflow);
    await settle(9000);
    expect(calls(reads.workflow)).toBe(hidden);
    visibility("visible");
    await settle();
    expect(calls(reads.workflow)).toBe(hidden + 1);
  });

  it("reads nothing while the page is another tab", async () => {
    const reads = client();
    render(<Probe reads={reads} active={false} />);
    await settle(9000);
    expect([calls(reads.task), calls(reads.workflow)]).toEqual([0, 0]);
  });

  it("lets an older reply that arrives late replace nothing the newer one wrote", async () => {
    const reads = client();
    let release!: () => void;
    (reads.workflow as ReturnType<typeof vi.fn>).mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ value: workflow({ revision: 3 }), verifiedAtMs: 1 }); }));
    render(<Probe reads={reads} />);
    await settle();
    await act(async () => { latest.refresh(); });
    await settle();
    expect(latest.workflow?.revision).toBe(9);
    await act(async () => { release(); });
    await settle();
    expect(latest.workflow?.revision).toBe(9);
  });
});

describe("when the detail is left", () => {
  it("aborts the read of the task that is under way, and ignores its late reply", async () => {
    const reads = client();
    let signal!: AbortSignal;
    (reads.task as ReturnType<typeof vi.fn>).mockImplementationOnce((_runId: string, given: AbortSignal) => { signal = given; return new Promise(() => undefined); });
    const view = render(<Probe reads={reads} />);
    await settle();
    expect(signal.aborted).toBe(false);
    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});

describe("what the head rests on", () => {
  it("says when the server checked the read the head rests on, per read: a read that carries no time carries none", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    expect(latest.verifiedAtMs).not.toBeNull();
    (reads.workflow as ReturnType<typeof vi.fn>).mockImplementation(async () => ({ value: workflow(), verifiedAtMs: null }));
    await settle(3000);
    expect(latest.verifiedAtMs).toBeNull();
    // The time of another read is not lent to it.
    expect(calls(reads.task)).toBeGreaterThan(0);
  });

});

describe("what moves the reads", () => {
  it("asks the audit for the routing decisions of the timeline, and reads it again when they change", async () => {
    const reads = client();
    const view = render(<Probe reads={reads} decisionIds={["d1"]} />);
    await settle();
    expect(reads.audit).toHaveBeenLastCalledWith("m1", ["d1"]);
    view.rerender(<Probe reads={reads} decisionIds={["d1", "d2"]} />);
    await settle();
    expect(reads.audit).toHaveBeenLastCalledWith("m1", ["d1", "d2"]);
    expect(calls(reads.audit)).toBe(2);
  });

  it("reads the bounds of each decision the program made, once, and none of a Router's", async () => {
    const bases = { "decision-1": { constraints: {}, requiredCapabilities: [], candidateCount: 1, excluded: [], coolingCount: null, cooling: [], preferences: [] } };
    const reads = client({ routing: [routing({ source: "single-candidate" }), routing({ decisionId: "d2", source: "model-selection" })], bases });
    render(<Probe reads={reads} />);
    await settle();
    await settle(6000);
    expect(reads.basis).toHaveBeenCalledExactlyOnceWith("decision-1");
    expect(latest.bases).toEqual(bases);
  });

  it("shows a read under way, and starts no second while it lasts", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    expect(latest.busy).toBe(false);
    let release!: () => void;
    (reads.task as ReturnType<typeof vi.fn>).mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ value: parseTask(task(), "m1"), verifiedAtMs: 1 }); }));
    const before = calls(reads.workflow);
    await act(async () => { latest.refresh(); });
    expect(latest.busy).toBe(true);
    await act(async () => { latest.refresh(); });
    await settle();
    expect(calls(reads.workflow)).toBe(before + 1);
    await act(async () => { release(); });
    await settle();
    expect(latest.busy).toBe(false);
  });
});
