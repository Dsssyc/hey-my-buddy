import { cn } from "cn";
import { ChevronRight } from "lucide-react";
import { forwardRef, type FocusEvent } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { StateMark } from "../components/ui/state-mark";
import { useTextClipped } from "../components/use-text-clipped";
import { HarnessLogo } from "../buddies/HarnessLogo";
import { buddyLabel, displayTitle, rowStateInfo } from "../macro-task-display";
import type { FriendlyBuddy } from "../macro-task-display";
import type { TimelineRow } from "../macro-task-types";

/**
 * One micro task's name cell: two lines that hold the title and the buddy,
 * with the state word beside it only while a human still has something to do
 * or something is running — a settled state is the colour band at the left
 * edge alone. The complete recorded title and the buddy's full identity are
 * on hover and keyboard focus, with a lane's summary after them when the row
 * heads one. In the timeline the cell also carries the state mark that
 * becomes the open hint; beside a detail it is a plain list row. A lane
 * header places its collapse toggle beside this cell as a sibling, never
 * inside it.
 */
export const RowName = forwardRef<HTMLButtonElement, {
  row: TimelineRow; rows: readonly TimelineRow[]; tabIndex: number; current: boolean; fresh: boolean; buddies?: readonly FriendlyBuddy[] | null;
  /** The plain list beside an open detail: no open hint, the current row is where the reader is. */
  list?: boolean;
  /** The taller lane-header row (60px) beside the ordinary 52px one. */
  tall?: boolean;
  /** A lane member: one indent step inside its Lead, without any tree glyph. */
  indent?: boolean;
  /** A word the accessible name starts with, such as the Lead badge. */
  ariaPrefix?: string;
  /** Facts the hover appends after the title and identity, such as the lane's summary. */
  hoverExtra?: ReactNode;
  onClick: () => void; onEnter: () => void; onLeave: () => void; onFocus: (event: FocusEvent<HTMLButtonElement>) => void; onBlur: () => void;
}>(function RowName({ row, rows, tabIndex, current, fresh, buddies, list, tall, indent, ariaPrefix, hoverExtra, onClick, onEnter, onLeave, onFocus, onBlur }, ref) {
  const { t } = useTranslation("tasks");
  const state = rowStateInfo(row, rows);
  const title = displayTitle(row.titleSource, row.title);
  const repairTitle = row.fixes ? displayTitle(row.fixesTitleSource!, row.fixesTitle!).fullText : null;
  const repair = row.fixes ? t("fixes", { title: repairTitle }) : null;
  const [titleRef, titleClipped] = useTextClipped<HTMLSpanElement>(title.text);
  const tone = row.status === "queued" ? null : state.tone === "amber" || row.category === "host" || row.state === "delivered" ? "hand"
    : state.tone === "green" || row.category === "active" ? "live" : row.state === "failed" || row.status === "failed" || row.acceptanceVerdict === "failed" ? "fail" : "past";
  const mark = tone === "live" ? "live" : null;
  const button = <button ref={ref} type="button" data-key={`row:${row.runId}`} data-slot="timeline-row-name" data-run-id={row.runId} data-fixes={row.fixes}
    tabIndex={tabIndex} aria-current={list && current ? "true" : undefined} aria-label={[ariaPrefix, title.fullText, repair, state.label].filter(Boolean).join(" · ")} onClick={onClick}
    onPointerEnter={onEnter} onPointerLeave={onLeave} onFocus={onFocus} onBlur={onBlur}
    className={cn("group flex w-full shrink-0 select-none flex-col justify-center gap-1 border-t px-3 text-left outline-none transition-colors duration-150 motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-live",
      tall ? "h-[60px] border-t-0" : "h-[52px]",
      current ? "bg-live-wash hover:bg-live-wash" : "hover:bg-sunk")}
    style={{ paddingLeft: 13 + (row.depth + (indent ? 1 : 0)) * 18, boxShadow: tone ? `inset 3px 0 var(--${tone === "hand" ? "hand-mark" : tone})` : undefined }} data-state-tone={tone ?? "queued"}>
    <span className="flex w-full min-w-0 items-center gap-1.5 text-sm">
      {row.kind === "helper" && <span aria-hidden="true" className="text-ink3">└</span>}
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
        <span ref={titleRef} className="min-w-0 truncate font-medium">{title.text}</span>
        {row.fixes && <span data-slot="fix-link" className="min-w-0 max-w-1/2 shrink-0 truncate text-xs text-ink3" aria-label={repair!}>↪ {repairTitle}</span>}
      </span>
      {!!row.fixedBy?.length && <span data-slot="fixed-by" className="shrink-0 text-xs text-ink3" aria-label={t("fixedBy", { n: row.fixedBy.length })}>↩ {row.fixedBy.length}</span>}
      {fresh && <span aria-label={t("newMicroTask")} className="size-1.5 shrink-0 rounded-full bg-hand-mark"/>}
      {!list && <span className="relative ml-auto flex size-5 shrink-0 items-center justify-center" aria-hidden="true">
        {mark && <span data-slot="row-state" data-kind={mark} className={cn("absolute flex items-center justify-center transition-opacity duration-150 group-hover:opacity-0 group-focus-visible:opacity-0 motion-reduce:transition-none")}>
          {mark === "live" && <span className="absolute size-2 rounded-full bg-live motion-safe:animate-[ping_2.4s_cubic-bezier(0,0,0.2,1)_infinite]"/>}
          <StateMark kind={mark} className="size-2.5"/>
        </span>}
        <ChevronRight size={16} strokeWidth={1.5} className={cn("text-ink2 transition-[opacity,rotate,color] duration-150 group-hover:text-ink group-hover:opacity-100 group-focus-visible:opacity-100 motion-reduce:transition-none opacity-0")}/>
      </span>}
    </span>
    <span className={cn("block w-full truncate text-xs", current ? "text-ink3" : "text-ink4")}>{!state.settled && <span className={cn("mr-1.5", tone === "hand" ? "text-hand" : mark === "live" ? "text-live" : !current && "text-ink3")}>{state.label}</span>}{row.buddy?.harness && <span aria-hidden="true" className="mr-1 inline-flex translate-y-[2px] items-center [&_img]:size-3.5 [&_[data-slot=harness-logo]]:size-3.5 [&>span]:size-3.5"><HarnessLogo harness={row.buddy.harness}/></span>}{buddyLabel(row.buddy, buddies)}</span>
  </button>;
  const tuple = row.buddy ? [row.buddy.harness, row.buddy.provider, row.buddy.model, row.buddy.effort].map(value => value ?? t("unknown")).join(" · ") : null;
  const wholeTitle = titleClipped || title.clipped;
  return repairTitle || tuple || wholeTitle || hoverExtra ? <Tooltip><TooltipTrigger asChild onFocus={event => { if (!event.currentTarget.matches(":focus-visible")) event.preventDefault(); }}>{button}</TooltipTrigger><TooltipContent side="bottom" align="start" className="wrap-anywhere text-left">
    {wholeTitle && <span className="block">{title.fullText}</span>}
    {repairTitle && repairTitle !== (wholeTitle ? title.fullText : null) && <span className="block">{repairTitle}</span>}
    {tuple && <span className="block text-xs text-ink3">{tuple}</span>}
    {hoverExtra}
  </TooltipContent></Tooltip> : button;
});
