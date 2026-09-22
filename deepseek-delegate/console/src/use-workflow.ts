import { useEffect, useRef, useState } from "react";
import { errorText, uncertainResponse } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";

export function useWorkflow(api: ConsoleApi, task: Task, snapshot: Snapshot, refresh: () => Promise<Snapshot | null>) {
  const [value, setValue] = useState<Workflow | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [controlFile, setControlFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [reload, setReload] = useState(0);
  const pending = useRef<{ operation: string; params: Record<string, unknown> } | null>(null);
  const active = useRef(false);
  const runId = task.runId;
  const csrf = snapshot.csrfToken;
  useEffect(() => {
    // React 19 cleanup prevents an older request replacing a newer snapshot.
    // https://react.dev/reference/react/useEffect#fetching-data-with-effects
    let current = true;
    setValue(null);
    api.command<Workflow>("workflow_get", { runId }, csrf).then(result => {
      if (!result.governed || result.runId !== runId || !Number.isInteger(result.revision)) throw new Error("协作记录不完整，请检查服务版本。");
      if (current) { setValue(result); setError(""); }
    }).catch(reason => { if (current) setError(errorText(reason)); });
    return () => { current = false; };
  }, [api, runId, csrf, task.revision, task.workflow?.revision, reload]);

  async function command(operation: string, params: Record<string, unknown> = {}) {
    if (active.current || (!value && !pending.current)) return;
    const request = pending.current || {
      operation,
      params: { runId, commandId: crypto.randomUUID(), ...params },
    };
    pending.current = request;
    active.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.command<{ controlFile?: string }>(request.operation, request.params, csrf);
      if (typeof result?.controlFile === "string") setControlFile(result.controlFile);
      pending.current = null;
      setUncertain(false);
      setNotice("操作已记录，正在读取最新状态。");
      setReload(n => n + 1);
      await refresh();
    } catch (reason) {
      if (uncertainResponse(reason)) {
        setUncertain(true);
        setError("提交结果尚未确认。请重试同一操作，服务会按原命令 ID 去重。当前草稿暂时锁定。");
      } else {
        pending.current = null;
        setUncertain(false);
        setError(errorText(reason));
      }
    } finally { active.current = false; setBusy(false); }
  }
  return { value, error, notice, controlFile, busy, uncertain, command, reload: () => setReload(n => n + 1) };
}
