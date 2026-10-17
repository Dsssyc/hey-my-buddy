import { acceptanceWaitMs } from "../macro-task-display";
import type { TimelineRow, TimelineSpan } from "../macro-task-types";
import { parseTimelineInstant as instant } from "./instant";

/** The same confirmed delivery-to-verdict interval in the timeline. */
export function acceptanceInterval(row: TimelineRow, spans: readonly TimelineSpan[], nowMs: number) {
  const executions = spans.filter(span => span.kind === "execution");
  const latest = executions.reduce<TimelineSpan | null>((last, span) => (instant(span.endAt) ?? -Infinity) > (instant(last?.endAt) ?? -Infinity) ? span : last, null);
  const open = row.state === "delivered" && executions.length > 0 && executions.every(span => span.endAt !== null && span.shutdownConfirmed === true);
  const startMs = latest ? instant(latest.endAt) : null;
  const endMs = open ? nowMs : acceptanceWaitMs(row, spans) !== null ? instant(row.acceptedAt ?? row.reviewRejectedAt) : null;
  return startMs !== null && endMs !== null ? { startMs, endMs, open } : null;
}
