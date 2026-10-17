import i18n from "i18next";
import type { Lineage, Task } from "./types";

/** Where a micro task title came from; the Worker result summary is never one. */
type TitleSource = "title" | "task" | "none";

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

const UNTITLED_MICRO_TASK = () => i18n.t("console:unnamedMicroTask");
/** Task-first-line titles cap at roughly this many characters (0.16 0.1). */
const TASK_LINE_CHARS = 40;
/** Suffix pointing readers to the detail view for the full task text. */

type TaskTitle = {
  /** The bounded text lists, headings and breadcrumbs display. */
  text: string;
  /**
   * The complete title for tooltips: the whole first task line for a task
   * source (Host-revised 0.16 design), or the explicit title in full. The
   * Worker's result summary never appears here.
   */
  fullText: string;
  source: TitleSource;
};

/**
 * The one micro task title rule (0.16 0.1): explicit workflow.title, then the
 * first nonempty task line labelled 取自任务首行, then 未命名微任务. A Worker
 * resultSummary is never a title; callers show it only as a “结果” line. The
 * raw task text stays available in the detail view.
 */
export function taskTitle(task: Task): TaskTitle {
  const explicit = normalizeDisplay(task.workflow?.title ?? "");
  if (explicit) return { text: explicit, fullText: explicit, source: "title" };
  const firstLine = normalizeDisplay(task.task.trim().split("\n", 1)[0]);
  if (firstLine) return { text: excerpt(firstLine, TASK_LINE_CHARS), fullText: firstLine, source: "task" };
  return { text: UNTITLED_MICRO_TASK(), fullText: UNTITLED_MICRO_TASK(), source: "none" };
}

export function needsReview(task: Task): boolean {
  // Micro task review eligibility follows the micro task state.
  return (!task.workflow || task.workflow.state === "delivered") &&
    (!task.workflow || task.workflowShutdown?.descendantsConfirmed === true) &&
    !task.acceptedAt &&
    task.spec?.harness !== "decision" &&
    ["completed", "failed", "cancelled"].includes(task.status) &&
    task.resultAvailable === true &&
    task.shutdownConfirmed === true;
}

export function taskStatus(task: Task): string {
  if (["cancelling", "reconciliation-needed"].includes(task.status)) return task.status;
  if (task.workflow?.state === "cancelled" && task.workflowShutdown?.unconfirmedCount) return "cancelling";
  return displayWorkflowState(task.workflow?.state || task.status, task.lineage?.rootLeadRunId ?? null);
}

/** Presentation only: the stored state stays awaiting-host for a Lead-owned tree. */
function displayWorkflowState(state: string, rootLeadRunId: Lineage["rootLeadRunId"]): string {
  return state === "awaiting-host" && rootLeadRunId !== null ? "awaiting-lead" : state;
}
