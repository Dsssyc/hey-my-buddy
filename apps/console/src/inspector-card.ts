/**
 * Pure card models for the pinned timeline inspector (0.15 C2, restructured
 * 0.16 P1.6). One selection — a span, a Host event, a settlement flag or a
 * whole delegation — becomes one card of at most six labelled fields (类型、
 * 委派、回合、配置、时间、结果); every value comes from the bounded read and
 * missing facts render as 未记录 rather than guesses. The model keeps the last
 * resolvable facts when a refresh drops the selected record, so the inspector
 * never silently clears.
 */

import type { ObjectiveTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import type { SpanOutcome, TimelineItem } from "./objective-display";
import {
  clockTime, configurationLabel, configurationRawLabel, displayTitle, durationText, outcomeLabel,
  rowLabelItem, rowStateInfo, settleItem, spanHead, spanOutcome, toMs,
  acceptanceWaitText, titleLineTooltip, type FriendlyProfile,
} from "./objective-display";
import { latestExecutionResult, relatedEventsForRun, relatedEventsForSpan, runRollup, hasScopeLimitations, type RunRollup } from "./objective-metrics";

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
  /** The 委派 field links to selecting that delegation's row. */
  link?: CardLink;
};

export type RelatedEvent = { item: TimelineItem; event: TimelineEvent };

export type InspectorCard = {
  /** Selection identity the card was built for. */
  key: string;
  head: string;
  /** Fixed order 类型、委派、回合、配置、时间、结果; inapplicable fields are absent. */
  fields: CardField[];
  /** What the 打开详情 button opens; null when no detail target exists. */
  openItem: TimelineItem | null;
  /** Host events matched by non-empty record identity. */
  relatedEvents: RelatedEvent[];
};

const UNRECORDED = "未记录";

function delegationField(row: TimelineRow): CardField {
  const title = displayTitle(row.titleSource, row.title);
  return {
    label: "委派",
    value: title.text,
    title: titleLineTooltip(title),
    link: { kind: "run", runId: row.runId, label: title.text },
  };
}

function spanTimingValue(span: TimelineSpan, observedAtMs: number | null): CardField {
  const startMs = toMs(span.startAt);
  const endMs = toMs(span.endAt);
  if (span.clockSkew === true || (startMs !== null && endMs !== null && endMs < startMs)) {
    return { label: "时间", value: "时间异常，未计算时长", tone: "warn" };
  }
  if (startMs === null) return { label: "时间", value: "时间未记录" };
  if (endMs === null) {
    const outcome = spanOutcome(span);
    if (outcome === "running") {
      return { label: "时间", value: observedAtMs !== null ? `${clockTime(startMs)} 起 · 进行中（截至 ${clockTime(observedAtMs)}）` : `${clockTime(startMs)} 起 · 进行中` };
    }
    if (outcome === "open") {
      return { label: "时间", value: `${clockTime(startMs)} 起 · 仍在等待` };
    }
    return { label: "时间", value: `${clockTime(startMs)} 起 · 结束时间缺失`, tone: "warn" };
  }
  const duration = durationText(endMs - startMs);
  return { label: "时间", value: `${clockTime(startMs)}–${clockTime(endMs)}${duration ? ` · ${duration}` : ""}` };
}

function errorFirstLine(span: TimelineSpan): string | null {
  if (typeof span.error !== "string" || !span.error.trim()) return null;
  return span.error.trim().split("\n", 1)[0] ?? null;
}

function spanResultValue(span: TimelineSpan, outcome: SpanOutcome): CardField {
  const error = errorFirstLine(span);
  if (outcome === "unknown") {
    return { label: "结果", value: `结束未确认${error ? ` · ${error}` : ""}`, tone: "warn", title: span.error };
  }
  if (span.resultStatus === "ok") return { label: "结果", value: "执行完成" };
  if (span.resultStatus === "failed") return { label: "结果", value: `执行失败${error ? ` · ${error}` : ""}`, tone: "warn", title: span.error };
  if (span.resultStatus === "cancelled") return { label: "结果", value: "已取消" };
  if (outcome === "running") return { label: "结果", value: "执行中" };
  return { label: "结果", value: outcomeLabel(span, outcome) };
}

