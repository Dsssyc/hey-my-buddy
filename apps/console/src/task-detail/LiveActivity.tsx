import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { StateMark } from "@/components/ui/state-mark";
import { formatRelative } from "../i18n/format";
import type { Activity } from "./types";

/** The row that stays under a running turn: what it is doing and on what ground that is known, and when it last said so. */
export function LiveActivity({ activity, stuck, nowMs, className }: { activity: Activity | null; stuck: boolean; nowMs: number; className?: string }) {
  const { t } = useTranslation("taskDetail");
  const heartbeat = !activity || activity.basis === "phase" || activity.basis === "unknown";
  const phase = heartbeat ? t("activity.unknown") : t(`activity.${activity.phase}`);
  const doing = !heartbeat && ["starting", "waiting-model", "streaming-model", "tool-running", "finishing"].includes(activity.phase);
  const at = activity?.observedAt ? Date.parse(activity.observedAt) : NaN;
  return <div data-slot="detail-live" className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 text-xs", className)}>
    <StateMark kind={doing ? "live" : "idle"} />
    <span className={cn(doing ? "text-ink state-breathe" : "text-ink3")}>{phase}</span>
    {activity?.toolName && !heartbeat && <span className="min-w-0 font-mono text-ink2 wrap-anywhere">{activity.toolName}</span>}
    <span className={cn("ml-auto whitespace-nowrap tabular-nums", stuck ? "text-hand" : "text-ink3")}>
      {Number.isFinite(at) ? t("activity.reported", { when: formatRelative(at, nowMs) }) : t("activity.notReported")}
    </span>
  </div>;
}
