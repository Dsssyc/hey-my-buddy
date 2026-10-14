import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  TaskActivityView,
  activityEvidence,
  countsText,
  parseActivity,
  phaseLabels,
  recordedTermination,
  terminationText,
} from "./task-activity";
import type { Task, TaskActivity } from "./types";

function task(overrides: Partial<Task> = {}): Task {
  return {
    runId: "run-1",
    task: "实现活动观察",
    status: "running",
    owner: "host-a",
    cwd: "/repo",
    revision: 3,
    createdAt: "2026-09-25T09:00:00Z",
    acceptedAt: null,
    acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
    ...overrides,
  };
}

afterEach(cleanup);

describe("bounded activity projection", () => {
  it("does not show an earlier termination as the new attempt's outcome", () => {
    const current = task({ activeAttemptId: "new", selectedAttemptId: "old",
      selectedAttempt: { attemptId: "old", result: { terminationReason: "completed" } } });
    expect(recordedTermination(current)).toBeNull();
    expect(terminationText(current)).toBeNull();
  });
  it("reads only the frozen whitelist and keeps unknown counts unknown", () => {
    const parsed = parseActivity({
      phase: "tool-running",
      observedAt: "2026-09-25T10:00:00Z",
      eventSeq: 4,
      toolName: "apply_patch",
      counts: { toolCalls: 2 },
      prompt: "不应显示的秘密提示词",
      toolArguments: { token: "secret" },
    });
    expect(parsed).toEqual({
      phase: "tool-running",
      observedAt: "2026-09-25T10:00:00Z",
      eventSeq: 4,
      nativeSessionId: null,
      lastNativeActivityAt: null,
      lastToolActivityAt: null,
      toolName: "apply_patch",
      waitingReason: null,
      counts: { modelTurns: null, toolCalls: 2 },
    });
    const wire = JSON.stringify(parsed);
    expect(wire).not.toContain("秘密");
    expect(wire).not.toContain("secret");
    expect(wire).not.toContain("toolArguments");
  });

  it("rejects a payload that carries nothing recognizable", () => {
    expect(parseActivity(null)).toBeNull();
    expect(parseActivity("streaming-model")).toBeNull();
    expect(parseActivity({ prompt: "只写了正文" })).toBeNull();
    // A recognized phase is a recorded observation, even when it is "unknown".
    expect(parseActivity({ phase: "unknown" })).toMatchObject({ phase: "unknown" });
    // An unrecognized phase with no other evidence is unknown, never synthesized.
    expect(parseActivity({ phase: "almost-done", observedAt: "2026-09-25T10:00:00Z" })).toMatchObject({ phase: "unknown" });
  });

  it("classifies heartbeat, native and tool evidence distinctly", () => {
    expect(activityEvidence({ phase: "starting", observedAt: "2026-09-25T10:00:00Z" })).toBe("heartbeat");
    expect(activityEvidence({ phase: "streaming-model", lastNativeActivityAt: "2026-09-25T10:00:00Z" })).toBe("native");
    expect(activityEvidence({ phase: "tool-running", toolName: "bash" })).toBe("tool");
    expect(activityEvidence({ phase: "unknown" })).toBe("unknown");
    expect(countsText({ phase: "unknown", counts: { modelTurns: 3, toolCalls: null } })).toBe("模型回合 3 · 工具调用未记录");
  });

  it("labels every recorded stop outcome and never invents a cause", () => {
    expect(terminationText(task({ status: "cancelled", terminationReason: "deadline" }))).toBe("Router 预算用完");
    expect(terminationText(task({ status: "cancelled", terminationReason: "wrap-up" }))).toBe("Host 收尾完成");
    expect(terminationText(task({ status: "cancelled", terminationReason: "grace-expired" }))).toBe("收尾宽限到期后停止");
    expect(terminationText(task({ status: "cancelled", terminationReason: "forced-stop" }))).toBe("强制停止");
    expect(terminationText(task({ status: "cancelled", terminationReason: "console-stop" }))).toBe("在控制台停止");
    expect(terminationText(task({ status: "cancelled", terminationReason: "service-stop" }))).toBe("服务停止");
    expect(terminationText(task({ status: "cancelled", terminationReason: "unknown-stop" }))).toBe("停止（发起者未知）");
    expect(terminationText(task({ status: "failed", terminationReason: "harness-error" }))).toBe("Harness 错误");
    expect(terminationText(task({ status: "completed", terminationReason: "completed" }))).toBe("正常完成");
    expect(terminationText(task({ status: "cancelled" }))).toContain("未知");
    expect(terminationText(task({ status: "running" }))).toBeNull();
  });

  it("reads the durable cause from the selected attempt receipt", () => {
    // The board records the cause on the attempt result, not on the task row.
    expect(terminationText(task({ status: "cancelled",
      selectedAttempt: { result: { status: "cancelled", terminationReason: "unknown-stop" } } }))).toBe("停止（发起者未知）");
    expect(terminationText(task({ status: "failed",
      selectedAttempt: { result: { status: "error", terminationReason: "transport-error" } } }))).toBe("传输故障");
    // A completed attempt without a recorded cause never borrows the cancel wording.
    expect(terminationText(task({ status: "completed", selectedAttempt: { result: {} } }))).toBe("未记录终止原因（未知）");
    const rendered = render(<TaskActivityView task={task({ status: "cancelled",
      selectedAttempt: { result: { terminationReason: "deadline" } } })} />);
    expect(rendered.container.textContent).toContain("Router 预算用完");
    expect(rendered.container.textContent).not.toContain("用户取消");
  });

  it("uses the receipt cause without claiming the record gave no reason", () => {
    const rendered = render(<TaskActivityView task={task({ status: "failed",
      selectedAttempt: { result: { terminationReason: "transport-error" } } })} />);
    expect(rendered.container.textContent).toContain("传输故障");
    expect(rendered.container.textContent).not.toContain("原始记录未给出原因");
  });

  it("prefers the canonical receipt cause over a task-level field", () => {
    expect(recordedTermination(task({ terminationReason: "unknown-stop",
      selectedAttempt: { result: { terminationReason: "deadline" } } }))).toBe("deadline");
    expect(recordedTermination(task({ terminationReason: "deadline" }))).toBe("deadline");
    expect(recordedTermination(task({ status: "cancelled" }))).toBeNull();
  });

  it("still marks a genuinely missing cause as unknown", () => {
    const rendered = render(<TaskActivityView task={task({ status: "cancelled" })} />);
    expect(rendered.container.textContent).toContain("停止（发起者未知）");
    expect(rendered.container.textContent).not.toMatch(/终止原因：用户取消/);
  });

  it("shows the durable Host or console actor and reason without changing a completed result", () => {
    const host = render(<TaskActivityView task={task({ status: "cancelled", terminationReason: "wrap-up",
      stopInitiator: "host", cancellation: { actor: "host:codex-main", reason: "I will finish the remaining work" } })} />);
    expect(host.container.textContent).toContain("Host 停止");
    expect(host.container.textContent).toContain("发起 Hostcodex-main");
    expect(host.container.textContent).toContain("停止理由I will finish the remaining work");
    host.unmount();
    const consoleStop = render(<TaskActivityView task={task({ status: "cancelled", terminationReason: "console-stop",
      stopInitiator: "console", cancellation: { actor: "console:private-session", reason: "Stop objective" } })} />);
    expect(consoleStop.container.textContent).toContain("在控制台停止");
    expect(consoleStop.container.textContent).not.toContain("private-session");
    consoleStop.unmount();
    const serviceStop = render(<TaskActivityView task={task({ status: "cancelled", terminationReason: "service-stop",
      stopInitiator: "service", cancellation: { actor: "service-stop", reason: null } })} />);
    expect(serviceStop.container.textContent).toContain("服务停止");
    expect(serviceStop.container.textContent).not.toContain("发起者未知");
    serviceStop.unmount();
    const unknown = render(<TaskActivityView task={task({ status: "cancelled", terminationReason: "unknown-stop",
      selectedAttempt: { stopInitiator: "unknown" }, cancellation: { actor: "host:old-actor", reason: null } })} />);
    expect(unknown.container.textContent).toContain("停止（发起者未知）");
    expect(unknown.container.textContent).not.toContain("old-actor");
    unknown.unmount();
    const completed = render(<TaskActivityView task={task({ status: "completed", terminationReason: "completed",
      workflow: { state: "cancelled", awaitingHost: false, hostId: "host", ownerGeneration: 1, revision: 1 },
      cancellation: { actor: "host:codex-main", reason: "Stop goal" } })} />);
    expect(completed.container.textContent).toContain("正常完成");
    expect(completed.container.textContent).toContain("停止发起Host 停止");
  });

  it("shows a Lead stop and its bounded actor and reason from the selected attempt", () => {
    const lead = render(<TaskActivityView task={task({ status: "cancelled", terminationReason: "wrap-up",
      stopInitiator: "host", selectedAttempt: { stopInitiator: "lead" },
      cancellation: { actor: "lead:review-lead", reason: "Please summarize the current work" } })} />);
    expect(lead.container.textContent).toContain("Lead 收尾完成");
    expect(lead.container.textContent).toContain("停止发起Lead 停止");
    expect(lead.container.textContent).toContain("发起 Leadreview-lead");
    expect(lead.container.textContent).toContain("停止理由Please summarize the current work");
    expect(lead.container.textContent).not.toContain("发起 Host");
    lead.unmount();

    const mismatched = render(<TaskActivityView task={task({ status: "cancelled",
      stopInitiator: "lead", cancellation: { actor: "host:old-owner", reason: null } })} />);
    expect(mismatched.container.textContent).toContain("Lead 停止");
    expect(mismatched.container.textContent).not.toContain("old-owner");
    expect(terminationText(task({ status: "cancelled",
      cancellation: { actor: `lead:${"x".repeat(257)}`, reason: null } }))).toBe("停止（发起者未知）");
  });
});

