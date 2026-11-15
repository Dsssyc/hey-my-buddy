import { BuddyName } from "../components/AliasTag";
import { cn } from "cn";
import { forwardRef, memo, useImperativeHandle, useMemo, useRef, type ComponentProps, type FocusEvent } from "react";
import { useTranslation } from "react-i18next";
import { StateMark } from "../components/ui/state-mark";
import { ScrollingTitle } from "../components/ScrollingTitle";
import { useFadingText } from "../components/use-fading-text";
import { HarnessLogo } from "../buddies/HarnessLogo";
import { buddyLabel, displayTitle, rowStateInfo } from "../macro-task-display";
import type { TimelineRow } from "../macro-task-types";
import { isFailedRow } from "./failure";

/** A compact repair reference that fades smoothly instead of ellipsis-truncating. */
function FixLink({ title, label }: { title: string; label: string }) {
  const fade = useFadingText(`↪ ${title}`);
  return <span ref={fade.ref} data-slot="fix-link" className={cn("min-w-0 max-w-1/2 shrink-0 text-xs text-ink3", fade.className)} aria-label={label}>↪ {title}</span>;
}

/** A micro task's full scrolling title, repair links, new mark, state and buddy. */
const RowNameView = memo(forwardRef<HTMLButtonElement, {
  row: TimelineRow; rows: readonly TimelineRow[]; tabIndex: number; current: boolean; fresh: boolean;
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
  onClick: () => void; onEnter: () => void; onLeave: () => void; onFocus: (event: FocusEvent<HTMLButtonElement>) => void; onBlur: () => void;
}>(function RowName({ row, rows, tabIndex, current, fresh, list, tall, laneLevel, lead, ariaPrefix, onClick, onEnter, onLeave, onFocus, onBlur }, ref) {
  const { t } = useTranslation("tasks");
  const state = rowStateInfo(row, rows);
  const title = displayTitle(row.titleSource, row.title);
  const repairTitle = row.fixes ? displayTitle(row.fixesTitleSource!, row.fixesTitle!).fullText : null;
  const repair = row.fixes ? t("fixes", { title: repairTitle }) : null;
  const paused = row.status === "queued" && row.queueReason === "paused";
  const tone = paused || (row.status === "queued" && row.state !== "awaiting-host") ? null
    : state.tone === "green" ? "live"
    : state.tone === "amber" || row.category === "host" || row.state === "delivered" ? "hand"
    : row.category === "active" ? "live" : isFailedRow(row) ? "fail" : "past";
  const mark = paused ? "paused" : tone === "live" ? "live" : row.state === "delivered" ? "review"
    : row.state === "awaiting-host" || row.kind !== "helper" && (row.state === "failed" || row.state === "cancelled") && !["queued", "running", "reconciliation-needed"].includes(row.status) ? "hand"
      : null;
  const showState = !state.settled;
  return <button ref={ref} type="button" data-key={`row:${row.runId}`} data-slot="timeline-row-name" data-run-id={row.runId} data-fixes={row.fixes}
    tabIndex={tabIndex} aria-current={list && current ? "true" : undefined} aria-label={[ariaPrefix, title.fullText, repair, state.label].filter(Boolean).join(" · ")} onClick={onClick}
    onPointerEnter={onEnter} onPointerLeave={onLeave} onFocus={onFocus} onBlur={onBlur}
    className={cn("group relative flex w-full shrink-0 select-none flex-col justify-center gap-1 border-t px-3 text-left transition-colors duration-150 motion-reduce:transition-none outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus",
      fresh && "row-fresh",
      tall ? "h-[60px] border-t-0" : "h-[52px]",
      current ? "bg-selected-row hover:bg-selected-row" : "hover:bg-sunk")}
    style={{ paddingLeft: 13 + (laneLevel ?? row.depth) * 18 }} data-state-tone={tone ?? "queued"}>
    {Array.from({ length: laneLevel ?? 0 }, (_, index) => <span key={index} aria-hidden="true" data-slot="lane-guide" data-level={index + 1} className="absolute inset-y-0 border-l border-line" style={{ left: 13.5 + index * 18 }}/>)}
    {tone && <div aria-hidden="true" data-slot="row-state-edge" className="pointer-events-none absolute inset-y-0 w-[3px]" style={{ left: lead ? -28 : (laneLevel === undefined ? 0 : 12.5 + (laneLevel - 1) * 18), background: `var(--${tone === "hand" ? "hand-mark" : tone === "live" ? "ink" : tone})` }}/>}
    <span className="flex w-full min-w-0 items-center gap-1.5 text-sm">
      {row.kind === "helper" && <span aria-hidden="true" className="text-ink3">└</span>}
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5">
        <ScrollingTitle text={title.fullText} className="font-medium"/>
        {row.fixes && <FixLink title={repairTitle!} label={repair!}/>}
      </span>
      {!!row.fixedBy?.length && <span data-slot="fixed-by" className="shrink-0 text-xs text-ink3" aria-label={t("fixedBy", { n: row.fixedBy.length })}>↩ {row.fixedBy.length}</span>}
      {fresh && <span aria-label={t("newMicroTask")} className="size-1.5 shrink-0 rounded-full bg-hand-mark"/>}
      {!list && mark && <span data-slot="row-state" data-kind={mark} className="ml-auto flex size-5 shrink-0 items-center justify-center" aria-hidden="true"><StateMark kind={mark} breathing={mark === "live" || mark === "paused" && !row.shutdownConfirmed} className="size-2.5"/></span>}
    </span>
    <span className={cn("block w-full truncate text-xs", current ? "text-ink3" : "text-ink4")}>{showState && <span className={cn("mr-1.5", tone === "hand" ? "text-hand" : mark === "live" || paused ? "text-ink" : !current && "text-ink3", (mark === "live" || paused && !row.shutdownConfirmed) && "state-breathe")}>{state.label}</span>}{showState && !row.buddy?.harness && !ariaPrefix && <span aria-hidden="true" className="mr-1.5 text-ink4">·</span>}{row.buddy?.harness && <span aria-hidden="true" className="mr-1 inline-flex translate-y-[2px] items-center [&_img]:size-3.5 [&_[data-slot=harness-logo]]:size-3.5 [&>span]:size-3.5"><HarnessLogo harness={row.buddy.harness}/></span>}{ariaPrefix && <span>{ariaPrefix} · </span>}<BuddyName buddy={row.buddy}>{buddyLabel(row.buddy)}</BuddyName></span>
  </button>;
}));

/** Keep event bindings current without redrawing an unchanged name cell. */
export const RowName = forwardRef<HTMLButtonElement, ComponentProps<typeof RowNameView>>(function RowName(props, ref) {
  const current = useRef(props);
  current.current = props;
  const button = useRef<HTMLButtonElement>(null);
  useImperativeHandle(ref, () => button.current!);
  const handlers = useMemo(() => ({
    onClick: () => current.current.onClick(),
    onEnter: () => current.current.onEnter(),
    onLeave: () => current.current.onLeave(),
    onFocus: (event: FocusEvent<HTMLButtonElement>) => current.current.onFocus(event),
    onBlur: () => current.current.onBlur(),
  }), []);
  return <RowNameView {...props} {...handlers} ref={button}/>;
});
