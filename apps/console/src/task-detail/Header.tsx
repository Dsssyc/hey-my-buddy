import { cn } from "cn";
import { ChevronLeft, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { formatClock, formatDuration, formatShortDateTime } from "../i18n/format";
import { taskTitle } from "../task-state";
import type { Task } from "../types";
import { remindersOf } from "./evidence";
import type { Workflow } from "./types";

type Word = { key: "running" | "queued" | "host" | "lead" | "review" | "accepted" | "done" | "ended" | "failed"; mark: "live" | "hand" | "done" | "fail" | "idle" };

/** The one word a micro task's state has, and the mark that goes with it. */
export function stateWord(task: Task, workflow: Workflow | null, lead: boolean): Word {
  if (task.status === "queued") return { key: "queued", mark: "idle" };
  switch (workflow?.state) {
    case "awaiting-host": return { key: lead ? "lead" : "host", mark: "hand" };
    case "delivered": return { key: "review", mark: "hand" };
    case "accepted": return { key: "accepted", mark: "done" };
    case "failed": return { key: "failed", mark: "fail" };
    case "concluded": case "cancelled": return { key: "ended", mark: "idle" };
    case "executing": case "waiting-helpers": case "waiting-owned": return { key: "running", mark: "live" };
    default: break;
  }
  if (["running", "cancelling"].includes(task.status)) return { key: "running", mark: "live" };
  if (task.status === "failed") return { key: "failed", mark: "fail" };
  return { key: task.status === "completed" ? "done" : "ended", mark: "idle" };
}

/**
 * The head of the detail: the way back, what the micro task is and in what
 * state, when it began and how long it has gone on, and the one control that
 * reads it again. Reading is all it does; nothing here changes the board.
 */
export function Header({ task, workflow, lead, turns, endMs, verifiedAtMs, error, backTo, onBack, onRefresh }: {
  task: Task; workflow: Workflow | null; lead: boolean; turns: number | null; endMs: number;
  verifiedAtMs: number | null; error: string; backTo: "timeline" | "records"; onBack: () => void; onRefresh: () => void;
}) {
  const { t } = useTranslation("taskDetail");
  const shell = useTranslation("shell").t;
  const title = taskTitle(task);
  const text = title.source === "none" ? t("untitled") : title.text;
  const word = stateWord(task, workflow, lead);
  const attemptId = task.activeAttemptId ?? "";
  const reminders = remindersOf(task, attemptId);
  const start = Date.parse(task.createdAt);
  const same = new Date(start).toDateString() === new Date().toDateString();
  return <header data-slot="detail-header" className="shrink-0 border-b bg-card">
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 px-4 py-3 sm:px-6">
      <Button variant="outline" size="sm" onClick={onBack} className="mr-1.5"><ChevronLeft strokeWidth={1.5} aria-hidden="true" />{t(`back.${backTo}`)}</Button>
      <StateMark kind={word.mark} />
      <Hint text={title.source === "task" ? t("title.fromTask", { title: title.fullText }) : title.source === "none" ? undefined : title.fullText}>
        <h2 tabIndex={0} className="min-w-0 max-w-full truncate rounded-sm text-base font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{text}</h2>
      </Hint>
      <span className={cn("text-xs", word.mark === "hand" ? "text-hand" : word.mark === "live" ? "text-live" : word.mark === "fail" ? "text-fail" : "text-ink2")}>{t(`state.${word.key}`)}</span>
      {reminders.overdue && <span className="text-xs text-hand">{shell("reminders.overdue")}</span>}
      {reminders.possiblyStuck && <span className="text-xs text-hand">{shell("reminders.possiblyStuck")}</span>}
      <dl className="ml-auto flex flex-wrap gap-x-5 gap-y-1 text-xs tabular-nums">
        <div className="flex gap-1.5"><dt className="text-ink3">{t("header.started")}</dt><dd>{Number.isFinite(start) ? (same ? formatClock(start) : formatShortDateTime(start)) : t("unrecorded")}</dd></div>
        {Number.isFinite(start) && <div className="flex gap-1.5"><dt className="text-ink3">{t("header.elapsed")}</dt><dd>{formatDuration(Math.max(0, endMs - start))}</dd></div>}
        {turns !== null && <div className="flex gap-1.5"><dt className="text-ink3">{t("header.turns")}</dt><dd>{turns}</dd></div>}
      </dl>
      <Hint text={verifiedAtMs !== null ? t("refresh.verified", { time: formatClock(verifiedAtMs) }) : t("refresh.unverified")}>
        <Button variant="outline" size="icon-sm" aria-label={t("refresh.label")} onClick={onRefresh}><RefreshCw strokeWidth={1.5} aria-hidden="true" /></Button>
      </Hint>
    </div>
    {error && <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t bg-fail-wash px-4 py-2 text-fail sm:px-6">
      <StateMark kind="fail" /><span className="min-w-0 wrap-anywhere">{error}</span>
      <Button variant="outline" size="sm" onClick={onRefresh}>{t("retry")}</Button>
    </div>}
  </header>;
}
