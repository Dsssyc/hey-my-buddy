import { useCallback, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isReadOnlyRefusal, uncertainResponse } from "./api";
import type { Snapshot } from "./types";
import type { ObjectiveStopResult, ObjectiveSummary, ObjectiveTimeline } from "./objective-types";
import { errorOfStopReply } from "./objective-stop-reply";
import type { AuthorityLatch } from "./console-session";
import { READ_ONLY_ACTION_REFUSAL } from "./console-session";

/**
 * Objective-level stop (0.15.1 U4): the only write this view still offers.
 * `objective_stop` requires the current writer; one explicit confirmation names
 * the objective and its scope, and the exact command identity is retained so a
 * lost reply can be replayed without adding a new cancellation scope. The
 * acknowledgement is never read as termination evidence — the timeline's own
 * complete reads decide 正在停止 / 停止未确认 / 已停止 — and no navigation is
 * ever locked.
 */
export type ObjectiveStopPhase = "stopping" | "unknown" | "acknowledged" | "refused";

export type ObjectiveStopEntry = {
  commandId: string;
  phase: ObjectiveStopPhase;
  /** The server reply once acknowledged; drives the refresh-based status. */
  result: ObjectiveStopResult | null;
  error: string;
};

export type ObjectiveStopStatus = {
  phase: ObjectiveStopPhase | "in-progress" | "unconfirmed-stop" | "stopped";
  label: string;
  detail: string;
};

const ACTIVE_TASK_STATUS = new Set(["queued", "running", "cancelling", "reconciliation-needed"]);
const ACTIVE_ROW_STATE = new Set(["executing", "waiting-helpers", "awaiting-host"]);

