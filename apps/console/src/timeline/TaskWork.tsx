import { cn } from "cn";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { Button } from "../components/ui/button";
import { Collapsible, CollapsibleContent } from "../components/ui/collapsible";
import { ScrollArea } from "../components/ui/scroll-area";
import { MacroTaskOverview } from "../MacroTaskOverview";
import { buddyLabel, clockTime, displayTitle, rowStateInfo } from "../macro-task-display";
import { formatDuration } from "../i18n/format";
import { usageTotalsLabel } from "../usage-totals";
import type { FriendlyBuddy } from "../macro-task-display";
import type { MacroTaskSummary, MacroTaskTimeline, TimelineRow, TimelineSpan } from "../macro-task-types";
import { parseTimelineInstant as instant } from "./instant";
import { timelineScaleInput } from "./scale";
import { useTimelineZoom } from "./useTimelineZoom";
import { useViewport } from "./useViewport";
import { RowName } from "./RowName";
import { ZoomControls } from "./ZoomControls";
import { buddyColorRegistry } from "./buddyColors";
import { groupLanes, laneSummary, ownedWaits } from "./lanes";
import type { LaneGroup } from "./lanes";
import { markerAt } from "./MarkerCluster";
import type { TimelineMarker } from "./MarkerCluster";
import { Axis, Track, spanReadout, markerReadout } from "./Tracks";

export type TaskWorkProps = {
  summary: MacroTaskSummary | null; timeline: MacroTaskTimeline | null; displayObservedAt: string | null;
  loading: boolean; error: string; stale: boolean; newRunIds: ReadonlySet<string>; active: boolean;
  openedRunId: string | null; detail: (runId: string) => ReactNode;
  headerActions?: ReactNode; listToggle?: ReactNode; buddies?: readonly FriendlyBuddy[] | null;
  onOpen: (runId: string, key?: string) => void; onClose: () => void;
  onRetry: () => void; onBackToList: () => void;
};

const NAME_PX = 272;
const SLACK_PX = 8;
const LEAD_PX = 60;

/**
 * One macro task's working area: without a detail it is the lanes — every Lead
 * with the micro tasks it owns under one shared time scale, in one scroll area
 * whose sticky name column and lane summaries stay in view; with a detail the
 * left column becomes the plain micro task list and the detail, with its steps
 * in a strip along its top, fills the rest.
 */
