import { cn } from "cn";
import { useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { useTextClipped } from "@/components/use-text-clipped";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { CopyableIdentifier } from "@/components/CopyableIdentifier";
import { formatClock, formatDuration, formatMomentMinutes } from "../i18n/format";
import { taskStatus, taskTitle } from "../task-state";
import type { Task } from "../types";
import type { TaskView, Workflow } from "./types";

export function DetailBack({ onBack }: { onBack: () => void }) {
  const { t } = useTranslation("taskDetail");
  return <Hint text={t("back")}><Button variant="ghost" size="icon-sm" data-slot="detail-back" aria-label={t("back")} onClick={onBack}><ArrowLeft aria-hidden="true"/></Button></Hint>;
}

export type StateWord = { key: "running" | "stopping" | "queued" | "host" | "lead" | "review" | "accepted" | "done" | "ended" | "failed"; mark: "live" | "hand" | "done" | "fail" | "idle" };

/** The one word a micro task's state has, and the mark that goes with it. */
export function stateWord(task: Task, lead: boolean, workflowState: string | null = task.workflow?.state ?? null): StateWord {
  if (taskStatus(task) === "cancelling") return { key: "stopping", mark: "live" };
  if (task.state === "queued") return { key: "queued", mark: "idle" };
  switch (workflowState) {
    case "awaiting-host": return { key: lead ? "lead" : "host", mark: "hand" };
    case "delivered": return { key: "review", mark: "hand" };
    case "accepted": return { key: "accepted", mark: "done" };
    case "failed": return { key: "failed", mark: "fail" };
    case "concluded": case "cancelled": return { key: "ended", mark: "idle" };
    case "executing": case "waiting-helpers": case "waiting-owned": return { key: "running", mark: "live" };
    default: break;
  }
  if (task.state === "running") return { key: "running", mark: "live" };
  if (task.state === "failed") return { key: "failed", mark: "fail" };
  return { key: task.state === "completed" ? "done" : "ended", mark: "idle" };
}

/**
 * The head of the detail: what the micro task is, when it began
 * and how long it has gone on, and the one control
 * that reads it again. Reading is all it does; nothing here changes the board.
 */
export function Header({ view, workflow, turns, endMs, nowMs, verifiedAtMs, error, busy, onRefresh, onBack }: {
  view: TaskView; workflow: Workflow | null; turns: number | null; endMs: number; nowMs: number;
  verifiedAtMs: number | null; error: string; busy: boolean; onRefresh: () => void; onBack: () => void;
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
  const text = title.source === "none" ? t("untitled") : title.text;
  const word = stateWord(view.task, view.lead, workflow?.state ?? null);
  const reminders = view.attempt ?? { overdue: false, possiblyStuck: false };
  const start = Date.parse(view.task.createdAt);
  const [titleRef, clipped] = useTextClipped<HTMLHeadingElement>(text);
  const titleHint = title.source === "task" ? t("title.fromTask", { title: title.fullText }) : clipped && title.source !== "none" ? title.fullText : undefined;
  return <header data-slot="detail-header" className="shrink-0 bg-card">
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 py-2 pl-1.5 pr-4 sm:pr-6">
      <div className="flex min-w-0 flex-1 items-center gap-2.5 @max-[650px]:order-1 @max-[650px]:basis-full">
        <DetailBack onBack={onBack}/>
        <Hint text={titleHint}>
          <h2 ref={titleRef} tabIndex={0} className="min-w-0 truncate rounded-sm text-base font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{text}</h2>
        </Hint>
        <CopyableIdentifier value={view.task.runId}/>
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-x-4 @max-[650px]:order-2 @max-[650px]:w-full @max-[650px]:flex-wrap @max-[650px]:gap-2">
        <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums @max-[650px]:basis-full">
          <div className="flex gap-1.5"><dt className="text-ink3">{t("header.started")}</dt><dd>{Number.isFinite(start) ? formatMomentMinutes(start, nowMs) : t("unrecorded")}</dd></div>
          <div className="flex gap-1.5"><dt className="text-ink3">{t("header.elapsed")}</dt><dd>{Number.isFinite(start) ? formatDuration(Math.max(0, endMs - start)) : t("unrecorded")}</dd></div>
          {turns !== null && <div className="flex gap-1.5"><dt className="text-ink3">{t("header.turns")}</dt><dd>{turns}</dd></div>}
        </dl>
        <div className="flex shrink-0 items-center gap-2 @max-[650px]:ml-auto">
          <Hint text={word.key === "stopping" && workflow?.shutdown ? t("acceptance.unconfirmed", { n: workflow.shutdown.unconfirmedCount }) : undefined}>
            <span data-slot="detail-status" tabIndex={word.key === "stopping" ? 0 : undefined} className={cn("text-xs whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring", word.mark === "hand" ? "text-hand" : word.mark === "live" ? "text-ink state-breathe" : word.mark === "fail" ? "text-fail" : "text-ink2")}>{t(`state.${word.key}`)}</span>
          </Hint>
          {reminders.overdue && <span className="text-xs whitespace-nowrap text-hand">{shell("reminders.overdue")}</span>}
          {reminders.possiblyStuck && <span className="text-xs whitespace-nowrap text-hand">{shell("reminders.possiblyStuck")}</span>}
          <Hint text={verifiedAtMs !== null ? t("refresh.verified", { time: formatClock(verifiedAtMs) }) : t("refresh.unverified")}>
            <Button variant="ghost" size="icon-sm" aria-label={t("refresh.label")} data-slot="detail-refresh" aria-busy={busy} disabled={refreshing} onClick={refresh}>
              <RefreshCw data-slot="detail-refresh-icon" aria-hidden="true"
                className={cn(turning && "detail-refresh-turn", busy && "motion-reduce:opacity-50")}
                onAnimationIteration={() => { if (!busy) setTurning(false); }} />
            </Button>
          </Hint>
        </div>
      </div>
    </div>
    {error && <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t bg-fail-wash px-4 py-2 text-fail sm:px-6">
      <StateMark kind="fail" /><span className="min-w-0 wrap-anywhere">{error}</span>
      <Button variant="outline" size="sm" disabled={refreshing} onClick={refresh}>{t("retry")}</Button>
    </div>}
  </header>;
}
