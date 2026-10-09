import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ObjectiveFilter, ObjectiveSummary } from "./objective-types";
import { Empty } from "./ui";
import { readVerificationText } from "./api";
import { Popover } from "./Popover";
import {
  objectiveStateLabel, dayClock, displayTitle, relativeTime, TASK_SOURCE_NOTE, titleLineTooltip,
} from "./objective-display";

export type ObjectiveListProps = {
  rows: ObjectiveSummary[];
  verifiedAtMs?: number | null;
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
  /** False while the 委派记录 tab is switched away; transient popovers close. */
  active?: boolean;
  /** Whether this particular list surface is visible (rail, drawer and mobile differ). */
  visible?: boolean;
  /** Collapsed 48px rail while a detail is open above 760px (0.15.1 U1). */
  rail: boolean;
  /** Focus target restored when the drawer closes. */
  railButtonRef?: React.RefObject<HTMLButtonElement | null>;
  onToggleRail?: () => void;
  listNavigation?: ReactNode;
  onCollapse?: () => void;
  onFilterChange: (filter: ObjectiveFilter) => void;
  onQueryChange: (query: string) => void;
  onProjectChange: (projectId: string) => void;
  onHostChange: (hostId: string) => void;
  onSelect: (objectiveId: string) => void;
  onRetry: () => void;
  onMore: () => void;
  onApplyReorder: () => void;
};

type ShowKind = "objectives" | "standalone" | "all";
const SHOW_OPTIONS: [ShowKind, string][] = [["objectives", "工作目标"], ["standalone", "历史独立委派"], ["all", "全部"]];

/** The one progress sentence from recorded counts (0.16 P1.4). */
function progressText(row: ObjectiveSummary): string {
  const counts = row.counts;
  const base = `${counts.roots} 个委派`;
  if (row.state === "host") return `${base} · 等待 Host`;
  if (row.state === "active") return `${base} · 进行中`;
  if (row.state === "review") return `${base} · ${counts.review} 个等待验收`;
  if (row.state === "ended" && counts.roots > 0 && counts.accepted === counts.roots) return `${base} · 全部已验收`;
  return `${base} · ${counts.accepted} 个已验收`;
}

function EntryRow({ row, selected, onSelect }: {
  row: ObjectiveSummary; selected: string | null; onSelect: (objectiveId: string) => void;
}) {
  const now = Date.now();
  const chosen = selected === row.objectiveId;
  const title = displayTitle(row.titleSource, row.title);
  const state = objectiveStateLabel(row);
  return <li key={row.objectiveId} data-objective-id={row.objectiveId}>
    <button className={"task-row objective-row" + (chosen ? " selected" : "")} aria-pressed={chosen}
      title={`来源 Host：${row.sourceHostId || "未记录"} · 最近活动 ${dayClock(row.lastActivityAt)}`}
      onClick={() => onSelect(row.objectiveId)}>
      <strong className={"task-title" + (title.fromTask ? " single-line" : "")}
        title={titleLineTooltip(title)}>
        <span className="objective-list-title-text">{title.text}</span>
        {title.fromTask && <span className="title-source-note">{TASK_SOURCE_NOTE}</span>}
      </strong>
      <span className="objective-row-vitals">
        <span className="objective-row-state"><span className="st ok" aria-hidden="true">●</span>{state}</span>
        <span className="objective-row-progress">{progressText(row)}</span>
        <time className="small muted" dateTime={row.lastActivityAt}>{relativeTime(row.lastActivityAt, now)}</time>
      </span>
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
    <button className="group-heading" aria-expanded={!collapsed.has(group.project.id)}
      title={`${group.project.path || group.project.id}\n已加载 ${group.runs.length} 个`}
      onClick={() => toggle(group.project.id)}>
      <span>{collapsed.has(group.project.id) ? "▸" : "▾"} {group.project.label}</span>
      <span className="group-count">{group.runs.length}</span>
    </button>
    {!collapsed.has(group.project.id) && <ul className="task-list">{group.runs.map(row =>
      <EntryRow key={row.objectiveId} row={row} selected={selected} onSelect={onSelect} />)}</ul>}
  </section>)}</>;
}

/**
 * Left column of the 委派记录 tab (0.16 P1.4): work objectives grouped by
 * source project, ordered by latest activity. One 筛选 popup folds the
 * project/host selectors and the show-kind choice; entries are two lines —
 * title, then state + progress sentence + relative time — with the Host ID
 * only in the tooltip. Standalone roots are 历史独立委派 in their own
 * collapsed section. Reads only — and while a detail is open the column can
 * collapse into an expandable narrow rail (U1).
 */
