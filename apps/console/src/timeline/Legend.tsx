import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { ConclusionMark } from "./ConclusionMark";
import { buddyColorPrefix } from "./buddyColors";
import { ClusterMark } from "./ClusterMark";

/** A compact key for the timeline's marks and their meanings. */
export function Legend() {
  const { t } = useTranslation("tasks");
  const conclusion = (kind: "accepted" | "failed") => <svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18" className="shrink-0"><ConclusionMark kind={kind} x={9} y={9}/></svg>;
  const items: { key: string; label: string; meaning: string; mark: ReactNode }[] = [
    { key: "execution", label: t("execution"), meaning: t("legendMeaning.execution"), mark: <span aria-hidden="true" className="flex h-2 w-6 border border-line2">{buddyColorPrefix(3).map((color, index) => <span key={index} data-slot="legend-execution-color" className="flex-1" style={{ backgroundColor: color }}/>)}</span> },
    { key: "wait", label: t("wait"), meaning: t("legendMeaning.wait"), mark: <span aria-hidden="true" className="w-4 border-t border-ink3"/> },
    { key: "waiting", label: t("waiting"), meaning: t("legendMeaning.waiting"), mark: <span aria-hidden="true" className="w-4 border-t-2 border-dashed border-hand-mark"/> },
    { key: "silent", label: t("drawing.silentLegend"), meaning: t("legendMeaning.silent"), mark: <span aria-hidden="true" className="h-2 w-4 border border-hand-mark bg-hand-wash"/> },
    { key: "inquiry", label: t("drawing.inquiry"), meaning: t("legendMeaning.inquiry"), mark: <span aria-hidden="true" className="size-2 rotate-45 border border-ink2"/> },
    { key: "event", label: t("drawing.eventLegend"), meaning: t("legendMeaning.event"), mark: <span aria-hidden="true" className="size-1.5 rounded-full bg-past"/> },
    { key: "accepted", label: t("drawing.acceptedLegend"), meaning: t("legendMeaning.accepted"), mark: conclusion("accepted") },
    { key: "failed", label: t("drawing.failedConclusionLegend"), meaning: t("legendMeaning.failed"), mark: conclusion("failed") },
    { key: "cluster", label: t("drawing.clusterLegend"), meaning: t("legendMeaning.cluster"), mark: <svg aria-hidden="true" width="20" height="20" viewBox="0 0 20 20" className="shrink-0"><ClusterMark count={2} x={10} y={10}/></svg> },
    { key: "idle", label: t("drawing.idleLegend"), meaning: t("legendMeaning.idle"), mark: <span aria-hidden="true" className="h-3 w-4 bg-[repeating-linear-gradient(135deg,var(--sunk)_0_3px,var(--line)_3px_4px)]"/> },
  ];
  return <Popover>
    <PopoverTrigger asChild><button type="button" data-slot="legend-trigger" className="shrink-0 border-b border-dotted border-ink3 text-ink2 hover:border-ink hover:text-ink focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("legendLabel")}</button></PopoverTrigger>
    <PopoverContent data-slot="timeline-legend" role="dialog" aria-label={t("legendLabel")} align="end" sideOffset={8} className="w-[22rem] max-w-[calc(100vw-2rem)] max-h-(--radix-popover-content-available-height) overflow-y-auto rounded-none p-4">
      <div className="grid gap-3">{items.map(({ key, label, meaning, mark }) =>
        <div key={key} data-slot="legend-item" data-kind={key} className="grid grid-cols-[1.5rem_minmax(0,1fr)] items-start gap-2.5">
          <span className="flex h-5 items-center justify-center">{mark}</span>
          <span className="min-w-0"><span className="block font-medium text-ink">{label}</span><span className="block text-ink2">{meaning}</span></span>
        </div>)}</div>
    </PopoverContent>
  </Popover>;
}
