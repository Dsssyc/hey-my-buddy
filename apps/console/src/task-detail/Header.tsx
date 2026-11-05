import { cn } from "cn";
import { useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { ScrollingTitle } from "@/components/ScrollingTitle";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { CopyableIdentifier } from "@/components/CopyableIdentifier";
import { formatDuration } from "../i18n/format";
import { taskStatus, taskTitle } from "../task-state";
import { workflowOwnerText } from "../actor";
import type { Task } from "../types";
import type { TaskView, Workflow } from "./types";

export function DetailBack({ onBack }: { onBack: () => void }) {
  const { t } = useTranslation("taskDetail");
  return <Button variant="ghost" size="icon-sm" data-slot="detail-back" aria-label={t("back")} onClick={onBack}><ArrowLeft aria-hidden="true"/></Button>;
}

export type StateWord = { key: "running" | "stopping" | "unknown" | "queued" | "host" | "lead" | "failedHost" | "cancelledHost" | "failedLead" | "cancelledLead" | "review" | "accepted" | "done" | "ended" | "failed"; mark: "live" | "hand" | "review" | "done" | "fail" | "idle" };

/** The one word a micro task's state has, and the mark that goes with it. */
export function stateWord(task: Task, lead: boolean, workflowState: string | null = task.workflow?.state ?? null, conclusionStatus: string | null = null): StateWord {
  if (taskStatus(task) === "cancelling") return { key: "stopping", mark: "live" };
  if (task.state === "queued") return { key: "queued", mark: "idle" };
  if (workflowState === "failed" || workflowState === "cancelled") {
    if (task.state === "running") return { key: "running", mark: "live" };
    if (task.state === "reconciliation-needed") return { key: "unknown", mark: "live" };
  }
  switch (workflowState) {
    case "awaiting-host": return { key: lead ? "lead" : "host", mark: "hand" };
    case "delivered": return { key: "review", mark: "review" };
    case "accepted": return { key: "accepted", mark: "done" };
    case "failed": return task.lineage?.kind === "helper" ? { key: "failed", mark: "fail" }
      : { key: lead ? "failedLead" : "failedHost", mark: "hand" };
    case "cancelled": return task.lineage?.kind === "helper" ? { key: "ended", mark: "idle" }
      : { key: lead ? "cancelledLead" : "cancelledHost", mark: "hand" };
    case "concluded": return conclusionStatus === "failed" ? { key: "failed", mark: "fail" } : { key: "ended", mark: "idle" };
    case "executing": case "waiting-helpers": case "waiting-owned": return { key: "running", mark: "live" };
    default: break;
  }
  if (task.state === "running") return { key: "running", mark: "live" };
  if (task.state === "failed") return { key: "failed", mark: "fail" };
  return { key: task.state === "completed" ? "done" : "ended", mark: "idle" };
}

/**
 * The head of the detail: what the micro task is,
 * how long it has gone on, and the one control
 * that reads it again. Reading is all it does; nothing here changes the board.
 */
export function Header({ view, workflow, turns, endMs, error, busy, onRefresh, onBack }: {
  view: TaskView; workflow: Workflow | null; turns: number | null; endMs: number;
  error: string; busy: boolean; onRefresh: () => void; onBack: () => void;
}) {
  const { t } = useTranslation("taskDetail");
  const shell = useTranslation("shell").t;
  const [turning, setTurning] = useState(false);
  useEffect(() => {
    if (!turning) return;
    const preference = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const stopForReducedMotion = () => { if (preference?.matches) setTurning(false); };
    stopForReducedMotion();
    preference?.addEventListener("change", stopForReducedMotion);
    return () => preference?.removeEventListener("change", stopForReducedMotion);
  }, [turning]);
  const refreshing = busy || turning;
  const refresh = () => { setTurning(true); onRefresh(); };
  const title = taskTitle(view.task);
  const text = title.source === "none" ? t("untitled") : title.fullText;
  const word = stateWord(view.task, view.lead, workflow?.state, workflow?.hostConclusion?.executionStatus ?? null);
  const reminders = view.attempt ?? { overdue: false, possiblyStuck: false };
  const start = Date.parse(view.task.createdAt);
  // The bar shares the left column header's ground, height and bottom line; side
  // by side it is one line, and only a detail filling the whole width wraps.
  return <header data-slot="detail-header" className="flex shrink-0 flex-col border-b border-line bg-ruler">
    <div className="flex h-9 items-center gap-x-2.5 gap-y-1.5 pl-1.5 pr-4 sm:pr-6 @max-[760px]:h-auto @max-[760px]:flex-wrap @max-[760px]:py-2">
      <div className="flex min-w-0 flex-1 items-center gap-2.5 @max-[760px]:basis-full">
        <DetailBack onBack={onBack}/>
        <ScrollingTitle as="h2" text={text} focusable className="rounded-sm text-base font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"/>
        <CopyableIdentifier value={view.task.runId}/>
      </div>
      <div className="ml-auto flex min-w-0 items-center gap-x-4 @max-[760px]:w-full @max-[760px]:flex-wrap @max-[760px]:gap-2">
        <dl className="flex min-w-0 gap-x-4 gap-y-1 text-xs tabular-nums @max-[760px]:basis-full @max-[760px]:flex-wrap">
          <div className="flex min-w-0 gap-1.5"><dt className="shrink-0 text-ink3">{t("delegation.owner")}</dt><dd className="min-w-0 truncate @max-[760px]:wrap-anywhere">{workflowOwnerText(view.task)}</dd></div>
          <div className="flex gap-1.5"><dt className="text-ink3">{t("header.elapsed")}</dt><dd>{Number.isFinite(start) ? formatDuration(Math.max(0, endMs - start)) : t("unrecorded")}</dd></div>
          {turns !== null && <div className="flex gap-1.5"><dt className="text-ink3">{t("header.turns")}</dt><dd>{turns}</dd></div>}
        </dl>
        <div className="flex shrink-0 items-center gap-2 @max-[760px]:ml-auto">
          <Hint text={word.key === "stopping" && workflow?.shutdown ? t("acceptance.unconfirmed", { n: workflow.shutdown.unconfirmedCount }) : undefined}>
            <span data-slot="detail-status" tabIndex={word.key === "stopping" ? 0 : undefined} className={cn("inline-flex items-center gap-1.5 text-xs whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring", word.mark === "hand" || word.mark === "review" ? "text-hand" : word.mark === "live" ? "text-ink state-breathe" : word.mark === "fail" ? "text-fail" : "text-ink2")}><StateMark kind={word.mark}/>{t(`state.${word.key}`)}</span>
          </Hint>
          {reminders.overdue && <span className="text-xs whitespace-nowrap text-hand">{shell("reminders.overdue")}</span>}
          {reminders.possiblyStuck && <span className="text-xs whitespace-nowrap text-hand">{shell("reminders.possiblyStuck")}</span>}
          <Button variant="ghost" size="icon-sm" aria-label={t("refresh.label")} data-slot="detail-refresh" aria-busy={busy} disabled={refreshing} onClick={refresh}>
            <RefreshCw data-slot="detail-refresh-icon" aria-hidden="true"
              className={cn(turning && "detail-refresh-turn", busy && "motion-reduce:opacity-50")}
              onAnimationIteration={() => { if (!busy) setTurning(false); }} />
          </Button>
        </div>
      </div>
    </div>
    {error && <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t bg-fail-wash px-4 py-2 text-fail sm:px-6">
      <StateMark kind="fail" /><span className="min-w-0 wrap-anywhere">{error}</span>
      <Button variant="outline" size="sm" disabled={refreshing} onClick={refresh}>{t("retry")}</Button>
    </div>}
  </header>;
}
