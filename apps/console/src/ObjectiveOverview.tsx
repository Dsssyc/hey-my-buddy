import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import type { ObjectiveSummary, ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";
import { Badge } from "./ui";
import { Popover } from "./Popover";
import { excerpt } from "./task-state";
import {
  TASK_SOURCE_NOTE, categoryTone, clockTime, displayTitle, durationText,
  objectiveProgressText, relativeTime, rowStateInfo,
} from "./objective-display";
import { objectiveMetrics, rootRollups, type RunRollup } from "./objective-metrics";

/** The single hierarchy explanation (0.16 T2): one sentence, no list, no heading. */
export const HIERARCHY_HELP_TEXT =
  "工作目标是一项议程；委派是交给 Buddy、单独验收的一项工作；协助任务是委派派生的子工作；回合是一次执行。";

/**
 * The detail overview's two rows (0.16 T4): 目标摘要 and the Worker's own
 * result line. The intent title is the detail heading's job and never repeats
 * here.
 */
export function DelegationDetailRows({ taskSummary, resultSummary }: {
  taskSummary: string | null | undefined;
  resultSummary: string | null | undefined;
}) {
  return <div className="card-rows detail-rows">
    <span className="card-row">
      <span className="card-row-label">目标摘要</span>
      <span className="card-row-text" title={taskSummary ?? undefined}>{taskSummary ?? "未记录任务原文"}</span>
    </span>
    <span className="card-row">
      <span className="card-row-label">结果</span>
      <span className="card-row-text" title={resultSummary ?? undefined}>{resultSummary ? `结果：${resultSummary}` : "结果：暂无"}</span>
      {resultSummary && <span className="worker-note">Worker 自述</span>}
    </span>
  </div>;
}

/**
 * The delegation card (0.16 T3): a bold two-line intent title, the status at
 * the top right, one single-line result with a small Worker 自述 label, and a
 * weak meta row only when it has content. The card's minimum height covers two
 * title lines plus the result line, so one-line titles never make the strip
 * jump. No visible index; screen readers get “第 n 个委派，共 m 个”.
 */
function DelegationCard({ rollup, index, total, selected, tabbable, onSelect, onOpen }: {
  rollup: RunRollup; index: number; total: number; selected: boolean; tabbable: boolean;
  onSelect: (runId: string) => void; onOpen: (runId: string) => void;
}) {
  const state = rowStateInfo(rollup.row);
  const title = displayTitle(rollup.row.titleSource, rollup.row.title);
  const summary = rollup.row.summary;
  const meta: string[] = [];
  if (title.fromTask) meta.push(TASK_SOURCE_NOTE);
  if (rollup.helpers.length) meta.push(`协助 ${rollup.helpers.length}`);
  return <button type="button" className={"delegation-card" + (selected ? " selected" : "")}
    aria-pressed={selected} tabIndex={tabbable ? 0 : -1}
    aria-label={`第 ${index + 1} 个委派，共 ${total} 个：${title.text}，${state.label}（单击选中，双击打开详情）`}
    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); onOpen(rollup.row.runId); } }}
    title={title.fromTask ? `${title.text}（完整任务见详情）` : title.text}
    onClick={() => onSelect(rollup.row.runId)}
    onDoubleClick={() => onOpen(rollup.row.runId)}>
    <span className="delegation-card-top">
      <span className="delegation-card-title">{title.text}</span>
      <span className="delegation-card-state">
        <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
        {state.label}
      </span>
    </span>
    <span className="delegation-card-result">
      <span className="delegation-card-result-text" title={summary ?? undefined}>{summary ? `结果：${summary}` : "结果：暂无"}</span>
      {summary && <span className="worker-note">Worker 自述</span>}
    </span>
    {meta.length > 0 && <span className="delegation-card-meta">{meta.join(" · ")}</span>}
  </button>;
}

/**
 * The single-root objective keeps one compact card: the same T3 layout plus an
 * explicit 打开详情 action below the status. The full fact set lives in the
 * delegation detail and the pinned inspector, not here.
 */
function SingleDelegationCard({ rollup, onOpen }: {
  rollup: RunRollup; onOpen: (runId: string) => void;
}) {
  const state = rowStateInfo(rollup.row);
  const title = displayTitle(rollup.row.titleSource, rollup.row.title);
  const summary = rollup.row.summary;
  const meta: string[] = [];
  if (title.fromTask) meta.push(TASK_SOURCE_NOTE);
  if (rollup.helpers.length) meta.push(`协助 ${rollup.helpers.length}`);
  return <div className="delegation-card single expanded">
    <div className="delegation-card-top">
      <span className="delegation-card-title">{title.text}</span>
      <span className="delegation-card-state">
        <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
        {state.label}
        <button type="button" className="button small-button" onClick={() => onOpen(rollup.row.runId)}>打开详情</button>
      </span>
    </div>
    <div className="delegation-card-result">
      <span className="delegation-card-result-text" title={summary ?? undefined}>{summary ? `结果：${summary}` : "结果：暂无"}</span>
      {summary && <span className="worker-note">Worker 自述</span>}
    </div>
    {meta.length > 0 && <div className="delegation-card-meta">{meta.join(" · ")}</div>}
  </div>;
}

