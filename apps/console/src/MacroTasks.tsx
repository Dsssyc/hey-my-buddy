import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { MacroTaskFilter, MacroTaskSummary } from "./macro-task-types";
import type { TimelineItem } from "./macro-task-display";
import { CATEGORY_LABEL, categoryTone } from "./macro-task-display";
import type { InspectorSelection } from "./inspector-card";
import { Tasks } from "./Tasks";
import { SplitView } from "./SplitView";
import { MacroTaskList } from "./MacroTaskList";
import { MacroTaskTimeline } from "./MacroTaskTimeline";
import { TaskDetail } from "./task-detail/TaskDetail";
import { createTaskDetailClient } from "./task-detail/client";
import { TASKS_ROOT, tasksHash, tasksLocation } from "./task-detail/location";
import type { TasksLocation } from "./task-detail/location";
import { useMacroTaskList } from "./use-macro-task-list";
import { useMacroTaskTimeline } from "./use-macro-task-timeline";
import { stopScope, stopStatus, useMacroTaskStop } from "./macro-task-stop";
import type { StopScope } from "./macro-task-stop";
import type { AuthorityLatch } from "./console-session";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "./console-session";
import { displayTitle } from "./macro-task-display";
import { useBackgroundInert } from "./modal";
import { useViewPreference } from "./view-preferences";

