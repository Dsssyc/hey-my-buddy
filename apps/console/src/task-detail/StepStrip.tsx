import { Hint } from "../components/hint";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import type { MacroTaskTimeline, TimelineRow } from "../macro-task-types";
import { buddyColorsFor } from "../timeline/buddyColors";
import { Axis, Track } from "../timeline/Tracks";
import { CONCLUSION_OUTSET_PX as SLACK } from "../timeline/ConclusionMark";
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

const STRIP_PX = 44;

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
 * happened on, the open acceptance is the diamond at the wait's start, and a
 * settled acceptance is its settling mark. The strip is centred on the step
 * the detail opened with exactly once — a step without an instant anchors on
 * the wait that leads to it, and if nothing is recorded the position is
 * decided then and there; selecting another step or a later read never moves
 * the scroll again.
 */
export function StepStrip({ row, timeline, steps, selected, onStep, nowMs }: {
  row: TimelineRow; timeline: MacroTaskTimeline; steps: Steps; selected: number;
  /** The address's step, counted from 1, exactly as the page's own onStep. */
  onStep: (step: number) => void; nowMs: number;
}) {
  const { t } = useTranslation("taskDetail");
  const { viewport, setViewport, width } = useViewport(640);
  const [gestureArea, setGestureArea] = useState<HTMLDivElement | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  viewportRef.current = viewport;
  const [ruler, setRuler] = useState<HTMLDivElement | null>(null);
  const centered = useRef(false);
  const spans = useMemo(() => timeline.spans.filter(span => span.runId === row.runId), [timeline, row.runId]);
  const events = useMemo(() => timeline.events.filter(event => event.runId === row.runId), [timeline, row.runId]);
  const wait = acceptanceInterval(row, spans, nowMs);
  // The opening anchor is decided from the first presented state and never revisited.
  const initialAnchor = useRef<number | null | undefined>(undefined);
  if (initialAnchor.current === undefined) initialAnchor.current = anchorOf(steps.steps[selected] ?? null, spans, wait?.startMs ?? null);
  // Fit covers this micro task's own whole record, whichever step is selected.
  const scoped = useMemo(() => ({ ...timeline, rows: [row], spans, events }), [timeline, row, spans, events]);
  const input = useMemo(() => timelineScaleInput(scoped), [scoped]);
  const buddyColors = useMemo(() => buddyColorsFor(timeline.spans), [timeline.spans]);
  const { scale, change: zoom, zoomAt, atMinimum } = useTimelineZoom(input, Math.max(1, width - SLACK * 2), row.runId, viewportRef,
    steps.steps[selected]?.at ?? null, nowMs, SLACK);
  useTimelineGestures(gestureArea, ruler, zoomAt, () => zoom(0), true);
  useEffect(() => {
    if (centered.current || !scale || !viewport) return;
    centered.current = true;
    const at = initialAnchor.current ?? null;
    if (at !== null) viewport.scrollLeft = Math.max(0, scale.toX(at)! + SLACK - viewport.clientWidth / 2);
  }, [scale, viewport]);
  const step = steps.steps[selected] ?? null;
  // A settled acceptance selects its settling mark; a pending one and a change of buddy are the overlays below.
  const selectedKey = step?.kind === "acceptance" && !step.pending
    ? (mark => mark ? `event:${mark.seq}` : null)(step.events.find(event => SETTLING_EVENTS.includes(event.kind)))
    : step?.kind === "handoff" || step?.kind === "acceptance" ? null : step?.key ?? null;
  const selectKey = (key: string) => {
    const index = stepForItem(steps, key, timeline);
    if (index !== null && index >= 0) onStep(index + 1);
  };
  const overlay = (children: (x: (at: number) => number) => ReactNode) => scale ? <svg aria-hidden={false} viewBox={`0 0 ${scale.widthPx + SLACK * 2} ${STRIP_PX}`} width={scale.widthPx + SLACK * 2} height={STRIP_PX}
    className="pointer-events-none absolute inset-0 left-0 top-0 block max-w-none overflow-visible">
    {children(at => scale.toX(at)! + SLACK)}
  </svg> : null;
  const delegationAt = steps.steps[0]?.at ?? null;
  const acceptanceIndex = steps.steps.findIndex(item => item.kind === "acceptance" && item.pending);
  const sliceButton = (key: string, index: number, label: string, at: number, mark: ReactNode, hit: { x: number; y: number; w: number; h: number }) => <Hint key={key} text={label}><g role="button" tabIndex={selected === index ? 0 : -1} data-key={key} aria-pressed={selected === index} aria-label={label}
    onClick={() => onStep(index + 1)}
    onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onStep(index + 1); } }}
    className={selected === index ? "pointer-events-auto outline-none" : "pointer-events-auto outline-none focus-visible:[&_.hit]:stroke-ring focus-visible:[&_.hit]:stroke-2"}>
    <rect className="hit" fill="transparent" x={Math.max(0, Math.min(scale!.widthPx + SLACK * 2 - hit.w, hit.x))} y={hit.y} width={hit.w} height={hit.h}/>
    {selected === index && <rect data-slot="selected-item" x={at - 8} y={STRIP_PX / 2 - 8} width={16} height={16} fill="none" className="pointer-events-none stroke-focus"/>}
    {mark}
  </g></Hint>;
  const middle = STRIP_PX / 2;
  return <div ref={setGestureArea} data-slot="detail-strip" role="group" aria-label={t("strip.label")} onKeyDown={event => {
    if (event.defaultPrevented || (event.target as Element).closest("input, textarea, select, [role=dialog]")) return;
    if (event.key === "+" || event.key === "=") { event.preventDefault(); zoom(1); }
    else if (event.key === "-") { event.preventDefault(); zoom(-1); }
    else if (event.key === "0") { event.preventDefault(); zoom(0); }
  }} className="relative flex flex-none select-none">
    <div data-slot="step-navigation" className="flex shrink-0 flex-col items-center justify-between border-r border-line px-3 py-2">
      <span data-slot="step-counter" className="whitespace-nowrap text-xs font-medium tabular-nums text-ink">{t("strip.position", { current: selected + 1, total: steps.steps.length })}</span>
      <div className="flex gap-1.5">
        <Hint text={t("strip.previous")}><Button type="button" variant="ghost" size="icon-sm" aria-label={t("strip.previous")} aria-disabled={selected === 0 || undefined} onClick={() => { if (selected > 0) onStep(selected); }}>
          <ChevronLeft aria-hidden="true"/>
        </Button></Hint>
        <Hint text={t("strip.next")}><Button type="button" variant="ghost" size="icon-sm" aria-label={t("strip.next")} aria-disabled={selected === steps.steps.length - 1 || undefined} onClick={() => { if (selected < steps.steps.length - 1) onStep(selected + 2); }}>
          <ChevronRight aria-hidden="true"/>
        </Button></Hint>
      </div>
    </div>
    <ScrollArea className="min-w-0 flex-1" orientation="both" viewportProps={{ ref: setViewport, "data-slot": "detail-strip-scroll" }}>
      {scale && input && <div className="relative" style={{ width: scale.widthPx + SLACK * 2 }}>
        <ZoomRuler ref={setRuler} className="bg-transparent" atMinimum={atMinimum} onChange={zoom} viewportWidth={width}>
          <Axis compact scale={scale} ongoing={input.nowAtEnd} nowMs={nowMs}/>
        </ZoomRuler>
        <div className="relative">
          <Track row={row} spans={spans} events={events} scale={scale} nowMs={nowMs} ongoing={input.nowAtEnd}
            selected={false} selectedKey={selectedKey} tabKey={selectedKey} buddyColors={buddyColors} height={STRIP_PX}
            onOpen={(_runId, key) => selectKey(key)} onSelect={(_runId, key) => selectKey(key ?? "")}
            onHover={() => {}} onFocusSpan={() => {}} onMarker={() => {}}/>
          {overlay(x => <>
            {delegationAt !== null && sliceButton("delegation", 0, t("step.delegation"), x(delegationAt),
              <circle cx={x(delegationAt)} cy={middle} r={4} className="fill-past"/>,
              { x: x(delegationAt) - 14, y: middle - 14, w: 28, h: 28 })}
            {steps.steps.map((item, index) => item.kind === "handoff" && item.at !== null && Number.isFinite(item.at)
              ? sliceButton(item.key, index, [t("step.handoff"), modelEffort(item.from) || t("unknown"), modelEffort(item.to) || t("unknown")].join(" · "), x(item.at!),
                <path d={`M${x(item.at!)} ${middle - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`} className="fill-ink2"/>,
                { x: x(item.at!) - 14, y: middle - 14, w: 28, h: 28 })
              : null)}
            {acceptanceIndex >= 0 && wait && sliceButton("acceptance-wait", acceptanceIndex, t("wait.acceptance"), x(wait.startMs),
              <path d={`M${x(wait.startMs)} ${middle - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`} className="fill-hand-mark"/>,
              { x: x(wait.startMs) - 14, y: middle - 14, w: 28, h: 28 })}
          </>)}
        </div>
      </div>}
    </ScrollArea>
  </div>;
}
