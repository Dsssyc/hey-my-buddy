import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { client, inquiry, task, workflow } from "./task-detail.fixtures";
import type { DetailRead } from "./use-task-detail";
import { useTaskDetail } from "./use-task-detail";

let latest!: DetailRead;
function Probe({ reads, active = true, runId = "m1" }: { reads: ReturnType<typeof client>; active?: boolean; runId?: string }) {
  latest = useTaskDetail(reads, runId, active);
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
    expect(latest.task?.runId).toBe("m1");
    expect(latest.workflow?.revision).toBe(9);
    expect(latest.audit?.turns).toHaveLength(2);
    expect(latest.routing).toHaveLength(1);
    expect(latest.inquiries).toEqual({ items: [], more: false });
    expect(latest.verifiedAtMs).not.toBeNull();
    expect(latest.error).toBe("");
    expect([calls(reads.task), calls(reads.audit), calls(reads.inquiries)]).toEqual([1, 1, 1]);
    expect(Object.keys(reads).filter(key => key !== "held").sort()).toEqual(["audit", "inquiries", "task", "workflow"]);
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
    const running = client({ task: task({ status: "running", activeAttemptId: "att-2" }), inquiries: { items: [inquiry()], more: false } });
    render(<Probe reads={running} />);
    await settle();
    const taskReads = calls(running.task), inquiryReads = calls(running.inquiries);
    await settle(3000);
    await settle(3000);
    expect(calls(running.task)).toBe(taskReads + 2);
    expect(calls(running.inquiries)).toBe(inquiryReads + 2);
    // When it stops running, the beat of its inquiries stops with it, after one last read.
    running.held.task = task({ status: "completed", activeAttemptId: null });
    await settle(3000);
    const after = calls(running.inquiries);
    expect(after).toBeGreaterThan(inquiryReads + 2);
    await settle(9000);
    expect(calls(running.inquiries)).toBe(after);
    expect(calls(running.task)).toBe(taskReads + 3);
  });

  it("reads no collaboration record for a run that has none", async () => {
    const reads = client({ task: task({ workflow: undefined, lineage: { ...task().lineage, kind: "execution" } }) });
    render(<Probe reads={reads} />);
    await settle(6000);
    expect(latest.task?.runId).toBe("m1");
    expect([calls(reads.workflow), calls(reads.audit), calls(reads.inquiries)]).toEqual([0, 0, 0]);
  });

  it("keeps what it read when a read fails, says which failed, and clears it when the read works again", async () => {
    const reads = client();
    render(<Probe reads={reads} />);
    await settle();
    (reads.workflow as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new ApiError("NETWORK", "无法连接本地黑板"));
    await settle(3000);
    expect(latest.error).toContain("无法连接本地黑板");
    expect(latest.workflow?.revision).toBe(9);
    expect(latest.task?.runId).toBe("m1");
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
