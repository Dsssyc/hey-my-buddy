import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { MacroTaskFilter, MacroTaskSummary } from "./macro-task-types";
import type { TimelineItem } from "./macro-task-display";

import type { InspectorSelection } from "./inspector-card";
import { Tasks } from "./Tasks";
import { ListFold } from "./MacroTaskList";
import { Hint } from "./components/hint";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "./components/ui/dialog";
import { StateMark } from "./components/ui/state-mark";
import { formatClockMinutes, formatNumber } from "./i18n/format";
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

import { displayTitle } from "./macro-task-display";

import { useViewPreference } from "./view-preferences";

function ConnectionStrip({ error, verifiedAtMs, retry }: { error: string; verifiedAtMs: number | null; retry: () => void }) {
  const { t } = useTranslation("tasks");
  return <div className="macro-offline" role="alert"><StateMark kind="fail"/><Hint text={error}><span tabIndex={0}>{t("offline")}</span></Hint>
    {verifiedAtMs !== null && <small>{t("lastRead", { time: formatClockMinutes(verifiedAtMs) })}</small>}
    <button type="button" onClick={retry}>{t("retry")}</button>
  </div>;
}

/** The macroTask stop's explicit confirmation: names the macroTask and its scope. */
function StopDialog({ summary, scope, onConfirm, onCancel }: {
  summary: MacroTaskSummary;
  scope: StopScope;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["shell", "tasks"]);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const title = summary.titleSource === "none" ? t("tasks:unnamed") : displayTitle(summary.titleSource, summary.title).text;
  return <Dialog open onOpenChange={open => { if (!open) onCancel(); }}>
    <DialogContent className="stop-dialog" onOpenAutoFocus={event => { event.preventDefault(); cancelRef.current?.focus(); }} aria-describedby="stop-macro-task-name stop-macro-task-body stop-macro-task-counts">
      <DialogTitle>{t("macroStop.title")}</DialogTitle>
      <Hint text={title}><p id="stop-macro-task-name" tabIndex={0} className="stop-dialog-task-title">{title}</p></Hint>
      <DialogDescription id="stop-macro-task-body">{t("macroStop.scope")}</DialogDescription>
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
    </DialogContent>
  </Dialog>;
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
export function MacroTasks({ snapshot, api, refresh, active = true, authority, writesAvailable = true, onAddress, connectionError = "", verifiedAtMs = null }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
  authority?: AuthorityLatch; writesAvailable?: boolean;
  /** Told where the page is, so that the link back to the tasks leads to the same place. */
  onAddress?: (hash: string) => void;
  connectionError?: string; verifiedAtMs?: number | null;
}) {
  const { t } = useTranslation("tasks");
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
  const listNavigation = (loaded?: number) => <div className="macro-list-navigation" role="group" aria-label={t("list")}>
    <Hint text={loaded === undefined ? undefined : t("loaded", { n: formatNumber(loaded) })}><button type="button" aria-pressed={view === "macroTasks"} onClick={() => setView("macroTasks")}>{t("macro")}</button></Hint>
    <button type="button" aria-pressed={view === "records"} onClick={() => setView("records")}>{t("records")}</button>
  </div>;
  return <div className="history-switch">
    {view === "records" && connectionError && <ConnectionStrip error={connectionError} verifiedAtMs={verifiedAtMs} retry={() => { void refresh(); }}/>}
    <div className="history-view" hidden={view !== "macroTasks"}>
      <MacroTasksWorkspace snapshot={snapshot} api={api} refresh={refresh}
        active={active && view === "macroTasks"} authority={authority} writesAvailable={writesAvailable} connectionError={connectionError} verifiedAtMs={verifiedAtMs}
        location={location} origin={origin} go={go}
        listNavigation={listNavigation} listCollapsed={listCollapsed}
        onCollapseList={() => setListCollapsed(true)} onExpandList={() => setListCollapsed(false)} />
    </div>
    <div className="history-view" hidden={view !== "records"}>
      <Tasks snapshot={snapshot} api={api} refresh={refresh} active={active && view === "records"}
        onOpenMicroTask={(macroTaskId, runId) => go({ macroTaskId, runId, step: null }, "push")}
        listNavigation={listNavigation()} listCollapsed={listCollapsed}
        onCollapseList={() => setListCollapsed(true)} onExpandList={() => setListCollapsed(false)} />
    </div>
  </div>;
}

