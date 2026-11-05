import { Cell, Grid, Gridline, useGridRef } from "react-resizable-panels";
import { cn } from "cn";
import { Button } from "./components/ui/button";
import { Collapsible, CollapsibleContent } from "./components/ui/collapsible";
import { memo, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { MacroTaskFilter, MacroTaskSummary } from "./macro-task-types";



import { ListFold, MacroListHeading } from "./MacroTaskList";
import { Hint } from "./components/hint";
import { MacroTaskEntryMenu } from "./MacroTaskEntryMenu";
import { Dialog, DialogContent, DialogTitle, DialogDescription } from "./components/ui/dialog";
import { StateMark } from "./components/ui/state-mark";
import { formatClockMinutes, formatNumber } from "./i18n/format";
import { MacroTaskList } from "./MacroTaskList";
import { TaskWork } from "./timeline/TaskWork";
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
import { MacroTaskDeleteDialog } from "./MacroTaskDeleteDialog";

import { useNumberViewPreference, useViewPreference } from "./view-preferences";


function ConnectionStrip({ error, verifiedAtMs, retry, busy }: { error: string; verifiedAtMs: number | null; retry: () => void; busy: boolean }) {
  const { t } = useTranslation("tasks");
  return <div data-slot="macro-offline" className="flex min-h-9 shrink-0 flex-wrap items-center gap-x-2.5 gap-y-1 border-b bg-fail-wash px-4 py-1 text-sm sm:px-6" role="alert"><StateMark kind="fail"/><Hint text={error}><span tabIndex={0}>{t("offline")}</span></Hint>
    {verifiedAtMs !== null && <span className="text-xs text-ink2 tabular-nums">{t("lastRead", { time: formatClockMinutes(verifiedAtMs) })}</span>}
    <Button variant="outline" size="sm" className="ml-auto" aria-busy={busy} disabled={busy} onClick={retry}>{t(busy ? "loading" : "retry")}</Button>
  </div>;
}

/** The macroTask stop's explicit confirmation: names the macroTask and its scope. */
function StopDialog({ summary, scope, onConfirm, onCancel, returnFocus }: {
  summary: MacroTaskSummary;
  scope: StopScope;
  onConfirm: () => void;
  onCancel: () => void;
  returnFocus: RefObject<HTMLButtonElement | null>;
}) {
  const { t } = useTranslation(["shell", "tasks"]);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const title = summary.titleSource === "none" ? t("tasks:unnamed") : displayTitle(summary.titleSource, summary.title).text;
  return <Dialog open onOpenChange={open => { if (!open) onCancel(); }}>
    <DialogContent className="text-sm" onCloseAutoFocus={event => { event.preventDefault(); returnFocus.current?.focus(); }} onOpenAutoFocus={event => { event.preventDefault(); cancelRef.current?.focus(); }} aria-describedby="stop-macro-task-name stop-macro-task-body stop-macro-task-counts">
      <DialogTitle>{t("macroStop.title")}</DialogTitle>
      <p id="stop-macro-task-name" className="wrap-anywhere text-sm font-medium">{summary.titleSource === "none" ? title : summary.title}</p>
      <DialogDescription id="stop-macro-task-body">{t("macroStop.scope")}</DialogDescription>
      <ul id="stop-macro-task-counts" className="grid gap-1 text-sm tabular-nums">
        <li>{t("macroStop.scopeRoots", { count: scope.roots })}</li>
        <li>{t("macroStop.scopeHelpers", { count: scope.helpers })}</li>
        <li>{t("macroStop.scopeDelivered", { count: scope.delivered })}</li>
        <li>{t("macroStop.scopeAwaitingHost", { count: scope.awaitingHost })}</li>
        <li>{t("macroStop.scopeAccepted", { count: summary.counts.accepted })}</li>
      </ul>
      <div data-slot="stop-dialog-actions" className="flex justify-end gap-2">
        <Button ref={cancelRef} variant="outline" onClick={onCancel}>{t("macroStop.cancel")}</Button>
        <Button variant="destructive" onClick={onConfirm}>{t("macroStop.confirm")}</Button>
      </div>
    </DialogContent>
  </Dialog>;
}

/**
 * Where the page is (a macro task, one of its micro tasks, a step of it) is the
 * address, and the address is the only place it lives: choosing a macro task,
 * opening a micro task and going back to the timeline each add a step to the
 * browser's history, and choosing a step of a micro task only replaces the one
 * it is on.
 */
export const MacroTasks = memo(function MacroTasks({ snapshot, api, refresh, active = true, authority, writesAvailable = true, onAddress, connectionError = "", verifiedAtMs = null }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
  authority?: AuthorityLatch; writesAvailable?: boolean;
  /** Told where the page is, so that the link back to the tasks leads to the same place. */
  onAddress?: (hash: string) => void;
  connectionError?: string; verifiedAtMs?: number | null;
}) {
  const { t } = useTranslation("tasks");

  const [listCollapsed, setListCollapsed] = useViewPreference("macro-task-list-collapsed");
  const [location, setLocation] = useState<TasksLocation>(() => tasksLocation(window.location.hash) ?? TASKS_ROOT);
  // The one focused item, shared by the detail strip and its overview.
  const [origin, setOrigin] = useState<string | null>(null);
  function go(next: TasksLocation, how: "push" | "replace", from: string | null = null) {
    const hash = tasksHash(next);
    const state = window.history.state;
    if (window.location.hash !== hash) (how === "push" ? window.history.pushState : window.history.replaceState).call(window.history, state, "", hash);
    setOrigin(from);
    setLocation(next);
    onAddress?.(hash);
  }
  useEffect(() => {
    function canonicalAddress(next: TasksLocation) {
      const hash = tasksHash(next);
      if (window.location.hash !== hash) window.history.replaceState(window.history.state, "", hash);
      onAddress?.(hash);
    }
    if (tasksLocation(window.location.hash)) canonicalAddress(location);
    const changed = () => {
      const next = tasksLocation(window.location.hash);
      if (!next) return;
      setOrigin(null);
      setLocation(next);
      canonicalAddress(next);
    };
    // A step back or forward through the history, and a hand-written address, both land here.
    window.addEventListener("hashchange", changed);
    window.addEventListener("popstate", changed);
    return () => { window.removeEventListener("hashchange", changed); window.removeEventListener("popstate", changed); };
  }, []);
  return <MacroTasksWorkspace snapshot={snapshot} api={api} refresh={refresh}
    active={active} authority={authority} writesAvailable={writesAvailable} connectionError={connectionError} verifiedAtMs={verifiedAtMs}
    location={location} origin={origin} go={go} listCollapsed={listCollapsed}
    onCollapseList={() => setListCollapsed(true)} onExpandList={() => setListCollapsed(false)} />;
});

