import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "i18next";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, isSessionExpiredRefusal, uncertainResponse } from "./api";
import type { Snapshot } from "./types";
import { workActionReplySchema } from "./macro-task-types";
import type { MacroTaskSummary, WorkAction } from "./macro-task-types";
import type { AuthorityLatch } from "./console-session";
import { LOGIN_EXPIRED_ACTION_REFUSAL, sessionCanWrite } from "./console-session";

export type WorkEntry = {
  action: WorkAction; commandId: string;
  phase: "pending" | "unknown" | "acknowledged" | "refused";
  error: (() => string) | null;
};
export type WorkEntries = Partial<Record<WorkAction, WorkEntry>>;

/** Only unresolved or refused commands add text; successful receipts add none. */
export function workFeedback(entry: WorkEntry): string | null {
  if (entry.phase === "unknown") return i18n.t(`shell:macroWork.${entry.action}Unknown`);
  if (entry.phase === "refused") return entry.error!();
  return null;
}

/** Validate each action's sole current receipt at the process boundary. */
export function parseWorkReply(macroTaskId: string, value: unknown) {
  const parsed = workActionReplySchema.safeParse(value);
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
    patch(id, entry.action, { ...entry, phase: entry.phase === "unknown" ? "unknown" : "pending", error: null });
    let acknowledged = false;
    try {
      parseWorkReply(id, await api.command(`macro_task_${entry.action}`, {
        macroTaskId: id, commandId: entry.commandId,
      }, snapshot.csrfToken));
      patch(id, entry.action, { ...entry, phase: "acknowledged", error: null });
      acknowledged = true;
    } catch (reason) {
      const unknown = uncertainResponse(reason) || entry.phase === "unknown" && isSessionExpiredRefusal(reason);
      patch(id, entry.action, { ...entry, phase: unknown ? "unknown" : "refused", error: () => errorText(reason) });
    } finally {
      inFlight.current.delete(id);
      if (entry.action === "end") setConfirming(previous => previous?.macroTaskId === id ? null : previous);
    }
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
      patch(summary.macroTaskId, action, { action, commandId: "", phase: "refused", error: LOGIN_EXPIRED_ACTION_REFUSAL });
      if (fresh && action === "end") setConfirming(null);
      return;
    }
    const reusable = !fresh && existing && existing.commandId !== "" && existing.phase === "unknown";
    const entry: WorkEntry = reusable ? existing : { action, commandId: crypto.randomUUID(), phase: "pending", error: null };
    void dispatch(summary, entry);
  }, [dispatch, entries, patch, writable]);

  const requestEnd = useCallback((summary: MacroTaskSummary) => {
    if (writable && summary.counts.unfinished > 0 && !inFlight.current.has(summary.macroTaskId)) setConfirming(summary);
  }, [writable]);
  const confirmEnd = useCallback(() => {
    const target = confirming!;
    run(target, "end", true);
  }, [confirming, run]);
  const busy = (id: string) => inFlight.current.has(id);
  return { entries, confirming, writable, busy, run, requestEnd, confirmEnd, cancelConfirm: () => { if (!confirming || !inFlight.current.has(confirming.macroTaskId)) setConfirming(null); } };
}
