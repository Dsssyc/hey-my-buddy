/**
 * Pure card models for the pinned timeline inspector (0.15 C2). One selection —
 * a span, a Host event, a settlement flag or a whole delegation — becomes a
 * plain structure of labelled groups; every value comes from the bounded read
 * and missing facts render as 未记录 rather than guesses. The model keeps the
 * last resolvable facts when a refresh drops the selected record, so the
 * inspector never silently clears.
 */

import type { ObjectiveTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import type { SpanOutcome, TimelineItem } from "./objective-display";
import {
  clockTime, configurationLabel, displayTitle, durationText, outcomeLabel, rangeText, rowLabelItem,
  rowStateInfo, settleItem, spanHead, spanOutcome, toMs,
} from "./objective-display";
import { latestExecutionResult, relatedEventsForRun, relatedEventsForSpan, runRollup, hasScopeLimitations, type RunRollup } from "./objective-metrics";

export type InspectorSelection =
  | { type: "item"; key: string }
  | { type: "run"; runId: string };

export type CardLink =
  | { kind: "item"; item: TimelineItem }
  | { kind: "run"; runId: string; label: string };

export type CardLine = { text: string; tone?: "muted" | "warn" };

export type CardGroup = { label: string; lines: CardLine[]; link?: CardLink };

export type RelatedEvent = { item: TimelineItem; event: TimelineEvent };

export type InspectorCard = {
  /** Selection identity the card was built for. */
  key: string;
  head: string;
  groups: CardGroup[];
  /** What the 打开详情 button opens; null when no detail target exists. */
  openItem: TimelineItem | null;
  /** Host events matched by non-empty record identity. */
  relatedEvents: RelatedEvent[];
};

const UNRECORDED = "未记录";

function lines(...values: (string | null | undefined)[]): CardLine[] {
  return values.filter((value): value is string => !!value && value.trim().length > 0).map(text => ({ text }));
}

function spanTimingLine(span: TimelineSpan, observedAtMs: number | null): CardLine[] {
  const startMs = toMs(span.startAt);
  const endMs = toMs(span.endAt);
  if (span.clockSkew === true || (startMs !== null && endMs !== null && endMs < startMs)) {
    return [{ text: "时间异常，未计算时长", tone: "warn" }, ...lines(span.startAt ?? null, span.endAt ?? null)];
  }
  if (startMs === null) return [{ text: "时间未记录" }];
  if (endMs === null) {
    // Only a legitimately open span extends to the observation instant; a
    // terminal span with a missing end is unknown, never "至今".
    const outcome = spanOutcome(span);
    if (outcome === "running" || outcome === "open") {
      const through = observedAtMs !== null ? `至今（截至 ${clockTime(observedAtMs)}）` : "至今";
      return [{ text: `${clockTime(startMs)} ${through}` }];
    }
    return [{ text: `${clockTime(startMs)} · 结束时间缺失`, tone: "warn" }];
  }
  const duration = durationText(endMs - startMs);
  return [{ text: `${rangeText(startMs, endMs)}${duration ? ` · ${duration}` : ""}` }];
}

function resultLines(span: TimelineSpan, outcome: SpanOutcome): CardLine[] {
  const result = span.resultStatus === "ok" ? "成功" : span.resultStatus === "failed" ? "失败" : span.resultStatus === "cancelled" ? "已取消" : null;
  const stateText = outcomeLabel(span, outcome);
  // An unconfirmed stop outranks any terminal styling, exactly like the bars.
  if (outcome === "unknown") return [{ text: `结束未确认${result ? ` · 收据 ${result}` : ""}`, tone: "warn" }];
  return [{ text: `${result ?? "结果未记录"} · ${stateText}` }];
}

function recordIdentityLines(event: TimelineEvent): CardLine[] {
  const parts: string[] = [];
  if (event.attemptId) parts.push(`attempt ${event.attemptId}`);
  if (event.requestId) parts.push(`request ${event.requestId}`);
  if (event.artifactId) parts.push(`artifact ${event.artifactId}`);
  if (event.integrationId) parts.push(`integration ${event.integrationId}`);
  if (event.continuationId) parts.push(`continuation ${event.continuationId}`);
  return parts.map(text => ({ text, tone: "muted" as const }));
}

function rollupGroups(rollup: RunRollup, rowsById: Map<string, TimelineRow>): CardGroup[] {
  const { row } = rollup;
  const scopeLimited = hasScopeLimitations(rollup.limitations);
  const task: CardLine[] = [
    // U3: a task first-line fallback stays a clipped one-liner here too.
    { text: displayTitle(row.titleSource, row.title).text },
    ...(row.taskSummary ? [{ text: `任务开头：${row.taskSummary}`, tone: "muted" as const }] : []),
    ...(row.summary ? [{ text: `结果：${row.summary}（Worker 自述，非验收）`, tone: "muted" as const }] : [{ text: "尚无 Worker 结论", tone: "muted" as const }]),
    { text: row.kind === "helper" ? `第 ${row.depth + 1} 层协助任务` : "委派" },
  ];
  const taskLink: CardLink | undefined = row.parentRunId
    ? { kind: "run", runId: row.parentRunId, label: rowsById.get(row.parentRunId)
        ? displayTitle(rowsById.get(row.parentRunId)!.titleSource, rowsById.get(row.parentRunId)!.title).text : row.parentRunId }
    : undefined;
  if (rollup.helpers.length) task.push({ text: `协助任务 ${rollup.helpers.length} 个` });

  const rounds: CardLine[] = rollup.executions.length
    ? rollup.executions.map(span => {
      const startMs = toMs(span.startAt);
      const endMs = toMs(span.endAt);
      const outcome = spanOutcome(span);
      // A missing end is "至今" only for a live execution; otherwise unknown.
      const when = startMs === null ? UNRECORDED : endMs === null
        ? (outcome === "running" ? `${clockTime(startMs)}–至今` : `${clockTime(startMs)}–结束时间缺失`)
        : `${clockTime(startMs)}–${clockTime(endMs)}`;
      const result = outcome === "unknown" ? "结束未确认" : span.resultStatus === "ok" ? "成功" : span.resultStatus === "failed" ? "失败" : span.resultStatus === "cancelled" ? "已取消" : "未记录";
      return { text: `第 ${span.turnIndex ?? "?"} 轮 · ${when} · ${configurationLabel(span.configuration)} · ${result}` };
    })
    : [{ text: scopeLimited ? "已记录范围内没有执行片段" : "没有已记录的执行片段", tone: "muted" }];

  const latest = latestExecutionResult(rollup);
  const state = rowStateInfo(row);
  const resultGroup: CardLine[] = latest
    ? [{ text: `${latest.label} · 任务状态 ${state.label}` }]
    : [{ text: `${scopeLimited ? "执行情况未知（读取不完整）" : "未执行"} · 任务状态 ${state.label}`, tone: "muted" }];

  const acceptance: CardLine[] = row.acceptanceVerdict
    ? [{ text: `${row.acceptanceVerdict === "rejected" ? "验收问题" : row.acceptanceVerdict === "accepted" ? "已验收" : row.acceptanceVerdict} · ${row.acceptedAt ? clockTime(row.acceptedAt) : UNRECORDED}` }]
    : row.category === "review"
      ? [{ text: "待验收", tone: "warn" }]
      : [{ text: row.category === "ended" ? "验收未记录" : "尚未验收", tone: "muted" }];

  const pending: CardLine[] = rollup.pending.length
    ? rollup.pending.map(span => {
      const startMs = toMs(span.startAt);
      const kind = span.requestKind ? `等待 Host（${span.requestKind}）` : "等待 Host";
      const summaryText = span.summary ? ` · ${span.summary}` : "";
      return { text: `${kind}${summaryText} · 开始于 ${startMs !== null ? clockTime(startMs) : UNRECORDED}`, tone: "warn" as const };
    })
    : [{ text: scopeLimited ? "已记录范围内无待决" : "无", tone: "muted" }];

  const groups: CardGroup[] = [
    { label: "任务", lines: task, link: taskLink },
    { label: "轮次", lines: rounds },
    { label: "结果", lines: resultGroup },
    { label: "验收", lines: acceptance },
    { label: "待决", lines: pending },
  ];
  if (scopeLimited) {
    groups.push({ label: "边界", lines: [{ text: "读取不完整（筛选或截断），以上为已记录部分", tone: "warn" }] });
  }
  return groups;
}

/**
 * Builds the fixed card for one selection from the current read, or null when
 * the selection cannot be resolved (truncated, filtered or not yet loaded).
 */
export function buildInspectorCard(
  selection: InspectorSelection,
  timeline: ObjectiveTimeline,
  itemsByKey: ReadonlyMap<string, TimelineItem>,
): InspectorCard | null {
  const rowsById = new Map(timeline.rows.map(row => [row.runId, row]));
  const spansById = new Map(timeline.spans.map(span => [span.spanId, span]));
  const observedAtMs = toMs(timeline.observedAt);

  if (selection.type === "run") {
    const row = rowsById.get(selection.runId);
    if (!row) return null;
    const rollup = runRollup(row, timeline);
    return {
      key: `run:${selection.runId}`,
      head: "整项委派",
      groups: rollupGroups(rollup, rowsById),
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
    const groups: CardGroup[] = [];
    if (span.kind === "queue") {
      groups.push({ label: "排队", lines: [...spanTimingLine(span, observedAtMs), { text: span.endAt ? "已认领" : "仍在排队" }] });
    } else if (span.kind === "host") {
      groups.push({ label: "等待 Host", lines: [
        { text: `请求类型 ${span.requestKind ?? UNRECORDED}` },
        { text: outcomeLabel(span, outcome) },
        ...spanTimingLine(span, observedAtMs),
        ...lines(span.summary),
      ] });
    } else {
      groups.push({ label: "时间", lines: spanTimingLine(span, observedAtMs) });
      groups.push({ label: "配置", lines: [{ text: configurationLabel(span.configuration) }] });
      groups.push({ label: "结果", lines: resultLines(span, outcome) });
      const summary: CardLine[] = [
        ...lines(span.disposition ? `回合结论 ${span.disposition}` : null, span.error),
        ...(span.kind === "routing" && span.decisionTaskId ? [{ text: `内部路由计算 ${span.decisionTaskId}` }] : []),
      ];
      groups.push({ label: "摘要", lines: summary.length ? summary : [{ text: UNRECORDED, tone: "muted" }] });
    }
    return {
      key: selection.key,
      head: `${spanHead(span.kind)} · ${displayTitle(row.titleSource, row.title).text}`,
      groups,
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
    return {
      key: selection.key,
      head: `Host 事件：${event.label || event.kind}`,
      groups: [
        { label: "事件", lines: [{ text: clockTime(event.at) }, ...lines(event.actor ? `actor ${event.actor}` : null), ...lines(event.summary)] },
        { label: "记录标识", lines: recordIdentityLines(event).length ? recordIdentityLines(event) : [{ text: UNRECORDED, tone: "muted" }] },
        { label: "所属委派", lines: [{ text: row ? displayTitle(row.titleSource, row.title).text : `委派 ${event.runId}` }], link: row ? { kind: "run", runId: row.runId, label: displayTitle(row.titleSource, row.title).text } : undefined },
      ],
      openItem: item,
      relatedEvents: [],
    };
  }

  if (selection.key.startsWith("settle:")) {
    const runId = selection.key.slice(7);
    const row = rowsById.get(runId);
    if (!row) return null;
    return {
      key: selection.key,
      head: "结算旗标",
      groups: [
        { label: "验收", lines: [
          { text: row.acceptanceVerdict === "rejected" ? "验收问题" : row.acceptanceVerdict === "accepted" ? "已验收" : row.acceptanceVerdict ?? UNRECORDED },
          { text: row.acceptedAt ? clockTime(row.acceptedAt) : UNRECORDED, tone: "muted" },
        ] },
        { label: "所属委派", lines: [{ text: displayTitle(row.titleSource, row.title).text }], link: { kind: "run", runId: row.runId, label: displayTitle(row.titleSource, row.title).text } },
      ],
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
    groups: rollupGroups(rollup, rowsById),
    openItem: itemsByKey.get(rowLabelItem(row).key) ?? item,
    relatedEvents: relatedEventsForRun(runId, timeline.events)
      .map(event => ({ item: itemsByKey.get(`event:${event.seq}`) ?? null, event }))
      .filter((entry): entry is RelatedEvent => entry.item !== null),
  };
}
