import { useCallback, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isSessionExpiredRefusal, uncertainResponse } from "./api";
import type { Snapshot } from "./types";
import type { MacroTaskStopResult, MacroTaskSummary, MacroTaskTimeline } from "./macro-task-types";
import { errorOfStopReply } from "./macro-task-stop-reply";
import type { AuthorityLatch } from "./console-session";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "./console-session";

/**
 * MacroTask-level stop (0.15.1 U4): the only write this view still offers.
 * `macro_task_stop` requires the current writer; one explicit confirmation names
 * the macroTask and its scope, and the exact command identity is retained so a
 * lost reply can be replayed without adding a new cancellation scope. The
 * acknowledgement is never read as termination evidence — the timeline's own
 * complete reads decide 正在停止 / 停止未确认 / 已停止 — and no navigation is
 * ever locked.
 */
export type MacroTaskStopPhase = "stopping" | "unknown" | "acknowledged" | "refused";

export type MacroTaskStopEntry = {
  commandId: string;
  phase: MacroTaskStopPhase;
  /** The server reply once acknowledged; drives the refresh-based status. */
  result: MacroTaskStopResult | null;
  error: string;
};

export type MacroTaskStopStatus = {
  phase: MacroTaskStopPhase | "in-progress" | "unconfirmed-stop" | "stopped";
  label: string;
  detail: string;
};

const ACTIVE_TASK_STATUS = new Set(["queued", "running", "cancelling", "reconciliation-needed"]);
const ACTIVE_ROW_STATE = new Set(["executing", "waiting-helpers", "awaiting-host"]);

/** Whether a summary still has members a macro task stop could cancel. */
export function stoppableSummary(summary: MacroTaskSummary | null): boolean {
  if (!summary) return false;
  return summary.counts.active + summary.counts.host + summary.counts.review > 0;
}

/**
 * Honest stop status derived from the acknowledged reply plus the refreshed
 * timeline read. A stop is proven only by a COMPLETE read (no filters, no
 * truncation) in which every affected root is cancelled and every member row
 * of those roots — helpers included — carries confirmed shutdown. Anything
 * less keeps 正在停止 (work still visible) or 停止未确认; a partial read can
 * never claim 已停止.
 */
export function stopStatus(entry: MacroTaskStopEntry | undefined, timeline: MacroTaskTimeline | null): MacroTaskStopStatus | null {
  if (!entry) return null;
  if (entry.phase === "refused") {
    return { phase: "refused", label: "停止请求未提交", detail: entry.error || "请求被拒绝。" };
  }
  if (entry.phase === "unknown") {
    return {
      phase: "unknown",
      label: "停止未确认",
      detail: "停止结果未知，可能已生效；可重试同一请求或刷新核对。",
    };
  }
  if (entry.phase === "stopping") {
    return { phase: "stopping", label: "正在停止", detail: "停止中 · 等待证据" };
  }
  const runIds = entry.result?.runIds ?? [];
  if (!timeline) {
    return { phase: "unconfirmed-stop", label: "停止未确认", detail: "时间轴读取不完整" };
  }
  const complete = timeline.scopeComplete && !timeline.filtered && !timeline.truncated.rows;
  const rowsById = new Map(timeline.rows.map(row => [row.runId, row]));
  // The stop scope's roots: every affected root's whole tree must be verified.
  const affectedRoots = new Set(runIds
    .map(runId => rowsById.get(runId))
    .filter(row => row !== undefined && row.parentRunId === null)
    .map(row => row!.runId));
  let pending = 0, uncertain = 0;
  const count = (row: { status: string; state: string; shutdownConfirmed: boolean }) => {
    if (row.shutdownConfirmed) return;
    if (ACTIVE_TASK_STATUS.has(row.status) || ACTIVE_ROW_STATE.has(row.state)) pending += 1;
    else uncertain += 1;
  };
  // Every run the reply named must be present and confirmed; a missing run
  // cannot be verified from this read.
  for (const runId of runIds) {
    const row = rowsById.get(runId);
    if (!row) { uncertain += 1; continue; }
    if (affectedRoots.has(row.rootRunId)) continue; // counted with the whole tree below
    count(row);
  }
  // Whole-tree verification: all members of each affected root, helpers included.
  for (const row of timeline.rows) {
    if (affectedRoots.has(row.rootRunId)) count(row);
  }
  // A root the server cancelled must actually read as cancelled.
  for (const rootId of affectedRoots) {
    const root = rowsById.get(rootId);
    if (root && root.state !== "cancelled" && root.status !== "cancelled") pending += 1;
  }
  if (pending > 0) {
    return { phase: "in-progress", label: "正在停止", detail: `仍有 ${pending} 项停止未确认` };
  }
  if (uncertain > 0) {
    return { phase: "unconfirmed-stop", label: "停止未确认", detail: `${uncertain} 项停止未确认或超出读取范围` };
  }
  if (!complete) {
    return {
      phase: "unconfirmed-stop",
      label: "停止未确认",
      detail: "时间轴读取不完整 · 停止未确认",
    };
  }
  // runIds mixes roots and helpers; only the root count names 微任务.
  return { phase: "stopped", label: "已停止", detail: `${affectedRoots.size} 个微任务及协助任务已停止` };
}

