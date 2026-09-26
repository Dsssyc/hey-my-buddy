import type { Task } from "./types";

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

/**
 * ADR-012 X title fallback for delegation rows and headings: the run's own
 * newest concluded result summary wins, then the first task line, then an
 * explicit unnamed marker. A request, helper or route summary is never read
 * here, and the raw task text stays available in the detail view.
 */
export function taskTitle(task: Task): string {
  const summary = task.workflow?.resultSummary;
  const firstLine = typeof task.task === "string" ? task.task.trim().split("\n", 1)[0] ?? "" : "";
  return normalizeDisplay(typeof summary === "string" ? summary : "") || normalizeDisplay(firstLine) || "未命名委派";
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
