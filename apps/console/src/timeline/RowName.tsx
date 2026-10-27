import { cn } from "cn";
import { forwardRef, type FocusEvent } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { StateMark } from "../components/ui/state-mark";
import { useFadingText } from "../components/use-fading-text";
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
  /** The member's tree level inside its Lead lane. */
  laneLevel?: number;
  /** A Lead's stripe starts at the lane's left edge, before its separate disclosure. */
  lead?: boolean;
  /** A word the accessible name starts with, such as the Lead badge. */
  ariaPrefix?: string;
  /** Facts the hover appends after the title and identity, such as the lane's summary. */
  hoverExtra?: ReactNode;
  onClick: () => void; onEnter: () => void; onLeave: () => void; onFocus: (event: FocusEvent<HTMLButtonElement>) => void; onBlur: () => void;
}>(function RowName({ row, rows, tabIndex, current, fresh, buddies, list, tall, laneLevel, lead, ariaPrefix, hoverExtra, onClick, onEnter, onLeave, onFocus, onBlur }, ref) {
  const { t } = useTranslation("tasks");
  const state = rowStateInfo(row, rows);
  const title = displayTitle(row.titleSource, row.title);
  const repairTitle = row.fixes ? displayTitle(row.fixesTitleSource!, row.fixesTitle!).fullText : null;
  const repair = row.fixes ? t("fixes", { title: repairTitle }) : null;
  const titleText = useFadingText(title.text);
  const tone = row.status === "queued" ? null : state.tone === "amber" || row.category === "host" || row.state === "delivered" ? "hand"
    : state.tone === "green" || row.category === "active" ? "live" : row.state === "failed" || row.status === "failed" ? "fail" : "past";
  const mark = tone === "live" ? "live" : null;
  const button = <button ref={ref} type="button" data-key={`row:${row.runId}`} data-slot="timeline-row-name" data-run-id={row.runId} data-fixes={row.fixes}
    tabIndex={tabIndex} aria-current={list && current ? "true" : undefined} aria-label={[ariaPrefix, title.fullText, repair, state.label].filter(Boolean).join(" · ")} onClick={onClick}
    onPointerEnter={onEnter} onPointerLeave={onLeave} onFocus={onFocus} onBlur={onBlur}
    className={cn("group relative flex w-full shrink-0 select-none flex-col justify-center gap-1 border-t px-3 text-left outline-none transition-colors duration-150 motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
      tall ? "h-[60px] border-t-0" : "h-[52px]",
      current ? "bg-selected-row hover:bg-selected-row" : "hover:bg-sunk")}
    style={{ paddingLeft: 13 + (laneLevel ?? row.depth) * 18 }} data-state-tone={tone ?? "queued"}>
    {Array.from({ length: laneLevel ?? 0 }, (_, index) => <span key={index} aria-hidden="true" data-slot="lane-guide" data-level={index + 1} className="absolute inset-y-0 border-l border-line" style={{ left: 13.5 + index * 18 }}/>)}
    {tone && <div aria-hidden="true" data-slot="row-state-edge" className="pointer-events-none absolute inset-y-0 w-[3px]" style={{ left: lead ? -28 : (laneLevel === undefined ? 0 : 12.5 + (laneLevel - 1) * 18), background: `var(--${tone === "hand" ? "hand-mark" : tone === "live" ? "ink" : tone})` }}/>}
    <span className="flex w-full min-w-0 items-center gap-1.5 text-sm">
      {row.kind === "helper" && <span aria-hidden="true" className="text-ink3">└</span>}
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
        <span ref={titleText.ref} className={cn("min-w-0 font-medium", titleText.className)}>{title.text}</span>
        {row.fixes && <span data-slot="fix-link" className="min-w-0 max-w-1/2 shrink-0 truncate text-xs text-ink3" aria-label={repair!}>↪ {repairTitle}</span>}
      </span>
      {!!row.fixedBy?.length && <span data-slot="fixed-by" className="shrink-0 text-xs text-ink3" aria-label={t("fixedBy", { n: row.fixedBy.length })}>↩ {row.fixedBy.length}</span>}
      {fresh && <span aria-label={t("newMicroTask")} className="size-1.5 shrink-0 rounded-full bg-hand-mark"/>}
      {!list && mark && <span data-slot="row-state" data-kind={mark} className="ml-auto flex size-5 shrink-0 items-center justify-center" aria-hidden="true"><StateMark kind={mark} className="size-2.5"/></span>}
    </span>
    <span className={cn("block w-full truncate text-xs", current ? "text-ink3" : "text-ink4")}>{!state.settled && <span className={cn("mr-1.5", tone === "hand" ? "text-hand" : mark === "live" ? "text-ink state-breathe" : !current && "text-ink3")}>{state.label}</span>}{row.buddy?.harness && <span aria-hidden="true" className="mr-1 inline-flex translate-y-[2px] items-center [&_img]:size-3.5 [&_[data-slot=harness-logo]]:size-3.5 [&>span]:size-3.5"><HarnessLogo harness={row.buddy.harness}/></span>}{ariaPrefix && <span>{ariaPrefix} · </span>}{buddyLabel(row.buddy, buddies)}</span>
  </button>;
  const tuple = row.buddy ? [row.buddy.harness, row.buddy.provider, row.buddy.model, row.buddy.effort].map(value => value ?? t("unknown")).join(" / ") : null;
  const wholeTitle = titleText.hint !== undefined || title.clipped;
  return repairTitle || tuple || wholeTitle || hoverExtra || fresh || !!row.fixedBy?.length ? <Tooltip><TooltipTrigger asChild onFocus={event => { if (!event.currentTarget.matches(":focus-visible")) event.preventDefault(); }}>{button}</TooltipTrigger><TooltipContent side="bottom" align="start" className="wrap-anywhere text-left">
    {wholeTitle && <span className="block">{title.fullText}</span>}
    {repairTitle && repairTitle !== (wholeTitle ? title.fullText : null) && <span className="block">{repairTitle}</span>}
    {tuple && <span className="block text-xs text-ink3">{tuple}</span>}
    {fresh && <span className="block">{t("newMicroTask")}</span>}
    {!!row.fixedBy?.length && <span className="block">{t("fixedBy", { n: row.fixedBy.length })}</span>}
    {hoverExtra}
  </TooltipContent></Tooltip> : button;
});