export function TaskWork(props: TaskWorkProps) {
  const { t } = useTranslation("tasks");
  const { timeline, openedRunId, displayObservedAt } = props;
  const lastPointerAt = useRef<number | null>(null);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState(openedRunId);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  useEffect(() => { if (openedRunId !== null) setSelectedRunId(openedRunId); }, [openedRunId]);
  const [hoveredRow, setHoveredRow] = useState<TimelineRow | null>(null);
  const [focusedRow, setFocusedRow] = useState<TimelineRow | null>(null);
  const [focusedSpan, setFocusedSpan] = useState<TimelineSpan | null>(null);
  const [pointerX, setPointerX] = useState<number | null>(null);
  const [activeMarker, setActiveMarker] = useState<TimelineMarker | null>(null);
  const [hoveredSpan, setHoveredSpan] = useState<TimelineSpan | null>(null);
  const [collapsedLanes, setCollapsedLanes] = useState<ReadonlySet<string>>(() => new Set());
  const { viewport: workspace, setViewport: setWorkspace, width } = useViewport<HTMLElement>(1088);
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null);
  const narrow = width <= 760;
  const viewportRef = useRef<HTMLDivElement | null>(null);
  viewportRef.current = viewport;
  const rowsRef = useRef(new Map<string, HTMLButtonElement>());
  const movingDetailFocus = useRef(false);
  const wasOpen = useRef<string | null>(null);
  const open = openedRunId !== null;
  const nowMs = instant(displayObservedAt ?? timeline?.observedAt) ?? 0;
  const displayed = useMemo(() => timeline ? { ...timeline, observedAt: displayObservedAt ?? timeline.observedAt } : null, [timeline, displayObservedAt]);
  const input = useMemo(() => displayed ? timelineScaleInput(displayed) : null, [displayed]);
  const selectedEvent = timeline?.events.find(event => event.runId === selectedRunId && `event:${event.seq}` === selectedKey);
  const inquirySpan = timeline?.spans.find(span => span.runId === selectedRunId && span.inquiries?.some(inquiry => `inquiry:${inquiry.inquiryId}` === selectedKey));
  const selectedInquiry = inquirySpan?.inquiries?.find(inquiry => `inquiry:${inquiry.inquiryId}` === selectedKey);
  const selectedMarker: TimelineMarker | null = selectedEvent ? { kind: "event", event: selectedEvent, x: 0 }
    : selectedInquiry && inquirySpan ? { kind: "inquiry", inquiry: selectedInquiry, span: inquirySpan, x: 0 } : null;
  const selectedSpan = inquirySpan ?? timeline?.spans.find(span => `span:${span.spanId}` === selectedKey);
  const selectedAt = selectedMarker ? instant(markerAt(selectedMarker)) : selectedSpan ? instant(selectedSpan.startAt) : selectedRunId ? Math.min(...(timeline?.spans.filter(span => span.runId === selectedRunId).map(span => instant(span.startAt)).filter((at): at is number => at !== null) ?? []), ...(timeline?.events.filter(event => event.runId === selectedRunId).map(event => instant(event.at)).filter((at): at is number => at !== null) ?? [])) : null;
  const macroId = timeline?.macroTask.macroTaskId;
  const buddyColors = useMemo(() => buddyColorRegistry(macroId, timeline?.spans ?? []), [macroId, timeline]);
  // The scale may fill the whole viewport beside the sticky name column.
  const originX = narrow ? SLACK_PX : NAME_PX + SLACK_PX;
  const available = Math.max(1, width - (narrow ? SLACK_PX * 2 : NAME_PX + SLACK_PX * 2));
  const { scale, change: zoom, atMinimum, atMaximum } = useTimelineZoom(input, available, macroId, viewportRef, selectedAt !== null && Number.isFinite(selectedAt) ? selectedAt : null, lastPointerAt, nowMs, originX);
  useEffect(() => { setSelectedRunId(openedRunId); setSelectedKey(null); lastPointerAt.current = null; setPointerX(null); }, [macroId]);
  useEffect(() => {
    const previous = wasOpen.current;
    if (openedRunId !== previous || open && narrow) {
      const target = openedRunId ?? previous;
      movingDetailFocus.current = true;
      if (open && narrow) workspace?.querySelector<HTMLElement>('[data-slot=task-detail]')?.focus({ preventScroll: true });
      else if (target) rowsRef.current.get(target)?.focus({ preventScroll: true });
      movingDetailFocus.current = false;
    }
    wasOpen.current = openedRunId;
  }, [openedRunId, open, narrow, workspace]);
  const rows = timeline?.rows ?? [];
  const layout = useMemo(() => groupLanes(rows), [rows]);
  const summaries = useMemo(() => layout.lanes.map(lane => laneSummary(lane, timeline!)), [layout, timeline]);
  const spansByRow = useMemo(() => {
    const grouped = new Map<string, TimelineSpan[]>();
    for (const span of timeline?.spans ?? []) {
      const list = grouped.get(span.runId) ?? []; list.push(span); grouped.set(span.runId, list);
    }
    return grouped;
  }, [timeline]);
  const tabKey = focusKey ?? `row:${selectedRunId ?? rows[0]?.runId}`;
  const focusRow = (runId: string) => rowsRef.current.get(runId)?.focus();
  const fragments = (runId: string) => Array.from(document.querySelectorAll<SVGElement>("[data-slot=timeline-track] [data-key]"))
    .filter(element => element.closest("[data-run-id]")?.getAttribute("data-run-id") === runId)
    .sort((left, right) => Number(left.dataset.at) - Number(right.dataset.at));
  const rowLine = (row: TimelineRow) => {
    const spans = spansByRow.get(row.runId) ?? [];
    const turns = spans.filter(span => span.kind === "execution").length;
    const wait = spans.find(span => (span.kind === "queue" && span.state === "queued" || span.kind === "host" && span.state === "open") && span.endAt === null);
    const lastEnd = row.state === "delivered" && spans.filter(span => span.kind === "execution").every(span => span.shutdownConfirmed === true)
      ? Math.max(0, ...spans.filter(span => span.kind === "execution").map(span => instant(span.endAt) ?? 0)) : 0;
    const start = wait ? instant(wait.startAt) : lastEnd || null;
    return [rowStateInfo(row, rows).label, start !== null ? t("openWait", { duration: formatDuration(Math.max(0, nowMs - start)) }) : null, t("turnCount", { n: turns }), buddyLabel(row.buddy, props.buddies)].filter(Boolean).join(" · ");
  };
  const activeSpan = hoveredSpan ?? (hoveredRow ? null : focusedSpan ?? selectedSpan);
  const activeRow = hoveredRow ?? focusedRow ?? rows.find(row => row.runId === selectedRunId);
  const pointerGap = scale?.breaks.find(gap => pointerX !== null && pointerX >= gap.startX && pointerX <= gap.endX);
  const pointerLine = pointerGap ? t("drawing.gap", { start: clockTime(pointerGap.startMs), end: clockTime(pointerGap.endMs), duration: formatDuration(pointerGap.endMs - pointerGap.startMs) }) : null;
  const marker = activeMarker ?? (!hoveredRow && !hoveredSpan && !focusedRow && !focusedSpan ? selectedMarker : null);
  const facts = marker ? markerReadout(marker) : activeSpan ? t("segment", { label: spanReadout(activeSpan, nowMs, props.buddies, rows.find(row => row.runId === activeSpan.runId)) }) : activeRow ? rowLine(activeRow)
    : input?.domain ? t("range", { start: clockTime(input.domain.startMs), end: clockTime(input.domain.endMs), n: rows.length }) : t("noTime");
  const readout = pointerLine ?? facts;
  const incomplete = timeline && (timeline.filtered || !timeline.scopeComplete || Object.values(timeline.truncated).some(Boolean));
  const select = (runId: string, key?: string) => {
    setSelectedRunId(runId); setSelectedKey(key ?? null);
    setPointerX(null); setHoveredSpan(null); setFocusedSpan(null); setHoveredRow(null); setFocusedRow(null); setActiveMarker(null);
  };
  const renderName = (row: TimelineRow, options: { list?: boolean; tall?: boolean; indent?: boolean; ariaPrefix?: string; hoverExtra?: ReactNode } = {}) => <RowName
    key={row.runId}
    ref={element => { if (element) rowsRef.current.set(row.runId, element); else rowsRef.current.delete(row.runId); }}
    row={row} rows={rows} list={options.list} tall={options.tall} indent={options.indent} ariaPrefix={options.ariaPrefix} hoverExtra={options.hoverExtra}
    tabIndex={tabKey === `row:${row.runId}` ? 0 : -1} current={(options.list ? openedRunId : selectedRunId) === row.runId} fresh={props.newRunIds.has(row.runId)} buddies={props.buddies}
    onClick={() => { select(row.runId); props.onOpen(row.runId); }}
    onEnter={() => { setPointerX(null); setActiveMarker(null); setHoveredSpan(null); setHoveredRow(row); }} onLeave={() => setHoveredRow(null)} onFocus={event => { if (movingDetailFocus.current) event.preventDefault(); setPointerX(null); setHoveredRow(null); setHoveredSpan(null); setActiveMarker(null); setFocusedRow(row); }} onBlur={() => setFocusedRow(null)}/>;
  const trackProps = (row: TimelineRow) => ({
    boundary: viewport, tabKey, selected: selectedRunId === row.runId, row, spans: spansByRow.get(row.runId) ?? [],
    events: timeline!.events.filter(event => event.runId === row.runId),
    onMarker: (marker: TimelineMarker | null) => { setActiveMarker(marker); if (marker) setHoveredSpan(null); },
    buddyColors, scale: scale!, nowMs, ongoing: input!.nowAtEnd, buddies: props.buddies, onOpen: props.onOpen, onSelect: select,
    onFocusSpan: (span: TimelineSpan | null) => { setPointerX(null); setHoveredRow(null); setHoveredSpan(null); setActiveMarker(null); setFocusedSpan(span); },
    onHover: (span: TimelineSpan | null) => { setActiveMarker(null); setHoveredSpan(span); if (span) setHoveredRow(null); },
  });
  const trackRow = (row: TimelineRow, inLane: boolean) => <div key={row.runId} data-slot="timeline-row" data-run-id={row.runId}
    onClick={event => { if (!(event.target as Element).closest("[data-key]")) select(row.runId); }}
    onDoubleClick={event => { if (!(event.target as Element).closest("[data-key]")) props.onOpen(row.runId); }}
    className={cn("relative transition-colors duration-150 motion-reduce:transition-none", inLane ? "bg-card" : "bg-background", selectedRunId === row.runId && "bg-live-wash")}>
    {narrow
      ? <><div className={cn("sticky left-0 z-10", inLane ? "bg-card" : "bg-background")} style={{ width: Math.max(1, width - 2) }}>{renderName(row, { indent: inLane })}</div>
        <Track {...trackProps(row)}/></>
      : <div className="grid items-stretch" style={{ gridTemplateColumns: `${NAME_PX}px ${scale!.widthPx + SLACK_PX * 2}px minmax(0,1fr)` }}>
        <div className={cn("sticky left-0 z-10", inLane ? "bg-card" : "bg-background")}>{renderName(row, { indent: inLane })}</div>
        <Track {...trackProps(row)}/>
        <div className="border-t"/>
      </div>}
  </div>;
  const toggleLane = (leadRunId: string) => setCollapsedLanes(previous => {
    const next = new Set(previous);
    if (next.has(leadRunId)) next.delete(leadRunId); else next.add(leadRunId);
    return next;
  });
  const laneToggle = (lead: TimelineRow, collapsed: boolean) => <button type="button" data-slot="lane-toggle" aria-expanded={!collapsed} aria-controls={`lane-${lead.runId}`}
    aria-label={t("laneToggle", { title: displayTitle(lead.titleSource, lead.title).fullText })} onClick={() => toggleLane(lead.runId)}
    className="flex size-7 shrink-0 self-center items-center justify-center outline-none focus-visible:ring-2 focus-visible:ring-live">
    <ChevronRight size={16} strokeWidth={1.5} className={cn("text-ink2 transition-[rotate] duration-150 motion-reduce:transition-none", !collapsed && "rotate-90")}/>
  </button>;
  const laneHeader = (lane: LaneGroup, index: number) => {
    const lead = lane.lead;
    const collapsed = collapsedLanes.has(lead.runId);
    const summary = summaries[index]!;
    // The lane's own numbers ride on the Lead row's hover, where the reader asks for them.
    const summaryLines = <span className="block text-xs text-ink3 tabular-nums">
      <span className="block">{[
        t("laneMicroTasks", { n: lane.members.length + 1 }),
        `${t("elapsed")}${summary.elapsedMs !== null ? formatDuration(summary.elapsedMs) : t("unknown")}`,
        `${t("execution")}${summary.executionMs !== null ? formatDuration(summary.executionMs) : t("unknown")}`,
      ].join(" · ")}</span>
      <span className="block">{usageTotalsLabel(summary.usage)}</span>
    </span>;
    const leadName = () => <div className="flex min-w-0 flex-1 items-stretch">
      {renderName(lead, { tall: true, ariaPrefix: t("leadBadge"), hoverExtra: summaryLines })}
    </div>;
    // The divider under a Lead runs across the whole row; the members below it sit one indent step in.
    return <header data-slot="timeline-lane-lead" data-run-id={lead.runId} className={cn("border-b-2 border-ink2", narrow ? "block" : "grid items-stretch")} style={{ gridTemplateColumns: narrow ? undefined : `${NAME_PX}px ${scale!.widthPx + SLACK_PX * 2}px minmax(0,1fr)` }}>
      {narrow
        ? <div className="sticky left-0 z-10 bg-card" style={{ width: Math.max(1, width - 2) }}>
          <div className="flex items-stretch">{laneToggle(lead, collapsed)}<span className="flex shrink-0 items-center text-xs text-ink2">{t("leadBadge")}</span>{leadName()}</div>
        </div>
        : <div className="sticky left-0 z-20 flex items-stretch bg-card">
          {laneToggle(lead, collapsed)}
          <span className="flex shrink-0 items-center text-xs text-ink2">{t("leadBadge")}</span>
          {leadName()}
        </div>}
      {scale && input && <div className={narrow ? "block" : "flex items-center"}>
        <Track {...trackProps(lead)} ownedWaits={ownedWaits(lead, timeline!.spans, nowMs)} height={LEAD_PX}/>
      </div>}
    </header>;
  };
  const trackWidth = scale ? scale.widthPx + SLACK_PX * 2 : 0;
  const lanesScroll = useRef<{ left: number; top: number } | null>(null);
  useEffect(() => {
    if (open || !viewport || !lanesScroll.current) return;
    viewport.scrollLeft = lanesScroll.current.left;
    viewport.scrollTop = lanesScroll.current.top;
  }, [open, viewport]);
  const laneOrder = useMemo(() => [
    ...layout.lanes.flatMap(lane => collapsedLanes.has(lane.lead.runId) ? [lane.lead] : [lane.lead, ...lane.members]),
    ...layout.ordinary,
  ], [layout, collapsedLanes]);
  const lanesView = <ScrollArea className="min-h-0 min-w-0 flex-1 bg-background" orientation="both"
    viewportProps={{ ref: setViewport, "data-slot": "timeline-tracks",
      onScroll: event => { lanesScroll.current = { left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop }; },
      onPointerMove: event => {
        if (!scale) return;
        const target = (event.target as Element).closest("svg");
        if (!target || !["timeline-track", "timeline-axis"].includes(target.getAttribute("data-slot") ?? "")) return;
        const x = Math.max(0, Math.min(scale.widthPx, event.clientX - target.getBoundingClientRect().left - SLACK_PX));
        setPointerX(x); lastPointerAt.current = scale.toTime(x);
        const runId = target.closest("[data-run-id]")?.getAttribute("data-run-id");
        setHoveredRow(rows.find(row => row.runId === runId) ?? null);
      },
      onPointerLeave: () => { setPointerX(null); setHoveredSpan(null); setHoveredRow(null); setActiveMarker(null); } }}>
    {scale && input && <div data-slot="timeline-canvas" data-ppm={scale.pixelsPerMinute} className="relative" style={{ width: narrow ? trackWidth : `max(100%, ${NAME_PX + trackWidth}px)`, minWidth: "100%" }}>
      <div data-slot="timeline-ruler" className="sticky top-0 z-30 border-b border-line bg-card" style={narrow ? undefined : { display: "grid", gridTemplateColumns: `${NAME_PX}px ${trackWidth}px minmax(0,1fr)` }}>
        {narrow
          ? <Axis scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs} pointerX={pointerX} pointerBounds={{ left: Math.max(0, (viewport?.scrollLeft ?? 0) - originX), right: Math.min(scale.widthPx, (viewport?.scrollLeft ?? 0) - originX + width) }}/>
          : <><div className="sticky left-0 z-40 flex h-9 items-center justify-between bg-card pl-4 pr-3 text-xs text-ink3"><span>{t("microTasks")}</span><span>{rows.length}</span></div>
            <Axis scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs} pointerX={pointerX} pointerBounds={{ left: Math.max(0, (viewport?.scrollLeft ?? 0) - originX), right: Math.min(scale.widthPx, (viewport?.scrollLeft ?? 0) - originX + width) }}/>
            <div className="bg-card"/></>}
      </div>
      {layout.lanes.map((lane, index) => <section key={lane.lead.runId} data-slot="timeline-lane" className={cn("border-t-2 border-ink2 bg-card", index > 0 && "mt-6")}>
        {laneHeader(lane, index)}
        <Collapsible open={!collapsedLanes.has(lane.lead.runId)}>
          <CollapsibleContent>
            <div id={`lane-${lane.lead.runId}`}>{lane.members.map(row => trackRow(row, true))}</div>
          </CollapsibleContent>
        </Collapsible>
      </section>)}
      {layout.lanes.length > 0 && layout.ordinary.length > 0 && <div className="sticky left-0 mt-6 flex h-9 w-max items-center bg-background px-4 text-xs text-ink3">{t("otherMicroTasks")}</div>}
      {layout.ordinary.map(row => trackRow(row, false))}
      {pointerX !== null && <div data-slot="timeline-pointer" aria-hidden="true" className="pointer-events-none absolute top-9 bottom-0 z-20" style={{ left: pointerX + (narrow ? 0 : NAME_PX), borderLeft: "1px solid var(--ink3)" }}/>}
    </div>}
  </ScrollArea>;

  return <section ref={setWorkspace} data-slot="task-work" onKeyDownCapture={event => {
    // A title tooltip must not consume the detail's close gesture first.
    if (open && event.key === "Escape" && !(event.target as Element).closest("input, textarea, select, [role=dialog]")) {
      event.preventDefault(); props.onClose();
    }
  }} onFocusCapture={event => {
    const key = (event.target as Element).closest("[data-key]")?.getAttribute("data-key");
    if (key) { setFocusKey(key); setPointerX(null); setHoveredRow(null); setHoveredSpan(null); setActiveMarker(null); }
  }} onBlurCapture={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusKey(null);
  }} onKeyDown={event => {
    if (!open && ["+", "=", "-", "0"].includes(event.key) && !(event.target as Element).closest("input, textarea, select, [role=dialog]")) {
      event.preventDefault(); zoom(event.key === "0" ? 0 : event.key === "-" ? -1 : 1); return;
    }
    if (!(event.target as Element).closest("[data-slot=task-names], [data-slot=timeline-tracks], [data-slot=timeline-detail-stage]")) return;
    if (event.defaultPrevented || (event.target as Element).closest("input, textarea, select, [role=dialog]")) return;
    const element = (event.target as Element).closest("[data-key]");
    const runId = open ? openedRunId : element?.closest("[data-run-id]")?.getAttribute("data-run-id");
    if (!runId) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const order = open ? rows : laneOrder;
      const index = order.findIndex(row => row.runId === runId);
      const next = order[Math.max(0, Math.min(order.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
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
        {!open && <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 text-xs text-ink2 sm:px-6">
          <div data-slot="timeline-legend" className="flex flex-wrap gap-4"><span className="flex items-center gap-1.5"><i className="h-2 w-4 border border-line2 bg-past-wash"/>{t("execution")}</span><span className="flex items-center gap-1.5"><i className="w-4 border-t border-ink3"/>{t("wait")}</span><span className="flex items-center gap-1.5"><i className="w-4 border-t-2 border-dashed border-hand-mark"/>{t("waiting")}</span><span className="flex items-center gap-1.5"><i className="h-2 w-4 border border-hand-mark bg-hand-wash"/>{t("drawing.silentLegend")}</span><span className="flex items-center gap-1.5"><i className="size-2 rotate-45 border border-ink2"/>{t("drawing.inquiry")}</span><span className="flex items-center gap-1.5"><i className="size-1.5 rounded-full bg-past"/>{t("drawing.eventLegend")}</span><span className="flex items-center gap-1.5"><i className="flex size-4 items-center justify-center rounded-full border border-past">2</i>{t("drawing.clusterLegend")}</span><span className="flex items-center gap-1.5"><i className="h-3 w-4 bg-[repeating-linear-gradient(135deg,var(--sunk)_0_3px,var(--idle-stripe)_3px_4px)]"/>{t("drawing.idleLegend")}</span></div>
          <ZoomControls className="ml-auto" onChange={zoom} atMinimum={atMinimum} atMaximum={atMaximum}/>
        </div>}
        {open
          ? <div data-slot="timeline-detail-stage" className={cn("grid min-h-0 min-w-0 flex-1", narrow ? "grid-cols-1" : "grid-cols-[272px_minmax(0,1fr)]")}>
            <ScrollArea className={cn("min-h-0 min-w-0 bg-card", narrow && "hidden")}
              viewportProps={{ "data-slot": "task-names" }}>
              <div className="w-0 min-w-full">
              <div className="flex h-9 shrink-0 items-center justify-between px-3 text-xs text-ink3"><span>{t("microTasks")}</span><span>{rows.length}</span></div>
              {rows.map(row => renderName(row, { list: true }))}
              {!rows.some(row => row.runId === openedRunId) && <Button variant="ghost" size="sm" className="mt-2" onClick={props.onClose}>{t("closeDetail")}</Button>}
              </div>
            </ScrollArea>
            <div className={cn("flex min-h-0 min-w-0 flex-col bg-card", !narrow && "border-l")}>
              {openedRunId !== null && props.detail(openedRunId)}
            </div>
          </div>
          : <div role="region" aria-label={t("laneRegion")} className="flex min-h-0 min-w-0 flex-1 flex-col">{lanesView}</div>}
        {!open && <div data-slot="timeline-status" role="status" className="flex h-9 shrink-0 items-center gap-2 overflow-hidden border-t bg-card px-4 text-xs text-ink2 sm:px-6"><span className="truncate">{readout}{incomplete ? ` · ${t("uncompressed")}` : ""}</span></div>}
      </>}
  </section>;
}
