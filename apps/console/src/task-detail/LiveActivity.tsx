import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
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
  const ground = activity ? [
    t(`activity.basis.${activity.basis}`),
    activity.phase !== "unknown" && heartbeat ? t(`activity.${activity.phase}`) : null,
    activity.lastToolAt ? t("activity.lastTool", { when: formatRelative(Date.parse(activity.lastToolAt), nowMs) }) : null,
    activity.lastNativeAt ? t("activity.lastNative", { when: formatRelative(Date.parse(activity.lastNativeAt), nowMs) }) : null,
    activity.eventSeq !== null ? t("activity.event", { n: activity.eventSeq }) : null,
  ].filter(Boolean).join(" · ") : t("activity.basis.unknown");
  return <div data-slot="detail-live" className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 text-xs", className)}>
    <StateMark kind={doing ? "live" : "idle"} />
    <Hint text={ground}><span tabIndex={0} className={cn("rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50", doing ? "text-ink state-breathe" : "text-ink3")}>{phase}</span></Hint>
    {activity?.toolName && !heartbeat && <span className="min-w-0 font-mono text-ink2 wrap-anywhere">{activity.toolName}</span>}
    <span className={cn("ml-auto whitespace-nowrap tabular-nums", stuck ? "text-hand" : "text-ink3")}>
      {Number.isFinite(at) ? t("activity.reported", { when: formatRelative(at, nowMs) }) : t("activity.notReported")}
    </span>
  </div>;
}
