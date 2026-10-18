import { spanOutcome } from "../macro-task-display";
import type { MacroTaskTimeline, TimelineRow, TimelineSpan } from "../macro-task-types";
import { parseTokenUsage } from "../task-detail/parse";
import { parseTimelineInstant } from "./instant";

/**
 * Lanes group the timeline's rows by the recorded ownership facts (ADR-026
 * §10): a Lead is a row that itself carries delegation authority, and its lane
 * holds every row whose root tree it owns, helpers included. Nothing is
 * inferred from names or from having children; rows without a Lead stay in the
 * ordinary list in the tree order the service sent.
 */
export type LaneGroup = { lead: TimelineRow; members: TimelineRow[] };
export type LaneLayout = { lanes: LaneGroup[]; ordinary: TimelineRow[] };

export function groupLanes(rows: readonly TimelineRow[]): LaneLayout {
  const leads = rows.filter(row => row.delegationAuthority);
  const leadIds = new Set(leads.map(row => row.runId));
  const owned = new Set<string>();
  const lanes = leads.map(lead => {
    const members = rows.filter(row => row.rootLeadRunId === lead.runId && row.runId !== lead.runId);
    for (const member of members) owned.add(member.runId);
    return { lead, members };
  });
  const ordinary = rows.filter(row => !leadIds.has(row.runId) && !owned.has(row.runId));
  return { lanes, ordinary };
}

/** How a lane's tokens add up: cached input is never counted twice, and a missing or partial record is never a number. */
export type LaneUsage = { tokens: number | null; partial: boolean };

export type LaneSummary = {
  /** Wall-clock from the lane's earliest recorded start to its latest instant; null when no recorded time. */
  elapsedMs: number | null;
  /** Sum of the lane's confirmable execution intervals; concurrent members each count. */
  executionMs: number | null;
  usage: LaneUsage;
};

/**
 * The lane's own three numbers. Elapsed is wall-clock from the lane's earliest
 * recorded start (the earliest row's creation when no span has one) to its
 * latest recorded instant — the read's clock while anything is still open.
 * Execution is the cumulative length of every confirmable execution interval,
 * a Lead's Router runs included; overlapping members each count their own.
 * Corrupt timestamps feed no number.
 */
export function laneSummary(lane: LaneGroup, timeline: MacroTaskTimeline): LaneSummary {
  const runIds = new Set([lane.lead.runId, ...lane.members.map(row => row.runId)]);
  const spans = timeline.spans.filter(span => runIds.has(span.runId));
  const events = timeline.events.filter(event => runIds.has(event.runId));
  const observedAtMs = parseTimelineInstant(timeline.observedAt);
  const usable = spans.filter(span => span.clockSkew !== true);
  const starts = usable.map(span => parseTimelineInstant(span.startAt)).filter((at): at is number => at !== null);
  const created = [lane.lead, ...lane.members].map(row => parseTimelineInstant(row.createdAt)).filter((at): at is number => at !== null);
  const earliest = starts.length ? Math.min(...starts) : created.length ? Math.min(...created) : null;
  const latest: number[] = [];
  let executionMs = 0;
  for (const span of usable) {
    const startMs = parseTimelineInstant(span.startAt);
    if (startMs === null) continue;
    const outcome = spanOutcome(span);
    const recordedEnd = parseTimelineInstant(span.endAt);
    if (recordedEnd !== null) latest.push(recordedEnd);
    const open = outcome === "running" || outcome === "open";
    if (open && observedAtMs !== null && observedAtMs >= startMs) latest.push(observedAtMs);
    if (span.kind !== "execution" && span.kind !== "routing" || outcome === "unknown") continue;
    const endMs = open ? observedAtMs : recordedEnd;
    if (endMs === null || endMs < startMs) continue;
    executionMs += endMs - startMs;
  }
  for (const event of events) {
    const atMs = parseTimelineInstant(event.at);
    if (atMs !== null) latest.push(atMs);
  }
  const last = latest.length ? Math.max(...latest) : null;
  return {
    elapsedMs: earliest !== null && last !== null && last >= earliest ? last - earliest : null,
    executionMs: executionMs > 0 ? executionMs : null,
    usage: laneUsage(spans),
  };
}

/** One execution's tokens under the recorded cache basis; cached input is never counted twice. */
function spanTokens(span: TimelineSpan): { tokens: number | null; partial: boolean } {
  if (span.kind !== "execution" && span.kind !== "routing") return { tokens: null, partial: false };
  const usage = parseTokenUsage(span.tokenUsage);
  if (!usage) return { tokens: null, partial: true };
  const input = usage.inputTokens, cached = usage.cachedInputTokens, output = usage.outputTokens;
  if (input === null && cached === null && output === null) return { tokens: null, partial: true };
  const total = usage.inputBasis === "excludes-cached" ? (input ?? 0) + (cached ?? 0) + (output ?? 0) : (input ?? 0) + (output ?? 0);
  const partial = input === null || output === null || usage.inputBasis === "excludes-cached" && cached === null
    || usage.completeness === "partial" || usage.completeness === "unknown";
  return { tokens: total, partial };
}

function laneUsage(spans: readonly TimelineSpan[]): LaneUsage {
  let total = 0, known = 0, unknown = 0, partial = false;
  for (const span of spans) {
    if (span.kind !== "execution" && span.kind !== "routing") continue;
    const reading = spanTokens(span);
    if (reading.tokens === null) { unknown += 1; continue; }
    known += 1;
    total += reading.tokens;
    partial = partial || reading.partial;
  }
  return { tokens: known > 0 ? total : null, partial: partial || known > 0 && unknown > 0 };
}

/**
 * The stretches a Lead waited for its owned micro tasks, derived only from
 * recorded facts: an execution whose disposition is `waiting` ends the Lead's
 * own work, and the same run's next queue, routing or execution span is the
 * wakeup. No end is invented — an open wait runs to the read's clock only
 * while the Lead row is still in its waiting-owned state.
 */
export type OwnedWait = { startMs: number; endMs: number; open: boolean };

export function ownedWaits(lead: TimelineRow, spans: readonly TimelineSpan[], nowMs: number): OwnedWait[] {
  const own = spans.filter(span => span.runId === lead.runId);
  const waits: OwnedWait[] = [];
  for (const span of own) {
    if (span.kind !== "execution" || span.disposition !== "waiting") continue;
    const start = parseTimelineInstant(span.endAt);
    if (start === null) continue;
    const wakeups = own
      .filter(other => other !== span && (other.kind === "queue" || other.kind === "routing" || other.kind === "execution"))
      .map(other => parseTimelineInstant(other.startAt))
      .filter((at): at is number => at !== null && at >= start);
    const end = wakeups.length ? Math.min(...wakeups) : lead.state === "waiting-owned" ? nowMs : null;
    if (end === null) continue;
    waits.push({ startMs: start, endMs: end, open: !wakeups.length });
  }
  return waits.sort((left, right) => left.startMs - right.startMs);
}
