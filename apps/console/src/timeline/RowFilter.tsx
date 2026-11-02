import { useTranslation } from "react-i18next";
import { cn } from "cn";

/** The same compact header sits beside the axis, or above it on narrow screens. */
export function RowFilter({ unfinished, count, onChange }: { unfinished: boolean; count: number; onChange: (value: boolean) => void }) {
  const { t } = useTranslation("tasks");
  return <div data-slot="timeline-row-filter" className="flex h-9 items-center justify-between gap-2 bg-ruler px-3 text-xs text-ink3">
    <span className="flex items-center gap-1 whitespace-nowrap"><span>{t("microTasks")}</span><span data-slot="timeline-row-count" className="tabular-nums">{count}</span></span>
    <div role="group" aria-label={t("microTasks")} data-zoom-exclude className="flex shrink-0 items-center gap-0.5">
      {[false, true].map(value => <button key={String(value)} type="button" aria-pressed={unfinished === value}
        onClick={() => onChange(value)}
        className={cn("h-7 whitespace-nowrap border-b-2 px-1 outline-none hover:text-ink focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring", unfinished === value ? "border-ink text-ink" : "border-transparent")}>
        {value ? t("unfinished") : t("all")}
      </button>)}
    </div>
  </div>;
}
