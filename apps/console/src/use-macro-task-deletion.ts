import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, isSessionExpiredRefusal, uncertainResponse } from "./api";
import type { ConsoleApi } from "./api";
import { sessionCanWrite } from "./console-session";
import type { AuthorityLatch } from "./console-session";
import type { Snapshot } from "./types";
import { parseMacroTaskDeletionPreview, parseMacroTaskDeletionResult } from "./macro-task-deletion";
import type { ConfirmedMacroTaskDeletion, MacroTaskDeletionPreview, MacroTaskDeletionResult } from "./macro-task-deletion";

type DeletionPhase = "previewing" | "ready" | "preview-failed" | "submitting" | "failed" | "incomplete" | "unknown";
type DeletionState = {
  phase: DeletionPhase;
  preview: MacroTaskDeletionPreview | null;
  errorCode: string | null;
  result: MacroTaskDeletionResult | null;
};
export type MacroTaskDeletionClient = {
  api: ConsoleApi;
  snapshot: Snapshot;
  writesAvailable: boolean;
  authority?: AuthorityLatch;
  onDeleted: (macroTaskId: string, skippedCount: number) => void;
};

/** Target identity, count and confirmation stay fixed across an unresolved retry. */
export function useMacroTaskDeletion(macroTaskId: string, client: MacroTaskDeletionClient) {
  const [state, setState] = useState<DeletionState>({ phase: "previewing", preview: null, errorCode: null, result: null });
  const current = useRef(client);
  current.current = client;
  const mounted = useRef(false), reading = useRef(0), submitting = useRef(false);
  const confirmation = useRef<ConfirmedMacroTaskDeletion | null>(null);
  const completed = useRef(false);
  const canWrite = () => {
    const { snapshot, authority, writesAvailable } = current.current;
    return writesAvailable && (authority ? authority.writable(snapshot) : sessionCanWrite(snapshot));
  };
  const loseAuthority = (error: unknown) => {
    if (isSessionExpiredRefusal(error)) current.current.authority?.lose(current.current.snapshot.consoleSession.id);
  };

  const readPreview = useCallback(async () => {
    const ticket = ++reading.current;
    confirmation.current = null;
    if (!canWrite()) {
      setState({ phase: "preview-failed", preview: null, errorCode: null, result: null });
      return;
    }
    setState({ phase: "previewing", preview: null, errorCode: null, result: null });
    let preview: MacroTaskDeletionPreview;
    try {
      const { api, snapshot } = current.current;
      preview = parseMacroTaskDeletionPreview(macroTaskId,
        await api.command("macro_task_deletion_preview", { macroTaskId }, snapshot.csrfToken));
    } catch (error) {
      if (!mounted.current || ticket !== reading.current) return;
      if (error instanceof ApiError && error.code === "NOT_FOUND") {
        completed.current = true;
        current.current.onDeleted(macroTaskId, 0);
        return;
      }
      loseAuthority(error);
      setState({ phase: "preview-failed", preview: null, errorCode: error instanceof ApiError ? error.code : "NETWORK", result: null });
      return;
    }
    if (!mounted.current || ticket !== reading.current) return;
    if (preview.deletionState === "deleting") confirmation.current = { macroTaskId, microTaskCount: preview.microTaskCount, confirm: true };
    setState({ phase: preview.deletionState === "deleting" ? "incomplete" : "ready", preview, errorCode: null, result: null });
  }, [macroTaskId]);

  useEffect(() => {
    mounted.current = true;
    void readPreview();
    return () => { mounted.current = false; ++reading.current; };
  }, [readPreview]);

  const submit = async () => {
    if (submitting.current || !canWrite()) return;
    if (state.phase === "ready" && state.preview?.blockerTotal === 0) {
      confirmation.current = { macroTaskId, microTaskCount: state.preview.microTaskCount, confirm: true };
    } else if (state.phase !== "unknown" && state.phase !== "incomplete") return;
    const confirmed = confirmation.current!;
    submitting.current = true;
    setState({ ...state, phase: "submitting", errorCode: null });
    let result: MacroTaskDeletionResult;
    try {
      const { api, snapshot } = current.current;
      result = parseMacroTaskDeletionResult(confirmed,
        await api.command("macro_task_delete", confirmed, snapshot.csrfToken));
    } catch (error) {
      if (!mounted.current) return;
      loseAuthority(error);
      if (error instanceof ApiError && error.code === "NOT_FOUND") {
        completed.current = true;
        current.current.onDeleted(macroTaskId, 0);
        return;
      }
      const unresolved = state.phase === "unknown" || state.phase === "incomplete";
      setState({ ...state, phase: uncertainResponse(error) ? "unknown" : unresolved ? state.phase : "failed",
        errorCode: error instanceof ApiError ? error.code : "NETWORK" });
      return;
    } finally {
      submitting.current = false;
    }
    if (result.state === "deleted") {
      // The confirmed deletion remains true even if its dialog was closed by
      // navigation. The parent removes only this target, using its live URL.
      completed.current = true;
      current.current.onDeleted(macroTaskId, result.skipped?.length ?? 0);
    } else if (mounted.current) setState({ ...state, phase: "incomplete", errorCode: null, result });
  };

  return { ...state, writable: canWrite(), completed, readPreview, submit };
}
