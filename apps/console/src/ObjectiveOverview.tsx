import { useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import type { ObjectiveSummary, ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";
import type { TimelineRow } from "./objective-types";
import { Badge } from "./ui";
import { excerpt } from "./task-state";
import {
  CATEGORY_LABEL, COUNT_ORDER, categoryTone, clockSeconds, clockTime, durationText, rowStateInfo, totalDelegations,
} from "./objective-display";
import { buildInspectorCard } from "./inspector-card";
import { latestExecutionResult, objectiveMetrics, rootRollups, type RunRollup } from "./objective-metrics";

/** One root delegation's honest one-line rollup: result, acceptance, pending. */
function rollupFacts(rollup: RunRollup): { result: string; acceptance: string; pending: number; rounds: number } {
  const latest = latestExecutionResult(rollup);
  const result = latest ? latest.label : "未执行";
  const acceptance = rollup.row.acceptanceVerdict === "rejected" ? "验收问题"
    : rollup.row.acceptanceVerdict === "accepted" ? "已验收"
      : rollup.row.category === "review" ? "待验收" : "—";
  return { result, acceptance, pending: rollup.pending.length, rounds: rollup.executions.length };
}

function DelegationCard({ rollup, index, selected, tabbable, onSelect, onOpen }: {
  rollup: RunRollup; index: number; selected: boolean; tabbable: boolean;
  onSelect: (runId: string) => void; onOpen: (runId: string) => void;
}) {
  const state = rowStateInfo(rollup.row);
  const facts = rollupFacts(rollup);
  const execution = rollup.executionMs !== null ? durationText(rollup.executionMs) : null;
  return <button type="button" className={"delegation-card" + (selected ? " selected" : "")}
    aria-pressed={selected} tabIndex={tabbable ? 0 : -1}
    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(rollup.row.runId); } }}
    title={`${rollup.row.title}（单击选中，双击打开详情）`}
    onClick={() => onSelect(rollup.row.runId)}
    onDoubleClick={() => onOpen(rollup.row.runId)}>
    <span className="delegation-card-head">
      <span className="muted">#{index + 1}</span>
      <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
      <span className="delegation-card-title">{excerpt(rollup.row.title, 80)}</span>
    </span>
    <span className="delegation-card-facts">
      结果 {facts.result} · 验收 {facts.acceptance} · 待决 {facts.pending}
      {rollup.helpers.length ? ` · 含 ${rollup.helpers.length} 个协助` : ""}
    </span>
    <span className="delegation-card-meta muted">
      执行 {execution ?? "未记录"} · {facts.rounds} 轮{rollup.limitations.length ? " · 已记录部分" : ""}
    </span>
  </button>;
}

function SingleDelegationCard({ rollup, timeline, onOpen }: {
  rollup: RunRollup; timeline: ObjectiveTimelineData; onOpen: (runId: string) => void;
}) {
  const card = useMemo(() => buildInspectorCard({ type: "run", runId: rollup.row.runId }, timeline, new Map()), [rollup.row.runId, timeline]);
  const execution = rollup.executionMs !== null ? durationText(rollup.executionMs) : null;
  if (!card) return null;
  return <div className="delegation-card single expanded">
    <div className="inspector-card-head">
      <strong>{excerpt(rollup.row.title, 100)}</strong>
      <button type="button" className="button small-button" onClick={() => onOpen(rollup.row.runId)}>打开详情</button>
    </div>
    <p className="delegation-metrics">执行占用 {execution ?? "未记录"}{rollup.executions.length ? ` · ${rollup.executions.length} 轮` : ""}{rollup.limitations.length ? " · 已记录部分" : ""}</p>
    {card.groups.map(group => <div key={group.label} className="inspector-group">
      <span className="inspector-group-label">{group.label}</span>
      <span className="inspector-group-lines">
        {group.lines.map((line, index) => <span key={index} className={line.tone ? `tone-${line.tone}` : undefined}>{line.text}</span>)}
        {group.link && group.link.kind === "run" && <span className="muted">{group.link.label}</span>}
      </span>
    </div>)}
  </div>;
}

/**
 * The overview header of the timeline view (0.15 C5/C6): recorded facts, the
 * three distinct duration numbers, root delegation cards in creation order —
 * or one expanded card for a single root. Everything derives from the bounded
 * read; missing facts say 未记录 / 不完整 and are never repaired.
 */