/**
 * The collapsible delegation-card band (0.16 P2.1): “▾ 委派（n）” toggles the
 * whole strip; collapsed it is one 28px row. Cards scroll horizontally only.
 */
export function DelegationStrip({ timeline, selectedRunId, collapsed, onToggleCollapsed, onSelectRun, onOpenRun }: {
  timeline: ObjectiveTimelineData;
  selectedRunId: string | null;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelectRun: (runId: string) => void;
  onOpenRun: (runId: string) => void;
}) {
  const roots = useMemo(() => rootRollups(timeline), [timeline]);
  const [cardFocus, setCardFocus] = useState(0);
  const stripRoot = useRef<HTMLDivElement>(null);
  if (!roots.length) return null;
  const stripId = "delegation-strip-region";
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
  return <div className={"delegation-band" + (collapsed ? " collapsed" : "")}>
    <button type="button" className="delegation-band-toggle" aria-expanded={!collapsed} aria-controls={stripId}
      onClick={onToggleCollapsed}>
      <span aria-hidden="true">{collapsed ? "▸" : "▾"}</span> 委派（{roots.length}）
    </button>
    {!collapsed && (roots.length === 1
      ? <div className="delegation-strip single-root"><SingleDelegationCard rollup={roots[0]!} onOpen={onOpenRun} /></div>
      : <div className="delegation-strip" id={stripId} ref={stripRoot} role="group"
        aria-label="委派卡（按创建顺序）" onKeyDown={onStripKeyDown}>
        {roots.map((rollup, index) => <DelegationCard key={rollup.row.runId} rollup={rollup} index={index} total={roots.length}
          selected={selectedRunId === rollup.row.runId} tabbable={index === Math.min(cardFocus, roots.length - 1)}
          onSelect={onSelectRun} onOpen={onOpenRun} />)}
        {(timeline.truncated.rows || timeline.filtered) && <span className="muted strip-bound">
          显示 {timeline.rows.filter(row => row.parentRunId === null).length} / {timeline.totals.rows} 个委派{timeline.truncated.rows ? " · 已截断" : ""}
        </span>}
      </div>)}
  </div>;
}

/**
 * The overview header (0.16 P2.1): title/description, the one status line
 * (status badge · acceptance progress · latest activity · the single hierarchy
 * “?” entry) and the always-collapsed 时间统计 disclosure. Durations stay plain
 * sentences with their honest caveats.
 */
export function ObjectiveOverview({ summary, timeline, loading, stale, onBackToList, headerActions, compact, hidden }: {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimelineData | null;
  loading: boolean;
  stale: boolean;
  onBackToList: () => void;
  /** Objective-level actions (停止目标 and its honest status). */
  headerActions?: ReactNode;
  /** Compact form at viewport heights of 800px or less (P2.1). */
  compact?: boolean;
  /** The timeline view's hidden state: transient popovers close (objectives.md). */
  hidden?: boolean;
}) {
  const shown = timeline?.objective ?? summary;
  const [helpOpen, setHelpOpen] = useState(false);
  const helpButton = useRef<HTMLButtonElement>(null);
  const metrics = useMemo(() => (timeline ? objectiveMetrics(timeline) : null), [timeline]);
  // A transient popover closes when its view hides or switches away.
  useEffect(() => { if (hidden) setHelpOpen(false); }, [hidden]);

  if (!shown) return null;
  const title = displayTitle(shown.titleSource, shown.title);
  const titleAttr = title.fromTask
    ? `${title.text}（完整任务见详情）`
    : shown.title;
  const cumulative = metrics && metrics.sumMs !== null && metrics.unionMs !== null && metrics.sumMs !== metrics.unionMs
    ? metrics.sumMs : null;
  const helpId = "hierarchy-help-popover";

  return <header className={"detail-header tl-head" + (compact ? " compact" : "")}>
    <button type="button" className="button small-button narrow-back" onClick={onBackToList}>‹ 工作目标列表</button>
    <div className="objective-title-row">
      <h2 className="one-line-title" title={titleAttr}>{excerpt(title.text, 100)}</h2>
      <span className="chip-row">
        {shown.kind === "standalone" && <Badge tone="neutral">历史独立委派</Badge>}
        {headerActions}
      </span>
    </div>
    {shown.description && <p className="objective-description" title={compact ? shown.description : undefined}>{shown.description}</p>}
    <p className="objective-vitals">
      <Badge tone={categoryTone(shown.state)}>{objectiveProgressText(shown)}</Badge>
      <span title={shown.lastActivityAt}>最近活动 {clockTime(shown.lastActivityAt)}（{relativeTime(shown.lastActivityAt, Date.now())}）</span>
      <span className="hierarchy-help-entry">
        <button ref={helpButton} type="button" id="hierarchy-help-button" className="icon-button help-button"
          aria-label="层级说明" title="层级说明" aria-expanded={helpOpen} aria-controls={helpId}
          onClick={() => setHelpOpen(current => !current)}>?</button>
        {helpOpen && <Popover anchor={helpButton.current} label="层级说明" width="min(38em, calc(100vw - 16px))"
          onClose={() => setHelpOpen(false)}>
          <p className="hierarchy-help-text">{HIERARCHY_HELP_TEXT}</p>
        </Popover>}
      </span>
    </p>
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
  </header>;
}
