import { useRef } from "react";
import type { RefObject } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "./components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./components/ui/dialog";
import { ScrollArea } from "./components/ui/scroll-area";
import { StateMark } from "./components/ui/state-mark";
import { displayTitle } from "./macro-task-display";
import { formatNumber } from "./i18n/format";
import { byteText } from "./settings/storage-display";
import type { MacroTaskSummary } from "./macro-task-types";
import type { DeletionBlocker, DeletionBlockerKind } from "./macro-task-deletion";
import { useMacroTaskDeletion } from "./use-macro-task-deletion";
import type { MacroTaskDeletionClient } from "./use-macro-task-deletion";

function blockerKind(blocker: DeletionBlocker): DeletionBlockerKind {
  return blocker.reason.code.slice("macro-task-deletion.".length) as DeletionBlockerKind;
}
function blockerTone(blocker: DeletionBlocker) {
  const kind = blockerKind(blocker);
  return ["awaiting-host", "review", "stop-unconfirmed"].includes(kind) ? "text-hand" : kind === "queued" ? "text-ink3" : "text-live";
}

function DeletionBlockers({ blockers }: { blockers: DeletionBlocker[] }) {
  const { t } = useTranslation("macroHeader");
  const groups = new Map<string, DeletionBlocker[]>();
  for (const blocker of blockers) groups.set(blocker.taskId, [...groups.get(blocker.taskId) ?? [], blocker]);
  return <ul className="pt-3" data-slot="macro-delete-blockers">
    {[...groups].map(([id, reasons]) => {
      const title = reasons[0].titleSource === "none" ? t("unnamed") : reasons[0].title;
      const hand = reasons.some(reason => ["awaiting-host", "review", "stop-unconfirmed"].includes(blockerKind(reason)));
      return <li key={id} className="flex gap-2.5 border-t py-2.5 last:border-b">
        <StateMark className="mt-1.5" kind={hand ? "hand" : reasons.every(reason => blockerKind(reason) === "queued") ? "idle" : "live"}/>
        <div className="min-w-0 flex-1 wrap-anywhere">
          <div className="flex items-baseline justify-between gap-3">
            <p className="min-w-0 font-medium">{title}</p>
            <span className={`shrink-0 text-xs ${blockerTone(reasons[0])}`}>{t(`deletion.blockers.${blockerKind(reasons[0])}.state`)}</span>
          </div>
          <p className="mt-0.5 text-xs text-ink3">{t(`deletion.blockers.${blockerKind(reasons[0])}.resolution`)}</p>
          {reasons.slice(1).map(reason => <div key={reason.reason.code} className="mt-1">
            <p className={`text-xs ${blockerTone(reason)}`}>{t(`deletion.blockers.${blockerKind(reason)}.state`)}</p>
            <p className="mt-0.5 text-xs text-ink3">{t(`deletion.blockers.${blockerKind(reason)}.resolution`)}</p>
          </div>)}
        </div>
      </li>;
    })}
  </ul>;
}

