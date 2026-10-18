import { useId } from "react";
import i18n from "i18next";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { clockTime, spanOutcome, outcomeLabel, buddyLabel, buddyColorKey, displayTitle, eventDetails, eventName } from "../macro-task-display";
import type { TimelineEvent, TimelineRow, TimelineSpan } from "../macro-task-types";
import type { FriendlyBuddy } from "../macro-task-display";
import type { TimelineScale } from "./scale";
import { parseTimelineInstant as instant } from "./instant";
import { formatClock, formatClockMinutes, formatDuration } from "../i18n/format";
import { acceptanceInterval } from "./acceptance";
import { fitLabel, useTextMeasure } from "./labels";
import type { Interval, Label, Occupancy } from "./labels";
import { MarkerCluster, markerAt, markerKey } from "./MarkerCluster";
import type { TimelineMarker } from "./MarkerCluster";
import type { OwnedWait } from "./lanes";

export function eventReadout(event: TimelineEvent): string {
  return [eventName(event), clockTime(event.at), eventDetails(event)].filter(Boolean).join(" · ");
}

export function markerReadout(marker: TimelineMarker): string {
  return marker.kind === "event" ? eventReadout(marker.event) : i18n.t("tasks:drawing.inquiryReadout", {
    author: marker.inquiry.author, at: clockTime(marker.inquiry.createdAt), state: i18n.t(`taskDetail:inquiry.${marker.inquiry.state}`),
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
    ...(span.inquiries ?? []).map(inquiry => i18n.t("tasks:drawing.inquiryReadout", { author: inquiry.author, at: clockTime(inquiry.createdAt), state: i18n.t(`taskDetail:inquiry.${inquiry.state}`) })),
  ].filter(Boolean).join(" · ");
}

/** The same scale draws the one axis and all rows; waits cross every break. */
export function Axis({ scale, ongoing, nowMs, pointerX = null, pointerBounds }: { scale: TimelineScale; ongoing: boolean; nowMs: number; pointerX?: number | null; pointerBounds?: Interval }) {
  const { t } = useTranslation("tasks");
  const pattern = useId();
  const pointer = pointerX === null || scale.breaks.some(gap => pointerX >= gap.startX && pointerX <= gap.endX) ? null : (scale.pixelsPerMinute > 30 ? formatClock : formatClockMinutes)(scale.toTime(pointerX)!);
  const width = scale.widthPx;
  const nowX = scale.toX(nowMs)!;
  const now = `${t("now")} ${clockTime(nowMs)}`;
  const gapLabels = scale.breaks.map(gap => formatDuration(gap.endMs - gap.startMs, "minutes"));
  const { measure, ruler } = useTextMeasure([now, ...(pointer ? [pointer] : []), ...gapLabels, ...scale.ticks.filter(tick => tick.labeled).map(tick => clockTime(tick.atMs))]);
  const pointerWidth = pointer === null ? 0 : measure(pointer) + 8;
  const pointerLeft = Math.max(pointerBounds?.left ?? 0, Math.min((pointerBounds?.right ?? width) - pointerWidth, pointerX ?? 0));
  const nowLeft = Math.max(0, Math.min(width - measure(now), nowX - measure(now)));
  const occupied: Occupancy[] = [];
  if (ongoing) occupied.push({ kind: "label", left: nowLeft, right: nowLeft + measure(now) });
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
  for (const tick of scale.ticks.filter(item => item.labeled)) {
    const text = clockTime(tick.atMs), x = scale.toX(tick.atMs)!;
    const label = fitLabel([text], { left: 0, right: width }, occupied, measure, x - measure(text) / 2);
    // A clock stays with its tick, even if no room is left for its text.
    if (label && Math.abs((label.left + label.right) / 2 - x) <= measure(text) / 2 + 4) { labels.push({ ...label, y: 16, className: "fill-ink3" }); occupied.push(label); }
  }
  return <svg data-slot="timeline-axis" viewBox={`-8 0 ${width + 16} 36`} width={width + 16} height="36" className="block max-w-none overflow-hidden text-xs" aria-hidden="true">
    {ruler}<IdlePattern id={pattern}/><line x1={0} x2={width} y1={30} y2={30} className="stroke-line2"/>
    {scale.breaks.map((gap, index) => { const center = (gap.startX + gap.endX) / 2; return <g key={gap.startMs}>
      <rect x={gap.startX} width={gap.endX - gap.startX} height={36} fill={`url(#${pattern})`}/>
      <path data-slot="axis-break-mark" d={`M${center - 7} 34l6-8m0 8l6-8`} className="stroke-line2"/>
      {visibleGapLabels.has(gap.startMs) && <text data-slot="gap-label" x={center} y={16} textAnchor="middle" className="fill-ink3">{gapLabels[index]}</text>}
    </g>; })}
    {scale.ticks.map(tick => <line key={tick.atMs} x1={scale.toX(tick.atMs)!} x2={scale.toX(tick.atMs)!} y1={tick.labeled ? 24 : 27} y2={31} className="stroke-line2"/>)}
    {labels.map(label => <text data-slot="axis-label" key={label.left} x={label.left} y={label.y} className={label.className}>{label.text}</text>)}
    {ongoing && measure(now) <= width && <text data-slot="axis-label" x={nowLeft} y={16} className="fill-live">{now}</text>}
    {pointer !== null && Number.isFinite(pointerWidth) && <g data-slot="pointer-clock" transform={`translate(${pointerLeft} 0)`}>
      <rect width={pointerWidth} height={22} className="fill-ink"/>
      <text x={4} y={16} className="fill-card">{pointer}</text>
    </g>}
  </svg>;
}

function IdlePattern({ id }: { id: string }) {
  return <defs><pattern id={id} patternUnits="userSpaceOnUse" width={8} height={8}>
    <rect width={8} height={8} className="fill-sunk"/>
    <path d="M-2 2l4-4M0 8l8-8M6 10l4-4" fill="none" stroke="var(--idle-stripe)"/>
  </pattern></defs>;
}

type PositionedSpan = { span: TimelineSpan; start: number; end: number; execution: boolean; outcome: ReturnType<typeof spanOutcome> };
const diamond = (x: number, y: number) => `M${x} ${y - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`;

export function Track({ row, spans, events, scale, nowMs, ongoing, buddies, onOpen, onSelect, onHover, onFocusSpan, onMarker, buddyColors, selected, selectedKey, ownedWaits, tabKey, boundary, height = 52 }: {
  row: TimelineRow; spans: TimelineSpan[]; events: TimelineEvent[]; scale: TimelineScale; nowMs: number; ongoing: boolean;
  buddies?: readonly FriendlyBuddy[] | null; selected: boolean; tabKey?: string | null; boundary?: HTMLElement | null;
  /** The detail strip highlights one fragment instead of the whole selected row; null is a strip selection whose slice is an overlay. */
  selectedKey?: string | null;
  /** A Lead's waits for its owned micro tasks, drawn like other waits from the recorded facts. */
  ownedWaits?: readonly OwnedWait[];
  /** Row height; 52 keeps the bar, both name lines and their spacing proportionate, a lane header is taller. */
  height?: number;
  onSelect: (runId: string, key?: string) => void;
  onOpen: (runId: string, key: string) => void; onHover: (span: TimelineSpan | null) => void; onFocusSpan: (span: TimelineSpan | null) => void;
  onMarker: (marker: TimelineMarker | null) => void; buddyColors: ReadonlyMap<string, string>;
}) {
  const { t } = useTranslation("tasks");
  const pattern = useId();
  const middle = height / 2;
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
  const focusMarker = (marker: TimelineMarker | null) => { onFocusSpan(null); onMarker(marker); };
  const markerName = (marker: TimelineMarker) => marker.kind === "event" ? eventName(marker.event) : t("drawing.inquiry");
  const duration = (item: PositionedSpan) => formatDuration((instant(item.span.endAt) ?? nowMs) - instant(item.span.startAt)!, "minutes");
  const waitingKind = (span: TimelineSpan) => span.kind === "queue" ? t("kind.queue") : i18n.t(row.rootLeadRunId ? "console:waitingForLead" : "console:waitingForHost");
  const fullLabel = (item: PositionedSpan) => item.execution ? item.outcome === "running" ? t("drawing.running", { duration: duration(item) }) : item.outcome === "failed" ? t("drawing.failed", { duration: duration(item) }) : duration(item)!
    : t(item.span.kind === "queue" ? "drawing.queue" : "drawing.waiting", { kind: waitingKind(item.span), duration: duration(item) });
  const tails = positioned.filter(item => item.outcome === "running" && item.span.reminders?.possiblyStuck)
    .map(item => ({ item, x: scale.toX(instant(item.span.reminders!.lastNativeEventAt ?? item.span.startAt)!)!, duration: formatDuration(nowMs - instant(item.span.reminders!.lastNativeEventAt ?? item.span.startAt)!, "minutes") }));
  const expected = positioned.filter(item => item.outcome === "running" && item.span.expectedSeconds !== null)
    .map(item => ({ item, at: instant(item.span.startAt)! + item.span.expectedSeconds! * 1000 }))
    .filter(entry => entry.at < nowMs)
    .map(entry => ({ ...entry, x: scale.toX(entry.at)!, text: t("drawing.expected", { duration: formatDuration(entry.item.span.expectedSeconds! * 1000, "minutes") }) }));
  const owned = (ownedWaits ?? []).map(wait => ({ wait, start: scale.toX(wait.startMs)!, end: scale.toX(wait.endMs)!, duration: formatDuration(wait.endMs - wait.startMs, "minutes") }))
    .filter(entry => entry.end >= entry.start);
  const ownedLabel = (entry: { wait: OwnedWait; duration: string }) => t("drawing.waiting", { kind: i18n.t("console:waitingForOwnedMicroTasks"), duration: entry.duration });
  const acceptanceLabel = acceptance ? t("drawing.waiting", { kind: t("review"), duration: acceptance.duration }) : "";
  const candidates = [t("drawing.inquiry"), t("review"), ...positioned.flatMap(item => [fullLabel(item), duration(item)!, waitingKind(item.span)]),
    ...owned.map(entry => ownedLabel(entry)), ...tails.flatMap(tail => [t("drawing.silent", { duration: tail.duration }), t("drawing.silence", { duration: tail.duration })]),
    ...expected.map(entry => entry.text), ...markers.map(marker => eventName(marker.event)), acceptanceLabel, acceptance?.duration ?? ""];
  const { measure, ruler } = useTextMeasure(candidates);
  const labels: Label[] = [];
  const occupied: Record<number, Occupancy[]> = { [topTextY]: [], [barTextY]: [...markerGroups.map(group => ({ x: group.reduce((sum, mark) => sum + mark.x, 0) / group.length, radius: group.length > 1 ? 11 : 8 })), ...expected.map(mark => ({ x: mark.x, radius: 8 }))].map(mark => ({ kind: "obstacle" as const, left: mark.x - mark.radius, right: mark.x + mark.radius })) };
  const add = (texts: string[], left: number, right: number, y: number, className: string, preferred = left, blocks: Occupancy[] = []) => {
    const label = fitLabel(texts, { left: Math.max(0, left), right: Math.min(scale.widthPx, right) }, [...occupied[y]!, ...blocks], measure, preferred);
    if (label) { labels.push({ ...label, y, className }); occupied[y]!.push(label); }
  };
  for (const { x } of singletonInquiries) {
    const labelWidth = measure(t("drawing.inquiry"));
    add([t("drawing.inquiry")], x - labelWidth / 2, x + labelWidth / 2, topTextY, "fill-ink2");
  }
  for (const entry of expected) add([entry.text], entry.item.start, entry.item.end, topTextY, "fill-hand", entry.x - measure(entry.text) - 4);
  for (const marker of singletonEvents) {
    // Acceptance is the row's own settling mark; its word stays in the readout
    // and on hover instead of repeating beside every settled row.
    if (marker.event.kind === "accept") continue;
    const text = eventName(marker.event), room = measure(text) + 20;
    if (allMarkers.some(other => other !== marker && Math.abs(other.x - marker.x) < room)) continue;
    const next = Math.min(scale.widthPx, ...allMarkers.filter(other => other.x > marker.x).map(other => other.x - 10));
    add([text], marker.x + 10, next, barTextY, "fill-ink2", marker.x + 10, positioned.filter(item => item.execution).map(item => ({ kind: "obstacle" as const, left: item.start, right: item.end })));
  }
  if (acceptance && acceptanceOpen) add([acceptanceLabel, t("review"), acceptance.duration!], acceptance.start + 10, acceptance.end, topTextY, "fill-hand");
  for (const entry of owned) add([ownedLabel(entry), i18n.t("console:waitingForOwnedMicroTasks"), entry.duration], entry.start + (entry.wait.open ? 10 : 0), entry.end, topTextY, entry.wait.open ? "fill-hand" : "fill-ink3");
  for (const item of positioned) {
    if (item.execution) {
      const tail = tails.find(entry => entry.item === item);
      add([fullLabel(item), duration(item)!], item.start + 6, (tail?.x ?? item.end) - 6, barTextY, item.span.kind === "execution" && ["running", "failed"].includes(item.outcome) ? "fill-card" : "fill-ink");
      if (tail) add([t("drawing.silent", { duration: tail.duration }), t("drawing.silence", { duration: tail.duration })], tail.x + 6, item.end - 6, barTextY, "fill-hand");
    } else add([fullLabel(item), duration(item)!], item.start + (item.outcome === "open" && item.span.kind === "host" ? 10 : 0), item.end, topTextY, item.outcome === "open" && item.span.kind === "host" ? "fill-hand" : "fill-ink3");
  }
  return <svg data-slot="timeline-track" viewBox={`-8 0 ${scale.widthPx + 16} ${height}`} width={scale.widthPx + 16} height={height} preserveAspectRatio="none" className="block max-w-none overflow-hidden border-t text-xs" aria-label={t("track", { title: displayTitle(row.titleSource, row.title).fullText })}>
    {ruler}<IdlePattern id={pattern}/>{scale.breaks.map(gap => <rect aria-hidden="true" data-slot="timeline-break" key={gap.startMs} x={gap.startX} width={gap.endX - gap.startX} height={height} fill={`url(#${pattern})`}/>)}
    {ongoing && <line aria-hidden="true" data-slot="timeline-now" x1={scale.toX(nowMs)!} x2={scale.toX(nowMs)!} y2={height} className="stroke-live opacity-60"/>}
    {acceptance && <g><line data-slot="acceptance-wait" x1={acceptance.start} x2={acceptance.end} y1={middle} y2={middle} strokeWidth={acceptanceOpen ? 2 : 1.5} strokeDasharray={acceptanceOpen ? "5 4" : undefined} className={acceptanceOpen ? "stroke-hand-mark" : "stroke-ink3"}/>{acceptanceOpen && <path data-slot="waiting-diamond" d={diamond(acceptance.start, middle)} className="fill-hand-mark"/>}</g>}
    {owned.map(entry => <line key={entry.wait.startMs} data-slot="owned-wait" x1={entry.start} x2={entry.end} y1={middle} y2={middle} strokeWidth={entry.wait.open ? 2 : 1.5} strokeDasharray={entry.wait.open ? "5 4" : undefined} className={entry.wait.open ? "stroke-hand-mark" : "stroke-ink3"}/>)}
    {positioned.map(({ span, start, end, execution, outcome }) => <g key={span.spanId} role="button" tabIndex={tabKey === `span:${span.spanId}` ? 0 : -1} data-at={instant(span.startAt)!} data-key={`span:${span.spanId}`} aria-pressed={selectedKey !== undefined ? selectedKey === `span:${span.spanId}` : undefined} aria-label={t("segment", { label: spanReadout(span, nowMs, buddies, row) })}
      onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(row.runId, `span:${span.spanId}`); } }}
      onFocus={() => onFocusSpan(span)} onBlur={() => onFocusSpan(null)} onClick={() => onSelect(row.runId, `span:${span.spanId}`)} onDoubleClick={() => onOpen(row.runId, `span:${span.spanId}`)} onPointerEnter={() => onHover(span)} onPointerLeave={() => onHover(null)} className="group/fragment cursor-pointer outline-none">
      {(selectedKey !== undefined ? selectedKey === `span:${span.spanId}` : selected) && <rect data-slot="selected-span" x={Math.max(-6, start - 2)} y={execution ? middle - 13 : middle - 3} width={Math.max(5, end - start + 4)} height={execution ? 26 : 6} fill="none" className="stroke-live"/>}
      <rect x={start - 3} y={middle - 14} width={Math.max(6, end - start + 6)} height={28} fill="transparent"/>
      {execution ? <rect data-slot={span.kind === "routing" ? "routing-bar" : "execution-bar"} data-outcome={outcome} x={start} y={middle - 11} width={Math.max(1, end - start)} height={22}
        strokeDasharray={["unknown", "cancelled", "abstention"].includes(outcome) ? "3 2" : undefined}
        style={{ fill: span.kind === "execution" && !["running", "failed", "unknown"].includes(outcome) ? buddyColors.get(buddyColorKey(span.buddy)) : undefined }}
        className={span.kind === "routing" ? cn("fill-card", outcome === "failed" ? "stroke-fail" : outcome === "running" ? "stroke-live" : "stroke-ink3") : cn(outcome === "running" ? "fill-live stroke-live" : outcome === "failed" ? "fill-fail stroke-fail" : outcome === "unknown" ? "fill-hand-wash stroke-hand-mark" : "fill-past-wash stroke-line2")}/>
        : <line data-slot={`${span.kind}-wait`} x1={start} x2={end} y1={middle} y2={middle} strokeWidth={outcome === "open" && span.kind === "host" ? 2 : 1.5} strokeDasharray={outcome === "open" && span.kind === "host" ? "5 4" : undefined} className={outcome === "open" && span.kind === "host" ? "stroke-hand-mark" : "stroke-ink3"}/>}
      {span.kind === "host" && outcome === "open" && <path data-slot="waiting-diamond" d={diamond(start, middle)} className="fill-hand-mark"/>}
      {outcome === "unknown" && <path data-slot="unconfirmed-end" d={`M${end - 4} ${middle - 11}l4 6-4 5 4 5-4 6`} className="fill-none stroke-hand-mark" strokeWidth={2}/>}
      <rect aria-hidden="true" data-slot="fragment-focus" x={Math.max(-6, start - 5)} y={middle - 17} width={Math.max(12, end - start + 10)} height={34} fill="none" strokeWidth={2} className="pointer-events-none stroke-ink opacity-0 group-focus/fragment:opacity-100"/>
    </g>)}
    {tails.map(tail => <rect pointerEvents="none" data-slot="silent-tail" key={tail.item.span.spanId} x={tail.x} y={middle - 10.25} width={Math.max(0, tail.item.end - tail.x)} height={20.5} strokeWidth={1.5} className="fill-hand-wash stroke-hand-mark"/>)}
    {expected.map(entry => <line pointerEvents="none" data-slot="expected-mark" key={entry.item.span.spanId} x1={entry.x} x2={entry.x} y1={middle - 16} y2={middle + 16} strokeWidth={2} className="stroke-hand-mark"/>)}
    {singletonInquiries.map(marker => <g key={markerKey(marker)} role="button" tabIndex={tabKey === markerKey(marker) ? 0 : -1} data-at={Date.parse(markerAt(marker))} data-key={markerKey(marker)} aria-pressed={selectedKey !== undefined ? selectedKey === markerKey(marker) : undefined} aria-label={markerReadout(marker)}
      onPointerEnter={() => focusMarker(marker)} onPointerLeave={() => focusMarker(null)} onFocus={() => focusMarker(marker)} onBlur={() => focusMarker(null)}
      onClick={() => onSelect(row.runId, markerKey(marker))} onDoubleClick={() => onOpen(row.runId, markerKey(marker))} onKeyDown={key => { if (key.key === "Enter") { key.preventDefault(); onOpen(row.runId, markerKey(marker)); } }} className="group/fragment cursor-pointer outline-none">
      {selectedKey === markerKey(marker) && <rect data-slot="selected-span" x={marker.x - 9} y={middle - 9} width={18} height={18} fill="none" className="stroke-live"/>}
      <path data-slot="inquiry-mark" d={diamond(marker.x, middle)} className="fill-card stroke-ink2" strokeWidth={1.3}/>
      <rect aria-hidden="true" data-slot="fragment-focus" x={marker.x - 8} y={middle - 10} width={16} height={20} fill="none" strokeWidth={2} className="pointer-events-none stroke-ink opacity-0 group-focus/fragment:opacity-100"/>
    </g>)}
    {singletonEvents.map(({ event, x }) => <g key={event.seq} role="button" tabIndex={tabKey === `event:${event.seq}` ? 0 : -1} data-at={instant(event.at)!} data-key={`event:${event.seq}`} aria-pressed={selectedKey !== undefined ? selectedKey === `event:${event.seq}` : undefined} aria-label={eventReadout(event)} onPointerEnter={() => focusMarker({ kind: "event", event, x })} onPointerLeave={() => focusMarker(null)} onFocus={() => focusMarker({ kind: "event", event, x })} onBlur={() => focusMarker(null)}
      onClick={() => onSelect(row.runId, `event:${event.seq}`)} onDoubleClick={() => onOpen(row.runId, `event:${event.seq}`)} onKeyDown={key => { if (key.key === "Enter") { key.preventDefault(); onOpen(row.runId, `event:${event.seq}`); } }} className="group/fragment cursor-pointer outline-none">
      {selectedKey === `event:${event.seq}` && <rect data-slot="selected-span" x={x - 10} y={middle - 10} width={20} height={20} fill="none" className="stroke-live"/>}
      {event.kind === "accept" || event.kind === "conclude" && (row.state === "failed" || row.status === "failed" || row.acceptanceVerdict === "failed") ? <g data-slot={event.kind === "accept" ? "accept-mark" : "failed-conclusion-mark"}>
        <circle cx={x} cy={middle} r={8} className="fill-card stroke-past"/>
        <path d={event.kind === "accept" ? `M${x - 4} ${middle}l3 3 5-6` : `M${x - 3} ${middle - 3}l6 6m0-6l-6 6`} fill="none" className={event.kind === "accept" ? "stroke-past" : "stroke-fail"} strokeWidth={1.8}/>
      </g> : <circle data-slot="event-mark" cx={x} cy={middle} r={2.5} className="fill-past"/>}
      <rect aria-hidden="true" data-slot="fragment-focus" x={x - 8} y={middle - 10} width={16} height={20} fill="none" strokeWidth={2} className="pointer-events-none stroke-ink opacity-0 group-focus/fragment:opacity-100"/>
    </g>)}
    {markerGroups.filter(group => group.length > 1).map(group => <MarkerCluster key={markerKey(group[0]!)} markers={group} x={group.reduce((sum, marker) => sum + marker.x, 0) / group.length} y={middle}
      runId={row.runId} taskTitle={displayTitle(row.titleSource, row.title).fullText} tabKey={tabKey} boundary={boundary} onOpen={onOpen} onFocusMarker={focusMarker} markerName={markerName} markerReadout={markerReadout} selectedKey={selectedKey}/>)}
    {labels.map((label, index) => <text aria-hidden="true" pointerEvents="none" data-slot="track-label" key={index} x={label.left} y={label.y} className={label.className}>{label.text}</text>)}
  </svg>;
}