/** The macroTask stop's explicit confirmation: names the macroTask and its scope. */
function StopDialog({ summary, scope, onConfirm, onCancel }: {
  summary: MacroTaskSummary;
  scope: StopScope;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("shell");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const backdrop = useRef<HTMLDivElement>(null);
  useBackgroundInert(backdrop);
  useEffect(() => { cancelRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);
  const title = displayTitle(summary.titleSource, summary.title).text;
  return <div className="dialog-backdrop" ref={backdrop}>
    <div className="dialog stop-dialog" role="dialog" aria-modal="true" aria-labelledby="stop-macro-task-title" aria-describedby="stop-macro-task-name stop-macro-task-body stop-macro-task-counts">
      <h2 id="stop-macro-task-title">{t("macroStop.title")}</h2>
      <p id="stop-macro-task-name" className="stop-dialog-task-title" title={title}>{title}</p>
      <p id="stop-macro-task-body">{t("macroStop.scope")}</p>
      <ul id="stop-macro-task-counts" className="stop-dialog-counts">
        <li>{t("macroStop.scopeRoots", { count: scope.roots })}</li>
        <li>{t("macroStop.scopeHelpers", { count: scope.helpers })}</li>
        <li>{t("macroStop.scopeDelivered", { count: scope.delivered })}</li>
        <li>{t("macroStop.scopeAwaitingHost", { count: scope.awaitingHost })}</li>
        <li>{t("macroStop.scopeAccepted", { count: summary.counts.accepted })}</li>
      </ul>
      <div className="actions">
        <button ref={cancelRef} type="button" className="button" onClick={onCancel}>{t("macroStop.cancel")}</button>
        <button type="button" className="button danger" onClick={onConfirm}>{t("macroStop.confirm")}</button>
      </div>
    </div>
  </div>;
}

/**
 * Content of the 微任务记录 tab. The primary view browses micro tasks
 * as work macroTasks; the clearly labelled secondary switch keeps the
 * raw task history (command and external records included) reachable
 * instead of silently removing it.
 *
 * Where the page is (a macro task, one of its micro tasks, a step of it) is the
 * address, and the address is the only place it lives: choosing a macro task,
 * opening a micro task and going back to the timeline each add a step to the
 * browser's history, and choosing a step of a micro task only replaces the one
 * it is on.
 */
export function MacroTasks({ snapshot, api, refresh, active = true, authority, writesAvailable = true, onAddress }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
  authority?: AuthorityLatch; writesAvailable?: boolean;
  /** Told where the page is, so that the link back to the tasks leads to the same place. */
  onAddress?: (hash: string) => void;
}) {
  const [view, setView] = useState<"macroTasks" | "records">("macroTasks");
  const [listCollapsed, setListCollapsed] = useViewPreference("micro-task-list-collapsed");
  const [location, setLocation] = useState<TasksLocation>(() => tasksLocation(window.location.hash) ?? TASKS_ROOT);
  // The timeline item the open detail came from; it names the step the detail opens on.
  const [origin, setOrigin] = useState<string | null>(null);
  function go(next: TasksLocation, how: "push" | "replace", from: string | null = null) {
    const hash = tasksHash(next);
    if (window.location.hash !== hash) (how === "push" ? window.history.pushState : window.history.replaceState).call(window.history, null, "", hash);
    setOrigin(from);
    setLocation(next);
    onAddress?.(hash);
    if (next.macroTaskId) setView("macroTasks");
  }
  useEffect(() => {
    onAddress?.(tasksHash(location));
    const changed = () => {
      const next = tasksLocation(window.location.hash);
      if (!next) return;
      setOrigin(null);
      setLocation(next);
      onAddress?.(tasksHash(next));
      if (next.macroTaskId) setView("macroTasks");
    };
    // A step back or forward through the history, and a hand-written address, both land here.
    window.addEventListener("hashchange", changed);
    window.addEventListener("popstate", changed);
    return () => { window.removeEventListener("hashchange", changed); window.removeEventListener("popstate", changed); };
  }, []);
  const destination = view === "macroTasks" ? "全部执行记录" : "宏任务";
  const listNavigation = <button type="button" className="button small-button history-view-toggle"
    title={`切换到${destination}`} aria-label={`切换到${destination}`}
    onClick={() => setView(current => current === "macroTasks" ? "records" : "macroTasks")}>
    <span aria-hidden="true">⇄</span>
  </button>;
  return <div className="history-switch">
    <div className="history-view" hidden={view !== "macroTasks"}>
      <MacroTasksWorkspace snapshot={snapshot} api={api} refresh={refresh}
        active={active && view === "macroTasks"} authority={authority} writesAvailable={writesAvailable}
        location={location} origin={origin} go={go}
        listNavigation={listNavigation} listCollapsed={listCollapsed}
        onCollapseList={() => setListCollapsed(true)} onExpandList={() => setListCollapsed(false)} />
    </div>
    <div className="history-view" hidden={view !== "records"}>
      <Tasks snapshot={snapshot} api={api} refresh={refresh} active={active && view === "records"}
        onOpenMicroTask={(macroTaskId, runId) => go({ macroTaskId, runId, step: null }, "push")}
        listNavigation={listNavigation} listCollapsed={listCollapsed}
        onCollapseList={() => setListCollapsed(true)} onExpandList={() => setListCollapsed(false)} />
    </div>
  </div>;
}

function MacroTasksWorkspace({ snapshot, api, refresh, active, authority, writesAvailable, location, origin, go, listNavigation, listCollapsed, onCollapseList, onExpandList }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active: boolean;
  authority?: AuthorityLatch; writesAvailable: boolean;
  location: TasksLocation; origin: string | null;
  go: (next: TasksLocation, how: "push" | "replace", from?: string | null) => void;
  listNavigation: ReactNode; listCollapsed: boolean; onCollapseList: () => void; onExpandList: () => void;
}) {
  const { t } = useTranslation("shell");
  const [filter, setFilter] = useState<MacroTaskFilter>("all");
  const [query, setQuery] = useState(""), [projectId, setProjectId] = useState(""), [hostId, setHostId] = useState("");
  const selected = location.macroTaskId;
  const detailRunId = selected ? location.runId : null;
  // The pinned inspector selection, kept per macroTask so switching back restores it.
  const [selectionByMacroTask, setSelectionByMacroTask] = useState<Map<string, InspectorSelection>>(() => new Map());
  const list = useMacroTaskList(api, { query, projectId, hostId, filter }, active);
  const timeline = useMacroTaskTimeline(api, selected, active);
  const detailClient = useMemo(() => createTaskDetailClient(api, snapshot.csrfToken), [api, snapshot.csrfToken]);
  // The list row is freshest, but a list refresh must never unmount an open
  // detail: the timeline read's summary covers the gap, and a snapshot of the
  // last seen summary covers the moments before the timeline read lands.
  const listedSummary = list.rows.find(row => row.macroTaskId === selected)
    ?? (timeline.timeline?.macroTask.macroTaskId === selected ? timeline.timeline.macroTask : null)
    ?? null;
  const [summarySnapshot, setSummarySnapshot] = useState<MacroTaskSummary | null>(null);
  useEffect(() => {
    if (listedSummary) setSummarySnapshot(listedSummary);
    else if (!selected) setSummarySnapshot(null);
  }, [listedSummary, selected]);
  const selectedSummary = listedSummary
    ?? (summarySnapshot?.macroTaskId === selected ? summarySnapshot : null);
  const choices = useMemo(() => ({
    projects: [...new Map(list.rows.map(row => [row.project.id, row.project])).values()],
    hosts: [...new Set(list.rows.map(row => row.sourceHostId).filter((id): id is string => !!id))].sort(),
  }), [list.rows]);

  // The viewport picks the layout: at 760px or below the list gives way to the
  // macro task that is chosen, and the detail's two columns stack.
  const railButtonRef = useRef<HTMLButtonElement>(null);
  const [narrowViewport, setNarrowViewport] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 760px)");
    const update = () => setNarrowViewport(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  const railActive = listCollapsed;

  // MacroTask-level stop: the one retained write, with its explicit
  // confirmation and a retained command identity. Nothing here locks browsing.
  const reload = async () => {
    const result = await refresh();
    list.reset();
    void timeline.retry();
    return result;
  };
  const stop = useMacroTaskStop(api, snapshot, writesAvailable, authority, reload);
  const stopEntry = selected ? stop.entries.get(selected) : undefined;
  const stopState = stopStatus(stopEntry, timeline.timeline);

  function setSelection(next: InspectorSelection | null) {
    if (!selected) return;
    setSelectionByMacroTask(previous => {
      const map = new Map(previous);
      if (next === null) map.delete(selected);
      else map.set(selected, next);
      return map;
    });
  }
  const selection = selected ? selectionByMacroTask.get(selected) ?? null : null;

  const selectMacroTask = (macroTaskId: string | null) => go({ macroTaskId, runId: null, step: null }, "push");
  const openRun = (runId: string, from: string | null = null) => {
    // Opening from a row or a card names no span: the detail opens on its own first choice.
    setSelection({ type: "run", runId });
    go({ macroTaskId: selected, runId, step: null }, "push", from);
  };
  function selectItem(item: TimelineItem) {
    setSelection({ type: "item", key: item.key });
  }
  function selectRun(runId: string) {
    setSelection({ type: "run", runId });
  }
  function openItem(item: TimelineItem) {
    setSelection({ type: "item", key: item.key });
    go({ macroTaskId: selected, runId: item.runId, step: null }, "push", item.key);
  }
  const backToTimeline = () => go({ macroTaskId: selected, runId: null, step: null }, "push");

  // The header's stop control: one macro-task-level action, its honest status,
  // and the replay entry for a lost reply. Read-only sessions see the reason.
  let stopControl: ReactNode = null;
  if (selectedSummary) {
    const stopping = stopEntry?.phase === "stopping";
    const scope = stopScope(selectedSummary);
    const stoppingAvailable = scope.roots + scope.helpers > 0;
    stopControl = <span className="stop-control">
      <button type="button" className="button small-button danger"
        disabled={!stop.writable || stopping || !stoppingAvailable}
        title={!stop.writable ? LOGIN_EXPIRED_ACTION_REFUSAL : stopping ? t("macroStop.pending") : !stoppingAvailable ? t("macroStop.noActive") : t("macroStop.actionHint")}
        onClick={() => stop.requestStop(selectedSummary, scope)}>{t("macroStop.title")}</button>
      {stopState && <span className={`stop-status stop-${stopState.phase}`} role="status"
        title={stopState.detail}>{stopState.label}</span>}
      {stopState?.phase === "unknown" && stop.writable && !stopping
        && <button type="button" className="button small-button"
          title={t("macroStop.retryHint")}
          onClick={() => stop.retryStop(selectedSummary)}>{t("macroStop.retry")}</button>}
      {stopState?.phase === "refused"
        && <button type="button" className="button small-button" onClick={() => stop.dismissStop(selectedSummary.macroTaskId)}>{t("macroStop.dismiss")}</button>}
    </span>;
  }

  const listPane = <MacroTaskList
    rows={list.rows} total={list.total} loading={list.loading} error={list.error} verifiedAtMs={list.verifiedAtMs}
    nextCursor={list.nextCursor} reorder={list.reorder}
    filter={filter} query={query} projectId={projectId} hostId={hostId} choices={choices}
    selected={selected}
    active={active}
    visible={active && !railActive && !(narrowViewport && !!selected)}
    rail={railActive} railButtonRef={railButtonRef}
    listNavigation={listNavigation}
    onCollapse={onCollapseList}
    onToggleRail={onExpandList}
    onFilterChange={setFilter} onQueryChange={setQuery} onProjectChange={setProjectId} onHostChange={setHostId}
    onSelect={selectMacroTask} onRetry={list.retry} onMore={list.more}
    onApplyReorder={list.applyReorder} />;

  // The detail takes the place of the timeline, under the macro task's own header.
  const detail = detailRunId ? <TaskDetail key={detailRunId} client={detailClient} runId={detailRunId}
    timeline={timeline.timeline} timelineLoading={timeline.loading && !timeline.timeline}
    nowMs={timeline.displayObservedAt ? Date.parse(timeline.displayObservedAt) : Date.now()}
    step={location.step} origin={origin} active={active}
    onStep={step => go({ macroTaskId: selected, runId: detailRunId, step }, "replace", origin)}
    onBack={backToTimeline} onOpenRun={runId => openRun(runId)} /> : null;

  const timelinePane = selected ? <MacroTaskTimeline
    active={active}
    summary={selectedSummary}
    timeline={timeline.timeline}
    displayObservedAt={timeline.displayObservedAt}
    loading={timeline.loading}
    error={timeline.error}
    stale={timeline.stale}
    newRunIds={timeline.newRunIds}
    hidden={!!detail}
    detail={detail}
    openedKey={origin}
    openedRunId={detailRunId}
    selection={selection}
    headerActions={stopControl}
    buddies={snapshot.buddies}
    onSelectItem={selectItem}
    onOpenItem={openItem}
    onSelectRun={selectRun}
    onOpenRun={runId => openRun(runId)}
    onClearSelection={() => setSelection(null)}
    onRetry={() => void timeline.retry()}
    onBackToList={() => selectMacroTask(null)} /> : null;

  const detailPane = <aside className="panel detail-panel" aria-label="宏任务详情">
    {selected && <div className="panel-toolbar list-mobile-navigation">
      {listNavigation}<h2>宏任务</h2>
      <button type="button" className="button small-button" title={listCollapsed ? "展开宏任务列表" : "收起宏任务列表"}
        aria-label={listCollapsed ? "展开宏任务列表" : "收起宏任务列表"}
        onClick={listCollapsed ? onExpandList : onCollapseList}>{listCollapsed ? "展开列表" : "收起列表"}</button>
    </div>}
    {selected
      ? <div className="right-stage"><div className="right-stage-pane">{timelinePane}</div></div>
      : <div className="detail-placeholder">
        <h2>选择一个宏任务</h2>

      </div>}
  </aside>;
  return <>
    <div className="workspace-wrap">
      <SplitView selected={!!selected} rail={railActive} list={listPane} detail={detailPane} />
    </div>
    {stop.confirming && <StopDialog summary={stop.confirming.summary} scope={stop.confirming.scope}
      onConfirm={() => stop.confirmStop(stop.confirming!.summary)}
      onCancel={stop.cancelConfirm} />}
  </>;
}
