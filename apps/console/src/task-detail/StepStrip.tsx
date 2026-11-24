import { AliasTag, useNamedBuddy, accessibleBuddyName } from "../components/AliasTag";
import { useContext, useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ReactNode, Ref } from "react";
import { ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import type { MacroTaskTimeline, TimelineRow } from "../macro-task-types";
import { buddyColorsFor } from "../timeline/buddyColors";
import { NowLine } from "../timeline/NowLine";
import { Axis, Track, TIMELINE_AXIS_PX, TIMELINE_TRACK_PX as STRIP_PX } from "../timeline/Tracks";
import { fragmentHitPath, WaitingDiamond, TIMELINE_OUTSET_PX as SLACK } from "../timeline/trackMarks";
import { timelineScaleInput } from "../timeline/scale";
import { useTimelineZoom } from "../timeline/useTimelineZoom";
import { useTimelineGestures } from "../timeline/useTimelineGestures";
import { ZoomRuler } from "../timeline/ZoomRuler";
import { acceptanceInterval } from "../timeline/acceptance";
import { parseTimelineInstant } from "../timeline/instant";
import { useViewport } from "../timeline/useViewport";
import { SETTLING_EVENTS, stepForItem } from "./steps";
import type { Step, Steps } from "./steps";
import { modelEffort } from "./names";
import type { Interval } from "../timeline/labels";
import { DetailLinks } from "./linking";
import { TimelineThumbnail } from "./TimelineThumbnail";
import { buddyColorKey, eventName } from "../macro-task-display";
import { isFailedRow } from "../timeline/failure";
import { formatClock, formatDuration } from "../i18n/format";

const STEP_SELECTION_RADIUS_PX = 8;

/** Where a step's slice sits, from recorded facts only; a step with no instant of its own anchors on the wait that leads to it. */
function anchorOf(step: Step | null, spans: readonly { spanId: string; startAt: string | null }[], acceptanceStart: number | null): number | null {
  if (!step) return null;
  if (step.at !== null && Number.isFinite(step.at)) return step.at;
  for (const wait of step.waits) {
    if (wait.spanId === null) continue;
    const span = spans.find(item => item.spanId === wait.spanId);
    const at = span ? parseTimelineInstant(span.startAt) : null;
    if (at !== null) return at;
  }
  return step.kind === "acceptance" && step.pending ? acceptanceStart : null;
}

/**
 * The steps of one micro task as a permanent strip along the top of its
 * detail: it is the micro task's own row of the macro task, drawn by the same
 * Axis and Track from the same recorded spans, events and inquiries. Clicking
 * a slice switches the step shown below; one slice is selected at a time, and
 * every retained step has a slice of its own — the delegation is the dot at
 * the row's start, a change of buddy is the diamond at the boundary it
 * happened on, the open acceptance is the outlined diamond at the wait's start, and a
 * settled acceptance is its settling mark. Inquiries, waits (including a
 * finished acceptance wait) and events are separate targets within a step.
 * The strip is centred on the step
 * the detail opened with exactly once — a step without an instant anchors on
 * the wait that leads to it, and if nothing is recorded the position is
 * decided then and there. Selecting another step reveals its drawn slice or
 * marker with the smallest horizontal scroll; later reads leave the view alone.
 */
export function StepStrip({ row, timeline, steps, selected, onStep, nowMs, revealRef }: {
  row: TimelineRow; timeline: MacroTaskTimeline; steps: Steps; selected: number;
  /** The address's step, counted from 1, exactly as the page's own onStep. */
  onStep: (step: number) => void; nowMs: number;
  revealRef: Ref<() => void>;
}) {
  const { t } = useTranslation("taskDetail");
  const named = useNamedBuddy();
  const [expanded, setExpanded] = useState(false);
  const timelineId = useId();
  const [timelineToggle, setTimelineToggle] = useState<HTMLButtonElement | null>(null);
  const thumbnail = useViewport<HTMLSpanElement>(300);
  const links = useContext(DetailLinks)!;
  const focusedKey = links.origin && stepForItem(steps, links.origin, timeline) === selected ? links.origin : null;
  const { viewport, setViewport, width } = useViewport(640);
  const [gestureArea, setGestureArea] = useState<HTMLDivElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  viewportRef.current = viewport;
  const [ruler, setRuler] = useState<HTMLDivElement | null>(null);
  const centered = useRef(false);
  const previousSelected = useRef({ selected, focusedKey: null as string | null });
  const trackSelection = useRef<Interval | null>(null);
  const spans = useMemo(() => timeline.spans.filter(span => span.runId === row.runId), [timeline, row.runId]);
  const events = useMemo(() => timeline.events.filter(event => event.runId === row.runId), [timeline, row.runId]);
  const wait = acceptanceInterval(row, spans, nowMs);
  const step = steps.steps[selected] ?? null;
  // A settled acceptance selects its settling mark; a pending one and a change of buddy are the overlays below.
  const stepKey = step?.kind === "acceptance" && !step.pending
    ? (mark => mark ? `event:${mark.seq}` : null)(step.events.find(event => SETTLING_EVENTS.includes(event.kind)))
    : step?.kind === "handoff" || step?.kind === "acceptance" ? null : step?.key ?? null;
  const selectedKey = focusedKey?.startsWith("settle:") ? null : focusedKey ?? stepKey;
  const thumbnailSelection = focusedKey ?? (step?.kind === "acceptance" && step.pending ? `settle:${row.runId}` : selectedKey ?? step?.key ?? null);
  // The opening anchor is decided from the first presented state and never revisited.
  const initialAnchor = useRef<number | null | undefined>(undefined);
  if (initialAnchor.current === undefined) initialAnchor.current = anchorOf(steps.steps[selected] ?? null, spans, wait?.startMs ?? null);
  // Fit covers this micro task's own whole record, whichever step is selected.
  const scoped = useMemo(() => ({ ...timeline, rows: [row], spans, events }), [timeline, row, spans, events]);
  const input = useMemo(() => timelineScaleInput(scoped), [scoped]);
  const buddyColors = useMemo(() => buddyColorsFor(timeline.spans), [timeline.spans]);
  const { scale, change: zoom, zoomAt, atMinimum } = useTimelineZoom(input, Math.max(1, width - SLACK * 2), row.runId, viewportRef,
    steps.steps[selected]?.at ?? null, nowMs, SLACK);
  useTimelineGestures(gestureArea, ruler, zoomAt, () => zoom(0), true, {
    element: expanded ? null : timelineToggle, reveal: () => setExpanded(true),
  });
  useEffect(() => {
    if (centered.current || !scale || !viewport) return;
    centered.current = true;
    const at = initialAnchor.current ?? null;
    if (at !== null) viewport.scrollLeft = Math.max(0, scale.toX(at)! + SLACK - viewport.clientWidth / 2);
  }, [scale, viewport]);
  const reveal = () => {
    if (!scale || !viewport) return;
    let range = trackSelection.current;
    const acceptanceWait = step?.kind === "acceptance" && (step.pending || focusedKey?.startsWith("settle:"));
    if (range === null && (step?.kind === "delegation" || step?.kind === "handoff" || acceptanceWait)) {
      const at = acceptanceWait ? wait?.startMs ?? null : anchorOf(step, spans, wait?.startMs ?? null);
      if (at === null) return;
      const x = scale.toX(at)!;
      range = { left: x - STEP_SELECTION_RADIUS_PX, right: x + STEP_SELECTION_RADIUS_PX };
    }
    if (range === null) return;
    const slice = { left: range.left + SLACK, right: range.right + SLACK };
    const view = viewport;
    const left = view.scrollLeft;
    const right = left + view.clientWidth;
    const delta = slice.right - slice.left > view.clientWidth || slice.left < left ? slice.left - left
      : slice.right > right ? slice.right - right : 0;
    if (delta !== 0) view.scrollTo({ left: view.scrollLeft + delta,
      behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };
  useImperativeHandle(revealRef, () => reveal);
  useEffect(() => {
    if (!scale || !viewport) return;
    if (previousSelected.current.selected === selected && previousSelected.current.focusedKey === focusedKey) return;
    previousSelected.current = { selected, focusedKey };
    reveal();
  }, [selected, focusedKey, viewport, scale]);
  const overlay = (children: (x: (at: number) => number) => ReactNode) => scale ? <svg aria-hidden={false} viewBox={`0 0 ${scale.widthPx + SLACK * 2} ${STRIP_PX}`} width={scale.widthPx + SLACK * 2} height={STRIP_PX}
    className="pointer-events-none absolute inset-0 left-0 top-0 block max-w-none overflow-visible">
    {children(at => scale.toX(at)! + SLACK)}
  </svg> : null;
  const delegationAt = steps.steps[0]?.at ?? null;
  const acceptanceIndex = steps.steps.findIndex(item => item.kind === "acceptance");
  const sliceButton = (key: string, index: number, label: string, at: number, mark: ReactNode, itemKey?: string) => {
    const pressed = selected === index && (itemKey
      ? focusedKey === itemKey || step?.pending && focusedKey === null
      : focusedKey === null || focusedKey === key);
    return <g key={key} role="button" tabIndex={itemKey || selected === index ? 0 : -1} data-key={key} aria-pressed={pressed} aria-label={label}
      onClick={() => itemKey ? links.onItem(itemKey) : onStep(index + 1)}
      onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); if (itemKey) links.onItem(itemKey); else onStep(index + 1); } }}
      className={pressed ? "pointer-events-auto outline-none" : "pointer-events-auto outline-none focus-visible:[&_.hit]:stroke-ring focus-visible:[&_.hit]:stroke-2"}>
      <path className="hit" fill="transparent" d={fragmentHitPath(at - 6, at + 6, STRIP_PX / 2, 0, scale!.widthPx + SLACK * 2)}/>
      {pressed && <rect data-slot="selected-item" x={at - STEP_SELECTION_RADIUS_PX} y={STRIP_PX / 2 - STEP_SELECTION_RADIUS_PX} width={STEP_SELECTION_RADIUS_PX * 2} height={STEP_SELECTION_RADIUS_PX * 2} fill="none" className="pointer-events-none stroke-focus"/>}
      {itemKey && <rect data-slot="fragment-focus" x={at - STEP_SELECTION_RADIUS_PX} y={STRIP_PX / 2 - STEP_SELECTION_RADIUS_PX} width={STEP_SELECTION_RADIUS_PX * 2} height={STEP_SELECTION_RADIUS_PX * 2} fill="none" className="pointer-events-none stroke-focus opacity-0"/>}
      {mark}
    </g>;
  };
  const middle = STRIP_PX / 2;
  const buddy = step?.span?.routing?.routerBuddy ?? step?.span?.buddy ?? step?.to ?? null;
  const color = buddyColors.get(buddyColorKey(buddy));
  const title = !step ? null : step.kind === "turn" ? t("step.turn", { n: step.turnIndex })
    : step.kind === "cancel" ? step.events[0] && eventName(step.events[0])
      : t(`step.${step.kind}`);
  const end = parseTimelineInstant(step?.span?.endAt ?? null) ?? (step?.outcome === "running" ? nowMs : null);
  return <div data-slot="detail-strip" role="group" aria-label={t("strip.label")} onKeyDown={event => {
    if (!expanded || event.defaultPrevented || (event.target as Element).closest("input, textarea, select, [role=dialog]")) return;
    if (event.key === "+" || event.key === "=") { event.preventDefault(); zoom(1); }
    else if (event.key === "-") { event.preventDefault(); zoom(-1); }
    else if (event.key === "0") { event.preventDefault(); zoom(0); }
  }} className="select-none">
    <div data-slot="step-bar" className="flex h-[38px] min-w-0 items-stretch pl-1.5 text-xs">
      <div data-slot="step-navigation" className="flex shrink-0 items-center border-r border-line pr-2">
        <Button type="button" variant="ghost" size="icon-sm" aria-label={t("strip.previous")} aria-disabled={selected === 0 || undefined} onClick={() => { if (selected > 0) onStep(selected); }}>
          <ChevronLeft aria-hidden="true"/>
        </Button>
        <span data-slot="step-counter" className="min-w-[9.5ch] text-center font-medium whitespace-nowrap tabular-nums text-ink">{t("strip.position", { current: selected + 1, total: steps.steps.length })}</span>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={t("strip.next")} aria-disabled={selected === steps.steps.length - 1 || undefined} onClick={() => { if (selected < steps.steps.length - 1) onStep(selected + 2); }}>
          <ChevronRight aria-hidden="true"/>
        </Button>
      </div>
      <button ref={setTimelineToggle} type="button" data-slot="timeline-toggle" aria-expanded={expanded} aria-controls={timelineId}
        aria-label={expanded ? t("strip.hide") : input.domain ? t("strip.show") : `${t("strip.show")} · ${t("strip.empty")}`}
        onClick={() => setExpanded(open => !open)}
        className="group flex h-full w-[clamp(200px,32cqw,420px)] max-w-[calc(100cqw-13rem)] @min-[700px]:max-w-[calc(100cqw-25rem)] shrink-0 cursor-pointer items-center gap-2 overflow-hidden border-r border-line bg-sunk px-2.5 text-ink2 outline-none transition-colors duration-150 motion-reduce:transition-none hover:bg-selection hover:text-ink aria-expanded:bg-selection aria-expanded:text-ink aria-expanded:hover:bg-selection/80 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
        <span ref={thumbnail.setViewport} className="min-w-0 flex-1 truncate">
          {expanded ? t("strip.hide")
            : <TimelineThumbnail input={input} width={thumbnail.width} spans={spans} events={events} steps={steps.steps} runId={row.runId}
                selectedKey={thumbnailSelection} buddyColors={buddyColors} wait={wait} failed={isFailedRow(row)} nowMs={nowMs}/>}
        </span>
        <ChevronDown className="size-3 shrink-0 text-ink3 transition-[color,rotate] duration-150 motion-reduce:transition-none group-hover:text-ink group-aria-expanded:rotate-180" aria-hidden="true"/>
      </button>
      <div data-slot="step-readout" className="flex min-w-0 flex-1 items-center gap-2.5 px-4 @max-[640px]:flex-wrap @max-[640px]:gap-y-0 @max-[440px]:pr-2">
        <span className="shrink-0 font-semibold text-ink">{title}</span>
        {buddy && <span className="flex min-w-0 items-center font-mono text-xs text-ink2 @max-[520px]:hidden">
          {color && <span aria-hidden="true" className="mr-1.5 size-2.5 shrink-0 rounded-[2px]" style={{ backgroundColor: color }}/>}
          <span className="truncate">{modelEffort(buddy)}</span><AliasTag alias={named(buddy)?.alias}/>
        </span>}
        {step?.at != null && <span data-slot="step-clock" className="ml-auto flex shrink-0 items-center gap-3 whitespace-nowrap tabular-nums text-ink3 @max-[520px]:hidden">
          <span>{formatClock(step.at)}{end !== null && ` – ${formatClock(end)}`}</span>
          {step.ms !== null && <span className="text-ink">{formatDuration(step.ms, "fine")}</span>}
        </span>}
      </div>
    </div>
    <div id={timelineId} inert={!expanded}
      className="grid grid-rows-[0fr] overflow-hidden motion-safe:transition-[grid-template-rows] motion-safe:duration-200 data-[expanded=true]:grid-rows-[1fr]"
      data-expanded={expanded}>
      <div className="min-h-0 overflow-hidden">
        <div ref={setGestureArea} data-slot="detail-timeline" className="relative flex border-t border-line bg-background" style={{ height: TIMELINE_AXIS_PX + STRIP_PX + 1 }}>
            <ScrollArea className="min-w-0 flex-1" orientation="both" viewportProps={{ ref: setViewport, "data-slot": "detail-strip-scroll" }}>
              {scale && input && <div className="relative" style={{ width: scale.widthPx + SLACK * 2 }}>
                <ZoomRuler ref={setRuler} className="bg-sunk" atMinimum={atMinimum} onChange={zoom} viewportWidth={width}>
                  <Axis compact scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs}/>
                </ZoomRuler>
                <div className="relative">
                  <Track row={row} spans={spans} events={events} scale={scale} nowMs={nowMs}
                    selected={false} selectedKey={selectedKey} tabKey={selectedKey} buddyColors={buddyColors} height={STRIP_PX} selectionRef={trackSelection} itemLinks={links.items}
                    onOpen={(_runId, key) => links.onItem(key)} onSelect={() => {}}
                    onHover={() => {}} onFocusSpan={() => {}} onMarker={() => {}}/>
                  {overlay(x => <>
                    {delegationAt !== null && sliceButton("delegation", 0, t("step.delegation"), x(delegationAt),
                      <circle cx={x(delegationAt)} cy={middle} r={4} className="fill-past"/>)}
                    {steps.steps.map((item, index) => item.kind === "handoff" && item.at !== null && Number.isFinite(item.at)
                      ? sliceButton(item.key, index, [t("step.handoff"), accessibleBuddyName(modelEffort(item.from), named(item.from)) || t("unknown"), accessibleBuddyName(modelEffort(item.to), named(item.to)) || t("unknown")].join(" · "), x(item.at!),
                        <path d={`M${x(item.at!)} ${middle - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`} className="fill-ink2"/>)
                      : null)}
                    {acceptanceIndex >= 0 && wait && sliceButton(`settle:${row.runId}`, acceptanceIndex, t("wait.acceptance"), x(wait.startMs),
                      <WaitingDiamond x={x(wait.startMs)} y={middle} kind="review"/>,
                      `settle:${row.runId}`)}
                  </>)}
                  {input.nowAtEnd && <NowLine x={SLACK + scale.toX(nowMs)!}/>}
                </div>
              </div>}
            </ScrollArea>
        </div>
      </div>
    </div>
  </div>;
}