export function MacroTaskDeleteDialog({ summary, open, returnFocus, onClose, ...client }: MacroTaskDeletionClient & {
  summary: MacroTaskSummary;
  open: boolean;
  returnFocus: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}) {
  const { t } = useTranslation("macroHeader");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const deletion = useMacroTaskDeletion(summary.macroTaskId, client);
  const { phase, preview } = deletion;
  const busy = phase === "submitting";
  const blocked = !!preview?.blockerTotal;
  const unresolved = phase === "incomplete" || phase === "unknown";
  const ready = phase === "ready" && !blocked;
  const title = summary.titleSource === "none" ? t("unnamed") : displayTitle(summary.titleSource, summary.title).text;
  const incomplete = preview && preview.blockerTotal > preview.blockers.length;
  const taskCount = new Set(preview?.blockers.map(blocker => blocker.taskId)).size;
  const errorKey = `deletion.errors.${deletion.errorCode}`;
  const error = deletion.errorCode ? t(errorKey, { defaultValue: t("deletion.errors.other", { code: deletion.errorCode }) }) : null;
  const failure = deletion.result?.state === "deleting" ? deletion.result : null;
  const contentScope = (value: { count: number | null; bytes: number | null }, kind: "artifactCount" | "sessionCount") => t("deletion.contentScope", {
    quantity: value.count === null ? t("deletion.unknownCount") : t(`deletion.${kind}`, { n: formatNumber(value.count) }),
    size: value.bytes === null ? t("deletion.unknownBytes") : t("deletion.totalBytes", { bytes: byteText(value.bytes) }),
  });
  return <Dialog open={open} onOpenChange={value => { if (!value && !busy) onClose(); }}>
    <DialogContent data-slot="macro-delete-dialog" className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 rounded-sm p-0 text-sm sm:max-w-[480px]"
      onOpenAutoFocus={event => { event.preventDefault(); cancelRef.current?.focus(); }}
      onCloseAutoFocus={event => { event.preventDefault(); if (!deletion.completed.current) returnFocus.current?.focus(); }}
      onEscapeKeyDown={event => { if (busy) event.preventDefault(); }}
      onInteractOutside={event => { if (busy) event.preventDefault(); }}>
      <div className="shrink-0 px-6 pt-5">
        <DialogTitle className="text-base leading-normal wrap-anywhere">{t(blocked ? "deletion.blockedTitle" : "deletion.title", { title })}</DialogTitle>
        <DialogDescription className="pt-2.5 leading-relaxed text-ink2">{blocked
          ? incomplete ? t("deletion.blockedPartial") : t("deletion.blocked", { n: formatNumber(taskCount) })
          : t("deletion.lead")}</DialogDescription>
      </div>
      <ScrollArea className="min-h-0 shrink" viewportProps={{ tabIndex: 0, "aria-label": t("deletion.scope"), "aria-busy": phase === "previewing" || busy }}>
        <div className="px-6">
          {phase === "previewing" && <p role="status" className="pt-3 text-ink2">{t("deletion.loading")}</p>}
          {preview && (blocked ? <>
            <DeletionBlockers blockers={preview.blockers}/>
            {incomplete && <p className="pt-3 text-xs text-ink3">{t("deletion.truncated", { shown: formatNumber(preview.blockers.length), total: formatNumber(preview.blockerTotal) })}</p>}
          </> : <>
            <dl data-slot="macro-delete-scope" className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4.5 gap-y-1.5 pt-3.5 tabular-nums [&_dd]:wrap-anywhere [&_dt]:text-ink3">
              <dt>{t("deletion.microTasks")}</dt><dd>{t("deletion.microTaskCount", { n: formatNumber(preview.microTaskCount), unconcluded: formatNumber(preview.unconcludedFailureCount) })}</dd>
              <dt>{t("deletion.artifacts")}</dt><dd>{contentScope(preview.scope.artifacts, "artifactCount")}</dd>
              <dt>{t("deletion.sessions")}</dt><dd>{contentScope(preview.scope.sessions, "sessionCount")}</dd>
              <dt>{t("deletion.workspaces")}</dt><dd>{t("deletion.workspaceScope", {
                checkouts: preview.scope.checkouts === null ? t("deletion.unknownCheckouts") : t("deletion.checkoutCount", { n: formatNumber(preview.scope.checkouts) }),
                gitRefs: preview.scope.gitRefs === null ? t("deletion.unknownGitRefs") : t("deletion.gitRefCount", { n: formatNumber(preview.scope.gitRefs) }),
              })}</dd>
            </dl>
            <p className="pt-3 text-xs leading-relaxed text-ink3">{t("deletion.backup")}</p>
          </>)}
          {busy && <p role="status" className="pt-3 text-live">{t("deletion.submitting")}</p>}
          {unresolved && <p role="status" className="pt-3 text-hand">{t(phase === "unknown" ? "deletion.unknown" : "deletion.incomplete")}</p>}
          {failure && phase !== "submitting" && <p className="pt-2 text-xs text-ink2">{t("deletion.physicalFailure", { kind: t(`deletion.objects.${failure.objectKind}`), code: failure.reason.params.errorCode })}</p>}
          {error && <p role="alert" className="pt-3 text-fail">{error}</p>}
          {!deletion.writable && <p role="alert" className="pt-3 text-fail">{t("deletion.unavailable")}</p>}
        </div>
      </ScrollArea>
      <div data-slot="macro-delete-actions" className="flex shrink-0 flex-wrap justify-end gap-2 px-6 py-5">
        <Button ref={cancelRef} variant="outline" aria-disabled={busy} onClick={() => { if (!busy) onClose(); }}>{t(blocked ? "deletion.acknowledge" : "deletion.cancel")}</Button>
        {(phase === "preview-failed" || phase === "failed") && <Button variant="outline" aria-disabled={!deletion.writable || busy} onClick={() => { if (deletion.writable && !busy) void deletion.readPreview(); }}>{t("deletion.retryPreview")}</Button>}
        {(ready || unresolved || busy) && <Button variant="destructive" aria-disabled={!deletion.writable || busy} onClick={() => void deletion.submit()}>{t(busy ? "deletion.submitting" : unresolved ? "deletion.retryDelete" : "deletion.confirm")}</Button>}
      </div>
    </DialogContent>
  </Dialog>;
}
