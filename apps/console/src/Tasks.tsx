import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot, Task, TaskQuery } from "./types";
import { Empty, formatDate, Status } from "./ui";
import { excerpt, needsReview, taskStatus, taskTitle, titleTooltip } from "./task-state";
import { TaskDetails } from "./TaskDetails";
import { taskExecutor, taskHost, taskProject } from "./console-data";
import { SplitView } from "./SplitView";
import { mergeLiveTasks, useTaskHistory } from "./use-task-history";
import { useGlobalRefresh } from "./global-refresh";

export function Tasks({ snapshot, api, refresh, active = true }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
}) {
  const [filter, setFilter] = useState<TaskQuery["filter"]>("all"), [query, setQuery] = useState("");
  const [projectId, setProjectId] = useState(""), [hostId, setHostId] = useState(""), [internal, setInternal] = useState(false);
  const [selected, setSelected] = useState<string | null>(null), [remote, setRemote] = useState<Task | null>(null);
  const [detailError, setDetailError] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const history = useTaskHistory(api, { rootsOnly: !internal, query, projectId, hostId, filter }, active);
  useGlobalRefresh(async () => {
    if (!selected) return;
    const value = await api.task(selected);
    if (!value || typeof value !== "object" || !("runId" in value) || value.runId !== selected || !("task" in value)) throw new Error("委派详情不完整。");
    setRemote(value as Task);
    setDetailError("");
  }, active && !!selected);
  const tasks = mergeLiveTasks(history.runs, snapshot.tasks.runs);
  const listed = tasks.find(t => t.runId === selected) || snapshot.tasks.runs.find(t => t.runId === selected);
  const saved = remote?.runId === selected ? remote : undefined;
  const task = saved && (!listed || saved.revision > listed.revision || saved.revision === listed.revision && (saved.workflow?.revision || 0) >= (listed.workflow?.revision || 0)) ? saved : listed;
  const updateSelected = useCallback((next: Task) => setRemote(previous =>
    previous?.runId === next.runId && previous.revision === next.revision && previous.workflow?.revision === next.workflow?.revision ? previous : next), []);
  const scroll = useRef<HTMLDivElement>(null);
  const scrollIntent = useRef(false);
  const anchor = useRef<{ runId: string; top: number } | null>(null);
  const choices = useMemo(() => {
    const records = [...snapshot.tasks.runs, ...history.runs];
    return { projects: [...new Map(records.filter(t => t.delegation?.kind === "goal").map(t => [taskProject(t).id, taskProject(t)])).values()],
      hosts: [...new Set(records.map(t => t.delegation?.sourceHostId).filter((x): x is string => !!x))].sort() };
  }, [snapshot.tasks.runs, history.runs]);
  const matchesState = (t: Task) => filter === "all" || (filter === "host" ? t.workflow?.awaitingHost :
    filter === "review" ? needsReview(t) : ["queued", "running", "cancelling", "reconciliation-needed"].includes(t.status)
      || ["executing", "awaiting-host", "waiting-helpers"].includes(t.workflow?.state || ""));
  const visible = tasks.filter(matchesState);
  const groups = new Map<string, { project: ReturnType<typeof taskProject>; runs: Task[] }>();
  for (const row of visible) {
    const project = taskProject(row);
    if (!groups.has(project.id)) groups.set(project.id, { project, runs: [] });
    groups.get(project.id)!.runs.push(row);
  }
  const newest = history.runs[0];
  const newRecords = !history.loading ? snapshot.tasks.runs.filter(t => (!internal ? t.delegation?.kind === "goal" : true) && matchesState(t) &&
    (!projectId || taskProject(t).id === projectId) && (!hostId || [t.delegation?.sourceHostId, t.delegation?.currentHostId].includes(hostId)) &&
    [t.task, t.runId, taskProject(t).path, t.delegation?.sourceHostId, t.delegation?.currentHostId,
      t.delegation?.configuration?.adapter || t.spec?.adapter, t.delegation?.configuration?.model || t.spec?.model].join(" ").toLowerCase().includes(query.trim().toLowerCase()) &&
    !history.runs.some(r => r.runId === t.runId) &&
    (!newest || t.createdAt > newest.createdAt || t.createdAt === newest.createdAt && t.runId > newest.runId)) : [];
  useEffect(() => {
    if (!selected || task || !active) return;
    let current = true; setDetailError("");
    api.task(selected).then(value => {
      if (!value || typeof value !== "object" || !("runId" in value) || value.runId !== selected || !("task" in value)) throw new Error("委派详情不完整。");
      if (current) setRemote(value as Task);
    }).catch(error => { if (current) setDetailError(errorText(error)); });
    return () => { current = false; };
  }, [api, selected, active, !!task]);
  function selectTask(id: string | null) {
    const next = tasks.find(t => t.runId === id) || snapshot.tasks.runs.find(t => t.runId === id);
    setRemote(next || null);
    setSelected(id); setDetailError("");
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
  const reload = async () => { const result = await refresh(); history.reset(); return result; };
  const list = <section className="panel list-panel" aria-label="委派列表">
    <div className="panel-toolbar"><h2>委派记录</h2></div>
    <div className="list-filters">
      <div className="segmented" aria-label="任务筛选">{([["all", "全部"], ["active", "进行中"], ["host", "等待 Host"], ["review", "等待验收"]] as const).map(([key, label]) =>
        <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}</div>
      <label className="search"><span className="sr-only">搜索委派</span><input value={query} maxLength={200} onChange={e => setQuery(e.target.value)} placeholder="目标、项目、委派方或 ID" /></label>
      <div className="filter-pair"><label><span className="sr-only">项目筛选</span><select value={projectId} onChange={e => setProjectId(e.target.value)}><option value="">全部项目</option>
        {choices.projects.map(p => <option key={p.id} value={p.id}>{p.label} — {p.path || p.id}</option>)}</select></label>
        <label><span className="sr-only">委派方筛选</span><select value={hostId} onChange={e => setHostId(e.target.value)}><option value="">全部委派方</option>{choices.hosts.map(id => <option key={id}>{id}</option>)}</select></label></div>
      <label className="check-field"><input type="checkbox" checked={internal} onChange={e => setInternal(e.target.checked)} />显示协助任务与内部执行</label>
      <p className="small muted">已加载 {history.runs.length} / {history.total} 条 · 项目选项来自已加载记录</p>
    </div>
    {newRecords.length > 0 && <button className="new-records" onClick={() => { history.reset(); if (scroll.current) scroll.current.scrollTop = 0; }}>有 {newRecords.length} 条新记录 · 回到最新</button>}
    {history.error && <div className="list-error" role="alert">{history.error}<button className="button small-button" onClick={() => void history.retry()}>重试读取</button></div>}
    <div ref={scroll} className="list-scroll" tabIndex={0} aria-label="委派条目"
      onWheel={() => { scrollIntent.current = true; }} onTouchMove={() => { scrollIntent.current = true; }}
      onKeyDown={e => { if (["ArrowDown", "PageDown", "End"].includes(e.key)) scrollIntent.current = true; }}
      onScroll={e => { const el = e.currentTarget; if (scrollIntent.current && el.scrollHeight - el.scrollTop - el.clientHeight < 140) { scrollIntent.current = false; loadMore(); } }}>
      {[...groups].map(([id, group]) => <section key={id} className="project-group">
        <button className="group-heading" aria-expanded={!collapsed.has(id)} title={group.project.path || group.project.id} onClick={() => setCollapsed(all => {
          const next = new Set(all); if (next.has(id)) next.delete(id); else next.add(id); return next;
        })}><span>{collapsed.has(id) ? "▸" : "▾"} {group.project.label}</span>
          <span className="small">已加载 {group.runs.length} {internal ? "条执行记录" : "个委派目标"}</span></button>
        {!collapsed.has(id) && <ul className="task-list">{group.runs.map(row => {
          // The selected row shows the title from the freshest read (the bounded
          // workflow refresh merges it here), so an older row outside the snapshot
          // window agrees with the open detail; badges and filters keep the row.
          const rowTask = task && row.runId === task.runId ? task : row;
          return <li key={row.runId} data-run-id={row.runId}>
          <button className={"task-row " + (selected === row.runId ? "selected" : "")} aria-pressed={selected === row.runId} onClick={() => selectTask(row.runId)}>
            <span className="row-between"><Status status={taskStatus(row)} /><time className="small muted" dateTime={row.createdAt}>{formatDate(row.createdAt)}</time></span>
            <strong className="task-title" title={titleTooltip(taskTitle(rowTask))}>{excerpt(taskTitle(rowTask).text, 100)}</strong>
            <span className="small truncate" title={taskHost(row) + " → " + taskExecutor(row)}>{taskHost(row)} → {taskExecutor(row)}</span>
            {row.delegation?.kind !== "goal" && <span className="small muted">{row.delegation?.kind === "helper" ? "协助任务" : row.delegation?.kind === "decision" ? "内部路由 / 整理" : "执行记录"}</span>}
          </button>
        </li>;
        })}</ul>}
      </section>)}
      {!visible.length && !history.loading && !history.error && <Empty title="没有匹配的委派">调整筛选或搜索项目名称；内部计算默认收起。</Empty>}
      {history.loading && <p className="loading-row" role="status">正在读取委派记录…</p>}
      {history.nextCursor && <button className="load-more" disabled={history.loading} onClick={loadMore}>加载更早记录</button>}
    </div>
  </section>;
  const detail = <aside className="panel detail-panel" aria-label="任务详情">
    {task ? <TaskDetails key={task.runId} task={task} snapshot={snapshot} api={api} refresh={reload} selectTask={selectTask} active={active} onTaskUpdate={updateSelected} /> :
      <div className="detail-placeholder"><h2>{selected ? "正在读取委派…" : "选择一项委派"}</h2><p>{detailError || "按项目查看目标、委派方与执行结果。协助任务保留在所属目标的详情中。"}</p></div>}
  </aside>;
  return <SplitView selected={!!selected} list={list} detail={detail} />;
}
