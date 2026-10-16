import type { Lineage, Task } from "./types";

/** The task view's metadata boundary, shared by the list and the detail reader. */
export function recordMetadata(value: unknown): value is Lineage {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  const nullableText = (item: unknown) => item === null || typeof item === "string";
  const object = (item: unknown): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item);
  if (!["micro-task", "helper", "decision", "execution"].includes(row.kind as string)
    || !["sourceHostId", "currentHostId", "parentRunId", "rootRunId", "rootLeadRunId"].every(key => nullableText(row[key]))) return false;
  const project = row.project, buddy = row.buddy, route = row.routingTask;
  if (project !== null && !(object(project) && typeof project.id === "string" && typeof project.label === "string" && nullableText(project.path))) return false;
  if (buddy !== null && !(object(buddy) && ["harness", "provider", "model", "effort"].every(key => typeof buddy[key] === "string"))) return false;
  if (route !== null && !(object(route) && typeof route.runId === "string" && nullableText(route.title)
    && [null, "title", "first-line"].includes(route.titleSource as string | null) && nullableText(route.macroTaskId) && nullableText(route.macroTaskTitle))) return false;
  return true;
}

/** Fields the records list uses, checked before presentation shares the task. */
export function recordSummary(value: unknown): value is Task {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const task = value as Record<string, unknown>;
  if (!["runId", "task", "status", "createdAt"].every(key => typeof task[key] === "string")
    || !Number.isFinite(Date.parse(task.createdAt as string)) || !Number.isSafeInteger(task.revision) || !recordMetadata(task.lineage)) return false;
  if (task.workflow === undefined || task.workflow === null) return true;
  if (typeof task.workflow !== "object" || Array.isArray(task.workflow)) return false;
  const workflow = task.workflow as Record<string, unknown>;
  return typeof workflow.state === "string" && ["title", "macroTaskId"].every(key => workflow[key] === null || typeof workflow[key] === "string");
}
