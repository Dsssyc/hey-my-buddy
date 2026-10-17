import i18n from "i18next";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { clockTime, durationText, spanOutcome, outcomeLabel, buddyLabel, acceptanceWaitMs } from "../macro-task-display";
import type { TimelineRow, TimelineSpan } from "../macro-task-types";
import type { FriendlyBuddy } from "../macro-task-display";
import type { TimelineScale } from "./scale";
import { parseTimelineInstant as instant } from "./instant";

export function spanReadout(span: TimelineSpan, nowMs: number, buddies?: readonly FriendlyBuddy[] | null, row?: TimelineRow): string {
  const outcome = spanOutcome(span);
  const start = instant(span.startAt);
  const end = instant(span.endAt) ?? (outcome === "running" || outcome === "open" || outcome === "unknown" ? nowMs : null);
  const head = span.turnIndex !== null ? i18n.t("tasks:turn", { n: span.turnIndex })
    : span.kind === "host" ? i18n.t(row?.rootLeadRunId ? "console:waitingForLead" : "console:waitingForHost")
      : i18n.t(`tasks:kind.${span.kind}`);
  return [head, start === null ? null : clockTime(start), end === null ? null : clockTime(end),
    start !== null && end !== null ? durationText(end - start) : null, buddyLabel(span.buddy, buddies), outcomeLabel(span, outcome)].filter(Boolean).join(" · ");
}

/** The same scale draws the one axis and all rows; waits cross every break. */
export function Axis({ scale, ongoing }: { scale: TimelineScale; ongoing: boolean }) {
  const { t } = useTranslation("tasks");
  const width = scale.widthPx;
  return <svg data-slot="timeline-axis" viewBox={`-8 0 ${width + 16} 36`} width={width + 16} height="36" className="block max-w-none overflow-hidden text-xs" aria-label={t("timeline")}>
    <line x1={0} x2={width} y1={30} y2={30} className="stroke-line2"/>
    {scale.breaks.map(gap => <rect key={gap.startMs} x={gap.startX} width={gap.endX - gap.startX} height={36} className="fill-sunk"/>)}
    {scale.ticks.map(tick => {
      const x = scale.toX(tick.atMs)!;
      return <g key={tick.atMs}><line x1={x} x2={x} y1={tick.labeled ? 24 : 27} y2={31} className="stroke-line2"/>
        {tick.labeled && !(ongoing && x > width - 100) && <text x={x} y={16} textAnchor={x < 30 ? "start" : x > width - 30 ? "end" : "middle"} className="fill-ink3">{clockTime(tick.atMs)}</text>}</g>;
    })}
    {ongoing && <text x={width} y={16} textAnchor="end" className="fill-live">{t("now")} {clockTime(scale.toTime(width))}</text>}
  </svg>;
}

export function Track({ row, spans, scale, nowMs, ongoing, buddies, onOpen, onHover, onFocusSpan, compact, selected }: {
  row: TimelineRow; spans: TimelineSpan[]; scale: TimelineScale; nowMs: number; ongoing: boolean;
  buddies?: readonly FriendlyBuddy[] | null; compact: boolean; selected: boolean;
  onOpen: (runId: string, key: string) => void; onHover: (span: TimelineSpan | null) => void; onFocusSpan: (span: TimelineSpan | null) => void;
}) {
  const { t } = useTranslation("tasks");
  const height = compact ? 32 : 62;
  const middle = height / 2;
  const position = (ms: number | null) => ms === null ? null : scale.toX(ms);
  const executions = spans.filter(span => span.kind === "execution");
  const latest = executions.reduce<TimelineSpan | null>((last, span) => (instant(span.endAt) ?? -Infinity) > (instant(last?.endAt) ?? -Infinity) ? span : last, null);
  const settledWait = acceptanceWaitMs(row, spans);
  const acceptanceOpen = row.state === "delivered" && executions.length > 0 && executions.every(span => span.endAt !== null && span.shutdownConfirmed === true);
  const waitStart = latest ? instant(latest.endAt) : null;
  const waitEnd = acceptanceOpen ? nowMs : settledWait !== null ? instant(row.acceptedAt ?? row.reviewRejectedAt) : null;
  return <svg data-slot="timeline-track" viewBox={`-8 0 ${scale.widthPx + 16} ${height}`} width={scale.widthPx + 16} height={height} preserveAspectRatio="none" className="block max-w-none overflow-hidden border-t text-xs" aria-label={t("track", { title: row.title })}>
    {scale.breaks.map(gap => <rect data-slot="timeline-break" key={gap.startMs} x={gap.startX} width={gap.endX - gap.startX} height={height} className="fill-sunk"/>)}
    {ongoing && <line data-slot="timeline-now" x1={scale.widthPx} x2={scale.widthPx} y2={height} className="stroke-live opacity-60"/>}
    {waitStart !== null && waitEnd !== null && position(waitStart) !== null && position(waitEnd) !== null && <line data-slot="acceptance-wait" x1={position(waitStart)!} x2={position(waitEnd)!} y1={middle} y2={middle} strokeWidth={acceptanceOpen ? 2 : 1.5} strokeDasharray={acceptanceOpen ? "5 4" : undefined} className={acceptanceOpen ? "stroke-hand-mark" : "stroke-ink3"}/>}
    {spans.map(span => {
      const outcome = spanOutcome(span);
      const start = position(instant(span.startAt));
      const end = position(instant(span.endAt) ?? (["running", "open", "unknown"].includes(outcome) ? nowMs : null));
      if (start === null || end === null || end < start) return null;
      const execution = span.kind === "execution" || span.kind === "routing";
      const label = spanReadout(span, nowMs, buddies, row);
      return <g key={span.spanId} role="button" tabIndex={-1} data-key={`span:${span.spanId}`} aria-label={t("segment", { label })}
        onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(row.runId, `span:${span.spanId}`); } }}
        onFocus={() => onFocusSpan(span)} onBlur={() => onFocusSpan(null)} onClick={() => onOpen(row.runId, `span:${span.spanId}`)} onPointerEnter={() => onHover(span)} onPointerLeave={() => onHover(null)}
        className="cursor-pointer outline-none">
        {selected && <rect data-slot="selected-span" x={Math.max(-6, start - 2)} y={execution ? middle - 9 : middle - 3} width={Math.max(5, end - start + 4)} height={execution ? 18 : 6} fill="none" className="stroke-live" strokeWidth={1}/>}
        <rect x={start - 3} y={middle - 16} width={Math.max(6, end - start + 6)} height={32} fill="transparent"/>
        {execution ? <rect x={start} y={middle - 7} width={Math.max(1, end - start)} height={14} rx={1}
          strokeWidth={1} strokeDasharray={outcome === "unknown" ? "3 2" : undefined}
          className={cn(outcome === "running" ? "fill-live stroke-live" : outcome === "failed" ? "fill-fail stroke-fail" : outcome === "unknown" ? "fill-hand-wash stroke-hand-mark" : "fill-past-wash stroke-line2", span.kind === "routing" && "fill-card")}/>
          : <line x1={start} x2={end} y1={middle} y2={middle} strokeWidth={outcome === "open" ? 2 : 1.5} strokeDasharray={outcome === "open" ? "5 4" : undefined}
            className={outcome === "open" ? "stroke-hand-mark" : "stroke-ink3"}/>}
      </g>;
    })}
  </svg>;
}
