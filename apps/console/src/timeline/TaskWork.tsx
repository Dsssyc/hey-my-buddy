import { cn } from "cn";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../components/ui/button";
import { Collapsible, CollapsibleContent } from "../components/ui/collapsible";
import { MacroTaskOverview } from "../MacroTaskOverview";
import { buddyLabel, clockTime, rowStateInfo } from "../macro-task-display";
import { formatDuration } from "../i18n/format";
import type { FriendlyBuddy } from "../macro-task-display";
import type { MacroTaskSummary, MacroTaskTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "../macro-task-types";
import { parseTimelineInstant as instant } from "./instant";
import { timelineScaleInput } from "./scale";
import { useTimelineZoom } from "./useTimelineZoom";
import { ChronologicalList } from "./ChronologicalList";
import { useViewPreference } from "../view-preferences";
import { Segmented, SegmentedItem } from "../components/ui/segmented";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { Minus, Plus, ScanLine } from "lucide-react";
import { RowName } from "./RowName";
import { Axis, Track, spanReadout, eventReadout } from "./Tracks";

export type TaskWorkProps = {
  summary: MacroTaskSummary | null; timeline: MacroTaskTimeline | null; displayObservedAt: string | null;
  loading: boolean; error: string; stale: boolean; newRunIds: ReadonlySet<string>; active: boolean;
  openedRunId: string | null; detail: (runId: string, railTarget: HTMLElement | null, exiting: boolean) => ReactNode;
  headerActions?: ReactNode; listToggle?: ReactNode; buddies?: readonly FriendlyBuddy[] | null;
  onOpen: (runId: string, key?: string) => void; onClose: () => void;
  onRetry: () => void; onBackToList: () => void;
};

/** Persistent tree names beside either the scaled tracks or one micro task. */
export function TaskWork(props: TaskWorkProps) {
  const { t } = useTranslation("tasks");
  const { timeline, openedRunId, displayObservedAt } = props;
  const [listView, setListView] = useViewPreference("timeline.chronological");
  const lastPointerAt = useRef<number | null>(null);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState(openedRunId);
  useEffect(() => { if (openedRunId !== null) setSelectedRunId(openedRunId); }, [openedRunId]);
  const [shownRunId, setShownRunId] = useState(openedRunId);
  useEffect(() => {
    if (openedRunId !== null) { setShownRunId(openedRunId); return; }
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const timer = window.setTimeout(() => setShownRunId(null), reduced ? 0 : 200);
    return () => window.clearTimeout(timer);
  }, [openedRunId]);
  const [railTarget, setRailTarget] = useState<HTMLDivElement | null>(null);
  const [hoveredRow, setHoveredRow] = useState<TimelineRow | null>(null);
  const [focusedRow, setFocusedRow] = useState<TimelineRow | null>(null);
  const [focusedSpan, setFocusedSpan] = useState<TimelineSpan | null>(null);
  const [pointerX, setPointerX] = useState<number | null>(null);
  const [activeEvent, setActiveEvent] = useState<TimelineEvent | null>(null);
  const [hoveredSpan, setHoveredSpan] = useState<TimelineSpan | null>(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const media = window.matchMedia?.("(max-width: 760px)");
    if (!media) return;
    const update = () => setNarrow(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [width, setWidth] = useState(640);
  const rootRef = useRef<HTMLElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const namesRef = useRef<HTMLDivElement>(null);
  const rowsRef = useRef(new Map<string, HTMLButtonElement>());
  const wasOpen = useRef<string | null>(null);
  const open = openedRunId !== null;
  const nowMs = instant(displayObservedAt ?? timeline?.observedAt) ?? 0;
  const displayed = useMemo(() => timeline ? { ...timeline, observedAt: displayObservedAt ?? timeline.observedAt } : null, [timeline, displayObservedAt]);
  const input = useMemo(() => displayed ? timelineScaleInput(displayed) : null, [displayed]);
  const selectedAt = selectedRunId ? Math.min(...(timeline?.spans.filter(span => span.runId === selectedRunId).map(span => instant(span.startAt)).filter((at): at is number => at !== null) ?? []), ...(timeline?.events.filter(event => event.runId === selectedRunId).map(event => instant(event.at)).filter((at): at is number => at !== null) ?? [])) : null;
  const { scale, change: zoom, atMinimum, atMaximum } = useTimelineZoom(input, width, timeline?.macroTask.macroTaskId, trackRef, selectedAt !== null && Number.isFinite(selectedAt) ? selectedAt : null, lastPointerAt, nowMs);
  useEffect(() => { setSelectedRunId(openedRunId); lastPointerAt.current = null; setPointerX(null); }, [timeline?.macroTask.macroTaskId]);
  useEffect(() => {
    const element = trackRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const measure = () => { if (element.clientWidth > 0) setWidth(element.clientWidth); };
    const observer = new ResizeObserver(measure);
    observer.observe(element); measure();
    return () => observer.disconnect();
  }, [!!timeline]);
  useEffect(() => {
    const previous = wasOpen.current;
    if (openedRunId !== previous) {
      const target = openedRunId ?? previous;
      if (target) rowsRef.current.get(target)?.focus();
    }
    wasOpen.current = openedRunId;
  }, [openedRunId, open]);
  const rows = timeline?.rows ?? [];
  const spansByRow = useMemo(() => {
    const grouped = new Map<string, TimelineSpan[]>();
    for (const span of timeline?.spans ?? []) {
      const list = grouped.get(span.runId) ?? []; list.push(span); grouped.set(span.runId, list);
    }
    return grouped;
  }, [timeline]);
  const tabKey = focusKey ?? `row:${selectedRunId ?? rows[0]?.runId}`;
  const focusRow = (runId: string) => rowsRef.current.get(runId)?.focus();
  const fragments = (runId: string) => Array.from(rootRef.current?.querySelectorAll<SVGElement>("[data-slot=timeline-track] [data-key]") ?? [])
    .filter(element => element.closest("[data-slot=timeline-row]")?.getAttribute("data-run-id") === runId)
    .sort((left, right) => Number(left.dataset.at) - Number(right.dataset.at));
  const rowLine = (row: TimelineRow) => {
    const spans = spansByRow.get(row.runId) ?? [];
    const turns = spans.filter(span => span.kind === "execution").length;
    const wait = spans.find(span => (span.kind === "queue" && span.state === "queued" || span.kind === "host" && span.state === "open") && span.endAt === null);
    const lastEnd = row.state === "delivered" && spans.filter(span => span.kind === "execution").every(span => span.shutdownConfirmed === true)
      ? Math.max(0, ...spans.filter(span => span.kind === "execution").map(span => instant(span.endAt) ?? 0)) : 0;
    const start = wait ? instant(wait.startAt) : lastEnd || null;
    return [rowStateInfo(row).label, start !== null ? t("openWait", { duration: formatDuration(Math.max(0, nowMs - start)) }) : null, t("turnCount", { n: turns }), buddyLabel(row.buddy, props.buddies)].filter(Boolean).join(" · ");
  };
  const activeSpan = hoveredSpan ?? (hoveredRow ? null : focusedSpan);
  const activeRow = hoveredRow ?? focusedRow;
  const pointerGap = scale?.breaks.find(gap => pointerX !== null && pointerX >= gap.startX && pointerX <= gap.endX);
  const pointerLine = pointerX !== null && scale ? pointerGap ? t("drawing.gap", { start: clockTime(pointerGap.startMs), end: clockTime(pointerGap.endMs), duration: formatDuration(pointerGap.endMs - pointerGap.startMs) }) : t("drawing.pointer", { at: clockTime(scale.toTime(pointerX)) }) : null;
  const facts = activeEvent ? eventReadout(activeEvent) : activeSpan ? t("segment", { label: spanReadout(activeSpan, nowMs, props.buddies, rows.find(row => row.runId === activeSpan.runId)) }) : activeRow ? rowLine(activeRow)
    : input?.domain ? t("range", { start: clockTime(input.domain.startMs), end: clockTime(input.domain.endMs), n: rows.length }) : t("noTime");
  const readout = [pointerLine, facts].filter(Boolean).join(" · ");
  const incomplete = timeline && (timeline.filtered || !timeline.scopeComplete || Object.values(timeline.truncated).some(Boolean));
  const renderName = (row: TimelineRow) => <Collapsible key={row.runId} open={openedRunId === row.runId} className={cn("min-w-0", open && openedRunId !== row.runId && "max-[760px]:hidden")}>
    <RowName ref={element => { if (element) rowsRef.current.set(row.runId, element); else rowsRef.current.delete(row.runId); }}
      row={row} tabIndex={tabKey === `row:${row.runId}` ? 0 : -1} selected={selectedRunId === row.runId} open={openedRunId === row.runId} fresh={props.newRunIds.has(row.runId)} buddies={props.buddies}
      onClick={() => openedRunId === row.runId ? props.onClose() : props.onOpen(row.runId)}
      onEnter={() => { setPointerX(null); setActiveEvent(null); setHoveredSpan(null); setHoveredRow(row); }} onLeave={() => setHoveredRow(null)} onFocus={() => { setPointerX(null); setHoveredRow(null); setHoveredSpan(null); setActiveEvent(null); setFocusedRow(row); }} onBlur={() => setFocusedRow(null)}/>
    <CollapsibleContent className="border-l-2 border-live bg-sunk">
      {(openedRunId ?? shownRunId) === row.runId && <div ref={setRailTarget} data-slot="task-steps-drawer"/>}
    </CollapsibleContent>
  </Collapsible>;
  return <section ref={rootRef} data-slot="task-work" onFocusCapture={event => {
    const key = (event.target as Element).closest("[data-key]")?.getAttribute("data-key");
    if (key) { setFocusKey(key); setPointerX(null); setHoveredRow(null); setHoveredSpan(null); setActiveEvent(null); }
  }} onBlurCapture={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusKey(null);
  }} onKeyDown={event => {
    if (!open && !listView && ["+", "=", "-", "0"].includes(event.key) && !(event.target as Element).closest("input, textarea, select, [role=dialog]")) {
      event.preventDefault(); zoom(event.key === "0" ? 0 : event.key === "-" ? -1 : 1); return;
    }
    if (!(event.target as Element).closest("[data-slot=task-names], [data-slot=timeline-tracks], [data-slot=timeline-detail-stage]")) return;
    if (event.defaultPrevented || (event.target as Element).closest("input, textarea, select, [role=dialog]")) return;
    const element = (event.target as Element).closest("[data-key]");
    const runId = open ? openedRunId : element?.closest("[data-run-id]")?.getAttribute("data-run-id");
    if (!runId) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const index = rows.findIndex(row => row.runId === runId);
      const next = rows[Math.max(0, Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
      if (next) { if (open && next.runId !== openedRunId) props.onOpen(next.runId); else focusRow(next.runId); }
    } else if (!open && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const items = fragments(runId), index = items.findIndex(item => item === element);
      const next = event.key === "Home" ? items[0] : event.key === "End" ? items.at(-1)
        : items[Math.min(items.length - 1, index + (event.key === "ArrowRight" ? 1 : -1))];
      if (next) next.focus(); else focusRow(runId);
    }
  }} data-mode={open ? "detail" : "timeline"} className="@container flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
    <MacroTaskOverview summary={props.summary} timeline={timeline} loading={props.loading} stale={props.stale} active={props.active} nowMs={nowMs}
      onBackToList={props.onBackToList} listToggle={props.listToggle} headerActions={props.headerActions}/>
    {props.error && <div role="alert" className="flex shrink-0 flex-wrap items-center gap-2 border-b bg-hand-wash px-4 py-2 text-xs text-hand">{t("timelineReadFailed", { at: clockTime(timeline?.observedAt) })}<span className="wrap-anywhere">{props.error}</span><Button variant="outline" size="sm" onClick={props.onRetry}>{t("retry")}</Button></div>}
    {props.stale && !props.error && <p role="status" className="border-b bg-hand-wash px-4 py-2 text-xs text-hand">{t("staleTimeline")}</p>}
    {!timeline && props.loading ? <div role="status" className="flex flex-1 animate-pulse items-center justify-center motion-reduce:animate-none">{t("loading")}</div>
      : timeline && rows.length === 0 && !open ? <p className="p-6 text-sm text-ink2">{t("noMicroTasks")}</p> : <>
        <div className="grid min-h-9 shrink-0 grid-cols-[auto_minmax(0,1fr)] items-center gap-x-5 gap-y-2 px-4 py-2 text-xs text-ink2 sm:px-6 @min-[720px]:grid-cols-[auto_minmax(0,1fr)_auto]"><Segmented aria-label={t("timelineView")} value={listView ? "list" : "timeline"} onValueChange={value => setListView(value === "list")} className="h-7 text-xs"><SegmentedItem value="timeline" className="py-0">{t("timeline")}</SegmentedItem><SegmentedItem value="list" className="py-0">{t("chronological")}</SegmentedItem></Segmented>
          <div data-slot="timeline-legend" aria-hidden={open} className={cn("col-span-2 row-start-2 flex flex-wrap gap-4 @min-[720px]:col-span-1 @min-[720px]:col-start-2 @min-[720px]:row-start-1", open && "invisible")}><span className="flex items-center gap-1.5"><i className="h-2 w-4 border border-line2 bg-past-wash"/>{t("execution")}</span><span className="flex items-center gap-1.5"><i className="w-4 border-t border-ink3"/>{t("wait")}</span><span className="flex items-center gap-1.5"><i className="w-4 border-t-2 border-dashed border-hand-mark"/>{t("waiting")}</span><span className="flex items-center gap-1.5"><i className="h-2 w-4 border border-hand-mark bg-hand-wash"/>{t("drawing.silentLegend")}</span></div>
          {!open && !listView && <div className="col-start-2 row-start-1 flex shrink-0 justify-self-end gap-1 @min-[720px]:col-start-3" data-slot="timeline-zoom">{([[-1, Minus, "zoomOut", atMinimum], [1, Plus, "zoomIn", atMaximum], [0, ScanLine, "zoomFit", atMinimum]] as const).map(([direction, Icon, label, unavailable]) => <Tooltip key={label}><TooltipTrigger asChild><Button variant="ghost" size="icon-sm" aria-label={t(label)} aria-disabled={unavailable} onClick={() => zoom(direction)}><Icon aria-hidden="true"/></Button></TooltipTrigger><TooltipContent>{t(label)}</TooltipContent></Tooltip>)}</div>}
        </div>
        <div role="region" aria-label={t("timeline")} className="relative grid min-h-0 min-w-0 flex-1 grid-cols-[272px_minmax(0,1fr)] max-[760px]:flex max-[760px]:flex-col">
          <div ref={namesRef} data-slot="task-names" className={cn("min-h-0 min-w-0 overflow-x-hidden overflow-y-auto bg-background pl-6 max-[760px]:px-4", open ? "max-[760px]:max-h-[38dvh] max-[760px]:shrink-0" : "max-[760px]:hidden")}
            onScroll={event => { if (!open && trackRef.current) trackRef.current.scrollTop = event.currentTarget.scrollTop; }}>
            <div className="flex h-9 shrink-0 items-center justify-between pr-4 text-xs text-ink3 max-[760px]:hidden"><span>{t("microTasks")}</span><span>{rows.length}</span></div>
            {(!narrow || open) && rows.map(renderName)}
            {open && !rows.some(row => row.runId === openedRunId) && <><Button variant="ghost" size="sm" onClick={props.onClose}>{t("closeDetail")}</Button><div ref={setRailTarget} data-slot="task-steps-drawer"/></>}
          </div>
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
            <div ref={trackRef} data-slot="timeline-tracks" aria-hidden={open || listView} inert={open || listView} className={cn("absolute inset-0 overflow-x-auto overflow-y-auto px-4 transition-[opacity,translate] duration-200 motion-reduce:transition-none", open || listView ? "pointer-events-none translate-x-6 opacity-0" : "translate-x-0 opacity-100")}
              onScroll={event => { if (!open && namesRef.current) namesRef.current.scrollTop = event.currentTarget.scrollTop; }}>
              {scale && input && <div data-slot="timeline-canvas" data-ppm={scale.pixelsPerMinute} className="relative min-h-full" style={{ width: scale.widthPx + 16 }} onPointerMove={event => {
                const target = (event.target as Element).closest("svg");
                if (!target || !["timeline-track", "timeline-axis"].includes(target.getAttribute("data-slot") ?? "")) return;
                const x = Math.max(0, Math.min(scale.widthPx, event.clientX - target.getBoundingClientRect().left - 8));
                setPointerX(x); lastPointerAt.current = scale.toTime(x);
                const runId = target.closest("[data-slot=timeline-row]")?.getAttribute("data-run-id");
                setHoveredRow(rows.find(row => row.runId === runId) ?? null);
              }} onPointerLeave={() => { setPointerX(null); setHoveredSpan(null); setHoveredRow(null); setActiveEvent(null); }}>{narrow && <svg aria-hidden="true" data-slot="timeline-break-columns" viewBox={`-8 0 ${scale.widthPx + 16} 1`} preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full">{scale.breaks.map(gap => <rect key={gap.startMs} x={gap.startX} width={gap.endX - gap.startX} height={1} className="fill-sunk"/>)}</svg>}<div className="sticky top-0 z-10 bg-card"><Axis scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs}/></div>
                {rows.map(row => <div key={row.runId} data-slot="timeline-row" data-run-id={row.runId} className="relative">
                  {narrow && !open && <div className="sticky left-0 z-10" style={{ width: Math.max(1, width - 32) }}>{renderName(row)}</div>}
                  <Track boundary={trackRef.current} tabKey={tabKey} selected={selectedRunId === row.runId} row={row} spans={spansByRow.get(row.runId) ?? []} events={timeline!.events.filter(event => event.runId === row.runId)} onEvent={event => { setActiveEvent(event); if (event) setHoveredSpan(null); }} scale={scale} nowMs={nowMs} ongoing={input.nowAtEnd} buddies={props.buddies}
                    onOpen={props.onOpen} onFocusSpan={span => { setPointerX(null); setHoveredRow(null); setHoveredSpan(null); setActiveEvent(null); setFocusedSpan(span); }} onHover={span => { setActiveEvent(null); setHoveredSpan(span); if (span) setHoveredRow(null); }}/>
                </div>)}
                {pointerX !== null && <svg data-slot="timeline-pointer" aria-hidden="true" viewBox={`-8 0 ${scale.widthPx + 16} 1`} preserveAspectRatio="none" className="pointer-events-none absolute inset-0 z-20 h-full w-full"><line x1={pointerX} x2={pointerX} y2={1} className="stroke-ink3" vectorEffect="non-scaling-stroke"/></svg>}
              </div>}
            </div>
            <div data-slot="timeline-list-stage" aria-hidden={open || !listView} inert={open || !listView} className={cn("absolute inset-0 flex min-h-0 min-w-0 flex-col bg-card transition-[opacity,translate] duration-200 motion-reduce:transition-none", !open && listView ? "translate-x-0 opacity-100" : "pointer-events-none translate-x-6 opacity-0")}>
              {timeline && scale && listView && <ChronologicalList timeline={timeline} scale={scale} nowMs={nowMs} onOpen={props.onOpen}/>}
            </div>
            <div data-slot="timeline-detail-stage" aria-hidden={!open} inert={!open} className={cn("absolute inset-0 flex min-h-0 min-w-0 flex-col border-t border-l bg-card transition-[opacity,translate] duration-200 motion-reduce:transition-none max-[760px]:border-l-0", open ? "translate-x-0 opacity-100" : "pointer-events-none translate-x-6 opacity-0")}>
              {(openedRunId ?? shownRunId) !== null && props.detail((openedRunId ?? shownRunId)!, railTarget, !open)}
            </div>
          </div>
        </div>
        {!open && <div data-slot="timeline-status" role="status" className="flex h-9 shrink-0 items-center gap-2 overflow-hidden border-t bg-card px-4 text-xs text-ink2 sm:px-6"><span className="truncate">{readout}{incomplete ? ` · ${t("uncompressed")}` : ""}</span></div>}
      </>}
  </section>;
}
