import { describe, expect, it } from "vitest";
import type { Task } from "../types";
import { activityOf, endingOf, finalText, remindersOf, sessionStorageOf } from "./evidence";
import { at, task } from "./task-detail.fixtures";

/** A task whose last attempt is `att-9`, with whatever the test records about it. */
const attempt = (overrides: Record<string, unknown> = {}, extra: Record<string, unknown> = {}): Task =>
  task({ activeAttemptId: null, selectedAttemptId: "att-9", status: "completed", selectedAttempt: { attemptId: "att-9", ...overrides }, ...extra });

describe("how the last attempt ended", () => {
  it("reads the cause from the receipt of the attempt, and a task-level copy only when the receipt has none", () => {
    expect(endingOf(attempt({ result: { terminationReason: "wrap-up" } }), "att-9")).toMatchObject({ cause: "wrap-up" });
    expect(endingOf(attempt({ result: { terminationReason: "deadline" } }, { terminationReason: "wrap-up" }), "att-9")).toMatchObject({ cause: "deadline" });
    expect(endingOf(attempt({ result: {} }, { terminationReason: "harness-error" }), "att-9")).toMatchObject({ cause: "harness-error" });
  });

  it("says nothing of an attempt that ended as it should", () => {
    expect(endingOf(attempt({ result: { terminationReason: "completed" } }), "att-9")).toBeNull();
    expect(endingOf(attempt({ result: {} }), "att-9")).toBeNull();
  });

  it("does not show an earlier attempt's ending as a new attempt's", () => {
    const earlier = attempt({ result: { terminationReason: "forced-stop" } });
    expect(endingOf(earlier, "att-1")).toBeNull();
    expect(endingOf(earlier, "att-9")).toMatchObject({ cause: "forced-stop" });
  });

  it("never invents a cause: a stop with none recorded names its stopper and is unknown otherwise", () => {
    const cancelled = attempt({ stopInitiator: "console" }, { status: "cancelled" });
    expect(endingOf(cancelled, "att-9")).toEqual({ cause: null, stoppedBy: "console", actor: null, reason: null, quota: null });
    expect(endingOf(attempt({}, { status: "cancelled" }), "att-9")).toMatchObject({ cause: null, stoppedBy: "unknown" });
  });

  it("names a Host's or a Lead's own cancellation by its recorded actor and reason, from the role and not from the name", () => {
    const stop = (role: string, id: string) => attempt({ result: { terminationReason: "wrap-up" } },
      { status: "cancelled", cancellation: { actor: { role, id }, reason: "改由 Host 接手" } });
    expect(endingOf(stop("host", "lead-in-name"), "att-9")).toMatchObject({ stoppedBy: "host", actor: { role: "host", id: "lead-in-name" }, reason: "改由 Host 接手" });
    expect(endingOf(stop("lead", "run-7"), "att-9")).toMatchObject({ stoppedBy: "lead", actor: { role: "lead", id: "run-7" } });
    // An attribution that is not a recognized role is no actor.
    expect(endingOf(attempt({}, { status: "cancelled", cancellation: { actor: { role: "someone", id: "x" }, reason: null } }), "att-9")).toMatchObject({ actor: null, stoppedBy: "unknown" });
  });

  it("names a native limit that ended the attempt", () => {
    expect(endingOf(attempt({ result: { terminationReason: "harness-error" } }, { status: "failed", quotaFailure: { code: "rate-limited" } }), "att-9")).toMatchObject({ quota: "rate-limited" });
    expect(endingOf(attempt({}, { status: "failed", quotaFailure: { code: "quota-exceeded" } }), "att-9")).toMatchObject({ quota: "quota-exceeded", cause: null });
  });
});