function MacroTasksWorkspace({ snapshot, api, refresh, active, authority, writesAvailable, location, origin, go, listNavigation, listCollapsed, onCollapseList, onExpandList, connectionError, verifiedAtMs }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active: boolean;
  authority?: AuthorityLatch; writesAvailable: boolean; connectionError: string; verifiedAtMs: number | null;
  location: TasksLocation; origin: string | null;
  go: (next: TasksLocation, how: "push" | "replace", from?: string | null) => void;
  listNavigation: (loaded?: number) => ReactNode; listCollapsed: boolean; onCollapseList: () => void; onExpandList: () => void;
}) {
  const { t } = useTranslation(["tasks", "shell"]);
  const [filter, setFilter] = useState<MacroTaskFilter>("all");
  const [query, setQuery] = useState("");
  const selected = location.macroTaskId;
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => setMenuOpen(false), [active, selected]);
  const detailRunId = selected ? location.runId : null;
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(max-width: 760px)");
    const changed = () => setNarrow(media.matches);
    changed(); media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  // The pinned inspector selection, kept per macroTask so switching back restores it.
  const [selectionByMacroTask, setSelectionByMacroTask] = useState<Map<string, InspectorSelection>>(() => new Map());
  const list = useMacroTaskList(api, { query, filter }, active);
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

  let stopControl: ReactNode = null;
  if (selectedSummary) {
    const stopping = stopEntry?.phase === "stopping";
    const scope = stopScope(selectedSummary);
    const reason = !stop.writable ? t("readonly") : stopping ? t("stopping") : scope.roots + scope.helpers === 0 ? t("shell:macroStop.noActive") : undefined;
    stopControl = <div className="macro-stop-menu">
      {stopState && <Hint text={stopState.detail}><span className={`macro-stop-state stop-${stopState.phase}`} role="status">{stopState.label}</span></Hint>}
      {stopState?.phase === "unknown" && stop.writable && !stopping && <Hint text={t("shell:macroStop.retryHint")}><button type="button" className="button small-button" onClick={() => stop.retryStop(selectedSummary)}>{t("shell:macroStop.retry")}</button></Hint>}
      {stopState?.phase === "refused" && <button type="button" className="button small-button" onClick={() => stop.dismissStop(selectedSummary.macroTaskId)}>{t("shell:macroStop.dismiss")}</button>}
      <Popover open={menuOpen && active} onOpenChange={setMenuOpen}><PopoverTrigger asChild><button type="button" className="macro-menu-button" aria-label={t("more")}>⋯</button></PopoverTrigger>
        <PopoverContent align="end" className="macro-menu-content p-1 w-40 rounded-none">
          <Hint text={reason}><button type="button" className="macro-stop-action" aria-label={t("shell:macroStop.title")} aria-disabled={!!reason} onClick={() => { if (!reason) { setMenuOpen(false); stop.requestStop(selectedSummary, scope); } }}>{t("stop")}</button></Hint>
        </PopoverContent>
      </Popover>
    </div>;
  }
  const listPane = !listCollapsed && <MacroTaskList
    rows={list.rows} totals={list.totals} loading={list.loading} error={list.error} verifiedAtMs={list.verifiedAtMs}
    nextCursor={list.nextCursor} reorder={list.reorder} filter={filter} query={query} selected={selected}
    active={active} visible={active && !(narrow && !!selected)} listNavigation={listNavigation(list.rows.length)} onCollapse={onCollapseList}
    onFilterChange={setFilter} onQueryChange={setQuery} onSelect={selectMacroTask}
    onRetry={list.retry} onMore={list.more} onApplyReorder={list.applyReorder} />;

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
    listToggle={listCollapsed ? <ListFold label={t("expand")} onClick={onExpandList}/> : undefined}
    buddies={snapshot.buddies}
    onSelectItem={selectItem}
    onOpenItem={openItem}
    onSelectRun={selectRun}
    onOpenRun={runId => openRun(runId)}
    onClearSelection={() => setSelection(null)}
    onRetry={() => void timeline.retry()}
    onBackToList={() => selectMacroTask(null)} /> : null;

  const disconnected = !!connectionError || !!list.error;
  const lastRead = list.verifiedAtMs ?? verifiedAtMs;
  const empty = !list.loading && !list.error && list.totals.all === 0 && !query && filter === "all";
  const detailPane = <aside className="panel detail-panel" aria-label={t("detail")}>
    {selected
      ? <div className="right-stage"><div className="right-stage-pane">{timelinePane}</div></div>
      : <>
        {listCollapsed && <div className="macro-empty-head"><ListFold label={t("expand")} onClick={onExpandList}/>{listNavigation(list.rows.length)}</div>}
        <div className="macro-first-use">{empty && <svg width="40" height="40" viewBox="3 3 58 58" className="macro-empty-mark" aria-hidden="true"><polygon points="12,13 22,13 22,61 12,54"/><polygon points="22,32 30,28 30,38 22,42"/><polygon points="42,3 52,10 52,51 42,51"/><polygon points="34,26 42,22 42,32 34,36"/><polygon points="30,28 34,26 34,36 30,38"/></svg>}<h2>{t(empty ? "emptyTitle" : "choose")}</h2>
          {empty && <><p>{t("emptyBody")}</p><div className="ready"><span>{t("harnesses", { n: formatNumber((snapshot.harnesses ?? []).filter(h => h.status === "ready").length) })}</span><span>{t("buddies", { n: formatNumber(snapshot.buddies.filter(b => b.enabled).length) })}</span><a href="#buddies">{t("buddy")}</a></div></>}
        </div>
      </>}
  </aside>;
  return <>
    <div className="workspace-wrap macro-shell">
      {disconnected && <ConnectionStrip error={connectionError || list.error} verifiedAtMs={lastRead} retry={() => { if (connectionError) void refresh(); else list.retry(); }}/>}
      <div className={`macro-workspace${selected ? " has-selection" : ""}${listCollapsed ? " list-collapsed" : ""}${empty ? " is-empty" : ""}`}>{listPane}{detailPane}</div>
    </div>
    {stop.confirming && <StopDialog summary={stop.confirming.summary} scope={stop.confirming.scope}
      onConfirm={() => stop.confirmStop(stop.confirming!.summary)}
      onCancel={stop.cancelConfirm} />}
  </>;
}
