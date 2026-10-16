import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ConsoleApi } from "./api";
import { readVerificationText } from "./api";
import type { Snapshot, Task, TaskQuery } from "./types";
import { Empty, formatDate, Status } from "./ui";
import { excerpt, needsReview, taskStatus, taskTitle, titleTooltip } from "./task-state";
import { TaskDetail } from "./task-detail/TaskDetail";
import { createTaskDetailClient } from "./task-detail/client";
import { macroTaskKey } from "./task-detail/location";
import { taskExecutor, taskHost, taskProject } from "./console-data";
import { SplitView } from "./SplitView";
import { useTaskHistory } from "./use-task-history";

/**
 * Every record the board keeps, in the order it was written. A micro task with a
 * collaboration record opens in its macro task, at its own address; a run
 * without one, a command or a Router's calculation, is shown here, as one step.
 */
export function Tasks({ snapshot, api, active = true, onOpenMicroTask, listNavigation, listCollapsed = false, onCollapseList, onExpandList }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
  onOpenMicroTask: (macroTaskId: string, runId: string) => void;
  listNavigation?: ReactNode; listCollapsed?: boolean; onCollapseList?: () => void; onExpandList?: () => void;
}) {
  const [filter, setFilter] = useState<TaskQuery["filter"]>("all"), [query, setQuery] = useState("");
  const [projectId, setProjectId] = useState(""), [hostId, setHostId] = useState(""), [internal, setInternal] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // The records view reads only its own paginated route: the loaded pages are
  // the rows, the first-page poll keeps them current, and the snapshot never
  // contributes rows to this list.
  const history = useTaskHistory(api, { rootsOnly: !internal, query, projectId, hostId, filter }, active);
  const tasks = history.runs;
  const scroll = useRef<HTMLDivElement>(null);
  const scrollIntent = useRef(false);
  const anchor = useRef<{ runId: string; top: number } | null>(null);
  const choices = useMemo(() => {
    // Filter choices come from the rows this view has itself read (bounded
    // memory in the hook), so a scope change reloading the list never empties
    // the dropdowns.
    const records = history.known;
    return { projects: [...new Map(records.filter(t => t.lineage?.kind === "micro-task").map(t => [taskProject(t).id, taskProject(t)])).values()],
      hosts: [...new Set(records.map(t => t.lineage?.sourceHostId).filter((x): x is string => !!x))].sort() };
  }, [history.known]);
  const matchesState = (t: Task) => filter === "all" || (filter === "host" ? t.workflow?.awaitingHost && !t.lineage?.rootLeadRunId :
    filter === "review" ? needsReview(t) : ["queued", "running", "cancelling", "reconciliation-needed"].includes(t.status)
      || ["executing", "awaiting-host", "waiting-helpers", "waiting-owned"].includes(t.workflow?.state || ""));
  const visible = tasks.filter(matchesState);
  const groups = new Map<string, { project: ReturnType<typeof taskProject>; runs: Task[] }>();
  for (const row of visible) {
    const project = taskProject(row);
    if (!groups.has(project.id)) groups.set(project.id, { project, runs: [] });
    groups.get(project.id)!.runs.push(row);
  }
  // New matching records the loaded history has not seen: detected by the
  // view's own first-page poll, never by merging snapshot rows.
  const newRecords = history.newIds.size;
  function selectTask(id: string | null) {
    const row = tasks.find(candidate => candidate.runId === id);
    const macroTaskId = row?.workflow ? macroTaskKey(row) : null;
    if (id && macroTaskId) onOpenMicroTask(macroTaskId, id);
    else setSelected(id);
  }
  function loadMore() {
    if (history.loading || !history.nextCursor || !scroll.current) return;
    const top = scroll.current.getBoundingClientRect().top;
    const row = [...scroll.current.querySelectorAll<HTMLElement>("[data-run-id]")].find(node => node.getBoundingClientRect().bottom > top);
    if (row) anchor.current = { runId: row.dataset.runId!, top: row.getBoundingClientRect().top };
    void history.more();
  }
  useLayoutEffect(() => {
    if (!anchor.current || !scroll.current || history.loading) return;
    const saved = anchor.current;
    const row = [...scroll.current.querySelectorAll<HTMLElement>("[data-run-id]")].find(node => node.dataset.runId === saved.runId);
    if (row) scroll.current.scrollTop += row.getBoundingClientRect().top - saved.top;
    anchor.current = null;
  }, [history.runs.length, history.loading]);
  const detailClient = useMemo(() => createTaskDetailClient(api, snapshot.csrfToken), [api, snapshot.csrfToken]);
  const list = <>
    {listCollapsed && <section className="panel list-panel rail-panel" aria-label="全部执行记录列表（已收起）">
      {listNavigation}
      <button type="button" className="rail-expand" aria-label="展开全部执行记录列表" title="展开全部执行记录列表" onClick={onExpandList}><span className="rail-text">全部执行记录</span></button>
    </section>}
    <section className="panel list-panel" aria-label="微任务列表" hidden={listCollapsed}>
    <div className="panel-toolbar list-title-toolbar">{listNavigation}<h2>全部执行记录</h2>
      {onCollapseList && <button type="button" className="icon-button" aria-label="收起全部执行记录列表" title="收起全部执行记录列表" onClick={onCollapseList}>‹</button>}
      <button type="button" className="button small-button" aria-label="刷新全部执行记录" title="刷新全部执行记录" disabled={history.refreshing || history.loading} onClick={() => void history.refresh()}>刷新</button>
    </div>
    <p className="small muted local-read-state">{readVerificationText(history.verifiedAtMs)}</p>
    <div className="list-filters">
      <div className="segmented" aria-label="任务筛选">{([["all", "全部"], ["active", "进行中"], ["host", "等待 Host"], ["review", "等待验收"]] as const).map(([key, label]) =>
        <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div>
      <label className="search"><span className="sr-only">搜索微任务</span><input value={query} maxLength={200} onChange={e => setQuery(e.target.value)} placeholder="微任务、项目、委派方或 ID" /></label>
      <div className="filter-pair"><label><span className="sr-only">项目筛选</span><select value={projectId} onChange={e => setProjectId(e.target.value)}><option value="">全部项目</option>
        {choices.projects.map(p => <option key={p.id} value={p.id}>{p.label} — {p.path || p.id}</option>)}</select></label>
        <label><span className="sr-only">委派方筛选</span><select value={hostId} onChange={e => setHostId(e.target.value)}><option value="">全部委派方</option>{choices.hosts.map(id => <option key={id}>{id}</option>)}</select></label></div>
      <label className="check-field"><input type="checkbox" checked={internal} onChange={e => setInternal(e.target.checked)} />显示协助任务与内部执行</label>
      <p className="small muted">已加载 {history.runs.length} / {history.total} 条</p>
    </div>
    {newRecords > 0 && <button className="new-records" onClick={() => { history.reset(); if (scroll.current) scroll.current.scrollTop = 0; }}>有 {newRecords} 条新记录 · 回到最新</button>}
    {history.error && <div className="list-error" role="alert">{history.error}<button className="button small-button" onClick={() => void history.retry()}>重试读取</button></div>}
    <div ref={scroll} className="list-scroll" tabIndex={0} aria-label="微任务条目"
      onWheel={() => { scrollIntent.current = true; }} onTouchMove={() => { scrollIntent.current = true; }}
      onKeyDown={e => { if (["ArrowDown", "PageDown", "End"].includes(e.key)) scrollIntent.current = true; }}
      onScroll={e => { const el = e.currentTarget; if (scrollIntent.current && el.scrollHeight - el.scrollTop - el.clientHeight < 140) { scrollIntent.current = false; loadMore(); } }}>
      {[...groups].map(([id, group]) => <section key={id} className="project-group">
        <button className="group-heading" aria-expanded={!collapsed.has(id)} title={group.project.path || group.project.id} onClick={() => setCollapsed(all => {
          const next = new Set(all); if (next.has(id)) next.delete(id); else next.add(id); return next;
        })}><span>{collapsed.has(id) ? "▸" : "▾"} {group.project.label}</span>
          <span className="small">已加载 {group.runs.length} {internal ? "条执行记录" : "个微任务"}</span></button>
        {!collapsed.has(id) && <ul className="task-list">{group.runs.map(row => {
          const executor = taskExecutor(row);
          const assignment = executor ? `${taskHost(row)} → ${executor}` : taskHost(row);
          return <li key={row.runId} data-run-id={row.runId}>
          <button className={"task-row " + (selected === row.runId ? "selected" : "")} aria-pressed={selected === row.runId} onClick={() => selectTask(row.runId)}>
            <span className="row-between"><Status status={taskStatus(row)} /><time className="small muted" dateTime={row.createdAt}>{formatDate(row.createdAt)}</time></span>
            <strong className="task-title" title={titleTooltip(taskTitle(row))}>{excerpt(taskTitle(row).text, 100)}</strong>
            <span className="small truncate" title={assignment}>{assignment}</span>
            {row.lineage?.kind !== "micro-task" && <span className="small muted">{row.lineage?.kind === "helper" ? "协助任务" : row.lineage?.kind === "decision" ? "内部路由 / 整理" : "执行记录"}</span>}
          </button>
        </li>;
        })}</ul>}
      </section>)}
      {!visible.length && !history.loading && !history.error && <Empty title="没有匹配的微任务">试试其他筛选或关键词。</Empty>}
      {history.loading && <p className="loading-row" role="status">正在读取微任务记录…</p>}
      {history.nextCursor && <button className="load-more" disabled={history.loading} onClick={loadMore}>加载更早记录</button>}
    </div>
  </section></>;
  const detail = <aside className="panel detail-panel" aria-label="任务详情">
    {selected && <div className="panel-toolbar list-mobile-navigation">{listNavigation}
      <h2>全部执行记录</h2>
      <button type="button" className="button small-button" onClick={listCollapsed ? onExpandList : onCollapseList}
        aria-label={listCollapsed ? "展开全部执行记录列表" : "收起全部执行记录列表"} title={listCollapsed ? "展开全部执行记录列表" : "收起全部执行记录列表"}>{listCollapsed ? "展开列表" : "收起列表"}</button>
    </div>}
    {selected ? <TaskDetail key={selected} client={detailClient} runId={selected} timeline={null} timelineLoading={false}
      nowMs={null} step={1} origin={null} active={active} backTo="records" listed={history.runs.find(run => run.runId === selected)?.revision ?? null} onStep={() => undefined}
      onBack={() => setSelected(null)} onOpenRun={selectTask} /> :
      <div className="detail-placeholder"><h2>选择一项微任务</h2></div>}
  </aside>;
  return <>
    <SplitView selected={!!selected} rail={listCollapsed} list={list} detail={detail} />
  </>;
}
