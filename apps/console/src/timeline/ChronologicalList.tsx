import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import i18n from "i18next";
import { useTranslation } from "react-i18next";
import { clockTime, durationText, spanOutcome } from "../macro-task-display";
import type { MacroTaskTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "../macro-task-types";
import { locale } from "../i18n";
import { formatDurationMinutes } from "../i18n/format";
import { parseTimelineInstant as instant } from "./instant";
import type { TimelineBreak, TimelineScale } from "./scale";
import { eventName, eventReadout, spanReadout } from "./Tracks";
import { acceptanceInterval } from "./acceptance";

type Entry =
  | { kind: "acceptance"; atMs: number; endMs: number; open: boolean; row: TimelineRow }
  | { kind: "span"; atMs: number; span: TimelineSpan; row: TimelineRow | undefined }
  | { kind: "event"; atMs: number; event: TimelineEvent; row: TimelineRow | undefined }
  | { kind: "inquiry"; atMs: number; span: TimelineSpan; inquiry: NonNullable<TimelineSpan["inquiries"]>[number]; row: TimelineRow | undefined }
  | { kind: "break"; atMs: number; gap: TimelineBreak };

function marker(entry: Entry): ReactNode {
  if (entry.kind === "break") return <span aria-hidden="true" className="block w-4 border-t border-dashed border-line2"/>;
  if (entry.kind === "acceptance") return <span aria-hidden="true" className={`block w-4 shrink-0 border-t ${entry.open ? "border-dashed border-hand-mark" : "border-ink3"}`}/>;
  if (entry.kind === "inquiry") return <svg aria-hidden="true" viewBox="0 0 16 16" className="size-4 shrink-0 fill-card stroke-ink2"><path d="M8 2.5 13.5 8 8 13.5 2.5 8Z"/></svg>;
  if (entry.kind === "event") return entry.event.kind === "accept"
    ? <svg aria-hidden="true" viewBox="0 0 16 16" className="size-4 shrink-0 fill-none stroke-past"><path d="m2.5 8 3.5 3.5 7-7" strokeWidth="2"/></svg>
    : <span aria-hidden="true" className="size-2 shrink-0 rounded-full bg-past"/>;
  const outcome = spanOutcome(entry.span);
  if (entry.span.kind === "queue" || entry.span.kind === "host") return <span aria-hidden="true" className={`block w-4 shrink-0 border-t-2 ${entry.span.kind === "host" && outcome === "open" ? "border-dashed border-hand-mark" : "border-ink3"}`}/>;
  if (entry.span.kind === "routing") return <span aria-hidden="true" className={`block h-2.5 w-4 shrink-0 border bg-card ${outcome === "failed" ? "border-fail" : outcome === "running" ? "border-live" : "border-ink3"} ${["unknown", "cancelled", "abstention"].includes(outcome) ? "border-dashed" : ""}`}/>;
  return <span aria-hidden="true" className={`block h-2.5 w-4 shrink-0 border ${outcome === "running" ? "border-live bg-live" : outcome === "failed" ? "border-fail bg-fail" : outcome === "unknown" ? "border-hand-mark bg-hand-wash" : "border-line2 bg-past-wash"}`}/>;
}

/** A time-ordered companion to the SVG tracks. A break is the scale's own interval, never a second estimate of idle time. */
export function ChronologicalList({ timeline, scale, nowMs, onOpen }: {
  timeline: MacroTaskTimeline; scale: TimelineScale; nowMs: number;
  onOpen: (runId: string, key: string) => void;
}) {
  const { t } = useTranslation("tasks");
  const rootRef = useRef<HTMLDivElement>(null);
  const rows = new Map(timeline.rows.map(row => [row.runId, row]));
  const entries: Entry[] = [];
  for (const span of timeline.spans) {
    const atMs = instant(span.startAt);
    if (atMs === null) continue;
    const row = rows.get(span.runId);
    entries.push({ kind: "span", atMs, span, row });
    for (const inquiry of span.inquiries ?? []) {
      const inquiryAt = instant(inquiry.createdAt);
      if (inquiryAt !== null) entries.push({ kind: "inquiry", atMs: inquiryAt, span, inquiry, row });
    }
  }
  for (const event of timeline.events) {
    const atMs = instant(event.at);
    if (atMs !== null) entries.push({ kind: "event", atMs, event, row: rows.get(event.runId) });
  }
  for (const row of timeline.rows) {
    const wait = acceptanceInterval(row, timeline.spans.filter(span => span.runId === row.runId), nowMs);
    if (wait) entries.push({ kind: "acceptance", atMs: wait.startMs, endMs: wait.endMs, open: wait.open, row });
  }
  for (const gap of scale.breaks) entries.push({ kind: "break", atMs: gap.startMs, gap });
  // A wait starting at a break boundary must remain visible before that separator.
  entries.sort((a, b) => a.atMs - b.atMs || Number(a.kind === "break") - Number(b.kind === "break"));

  const day = (ms: number) => new Date(ms).toDateString();
  const date = new Intl.DateTimeFormat(locale, { year: "numeric", month: "long", day: "numeric", weekday: "short" });
  let lastDay: string | null = null;
  const content = entries.flatMap((entry, index) => {
    const currentDay = day(entry.atMs);
    const heading = currentDay === lastDay ? [] : [<h3 key={`day:${currentDay}`} className="sticky top-0 z-10 border-b border-line2 bg-background px-4 py-2 text-xs font-medium text-ink2">{date.format(entry.atMs)}</h3>];
    lastDay = currentDay;
    if (entry.kind === "break") return [...heading, <div key={`break:${entry.gap.startMs}:${index}`} data-slot="chronological-break" data-start={entry.gap.startMs} data-end={entry.gap.endMs}
      className="grid grid-cols-[4.5rem_1rem_minmax(0,1fr)] items-center gap-3 border-b border-line2 bg-sunk px-4 py-2 text-xs text-ink3">
      <time dateTime={new Date(entry.atMs).toISOString()}>{clockTime(entry.atMs)}</time>{marker(entry)}<span>{t("idle", { duration: formatDurationMinutes(entry.gap.endMs - entry.gap.startMs) })}</span>
    </div>];
    const runId = entry.kind === "acceptance" ? entry.row.runId : entry.kind === "event" ? entry.event.runId : entry.span.runId;
    const key = entry.kind === "acceptance" ? `settle:${entry.row.runId}` : entry.kind === "event" ? `event:${entry.event.seq}` : entry.kind === "inquiry" ? `inquiry:${entry.inquiry.inquiryId}` : `span:${entry.span.spanId}`;
    const openKey = entry.kind === "inquiry" ? `span:${entry.span.spanId}` : key;
    const taskName = entry.row?.title ?? runId;
    let description: string;
    if (entry.kind === "acceptance") description = `${t("review")} · ${durationText(entry.endMs - entry.atMs)}`;
    else if (entry.kind === "event") description = eventName(entry.event);
    else if (entry.kind === "inquiry") description = [t("drawing.inquiry"), entry.inquiry.author, i18n.t(`taskDetail:inquiry.${entry.inquiry.state}`)].filter(Boolean).join(" · ");
    else {
      const span = entry.span;
      const outcome = spanOutcome(span);
      const endMs = instant(span.endAt) ?? (["open", "running", "unknown"].includes(outcome) ? nowMs : null);
      const duration = endMs === null ? null : durationText(endMs - entry.atMs);
      const name = span.kind === "execution" && span.turnIndex !== null ? t("turn", { n: span.turnIndex })
        : span.kind === "host" ? i18n.t(entry.row?.rootLeadRunId ? "console:waitingForLead" : "console:waitingForHost")
          : t(`kind.${span.kind}`);
      description = [name, duration, outcome === "unknown" ? t("drawing.stopUnconfirmed") : null].filter(Boolean).join(" · ");
    }
    const readout = entry.kind === "span" ? spanReadout(entry.span, nowMs, undefined, entry.row) : entry.kind === "event" ? eventReadout(entry.event) : description;
    return [...heading, <button aria-label={`${clockTime(entry.atMs)} · ${taskName} · ${readout}`} key={key} type="button" data-slot="chronological-item" data-key={key} data-run-id={runId}
      className="grid w-full grid-cols-[4.5rem_1rem_minmax(0,1fr)] items-center gap-3 border-b border-line2 px-4 py-2.5 text-left text-sm text-ink hover:bg-sunk focus-visible:bg-sunk focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ink"
      onClick={() => onOpen(runId, openKey)} onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        event.preventDefault(); event.stopPropagation();
        const items = Array.from(rootRef.current?.querySelectorAll<HTMLButtonElement>("[data-slot=chronological-item]") ?? []);
        items[Math.max(0, Math.min(items.length - 1, items.indexOf(event.currentTarget) + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
      }}>
      <time dateTime={new Date(entry.atMs).toISOString()} className="font-mono text-xs tabular-nums text-ink3">{clockTime(entry.atMs)}</time>
      <span className="flex w-4 justify-center">{marker(entry)}</span>
      <span className="min-w-0"><span className="block truncate font-medium">{taskName}</span><span className="block truncate text-xs text-ink2">{description}</span></span>
    </button>];
  });
  return <div ref={rootRef} data-slot="chronological-list" role="region" aria-label={t("chronological")} className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-background">{content}</div>;
}
