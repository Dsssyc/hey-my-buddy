import { useEffect, useState } from "react";
import { errorText } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";

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
  useEffect(() => {
    if (!visible) return;
    // React 19 cleanup prevents an older request replacing a newer snapshot.
    // https://react.dev/reference/react/useEffect#fetching-data-with-effects
    let current = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api.command<Workflow>("workflow_get", { runId }, csrf);
        if (!result.governed || result.runId !== runId || !Number.isInteger(result.revision)) throw new Error("协作记录不完整，请检查服务版本。");
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
