/**
 * Where the tasks page is: a macro task, one of its micro tasks, one step of it.
 * `#tasks/<macro task>/<micro task>/<n>`, each part encoded; `n` counts from 1.
 */
export type DetailTab = "overview" | "session";
export type TasksLocation = { macroTaskId: string | null; runId: string | null; step: number | null; tab?: "session" };

export const TASKS_ROOT: TasksLocation = { macroTaskId: null, runId: null, step: null };

export function tasksHash({ macroTaskId, runId, step, tab }: TasksLocation): string {
  const parts = macroTaskId ? ["tasks", macroTaskId] : ["tasks"];
  if (runId) {
    parts.push(runId);
    if (step) parts.push(String(step));
  }
  const params = new URLSearchParams();
  if (runId && tab === "session") params.set("tab", "session");
  const query = params.toString();
  return "#" + parts.map(encodeURIComponent).join("/") + (query ? `?${query}` : "");
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
    return { macroTaskId, runId, step: runId && step && /^[1-9]\d{0,3}$/.test(step) ? Number(step) : null,
      ...(runId && params.get("tab") === "session" ? { tab: "session" } : {}) };
  } catch {
    return TASKS_ROOT;
  }
}
