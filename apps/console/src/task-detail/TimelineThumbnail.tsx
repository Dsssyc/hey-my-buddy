import { cn } from "cn";
import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { buddyColorKey } from "../macro-task-display";
import type { TimelineEvent, TimelineSpan } from "../macro-task-types";
import { buddyColorsFor, leadingBuddyColor, runningBuddyColor } from "../timeline/buddyColors";
import { ConclusionMark } from "../timeline/ConclusionMark";
import { eventActions } from "../timeline/events";
import { parseTimelineInstant as instant } from "../timeline/instant";
import { createTimelineScale, type ScaleInput } from "../timeline/scale";
import { positionSpans, timelineDiamond } from "../timeline/Tracks";
import { WaitingDiamond } from "../timeline/trackMarks";
import type { acceptanceInterval } from "../timeline/acceptance";
import type { Step } from "./steps";

const EDGE = 9;
const MIDDLE = 11;
const WAIT_RADIUS = 3.5;

export function TimelineThumbnail({ input, width, spans, events, steps, runId, selectedKey, buddyColors, wait, failed, nowMs }: {
  input: ScaleInput; width: number; spans: TimelineSpan[]; events: TimelineEvent[]; steps: Step[]; runId: string;
  selectedKey: string | null; buddyColors: ReturnType<typeof buddyColorsFor>; wait: ReturnType<typeof acceptanceInterval>;
  failed: boolean; nowMs: number;
}) {
  const { t } = useTranslation("taskDetail");
  const id = useId();
  const scale = useMemo(() => createTimelineScale(input, Math.max(1, width - 2 * EDGE)), [input, width]);
  // The full scale reserves a minimum width for every fold. Squeeze those
  // coordinates uniformly when many folds cannot fit the whole thumbnail.
  const shrink = Math.min(1, Math.max(1, width - 2 * EDGE) / (scale.widthPx || 1));
  const px = (value: number) => EDGE + value * shrink;
  const x = (at: number) => px(scale.toX(at) ?? 0);
  const slices = positionSpans(spans, scale, nowMs);
  const dense = slices.filter(({ span }) => span.kind === "execution").length >= 8;
  const showDiamond = (start: number, end: number, open: boolean) => !dense || open || end - start >= WAIT_RADIUS * 2;
  const waitLine = (start: number, end: number, hand: boolean) =>
    <line x1={start} x2={end} y1={MIDDLE} y2={MIDDLE} strokeDasharray={hand ? "3 2" : undefined} className={hand ? "stroke-hand-mark" : "stroke-ink3"}/>;
  const focused = (key: string, at: number, end = at) => key === selectedKey &&
    <rect data-slot="thumbnail-selection" x={at - 4} y={MIDDLE - 8} width={end - at + 8} height={16} fill="none" className="stroke-focus"/>;
  return <svg data-slot="timeline-thumbnail" aria-hidden="true" viewBox={`0 0 ${width} ${MIDDLE * 2}`} className="block h-[22px] w-full">
    {input.domain && <line data-slot="thumbnail-baseline" x1={EDGE} x2={x(input.domain.endMs)} y1={MIDDLE} y2={MIDDLE} className="stroke-line2" strokeWidth={0.5}/>}
    {wait && waitLine(x(wait.startMs), x(wait.endMs), wait.open)}
    {slices.filter(slice => !slice.execution).map(({ span, start, end, outcome }) => <g key={span.spanId}>
      {waitLine(px(start), px(end), span.kind === "host" && outcome === "open")}
      {focused(`span:${span.spanId}`, px(start), px(end))}
    </g>)}
    {slices.filter(slice => slice.execution).map(({ span, start, end, outcome }) => {
      const color = buddyColors.get(buddyColorKey(span.buddy));
      const running = outcome === "running";
      const routing = span.kind === "routing";
      const barWidth = Math.max(1, (end - start) * shrink);
      return <g key={span.spanId}>
        <rect data-slot={routing ? "thumbnail-routing" : "thumbnail-execution"} x={px(start)} y={MIDDLE - 5} width={barWidth} height={10}
          fill={!routing && running && color ? runningBuddyColor(color) : color}
          strokeDasharray={routing && ["unknown", "cancelled", "abstention"].includes(outcome) ? "2 1" : undefined}
          className={cn(routing && "fill-card", outcome === "failed" ? "stroke-fail"
            : outcome === "unknown" && !routing ? "stroke-hand-mark" : routing && running ? "stroke-ink" : "stroke-ink3")} />
        {running && color && <>
          <defs><linearGradient id={`${id}-${span.spanId}`}><stop stopColor={leadingBuddyColor(color)} stopOpacity={0}/><stop offset="1" stopColor={leadingBuddyColor(color)}/></linearGradient></defs>
          <rect x={px(start) + Math.max(0, barWidth - 8)} y={MIDDLE - 4} width={Math.min(barWidth, 8)} height={8}
            fill={`url(#${id}-${span.spanId})`} className="execution-breathe"/>
        </>}
        {routing && running && <path d={`M${px(start) + barWidth} ${MIDDLE - 5}v10`} strokeWidth={2} className="stroke-ink execution-breathe"/>}
        {focused(`span:${span.spanId}`, px(start), px(end))}
      </g>;
    })}
    {slices.filter(({ span, start, end, outcome }) => span.kind === "host" && showDiamond(px(start), px(end), outcome === "open")).map(({ span, start }) =>
      <WaitingDiamond key={span.spanId} x={px(start)} y={MIDDLE} radius={WAIT_RADIUS}/>)}
    {wait && showDiamond(x(wait.startMs), x(wait.endMs), wait.open) && <WaitingDiamond x={x(wait.startMs)} y={MIDDLE} kind="review" radius={WAIT_RADIUS}/>}
    {wait && focused(`settle:${runId}`, x(wait.startMs))}
    {steps.map(step => step.at !== null && (step.kind === "delegation" || step.kind === "handoff") && <g key={step.key}>
      {step.kind === "delegation" ? <circle cx={x(step.at)} cy={MIDDLE} r={3} className="fill-past"/> :
        <path d={timelineDiamond(x(step.at), MIDDLE, 3.5)} className="fill-ink2"/>}
      {focused(step.key, x(step.at))}
    </g>)}
    {eventActions(events).map(({ event, relatedEvents }) => {
      const at = x(instant(event.at)!);
      const conclusion = event.kind === "accept" || event.kind === "conclude" && failed;
      const key = relatedEvents?.some(item => `event:${item.seq}` === selectedKey) ? selectedKey! : `event:${event.seq}`;
      return <g key={event.seq}>
        {conclusion ? <g transform={`translate(${at} ${MIDDLE}) scale(.65)`}><ConclusionMark kind={event.kind === "accept" ? "accepted" : "failed"} x={0} y={0}/></g>
          : <circle cx={at} cy={MIDDLE} r={2} className="fill-past"/>}
        {focused(key, at)}
      </g>;
    })}
    {spans.flatMap(span => (span.inquiries ?? []).map(inquiry => {
      const at = x(instant(inquiry.createdAt)!);
      const key = `inquiry:${inquiry.inquiryId}`;
      return <g key={key}>
        <path data-slot="thumbnail-inquiry" d={timelineDiamond(at, MIDDLE, 3.5)} className="fill-card stroke-ink2"/>
        {focused(key, at)}
      </g>;
    }))}
    {input.nowAtEnd && <line data-slot="thumbnail-now" x1={x(nowMs)} x2={x(nowMs)} y1={2} y2={20} className="stroke-live"/>}
    {slices.length === 0 && <text x={width / 2} y={MIDDLE} dominantBaseline="central" textAnchor="middle" className="fill-ink3 text-xs">{t("strip.empty")}</text>}
  </svg>;
}
