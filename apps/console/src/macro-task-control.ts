import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "i18next";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, isSessionExpiredRefusal, uncertainResponse } from "./api";
import type { Snapshot } from "./types";
import { workActionReplySchemas } from "./macro-task-types";
import type { MacroTaskEndResult, MacroTaskSummary, WorkAction } from "./macro-task-types";
import type { AuthorityLatch } from "./console-session";
import { LOGIN_EXPIRED_ACTION_REFUSAL, sessionCanWrite } from "./console-session";
import { clockTime } from "./macro-task-display";

export type WorkEntry = {
  action: WorkAction; commandId: string;
  phase: "pending" | "unknown" | "acknowledged" | "refused";
  result: MacroTaskEndResult | null;
  error: (() => string) | null;
};
export type WorkEntries = Partial<Record<WorkAction, WorkEntry>>;
const actions: WorkAction[] = ["pause", "resume", "end"];

/** Stop evidence belongs to the view, never an action receipt. */
export function pauseLabel(paused: MacroTaskSummary["paused"]): string | null {
  return paused === null ? null : paused.shutdownConfirmed
    ? i18n.t("shell:macroWork.paused", { time: clockTime(paused.pausedAt) })
    : i18n.t("shell:macroWork.pausing");
}

export function workFeedback(entry: WorkEntry): string[] | null {
  if (entry.phase === "unknown") return [i18n.t(`shell:macroWork.${entry.action}Unknown`)];
  if (entry.phase === "refused") return [entry.error!()];
  if (entry.action !== "end") return null;
  if (entry.phase === "pending") return [i18n.t("shell:macroWork.ending")];
  let ended = 0, delivered = 0, unconfirmed = 0;
  const errors = new Map<string, number>();
  for (const result of entry.result!.results) {
    if (result.concluded) ++ended;
    else if ("state" in result) ++delivered;
    else if (result.error.code === "SHUTDOWN_UNCONFIRMED") ++unconfirmed;
    else errors.set(result.error.code, (errors.get(result.error.code) ?? 0) + 1);
  }
  return [i18n.t("shell:macroWork.ended", { count: ended }),
    ...(delivered ? [i18n.t("shell:macroWork.delivered", { count: delivered })] : []),
    ...(unconfirmed ? [i18n.t("shell:macroWork.unconfirmed", { count: unconfirmed })] : []),
    ...[...errors].map(([code, count]) => i18n.t("shell:macroWork.notEnded", { count,
      reason: i18n.exists(`common:error.codes.${code}`) ? i18n.t(`common:error.codes.${code}`) : i18n.t("common:error.refused"),
    })),
  ];
}

export function canRetryEnd(entry: WorkEntry | undefined): boolean {
  return !!entry && (entry.phase === "unknown" || entry.phase === "refused"
    || entry.phase === "acknowledged" && entry.result!.results.some(result => !result.concluded));
}

/** Validate each action's sole current receipt at the process boundary. */
export function parseWorkReply(action: WorkAction, macroTaskId: string, value: unknown) {
  const parsed = workActionReplySchemas[action].safeParse(value);
  if (!parsed.success || parsed.data.macroTaskId !== macroTaskId) {
    throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.submit"));
  }
  return parsed.data;
}

/** Retain identities independently: an unresolved pause must survive an end or resume. */
export function useMacroTaskControl(api: ConsoleApi, snapshot: Snapshot, writesAvailable: boolean, authority: AuthorityLatch | undefined, refresh: () => Promise<unknown>) {
  useTranslation("shell");
  const [entries, setEntries] = useState<Map<string, WorkEntries>>(() => new Map());
  const [confirming, setConfirming] = useState<MacroTaskSummary | null>(null);
  const inFlight = useRef(new Set<string>());
  const writable = writesAvailable && (authority ? authority.writable(snapshot) : sessionCanWrite(snapshot));
  const patch = useCallback((id: string, action: WorkAction, entry: WorkEntry) => {
    setEntries(previous => new Map(previous).set(id, { ...previous.get(id), [action]: entry }));
  }, []);

  const dispatch = useCallback(async (summary: MacroTaskSummary, entry: WorkEntry) => {
    const id = summary.macroTaskId;
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    patch(id, entry.action, { ...entry, phase: "pending", error: null });
    let acknowledged = false;
    try {
      const reply = parseWorkReply(entry.action, id, await api.command(`macro_task_${entry.action}`, {
        macroTaskId: id, commandId: entry.commandId,
      }, snapshot.csrfToken));
      patch(id, entry.action, { ...entry, phase: "acknowledged", error: null,
        result: entry.action === "end" ? reply as MacroTaskEndResult : null });
      acknowledged = true;
    } catch (reason) {
      const unknown = uncertainResponse(reason) || entry.phase === "unknown" && isSessionExpiredRefusal(reason);
      patch(id, entry.action, { ...entry, phase: unknown ? "unknown" : "refused", error: () => errorText(reason) });
    } finally { inFlight.current.delete(id); }
    // View reads have their own pending state; they never extend a command lock.
    if (acknowledged) {
      try { await refresh(); } catch { /* Polling retries reads without undoing the receipt. */ }
    }
  }, [api, patch, refresh, snapshot.csrfToken]);

  const run = useCallback((summary: MacroTaskSummary, action: WorkAction, fresh = false) => {
    if (inFlight.current.has(summary.macroTaskId)) return;
    const existing = entries.get(summary.macroTaskId)?.[action];
    if (!writable) {
      if (existing && existing.phase !== "refused") return;
      patch(summary.macroTaskId, action, { action, commandId: "", phase: "refused", result: null, error: LOGIN_EXPIRED_ACTION_REFUSAL });
      return;
    }
    const reusable = !fresh && existing && existing.commandId !== "" && existing.phase === "unknown";
    const entry: WorkEntry = reusable ? existing : { action, commandId: crypto.randomUUID(), phase: "pending", result: null, error: null };
    void dispatch(summary, entry);
  }, [dispatch, entries, patch, writable]);

  const requestEnd = useCallback((summary: MacroTaskSummary) => {
    if (writable && summary.counts.unfinished > 0 && !inFlight.current.has(summary.macroTaskId)) setConfirming(summary);
  }, [writable]);
  const confirmEnd = useCallback(() => {
    const target = confirming!;
    setConfirming(null);
    run(target, "end", true);
  }, [confirming, run]);
  const busy = (id: string) => actions.some(action => entries.get(id)?.[action]?.phase === "pending");
  return { entries, confirming, writable, busy, run, requestEnd, confirmEnd, cancelConfirm: () => setConfirming(null) };
}