export function useMacroTaskStop(api: ConsoleApi, snapshot: Snapshot, writesAvailable: boolean, authority: AuthorityLatch | undefined, refresh: () => Promise<unknown>) {
  const [entries, setEntries] = useState<Map<string, MacroTaskStopEntry>>(() => new Map());
  const [confirming, setConfirming] = useState<MacroTaskSummary | null>(null);
  // One in-flight stop per macroTask: a second confirmation of the SAME
  // macroTask is ignored while its reply is pending, but different macroTasks
  // navigate and stop independently.
  const inFlight = useRef<Set<string>>(new Set());

  const writable = writesAvailable && (authority ? authority.writable(snapshot) : snapshot.consoleSession?.canWrite === true);

  const patch = useCallback((macroTaskId: string, next: Partial<MacroTaskStopEntry>) => {
    setEntries(previous => {
      const map = new Map(previous);
      const entry = map.get(macroTaskId);
      if (entry) map.set(macroTaskId, { ...entry, ...next });
      return map;
    });
  }, []);

  const dispatch = useCallback(async (summary: MacroTaskSummary, entry: MacroTaskStopEntry, replay: boolean) => {
    const macroTaskId = summary.macroTaskId;
    if (inFlight.current.has(macroTaskId)) return;
    inFlight.current.add(macroTaskId);
    patch(summary.macroTaskId, { phase: "stopping", error: "" });
    try {
      const result = await api.command<MacroTaskStopResult>("macro_task_stop", {
        macroTaskId: summary.macroTaskId,
        commandId: entry.commandId,
      }, snapshot.csrfToken);
      const checked = errorOfStopReply(summary.macroTaskId, result);
      // The acknowledged cancellation intent is durable: a later refresh
      // failure must never demote it back to refused/unknown.
      patch(summary.macroTaskId, { phase: "acknowledged", result: checked });
      try {
        await refresh();
      } catch {
        // Refresh is best effort; the 3-second reads carry the stop evidence.
      }
    } catch (reason) {
      if (uncertainResponse(reason)) {
        patch(summary.macroTaskId, { phase: "unknown", error: errorText(reason) });
      } else if (isSessionExpiredRefusal(reason) && replay) {
        // A replay that is definitely refused proves only that THIS request
        // was denied; the earlier unknown attempt stays unknown.
        patch(macroTaskId, {
          phase: "unknown",
          error: `${errorText(reason)} 停止结果未知；可重试同一请求。`,
        });
      } else {
        patch(summary.macroTaskId, { phase: "refused", error: errorText(reason) });
      }
    } finally {
      inFlight.current.delete(macroTaskId);
    }
  }, [api, patch, refresh, snapshot.csrfToken]);

  /** Step one: the explicit confirmation naming the macroTask and its scope. */
  const requestStop = useCallback((summary: MacroTaskSummary) => {
    if (!writable) return;
    setConfirming(summary);
  }, [writable]);

  const cancelConfirm = useCallback(() => setConfirming(null), []);

  /** Step two: mint the command identity and dispatch; a replay keeps it. */
  const confirmStop = useCallback((summary: MacroTaskSummary) => {
    setConfirming(null);
    const macroTaskId = summary.macroTaskId;
    // A dispatch for this macroTask is already pending: ignore the duplicate
    // before touching any entry, so the retained identity and status survive.
    if (inFlight.current.has(macroTaskId)) return;
    const existing = entries.get(macroTaskId);
    if (!writable) {
      // Losing the writer proves nothing about an earlier attempt: a retained
      // unknown (or still-pending/acknowledged) entry keeps its identity and
      // stays replayable. Only a request that never reached the board becomes
      // a plain refusal.
      if (existing && existing.phase !== "refused") return;
      setEntries(previous => new Map(previous).set(macroTaskId, {
        // Empty on purpose: this request was never dispatched, and the empty
        // identity is never reused once the writer returns.
        commandId: "", phase: "refused", result: existing?.result ?? null, error: LOGIN_EXPIRED_ACTION_REFUSAL,
      }));
      return;
    }
    // Reuse only a dispatched (nonempty) identity; anything else mints fresh.
    const reusable = existing && existing.commandId !== ""
      && (existing.phase === "unknown" || existing.phase === "refused");
    const entry: MacroTaskStopEntry = reusable
      ? { ...existing!, phase: "stopping", error: "" }
      : { commandId: crypto.randomUUID(), phase: "stopping", result: existing?.result ?? null, error: "" };
    setEntries(previous => new Map(previous).set(macroTaskId, entry));
    void dispatch(summary, entry, existing?.phase === "unknown");
  }, [dispatch, entries, writable]);

  /** Replay the retained dispatched identity: never an empty or pending one. */
  const retryStop = useCallback((summary: MacroTaskSummary) => {
    if (!writable) return;
    if (inFlight.current.has(summary.macroTaskId)) return;
    const entry = entries.get(summary.macroTaskId);
    if (!entry || entry.commandId === "" || entry.phase === "acknowledged" || entry.phase === "stopping") return;
    void dispatch(summary, entry, entry.phase === "unknown");
  }, [dispatch, entries, writable]);

  /** Clear a settled refusal so the header returns to the plain stop action. */
  const dismissStop = useCallback((macroTaskId: string) => {
    setEntries(previous => {
      const map = new Map(previous);
      const entry = map.get(macroTaskId);
      if (entry && entry.phase === "refused") map.delete(macroTaskId);
      return map;
    });
  }, []);

  return { entries, confirming, writable, requestStop, cancelConfirm, confirmStop, retryStop, dismissStop };
}
