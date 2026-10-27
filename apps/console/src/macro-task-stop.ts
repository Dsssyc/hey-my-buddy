import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "i18next";
import "./i18n";
import type { ConsoleApi } from "./api";
import { errorText, isSessionExpiredRefusal, uncertainResponse } from "./api";
import type { Snapshot } from "./types";
import type { MacroTaskStopResult, MacroTaskSummary, MacroTaskTimeline } from "./macro-task-types";
import { errorOfStopReply } from "./macro-task-stop-reply";
import type { AuthorityLatch } from "./console-session";
import { LOGIN_EXPIRED_ACTION_REFUSAL, sessionCanWrite } from "./console-session";

/**
 * MacroTask-level stop (0.15.1 U4): the only write this view still offers.
 * `macro_task_stop` requires the current writer; one explicit confirmation names
 * the macroTask and its scope, and the exact command identity is retained so a
 * lost reply can be replayed without adding a new cancellation scope. The
 * acknowledgement is never read as termination evidence — the timeline's own
 * complete reads decide 正在停止 / 停止未确认 / 已停止 — and no navigation is
 * ever locked.
 */
type MacroTaskStopPhase = "stopping" | "unknown" | "acknowledged" | "refused";

export type MacroTaskStopEntry = {
  commandId: string;
  phase: MacroTaskStopPhase;
  /** The server reply once acknowledged; drives the refresh-based status. */
  result: MacroTaskStopResult | null;
  error: string | (() => string);
};

type MacroTaskStopStatus = {
  phase: MacroTaskStopPhase | "in-progress" | "unconfirmed-stop" | "stopped";
  label: string;
  detail: string;
};

const ACTIVE_TASK_STATUS = new Set(["queued", "running", "cancelling", "reconciliation-needed"]);
const ACTIVE_ROW_STATE = new Set(["executing", "waiting-helpers", "waiting-owned"]);

/** Counts from a complete timeline, including active helpers below preserved roots. */
export function stopScope(summary: MacroTaskSummary) {
  return {
    roots: summary.counts.stoppableRoots,
    helpers: summary.counts.stoppableHelpers,
    delivered: summary.counts.delivered,
    awaitingHost: summary.counts.awaitingHost,
  };
}
export type StopScope = NonNullable<ReturnType<typeof stopScope>>;

/**
 * Honest stop status derived from the acknowledged reply plus the refreshed
 * timeline read. Only members marked stopped in the reply need cancellation
 * evidence. A complete read is required before claiming 已停止.
 */
export function stopStatus(entry: MacroTaskStopEntry | undefined, timeline: MacroTaskTimeline | null): MacroTaskStopStatus | null {
  const t = (key: keyof typeof import("./i18n/zh/shell").default["macroStop"], options?: Record<string, number | string>) =>
    i18n.t(`shell:macroStop.${key}`, options);
  if (!entry) {
    const unconfirmed = timeline?.rows.filter(row => row.state === "cancelled" && !row.shutdownConfirmed) ?? [];
    if (!unconfirmed.length) return null;
    const pending = unconfirmed.filter(row => ACTIVE_TASK_STATUS.has(row.status)).length;
    return pending ? { phase: "in-progress", label: t("stopping"), detail: t("pendingCount", { count: pending }) }
      : { phase: "unconfirmed-stop", label: t("unknown"), detail: t("uncertainCount", { count: unconfirmed.length }) };
  }
  if (entry.phase === "refused") {
    return { phase: "refused", label: t("refused"), detail: (typeof entry.error === "function" ? entry.error() : entry.error) || t("refusedDetail") };
  }
  if (entry.phase === "unknown") {
    return {
      phase: "unknown",
      label: t("unknown"),
      detail: t("unknownDetail"),
    };
  }
  if (entry.phase === "stopping") {
    return { phase: "stopping", label: t("stopping"), detail: t("stoppingDetail") };
  }
  const stopped = entry.result?.results.filter(result => result.stopped) ?? [];
  if (stopped.length === 0) {
    return { phase: "acknowledged", label: t("noOp"), detail: t("noOpDetail") };
  }
  if (!timeline) {
    return { phase: "unconfirmed-stop", label: t("unknown"), detail: t("incomplete") };
  }
  const complete = timeline.scopeComplete && !timeline.filtered && !timeline.truncated.rows;
  const rowsById = new Map(timeline.rows.map(row => [row.runId, row]));
  let pending = 0, uncertain = 0;
  for (const { runId } of stopped) {
    const row = rowsById.get(runId);
    if (!row) { uncertain += 1; continue; }
    if (row.state !== "cancelled") pending += 1;
    else if (!row.shutdownConfirmed) {
      if (ACTIVE_TASK_STATUS.has(row.status) || ACTIVE_ROW_STATE.has(row.state)) pending += 1;
      else uncertain += 1;
    }
  }
  if (pending > 0) {
    return { phase: "in-progress", label: t("stopping"), detail: t("pendingCount", { count: pending }) };
  }
  if (uncertain > 0) {
    return { phase: "unconfirmed-stop", label: t("unknown"), detail: t("uncertainCount", { count: uncertain }) };
  }
  if (!complete) {
    return {
      phase: "unconfirmed-stop",
      label: t("unknown"),
      detail: t("incompleteDetail"),
    };
  }
  const roots = stopped.filter(result => rowsById.get(result.runId)?.kind === "micro-task").length;
  const helpers = stopped.length - roots;
  return { phase: "stopped", label: t("stopped"), detail: t("stoppedCount", {
    roots: t("stoppedRoots", { count: roots }), helpers: t("stoppedHelpers", { count: helpers }),
  }) };
}

export function useMacroTaskStop(api: ConsoleApi, snapshot: Snapshot, writesAvailable: boolean, authority: AuthorityLatch | undefined, refresh: () => Promise<unknown>) {
  useTranslation("shell");
  const [entries, setEntries] = useState<Map<string, MacroTaskStopEntry>>(() => new Map());
  const [confirming, setConfirming] = useState<{ summary: MacroTaskSummary; scope: StopScope } | null>(null);
  // One in-flight stop per macroTask: a second confirmation of the SAME
  // macroTask is ignored while its reply is pending, but different macroTasks
  // navigate and stop independently.
  const inFlight = useRef<Set<string>>(new Set());

  const writable = writesAvailable && (authority ? authority.writable(snapshot) : sessionCanWrite(snapshot));

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
        patch(summary.macroTaskId, { phase: "unknown", error: () => errorText(reason) });
      } else if (isSessionExpiredRefusal(reason) && replay) {
        // A replay that is definitely refused proves only that THIS request
        // was denied; the earlier unknown attempt stays unknown.
        patch(macroTaskId, {
          phase: "unknown",
          error: () => `${errorText(reason)} ${i18n.t("shell:macroStop.replayUnknown")}`,
        });
      } else {
        patch(summary.macroTaskId, { phase: "refused", error: () => errorText(reason) });
      }
    } finally {
      inFlight.current.delete(macroTaskId);
    }
  }, [api, patch, refresh, snapshot.csrfToken]);

  /** Step one: the explicit confirmation naming the macroTask and its scope. */
  const requestStop = useCallback((summary: MacroTaskSummary, scope: StopScope) => {
    if (!writable || scope.roots + scope.helpers === 0) return;
    setConfirming({ summary, scope });
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
