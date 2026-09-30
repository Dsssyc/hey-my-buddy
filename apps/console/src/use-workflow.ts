import { useEffect, useState } from "react";
import { ApiError, errorText } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";
import { useGlobalRefresh } from "./global-refresh";

/**
 * Strict check of one `workflow_get` reply: the governed view must belong to
 * the requested run and carry an integer revision. Anything else — including a
 * future incompatible shape — is refused instead of being presented as a
 * partial collaboration record. This is the one parser both the poll and the
 * manual refresh use.
 */
export function parseWorkflowReply(value: unknown, runId: string): Workflow {
  const result = value as Workflow | null;
  if (!result || typeof result !== "object" || !result.governed || result.runId !== runId
    || !Number.isInteger(result.revision)) {
    throw new ApiError("INVALID_RESPONSE", "协作记录不完整，请检查服务版本。");
  }
  return result;
}

/**
 * Read-only workflow read for the delegation detail (0.15.1 U4). The browser
 * no longer submits, retries, acknowledges or continues anything, so this hook
 * only polls `workflow_get` — one of the reads every authenticated session
 * keeps — and never dispatches a mutation or mints a command identity.
 */
export function useWorkflow(api: ConsoleApi, task: Task, snapshot: Snapshot, visible = true) {
  const [value, setValue] = useState<Workflow | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const csrf = snapshot.csrfToken;
  const runId = task.runId;
  useGlobalRefresh(async () => {
    setValue(parseWorkflowReply(await api.command<Workflow>("workflow_get", { runId }, csrf), runId));
    setError("");
  }, visible);
  useEffect(() => {
    if (!visible) return;
    // React 19 cleanup prevents an older request replacing a newer snapshot.
    // https://react.dev/reference/react/useEffect#fetching-data-with-effects
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = parseWorkflowReply(await api.command<Workflow>("workflow_get", { runId }, csrf), runId);
        if (current) { setValue(result); setError(""); }
      } catch (reason) { if (current) setError(errorText(reason)); }
      if (current) timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { current = false; clearTimeout(timer); };
  }, [api, runId, csrf, task.revision, task.workflow?.revision, reload, visible]);

  return {
    value, error,
    reload: () => setReload(n => n + 1),
  };
}
