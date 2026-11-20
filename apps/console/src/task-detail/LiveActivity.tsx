import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { StateMark } from "@/components/ui/state-mark";
import { formatRelative } from "../i18n/format";
import type { Activity } from "./types";

/** The row that stays under a running turn: what it is doing and on what ground that is known, and when it last said so. */
export function LiveActivity({ activity, stuck, nowMs, footer, className }: { activity: Activity | null; stuck: boolean; nowMs: number; footer?: boolean; className?: string }) {
  const { t } = useTranslation("taskDetail");
  const heartbeat = !activity || activity.basis === "phase" || activity.basis === "unknown";
  const phase = heartbeat ? t("activity.unknown") : t(`activity.${activity.phase}`);
  const doing = !heartbeat && ["starting", "waiting-model", "streaming-model", "tool-running", "finishing"].includes(activity.phase);
  const at = activity?.observedAt ? Date.parse(activity.observedAt) : NaN;
  return <div data-slot="detail-live" className={cn("flex min-w-0 items-center gap-2 text-xs", footer && "border-t border-line bg-card px-6 py-2", className)}>
    <StateMark kind={doing ? "live" : "idle"} />
    <span className={cn("min-w-0 truncate", doing ? "text-ink state-breathe" : "text-ink3")}>{phase}</span>
    {activity?.toolName && !heartbeat && <span className={cn("min-w-0 truncate font-mono text-ink2", footer && "border border-line bg-sunk px-1.5 py-0.5 text-[11.5px] leading-none")}>{activity.toolName}</span>}
    <span className={cn("ml-auto shrink-0 whitespace-nowrap tabular-nums", stuck ? "text-hand" : "text-ink3")}>
      {Number.isFinite(at) ? t("activity.reported", { when: formatRelative(at, nowMs) }) : t("activity.notReported")}
    </span>
  </div>;
}
