import { useMemo, useRef, useState } from "react";
import type { ObjectiveFilter, ObjectiveSummary } from "./objective-types";
import { Badge, Empty } from "./ui";
import { excerpt } from "./task-state";
import {
  CATEGORY_LABEL, COUNT_ORDER, TASK_SOURCE_NOTE, categoryTone, dayClock, displayTitle, relativeTime, totalDelegations,
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
  /** Collapsed 48px rail while a detail is open above 760px (0.15.1 U1). */
  rail: boolean;
  /** The selected objective whose state icon the rail carries. */
  railState?: ObjectiveSummary | null;
  /** Focus target restored when the drawer closes. */
  railButtonRef?: React.RefObject<HTMLButtonElement | null>;
  onToggleRail?: () => void;
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

type ShowKind = "objectives" | "standalone" | "all";
const SHOW_OPTIONS: [ShowKind, string][] = [["objectives", "工作目标"], ["standalone", "未归档委派"], ["all", "全部"]];

function EntryRow({ row, selected, onSelect }: {
  row: ObjectiveSummary; selected: string | null; onSelect: (objectiveId: string) => void;
}) {
  const now = Date.now();
  const chosen = selected === row.objectiveId;
  const title = displayTitle(row.titleSource, row.title);
  return <li key={row.objectiveId} data-objective-id={row.objectiveId}>
    <button className={"task-row objective-row" + (chosen ? " selected" : "")} aria-pressed={chosen}
      onClick={() => onSelect(row.objectiveId)}>
      <span className="row-between">
        <span className="chip-row">
          <Badge tone={categoryTone(row.state)}>{CATEGORY_LABEL[row.state]}</Badge>
          {row.kind === "standalone" && <Badge tone="neutral">未归档委派</Badge>}
        </span>
        <time className="small muted" dateTime={row.lastActivityAt} title={dayClock(row.lastActivityAt)}>{relativeTime(row.lastActivityAt, now)}</time>
      </span>
      <strong className={"task-title" + (title.fromTask ? " single-line" : "")}
        title={title.fromTask ? `${title.text}（完整任务见详情）` : row.title}>
        {excerpt(title.text, 100)}
        {title.fromTask && <span className="title-source-note">{TASK_SOURCE_NOTE}</span>}
      </strong>
      {row.summary !== null && <span className="task-summary muted" title={row.summary}>结果：{excerpt(row.summary, 80)}</span>}
      <span className="count-line">
        {COUNT_ORDER.filter(entry => row.counts[entry.key] > 0).map(entry =>
          <span key={entry.key} className={`cnt cnt-${entry.key}`}><span aria-hidden="true">{entry.glyph}</span>{entry.label} {row.counts[entry.key]}</span>)}
        <span className="muted">共 {totalDelegations(row.counts)} 个委派</span>
      </span>
      <span className="small muted truncate" title={row.sourceHostId ?? undefined}>来源 Host：{row.sourceHostId || "未记录"}</span>
    </button>
  </li>;
}

function ProjectGroups({ rows, collapsed, toggle, selected, onSelect, headingNoun }: {
  rows: ObjectiveSummary[];
  collapsed: ReadonlySet<string>;
  toggle: (projectId: string) => void;
  selected: string | null;
  onSelect: (objectiveId: string) => void;
  headingNoun: string;
}) {
  const groups = useMemo(() => {
    const byProject = new Map<string, { project: ObjectiveSummary["project"]; runs: ObjectiveSummary[] }>();
    for (const row of rows) {
      if (!byProject.has(row.project.id)) byProject.set(row.project.id, { project: row.project, runs: [] });
      byProject.get(row.project.id)!.runs.push(row);
    }
    return [...byProject.values()];
  }, [rows]);
  return <>{groups.map(group => <section key={group.project.id} className="project-group">
    <button className="group-heading" aria-expanded={!collapsed.has(group.project.id)} title={group.project.path || group.project.id} onClick={() => toggle(group.project.id)}>
      <span>{collapsed.has(group.project.id) ? "▸" : "▾"} {group.project.label}</span>
      <span className="small">已加载 {group.runs.length} 个{headingNoun}</span>
    </button>
    {!collapsed.has(group.project.id) && <ul className="task-list">{group.runs.map(row =>
      <EntryRow key={row.objectiveId} row={row} selected={selected} onSelect={onSelect} />)}</ul>}
  </section>)}</>;
}

/**
 * Left column of the 委派记录 tab: work objectives grouped by source project,
 * ordered by latest activity. The default view lists `kind = objective` only;
 * unarchived standalone delegations fold into their own section whose count
 * covers only the loaded page (0.15 A4). Titles are the recorded intent with
 * the nullable summary as a second line (A5); a task first-line fallback is
 * one line of ~40 characters annotated 取自任务首行, without the full text in
 * its tooltip (0.15.1 U3). Reads only — and while a detail is open the column
 * can collapse into an expandable narrow rail (U1).
 */
export function ObjectiveList(props: ObjectiveListProps) {
  const { rows, total, loading, error, nextCursor, reorder } = props;
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [standaloneOpen, setStandaloneOpen] = useState(false);
  const [show, setShow] = useState<ShowKind>("objectives");
  const scroll = useRef<HTMLDivElement>(null);
  const scrollIntent = useRef(false);
  const objectives = useMemo(() => rows.filter(row => row.kind === "objective"), [rows]);
  const standalone = useMemo(() => rows.filter(row => row.kind === "standalone"), [rows]);
  const filtersIdle = props.filter === "all" && !props.query.trim() && !props.projectId && !props.hostId;
  function toggle(projectId: string) {
    setCollapsed(all => {
      const next = new Set(all);
      if (next.has(projectId)) next.delete(projectId); else next.add(projectId);
      return next;
    });
  }
  if (props.rail) {
    const state = props.railState ?? null;
    return <section className="panel list-panel rail-panel" aria-label="工作目标列表（已收起）">
      <button ref={props.railButtonRef} type="button" className="rail-expand" onClick={props.onToggleRail}
        aria-expanded={false} title="展开工作目标列表">
        <span className="rail-text" aria-hidden="true">工作目标 ›</span>
      </button>
      {state && <span className={`rail-state st ${categoryTone(state.state)}`} title={CATEGORY_LABEL[state.state]}
        aria-label={`当前工作目标：${CATEGORY_LABEL[state.state]}`}>●</span>}
    </section>;
  }
  function loadMore() {
    if (loading || !nextCursor || !scroll.current) return;
    scrollIntent.current = false;
    void props.onMore();
  }
  const mainRows = show === "standalone" ? standalone : show === "all" ? rows : objectives;
  const summaryLine = nextCursor
    ? `已加载 ${rows.length} 个记录 · 可能还有更多 · 按最近活动排序`
    : `已加载 ${rows.length} / ${total} 个记录 · 按最近活动排序`;
  return <section className="panel list-panel" aria-label="工作目标列表">
    <div className="panel-toolbar"><h2>工作目标</h2>
      <button className="button small-button" disabled={loading} onClick={props.onRefresh}>刷新记录</button>
    </div>
    <div className="list-filters">
      <div className="segmented" aria-label="按委派状态筛选">{([["all", "全部"], ["active", "进行中"], ["host", "待决定"], ["review", "待验收"]] as const).map(([key, label]) =>
        <button key={key} aria-pressed={props.filter === key} onClick={() => props.onFilterChange(key)}>{label}</button>)}</div>
      <label className="search"><span className="sr-only">搜索工作目标</span><input value={props.query} maxLength={200}
        onChange={event => props.onQueryChange(event.target.value)} placeholder="工作目标、委派标题、项目或 ID" /></label>
      <div className="filter-pair"><label><span className="sr-only">目标项目筛选</span><select aria-label="目标项目筛选" value={props.projectId} onChange={event => props.onProjectChange(event.target.value)}><option value="">全部项目</option>
        {props.choices.projects.map(project => <option key={project.id} value={project.id}>{project.label} — {project.path || project.id}</option>)}</select></label>
        <label><span className="sr-only">目标委派方筛选</span><select aria-label="目标委派方筛选" value={props.hostId} onChange={event => props.onHostChange(event.target.value)}><option value="">全部委派方</option>{props.choices.hosts.map(id => <option key={id}>{id}</option>)}</select></label>
        <label><span className="sr-only">显示类别</span><select aria-label="显示类别" value={show} onChange={event => setShow(event.target.value as ShowKind)}>
          {SHOW_OPTIONS.map(([key, label]) => <option key={key} value={key}>显示：{label}</option>)}</select></label></div>
      <p className="small muted">{summaryLine} · 项目选项来自已加载记录</p>
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
      <ProjectGroups rows={mainRows} collapsed={collapsed} toggle={toggle}
        selected={props.selected} onSelect={props.onSelect}
        headingNoun={show === "standalone" ? "未归档委派" : "工作目标"} />
      {show === "objectives" && standalone.length > 0 && <section className="project-group standalone-group">
        <button className="group-heading" aria-expanded={standaloneOpen} onClick={() => setStandaloneOpen(current => !current)}>
          <span>{standaloneOpen ? "▾" : "▸"} 未归档委派（已加载 {standalone.length}{nextCursor ? " · 可能还有更多" : ""}）</span>
        </button>
        {standaloneOpen && <>
          <p className="small muted standalone-note">这些委派提交时没有指定工作目标，按记录单独显示。</p>
          <ProjectGroups rows={standalone} collapsed={collapsed} toggle={toggle}
            selected={props.selected} onSelect={props.onSelect} headingNoun="未归档委派" />
        </>}
      </section>}
      {show !== "standalone" && !mainRows.length && !loading && !error && (filtersIdle
        ? (standalone.length
          ? <Empty title="已加载的记录中暂无工作目标" action={nextCursor
              ? <div className="actions"><button type="button" className="button small-button" onClick={loadMore}>加载更早记录</button></div>
              : undefined}>更早的记录可能包含工作目标。</Empty>
          : <Empty title="还没有工作目标">Host 提交委派后，这里按项目列出工作目标；旧记录各自显示为未归档委派。</Empty>)
        : <Empty title="没有匹配的工作目标">调整筛选或搜索项目名称。</Empty>)}
      {show === "standalone" && !standalone.length && !loading && !error && <Empty title="没有未归档委派">已加载的记录都归属于工作目标。</Empty>}
      {loading && <p className="loading-row" role="status">正在读取工作目标…</p>}
      {nextCursor && <button className="load-more" disabled={loading} onClick={loadMore}>加载更早工作目标</button>}
    </div>
  </section>;
}
