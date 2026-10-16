/**
 * Where the tasks page is: a macro task, one of its micro tasks, one step of it.
 * `#tasks/<macro task>/<micro task>/<n>`, each part encoded; `n` counts from 1.
 */
export type TasksLocation = { macroTaskId: string | null; runId: string | null; step: number | null; records?: true };

export const TASKS_ROOT: TasksLocation = { macroTaskId: null, runId: null, step: null };

export function tasksHash({ macroTaskId, runId, step, records }: TasksLocation): string {
  if (records) return "#tasks/records" + (runId ? "/" + encodeURIComponent(runId) : "");
  if (!macroTaskId) return "#tasks";
  const parts = ["tasks", macroTaskId];
  if (runId) {
    parts.push(runId);
    if (step) parts.push(String(step));
  }
  return "#" + parts.map(encodeURIComponent).join("/");
}

/** The location a hash names; null when it names another page. A part that cannot be read names nothing. */
export function tasksLocation(hash: string): TasksLocation | null {
  const [page, macro, run, step] = hash.slice(1).split("/");
  if (page !== "tasks") return null;
  try {
    if (macro === "records") return { macroTaskId: null, runId: run ? decodeURIComponent(run) : null, step: null, records: true };
    const macroTaskId = macro ? decodeURIComponent(macro) : null;
    const runId = macroTaskId && run ? decodeURIComponent(run) : null;
    return { macroTaskId, runId, step: runId && step && /^[1-9]\d{0,3}$/.test(step) ? Number(step) : null };
  } catch {
    return TASKS_ROOT;
  }
}

/** The key of the macro task a micro task belongs to: its own, or the standalone group of its root. */
export function macroTaskKey(task: { workflow?: { macroTaskId?: string | null } | null; lineage?: { rootRunId: string | null } | null }): string | null {
  const own = task.workflow?.macroTaskId;
  if (own) return own;
  const root = task.lineage?.rootRunId;
  return root ? `run:${root}` : null;
}