/** Whether a summary still has members an objective stop could cancel. */
export function stoppableSummary(summary: ObjectiveSummary | null): boolean {
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
export function stopStatus(entry: ObjectiveStopEntry | undefined, timeline: ObjectiveTimeline | null): ObjectiveStopStatus | null {
  if (!entry) return null;
  if (entry.phase === "refused") {
    return { phase: "refused", label: "停止请求未提交", detail: entry.error || "请求被拒绝。" };
  }
  if (entry.phase === "unknown") {
    return {
      phase: "unknown",
      label: "停止未确认",
      detail: "停止请求的回复丢失，结果未知：请求可能已生效。可重试同一停止请求，不会重复取消；也可稍后按刷新的记录核对。导航不受影响。",
    };
  }
  if (entry.phase === "stopping") {
    return { phase: "stopping", label: "正在停止", detail: "停止请求已发出，等待回复与停止证据。" };
  }
  const runIds = entry.result?.runIds ?? [];
  if (!timeline) {
    return { phase: "unconfirmed-stop", label: "停止未确认", detail: "缺少可核对的完整时间轴读取。" };
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
    return { phase: "in-progress", label: "正在停止", detail: `取消请求已记录，仍有 ${pending} 项执行或根委派未确认停止；确认前不算已停止。` };
  }
  if (uncertain > 0) {
    return { phase: "unconfirmed-stop", label: "停止未确认", detail: `取消范围内 ${uncertain} 项缺少确认的停止证据或不在当前读取范围。` };
  }
  if (!complete) {
    return {
      phase: "unconfirmed-stop",
      label: "停止未确认",
      detail: "当前时间轴读取不完整（筛选或截断），不能凭局部记录断定整组已停止。",
    };
  }
  // runIds mixes roots and helpers; only the root count names 委派.
  return { phase: "stopped", label: "已停止", detail: `这 ${affectedRoots.size} 个委派及其协助任务均已确认停止。` };
}

export function useObjectiveStop(api: ConsoleApi, snapshot: Snapshot, writesAvailable: boolean, authority: AuthorityLatch | undefined, refresh: () => Promise<unknown>) {
  const [entries, setEntries] = useState<Map<string, ObjectiveStopEntry>>(() => new Map());
  const [confirming, setConfirming] = useState<ObjectiveSummary | null>(null);
  // One in-flight stop per objective: a second confirmation of the SAME
  // objective is ignored while its reply is pending, but different objectives
  // navigate and stop independently.
  const inFlight = useRef<Set<string>>(new Set());

  const writable = writesAvailable && (authority ? authority.writable(snapshot) : snapshot.consoleSession?.canWrite === true);

  const patch = useCallback((objectiveId: string, next: Partial<ObjectiveStopEntry>) => {
    setEntries(previous => {
      const map = new Map(previous);
      const entry = map.get(objectiveId);
      if (entry) map.set(objectiveId, { ...entry, ...next });
      return map;
    });
  }, []);

  const dispatch = useCallback(async (summary: ObjectiveSummary, entry: ObjectiveStopEntry, replay: boolean) => {
    const objectiveId = summary.objectiveId;
    if (inFlight.current.has(objectiveId)) return;
    inFlight.current.add(objectiveId);
    patch(summary.objectiveId, { phase: "stopping", error: "" });
    try {
      const result = await api.command<ObjectiveStopResult>("objective_stop", {
        objectiveId: summary.objectiveId,
        commandId: entry.commandId,
      }, snapshot.csrfToken);
      const checked = errorOfStopReply(summary.objectiveId, result);
      // The acknowledged cancellation intent is durable: a later refresh
      // failure must never demote it back to refused/unknown.
      patch(summary.objectiveId, { phase: "acknowledged", result: checked });
      try {
        await refresh();
      } catch {
        // Refresh is best effort; the 3-second reads carry the stop evidence.
      }
    } catch (reason) {
      if (uncertainResponse(reason)) {
        patch(summary.objectiveId, { phase: "unknown", error: errorText(reason) });
      } else if (isReadOnlyRefusal(reason) && replay) {
        // A replay that is definitely refused proves only that THIS request
        // was denied; the earlier unknown attempt stays unknown.
        patch(objectiveId, {
          phase: "unknown",
          error: `${errorText(reason)} 此前那次停止请求的结果仍未知，保留原请求身份，可再次重试。`,
        });
      } else {
        patch(summary.objectiveId, { phase: "refused", error: errorText(reason) });
      }
    } finally {
      inFlight.current.delete(objectiveId);
    }
  }, [api, patch, refresh, snapshot.csrfToken]);

  /** Step one: the explicit confirmation naming the objective and its scope. */
  const requestStop = useCallback((summary: ObjectiveSummary) => {
    if (!writable) return;
    setConfirming(summary);
  }, [writable]);

  const cancelConfirm = useCallback(() => setConfirming(null), []);

  /** Step two: mint the command identity and dispatch; a replay keeps it. */
  const confirmStop = useCallback((summary: ObjectiveSummary) => {
    setConfirming(null);
    const objectiveId = summary.objectiveId;
    // A dispatch for this objective is already pending: ignore the duplicate
    // before touching any entry, so the retained identity and status survive.
    if (inFlight.current.has(objectiveId)) return;
    const existing = entries.get(objectiveId);
    if (!writable) {
      // Losing the writer proves nothing about an earlier attempt: a retained
      // unknown (or still-pending/acknowledged) entry keeps its identity and
      // stays replayable. Only a request that never reached the board becomes
      // a plain refusal.
      if (existing && existing.phase !== "refused") return;
      setEntries(previous => new Map(previous).set(objectiveId, {
        // Empty on purpose: this request was never dispatched, and the empty
        // identity is never reused once the writer returns.
        commandId: "", phase: "refused", result: existing?.result ?? null, error: READ_ONLY_ACTION_REFUSAL,
      }));
      return;
    }
    // Reuse only a dispatched (nonempty) identity; anything else mints fresh.
    const reusable = existing && existing.commandId !== ""
      && (existing.phase === "unknown" || existing.phase === "refused");
    const entry: ObjectiveStopEntry = reusable
      ? { ...existing!, phase: "stopping", error: "" }
      : { commandId: crypto.randomUUID(), phase: "stopping", result: existing?.result ?? null, error: "" };
    setEntries(previous => new Map(previous).set(objectiveId, entry));
    void dispatch(summary, entry, existing?.phase === "unknown");
  }, [dispatch, entries, writable]);

  /** Replay the retained dispatched identity: never an empty or pending one. */
  const retryStop = useCallback((summary: ObjectiveSummary) => {
    if (!writable) return;
    if (inFlight.current.has(summary.objectiveId)) return;
    const entry = entries.get(summary.objectiveId);
    if (!entry || entry.commandId === "" || entry.phase === "acknowledged" || entry.phase === "stopping") return;
    void dispatch(summary, entry, entry.phase === "unknown");
  }, [dispatch, entries, writable]);

  /** Clear a settled refusal so the header returns to the plain stop action. */
  const dismissStop = useCallback((objectiveId: string) => {
    setEntries(previous => {
      const map = new Map(previous);
      const entry = map.get(objectiveId);
      if (entry && entry.phase === "refused") map.delete(objectiveId);
      return map;
    });
  }, []);

  return { entries, confirming, writable, requestStop, cancelConfirm, confirmStop, retryStop, dismissStop };
}