function rollupFields(rollup: RunRollup, profiles?: readonly FriendlyProfile[] | null): CardField[] {
  const { row } = rollup;
  const scopeLimited = hasScopeLimitations(rollup.limitations);
  const state = rowStateInfo(row);
  const fields: CardField[] = [
    { label: "类型", value: row.kind === "helper" ? "协助任务" : "委派" },
    delegationField(row),
  ];
  const rounds = rollup.executions.length;
  fields.push({
    label: "回合",
    value: rounds ? `共 ${rounds} 轮` : scopeLimited ? "已记录范围内没有执行片段" : "没有已记录的执行片段",
    tone: rounds ? undefined : "muted",
  });
  fields.push({ label: "配置", value: configurationLabel(row.configuration, profiles), title: configurationRawLabel(row.configuration) });
  const starts = rollup.executions.map(span => toMs(span.startAt)).filter((value): value is number => value !== null);
  const ends = rollup.executions.map(span => toMs(span.endAt)).filter((value): value is number => value !== null);
  const pending = rollup.pending.map(span => toMs(span.startAt)).filter((value): value is number => value !== null);
  const allStarts = [...starts, ...pending];
  const timeValue = allStarts.length
    ? ends.length || pending.length
      ? `${clockTime(Math.min(...allStarts))}–${clockTime(Math.max(...ends, ...pending))}`
      : `${clockTime(Math.min(...allStarts))} 起`
    : "未记录";
  fields.push({ label: "时间", value: timeValue });
  const latest = latestExecutionResult(rollup);
  const resultHead = latest ? `${state.label} · ${latest.label}` : `${scopeLimited ? "执行情况未知（读取不完整）" : "未执行"} · ${state.label}`;
  const summary = row.summary;
  fields.push(summary
    ? { label: "结果", value: `${resultHead} · 结果：${summary}`, title: summary }
    : { label: "结果", value: `${resultHead} · 结果：暂无` });
  return fields;
}

/**
 * Builds the fixed card for one selection from the current read, or null when
 * the selection cannot be resolved (truncated, filtered or not yet loaded).
 */
