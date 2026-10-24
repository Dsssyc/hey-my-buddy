import { buddyUsesLightInk, leadingBuddyColor, runningBuddyColor } from "./buddyColors";
import { IsolatedStatusMark } from "./IsolatedStatusMark";
import { HarnessLogo } from "../buddies/HarnessLogo";
import { Hint } from "../components/hint";
import { useId, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import type { Ref } from "react";
import i18n from "i18next";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { clockTime, spanOutcome, outcomeLabel, buddyLabel, buddyNameParts, buddyColorKey, displayTitle, eventDetails, eventName } from "../macro-task-display";
import type { TimelineEvent, TimelineRow, TimelineSpan } from "../macro-task-types";
import type { FriendlyBuddy } from "../macro-task-display";
import type { TimelineScale } from "./scale";
import { parseTimelineInstant as instant } from "./instant";
import { formatClock, formatClockMinutes, formatDuration } from "../i18n/format";
import { acceptanceInterval } from "./acceptance";
import { fitAdjacentLabel, fitLabel, useTextMeasure } from "./labels";
import type { Interval, Label, Occupancy } from "./labels";
import { MarkerCluster, markerAt, markerKey } from "./MarkerCluster";
import { ConclusionMark, CONCLUSION_OUTSET_PX as INSET_PX } from "./ConclusionMark";
import { CLUSTER_SELECTION_PADDING_PX, clusterHalfWidth } from "./ClusterMark";
import type { TimelineMarker } from "./MarkerCluster";
import type { OwnedWait } from "./lanes";

const EVENT_SELECTION_RADIUS_PX = 10;
const INQUIRY_SELECTION_RADIUS_PX = 9;

function spanSelection(start: number, end: number): Interval {
  const left = Math.max(-6, start - 2);
  return { left, right: left + Math.max(5, end - start + 4) };
}

export function eventReadout(event: TimelineEvent): string {
  return [eventName(event), clockTime(event.at), eventDetails(event)].filter(Boolean).join(" · ");
}

export function markerReadout(marker: TimelineMarker): string {
  return marker.kind === "event" ? eventReadout(marker.event) : i18n.t("tasks:drawing.inquiryReadout", {
    at: clockTime(marker.inquiry.createdAt), state: i18n.t(`taskDetail:inquiry.${marker.inquiry.state}`),
  });
}

export function spanReadout(span: TimelineSpan, nowMs: number, buddies?: readonly FriendlyBuddy[] | null, row?: TimelineRow): string {
  const outcome = spanOutcome(span);
  const start = instant(span.startAt);
  const end = instant(span.endAt) ?? (outcome === "running" || outcome === "open" || outcome === "unknown" ? nowMs : null);
  const head = span.kind === "routing" ? i18n.t("tasks:drawing.routingMode", { mode: span.routing?.routingMode ? i18n.t(`taskDetail:routing.mode.${span.routing.routingMode}`) : i18n.t("console:notRecorded") })
    : span.turnIndex !== null ? i18n.t("tasks:turn", { n: span.turnIndex })
      : span.kind === "host" ? i18n.t(row?.rootLeadRunId ? "console:waitingForLead" : "console:waitingForHost") : i18n.t(`tasks:kind.${span.kind}`);
  const reminders = span.reminders;
  return [head, start === null ? null : clockTime(start), end === null ? null : clockTime(end),
    start !== null && end !== null && end >= start ? formatDuration(end - start) : null, buddyLabel(span.buddy, buddies), outcome === "unknown" ? i18n.t("tasks:drawing.stopUnconfirmed") : outcomeLabel(span, outcome),
    span.expectedSeconds === null ? null : i18n.t("tasks:drawing.expected", { duration: formatDuration(span.expectedSeconds * 1000) }),
    reminders?.possiblyStuck ? i18n.t("tasks:drawing.silent", { duration: formatDuration(nowMs - instant(reminders.lastNativeEventAt ?? span.startAt)!) }) : null,
    ...(span.inquiries ?? []).map(inquiry => i18n.t("tasks:drawing.inquiryReadout", { at: clockTime(inquiry.createdAt), state: i18n.t(`taskDetail:inquiry.${inquiry.state}`) })),
  ].filter(Boolean).join(" · ");
}

/** The same scale draws the one axis and all rows; waits cross every break. */
export function Axis({ scale, ongoing, nowMs, pointerX = null, pointerBounds, compact = false }: { scale: TimelineScale; ongoing: boolean; nowMs: number; pointerX?: number | null; pointerBounds?: Interval; compact?: boolean }) {
  const { t } = useTranslation("tasks");
  const axisRef = useRef<SVGSVGElement>(null);
  const [fitBounds, setFitBounds] = useState<Interval | null>(null);
  useLayoutEffect(() => {
    const axis = axisRef.current!;
    const ruler = axis.closest<HTMLElement>("[data-slot=zoom-ruler]");
    const viewport = ruler?.closest<HTMLElement>("[data-radix-scroll-area-viewport]");
    if (!ruler || !viewport) return;
    const fit = ruler.querySelector<HTMLElement>("[data-slot=zoom-fit]");
    const measure = () => {
      const axisBox = axis.getBoundingClientRect();
      const fitBox = fit?.getBoundingClientRect();
      const next = fitBox && fitBox.left - axisBox.left - INSET_PX < scale.widthPx && fitBox.right - axisBox.left - INSET_PX > 0
        ? { left: Math.max(0, fitBox.left - axisBox.left - INSET_PX), right: Math.min(scale.widthPx, fitBox.right - axisBox.left - INSET_PX) } : null;
      setFitBounds(previous => previous?.left === next?.left && previous?.right === next?.right ? previous : next);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(axis); observer.observe(viewport);
    if (fit) observer.observe(fit);
    viewport.addEventListener("scroll", measure, { passive: true });
    measure();
    return () => { observer.disconnect(); viewport.removeEventListener("scroll", measure); };
  }, [scale]);
  const pointer = pointerX === null || scale.breaks.some(gap => pointerX >= gap.startX && pointerX <= gap.endX) ? null : (scale.pixelsPerMinute > 30 ? formatClock : formatClockMinutes)(scale.toTime(pointerX)!);
  const width = scale.widthPx;
  const nowX = scale.toX(nowMs)!;
  const now = `${t("now")} ${clockTime(nowMs)}`;
  const gapLabels = scale.breaks.map(gap => formatDuration(gap.endMs - gap.startMs, "minutes"));
  const labeledTicks = scale.ticks.filter(tick => tick.labeled);
  const endpointTicks = labeledTicks.length > 1 ? [labeledTicks[0]!, labeledTicks.at(-1)!] : labeledTicks;
  // The detail strip has room for clocks according to its actual SVG width,
  // including when zoom changes that width. Endpoints get placement priority.
  const clockTicks = compact ? [...endpointTicks] : labeledTicks;
  if (compact) {
    let previousMiddleX = Number.NEGATIVE_INFINITY;
    for (const tick of labeledTicks.slice(1, -1)) {
      const x = scale.toX(tick.atMs)!;
      if (x - previousMiddleX < 200 || endpointTicks.some(endpoint => Math.abs(x - scale.toX(endpoint.atMs)!) < 200)) continue;
      clockTicks.push(tick);
      previousMiddleX = x;
    }
  }
  const { measure, ruler } = useTextMeasure([now, ...(pointer ? [pointer] : []), ...gapLabels, ...clockTicks.map(tick => clockTime(tick.atMs))]);
  const pointerWidth = pointer === null ? 0 : measure(pointer) + 8;
  const pointerRight = Math.min(pointerBounds?.right ?? width, fitBounds?.left ?? width);
  const pointerLeft = Math.max(pointerBounds?.left ?? 0, Math.min(pointerRight - pointerWidth - (fitBounds ? 4 : 0), pointerX ?? 0));
  const nowLeft = Math.max(0, Math.min(width - measure(now), nowX - measure(now)));
  const occupied: Occupancy[] = [];
  if (fitBounds && fitBounds.right > fitBounds.left) occupied.push({ kind: "obstacle", left: fitBounds.left, right: fitBounds.right });
  const showNow = ongoing && !occupied.some(item => nowLeft < item.right + 4 && nowLeft + measure(now) > item.left - 4);
  if (showNow) occupied.push({ kind: "label", left: nowLeft, right: nowLeft + measure(now) });
  const visibleGapLabels = new Set<number>();
  scale.breaks.forEach((gap, index) => {
    const center = (gap.startX + gap.endX) / 2, labelWidth = measure(gapLabels[index]!);
    const left = center - labelWidth / 2, right = center + labelWidth / 2;
    const label = fitLabel([gapLabels[index]!], { left, right }, occupied, measure);
    if (left >= 0 && right <= width && label) {
      visibleGapLabels.add(gap.startMs);
      occupied.push(label);
    }
    occupied.push({ kind: "obstacle", left: gap.startX, right: gap.endX });
  });
  const labels: Label[] = [];
  for (const tick of clockTicks) {
    const text = clockTime(tick.atMs), x = scale.toX(tick.atMs)!;
    const label = fitLabel([text], { left: 0, right: width }, occupied, measure, x - measure(text) / 2);
    // A clock stays with its tick, even if no room is left for its text.
    if (label && Math.abs((label.left + label.right) / 2 - x) <= measure(text) / 2 + 4) { labels.push({ ...label, y: 16, className: "fill-ink2" }); occupied.push(label); }
  }
  return <svg ref={axisRef} data-slot="timeline-axis" viewBox={`${-INSET_PX} 0 ${width + INSET_PX * 2} 36`} width={width + INSET_PX * 2} height="36" className="block max-w-none select-none overflow-hidden text-xs" aria-hidden="true">
    {ruler}<line data-slot="axis-baseline" x1={0} x2={width} y1={30} y2={30} className="stroke-ink3"/>
    {scale.breaks.map((gap, index) => { const center = (gap.startX + gap.endX) / 2; return <g key={gap.startMs}>
      <path data-slot="axis-break-mark" d={`M${center - 7} 34l6-8m0 8l6-8`} className="stroke-line"/>
      {visibleGapLabels.has(gap.startMs) && <text data-slot="gap-label" x={center} y={16} textAnchor="middle" className="fill-ink2">{gapLabels[index]}</text>}
    </g>; })}
    {scale.ticks.map(tick => <line key={tick.atMs} x1={scale.toX(tick.atMs)!} x2={scale.toX(tick.atMs)!} y1={tick.labeled ? 24 : 27} y2={31} className="stroke-ink3"/>)}
    {labels.map(label => <text data-slot="axis-label" key={label.left} x={label.left} y={label.y} className={label.className}>{label.text}</text>)}
    {showNow && measure(now) <= width && <text data-slot="axis-label" x={nowLeft} y={16} className="fill-live">{now}</text>}
    {pointer !== null && Number.isFinite(pointerWidth) && pointerRight - (pointerBounds?.left ?? 0) >= pointerWidth + 4 && <g data-slot="pointer-clock" transform={`translate(${pointerLeft} 0)`}>
      <rect width={pointerWidth} height={22} className="fill-popover"/>
      <text x={4} y={16} className="fill-ink">{pointer}</text>
    </g>}
  </svg>;
}

function IdlePattern({ id }: { id: string }) {
  return <defs><pattern id={id} patternUnits="userSpaceOnUse" width={8} height={8}>
    <rect width={8} height={8} className="fill-sunk"/>
    <path d="M-2 2l4-4M0 8l8-8M6 10l4-4" fill="none" stroke="var(--line)"/>
  </pattern></defs>;
}

type PositionedSpan = { span: TimelineSpan; start: number; end: number; execution: boolean; outcome: ReturnType<typeof spanOutcome> };
const diamond = (x: number, y: number) => `M${x} ${y - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`;

export function Track({ row, spans, events, scale, nowMs, ongoing, buddies, onOpen, onSelect, onHover, onFocusSpan, onMarker, buddyColors, selected, selectedKey = null, highlightedKey = null, ownedWaits, tabKey, boundary, height = 52, surface = "background", selectionRef }: {
  row: TimelineRow; spans: TimelineSpan[]; events: TimelineEvent[]; scale: TimelineScale; nowMs: number; ongoing: boolean;
  buddies?: readonly FriendlyBuddy[] | null; selected: boolean; tabKey?: string | null; boundary?: HTMLElement | null;
  /** Only this fragment is outlined; the selected row has its own neutral wash. */
  selectedKey?: string | null;
  highlightedKey?: string | null;
  /** The drawn selection's scale coordinates, computed from the same facts and dimensions as the SVG. */
  selectionRef?: Ref<Interval | null>;
  /** A Lead's waits for its owned micro tasks, drawn like other waits from the recorded facts. */
  ownedWaits?: readonly OwnedWait[];
  /** Row height; 52 keeps the bar, both name lines and their spacing proportionate, a lane header is taller. */
  height?: number;
  surface?: "background" | "card";
  onSelect: (runId: string, key?: string) => void;
  onOpen: (runId: string, key: string) => void; onHover: (span: TimelineSpan | null) => void; onFocusSpan: (span: TimelineSpan | null) => void;
  onMarker: (marker: TimelineMarker | null, source: "hover" | "focus") => void; buddyColors: ReadonlyMap<string, string>;
}) {
  const { t } = useTranslation("tasks");
  const pattern = useId();
  const isHighlighted = (key: string) => selectedKey === key || highlightedKey === key;
  const middle = height / 2;
  const paper = surface === "card" ? "stroke-card" : "stroke-background";
  const hitBox = (start: number, end: number) => {
    const width = Math.max(28, end - start + 6);
    return { x: Math.max(-INSET_PX, Math.min(scale.widthPx + INSET_PX - width, (start + end) / 2 - width / 2)), width };
  };
  // The extra width sits above and below the drawing, leaving neighbouring short bars reachable.
  const hitPath = (start: number, end: number) => {
    const { x, width } = hitBox(start, end);
    const right = Math.max(start + 1, end);
    return `M${x} ${middle - 14}H${x + width}V${middle - 11}H${right}V${middle + 11}H${x + width}V${middle + 14}H${x}V${middle + 11}H${start}V${middle - 11}H${x}Z`;
  };
  const barTextY = middle + 4;
  const topTextY = 12;
  const positioned: PositionedSpan[] = [];
  for (const span of spans) {
    const outcome = spanOutcome(span), startMs = instant(span.startAt);
    const endMs = instant(span.endAt) ?? (["running", "open", "unknown"].includes(outcome) ? nowMs : null);
    if (startMs === null || endMs === null) continue;
    const start = scale.toX(startMs), end = scale.toX(endMs);
    if (start === null || end === null || end < start) continue;
    positioned.push({ span, start, end, outcome, execution: span.kind === "execution" || span.kind === "routing" });
  }
  const wait = acceptanceInterval(row, spans, nowMs);
  const acceptanceOpen = wait?.open ?? false;
  const acceptance = wait ? { start: scale.toX(wait.startMs)!, end: scale.toX(wait.endMs)!, duration: formatDuration(wait.endMs - wait.startMs, "minutes") } : null;
  const isConclusion = (event: TimelineEvent) => event.kind === "accept" || event.kind === "conclude" && (row.state === "failed" || row.status === "failed");
  const markers = events.filter(event => event.kind !== "dispatch" && event.kind !== "integrate").map(event => ({ event, x: scale.toX(instant(event.at)!)! }));
  const inquiries = positioned.flatMap(({ span }) => (span.inquiries ?? []).map(inquiry => ({ inquiry, span, x: scale.toX(instant(inquiry.createdAt)!)! })));
  const allMarkers: TimelineMarker[] = [
    ...markers.map(({ event, x }) => ({ kind: "event" as const, event, x })),
    ...inquiries.map(({ inquiry, span, x }) => ({ kind: "inquiry" as const, inquiry, span, x })),
  ].sort((a, b) => a.x - b.x || markerKey(a).localeCompare(markerKey(b)));
  const markerGroups: TimelineMarker[][] = [];
  for (const marker of allMarkers) {
    const last = markerGroups.at(-1);
    if (last && marker.x - last.at(-1)!.x <= 16) last.push(marker);
    else markerGroups.push([marker]);
  }
  const singletonEvents = markerGroups.filter(group => group.length === 1 && group[0]!.kind === "event").map(group => group[0] as Extract<TimelineMarker, { kind: "event" }>);
  const singletonInquiries = markerGroups.filter(group => group.length === 1 && group[0]!.kind === "inquiry").map(group => group[0] as Extract<TimelineMarker, { kind: "inquiry" }>);
  const focusMarker = (marker: TimelineMarker | null, source: "hover" | "focus" = "focus") => {
    if (source === "focus") onFocusSpan(null);
    onMarker(marker, source);
  };
  const markerName = (marker: TimelineMarker) => marker.kind === "event" ? eventName(marker.event) : t("drawing.inquiry");
  const duration = (item: PositionedSpan) => formatDuration((instant(item.span.endAt) ?? nowMs) - instant(item.span.startAt)!, "minutes");
  const waitingKind = (span: TimelineSpan) => span.kind === "queue" ? t("kind.queue") : i18n.t(row.rootLeadRunId ? "console:waitingForLead" : "console:waitingForHost");
  const modelLabel = (span: TimelineSpan) => buddyNameParts(span.buddy, buddies).name;
  const identity = (span: TimelineSpan) => span.buddy ? [span.buddy.harness, span.buddy.provider, span.buddy.model, span.buddy.effort].map(value => value ?? t("unknown")).join(" / ") : t("unknown");
  const fullLabel = (item: PositionedSpan) => item.execution ? duration(item)!
    : t(item.span.kind === "queue" ? "drawing.queue" : "drawing.waiting", { kind: waitingKind(item.span), duration: duration(item) });
  const tails = positioned.filter(item => item.outcome === "running" && item.span.reminders?.possiblyStuck)
    .map(item => ({ item, x: scale.toX(instant(item.span.reminders!.lastNativeEventAt ?? item.span.startAt)!)! }));
  const expected = positioned.filter(item => item.outcome === "running" && item.span.expectedSeconds !== null)
    .map(item => ({ item, at: instant(item.span.startAt)! + item.span.expectedSeconds! * 1000 }))
    .filter(entry => entry.at < nowMs)
    .map(entry => ({ ...entry, x: scale.toX(entry.at)!, text: t("drawing.expected", { duration: formatDuration(entry.item.span.expectedSeconds! * 1000, "minutes") }) }));
  const owned = (ownedWaits ?? []).map(wait => ({ wait, start: scale.toX(wait.startMs)!, end: scale.toX(wait.endMs)!, duration: formatDuration(wait.endMs - wait.startMs, "minutes") }))
    .filter(entry => entry.end >= entry.start);
  const ownedLabel = (entry: { wait: OwnedWait; duration: string }) => t("drawing.waiting", { kind: i18n.t("console:waitingForOwnedMicroTasks"), duration: entry.duration });
  const acceptanceLabel = acceptance ? t("drawing.waiting", { kind: t("review"), duration: acceptance.duration }) : "";
  const candidates = [t("drawing.inquiry"), t("review"), ...positioned.flatMap(item => [`${modelLabel(item.span)} · ${fullLabel(item)}`, fullLabel(item), duration(item)!, waitingKind(item.span)]),
    ...owned.map(entry => ownedLabel(entry)),
    ...markerGroups.filter(group => group.length > 1).map(group => String(group.length)),
    ...expected.map(entry => entry.text), ...markers.map(marker => eventName(marker.event)), acceptanceLabel, acceptance?.duration ?? ""];
  const { measure, ruler } = useTextMeasure(candidates);
  useImperativeHandle<Interval | null, Interval | null>(selectionRef, () => {
    const span = positioned.find(item => `span:${item.span.spanId}` === selectedKey);
    let range: Interval;
    if (span) range = spanSelection(span.start, span.end);
    else {
      const group = markerGroups.find(group => group.some(marker => markerKey(marker) === selectedKey));
      if (!group) return null;
      const center = group.reduce((sum, marker) => sum + marker.x, 0) / group.length;
      const radius = group.length > 1
        ? clusterHalfWidth(group.length, measure(String(group.length))) + CLUSTER_SELECTION_PADDING_PX
        : group[0]!.kind === "event" ? EVENT_SELECTION_RADIUS_PX : INQUIRY_SELECTION_RADIUS_PX;
      range = { left: center - radius, right: center + radius };
    }
    return { left: Math.max(-INSET_PX, range.left), right: Math.min(scale.widthPx + INSET_PX, range.right) };
  });
  const lightInk = (span: TimelineSpan) => buddyUsesLightInk(buddyColors.get(buddyColorKey(span.buddy))!);
  const labels: Label[] = [];
  const occupied: Record<number, Occupancy[]> = { [topTextY]: [], [barTextY]: [...markerGroups.map(group => ({ x: group.reduce((sum, mark) => sum + mark.x, 0) / group.length, radius: group.length > 1 ? clusterHalfWidth(group.length, measure(String(group.length))) + 3 : 8 })), ...expected.map(mark => ({ x: mark.x, radius: 8 }))].map(mark => ({ kind: "obstacle" as const, left: mark.x - mark.radius, right: mark.x + mark.radius })) };
  const add = (texts: string[], left: number, right: number, y: number, className: string, preferred = left, blocks: Occupancy[] = []) => {
    const label = fitLabel(texts, { left: Math.max(0, left), right: Math.min(scale.widthPx, right) }, [...occupied[y]!, ...blocks], measure, preferred);
    if (label) { labels.push({ ...label, y, className }); occupied[y]!.push(label); }
  };
  for (const { x } of singletonInquiries) {
    const labelWidth = measure(t("drawing.inquiry"));
    add([t("drawing.inquiry")], x - labelWidth / 2, x + labelWidth / 2, topTextY, "fill-ink2");
  }
  for (const entry of expected) add([entry.text], entry.item.start, entry.item.end, topTextY, "fill-hand", entry.x - measure(entry.text) - 4);
  const eventObstacles: Occupancy[] = [
    ...scale.breaks.map(gap => ({ kind: "obstacle" as const, left: gap.startX, right: gap.endX })),
    ...positioned.map(item => ({ kind: "obstacle" as const, left: item.start, right: item.end })),
    ...owned.map(entry => ({ kind: "obstacle" as const, left: entry.start, right: entry.end })),
    ...(acceptance ? [{ kind: "obstacle" as const, left: acceptance.start, right: acceptance.end }] : []),
  ];
  for (const marker of singletonEvents) {
    if (isConclusion(marker.event)) continue;
    const label = fitAdjacentLabel(eventName(marker.event), { left: marker.x - 8, right: marker.x + 8 }, { left: 0, right: scale.widthPx }, [...occupied[barTextY]!, ...eventObstacles], measure);
    if (label) {
      labels.push({ ...label, y: barTextY, className: "fill-ink2", eventKey: markerKey(marker) });
      occupied[barTextY]!.push(label);
    }
  }
  if (acceptance && acceptanceOpen) add([acceptanceLabel, t("review"), acceptance.duration!], acceptance.start + 10, acceptance.end, topTextY, "fill-hand");
  for (const entry of owned) add([ownedLabel(entry), i18n.t("console:waitingForOwnedMicroTasks"), entry.duration], entry.start + (entry.wait.open ? 10 : 0), entry.end, topTextY, entry.wait.open ? "fill-hand" : "fill-ink3");
  for (const item of positioned) {
    if (item.execution) {
      const tail = tails.find(entry => entry.item === item);
      add([`${modelLabel(item.span)} · ${fullLabel(item)}`, fullLabel(item), duration(item)!], item.start + (item.span.kind === "execution" ? 26 : 6), (tail?.x ?? item.end) - (["failed", "unknown"].includes(item.outcome) ? 14 : 6), barTextY, item.span.kind === "execution" ? (lightInk(item.span) ? "fill-execution-ink-light" : "fill-execution-ink") : "fill-ink");
    } else add([fullLabel(item), duration(item)!], item.start + (item.outcome === "open" && item.span.kind === "host" ? 10 : 0), item.end, topTextY, item.outcome === "open" && item.span.kind === "host" ? "fill-hand" : "fill-ink3");
  }
  return <svg data-slot="timeline-track" viewBox={`${-INSET_PX} 0 ${scale.widthPx + INSET_PX * 2} ${height - 1}`} width={scale.widthPx + INSET_PX * 2} height={height} preserveAspectRatio="none" className="block max-w-none select-none overflow-hidden border-t text-xs" aria-label={t("track", { title: displayTitle(row.titleSource, row.title).fullText })}>
    {ruler}<IdlePattern id={pattern}/>{scale.breaks.map(gap => <rect aria-hidden="true" data-slot="timeline-break" key={gap.startMs} x={gap.startX} width={gap.endX - gap.startX} height={height} fill={`url(#${pattern})`}/>)}
    {selected && <rect aria-hidden="true" pointerEvents="none" data-slot="selected-row" x={-INSET_PX} width={scale.widthPx + INSET_PX * 2} height={height} className="fill-selected-row"/>}
    {ongoing && <line aria-hidden="true" data-slot="timeline-now" x1={scale.toX(nowMs)!} x2={scale.toX(nowMs)!} y2={height} className="stroke-live opacity-60"/>}
    {acceptance && <line data-slot="acceptance-wait" x1={acceptance.start} x2={acceptance.end} y1={middle} y2={middle} strokeWidth={acceptanceOpen ? 2 : 1.5} strokeDasharray={acceptanceOpen ? "5 4" : undefined} className={acceptanceOpen ? "stroke-hand-mark" : "stroke-ink3"}/>}
    {owned.map(entry => <line key={entry.wait.startMs} data-slot="owned-wait" x1={entry.start} x2={entry.end} y1={middle} y2={middle} strokeWidth={entry.wait.open ? 2 : 1.5} strokeDasharray={entry.wait.open ? "5 4" : undefined} className={entry.wait.open ? "stroke-hand-mark" : "stroke-ink3"}/>)}
    {positioned.map(({ span, start, end, execution, outcome }) => {
      const selection = spanSelection(start, end);
      return <Hint key={span.spanId} text={span.kind === "execution" ? identity(span) : spanReadout(span, nowMs, buddies, row)}><g role="button" tabIndex={tabKey === `span:${span.spanId}` ? 0 : -1} data-at={instant(span.startAt)!} data-key={`span:${span.spanId}`} aria-pressed={selectedKey === `span:${span.spanId}`} aria-label={t("segment", { label: spanReadout(span, nowMs, buddies, row) })}
      onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(row.runId, `span:${span.spanId}`); } }}
      onFocus={() => onFocusSpan(span)} onBlur={() => onFocusSpan(null)} onClick={event => { if (event.detail > 1) return; onSelect(row.runId, `span:${span.spanId}`); onOpen(row.runId, `span:${span.spanId}`); }} onPointerEnter={() => onHover(span)} onPointerLeave={() => onHover(null)} className="group/fragment outline-none">
      {isHighlighted(`span:${span.spanId}`) && <rect data-slot="selected-item" x={selection.left} y={execution ? middle - 13 : middle - 3} width={selection.right - selection.left} height={execution ? 26 : 6} fill="none" className="pointer-events-none stroke-focus"/>}
      <path data-slot="span-hit" d={hitPath(start, end)} fill="transparent"/>
      {execution ? <rect data-slot={span.kind === "routing" ? "routing-bar" : "execution-bar"} data-outcome={outcome} x={start} y={middle - 11} width={Math.max(1, end - start)} height={22}
        strokeDasharray={span.kind === "routing" && ["unknown", "cancelled", "abstention"].includes(outcome) ? "3 2" : undefined}
        style={{ fill: span.kind === "execution" ? (outcome === "running" ? runningBuddyColor(buddyColors.get(buddyColorKey(span.buddy))!) : buddyColors.get(buddyColorKey(span.buddy))) : undefined }}
        className={span.kind === "routing" ? cn("fill-card", outcome === "failed" ? "stroke-fail" : outcome === "running" ? "stroke-ink" : "stroke-ink3") : "stroke-line2"}/>
        : <line data-slot={`${span.kind}-wait`} x1={start} x2={end} y1={middle} y2={middle} strokeWidth={outcome === "open" && span.kind === "host" ? 2 : 1.5} strokeDasharray={outcome === "open" && span.kind === "host" ? "5 4" : undefined} className={outcome === "open" && span.kind === "host" ? "stroke-hand-mark" : "stroke-ink3"}/>}
      {span.kind === "execution" && ["failed", "unknown", "cancelled"].includes(outcome) && <IsolatedStatusMark slot="execution-status-outline"
        d={`M${start - 2.5} ${middle - 13.5}h${Math.max(1, end - start) + 5}v27h${-Math.max(1, end - start) - 5}Z`}
        paper={paper} stroke={outcome === "failed" ? "stroke-fail" : outcome === "unknown" ? "stroke-hand-mark" : "stroke-ink3"} dashed={outcome !== "failed"}/>}
      {span.kind === "execution" && <>
        <rect data-slot="execution-buddy-paper" pointerEvents="none" x={start + 2} y={middle - 9} width={18} height={18} className={surface === "card" ? "fill-card" : "fill-background"}/>
        <foreignObject data-slot="execution-buddy" pointerEvents="none" x={start + 4} y={middle - 7} width={14} height={14} className="overflow-visible text-foreground"><div className="[&_img]:size-3.5 [&_[data-slot=harness-logo]]:size-3.5 [&>span]:size-3.5">{span.buddy?.harness && <HarnessLogo harness={span.buddy.harness}/>}</div></foreignObject>
      </>}

      {span.kind === "execution" && outcome === "failed" && <IsolatedStatusMark slot="execution-failed" d={`M${end - 8} ${middle - 3}l6 6m0-6l-6 6`} paper={paper} stroke="stroke-fail" width={1.8}/>}
      {outcome === "unknown" && <IsolatedStatusMark slot="unconfirmed-end" d={`M${end - 4} ${middle - 11}l4 6-4 5 4 5-4 6`} paper={paper} stroke="stroke-hand-mark"/>}
      <rect aria-hidden="true" data-slot="fragment-focus" x={Math.max(-6, start - 5)} y={middle - 17} width={Math.max(12, end - start + 10)} height={34} fill="none" strokeWidth={2} className={cn("pointer-events-none stroke-ink opacity-0", !isHighlighted(`span:${span.spanId}`) && "group-focus/fragment:opacity-100")}/>
    </g></Hint>;
    })}
    {tails.map(tail => <rect key={tail.item.span.spanId} pointerEvents="none" data-slot="silent-tail" x={tail.x} y={middle - 10.25} width={Math.max(0, tail.item.end - tail.x)} height={20.5} className="fill-hand-wash"/>)}
    {positioned.filter(item => item.span.kind === "execution" && item.outcome === "running").map(({ span, start, end }) => <g key={span.spanId} pointerEvents="none">
      <defs><linearGradient id={`${pattern}-breath-${span.spanId}`}><stop stopColor={leadingBuddyColor(buddyColors.get(buddyColorKey(span.buddy))!)} stopOpacity={0}/><stop offset="1" stopColor={leadingBuddyColor(buddyColors.get(buddyColorKey(span.buddy))!)}/></linearGradient></defs>
      <rect x={Math.max(start, end - 16)} y={middle - 10} width={Math.min(16, Math.max(1, end - start))} height={20} fill={runningBuddyColor(buddyColors.get(buddyColorKey(span.buddy))!)}/>
      <rect data-slot="execution-breath" data-span-id={span.spanId} x={Math.max(start, end - 16)} y={middle - 10} width={Math.min(16, Math.max(1, end - start))} height={20} fill={`url(#${pattern}-breath-${span.spanId})`} className="execution-breathe"/>
    </g>)}
    {/* Status strokes sit above the moving edge, so motion cannot erase their paper. */}
    {tails.map(tail => <IsolatedStatusMark key={tail.item.span.spanId} slot="silent-tail-outline" d={`M${tail.x} ${middle - 10.25}h${Math.max(0, tail.item.end - tail.x)}v20.5h${-Math.max(0, tail.item.end - tail.x)}Z`} paper={paper} stroke="stroke-hand-mark" width={1.5}/>)}
    {acceptance && acceptanceOpen && <path pointerEvents="none" data-slot="waiting-diamond" d={diamond(acceptance.start, middle)} className="fill-hand-mark"/>}
    {positioned.filter(item => item.span.kind === "host" && item.outcome === "open").map(item => <path pointerEvents="none" key={item.span.spanId} data-slot="waiting-diamond" d={diamond(item.start, middle)} className="fill-hand-mark"/>)}
    {expected.map(entry => <IsolatedStatusMark slot="expected-mark" key={entry.item.span.spanId} d={`M${entry.x} ${middle - 16}v32`} paper={paper} stroke="stroke-hand-mark"/>)}
    {singletonInquiries.map(marker => <Hint key={markerKey(marker)} text={markerReadout(marker)}><g role="button" tabIndex={tabKey === markerKey(marker) ? 0 : -1} data-at={Date.parse(markerAt(marker))} data-key={markerKey(marker)} aria-pressed={selectedKey === markerKey(marker)} aria-label={markerReadout(marker)}
      onPointerEnter={() => focusMarker(marker, "hover")} onPointerLeave={() => focusMarker(null, "hover")} onFocus={() => focusMarker(marker, "focus")} onBlur={() => focusMarker(null, "focus")}
      onClick={event => { if (event.detail > 1) return; onSelect(row.runId, markerKey(marker)); onOpen(row.runId, markerKey(marker)); }} onKeyDown={key => { if (key.key === "Enter") { key.preventDefault(); onOpen(row.runId, markerKey(marker)); } }} className="group/fragment outline-none">
      <path data-slot="marker-hit" d={hitPath(marker.x - 5.5, marker.x + 5.5)} fill="transparent"/>
      {isHighlighted(markerKey(marker)) && <rect data-slot="selected-item" x={marker.x - INQUIRY_SELECTION_RADIUS_PX} y={middle - 9} width={INQUIRY_SELECTION_RADIUS_PX * 2} height={18} fill="none" className="pointer-events-none stroke-focus"/>}
      <path data-slot="inquiry-mark" d={diamond(marker.x, middle)} className="fill-card stroke-ink2" strokeWidth={1.3}/>
      <rect aria-hidden="true" data-slot="fragment-focus" x={marker.x - 8} y={middle - 10} width={16} height={20} fill="none" strokeWidth={2} className={cn("pointer-events-none stroke-ink opacity-0", !isHighlighted(markerKey(marker)) && "group-focus/fragment:opacity-100")}/>
    </g></Hint>)}
    {singletonEvents.map(({ event, x }) => <Hint key={event.seq} text={eventReadout(event)}><g role="button" tabIndex={tabKey === `event:${event.seq}` ? 0 : -1} data-at={instant(event.at)!} data-key={`event:${event.seq}`} aria-pressed={selectedKey === `event:${event.seq}`} aria-label={eventReadout(event)} onPointerEnter={() => focusMarker({ kind: "event", event, x }, "hover")} onPointerLeave={() => focusMarker(null, "hover")} onFocus={() => focusMarker({ kind: "event", event, x }, "focus")} onBlur={() => focusMarker(null, "focus")}
      onClick={click => { if (click.detail > 1) return; onSelect(row.runId, `event:${event.seq}`); onOpen(row.runId, `event:${event.seq}`); }} onKeyDown={key => { if (key.key === "Enter") { key.preventDefault(); onOpen(row.runId, `event:${event.seq}`); } }} className="group/fragment outline-none">
      <path data-slot="marker-hit" d={hitPath(x - (isConclusion(event) ? 8 : 2.5), x + (isConclusion(event) ? 8 : 2.5))} fill="transparent"/>
      {isHighlighted(`event:${event.seq}`) && <rect data-slot="selected-item" x={x - EVENT_SELECTION_RADIUS_PX} y={middle - 10} width={EVENT_SELECTION_RADIUS_PX * 2} height={20} fill="none" className="pointer-events-none stroke-focus"/>}
      {isConclusion(event) ? <g data-slot={event.kind === "accept" ? "accept-mark" : "failed-conclusion-mark"}>
        <ConclusionMark kind={event.kind === "accept" ? "accepted" : "failed"} x={x} y={middle}/>
      </g> : <circle data-slot="event-mark" cx={x} cy={middle} r={2.5} className="fill-past"/>}
      <rect aria-hidden="true" data-slot="fragment-focus" x={x - 8} y={middle - 10} width={16} height={20} fill="none" strokeWidth={2} className={cn("pointer-events-none stroke-ink opacity-0", !isHighlighted(`event:${event.seq}`) && "group-focus/fragment:opacity-100")}/>
    </g></Hint>)}
    {markerGroups.filter(group => group.length > 1).map(group => {
      const center = group.reduce((sum, marker) => sum + marker.x, 0) / group.length;
      return <MarkerCluster key={markerKey(group[0]!)} markers={group} x={center} y={middle} hitPath={hitPath(center - 9, center + 9)}
        runId={row.runId} taskTitle={displayTitle(row.titleSource, row.title).fullText} tabKey={tabKey} boundary={boundary} onOpen={onOpen} onSelect={onSelect} onFocusMarker={focusMarker} markerName={markerName} markerReadout={markerReadout} selectedKey={selectedKey} highlightedKey={highlightedKey}/>;
    })}
    {labels.map((label, index) => <text aria-hidden="true" pointerEvents="none" data-slot="track-label" data-event-key={label.eventKey} key={index} x={label.left} y={label.y} className={label.className}>{label.text}</text>)}
  </svg>;
}