describe("the native session of an attempt", () => {
  const nativeSession = { harness: "zcode", sessionId: "sess-z", storageScope: "task-private", storageOwner: "hey-my-buddy-attempt", nativeAppVisibility: "not-listed-in-native-app", resumeMode: "native-session" };

  it("is read from the receipt of the attempt, as the harness recorded it", () => {
    const recorded = attempt({ result: { status: "ok", result: { nativeSession } } });
    expect(sessionStorageOf(recorded, "att-9")).toEqual({ scope: "task-private", owner: "hey-my-buddy-attempt", visibility: "not-listed-in-native-app" });
    const codex = attempt({ result: { result: { nativeSession: { ...nativeSession, harness: "codex", storageScope: "harness-user-store", storageOwner: "harness-user-store", nativeAppVisibility: "unknown" } } } });
    expect(sessionStorageOf(codex, "att-9")).toMatchObject({ scope: "harness-user-store", visibility: "unknown" });
  });

  it("is never attached to another attempt, and never read from any other level of the receipt", () => {
    expect(sessionStorageOf(attempt({ result: { result: { nativeSession } } }), "att-new")).toBeNull();
    expect(sessionStorageOf(attempt({ result: { nativeSession } }), "att-9")).toBeNull();
    expect(sessionStorageOf(attempt({ result: { result: { nested: { nativeSession } } } }), "att-9")).toBeNull();
    // The retired top-level storage fields of an older receipt are not session evidence.
    expect(sessionStorageOf(attempt({ result: { result: { storageScope: "task-private", sessionId: "old" } } }), "att-9")).toBeNull();
    expect(sessionStorageOf(attempt({}), "att-9")).toBeNull();
  });
});

describe("the activity of the attempt that is running", () => {
  const running = (activity: unknown) => task({ activeAttemptId: "att-9", status: "running", selectedAttempt: { attemptId: "att-9" }, activity });

  it("is the phase, the report and the counts that were recorded, with a missing count unknown", () => {
    const found = activityOf(running({ phase: "tool-running", observedAt: at("11:00"), toolName: "Bash", lastToolActivityAt: at("11:00"), counts: { toolCalls: 23 } }), "att-9");
    expect(found).toEqual({ phase: "tool-running", observedAt: at("11:00"), toolName: "Bash", modelTurns: null, toolCalls: 23, heartbeat: false });
  });

  it("reads only the whitelist of phases and calls a report of the phase alone a heartbeat, not progress", () => {
    expect(activityOf(running({ phase: "daydreaming" }), "att-9")).toMatchObject({ phase: "unknown" });
    expect(activityOf(running({ phase: "waiting-model", observedAt: at("11:00") }), "att-9")).toMatchObject({ heartbeat: true });
    expect(activityOf(running({ phase: "waiting-model", lastNativeActivityAt: at("11:00") }), "att-9")).toMatchObject({ heartbeat: false });
    expect(activityOf(running({ phase: "streaming-model", counts: { modelTurns: -1, toolCalls: 1.5 } }), "att-9")).toMatchObject({ modelTurns: null, toolCalls: null });
  });

  it("is nothing for an attempt that is not the one running, or when nothing was reported", () => {
    expect(activityOf(running({ phase: "starting" }), "att-1")).toBeNull();
    expect(activityOf(running(null), "att-9")).toBeNull();
    expect(activityOf(task({ activeAttemptId: null, activity: { phase: "starting" } }), "att-9")).toBeNull();
  });

  it("counts the reminders only for the attempt that is running", () => {
    const reminders = { overdue: true, possiblyStuck: true, lastNativeEventAt: null, toolInProgress: false };
    const stuck = task({ activeAttemptId: "att-9", selectedAttempt: { attemptId: "att-9", reminders } });
    expect(remindersOf(stuck, "att-9")).toEqual({ overdue: true, possiblyStuck: true });
    expect(remindersOf(stuck, "att-1")).toEqual({ overdue: false, possiblyStuck: false });
    expect(remindersOf(task({ activeAttemptId: null, selectedAttempt: { attemptId: "att-9", reminders } }), "att-9")).toEqual({ overdue: false, possiblyStuck: false });
  });
});

describe("the final text of an execution", () => {
  it("is what it printed or answered, else the error it ended with", () => {
    expect(finalText(attempt({ result: { result: { finalText: "完成了" } } }))).toBe("完成了");
    expect(finalText(attempt({ result: { result: { stdout: "输出" } } }))).toBe("输出");
    expect(finalText(attempt({ result: { error: "命令失败" } }))).toBe("命令失败");
    expect(finalText(attempt({}))).toBeNull();
  });
});
