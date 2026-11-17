import i18n from "i18next";
import { useTranslation } from "react-i18next";
import { createElement, useEffect, useRef, useState, type ReactNode } from "react";
import { z } from "zod";
import { ApiError, errorText, isSessionExpiredRefusal } from "../api";
import type { ConsoleApi } from "../api";
import { isReason } from "../reason";
import type { Reason } from "../reason";
import { reasonText } from "../i18n/reason";
import { ReasonMessage } from "../i18n/ReasonMessage";
import type { Model, ProfileRun, Snapshot } from "../types";
import type { Editor } from "../use-editor";

const startReply = z.discriminatedUnion("status", [
  z.object({ status: z.literal("unavailable"), reason: z.custom<Reason>(isReason) }),
  z.object({ status: z.literal("running"), profileRunId: z.string().min(1), runId: z.string().min(1), buddyId: z.string().min(1), usesQuota: z.literal(true).optional() }),
]);
export type ProfileNotice = { id: number; model: string; run?: ProfileRun; message?: () => ReactNode };
export const PROFILE_NOTICE_MS = 6000;

/** Observe transitions in recorded runs, with one short-lived notice per model. */
export function useProfileActions({ snapshot, editor, api, refresh, mutationsAvailable }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const [busy, setBusy] = useState(false);
  const [notices, setNotices] = useState<ProfileNotice[]>([]);
  const sequence = useRef(0);
  const previous = useRef(new Map(snapshot.models.map(model => [model.modelId, model.latestProfileRun])));
  const tracked = useRef(new Set<string>());
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const running = useRef(false);
  const gate = useRef({ writable: editor.sessionWritable, connected: mutationsAvailable });
  gate.current = { writable: editor.sessionWritable, connected: mutationsAvailable };
  useEffect(() => () => timers.current.forEach(clearTimeout), []);
  function notice(model: string, detail: Pick<ProfileNotice, "run" | "message">) {
    const id = ++sequence.current;
    setNotices(rows => [...rows, { id, model, ...detail }]);
    timers.current.push(setTimeout(() => setNotices(rows => rows.filter(row => row.id !== id)), PROFILE_NOTICE_MS));
  }
  useEffect(() => {
    for (const model of snapshot.models) {
      const run = model.latestProfileRun;
      const before = previous.current.get(model.modelId);
      if (run && run.state !== "running" && (before?.state === "running" && before.profileRunId === run.profileRunId || tracked.current.has(run.profileRunId))) {
        notice(model.displayName, { run });
        tracked.current.delete(run.profileRunId);
      }
    }
    previous.current = new Map(snapshot.models.map(model => [model.modelId, model.latestProfileRun]));
  }, [snapshot.models]);
  const generalLock = !editor.sessionWritable ? i18n.t("buddies:pane.locked.session") : !mutationsAvailable ? i18n.t("buddies:models.refused")
    : !editor.editing || busy ? i18n.t("buddies:pane.locked.saving") : editor.changeCount ? i18n.t("buddies:modelProfile.saveFirst") : undefined;
  const profileLock = generalLock ?? (!snapshot.configuration?.profileBuddyId ? i18n.t("buddies:modelProfile.noBuddy")
    : snapshot.configuration.profileUnavailableReason ? createElement(ReasonMessage, { reason: snapshot.configuration.profileUnavailableReason }) : undefined);
  const profileLockDescription = typeof profileLock === "string" ? profileLock
    : snapshot.configuration?.profileUnavailableReason ? reasonText(snapshot.configuration.profileUnavailableReason) : undefined;
  const maintenanceLock = generalLock ?? (!snapshot.configuration?.maintenanceBuddyId ? t("evaluation.noMaintenanceBuddy")
    : undefined);
  async function build(models: Model[]) {
    if (profileLock || running.current) return;
    running.current = true; setBusy(true); editor.setAuxiliaryBusy(true);
    try {
      for (const model of models) {
        if (!gate.current.writable || !gate.current.connected) break;
        try {
          const parsed = startReply.safeParse(await api.command("profile_start", { modelId: model.modelId }, snapshot.csrfToken));
          if (!parsed.success) throw new ApiError("INVALID_RESPONSE", () => i18n.t("buddies:modelProfile.invalidReply"));
          if (parsed.data.status === "running") tracked.current.add(parsed.data.profileRunId);
          else { const reason = parsed.data.reason; notice(model.displayName, { message: () => createElement(ReasonMessage, { reason }) }); }
        } catch (failure) {
          notice(model.displayName, { message: () => errorText(failure) });
          if (isSessionExpiredRefusal(failure)) break;
        }
      }
      if (!await refresh()) notice("", { message: () => i18n.t("buddies:modelProfile.readFailed") });
    } catch (failure) { notice("", { message: () => errorText(failure) }); }
    finally { running.current = false; setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  async function maintain() {
    if (maintenanceLock || running.current) return;
    running.current = true; setBusy(true); editor.setAuxiliaryBusy(true);
    try {
      await api.command("maintenance_start", {}, snapshot.csrfToken);
      if (!await refresh()) notice("", { message: () => i18n.t("buddies:maintenance.readFailed") });
    } catch (failure) { notice("", { message: () => errorText(failure) }); }
    finally { running.current = false; setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  return { busy, notices, profileLock, profileLockDescription, maintenanceLock, build, maintain };
}
