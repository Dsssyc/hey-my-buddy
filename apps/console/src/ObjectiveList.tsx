import { useMemo, useRef, useState } from "react";
import type { ObjectiveFilter, ObjectiveSummary } from "./objective-types";
import { Badge, Empty } from "./ui";
import { excerpt } from "./task-state";
import {
  CATEGORY_LABEL, COUNT_ORDER, categoryTone, dayClock, relativeTime, totalDelegations,
} from "./objective-display";

export type ObjectiveListProps = {
  rows: ObjectiveSummary[];
  total: number;
  loading: boolean;
  error: string;
  nextCursor: string | null;
  reorder: { count: number | null } | null;
  filter: ObjectiveFilter;
  query: string;
  projectId: string;
  hostId: string;
  choices: { projects: { id: string; label: string; path: string | null }[]; hosts: string[] };
  selected: string | null;
  locked: boolean;
  onFilterChange: (filter: ObjectiveFilter) => void;
  onQueryChange: (query: string) => void;
  onProjectChange: (projectId: string) => void;
  onHostChange: (hostId: string) => void;
  onSelect: (objectiveId: string) => void;
  onRefresh: () => void;
  onRetry: () => void;
  onMore: () => void;
  onApplyReorder: () => void;
};

const LOCK_TITLE = "有结果未确认的操作：核对前不切换";

/**
 * Left column of the 委派记录 tab: work objectives grouped by source project,
 * ordered by latest activity. Reads only; the segmented filter matches member
 * delegations while counts stay overall.
 */