function MacroTasksWorkspace({ snapshot, api, refresh, active, authority, writesAvailable, location, origin, go, listCollapsed, onCollapseList: collapseList, onExpandList: expandList, connectionError, verifiedAtMs }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active: boolean;
  authority?: AuthorityLatch; writesAvailable: boolean; connectionError: string; verifiedAtMs: number | null;
  location: TasksLocation; origin: string | null;
  go: (next: TasksLocation, how: "push" | "replace", from?: string | null) => void;
  listCollapsed: boolean; onCollapseList: () => void; onExpandList: () => void;
}) {
  const { t } = useTranslation(["tasks", "shell", "macroHeader", "taskDetail"]);
  const [folding, setFolding] = useState(false);
  const onCollapseList = () => { setFolding(true); collapseList(); };
  const onExpandList = () => { setFolding(true); expandList(); };
  useEffect(() => {
    if (!folding) return;
    const timer = window.setTimeout(() => setFolding(false), 200);
    return () => window.clearTimeout(timer);
  }, [folding, listCollapsed]);
  const [filter, setFilter] = useState<MacroTaskFilter>("all");
  const [query, setQuery] = useState("");
  const selected = location.macroTaskId;
  const [showNarrowList, setShowNarrowList] = useState(!selected);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const [deleteTarget, setDeleteTarget] = useState<MacroTaskSummary | null>(null);
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => new Set());
  const [deletionNotice, setDeletionNotice] = useState<{ macroTaskId: string; title: string | null; refreshFailed: boolean } | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);
  const live = useRef(false), selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => { if (deletionNotice) noticeRef.current?.focus(); }, [deletionNotice?.macroTaskId]);
  const detailRunId = selected ? location.runId : null;
  useEffect(() => { if (detailRunId) setShowNarrowList(false); }, [detailRunId]);
  const [narrow, setNarrow] = useState(() => window.matchMedia?.("(max-width: 760px)").matches ?? false);
  const [sidebarWidth, setSidebarWidth] = useNumberViewPreference("macro-task-list-width", 288);
  const workspaceGrid = useGridRef();
  // Gridline exposes its DOM ref but does not forward HTML attributes.
  const resizeHandleRef = useCallback((element: HTMLDivElement | null) => {
    if (element) {
      element.setAttribute("aria-label", t("tasks:resizeList"));
      element.dataset.slot = "macro-sidebar-resize";
    }
  }, [t]);
  useEffect(() => {
    if (!narrow) workspaceGrid.current!.getTrackById("column", "sidebar").resize(listCollapsed ? "0px" : `${sidebarWidth}px`);
  }, [listCollapsed, narrow]);
  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(max-width: 760px)");
    const changed = () => setNarrow(media.matches);
    changed(); media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  const list = useMacroTaskList(api, { query, filter }, active);
  const rows = list.rows.filter(row => !deletedIds.has(row.macroTaskId));
  const groups = useMemo(() => list.groups.map(group => {
    const macroTasks = group.macroTasks.filter(row => !deletedIds.has(row.macroTaskId));
    return { ...group, macroTasks, total: group.total - (group.macroTasks.length - macroTasks.length) };
  }).filter(group => group.total > 0), [list.groups, deletedIds]);
  const totals = { ...list.totals };
  for (const row of list.rows) if (deletedIds.has(row.macroTaskId)) {
    --totals.all;
    if (row.state !== "ended" && row.state !== "empty") --totals[row.state];
  }
  const timeline = useMacroTaskTimeline(api, selected && !deletedIds.has(selected) ? selected : null, active);
  useEffect(() => {
    if (!active) return;
    if (selected && deletedIds.has(selected)) go(TASKS_ROOT, "replace");
    else if (!selected && rows.length) go({ macroTaskId: rows[0].macroTaskId, runId: null, step: null }, "replace");
  }, [active, selected, list.rows, deletedIds]);
  const detailClient = useMemo(() => createTaskDetailClient(api, snapshot.csrfToken), [api, snapshot.csrfToken]);
  // The list row is freshest, but a list refresh must never unmount an open
  // detail: the timeline read's summary covers the gap, and a snapshot of the
  // last seen summary covers the moments before the timeline read lands.
  const listedSummary = rows.find(row => row.macroTaskId === selected)
    ?? (timeline.timeline?.macroTask.macroTaskId === selected ? timeline.timeline.macroTask : null)
    ?? null;
  const [summarySnapshot, setSummarySnapshot] = useState<MacroTaskSummary | null>(null);
  useEffect(() => {
    if (listedSummary) setSummarySnapshot(listedSummary);
    else if (!selected) setSummarySnapshot(null);
  }, [listedSummary, selected]);
  const selectedSummary = listedSummary
    ?? (summarySnapshot?.macroTaskId === selected ? summarySnapshot : null);
  // MacroTask-level stop, with its explicit
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

  const deleted = (macroTaskId: string, summary: MacroTaskSummary) => {
    if (!live.current) return;
    const title = summary.titleSource === "none" ? null : displayTitle(summary.titleSource, summary.title).text;
    setDeletedIds(previous => new Set(previous).add(macroTaskId));
    setDeleteTarget(previous => previous?.macroTaskId === macroTaskId ? null : previous);
    setDeletionNotice({ macroTaskId, title, refreshFailed: false });
    list.reset();
    if (selectedRef.current === macroTaskId) {
      go(TASKS_ROOT, "replace");
      setShowNarrowList(true);
      if (listCollapsed) onExpandList();
    }
    const markRefreshFailure = () => {
      if (live.current) setDeletionNotice(previous => previous?.macroTaskId === macroTaskId ? { ...previous, refreshFailed: true } : previous);
    };
    void refresh().then(result => { if (!result) markRefreshFailure(); }, markRefreshFailure);
  };

  const selectMacroTask = (macroTaskId: string) => { setShowNarrowList(false); go({ macroTaskId, runId: null, step: null }, "push"); };
  const openRun = (runId: string, from: string | null = null) => go({ macroTaskId: selected, runId, step: null }, "push", from);
  const backToTimeline = () => go({ macroTaskId: selected, runId: null, step: null }, "push");

  const stopControl = stopState && <Hint text={stopState.detail}><span data-slot="macro-stop-state" tabIndex={0} className={cn("shrink-0 whitespace-nowrap text-xs", stopState.phase === "refused" ? "text-fail" : stopState.phase === "stopping" || stopState.phase === "in-progress" ? "text-foreground state-breathe" : stopState.phase === "stopped" || stopState.phase === "acknowledged" ? "text-past" : "text-hand")} role="status">{stopState.label}</span></Hint>;
  const entryActions = (summary: MacroTaskSummary) => <MacroTaskEntryMenu summary={summary} entry={stop.entries.get(summary.macroTaskId)} writable={stop.writable} active={active}
    onStop={(row, button) => { menuButtonRef.current = button; stop.requestStop(row, stopScope(row)); }}
    onRetry={stop.retryStop} onDismiss={stop.dismissStop}
    onDelete={(row, button) => { menuButtonRef.current = button; setDeleteTarget(row); }}/>;
  const listPane = <MacroTaskList
    groups={groups} totals={totals} loading={list.loading} error={list.error}
    reorder={list.reorder} filter={filter} query={query} sortFor={list.sortFor} selected={selected} actions={entryActions}
    active={active} visible={active && !listCollapsed && (!narrow || showNarrowList)} onCollapse={onCollapseList}
    onSortChange={list.changeSort}
    onFilterChange={setFilter} onQueryChange={setQuery} onSelect={selectMacroTask}
    onMore={list.more} onApplyReorder={list.applyReorder} />;

  const timelinePane = selected ? <TaskWork key={selected} readGoals={api.macroTaskGoals}
    active={active} summary={selectedSummary} timeline={timeline.timeline}
    displayObservedAt={timeline.displayObservedAt} loading={timeline.loading} error={timeline.error}
    stale={timeline.stale} newRunIds={timeline.newRunIds} openedRunId={detailRunId}
    detail={runId => <TaskDetail key={runId} client={detailClient} runId={runId}
      timeline={timeline.timeline} timelineLoading={timeline.loading && !timeline.timeline}
      nowMs={timeline.displayObservedAt ? Date.parse(timeline.displayObservedAt) : null}
      step={location.step} origin={origin} active={active} sessionClient={api} tab={location.tab ?? "overview"}
      onTab={(tab, replace) => { const { tab: _tab, ...next } = location; go({ ...next, ...(tab === "session" ? { tab: "session" } : {}) }, replace ? "replace" : "push", origin); }}
      onStep={(step, item) => {
        const { tab: _tab, ...overview } = location;
        go({ ...(item === undefined ? location : overview), macroTaskId: selected, runId, step }, "replace", item ?? origin);
      }}
      onBack={backToTimeline} onOpenRun={next => openRun(next)} />}
    headerActions={<>{stopControl}{selectedSummary && entryActions(selectedSummary)}</>} listToggle={listCollapsed ? <ListFold label={t("expand")} onClick={onExpandList}/> : undefined}
    buddies={snapshot.buddies} onOpen={(runId, key) => openRun(runId, key)} onClose={backToTimeline}
    onRetry={() => void timeline.retry()}
    onBackToList={() => { setShowNarrowList(true); if (listCollapsed) onExpandList(); }} /> : null;

  const disconnected = !!connectionError || !!list.error;
  const lastRead = list.verifiedAtMs ?? verifiedAtMs;
  const empty = !list.loading && !list.error && totals.all === 0 && !query && filter === "all";
  const detailPane = <aside data-slot="macro-detail" className={cn("flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-background", !listCollapsed && showNarrowList && !empty && "max-[760px]:hidden")} aria-label={t("detail")}>
    {selected
      ? <div className="flex min-h-0 flex-1 flex-col"><div className="flex min-h-0 min-w-0 flex-1 flex-col [container-type:inline-size]">{timelinePane}</div></div>
      : <>
        {listCollapsed && <div className="flex gap-3 px-4 py-3.5"><ListFold label={t("expand")} onClick={onExpandList}/><MacroListHeading count={totals.all}/></div>}
        <div data-slot="macro-first-use" className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 bg-background px-6 pt-6 pb-26 text-center">{empty && <svg width="40" height="40" viewBox="3 3 58 58" className="fill-ink3 opacity-35" aria-hidden="true"><polygon points="12,13 22,13 22,61 12,54"/><polygon points="22,32 30,28 30,38 22,42"/><polygon points="42,3 52,10 52,51 42,51"/><polygon points="34,26 42,22 42,32 34,36"/><polygon points="30,28 34,26 34,36 30,38"/></svg>}<h2 className="text-base font-semibold">{t(empty ? "emptyTitle" : "choose")}</h2>
          {empty && <><p className="text-sm text-ink2">{t("emptyBody")}</p><div className="mt-4.5 flex flex-wrap justify-center gap-4.5 border-t pt-3.5 text-xs text-ink3 tabular-nums"><span>{t("harnesses", { n: formatNumber((snapshot.harnesses ?? []).filter(h => h.status === "ready").length) })}</span><span>{t("buddies", { n: formatNumber(snapshot.buddies.filter(b => b.enabled).length) })}</span><a href="#buddies" className="text-foreground underline">{t("buddy")}</a></div></>}
        </div>
      </>}
  </aside>;
  return <>
    <div className="flex h-full min-h-0 flex-col">
      {deletionNotice && <div ref={noticeRef} tabIndex={-1} data-slot="macro-delete-result" className="flex shrink-0 flex-wrap items-center gap-3 border-b bg-card px-4 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-6">
        <p role="status" className="min-w-0 flex-1 wrap-anywhere">{t("macroHeader:deletion.deleted", { title: deletionNotice.title ?? t("macroHeader:unnamed") })}</p>
        {(deletionNotice.refreshFailed || list.error) && <p role="alert" className="text-fail">{t("macroHeader:deletion.refreshFailed")}</p>}
        <Button variant="ghost" size="sm" onClick={() => setDeletionNotice(null)}>{t("macroHeader:deletion.dismiss")}</Button>
      </div>}
      {disconnected && <ConnectionStrip busy={!connectionError && list.loading} error={connectionError || list.error} verifiedAtMs={lastRead} retry={() => { if (connectionError) void refresh(); else list.retry(); }}/>}
      <Collapsible open={!listCollapsed} onOpenChange={open => open ? onExpandList() : onCollapseList()} className="flex min-h-0 flex-1 flex-col">
        {narrow ? <div data-slot="macro-workspace" className="flex min-h-0 flex-1 flex-col">
          <CollapsibleContent data-slot="macro-sidebar" className={cn("h-full min-h-0 min-w-0 w-full", !showNarrowList && !empty && "hidden", empty && "h-auto shrink-0")}>{listPane}</CollapsibleContent>
          {detailPane}
        </div> : <Grid data-slot="macro-workspace" gridRef={workspaceGrid}
          className={cn("min-h-0 flex-1", folding && "transition-[grid-template-columns] duration-200 motion-reduce:transition-none")}
          columns={[{ id: "sidebar", defaultSize: listCollapsed ? "0px" : `${sidebarWidth}px`, minSize: listCollapsed ? "0px" : "288px", maxSize: listCollapsed ? "0px" : "50vw", groupResizeBehavior: "preserve-pixel-size" }, { id: "detail", minSize: "0px" }]}
          rows={1} resizeTargetMinimumSize={{ fine: 12, coarse: 24 }}
          onLayoutChanged={(_, meta) => { if (meta.isUserInteraction) setSidebarWidth(workspaceGrid.current!.getTrackById("column", "sidebar").getSize().inPixels); }}>
          <Cell row={0} column={0} className="min-h-0 min-w-0 overflow-hidden">
            <CollapsibleContent data-slot="macro-sidebar" className="h-full min-h-0 min-w-0 w-full">{listPane}</CollapsibleContent>
          </Cell>
          <Gridline type="column" column={1} disabled={listCollapsed} elementRef={resizeHandleRef} className={cn("sidebar-resize", listCollapsed ? "invisible w-0" : "w-px")}/>
          <Cell row={0} column={1} className="min-h-0 min-w-0 overflow-hidden">{detailPane}</Cell>
        </Grid>}
      </Collapsible>
    </div>
    {stop.confirming && <StopDialog summary={stop.confirming.summary} scope={stop.confirming.scope}
      onConfirm={() => stop.confirmStop(stop.confirming!.summary)}
      onCancel={stop.cancelConfirm} returnFocus={menuButtonRef} />}
    {deleteTarget && <MacroTaskDeleteDialog key={deleteTarget.macroTaskId} summary={deleteTarget}
      open={active} returnFocus={menuButtonRef}
      api={api} snapshot={snapshot} authority={authority} writesAvailable={writesAvailable && !connectionError}
      onClose={() => setDeleteTarget(null)} onDeleted={id => deleted(id, deleteTarget)}/>}

  </>;
}
