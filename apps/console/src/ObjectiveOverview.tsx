import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import type { ObjectiveSummary, ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";
import { Badge } from "./ui";
import { excerpt } from "./task-state";
import {
  TASK_SOURCE_NOTE, categoryTone, clockTime, displayTitle, durationText,
  objectiveProgressText, relativeTime, rowStateInfo, titleLineTooltip,
} from "./objective-display";
import { objectiveMetrics, rootRollups, type RunRollup } from "./objective-metrics";

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
      <span className="card-row-text" title={resultSummary ?? undefined}>{resultSummary ? `结果：${resultSummary}` : "结果未知"}</span>
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
    title={titleLineTooltip(title)}
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
      <span className="delegation-card-result-text" title={summary ?? undefined}>{summary ? `结果：${summary}` : "结果未知"}</span>
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
      <span className="delegation-card-result-text" title={summary ?? undefined}>{summary ? `结果：${summary}` : "结果未知"}</span>
      {summary && <span className="worker-note">Worker 自述</span>}
    </div>
    {meta.length > 0 && <div className="delegation-card-meta">{meta.join(" · ")}</div>}
  </div>;
}

/**
 * The collapsible delegation-card band (0.16 P2.1): “▾ 委派（n）” toggles the
 * whole strip; collapsed it is one 28px row. Cards wrap into at most two rows
 * until the user reveals the full set.
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
  const [stripWidth, setStripWidth] = useState(800);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    const strip = stripRoot.current;
    if (!strip || typeof ResizeObserver === "undefined") return;
    const measure = () => { if (strip.clientWidth > 0) setStripWidth(strip.clientWidth); };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(strip);
    return () => observer.disconnect();
  }, [collapsed, timeline.objective.objectiveId]);
  useEffect(() => setShowAll(false), [timeline.objective.objectiveId]);
  if (!roots.length) return null;
  const stripId = "delegation-strip-region";
  // The strip has 20px inline padding on each side and 8px between cards.
  const cardsPerRow = Math.max(1, Math.floor((stripWidth - 40 + 8) / 270));
  const firstTwoRows = cardsPerRow * 2;
  const selectedIndex = roots.findIndex(rollup => rollup.row.runId === selectedRunId);
  const visibleCount = showAll || selectedIndex >= firstTwoRows ? roots.length : Math.min(roots.length, firstTwoRows);
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
    if (next >= visibleCount) {
      setShowAll(true);
      requestAnimationFrame(() => stripRoot.current?.querySelectorAll<HTMLButtonElement>(".delegation-card")[next]?.focus());
    } else stripRoot.current?.querySelectorAll<HTMLButtonElement>(".delegation-card")[next]?.focus();
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
        {roots.slice(0, visibleCount).map((rollup, index) => <DelegationCard key={rollup.row.runId} rollup={rollup} index={index} total={roots.length}
          selected={selectedRunId === rollup.row.runId} tabbable={index === Math.min(cardFocus, visibleCount - 1)}
          onSelect={onSelectRun} onOpen={onOpenRun} />)}
        {visibleCount < roots.length && <button type="button" className="button small-button delegation-show-all"
          onClick={() => setShowAll(true)}>展开全部 {roots.length} 个</button>}
        {(timeline.truncated.rows || timeline.filtered) && <span className="muted strip-bound">
          显示 {timeline.rows.filter(row => row.parentRunId === null).length} / {timeline.totals.rows} 个委派{timeline.truncated.rows ? " · 已截断" : ""}
        </span>}
      </div>)}
  </div>;
}

/**
 * The overview header (0.16 P2.1): title/description, the one status line
 * (status badge · acceptance progress · latest activity) and the
 * always-collapsed 时间统计 disclosure. Durations stay plain
 * sentences with their honest caveats.
 */
export function ObjectiveOverview({ summary, timeline, loading, stale, onBackToList, headerActions, compact }: {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimelineData | null;
  loading: boolean;
  stale: boolean;
  onBackToList: () => void;
  /** Objective-level actions (停止目标 and its honest status). */
  headerActions?: ReactNode;
  /** Compact form at viewport heights of 800px or less (P2.1). */
  compact?: boolean;
}) {
  const shown = timeline?.objective ?? summary;
  const metrics = useMemo(() => (timeline ? objectiveMetrics(timeline) : null), [timeline]);

  if (!shown) return null;
  const title = displayTitle(shown.titleSource, shown.title);
  const titleAttr = titleLineTooltip(title);
  const cumulative = metrics && metrics.sumMs !== null && metrics.unionMs !== null && metrics.sumMs !== metrics.unionMs
    ? metrics.sumMs : null;

  return <header className={"detail-header tl-head" + (compact ? " compact" : "")}>
    <button type="button" className="button small-button narrow-back" onClick={onBackToList}>‹ 工作目标列表</button>
    <div className="objective-title-row">
      <h2 className="one-line-title" title={titleAttr}>{excerpt(title.text, 100)}</h2>
      {shown.description && <span className="objective-description" title={shown.description}>{shown.description}</span>}
      <span className="chip-row">
        {shown.kind === "standalone" && <Badge tone="neutral">历史独立委派</Badge>}
        {headerActions}
      </span>
    </div>
    <div className="objective-vitals">
      <Badge tone={categoryTone(shown.state)}>{objectiveProgressText(shown)}</Badge>
      <span title={shown.lastActivityAt}>最近活动 {clockTime(shown.lastActivityAt)}（{relativeTime(shown.lastActivityAt, Date.now())}）</span>
    <details className="time-stats">
      <summary>时间统计</summary>
      <div className="time-stats-body">
        {!timeline
          ? <p className="small muted">{loading ? "时间统计：读取中" : "时间统计：未记录"}</p>
          : <>
            <p className="time-stat">从开始到最近一次活动：{metrics?.totalSpanMs !== null && metrics?.totalSpanMs !== undefined ? durationText(metrics.totalSpanMs) ?? "未记录" : "未记录"}</p>
            <p className="time-stat">其间至少有一项在运行的时间：{metrics?.unionMs != null ? durationText(metrics.unionMs) ?? "未记录" : "未记录"}</p>
            {cumulative !== null && <p className="time-stat">回合累计：{durationText(cumulative)}</p>}
            {metrics!.runningCount > 0 && <p className="small muted">运行中 {metrics!.runningCount} 回合 · 截至 {clockTime(timeline.observedAt)}</p>}
            {metrics!.unknownEndCount > 0 && <p className="small muted">结束未确认 {metrics!.unknownEndCount} 回合 · 未计入</p>}
            {metrics!.limitations.length > 0 && <p className="small metric-warn" tabIndex={0}
              title={metrics!.limitations.map(entry => entry.label).join("；")}>统计范围不完整：{metrics!.limitations.map(entry => entry.label).join("；")}</p>}
            {stale && <p className="small muted">数据截至 {timeline.observedAt}</p>}
          </>}
        <p className="small muted record-source">来源 Host {shown.sourceHostId || "未记录"} · 当前 Host {shown.currentHostIds.length ? shown.currentHostIds.join("、") : "未记录"}</p>
      </div>
    </details>
    </div>
  </header>;
}
