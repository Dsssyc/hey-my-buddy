import type { Task } from "./types";

/** Where a delegation title came from; the Worker result summary is never one. */
export type TitleSource = "title" | "task" | "none";

export function excerpt(text: string, limit = 120): string {
  let result = "", count = 0;
  for (const character of text.trim()) {
    if (count++ === limit) return result + "…";
    result += character;
  }
  return result;
}

/** Display whitespace: every whitespace run, CRLF included, becomes one space. */
function normalizeDisplay(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export const TASK_TITLE_SOURCE_LABEL: Record<TitleSource, string> = {
  title: "Host 标题",
  task: "取自任务首行",
  none: "未命名委派",
};
export const UNTITLED_DELEGATION = "未命名委派";
/** Task-first-line titles cap at roughly this many characters (0.16 0.1). */
const TASK_LINE_CHARS = 40;

/**
 * The one delegation title rule (0.16 0.1): explicit workflow.title, then the
 * first nonempty task line labelled 取自任务首行, then 未命名委派. A Worker
 * resultSummary is never a title; callers show it only as a “结果” line. The
 * raw task text stays available in the detail view.
 */
export function taskTitle(task: Task): { text: string; source: TitleSource } {
  const explicit = normalizeDisplay(typeof task.workflow?.title === "string" ? task.workflow.title : "");
  if (explicit) return { text: explicit, source: "title" };
  const firstLine = typeof task.task === "string"
    ? normalizeDisplay(task.task.trim().split("\n", 1)[0] ?? "")
    : "";
  if (firstLine) return { text: excerpt(firstLine, TASK_LINE_CHARS), source: "task" };
  return { text: UNTITLED_DELEGATION, source: "none" };
}

export function needsReview(task: Task): boolean {
  return (
    (!task.workflow || task.workflow.state === "delivered") &&
    (!task.workflow || task.workflowShutdown?.descendantsConfirmed === true) &&
    !task.acceptedAt &&
    task.spec?.adapter !== "decision" &&
    ["completed", "failed", "cancelled"].includes(task.status) &&
    task.resultAvailable === true &&
    task.shutdownConfirmed === true
  );
}

export function taskStatus(task: Task): string {
  if (["cancelling", "reconciliation-needed"].includes(task.status)) return task.status;
  if (task.workflow?.state === "cancelled" && task.workflowShutdown?.unconfirmedCount) return "cancelling";
  return task.workflow?.state || task.status;
}

export function canRetry(task: Task): boolean {
  // A cancelled queued task has no process to stop. Missing identity fields are
  // unknown, so require the explicit nulls returned by the authoritative store.
  const neverClaimed =
    task.selectedAttemptId === null && task.activeAttemptId === null;
  return (
    !task.workflow &&
    task.spec?.adapter !== "decision" &&
    ["failed", "cancelled"].includes(task.status) &&
    (neverClaimed || task.shutdownConfirmed === true)
  );
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function resultText(value: unknown): string {
  const task = record(value);
  const attempt = record(task?.selectedAttempt);
  const receipt = record(attempt?.result);
  if (!receipt) return "尚无持久交付结果。执行结束和验收会分别记录。";
  const output = record(receipt.result);
  for (const text of [output?.finalText, output?.stdout, receipt.error]) {
    if (typeof text === "string" && text.trim()) return text;
  }
  return JSON.stringify(output || receipt, null, 2);
}
