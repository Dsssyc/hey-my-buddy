import type { MacroTaskTimeline } from "../macro-task-types";

/** Keep each displayed row's whole history, including its continuations. */
export function visibleTimeline(timeline: MacroTaskTimeline, unfinished: boolean): MacroTaskTimeline {
  if (!unfinished) return timeline;
  const rows = timeline.rows.filter(row => row.category === "active" || row.category === "host" || row.category === "review");
  const runIds = new Set(rows.map(row => row.runId));
  return {
    ...timeline, rows,
    spans: timeline.spans.filter(span => runIds.has(span.runId)),
    events: timeline.events.filter(event => runIds.has(event.runId)),
  };
}
