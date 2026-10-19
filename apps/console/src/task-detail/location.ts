/**
 * Where the tasks page is: a macro task, one of its micro tasks, one step of it.
 * `#tasks/<macro task>/<micro task>/<n>`, each part encoded; `n` counts from 1.
 */
export type TasksLocation = { macroTaskId: string | null; runId: string | null; step: number | null; session?: { taskId: string; attemptId: string } };

export const TASKS_ROOT: TasksLocation = { macroTaskId: null, runId: null, step: null };

export function tasksHash({ macroTaskId, runId, step, session }: TasksLocation): string {
  if (!macroTaskId) return "#tasks";
  const parts = ["tasks", macroTaskId];
  if (runId) {
    parts.push(runId);
    if (step) parts.push(String(step));
  }
  const query = session ? "?" + new URLSearchParams({ sessionTask: session.taskId, sessionAttempt: session.attemptId }) : "";
  return "#" + parts.map(encodeURIComponent).join("/") + query;
}

/** The location a hash names; null when it names another page. A part that cannot be read names nothing. */
export function tasksLocation(hash: string): TasksLocation | null {
  const [path, query] = hash.slice(1).split("?");
  const [page, macro, run, step] = path.split("/");
  if (page !== "tasks") return null;
  try {
    const macroTaskId = macro ? decodeURIComponent(macro) : null;
    const runId = macroTaskId && run ? decodeURIComponent(run) : null;
    const params = new URLSearchParams(query);
    const taskId = params.get("sessionTask"), attemptId = params.get("sessionAttempt");
    return { macroTaskId, runId, step: runId && step && /^[1-9]\d{0,3}$/.test(step) ? Number(step) : null,
      ...(macroTaskId && taskId && attemptId ? { session: { taskId, attemptId } } : {}) };
  } catch {
    return TASKS_ROOT;
  }
}