export function buildInspectorCard(
  selection: InspectorSelection,
  timeline: ObjectiveTimeline,
  itemsByKey: ReadonlyMap<string, TimelineItem>,
  profiles?: readonly FriendlyProfile[] | null,
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
      head: "整项委派",
      fields: rollupFields(rollup, profiles),
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
      fields.push({ label: "类型", value: "排队" }, delegationField(row), spanTimingValue(span, observedAtMs));
      fields.push({ label: "结果", value: span.endAt ? "已认领" : "仍在排队" });
    } else if (span.kind === "host") {
      fields.push({ label: "类型", value: "等待 Host" }, delegationField(row), spanTimingValue(span, observedAtMs));
      fields.push(span.summary
        ? { label: "结果", value: outcomeLabel(span, outcome), title: span.summary }
        : { label: "结果", value: outcomeLabel(span, outcome) });
    } else if (span.kind === "routing") {
      const routing = span.routing;
      const preference = (value: string | null | undefined) => ({
        matched: "符合偏好", alternative: "偏离偏好", none: "没有偏好", fallback: "偏好无可用候选",
      }[value ?? ""] ?? (value || UNRECORDED));
      const taskPreference = routing?.policyCheck?.taskPreference?.outcome;
      const userPreference = routing?.policyCheck?.userPreference;
      const deviated = taskPreference === "alternative" || userPreference === "alternative";
      const reason = routing?.reason?.trim() || UNRECORDED;
      const amount = (value: number | null | undefined, unit: string) =>
        value != null && Number.isFinite(value) && value >= 0 ? `${value} ${unit}` : UNRECORDED;
      fields.push({ label: "类型", value: "路由" }, delegationField(row));
      fields.push({ label: "已选配置", value: routing?.selectedProfile ? configurationLabel(routing.selectedProfile, profiles) : UNRECORDED });
      fields.push({ label: "理由", value: reason });
      fields.push({ label: "偏好结果", value: routing?.policyCheck
        ? `任务：${preference(taskPreference)}；用户：${preference(userPreference)}${deviated ? `；偏离理由：${reason}` : ""}`
        : UNRECORDED });
      fields.push(spanTimingValue(span, observedAtMs), { label: "结果", value: outcomeLabel(span, outcome) });
      fields.push({ label: "用时（elapsedMs）", value: amount(routing?.usage?.elapsedMs, "ms") },
        { label: "工具调用（toolCalls）", value: amount(routing?.usage?.toolCalls, "次") },
        { label: "读取量（bytesRead）", value: amount(routing?.usage?.bytesRead, "bytes") });
    } else {
      fields.push({ label: "类型", value: spanHead(span.kind) }, delegationField(row));
      fields.push({
        label: "回合",
        value: span.turnIndex !== null && span.turnIndex !== undefined ? `第 ${span.turnIndex} 轮` : UNRECORDED,
      });
      fields.push({
        label: "配置",
        value: configurationLabel(span.configuration, profiles),
        title: configurationRawLabel(span.configuration),
      });
      fields.push(spanTimingValue(span, observedAtMs), spanResultValue(span, outcome));
    }
    return {
      key: selection.key,
      head: span.turnIndex != null ? `${spanHead(span.kind)} · 第 ${span.turnIndex} 轮` : spanHead(span.kind),
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
      { label: "类型", value: `Host 事件 · ${event.label || event.kind}` },
      ...(row ? [delegationField(row)] : []),
      { label: "时间", value: clockTime(event.at) },
      { label: "结果", value: event.summary || UNRECORDED, title: event.summary || undefined },
    ];
    if (event.actor) fields.push({ label: "记录", value: `actor ${event.actor}`, tone: "muted" });
    return {
      key: selection.key,
      head: `Host 事件：${event.label || event.kind}`,
      fields,
      openItem: item,
      relatedEvents: [],
    };
  }

  if (selection.key.startsWith("settle:")) {
    const runId = selection.key.slice(7);
    const row = rowsById.get(runId);
    if (!row) return null;
    const rejected = row.acceptanceVerdict === "rejected";
    const fields: CardField[] = [
      { label: "类型", value: rejected ? "验收问题" : "验收" },
      delegationField(row),
    ];
    const wait = acceptanceWaitText(row, spansByRun.get(runId) ?? []);
    fields.push({
      label: "时间",
      value: wait ? `${clockTime(row.acceptedAt)}${wait ? ` · ${wait}` : ""}` : clockTime(row.acceptedAt),
      title: wait ?? undefined,
    });
    fields.push({ label: "结果", value: rejected ? "验收问题" : "已验收" });
    return {
      key: selection.key,
      head: rejected ? "验收问题" : "已验收",
      fields,
      openItem: itemsByKey.get(settleItem(row)?.key ?? "") ?? item,
      relatedEvents: [],
    };
  }

  // Row labels and any future item kinds select the whole delegation.
  const runId = selection.key.startsWith("row:") ? selection.key.slice(4) : item.runId;
  const row = rowsById.get(runId);
  if (!row) return null;
  const rollup = runRollup(row, timeline);
  return {
    key: selection.key,
    head: "整项委派",
    fields: rollupFields(rollup, profiles),
    openItem: itemsByKey.get(rowLabelItem(row).key) ?? item,
    relatedEvents: relatedEventsForRun(runId, timeline.events)
      .map(event => ({ item: itemsByKey.get(`event:${event.seq}`) ?? null, event }))
      .filter((entry): entry is RelatedEvent => entry.item !== null),
  };
}