export function ObjectiveOverview({ summary, timeline, loading, stale, selectedRunId, onSelectRun, onOpenRun, onBackToList }: {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimelineData | null;
  loading: boolean;
  stale: boolean;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onOpenRun: (runId: string) => void;
  onBackToList: () => void;
}) {
  const shown = timeline?.objective ?? summary;
  const roots = useMemo(() => (timeline ? rootRollups(timeline) : []), [timeline]);
  const metrics = useMemo(() => (timeline ? objectiveMetrics(timeline) : null), [timeline]);
  const [cardFocus, setCardFocus] = useState(0);
  const stripRoot = useRef<HTMLDivElement>(null);

  function onStripKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!roots.length) return;
    let next = cardFocus;
    if (event.key === "ArrowRight") next = Math.min(cardFocus + 1, roots.length - 1);
    else if (event.key === "ArrowLeft") next = Math.max(cardFocus - 1, 0);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = roots.length - 1;
    else return;
    event.preventDefault();
    setCardFocus(next);
    const cards = stripRoot.current?.querySelectorAll<HTMLButtonElement>(".delegation-card");
    cards?.[next]?.focus();
  }

  if (!shown) return null;
  const limitationTitle = metrics?.limitations.map(entry => entry.label).join("；") ?? "";
  const recordedPart = metrics && metrics.limitations.length > 0;
  const cumulative = metrics && metrics.unionMs !== null && metrics.sumMs !== null && metrics.sumMs !== metrics.unionMs
    ? metrics.sumMs : null;

  return <header className="detail-header tl-head">
    <div className="row-between">
      <button type="button" className="button small-button narrow-back" onClick={onBackToList}>返回工作目标</button>
      <span className="small muted truncate" title={shown.project.path || shown.project.id}>{shown.project.label}</span>
      <span className="chip-row">
        {shown.kind === "standalone" && <Badge tone="neutral">未归档委派</Badge>}
        <Badge tone={categoryTone(shown.state)}>{CATEGORY_LABEL[shown.state]}</Badge>
      </span>
    </div>
    <h2 title={shown.title}>{excerpt(shown.title, 100)}</h2>
    {shown.summary && <p className="objective-summary-line" title={shown.summary}>结果：{excerpt(shown.summary, 120)}</p>}
    <p className="assignment-line">
      <span>来源 Host：{shown.sourceHostId || "未记录"}</span>
      <span>当前 Host：{shown.currentHostIds.length ? shown.currentHostIds.join("、") : "未记录"}</span>
      <span>共 {totalDelegations(shown.counts)} 个委派{shown.counts.helpers ? `（含 ${shown.counts.helpers} 个协助任务）` : ""}</span>
      <span>最近活动 {clockSeconds(shown.lastActivityAt)}</span>
    </p>
    <span className="count-line">
      {COUNT_ORDER.filter(entry => shown.counts[entry.key] > 0).map(entry =>
        <span key={entry.key} className={`cnt cnt-${entry.key}`}><span aria-hidden="true">{entry.glyph}</span>{entry.label} {shown.counts[entry.key]}</span>)}
      <span className="muted">共 {totalDelegations(shown.counts)} 个委派</span>
    </span>
    <p className="metric-line">
      {!timeline
        ? loading ? <span className="muted">跨度与执行时长：读取中</span> : <span className="muted">跨度与执行时长：未记录</span>
        : <>
          <span>总跨度 {metrics?.totalSpanMs !== null && metrics?.totalSpanMs !== undefined ? durationText(metrics.totalSpanMs) ?? "未记录" : "未记录"}</span>
          <span>执行占用 {metrics?.unionMs != null ? durationText(metrics.unionMs) : "未记录"}</span>
          {cumulative !== null && <span>累计 {durationText(cumulative)}（{metrics!.segmentCount} 段，含并发）</span>}
          {metrics!.runningCount > 0 && <span className="muted">含进行中 {metrics!.runningCount} 段（计至读取时刻）</span>}
          {metrics!.unconfirmedEndCount > 0 && <span className="muted">含 {metrics!.unconfirmedEndCount} 段结束未确认，尾段未计</span>}
          {recordedPart && <span className="metric-warn" title={limitationTitle} tabIndex={0}>不完整（已记录部分）</span>}
          {stale && <span className="muted">按 {clockSeconds(timeline.observedAt)} 的数据</span>}
        </>}
    </p>
    {timeline && roots.length >= 2 && <div className="delegation-strip" ref={stripRoot} role="group"
      aria-label="根委派卡（按创建顺序）" onKeyDown={onStripKeyDown}>
      {roots.map((rollup, index) => <DelegationCard key={rollup.row.runId} rollup={rollup} index={index}
        selected={selectedRunId === rollup.row.runId} tabbable={index === Math.min(cardFocus, roots.length - 1)}
        onSelect={onSelectRun} onOpen={onOpenRun} />)}
      {(timeline.truncated.rows || timeline.filtered) && <span className="muted strip-bound">
        显示 {timeline.rows.filter((row: TimelineRow) => row.parentRunId === null).length} / {timeline.totals.rows} 个委派{timeline.truncated.rows ? " · 已截断" : ""}
      </span>}
    </div>}
    {timeline && roots.length === 1 && <SingleDelegationCard rollup={roots[0]!} timeline={timeline} onOpen={onOpenRun} />}
    <p className="tl-note">{shown.kind === "standalone"
      ? "这条委派提交时没有指定工作目标，按记录单独显示，不与其他记录合并。"
      : "工作目标只用于归档与浏览，不调度任务，也不作为验收条件。"}</p>
  </header>;
}
