import { cn } from "cn";
import { ChevronRight } from "lucide-react";
import { forwardRef, type FocusEvent } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { StateMark } from "../components/ui/state-mark";
import { buddyLabel, displayTitle, rowStateInfo } from "../macro-task-display";
import type { FriendlyBuddy } from "../macro-task-display";
import type { TimelineRow } from "../macro-task-types";

/** A row keeps the same name, measure and disclosure in both views. */
export const RowName = forwardRef<HTMLButtonElement, {
  row: TimelineRow; rows: readonly TimelineRow[]; tabIndex: number; open: boolean; selected: boolean; fresh: boolean; buddies?: readonly FriendlyBuddy[] | null;
  onClick: () => void; onOpen: () => void; onEnter: () => void; onLeave: () => void; onFocus: (event: FocusEvent<HTMLButtonElement>) => void; onBlur: () => void;
}>(function RowName({ row, rows, tabIndex, open, selected, fresh, buddies, onClick, onOpen, onEnter, onLeave, onFocus, onBlur }, ref) {
  const { t } = useTranslation("tasks");
  const state = rowStateInfo(row, rows);
  const title = displayTitle(row.titleSource, row.title);
  const repairTitle = row.fixes ? displayTitle(row.fixesTitleSource!, row.fixesTitle!).fullText : null;
  const repair = row.fixes ? t("fixes", { title: repairTitle }) : null;
  const tone = row.status === "queued" ? null : state.tone === "amber" || row.category === "host" || row.state === "delivered" ? "hand"
    : state.tone === "green" || row.category === "active" ? "live" : row.state === "failed" || row.status === "failed" || row.acceptanceVerdict === "failed" ? "fail" : "past";
  const mark = tone === "live" ? "live" : null;
  const button = <button ref={ref} type="button" data-key={`row:${row.runId}`} data-slot="timeline-row-name" data-run-id={row.runId} data-fixes={row.fixes}
    tabIndex={tabIndex} aria-expanded={open} aria-label={[title.fullText, repair, state.label].filter(Boolean).join(" · ")} onClick={onClick} onDoubleClick={onOpen} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(); } }}
    onPointerEnter={onEnter} onPointerLeave={onLeave} onFocus={onFocus} onBlur={onBlur}
    className={cn("select-none", "group flex h-[62px] w-full shrink-0 flex-col justify-center gap-1 border-t px-3 text-left outline-none transition-colors duration-150 motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-live",
      selected ? "bg-live-wash hover:bg-live-wash" : "hover:bg-sunk", open ? "max-[760px]:sticky max-[760px]:top-0 max-[760px]:z-20 min-[761px]:relative" : "relative")}
    style={{ paddingLeft: 13 + row.depth * 18, boxShadow: tone ? `inset 3px 0 var(--${tone === "hand" ? "hand-mark" : tone})` : undefined }} data-state-tone={tone ?? "queued"}>
    <span className="flex w-full min-w-0 items-center gap-1.5 text-sm">
      {row.kind === "helper" && <span aria-hidden="true" className="text-ink3">└</span>}
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
        <span className="min-w-0 truncate font-medium">{title.text}</span>
        {row.fixes && <span data-slot="fix-link" className="min-w-0 max-w-1/2 shrink-0 truncate text-xs text-ink3" aria-label={repair!}>↪ {repairTitle}</span>}
      </span>
      {!!row.fixedBy?.length && <span data-slot="fixed-by" className="shrink-0 text-xs text-ink3" aria-label={t("fixedBy", { n: row.fixedBy.length })}>↩ {row.fixedBy.length}</span>}
      {fresh && <span aria-label={t("newMicroTask")} className="size-1.5 shrink-0 rounded-full bg-hand-mark"/>}
      <span className="relative ml-auto flex size-7 shrink-0 items-center justify-center" aria-hidden="true">
        {mark && <span data-slot="row-state" data-kind={mark} className={cn("absolute flex items-center justify-center transition-opacity duration-150 group-hover:opacity-0 group-focus-visible:opacity-0 motion-reduce:transition-none", open && "opacity-0")}>
          {mark === "live" && <span className="absolute size-2 rounded-full bg-live motion-safe:animate-[ping_2.4s_cubic-bezier(0,0,0.2,1)_infinite]"/>}
          <StateMark kind={mark} className="size-2.5"/>
        </span>}
        <ChevronRight size={16} strokeWidth={1.5} className={cn("text-ink2 transition-[opacity,rotate,color] duration-150 group-hover:text-ink group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none", open ? "rotate-90 opacity-100" : "opacity-0")}/>
      </span>
    </span>
    <span className="block w-full truncate text-xs text-ink3"><span className={cn("mr-1.5", tone === "hand" && "text-hand", mark === "live" && "text-live")}>{state.label}</span>{buddyLabel(row.buddy, buddies)}</span>
  </button>;
  return row.fixes ? <Tooltip><TooltipTrigger asChild onFocus={event => { if (!event.currentTarget.matches(":focus-visible")) event.preventDefault(); }}>{button}</TooltipTrigger><TooltipContent side="right" align="start" className="wrap-anywhere text-left">{repairTitle}</TooltipContent></Tooltip> : button;
});
