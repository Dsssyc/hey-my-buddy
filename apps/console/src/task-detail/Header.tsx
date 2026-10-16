import { cn } from "cn";
import { ChevronLeft, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { formatClock, formatDuration, formatMoment } from "../i18n/format";
import { taskTitle } from "../task-state";
import type { TaskView, Workflow } from "./types";

type Word = { key: "running" | "queued" | "host" | "lead" | "review" | "accepted" | "done" | "ended" | "failed"; mark: "live" | "hand" | "done" | "fail" | "idle" };

/** The one word a micro task's state has, and the mark that goes with it. */
function stateWord(view: TaskView, workflow: Workflow | null): Word {
  const { task } = view;
  if (task.status === "queued") return { key: "queued", mark: "idle" };
  switch (workflow?.state) {
    case "awaiting-host": return { key: view.lead ? "lead" : "host", mark: "hand" };
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
 * The head of the detail, in one line: the way back, what the micro task is and
 * in what state, when it began and how long it has gone on, and the one control
 * that reads it again. Reading is all it does; nothing here changes the board.
 */
export function Header({ view, workflow, turns, endMs, nowMs, verifiedAtMs, error, busy, backTo, onBack, onRefresh }: {
  view: TaskView; workflow: Workflow | null; turns: number | null; endMs: number; nowMs: number;
  verifiedAtMs: number | null; error: string; busy: boolean; backTo: "timeline" | "records"; onBack: () => void; onRefresh: () => void;
}) {
  const { t } = useTranslation("taskDetail");
  const shell = useTranslation("shell").t;
  const title = taskTitle(view.task);
  const text = title.source === "none" ? t("untitled") : title.text;
  const word = stateWord(view, workflow);
  const reminders = view.attempt ?? { overdue: false, possiblyStuck: false };
  const start = Date.parse(view.task.createdAt);
  return <header data-slot="detail-header" className="shrink-0 border-b bg-card">
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 px-4 py-3 sm:px-6">
      <Button variant="outline" size="sm" onClick={onBack} className="mr-1.5"><ChevronLeft strokeWidth={1.5} aria-hidden="true" />{t(`back.${backTo}`)}</Button>
      <div className="flex min-w-0 flex-1 items-center gap-2.5 max-[760px]:basis-[12rem]">
        <StateMark kind={word.mark} />
        <Hint text={title.source === "task" ? t("title.fromTask", { title: title.fullText }) : title.source === "none" ? undefined : title.fullText}>
          <h2 tabIndex={0} className="min-w-0 truncate rounded-sm text-base font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{text}</h2>
        </Hint>
        <span className={cn("shrink-0 text-xs", word.mark === "hand" ? "text-hand" : word.mark === "live" ? "text-live" : word.mark === "fail" ? "text-fail" : "text-ink2")}>{t(`state.${word.key}`)}</span>
        {reminders.overdue && <span className="shrink-0 text-xs text-hand">{shell("reminders.overdue")}</span>}
        {reminders.possiblyStuck && <span className="shrink-0 text-xs text-hand">{shell("reminders.possiblyStuck")}</span>}
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-x-4">
        <dl className="flex gap-x-4 text-xs tabular-nums">
          <div className="flex gap-1.5"><dt className="text-ink3">{t("header.started")}</dt><dd>{Number.isFinite(start) ? formatMoment(start, nowMs) : t("unrecorded")}</dd></div>
          {Number.isFinite(start) && <div className="flex gap-1.5"><dt className="text-ink3">{t("header.elapsed")}</dt><dd>{formatDuration(Math.max(0, endMs - start))}</dd></div>}
          {turns !== null && <div className="flex gap-1.5"><dt className="text-ink3">{t("header.turns")}</dt><dd>{turns}</dd></div>}
        </dl>
        <Hint text={verifiedAtMs !== null ? t("refresh.verified", { time: formatClock(verifiedAtMs) }) : t("refresh.unverified")}>
          <Button variant="outline" size="icon-sm" aria-label={t("refresh.label")} aria-busy={busy} aria-disabled={busy} onClick={onRefresh}>
            <RefreshCw strokeWidth={1.5} aria-hidden="true" className={cn(busy && "animate-spin motion-reduce:animate-none")} />
          </Button>
        </Hint>
      </div>
    </div>
    {error && <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t bg-fail-wash px-4 py-2 text-fail sm:px-6">
      <StateMark kind="fail" /><span className="min-w-0 wrap-anywhere">{error}</span>
      <Button variant="outline" size="sm" aria-disabled={busy} onClick={onRefresh}>{t("retry")}</Button>
    </div>}
  </header>;
}
