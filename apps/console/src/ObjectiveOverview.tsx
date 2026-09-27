import { useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import type { ObjectiveSummary, ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";
import { Badge } from "./ui";
import { excerpt } from "./task-state";
import {
  CATEGORY_LABEL, TASK_SOURCE_NOTE, categoryTone, clockTime, displayTitle, durationText,
  relativeTime, rowStateInfo,
} from "./objective-display";
import { objectiveMetrics, rootRollups, type RunRollup } from "./objective-metrics";

/** One labeled single-line row of an overview card (0.15.1 U2). */
function CardRow({ label, text, title, note }: { label: string; text: string; title?: string; note?: string }) {
  return <span className="card-row">
    <span className="card-row-label">{label}</span>
    <span className="card-row-text" title={title}>{text}</span>
    {note && <span className="title-source-note">{note}</span>}
  </span>;
}

/**
 * The fixed three-row delegation overview (design §4): 做什么 / 目标摘要 /
 * 结果摘要. Rows always render — missing facts use their placeholders — so
 * cards never jump. The 目标摘要 carries the 任务开头 prefix and the 结果摘要
 * the Worker 自述，非验收 suffix; both stay single lines.
 */
export function DelegationThreeRows({ title, titleSource, taskSummary, resultSummary }: {
  title: string | null | undefined;
  titleSource: string | null | undefined;
  taskSummary: string | null | undefined;
  resultSummary: string | null | undefined;
}) {
  const titleLine = displayTitle(titleSource ?? "none", title || "未命名委派");
  return <div className="card-rows three-rows">
    <CardRow label="做什么" text={titleLine.text} note={titleLine.fromTask ? TASK_SOURCE_NOTE : undefined} />
    <CardRow label="目标摘要" text={taskSummary ?? "未记录任务原文"} />
    <CardRow label="结果摘要" text={(resultSummary ?? "尚无 Worker 结论") + (resultSummary ? "（Worker 自述，非验收）" : "")} />
  </div>;
}

function DelegationCard({ rollup, index, selected, tabbable, onSelect, onOpen }: {
  rollup: RunRollup; index: number; selected: boolean; tabbable: boolean;
  onSelect: (runId: string) => void; onOpen: (runId: string) => void;
}) {
  const state = rowStateInfo(rollup.row);
  return <button type="button" className={"delegation-card" + (selected ? " selected" : "")}
    aria-pressed={selected} tabIndex={tabbable ? 0 : -1}
    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(rollup.row.runId); } }}
    title={`#${index + 1} ${displayTitle(rollup.row.titleSource, rollup.row.title).text}（单击选中，双击打开详情）`}
    onClick={() => onSelect(rollup.row.runId)}
    onDoubleClick={() => onOpen(rollup.row.runId)}>
    <span className="delegation-card-head">
      <span className="muted">#{index + 1}</span>
      <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
      <span className="delegation-state muted">{state.label}</span>
      {rollup.helpers.length ? <span className="muted">协助 {rollup.helpers.length}</span> : null}
    </span>
    <DelegationThreeRows title={rollup.row.title} titleSource={rollup.row.titleSource}
      taskSummary={rollup.row.taskSummary} resultSummary={rollup.row.summary} />
  </button>;
}

/**
 * The single-root objective keeps one compact card: its status, the explicit
 * open action and the fixed three-row overview. The full fact set lives in
 * the delegation detail and the pinned inspector, not here (Host review 4).
 */
function SingleDelegationCard({ rollup, onOpen }: {
  rollup: RunRollup; onOpen: (runId: string) => void;
}) {
  const state = rowStateInfo(rollup.row);
  return <div className="delegation-card single expanded">
    <div className="inspector-card-head">
      <span className="chip-row">
        <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
        <span className="delegation-state muted">{state.label}</span>
      </span>
      <button type="button" className="button small-button" onClick={() => onOpen(rollup.row.runId)}>打开详情</button>
    </div>
    <DelegationThreeRows title={rollup.row.title} titleSource={rollup.row.titleSource}
      taskSummary={rollup.row.taskSummary} resultSummary={rollup.row.summary} />
  </div>;
}

/**
 * The overview header of the timeline view (design §3): beneath a one-line
 * title and the optional description it shows only the status badge, accepted
 * progress (x = counts.accepted accepted delegations of y = counts.roots) and
 * latest activity, plus the hierarchy note. The durations live in a collapsed
 * 时间统计 disclosure written as plain sentences with their honest caveats.
 */
