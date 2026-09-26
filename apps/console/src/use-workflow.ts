import { useEffect, useRef, useState } from "react";
import { errorText, isReadOnlyRefusal, uncertainResponse } from "./api";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";
import type { Workflow } from "./workflow-types";
import {
  CONNECTION_WRITE_PAUSED,
  READ_ONLY_ACTION_REFUSAL,
  UNRESOLVED_COMMAND_HANDOFF,
  createAuthorityLatch,
  readOnlySessionAllows,
} from "./console-session";
import type { AuthorityLatch } from "./console-session";

export function useWorkflow(api: ConsoleApi, task: Task, snapshot: Snapshot, refresh: () => Promise<Snapshot | null>, visible = true, writesAvailable = true, authority?: AuthorityLatch) {
  const [value, setValue] = useState<Workflow | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [controlFile, setControlFile] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [reload, setReload] = useState(0);
  const pending = useRef<{ operation: string; params: Record<string, unknown> } | null>(null);
  const active = useRef(false);
  const localLatch = useRef<AuthorityLatch | null>(null);
  if (!localLatch.current) localLatch.current = createAuthorityLatch();
  const latch = authority ?? localLatch.current;
  const runId = task.runId;
  const csrf = snapshot.csrfToken;
  const sessionId = useRef(snapshot.consoleSession?.id);
  sessionId.current = snapshot.consoleSession?.id;
  // Latch-aware session authority, restricted further by the page-level gate
  // (a failed authenticated poll pauses writes without claiming a takeover).
  const sessionWritable = latch.writable(snapshot);
  const sessionWritableRef = useRef(sessionWritable);
  sessionWritableRef.current = sessionWritable;
  const gate = useRef(writesAvailable && sessionWritable);
  gate.current = writesAvailable && sessionWritable;
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
        if (current) { setValue(result); if (!pending.current) setError(""); }
      } catch (reason) { if (current) setError(errorText(reason)); }
      if (current) timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { current = false; clearTimeout(timer); };
  }, [api, runId, csrf, task.revision, task.workflow?.revision, reload, visible]);

  async function command(operation: string, params: Record<string, unknown> = {}) {
    if (active.current || (!value && !pending.current)) return;
    const request = pending.current || {
      operation,
      params: { runId, commandId: crypto.randomUUID(), ...params },
    };
    /** A retained request is an earlier attempt whose reply was never confirmed. */
    const retry = pending.current !== null;
    // `workflow_get` polling stays available read-only; every mutation routed
    // through here is refused without a retry when the session lost authority or
    // the authenticated poll failed. The retained request keeps its exact
    // command ID and unknown-result status; nothing is replayed automatically.
    const liveSessionWritable = sessionWritableRef.current && latch.writable(snapshot);
    if (!readOnlySessionAllows(request.operation) && (!gate.current || !liveSessionWritable)) {
      // The live ref, not a possibly stale render value, decides the reason.
      setError(!liveSessionWritable
        ? (retry ? UNRESOLVED_COMMAND_HANDOFF : READ_ONLY_ACTION_REFUSAL)
        : (retry ? `${CONNECTION_WRITE_PAUSED}此前提交的同一命令结果仍未确认。` : CONNECTION_WRITE_PAUSED));
      return;
    }
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
      if (isReadOnlyRefusal(reason)) {
        // A definite refusal latches the loss for this session, so an in-flight
        // or stale callback cannot dispatch another write before the next poll.
        // It proves only that *this* request was denied: a retry of an already
        // ambiguous command stays retained with its unknown-result status.
        latch.lose(sessionId.current);
        gate.current = false;
        if (retry) {
          setUncertain(true);
          setError(UNRESOLVED_COMMAND_HANDOFF);
        } else {
          pending.current = null;
          setUncertain(false);
          setError(errorText(reason));
        }
      } else if (uncertainResponse(reason)) {
        setUncertain(true);
        setError("提交结果尚未确认。请重试同一操作，服务会按原命令 ID 去重。当前草稿暂时锁定。");
      } else {
        pending.current = null;
        setUncertain(false);
        setError(errorText(reason));
      }
    } finally { active.current = false; setBusy(false); }
  }
  return {
    value, error, notice, controlFile, busy, uncertain,
    /** Descriptor/latch authority: false means the session itself is read-only. */
    sessionWritable,
    /** Full gate: descriptor/latch authority and a live authenticated poll. */
    writable: writesAvailable && sessionWritable,
    command,
    reload: () => setReload(n => n + 1),
  };
}