describe("activity rendering", () => {
  it("reports a bare supervisor heartbeat without claiming native progress", () => {
    const { container } = render(<TaskActivityView task={task({
      activity: { phase: "waiting-model", observedAt: "2026-09-25T10:00:00Z", eventSeq: 7 },
    })} />);
    const view = screen.getByRole("region", { name: "执行活动（只读）" });
    expect(view.textContent).toContain(phaseLabels["waiting-model"]);
    expect(view.textContent).toContain("仅监管心跳");
    expect(view.textContent).toContain("进展未知");
    expect(view.textContent).not.toMatch(/\d+\s*%/);
  });

  it("reports a running tool and its timestamps without turning them into progress", () => {
    const activity: TaskActivity = {
      phase: "tool-running",
      observedAt: "2026-09-25T10:05:00Z",
      lastToolActivityAt: "2026-09-25T10:04:30Z",
      toolName: "apply_patch",
      counts: { modelTurns: 2, toolCalls: 5 },
    };
    render(<TaskActivityView task={task({ activity })} />);
    const view = screen.getByRole("region", { name: "执行活动（只读）" });
    expect(view.textContent).toContain("工具执行中");
    expect(view.textContent).toContain("收到工具活动");
    expect(view.textContent).toContain("apply_patch");
    expect(view.textContent).toContain("模型回合 2 · 工具调用 5");
    expect(view.textContent).toContain("工具执行中");
    expect(view.textContent).not.toMatch(/\d+\s*%/);
  });

  it("shows an explicit unknown state instead of manufacturing activity", () => {
    const explicit = render(<TaskActivityView task={task({ activity: { phase: "unknown" } })} />);
    expect(explicit.container.textContent).toContain("进展未知");
    expect(explicit.container.textContent).toContain("活动未知");
    expect(explicit.container.textContent).not.toContain("尚无活动记录");
    cleanup();
    render(<TaskActivityView task={task()} />);
    const view = screen.getByRole("region", { name: "执行活动（只读）" });
    expect(view.textContent).toContain("暂无活动记录");
    expect(view.textContent).not.toContain("停止未确认");
    expect(view.textContent).not.toMatch(/\d+\s*%/);
  });

  it("shows the recorded termination cause beside the activity", () => {
    render(<TaskActivityView task={task({ status: "cancelled", terminationReason: "deadline",
      activity: { phase: "unknown", waitingReason: "Router 预算用完" } })} />);
    const view = screen.getByRole("region", { name: "执行活动（只读）" });
    expect(view.textContent).toContain("Router 预算用完");
    expect(view.textContent).not.toContain("用户取消");
  });
});