export function ObjectiveOverview({ summary, timeline, loading, stale, selectedRunId, onSelectRun, onOpenRun, onBackToList, headerActions }: {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimelineData | null;
  loading: boolean;
  stale: boolean;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  onOpenRun: (runId: string) => void;
  onBackToList: () => void;
  /** Objective-level actions (停止目标 and its honest status). */
  headerActions?: ReactNode;
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
  const title = displayTitle(shown.titleSource, shown.title);
  const titleAttr = title.fromTask
    ? `${title.text}（完整任务见详情）`
    : shown.title;
  const cumulative = metrics && metrics.sumMs !== null && metrics.unionMs !== null && metrics.sumMs !== metrics.unionMs
    ? metrics.sumMs : null;

  return <header className="detail-header tl-head">
    <button type="button" className="button small-button narrow-back" onClick={onBackToList}>‹ 工作目标列表</button>
    <div className="objective-title-row">
      <h2 className="one-line-title" title={titleAttr}>{excerpt(title.text, 100)}</h2>
      <span className="chip-row">
        {shown.kind === "standalone" && <Badge tone="neutral">未归档委派</Badge>}
        {headerActions}
      </span>
    </div>
    {shown.description && <p className="objective-description">{shown.description}</p>}
    <p className="objective-vitals">
      <Badge tone={categoryTone(shown.state)}>{CATEGORY_LABEL[shown.state]}</Badge>
      <span>{shown.counts.accepted} / {shown.counts.roots} 个委派已验收</span>
      <span>最近活动 {clockTime(shown.lastActivityAt)}（{relativeTime(shown.lastActivityAt, Date.now())}）</span>
    </p>
    <div className="hierarchy-line">
      工作目标 › 委派 › 协助任务 › 回合
      <details className="hierarchy-help">
        <summary aria-label="层级说明">?</summary>
        <span className="hierarchy-help-body">
          工作目标：用户的一项议程，只用于归档与浏览，本身不执行、不验收。<br />
          委派：Host 为该议程提交的一项工作，单独验收。<br />
          协助任务：委派执行中派生的子工作，由所属委派整合，不单独验收。<br />
          回合：委派或协助任务的一次执行；接续会增加回合。
        </span>
      </details>
    </div>
    <details className="time-stats">
      <summary>时间统计</summary>
      <div className="time-stats-body">
        {!timeline
          ? <p className="small muted">{loading ? "时间统计：读取中" : "时间统计：未记录"}</p>
          : <>
            <p className="time-stat">从开始到最近一次活动：{metrics?.totalSpanMs !== null && metrics?.totalSpanMs !== undefined ? durationText(metrics.totalSpanMs) ?? "未记录" : "未记录"}</p>
            <p className="time-stat">其间至少有一项在运行的时间：{metrics?.unionMs != null ? durationText(metrics.unionMs) ?? "未记录" : "未记录"}</p>
            {cumulative !== null && <p className="time-stat">各回合运行时间相加：{durationText(cumulative)}（有回合同时运行，所以更长）</p>}
            {metrics!.runningCount > 0 && <p className="small muted">还有 {metrics!.runningCount} 个回合在运行，时间算到 {clockTime(timeline.observedAt)}。</p>}
            {metrics!.unknownEndCount > 0 && <p className="small muted">有 {metrics!.unknownEndCount} 个回合没确认何时结束，没有计入。</p>}
            {metrics!.limitations.length > 0 && <p className="small metric-warn" tabIndex={0}
              title={metrics!.limitations.map(entry => entry.label).join("；")}>只统计了已读取到的记录：{metrics!.limitations.map(entry => entry.label).join("；")}</p>}
            {stale && <p className="small muted">按 {timeline.observedAt} 的数据。</p>}
          </>}
        <p className="small muted record-source">记录来源：项目 {shown.project.label}{shown.project.path ? `（${shown.project.path}）` : ""} · 来源 Host {shown.sourceHostId || "未记录"} · 当前 Host {shown.currentHostIds.length ? shown.currentHostIds.join("、") : "未记录"}</p>
      </div>
    </details>
    {timeline && roots.length >= 2 && <div className="delegation-strip" ref={stripRoot} role="group"
      aria-label="委派卡（按创建顺序）" onKeyDown={onStripKeyDown}>
      {roots.map((rollup, index) => <DelegationCard key={rollup.row.runId} rollup={rollup} index={index}
        selected={selectedRunId === rollup.row.runId} tabbable={index === Math.min(cardFocus, roots.length - 1)}
        onSelect={onSelectRun} onOpen={onOpenRun} />)}
      {(timeline.truncated.rows || timeline.filtered) && <span className="muted strip-bound">
        显示 {timeline.rows.filter(row => row.parentRunId === null).length} / {timeline.totals.rows} 个委派{timeline.truncated.rows ? " · 已截断" : ""}
      </span>}
    </div>}
    {timeline && roots.length === 1 && <SingleDelegationCard rollup={roots[0]!} onOpen={onOpenRun} />}
  </header>;
}
