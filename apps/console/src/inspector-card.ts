import { actorText } from "./actor";
import i18n from "i18next";
/**
 * Pure card models for the pinned timeline inspector (0.15 C2, restructured
 * 0.16 P1.6). One selection — a span, a Host event, a settlement flag or a
 * whole micro task — becomes one card of at most six labelled fields (类型、
 * 微任务、回合、buddy、时间、结果); every value comes from the bounded read and
 * missing facts render as 未记录 rather than guesses. The model keeps the last
 * resolvable facts when a refresh drops the selected record, so the inspector
 * never silently clears.
 */

import type { MacroTaskTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "./macro-task-types";
import type { SpanOutcome, TimelineItem } from "./macro-task-display";
import { recordedRoutingMode } from "./routing-display";
import {
  clockTime, buddyLabel, buddyRawLabel, displayTitle, durationText, outcomeLabel,
  rowLabelItem, rowStateInfo, settleItem, spanHead, spanOutcome, toMs,
  acceptanceWaitText, eventItem, spanFacts, titleLineTooltip, type FriendlyBuddy,
} from "./macro-task-display";
import { latestExecutionResult, relatedEventsForRun, relatedEventsForSpan, runRollup, hasScopeLimitations, type RunRollup } from "./macro-task-metrics";

export type InspectorSelection =
  | { type: "item"; key: string }
  | { type: "run"; runId: string };

export type CardLink =
  | { kind: "item"; item: TimelineItem }
  | { kind: "run"; runId: string; label: string };

export type CardField = {
  label: string;
  value: string;
  tone?: "muted" | "warn";
  /** Tooltip carrying what the two-line clamp may hide. */
  title?: string;
  /** The 微任务 field links to selecting that micro task's row. */
  link?: CardLink;
};

export type RelatedEvent = { item: TimelineItem; event: TimelineEvent };

export type InspectorCard = {
  /** Selection identity the card was built for. */
  key: string;
  head: string;
  /** Fixed order 类型、微任务、回合、buddy、时间、结果; inapplicable fields are absent. */
  fields: CardField[];
  /** What the 打开详情 button opens; null when no detail target exists. */
  openItem: TimelineItem | null;
  /** Host events matched by non-empty record identity. */
  relatedEvents: RelatedEvent[];
  /** Rebuild copy from retained facts when the selected record leaves a later read. */
  refreshCopy?: () => InspectorCard;
};

const UNRECORDED = () => i18n.t("console:notRecorded");

function microTaskField(row: TimelineRow): CardField {
  const title = displayTitle(row.titleSource, row.title);
  return {
    label: i18n.t("console:microTask"),
    value: title.text,
    title: titleLineTooltip(title),
    link: { kind: "run", runId: row.runId, label: title.text },
  };
}

function spanTimingValue(span: TimelineSpan, observedAtMs: number | null): CardField {
  const startMs = toMs(span.startAt);
  const endMs = toMs(span.endAt);
  if (span.clockSkew === true || (startMs !== null && endMs !== null && endMs < startMs)) {
    return { label: i18n.t("console:time"), value: i18n.t("console:invalidTimeDurationNotCalculated"), tone: "warn" };
  }
  if (startMs === null) return { label: i18n.t("console:time"), value: i18n.t("console:timeNotRecorded") };
  if (endMs === null) {
    const outcome = spanOutcome(span);
    if (outcome === "running") {
      return { label: i18n.t("console:time"), value: observedAtMs !== null ? i18n.t("console:sinceRunningAsOf", { v0: clockTime(startMs), v1: clockTime(observedAtMs) }) : i18n.t("console:sinceRunning", { v0: clockTime(startMs) }) };
    }
    if (outcome === "open") {
      return { label: i18n.t("console:time"), value: i18n.t("console:sinceStillWaiting", { v0: clockTime(startMs) }) };
    }
    return { label: i18n.t("console:time"), value: i18n.t("console:sinceEndTimeMissing", { v0: clockTime(startMs) }), tone: "warn" };
  }
  const duration = durationText(endMs - startMs);
  return { label: i18n.t("console:time"), value: `${clockTime(startMs)}–${clockTime(endMs)}${duration ? ` · ${duration}` : ""}` };
}

function errorFirstLine(span: TimelineSpan): string | null {
  if (typeof span.error !== "string" || !span.error.trim()) return null;
  return span.error.trim().split("\n", 1)[0] ?? null;
}

function spanResultValue(span: TimelineSpan, outcome: SpanOutcome): CardField {
  const error = errorFirstLine(span);
  if (outcome === "unknown") {
    return { label: i18n.t("console:result"), value: i18n.t("console:stopUnconfirmedDetail", { v0: error ? ` · ${error}` : "" }), tone: "warn", title: span.error };
  }
  if (span.resultStatus === "ok") return { label: i18n.t("console:result"), value: i18n.t("console:executionCompleted") };
  if (span.resultStatus === "failed") return { label: i18n.t("console:result"), value: i18n.t("console:executionFailedDetail", { v0: error ? ` · ${error}` : "" }), tone: "warn", title: span.error };
  if (span.resultStatus === "cancelled") return { label: i18n.t("console:result"), value: i18n.t("console:cancelled") };
  if (outcome === "running") return { label: i18n.t("console:result"), value: i18n.t("console:executing") };
  return { label: i18n.t("console:result"), value: outcomeLabel(span, outcome) };
}

function rollupFields(rollup: RunRollup, buddies?: readonly FriendlyBuddy[] | null): CardField[] {
  const { row } = rollup;
  const scopeLimited = hasScopeLimitations(rollup.limitations);
  const state = rowStateInfo(row);
  const fields: CardField[] = [
    { label: i18n.t("console:type"), value: row.kind === "helper" ? i18n.t("console:helper") : i18n.t("console:microTask") },
    microTaskField(row),
  ];
  const rounds = rollup.executions.length;
  fields.push({
    label: i18n.t("console:turn"),
    value: rounds ? i18n.t("console:turnsInTotal", { v0: rounds }) : scopeLimited ? i18n.t("console:noExecutionSpansInTheRecordedScope") : i18n.t("console:noRecordedExecutionSpans"),
    tone: rounds ? undefined : "muted",
  });
  fields.push({ label: "buddy", value: buddyLabel(row.buddy, buddies), title: buddyRawLabel(row.buddy) });
  const starts = rollup.executions.map(span => toMs(span.startAt)).filter((value): value is number => value !== null);
  const ends = rollup.executions.map(span => toMs(span.endAt)).filter((value): value is number => value !== null);
  const pending = rollup.pending.map(span => toMs(span.startAt)).filter((value): value is number => value !== null);
  const allStarts = [...starts, ...pending];
  const timeValue = allStarts.length
    ? ends.length || pending.length
      ? `${clockTime(Math.min(...allStarts))}–${clockTime(Math.max(...ends, ...pending))}`
      : i18n.t("console:since", { v0: clockTime(Math.min(...allStarts)) })
    : i18n.t("console:notRecorded");
  fields.push({ label: i18n.t("console:time"), value: timeValue });
  const latest = latestExecutionResult(rollup);
  const resultHead = latest ? `${state.label} · ${latest.label}` : i18n.t("console:joined", { v0: scopeLimited ? i18n.t("console:executionUnknownIncompleteRead") : i18n.t("console:notExecuted"), v1: state.label });
  const summary = row.summary;
  fields.push(summary
    ? { label: i18n.t("console:result"), value: i18n.t("console:resultSummary", { v0: resultHead, v1: summary }), title: summary }
    : { label: i18n.t("console:result"), value: i18n.t("console:noResultSummary", { v0: resultHead }) });
  return fields;
}

/**
 * Builds the fixed card for one selection from the current read, or null when
 * the selection cannot be resolved (truncated, filtered or not yet loaded).
 */
function inspectorCard(
  selection: InspectorSelection,
  timeline: MacroTaskTimeline,
  itemsByKey: ReadonlyMap<string, TimelineItem>,
  buddies?: readonly FriendlyBuddy[] | null,
): InspectorCard | null {
  const rowsById = new Map(timeline.rows.map(row => [row.runId, row]));
  const spansById = new Map(timeline.spans.map(span => [span.spanId, span]));
  const spansByRun = new Map<string, TimelineSpan[]>();
  for (const span of timeline.spans) {
    if (!spansByRun.has(span.runId)) spansByRun.set(span.runId, []);
    spansByRun.get(span.runId)!.push(span);
  }
  const observedAtMs = toMs(timeline.observedAt);

  if (selection.type === "run") {
    const row = rowsById.get(selection.runId);
    if (!row) return null;
    const rollup = runRollup(row, timeline);
    return {
      key: `run:${selection.runId}`,
      head: i18n.t("console:wholeMicroTask"),
      fields: rollupFields(rollup, buddies),
      openItem: itemsByKey.get(rowLabelItem(row).key) ?? rowLabelItem(row),
      relatedEvents: relatedEventsForRun(selection.runId, timeline.events)
        .map(event => ({ item: itemsByKey.get(`event:${event.seq}`) ?? null, event }))
        .filter((entry): entry is RelatedEvent => entry.item !== null),
    };
  }

  const item = itemsByKey.get(selection.key);
  if (!item) return null;

  if (selection.key.startsWith("span:")) {
    const span = spansById.get(selection.key.slice(5));
    const row = span ? rowsById.get(span.runId) : undefined;
    if (!span || !row) return null;
    const outcome = spanOutcome(span);
    const fields: CardField[] = [];
    if (span.kind === "queue") {
      fields.push({ label: i18n.t("console:type"), value: i18n.t("console:queue") }, microTaskField(row), spanTimingValue(span, observedAtMs));
      fields.push({ label: i18n.t("console:result"), value: span.endAt ? i18n.t("console:started") : i18n.t("console:stillQueued") });
    } else if (span.kind === "host") {
      fields.push({ label: i18n.t("console:type"), value: item.head }, microTaskField(row), spanTimingValue(span, observedAtMs));
      fields.push(span.summary
        ? { label: i18n.t("console:result"), value: outcomeLabel(span, outcome), title: span.summary }
        : { label: i18n.t("console:result"), value: outcomeLabel(span, outcome) });
    } else if (span.kind === "routing") {
      const routing = span.routing;
      const preference = (value: string | null | undefined) => ({
        matched: i18n.t("console:matchesPreference"), alternative: i18n.t("console:differsFromPreference"), none: i18n.t("console:noPreference"),
      }[value ?? ""] ?? (value || UNRECORDED()));
      const userPreference = routing?.policyCheck?.userPreference;
      const deviated = userPreference === "alternative";
      const reason = routing?.reason?.trim() || UNRECORDED();
      const amount = (value: number | null | undefined, unit: string) =>
        value != null && Number.isFinite(value) && value >= 0 ? `${value} ${unit}` : UNRECORDED();
      fields.push({ label: i18n.t("console:type"), value: i18n.t("console:routing") }, microTaskField(row));
      fields.push({ label: i18n.t("console:actualMode"), value: recordedRoutingMode(routing?.routingMode) });
      fields.push({ label: i18n.t("console:selectedBuddy"), value: routing?.selectedBuddy ? buddyLabel(routing.selectedBuddy, buddies) : UNRECORDED() });
      fields.push({ label: i18n.t("console:reason"), value: reason });
      fields.push({ label: i18n.t("console:preferenceResult"), value: routing?.policyCheck
        ? i18n.t("console:user", { v0: preference(userPreference), v1: deviated ? i18n.t("console:reasonForDeviation", { v0: reason }) : "" })
        : UNRECORDED() });
      fields.push(spanTimingValue(span, observedAtMs), { label: i18n.t("console:result"), value: outcomeLabel(span, outcome) });
      fields.push({ label: i18n.t("console:elapsedTimeElapsedms"), value: amount(routing?.usage?.elapsedMs, "ms") },
        { label: i18n.t("console:toolCallsToolcalls"), value: amount(routing?.usage?.toolCalls, i18n.t("console:calls")) },
        { label: i18n.t("console:bytesReadBytesread"), value: amount(routing?.usage?.bytesRead, "bytes") });
    } else {
      fields.push({ label: i18n.t("console:type"), value: spanHead(span.kind) }, microTaskField(row));
      fields.push({
        label: i18n.t("console:turn"),
        value: span.turnIndex !== null && span.turnIndex !== undefined ? i18n.t("console:turnNumber", { v0: span.turnIndex }) : UNRECORDED(),
      });
      fields.push({
        label: "buddy",
        value: buddyLabel(span.buddy, buddies),
        title: buddyRawLabel(span.buddy),
      });
      fields.push(spanTimingValue(span, observedAtMs), spanResultValue(span, outcome));
    }
    return {
      key: selection.key,
      head: span.turnIndex != null ? i18n.t("console:itemTurn", { v0: item.head, v1: span.turnIndex }) : item.head,
      fields,
      openItem: item,
      relatedEvents: relatedEventsForSpan(span, timeline.events)
        .map(event => ({ item: itemsByKey.get(`event:${event.seq}`) ?? null, event }))
        .filter((entry): entry is RelatedEvent => entry.item !== null),
    };
  }

  if (selection.key.startsWith("event:")) {
    const seq = Number(selection.key.slice(6));
    const event = timeline.events.find(candidate => candidate.seq === seq);
    if (!event) return null;
    const row = rowsById.get(event.runId);
    const fields: CardField[] = [
      { label: i18n.t("console:type"), value: i18n.t("console:hostEventType", { v0: event.label || event.kind }) },
      ...(row ? [microTaskField(row)] : []),
      { label: i18n.t("console:time"), value: clockTime(event.at) },
      { label: i18n.t("console:result"), value: event.summary || UNRECORDED(), title: event.summary || undefined },
    ];
    if (event.actor) fields.push({ label: i18n.t("shell:actor.recordedBy"), value: actorText(event.actor), tone: "muted" });
    return {
      key: selection.key,
      head: i18n.t("console:hostEventHeading", { v0: event.label || event.kind }),
      fields,
      openItem: item,
      relatedEvents: [],
    };
  }

  if (selection.key.startsWith("settle:")) {
    const runId = selection.key.slice(7);
    const row = rowsById.get(runId);
    if (!row) return null;
    const rejected = !row.acceptedAt && !!row.reviewRejectedAt;
    const fields: CardField[] = [
      { label: i18n.t("console:type"), value: rejected ? i18n.t("console:acceptanceIssue") : i18n.t("console:acceptance") },
      microTaskField(row),
    ];
    const wait = acceptanceWaitText(row, spansByRun.get(runId) ?? []);
    fields.push({
      label: i18n.t("console:time"),
      value: wait ? `${clockTime(row.acceptedAt ?? row.reviewRejectedAt)} · ${wait}` : clockTime(row.acceptedAt ?? row.reviewRejectedAt),
      title: wait ?? undefined,
    });
    fields.push({ label: i18n.t("console:result"), value: rejected ? i18n.t("console:acceptanceIssue") : i18n.t("console:accepted") });
    return {
      key: selection.key,
      head: rejected ? i18n.t("console:acceptanceIssue") : i18n.t("console:accepted"),
      fields,
      openItem: itemsByKey.get(settleItem(row)?.key ?? "") ?? item,
      relatedEvents: [],
    };
  }

  // Row labels and any future item kinds select the whole micro task.
  const runId = selection.key.startsWith("row:") ? selection.key.slice(4) : item.runId;
  const row = rowsById.get(runId);
  if (!row) return null;
  const rollup = runRollup(row, timeline);
  return {
    key: selection.key,
    head: i18n.t("console:wholeMicroTask"),
    fields: rollupFields(rollup, buddies),
    openItem: itemsByKey.get(rowLabelItem(row).key) ?? item,
    relatedEvents: relatedEventsForRun(runId, timeline.events)
      .map(event => ({ item: itemsByKey.get(`event:${event.seq}`) ?? null, event }))
      .filter((entry): entry is RelatedEvent => entry.item !== null),
  };
}

/** Retain the bounded facts with a cached card so language changes preserve its evidence. */
export function buildInspectorCard(
  selection: InspectorSelection,
  timeline: MacroTaskTimeline,
  itemsByKey: ReadonlyMap<string, TimelineItem>,
  buddies?: readonly FriendlyBuddy[] | null,
): InspectorCard | null {
  const card = inspectorCard(selection, timeline, itemsByKey, buddies);
  if (!card) return null;
  return { ...card, refreshCopy: () => {
    const items = new Map<string, TimelineItem>();
    const rows = new Map(timeline.rows.map(row => [row.runId, row]));
    for (const row of timeline.rows) {
      const label = rowLabelItem(row);
      items.set(label.key, label);
      const settlement = settleItem(row);
      if (settlement) items.set(settlement.key, settlement);
    }
    for (const span of timeline.spans) {
      const row = rows.get(span.runId);
      if (!row) continue;
      const { item } = spanFacts(span, row, toMs(timeline.observedAt), buddies);
      items.set(item.key, item);
    }
    for (const event of timeline.events) {
      const item = eventItem(event, rows);
      items.set(item.key, item);
    }
    return inspectorCard(selection, timeline, items, buddies)!;
  } };
}
