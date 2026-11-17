import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { ListFilter } from "lucide-react";
import { Button } from "../components/ui/button";
import { Hint } from "../components/hint";
import { formatNumber } from "../i18n/format";

/** The same compact header sits beside the axis, or above it on narrow screens. */
export function RowFilter({ unfinished, count, total, onChange }: { unfinished: boolean; count: number; total: number; onChange: (value: boolean) => void }) {
  const { t } = useTranslation("tasks");
  return <div data-slot="timeline-row-filter" className="flex h-9 items-center justify-between gap-2 bg-ruler px-3 text-xs text-ink3">
    <span className="flex items-center gap-1 whitespace-nowrap"><span>{t("microTasks")}</span><span data-slot="timeline-row-count" className="tabular-nums">{unfinished ? `${formatNumber(count)} / ${formatNumber(total)}` : formatNumber(total)}</span></span>
    <div data-zoom-exclude className="shrink-0">
      <Hint text={t("unfinishedOnly")}><Button type="button" variant="ghost" size="icon-sm" aria-pressed={unfinished} aria-label={t("unfinishedOnly")}
        onClick={() => onChange(!unfinished)} className={cn(unfinished && "bg-selection text-ink!")}>
        <ListFilter aria-hidden="true"/>
      </Button></Hint>
    </div>
  </div>;
}