export function ObjectiveList(props: ObjectiveListProps) {
  const { rows, total, loading, error, nextCursor, reorder } = props;
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [standaloneOpen, setStandaloneOpen] = useState(false);
  const [show, setShow] = useState<ShowKind>("objectives");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filterButton = useRef<HTMLButtonElement>(null);
  const projectSelect = useRef<HTMLSelectElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const scrollIntent = useRef(false);
  const pointerInside = useRef(false);
  const applying = useRef(false);
  if (!reorder) applying.current = false;
  const visible = props.visible ?? (props.active !== false && !props.rail);
  const apply = () => {
    if (applying.current) return;
    applying.current = true;
    props.onApplyReorder();
  };
  const applyIfIdle = () => {
    if (visible && reorder && !pointerInside.current && (scroll.current?.scrollTop ?? 0) <= 0) apply();
  };
  useEffect(() => { applyIfIdle(); }, [reorder, visible]);
  // The filter popup is transient: it closes when the rail hides the list or
  // the tab is inactive, and reopening must not restore it (review §10).
  const filtersHidden = props.rail || props.active === false;
  const filtersShown = filtersOpen && !filtersHidden;
  useEffect(() => {
    if (filtersHidden) setFiltersOpen(false);
  }, [filtersHidden]);
  // Opening the filter popup lands focus on the 项目 select — the first
  // dropdown (design P1.4) — never on informational content (review §11).
  useEffect(() => {
    if (filtersShown) projectSelect.current?.focus({ preventScroll: true });
  }, [filtersShown]);
  const objectives = useMemo(() => rows.filter(row => row.kind === "objective"), [rows]);
  const standalone = useMemo(() => rows.filter(row => row.kind === "standalone"), [rows]);
  const filtersIdle = props.filter === "all" && !props.query.trim() && !props.projectId && !props.hostId && show === "objectives";
  const nonDefaultCount = (props.filter !== "all" ? 1 : 0) + (props.projectId ? 1 : 0) + (props.hostId ? 1 : 0) + (show !== "objectives" ? 1 : 0);
  function toggle(projectId: string) {
    setCollapsed(all => {
      const next = new Set(all);
      if (next.has(projectId)) next.delete(projectId); else next.add(projectId);
      return next;
    });
  }
  function loadMore() {
    if (loading || !nextCursor || !scroll.current) return;
    scrollIntent.current = false;
    void props.onMore();
  }
  const mainRows = show === "standalone" ? standalone : show === "all" ? rows : objectives;
  return <>
    <section className="panel list-panel rail-panel" hidden={!props.rail} aria-label="工作目标列表（已收起）">
      {props.listNavigation}
      <button ref={props.railButtonRef} type="button" className="rail-expand" onClick={props.onToggleRail}
        aria-expanded={false} aria-label="展开工作目标列表" title="展开工作目标列表">
        <span className="rail-text" aria-hidden="true">工作目标 ›</span>
      </button>
    </section>
    <section className="panel list-panel" hidden={props.rail} aria-label="工作目标列表"
    onPointerEnter={() => { pointerInside.current = true; }}
    onPointerLeave={() => { pointerInside.current = false; applyIfIdle(); }}>
    <div className="panel-toolbar">{props.listNavigation}<h2 title={`按最近活动排序 · 已加载 ${rows.length} 个`}>工作目标</h2>
      {props.onCollapse && <button type="button" className="button small-button list-collapse"
        title="收起工作目标列表" aria-label="收起工作目标列表" aria-expanded={true} onClick={props.onCollapse}>‹</button>}
      {reorder && visible && <button type="button" className="objective-update" title="按最近活动重新排序"
        onClick={() => { apply(); if (scroll.current) scroll.current.scrollTop = 0; }}>有更新</button>}
    </div>
    <p className="small muted local-read-state">{readVerificationText(props.verifiedAtMs)}</p>
    <div className="list-filters">
      <div className="list-filter-row">
        <label className="search"><span className="sr-only">搜索工作目标</span><input value={props.query} maxLength={200}
          onChange={event => props.onQueryChange(event.target.value)} placeholder="搜索目标、项目或 ID" /></label>
        <button ref={filterButton} type="button" className="button small-button filters-button"
          id="objective-filters-button" aria-expanded={filtersShown} aria-controls="objective-filters-popover"
          onClick={() => setFiltersOpen(current => !current)}>筛选{nonDefaultCount > 0 ? ` · ${nonDefaultCount}` : ""}</button>
        {filtersShown && <Popover id="objective-filters-popover" anchor={filterButton.current} label="筛选" width="min(22em, calc(100vw - 16px))"
          className="filters-popover" onClose={() => setFiltersOpen(false)}>
          <div className="filters-popover-body">
            <div className="segmented" aria-label="按委派状态筛选">{([["all", "全部"], ["active", "进行中"], ["host", "等待 Host"], ["review", "等待验收"]] as const).map(([key, label]) =>
              <button key={key} aria-pressed={props.filter === key} onClick={() => props.onFilterChange(key)}>{label}</button>)}</div>
            <label><span className="field-label">项目</span>
              <select ref={projectSelect} value={props.projectId} onChange={event => props.onProjectChange(event.target.value)}>
                <option value="">全部项目</option>
                {props.choices.projects.map(project => <option key={project.id} value={project.id}>{project.label}{project.path ? ` — ${project.path}` : ""}</option>)}
              </select></label>
            <label><span className="field-label">委派方</span>
              <select value={props.hostId} onChange={event => props.onHostChange(event.target.value)}>
                <option value="">全部委派方</option>{props.choices.hosts.map(id => <option key={id}>{id}</option>)}
              </select></label>
            <label><span className="field-label">显示</span>
              <select value={show} onChange={event => setShow(event.target.value as ShowKind)}>
                {SHOW_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select></label>
            <button type="button" className="button small-button filters-clear" onClick={() => {
              props.onFilterChange("all");
              props.onProjectChange("");
              props.onHostChange("");
              setShow("objectives");
            }}>清除筛选</button>
          </div>
        </Popover>}
      </div>
    </div>
    {error && <div className="list-error" role="alert">{error}<button className="button small-button" onClick={props.onRetry}>重试读取</button></div>}
    <div ref={scroll} className="list-scroll" tabIndex={0} aria-label="工作目标条目"
      onWheel={() => { scrollIntent.current = true; }} onTouchMove={() => { scrollIntent.current = true; }}
      onKeyDown={event => { if (["ArrowDown", "PageDown", "End"].includes(event.key)) scrollIntent.current = true; }}
      onScroll={event => {
        const element = event.currentTarget;
        if (element.scrollTop <= 0) { scrollIntent.current = false; applyIfIdle(); }
        if (scrollIntent.current && element.scrollHeight - element.scrollTop - element.clientHeight < 140) loadMore();
      }}>
      <ProjectGroups rows={mainRows} collapsed={collapsed} toggle={toggle}
        selected={props.selected} onSelect={props.onSelect}
        headingNoun={show === "standalone" ? "历史独立委派" : "工作目标"} />
      {show === "objectives" && standalone.length > 0 && <section className="project-group standalone-group">
        <button className="group-heading" aria-expanded={standaloneOpen}
          title={`未指定工作目标 · 已加载 ${standalone.length} 个${nextCursor ? "，还有更多" : ""}`}
          onClick={() => setStandaloneOpen(current => !current)}>
          <span>{standaloneOpen ? "▾" : "▸"} 历史独立委派（{standalone.length}）</span>
        </button>
        {standaloneOpen && <ProjectGroups rows={standalone} collapsed={collapsed} toggle={toggle}
          selected={props.selected} onSelect={props.onSelect} headingNoun="历史独立委派" />}
      </section>}
      {show !== "standalone" && !mainRows.length && !loading && !error && (filtersIdle
        ? (standalone.length
          ? <Empty title="已加载的记录中暂无工作目标" action={nextCursor
              ? <div className="actions"><button type="button" className="button small-button" onClick={loadMore}>加载更早工作目标</button></div>
              : undefined} />
          : <Empty title="还没有工作目标" />)
        : <Empty title="没有匹配的工作目标">试试其他筛选或关键词。</Empty>)}
      {show === "standalone" && !standalone.length && !loading && !error && <Empty title="没有历史独立委派">已加载的记录都归属于工作目标。</Empty>}
      {loading && <p className="loading-row" role="status">正在读取工作目标…</p>}
      {nextCursor && <button className="load-more" disabled={loading} onClick={loadMore}>加载更早工作目标</button>}
    </div>
  </section></>;
}
