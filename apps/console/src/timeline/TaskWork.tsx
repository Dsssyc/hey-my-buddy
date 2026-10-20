import { cn } from "cn";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../components/ui/button";
import { Collapsible, CollapsibleContent, Disclosure } from "../components/ui/collapsible";
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
import { Legend } from "./Legend";
import { NameDivider, useNameWidth } from "./NameDivider";
import { Hint } from "../components/hint";
import { ZoomRuler } from "./ZoomRuler";
import { useTimelineGestures } from "./useTimelineGestures";
import { buddyColorsFor } from "./buddyColors";
import { groupLanes, laneLevel, laneSummary, ownedWaits } from "./lanes";
import type { LaneGroup } from "./lanes";
import { markerAt, markerKey } from "./MarkerCluster";
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
  const { t } = useTranslation(["tasks", "taskDetail"]);
  const { timeline, openedRunId, displayObservedAt } = props;
  const [nameWidth, setNameWidth] = useNameWidth();
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState(openedRunId);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  useEffect(() => { if (openedRunId !== null) setSelectedRunId(openedRunId); }, [openedRunId]);
  const [hoveredRow, setHoveredRow] = useState<TimelineRow | null>(null);
  const [focusedRow, setFocusedRow] = useState<TimelineRow | null>(null);
  const [focusedSpan, setFocusedSpan] = useState<TimelineSpan | null>(null);
  const [pointerScreenX, setPointerScreenX] = useState<number | null>(null);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [hoveredMarker, setHoveredMarker] = useState<TimelineMarker | null>(null);
  const [focusedMarker, setFocusedMarker] = useState<TimelineMarker | null>(null);
  const [hoveredSpan, setHoveredSpan] = useState<TimelineSpan | null>(null);
  const activeMarker = hoveredMarker ?? (hoveredSpan || hoveredRow ? null : focusedMarker);
  const [collapsedLanes, setCollapsedLanes] = useState<ReadonlySet<string>>(() => new Set());
  const { viewport: workspace, setViewport: setWorkspace, width } = useViewport<HTMLElement>(1088);
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null);
  const [detailViewport, setDetailViewport] = useState<HTMLDivElement | null>(null);
  const [ruler, setRuler] = useState<HTMLDivElement | null>(null);
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
  const pointingSpan = hoveredSpan ?? (hoveredRow ? null : focusedSpan);
  const pointingRow = hoveredRow ?? focusedRow;
  const pointingAt = activeMarker ? instant(markerAt(activeMarker)) : pointingSpan ? instant(pointingSpan.startAt)
    : pointingRow ? Math.min(...(timeline?.spans.filter(span => span.runId === pointingRow.runId).map(span => instant(span.startAt)).filter((at): at is number => at !== null) ?? []), ...(timeline?.events.filter(event => event.runId === pointingRow.runId).map(event => instant(event.at)).filter((at): at is number => at !== null) ?? [])) : null;
  const anchorAt = pointingAt !== null && Number.isFinite(pointingAt) ? pointingAt : selectedAt !== null && Number.isFinite(selectedAt) ? selectedAt : null;
  const highlightedRunId = activeMarker ? (activeMarker.kind === "event" ? activeMarker.event.runId : activeMarker.span.runId) : pointingSpan?.runId ?? pointingRow?.runId ?? selectedRunId;
  const highlightedKey = activeMarker ? markerKey(activeMarker) : pointingSpan ? `span:${pointingSpan.spanId}` : pointingRow ? null : selectedKey;
  const macroId = timeline?.macroTask.macroTaskId;
  const buddyColors = useMemo(() => buddyColorsFor(timeline?.spans ?? []), [timeline]);
  // The scale may fill the whole viewport beside the sticky name column.
  const originX = narrow ? SLACK_PX : nameWidth + SLACK_PX;
  const available = Math.max(1, width - (narrow ? SLACK_PX * 2 : nameWidth + SLACK_PX * 2));
  const { scale, change: zoom, zoomAt, atMinimum } = useTimelineZoom(input, available, macroId, viewportRef, anchorAt, nowMs, originX);
  // Keep the pointer in viewport coordinates: zoom and scrolling move the
  // content beneath it, never the pointer itself.
  const pointerX = pointerScreenX === null || !scale || !narrow && pointerScreenX < nameWidth ? null
    : Math.max(0, Math.min(scale.widthPx, pointerScreenX + scrollLeft - originX));
  useLayoutEffect(() => { if (viewport) setScrollLeft(viewport.scrollLeft); }, [scale, viewport]);
  useTimelineGestures(viewport, ruler, zoomAt, () => zoom(0));
  useEffect(() => { setSelectedRunId(openedRunId); setSelectedKey(null); setPointerScreenX(null); }, [macroId]);
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
  const readout = activeMarker || hoveredSpan || focusedSpan ? facts : pointerLine ?? facts;
  const incomplete = timeline && (timeline.filtered || !timeline.scopeComplete || Object.values(timeline.truncated).some(Boolean));
  const select = (runId: string, key?: string) => {
    setSelectedRunId(runId); setSelectedKey(key ?? null);
    setPointerScreenX(null); setHoveredSpan(null); setFocusedSpan(null); setHoveredRow(null); setFocusedRow(null); setHoveredMarker(null); setFocusedMarker(null);
  };
  const renderName = (row: TimelineRow, options: { list?: boolean; tall?: boolean; laneLevel?: number; lead?: boolean; ariaPrefix?: string; hoverExtra?: ReactNode } = {}) => <RowName
    key={row.runId}
    ref={element => { if (element) rowsRef.current.set(row.runId, element); else rowsRef.current.delete(row.runId); }}
    row={row} rows={rows} list={options.list} tall={options.tall} laneLevel={options.laneLevel} lead={options.lead} ariaPrefix={options.ariaPrefix} hoverExtra={options.hoverExtra}
    tabIndex={tabKey === `row:${row.runId}` ? 0 : -1} current={options.list ? openedRunId === row.runId : selectedRunId === row.runId || highlightedRunId === row.runId} fresh={props.newRunIds.has(row.runId)} buddies={props.buddies}
    onClick={() => { select(row.runId); props.onOpen(row.runId); }}
    onEnter={() => { setPointerScreenX(null); setHoveredMarker(null); setHoveredSpan(null); setHoveredRow(row); }} onLeave={() => setHoveredRow(null)} onFocus={event => { if (movingDetailFocus.current) event.preventDefault(); setPointerScreenX(null); setHoveredRow(null); setHoveredSpan(null); setFocusedMarker(null); setFocusedRow(row); }} onBlur={() => setFocusedRow(null)}/>;
  const trackProps = (row: TimelineRow) => ({
    boundary: viewport, tabKey, selected: selectedRunId === row.runId || highlightedRunId === row.runId, row, spans: spansByRow.get(row.runId) ?? [],
    selectedKey: selectedRunId === row.runId ? selectedKey : null,
    highlightedKey: highlightedRunId === row.runId ? highlightedKey : null,
    events: timeline!.events.filter(event => event.runId === row.runId),
    onMarker: (marker: TimelineMarker | null, source: "hover" | "focus") => { if (source === "hover") { setHoveredMarker(marker); if (marker) setHoveredSpan(null); } else setFocusedMarker(marker); },
    buddyColors, scale: scale!, nowMs, ongoing: input!.nowAtEnd, buddies: props.buddies, onOpen: props.onOpen, onSelect: select,
    onFocusSpan: (span: TimelineSpan | null) => { setPointerScreenX(null); setHoveredRow(null); setHoveredSpan(null); setFocusedMarker(null); setFocusedSpan(span); },
    onHover: (span: TimelineSpan | null) => { setHoveredMarker(null); setHoveredSpan(span); if (span) setHoveredRow(null); },
  });
  const trackRow = (row: TimelineRow, lead?: TimelineRow) => <div key={row.runId} data-slot="timeline-row" data-run-id={row.runId}
    onClick={event => { if (!(event.target as Element).closest("[data-key]")) select(row.runId); }}
    className={cn("relative", lead ? "bg-card" : "bg-background")}>
    {narrow
      ? <><div className={cn("sticky left-0 z-10", lead ? "bg-card" : "bg-background")} style={{ width: Math.max(1, width - 2) }}>{renderName(row, { laneLevel: lead ? laneLevel(lead, row) : undefined })}</div>
        <Track {...trackProps(row)}/></>
      : <div className="grid items-stretch" style={{ gridTemplateColumns: `${nameWidth}px ${scale!.widthPx + SLACK_PX * 2}px minmax(0,1fr)` }}>
        <div className={cn("sticky left-0 z-10", lead ? "bg-card" : "bg-background")}>{renderName(row, { laneLevel: lead ? laneLevel(lead, row) : undefined })}</div>
        <Track {...trackProps(row)}/>
        <div className={cn("border-t", (selectedRunId === row.runId || highlightedRunId === row.runId) && "bg-selected-row")}/>
      </div>}
  </div>;
  const toggleLane = (leadRunId: string) => setCollapsedLanes(previous => {
    const next = new Set(previous);
    if (next.has(leadRunId)) next.delete(leadRunId); else next.add(leadRunId);
    return next;
  });
  const laneToggle = (lead: TimelineRow, collapsed: boolean) => <Hint text={t("laneToggle", { title: displayTitle(lead.titleSource, lead.title).fullText })}><Button type="button" variant="ghost" size="icon-sm" data-slot="lane-toggle" aria-expanded={!collapsed} aria-controls={`lane-${lead.runId}`}
    aria-label={t("laneToggle", { title: displayTitle(lead.titleSource, lead.title).fullText })} onClick={() => toggleLane(lead.runId)}
    className="self-center">
    <Disclosure open={!collapsed}/>
  </Button></Hint>;
  const laneFacts = (index: number) => {
    const lane = layout.lanes[index]!;
    const summary = summaries[index]!;
    // The lane's own numbers ride on the Lead row's hover, where the reader asks for them.
    return <span className="block text-xs text-ink3 tabular-nums">
      <span className="block">{[
        t("laneMicroTasks", { n: lane.members.length + 1 }),
        `${t("elapsed")}${summary.elapsedMs !== null ? formatDuration(summary.elapsedMs) : t("unknown")}`,
        `${t("execution")}${summary.executionMs !== null ? formatDuration(summary.executionMs) : t("unknown")}`,
      ].join(" · ")}</span>
      <span className="block">{usageTotalsLabel(summary.usage)}</span>
    </span>;
  };
  const laneHeader = (lane: LaneGroup, index: number) => {
    const lead = lane.lead;
    const collapsed = collapsedLanes.has(lead.runId);
    const leadName = () => <div className="flex min-w-0 flex-1 items-stretch">
      {renderName(lead, { tall: true, lead: true, ariaPrefix: t("leadBadge"), hoverExtra: laneFacts(index) })}
    </div>;
    // The divider under a Lead runs across the whole row; the members below it sit one indent step in.
    return <header data-slot="timeline-lane-lead" data-run-id={lead.runId} onClick={event => { if (!(event.target as Element).closest("[data-key], button")) select(lead.runId); }} className={cn("border-b border-line", narrow ? "block" : "grid items-stretch")} style={{ gridTemplateColumns: narrow ? undefined : `${nameWidth}px ${scale!.widthPx + SLACK_PX * 2}px minmax(0,1fr)` }}>
      {narrow
        ? <div className="sticky left-0 z-10 bg-card" style={{ width: Math.max(1, width - 2) }}>
          <div className="flex items-stretch">{laneToggle(lead, collapsed)}{leadName()}</div>
        </div>
        : <div className="sticky left-0 z-20 flex items-stretch bg-card">
          {laneToggle(lead, collapsed)}
          {leadName()}
        </div>}
      {scale && input && <div className={narrow ? "block" : "flex items-center"}>
        <Track {...trackProps(lead)} ownedWaits={ownedWaits(lead, timeline!.spans, nowMs)} height={LEAD_PX}/>
      </div>}
      {!narrow && <div className={cn((selectedRunId === lead.runId || highlightedRunId === lead.runId) && "bg-selected-row")}/>}
    </header>;
  };
  const trackWidth = scale ? scale.widthPx + SLACK_PX * 2 : 0;
  const lanesScrollLeft = useRef(0);
  const namesScrollTop = useRef(0);
  useLayoutEffect(() => {
    const target = open ? detailViewport : viewport;
    if (!target) return;
    target.scrollTop = namesScrollTop.current;
    if (!open) target.scrollLeft = lanesScrollLeft.current;
  }, [open, viewport, detailViewport]);
  const laneOrder = useMemo(() => [
    ...layout.lanes.flatMap(lane => collapsedLanes.has(lane.lead.runId) ? [lane.lead] : [lane.lead, ...lane.members]),
    ...layout.ordinary,
  ], [layout, collapsedLanes]);
  const detailNames = <>
    {layout.lanes.map((lane, index) => {
      const lead = lane.lead;
      const collapsed = collapsedLanes.has(lead.runId);
      return <section key={lead.runId} data-slot="detail-lane" className={cn("border-t border-line bg-card", index > 0 && "mt-6")}>
        <div data-slot="detail-lane-lead" data-run-id={lead.runId} className="flex items-stretch border-b border-line">
          {laneToggle(lead, collapsed)}
          <div className="min-w-0 flex-1">{renderName(lead, { list: true, tall: true, lead: true, ariaPrefix: t("leadBadge"), hoverExtra: laneFacts(index) })}</div>
        </div>
        <Collapsible open={!collapsed}><CollapsibleContent><div id={`lane-${lead.runId}`}>
          {lane.members.map(row => renderName(row, { list: true, laneLevel: laneLevel(lead, row) }))}
        </div></CollapsibleContent></Collapsible>
      </section>;
    })}
    {layout.lanes.length > 0 && layout.ordinary.length > 0 && <div className="mt-6 flex h-9 items-center px-4 text-xs text-ink3">{t("otherMicroTasks")}</div>}
    {layout.ordinary.map(row => renderName(row, { list: true }))}
  </>;
  const lanesView = <div data-slot="timeline-viewport" className="relative flex min-h-0 min-w-0 flex-1"><ScrollArea className="min-h-0 min-w-0 flex-1 bg-background" orientation="both"
    viewportProps={{ ref: setViewport, "data-slot": "timeline-tracks",
      onScroll: event => { lanesScrollLeft.current = event.currentTarget.scrollLeft; namesScrollTop.current = event.currentTarget.scrollTop; setScrollLeft(event.currentTarget.scrollLeft); },
      onPointerMove: event => {
        if (!scale) return;
        const view = event.currentTarget;
        const localX = event.clientX - view.getBoundingClientRect().left;
        if (!narrow && localX < nameWidth) { setPointerScreenX(null); return; }
        const target = (event.target as Element);
        setPointerScreenX(localX);
        const runId = target.closest("[data-run-id]")?.getAttribute("data-run-id");
        setHoveredRow(rows.find(row => row.runId === runId) ?? null);
      },
      onPointerLeave: () => { setPointerScreenX(null); setHoveredSpan(null); setHoveredRow(null); setHoveredMarker(null); } }}>
    {scale && input && <div data-slot="timeline-canvas" data-ppm={scale.pixelsPerMinute} className="relative min-h-full pb-8" style={{ width: narrow ? trackWidth : `max(100%, ${nameWidth + trackWidth}px)`, minWidth: "100%" }}>
      <div data-slot="timeline-ruler" className="sticky top-0 z-30 border-b border-line">
        <ZoomRuler ref={setRuler} viewportWidth={width} atMinimum={atMinimum} onChange={zoom} style={narrow ? undefined : { display: "grid", gridTemplateColumns: `${nameWidth}px ${trackWidth}px minmax(0,1fr)` }}>
        {narrow
          ? <Axis scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs} pointerX={pointerX} pointerBounds={{ left: Math.max(0, scrollLeft - originX), right: Math.min(scale.widthPx, scrollLeft - originX + width) }}/>
          : <><div className="sticky left-0 z-40 flex h-9 items-center justify-between bg-ruler pl-4 pr-3 text-xs text-ink3"><span>{t("microTasks")}</span><span>{rows.length}</span></div>
            <Axis scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs} pointerX={pointerX} pointerBounds={{ left: Math.max(0, scrollLeft - originX), right: Math.min(scale.widthPx, scrollLeft - originX + width) }}/>
            <div className="bg-ruler"/></>}
        </ZoomRuler>
      </div>
      {layout.lanes.map((lane, index) => <section key={lane.lead.runId} data-slot="timeline-lane" className={cn("border-t border-line bg-card", index > 0 && "mt-6")}>
        {laneHeader(lane, index)}
        <Collapsible open={!collapsedLanes.has(lane.lead.runId)}>
          <CollapsibleContent>
            <div id={`lane-${lane.lead.runId}`}>{lane.members.map(row => trackRow(row, lane.lead))}</div>
          </CollapsibleContent>
        </Collapsible>
      </section>)}
      {layout.lanes.length > 0 && layout.ordinary.length > 0 && <div className="sticky left-0 mt-6 flex h-9 w-max items-center bg-background px-4 text-xs text-ink3">{t("otherMicroTasks")}</div>}
      {layout.ordinary.map(row => trackRow(row))}
    </div>}
  </ScrollArea>
    {!narrow && <NameDivider width={nameWidth} onChange={setNameWidth}/>}
    {pointerX !== null && <div data-slot="timeline-pointer" aria-hidden="true" className="pointer-events-none absolute inset-y-0 z-40 border-l border-ink3" style={{ left: pointerScreenX! }}/>}
  </div>;

  return <section ref={setWorkspace} data-slot="task-work" onKeyDownCapture={event => {
    // A title tooltip must not consume the detail's close gesture first.
    if (open && event.key === "Escape" && !(event.target as Element).closest("input, textarea, select, [role=dialog]")) {
      event.preventDefault(); props.onClose();
    }
  }} onFocusCapture={event => {
    const key = (event.target as Element).closest("[data-key]")?.getAttribute("data-key");
    if (key) { setFocusKey(key); setPointerScreenX(null); setHoveredRow(null); setHoveredSpan(null); setHoveredMarker(null); }
  }} onBlurCapture={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocusKey(null);
  }} onKeyDown={event => {
    if (event.defaultPrevented) return;
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
      const order = laneOrder;
      const index = order.findIndex(row => row.runId === runId);
      const hiddenLead = index < 0 ? rows.find(row => row.runId === runId)?.rootLeadRunId : null;
      const position = index < 0 && hiddenLead ? order.findIndex(row => row.runId === hiddenLead) : index;
      const next = order[Math.max(0, Math.min(order.length - 1, position + (event.key === "ArrowDown" ? 1 : -1)))];
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
        {open
          ? <div data-slot="timeline-detail-stage" className="relative grid min-h-0 min-w-0 flex-1" style={{ gridTemplateColumns: narrow ? "minmax(0,1fr)" : `${nameWidth}px minmax(0,1fr)` }}>
            <ScrollArea className={cn("min-h-0 min-w-0 bg-card", narrow && "hidden")}
              viewportProps={{ ref: setDetailViewport, "data-slot": "task-names", onScroll: event => { namesScrollTop.current = event.currentTarget.scrollTop; } }}>
              <div className="w-0 min-w-full">
              <div className="sticky top-0 z-30 flex h-[37px] shrink-0 items-center justify-between border-b border-line bg-ruler pl-4 pr-3 text-xs text-ink3"><span>{t("microTasks")}</span><span>{rows.length}</span></div>
              {detailNames}
              {!rows.some(row => row.runId === openedRunId) && <Button variant="ghost" size="sm" className="mt-2" onClick={props.onClose}>{t("closeDetail")}</Button>}
              </div>
            </ScrollArea>
            {!narrow && <NameDivider width={nameWidth} onChange={setNameWidth}/>}
            <div className={cn("flex min-h-0 min-w-0 flex-col bg-card", !narrow && "border-l")}>
              {openedRunId !== null && props.detail(openedRunId)}
            </div>
          </div>
          : <div role="region" aria-label={t("laneRegion")} className="flex min-h-0 min-w-0 flex-1 flex-col">{lanesView}</div>}
        <div data-slot="timeline-status" className="flex min-h-9 shrink-0 items-center gap-2 border-t bg-card px-4 text-xs text-ink2 sm:px-6"><span role="status" className="min-w-0 flex-1 truncate">{!open && <>{readout}{incomplete ? ` · ${t("uncompressed")}` : ""}</>}</span><Legend/></div>
      </>}
  </section>;
}
