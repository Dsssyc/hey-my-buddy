import i18n from "i18next";
import { ApiError } from "./api";
import { isReason } from "./reason";
import type { Reason } from "./reason";

const blockerCodes = ["queued", "routing", "running", "awaiting-host", "review", "stop-unconfirmed"] as const;
export type DeletionBlockerKind = typeof blockerCodes[number];
export type DeletionBlocker = {
  runId: string | null;
  taskId: string;
  state: string;
  runState: string | null;
  reason: { code: `macro-task-deletion.${DeletionBlockerKind}`; params: Record<string, never> };
  attemptIds: string[];
} & ({ title: string; titleSource: "title" | "task" } | { title: null; titleSource: "none" });
export type DeletionScope = {
  artifacts: { count: number | null; bytes: number | null };
  sessions: { count: number | null; bytes: number | null };
  checkouts: number | null;
  gitRefs: number | null;
};
export type MacroTaskDeletionPreview = {
  macroTaskId: string;
  microTaskCount: number;
  unconcludedFailureCount: number;
  scope: DeletionScope;
  /** Counts reasons, so one micro task may occur more than once. */
  blockerTotal: number;
  blockers: DeletionBlocker[];
  backupRetention: "until-backup-rotation";
  deletionState: "deleting" | null;
};
export type ConfirmedMacroTaskDeletion = { macroTaskId: string; microTaskCount: number; confirm: true };

type DeletionReceipt = {
  deletionId: string; macroTaskId: string; microTaskCount: number; duplicate: boolean;
};
export type MacroTaskDeletionResult = DeletionReceipt & (
  | { state: "deleted"; deletedAt: string }
  | { state: "deleting"; deletedAt: null; failures: DeletionFailure[] }
);
export type DeletionFailure = {
  objectKind: "native-session" | "workspace" | "private-root" | "session-records" | "cli-file";
  path: string | null;
  reason: Reason;
  error: { message: string; errno: number | null; winerror: number | null } | null;
};

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, required: string[], optional: string[] = []): boolean {
  return required.every(key => Object.hasOwn(value, key))
    && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
}
function text(value: unknown): value is string { return typeof value === "string" && value.length > 0; }
function count(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
function scopeCount(value: unknown): boolean { return value === null || count(value); }
function contentScope(value: unknown): boolean {
  return record(value) && keys(value, ["count", "bytes"]) && scopeCount(value.count) && scopeCount(value.bytes);
}
function deletionScope(value: unknown): boolean {
  return record(value) && keys(value, ["artifacts", "sessions", "checkouts", "gitRefs"])
    && contentScope(value.artifacts) && contentScope(value.sessions)
    && scopeCount(value.checkouts) && scopeCount(value.gitRefs);
}
function emptyReason(value: unknown, codes: readonly string[]): boolean {
  return record(value) && keys(value, ["code", "params"])
    && codes.some(code => value.code === `macro-task-deletion.${code}`)
    && record(value.params) && Object.keys(value.params).length === 0;
}
function blocker(value: unknown): boolean {
  return record(value) && keys(value, ["runId", "taskId", "state", "runState", "reason", "attemptIds", "title", "titleSource"])
    && (value.runId === null || text(value.runId)) && text(value.taskId) && text(value.state)
    && (value.runState === null || text(value.runState)) && emptyReason(value.reason, blockerCodes)
    && Array.isArray(value.attemptIds) && value.attemptIds.every(text)
    && (value.titleSource === "none" ? value.title === null
      : ["title", "task"].includes(value.titleSource as string) && typeof value.title === "string");
}
function invalid(): never {
  throw new ApiError("INVALID_RESPONSE", () => i18n.t("macroHeader:deletion.errors.INVALID_RESPONSE"));
}

/** The confirmation is bound to this target and the service's complete count. */
export function parseMacroTaskDeletionPreview(macroTaskId: string, value: unknown): MacroTaskDeletionPreview {
  if (!record(value) || !keys(value, ["macroTaskId", "microTaskCount", "unconcludedFailureCount", "scope", "blockerTotal", "blockers", "backupRetention", "deletionState"])
    || value.macroTaskId !== macroTaskId || !count(value.microTaskCount) || !count(value.unconcludedFailureCount)
    || value.unconcludedFailureCount > value.microTaskCount || !deletionScope(value.scope) || !count(value.blockerTotal)
    || !Array.isArray(value.blockers) || value.blockers.length !== Math.min(50, value.blockerTotal)
    || !value.blockers.every(blocker) || value.backupRetention !== "until-backup-rotation"
    || ![null, "deleting"].includes(value.deletionState as null | string)) invalid();
  return value as MacroTaskDeletionPreview;
}

function failure(value: unknown): boolean {
  if (!record(value) || !keys(value, ["objectKind", "path", "reason", "error"])
    || !["native-session", "workspace", "private-root", "session-records", "cli-file"].includes(value.objectKind as string)
    || !(value.path === null || typeof value.path === "string") || !isReason(value.reason)) return false;
  const error = value.error;
  return error === null || record(error) && keys(error, ["message", "errno", "winerror"])
    && typeof error.message === "string"
    && (error.errno === null || Number.isSafeInteger(error.errno))
    && (error.winerror === null || Number.isSafeInteger(error.winerror));
}

export function parseMacroTaskDeletionResult(confirmed: ConfirmedMacroTaskDeletion, value: unknown): MacroTaskDeletionResult {
  const required = ["deletionId", "macroTaskId", "state", "deletedAt", "microTaskCount", "duplicate"];
  if (!record(value) || !text(value.deletionId) || value.macroTaskId !== confirmed.macroTaskId
    || value.microTaskCount !== confirmed.microTaskCount || typeof value.duplicate !== "boolean") invalid();
  if (value.state === "deleted") {
    if (!keys(value, required) || !text(value.deletedAt) || !Number.isFinite(Date.parse(value.deletedAt))) invalid();
  } else if (value.state === "deleting") {
    if (!keys(value, [...required, "failures"]) || value.deletedAt !== null
      || !Array.isArray(value.failures) || value.failures.length === 0 || !value.failures.every(failure)) invalid();
  } else invalid();
  return value as MacroTaskDeletionResult;
}