export function ObjectiveList(props: ObjectiveListProps) {
  const { rows, total, loading, error, nextCursor, reorder } = props;
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const scroll = useRef<HTMLDivElement>(null);
  const scrollIntent = useRef(false);
  const now = Date.now();
  const groups = useMemo(() => {
    const byProject = new Map<string, { project: ObjectiveSummary["project"]; runs: ObjectiveSummary[] }>();
    for (const row of rows) {
      if (!byProject.has(row.project.id)) byProject.set(row.project.id, { project: row.project, runs: [] });
      byProject.get(row.project.id)!.runs.push(row);
    }
    return [...byProject.values()];
  }, [rows]);
  const filtersIdle = props.filter === "all" && !props.query.trim() && !props.projectId && !props.hostId;
  function loadMore() {
    if (loading || !nextCursor || !scroll.current) return;
    scrollIntent.current = false;
    void props.onMore();
  }
  return <section className="panel list-panel" aria-label="工作目标列表">
    <div className="panel-toolbar"><h2>工作目标</h2><button className="button small-button" disabled={loading} onClick={props.onRefresh}>刷新记录</button></div>
    <div className="list-filters">
      <div className="segmented" aria-label="按委派状态筛选">{([["all", "全部"], ["active", "进行中"], ["host", "待决定"], ["review", "待验收"]] as const).map(([key, label]) =>
        <button key={key} aria-pressed={props.filter === key} onClick={() => props.onFilterChange(key)}>{label}</button>)}</div>
      <label className="search"><span className="sr-only">搜索工作目标</span><input value={props.query} maxLength={200}
        onChange={event => props.onQueryChange(event.target.value)} placeholder="工作目标、委派标题、项目或 ID" /></label>
      <div className="filter-pair"><label><span className="sr-only">目标项目筛选</span><select aria-label="目标项目筛选" value={props.projectId} onChange={event => props.onProjectChange(event.target.value)}><option value="">全部项目</option>
        {props.choices.projects.map(project => <option key={project.id} value={project.id}>{project.label} — {project.path || project.id}</option>)}</select></label>
        <label><span className="sr-only">目标委派方筛选</span><select aria-label="目标委派方筛选" value={props.hostId} onChange={event => props.onHostChange(event.target.value)}><option value="">全部委派方</option>{props.choices.hosts.map(id => <option key={id}>{id}</option>)}</select></label></div>
      <p className="small muted">已加载 {rows.length} / {total} 个工作目标 · 按最近活动排序 · 项目选项来自已加载记录</p>
    </div>
    {reorder && <button className="new-records" onClick={() => { props.onApplyReorder(); if (scroll.current) scroll.current.scrollTop = 0; }}>{reorder.count !== null
      ? `有 ${reorder.count} 个工作目标有新活动 · 按最近活动重新排序`
      : "有新活动 · 按最近活动重新排序"}</button>}
    {error && <div className="list-error" role="alert">{error}<button className="button small-button" onClick={props.onRetry}>重试读取</button></div>}
    <div ref={scroll} className="list-scroll" tabIndex={0} aria-label="工作目标条目"
      onWheel={() => { scrollIntent.current = true; }} onTouchMove={() => { scrollIntent.current = true; }}
      onKeyDown={event => { if (["ArrowDown", "PageDown", "End"].includes(event.key)) scrollIntent.current = true; }}
      onScroll={event => {
        const element = event.currentTarget;
        if (scrollIntent.current && element.scrollHeight - element.scrollTop - element.clientHeight < 140) loadMore();
      }}>
      {groups.map(group => <section key={group.project.id} className="project-group">
        <button className="group-heading" aria-expanded={!collapsed.has(group.project.id)} title={group.project.path || group.project.id} onClick={() => setCollapsed(all => {
          const next = new Set(all);
          if (next.has(group.project.id)) next.delete(group.project.id); else next.add(group.project.id);
          return next;
        })}><span>{collapsed.has(group.project.id) ? "▸" : "▾"} {group.project.label}</span>
          <span className="small">已加载 {group.runs.length} 个工作目标</span></button>
        {!collapsed.has(group.project.id) && <ul className="task-list">{group.runs.map(row => {
          const chosen = props.selected === row.objectiveId;
          return <li key={row.objectiveId} data-objective-id={row.objectiveId}>
            <button className={"task-row objective-row" + (chosen ? " selected" : "")} aria-pressed={chosen}
              disabled={props.locked && !chosen} title={props.locked && !chosen ? LOCK_TITLE : undefined}
              onClick={() => props.onSelect(row.objectiveId)}>
              <span className="row-between">
                <span className="chip-row">
                  <Badge tone={categoryTone(row.state)}>{CATEGORY_LABEL[row.state]}</Badge>
                  {row.kind === "standalone" && <Badge tone="neutral">独立委派</Badge>}
                </span>
                <time className="small muted" dateTime={row.lastActivityAt} title={dayClock(row.lastActivityAt)}>{relativeTime(row.lastActivityAt, now)}</time>
              </span>
              <strong className="task-title" title={row.title}>{excerpt(row.title, 100)}</strong>
              <span className="count-line">
                {COUNT_ORDER.filter(entry => row.counts[entry.key] > 0).map(entry =>
                  <span key={entry.key} className={`cnt cnt-${entry.key}`}><span aria-hidden="true">{entry.glyph}</span>{entry.label} {row.counts[entry.key]}</span>)}
                <span className="muted">共 {totalDelegations(row.counts)} 个委派</span>
              </span>
              <span className="small muted truncate" title={row.sourceHostId ?? undefined}>来源 Host：{row.sourceHostId || "未记录"}</span>
            </button>
          </li>;
        })}</ul>}
      </section>)}
      {!rows.length && !loading && !error && (filtersIdle
        ? <Empty title="还没有工作目标">Host 提交委派后，这里按项目列出工作目标；旧记录各自显示为独立委派。</Empty>
        : <Empty title="没有匹配的工作目标">调整筛选或搜索项目名称。</Empty>)}
      {loading && <p className="loading-row" role="status">正在读取工作目标…</p>}
      {nextCursor && <button className="load-more" disabled={loading} onClick={loadMore}>加载更早工作目标</button>}
    </div>
  </section>;
}
