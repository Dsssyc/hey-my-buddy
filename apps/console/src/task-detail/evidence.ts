import { parseActor } from "../actor";
import type { Actor, ActivityPhase, Task } from "../types";

/**
 * What the task view records of the execution it last ran. All of it belongs to
 * that one attempt, so it is read for a turn only when the turn is that attempt;
 * a recorded fact the board does not have stays unknown and is never replaced by
 * the current one.
 */
const PHASES: ActivityPhase[] = ["starting", "waiting-model", "streaming-model", "tool-running", "waiting-external", "waiting-host", "finishing", "unknown"];

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** The attempt the task view's per-attempt facts are about. */
export function selectedAttemptId(task: Task): string | null {
  return task.activeAttemptId ?? task.selectedAttemptId ?? null;
}

export function ownsAttempt(task: Task, attemptId: string): boolean {
  return task.selectedAttempt?.attemptId === attemptId;
}

export type Activity = {
  phase: ActivityPhase;
  observedAt: string | null;
  toolName: string | null;
  modelTurns: number | null;
  toolCalls: number | null;
  /** A report of the phase alone says nothing of progress: no tool and no native activity came with it. */
  heartbeat: boolean;
};

/** The reported activity of the attempt that is running now. */
export function activityOf(task: Task, attemptId: string): Activity | null {
  if (task.activeAttemptId !== attemptId) return null;
  const activity = record(task.activity);
  if (!activity) return null;
  const counts = record(activity.counts);
  const tool = text(activity.toolName) ?? text(activity.lastToolActivityAt);
  const native = text(activity.lastNativeActivityAt) ?? text(activity.nativeSessionId);
  return {
    phase: PHASES.includes(activity.phase as ActivityPhase) ? (activity.phase as ActivityPhase) : "unknown",
    observedAt: text(activity.observedAt),
    toolName: text(activity.toolName),
    modelTurns: count(counts?.modelTurns), toolCalls: count(counts?.toolCalls),
    heartbeat: !tool && !native,
  };
}

/** The reminders of the attempt that runs now: past its expected time, or silent for too long. */
export function remindersOf(task: Task, attemptId: string): { overdue: boolean; possiblyStuck: boolean } {
  const reminders = task.activeAttemptId === attemptId && task.selectedAttempt?.attemptId === attemptId ? task.selectedAttempt.reminders : null;
  return { overdue: reminders?.overdue === true, possiblyStuck: reminders?.possiblyStuck === true };
}

export type Ending = {
  /** Why the execution stopped, as recorded; null when it ended as it should or the cause is not recorded. */
  cause: string | null;
  /** Who stopped it, when it was stopped. */
  stoppedBy: "host" | "lead" | "console" | "service" | "unknown" | null;
  actor: Actor | null;
  reason: string | null;
  quota: "quota-exceeded" | "rate-limited" | null;
};

/** How the last attempt ended: the recorded cause, who cancelled it and why, and a native limit. */
export function endingOf(task: Task, attemptId: string): Ending | null {
  if (!ownsAttempt(task, attemptId)) return null;
  const receipt = record(task.selectedAttempt?.result);
  const raw = text(receipt?.terminationReason) ?? text(task.terminationReason);
  const cancelled = task.status === "cancelled" || task.status === "cancelling" || task.workflow?.state === "cancelled";
  const cancellation = record(task.cancellation);
  const initiator = task.selectedAttempt?.stopInitiator ?? task.stopInitiator ?? null;
  const actor = parseActor(cancellation?.actor);
  const reason = text(cancellation?.reason);
  const stoppedBy = initiator ?? (cancelled ? (actor?.role === "host" || actor?.role === "lead" || actor?.role === "console" || actor?.role === "service" ? actor.role : "unknown") : null);
  const quota = task.quotaFailure?.code ?? null;
  const normal = raw === "completed" || (raw === null && !cancelled && !quota);
  return normal ? null : { cause: raw, stoppedBy: cancelled ? stoppedBy : null, actor: cancelled ? actor : null, reason: cancelled ? reason : null, quota };
}

export type SessionStorage = { scope: string | null; owner: string | null; visibility: string | null };

/** Where the attempt's native session lives and whether the native app lists it: the receipt's own words, or unknown. */
export function sessionStorageOf(task: Task, attemptId: string): SessionStorage | null {
  if (!ownsAttempt(task, attemptId)) return null;
  const native = record(record(record(task.selectedAttempt?.result)?.result)?.nativeSession);
  if (!native) return null;
  return { scope: text(native.storageScope), owner: text(native.storageOwner), visibility: text(native.nativeAppVisibility) };
}

/** The execution's final text: what a command printed or a Router answered, or the error it ended with. */
export function finalText(task: Task): string | null {
  const receipt = record(task.selectedAttempt?.result);
  const output = record(receipt?.result);
  for (const value of [output?.finalText, output?.stdout, receipt?.error]) {
    const found = text(value);
    if (found) return found;
  }
  return null;
}
