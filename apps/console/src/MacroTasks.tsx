import { cn } from "cn";
import { Button } from "./components/ui/button";
import { Collapsible, CollapsibleContent } from "./components/ui/collapsible";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { MacroTaskFilter, MacroTaskSummary } from "./macro-task-types";



import { ListFold } from "./MacroTaskList";
import { Hint } from "./components/hint";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
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

import { useViewPreference } from "./view-preferences";


function ConnectionStrip({ error, verifiedAtMs, retry }: { error: string; verifiedAtMs: number | null; retry: () => void }) {
  const { t } = useTranslation("tasks");
  return <div data-slot="macro-offline" className="flex min-h-9 shrink-0 flex-wrap items-center gap-x-2.5 gap-y-1 border-b bg-fail-wash px-4 py-1 text-sm sm:px-6" role="alert"><StateMark kind="fail"/><Hint text={error}><span tabIndex={0}>{t("offline")}</span></Hint>
    {verifiedAtMs !== null && <span className="text-xs text-ink2 tabular-nums">{t("lastRead", { time: formatClockMinutes(verifiedAtMs) })}</span>}
    <Button variant="outline" size="sm" className="ml-auto" onClick={retry}>{t("retry")}</Button>
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
    <DialogContent className="rounded-none text-sm" onOpenAutoFocus={event => { event.preventDefault(); cancelRef.current?.focus(); }} aria-describedby="stop-macro-task-name stop-macro-task-body stop-macro-task-counts">
      <DialogTitle>{t("macroStop.title")}</DialogTitle>
      <Hint text={title}><p id="stop-macro-task-name" tabIndex={0} className="truncate text-sm font-medium">{title}</p></Hint>
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
export function MacroTasks({ snapshot, api, refresh, active = true, authority, writesAvailable = true, onAddress, connectionError = "", verifiedAtMs = null }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
  authority?: AuthorityLatch; writesAvailable?: boolean;
  /** Told where the page is, so that the link back to the tasks leads to the same place. */
  onAddress?: (hash: string) => void;
  connectionError?: string; verifiedAtMs?: number | null;
}) {
  const { t } = useTranslation("tasks");

  const [listCollapsed, setListCollapsed] = useViewPreference("macro-task-list-collapsed");
  const [location, setLocation] = useState<TasksLocation>(() => tasksLocation(window.location.hash) ?? TASKS_ROOT);
  // The timeline item the open detail came from; it names the step the detail opens on.
  const [origin, setOrigin] = useState<string | null>(null);
  function go(next: TasksLocation, how: "push" | "replace", from: string | null = null) {
    next = { ...next, sort: next.sort ?? location.sort };
    const hash = tasksHash(next);
    const state = window.history.state;
    if (window.location.hash !== hash) (how === "push" ? window.history.pushState : window.history.replaceState).call(window.history, state, "", hash);
    setOrigin(from);
    setLocation(next);
    onAddress?.(hash);
  }
  useEffect(() => {
    onAddress?.(tasksHash(location));
    const changed = () => {
      const next = tasksLocation(window.location.hash);
      if (!next) return;
      setOrigin(null);
      setLocation(next);
      onAddress?.(tasksHash(next));
    };
    // A step back or forward through the history, and a hand-written address, both land here.
    window.addEventListener("hashchange", changed);
    window.addEventListener("popstate", changed);
    return () => { window.removeEventListener("hashchange", changed); window.removeEventListener("popstate", changed); };
  }, []);
  const listNavigation = () => <h2 data-slot="macro-list-heading" className="text-base font-semibold">{t("macro")}</h2>;
  return <MacroTasksWorkspace snapshot={snapshot} api={api} refresh={refresh}
    active={active} authority={authority} writesAvailable={writesAvailable} connectionError={connectionError} verifiedAtMs={verifiedAtMs}
    location={location} origin={origin} go={go} listNavigation={listNavigation} listCollapsed={listCollapsed}
    onCollapseList={() => setListCollapsed(true)} onExpandList={() => setListCollapsed(false)} />;
}

function MacroTasksWorkspace({ snapshot, api, refresh, active, authority, writesAvailable, location, origin, go, listNavigation, listCollapsed, onCollapseList, onExpandList, connectionError, verifiedAtMs }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active: boolean;
  authority?: AuthorityLatch; writesAvailable: boolean; connectionError: string; verifiedAtMs: number | null;
  location: TasksLocation; origin: string | null;
  go: (next: TasksLocation, how: "push" | "replace", from?: string | null) => void;
  listNavigation: () => ReactNode; listCollapsed: boolean; onCollapseList: () => void; onExpandList: () => void;
}) {
  const { t } = useTranslation(["tasks", "shell", "macroHeader", "taskDetail"]);
  const [filter, setFilter] = useState<MacroTaskFilter>("all");
  const [query, setQuery] = useState("");
  const sort = location.sort ?? "activity-desc";
  const selected = location.macroTaskId;
  const [showNarrowList, setShowNarrowList] = useState(!selected);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const deletionOpening = useRef(false);
  const [deleteTarget, setDeleteTarget] = useState<MacroTaskSummary | null>(null);
  const [deletedIds, setDeletedIds] = useState<Set<string>>(() => new Set());
  const [deletionNotice, setDeletionNotice] = useState<{ macroTaskId: string; title: string | null; skipped: number; refreshFailed: boolean } | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);
  const live = useRef(false), selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => { if (deletionNotice) noticeRef.current?.focus(); }, [deletionNotice?.macroTaskId]);
  useEffect(() => setMenuOpen(false), [active, selected]);
  const detailRunId = selected ? location.runId : null;
  useEffect(() => { if (detailRunId) setShowNarrowList(false); }, [detailRunId]);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (!window.matchMedia) return;
    const media = window.matchMedia("(max-width: 760px)");
    const changed = () => setNarrow(media.matches);
    changed(); media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  const list = useMacroTaskList(api, { query, filter, sort }, active);
  const rows = list.rows.filter(row => !deletedIds.has(row.macroTaskId));
  const totals = { ...list.totals };
  for (const row of list.rows) if (deletedIds.has(row.macroTaskId)) {
    --totals.all;
    if (row.state !== "ended") --totals[row.state];
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

  const deleted = (macroTaskId: string, skipped: number, summary: MacroTaskSummary) => {
    if (!live.current) return;
    const title = summary.titleSource === "none" ? null : displayTitle(summary.titleSource, summary.title).text;
    setDeletedIds(previous => new Set(previous).add(macroTaskId));
    setDeleteTarget(previous => previous?.macroTaskId === macroTaskId ? null : previous);
    setDeletionNotice({ macroTaskId, title, skipped, refreshFailed: false });
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

  let stopControl: ReactNode = null;
  if (selectedSummary) {
    const stopping = stopEntry?.phase === "stopping";
    const scope = stopScope(selectedSummary);
    const reason = !stop.writable ? t("readonly") : stopping ? t("stopping") : scope.roots + scope.helpers === 0 ? t("shell:macroStop.noActive") : undefined;
    stopControl = <div data-slot="macro-stop-menu" className="flex items-center gap-2">
      {stopState && <Hint text={stopState.detail}><span data-slot="macro-stop-state" tabIndex={0} className={cn("text-xs", stopState.phase === "refused" ? "text-fail" : stopState.phase === "stopping" || stopState.phase === "in-progress" ? "text-foreground state-breathe" : stopState.phase === "stopped" || stopState.phase === "acknowledged" ? "text-past" : "text-hand")} role="status">{stopState.label}</span></Hint>}
      {stopState?.phase === "unknown" && stop.writable && !stopping && <Hint text={t("shell:macroStop.retryHint")}><Button variant="outline" size="sm" onClick={() => stop.retryStop(selectedSummary)}>{t("shell:macroStop.retry")}</Button></Hint>}
      {stopState?.phase === "refused" && <Button variant="outline" size="sm" onClick={() => stop.dismissStop(selectedSummary.macroTaskId)}>{t("shell:macroStop.dismiss")}</Button>}
      <Popover open={menuOpen && active} onOpenChange={setMenuOpen}><Hint text={t("more")}><PopoverTrigger asChild><Button ref={menuButtonRef} data-slot="macro-menu-button" variant="outline" size="icon" aria-label={t("more")}>⋯</Button></PopoverTrigger></Hint>
        <PopoverContent align="end" className="w-48 rounded-none p-1" onCloseAutoFocus={event => { if (deletionOpening.current) { event.preventDefault(); deletionOpening.current = false; } }}>
          <Hint text={reason}><Button data-slot="macro-stop-action" variant="ghost" className="w-full justify-start" aria-label={t("shell:macroStop.title")} aria-disabled={!!reason} onClick={() => { if (!reason) { setMenuOpen(false); stop.requestStop(selectedSummary, scope); } }}>{t("stop")}</Button></Hint>
          <div className="my-1 border-t"/>
          <Button data-slot="macro-delete-action" variant="ghost" className="w-full justify-start text-fail" onClick={() => { deletionOpening.current = true; setMenuOpen(false); setDeleteTarget(selectedSummary); }}>{t("macroHeader:deletion.action")}</Button>
        </PopoverContent>
      </Popover>
    </div>;
  }
  const listPane = <MacroTaskList
    rows={rows} totals={totals} loading={list.loading} error={list.error}
    nextCursor={list.nextCursor} reorder={list.reorder} filter={filter} query={query} sort={sort} selected={selected}
    active={active} visible={active && !listCollapsed && (!narrow || showNarrowList)} listNavigation={listNavigation()} onCollapse={onCollapseList}
    onSortChange={sort => go({ ...location, sort }, "push", origin)}
    onFilterChange={setFilter} onQueryChange={setQuery} onSelect={selectMacroTask}
    onMore={list.more} onApplyReorder={list.applyReorder} />;

  const timelinePane = selected ? <TaskWork key={selected}
    active={active} summary={selectedSummary} timeline={timeline.timeline}
    displayObservedAt={timeline.displayObservedAt} loading={timeline.loading} error={timeline.error}
    stale={timeline.stale} newRunIds={timeline.newRunIds} openedRunId={detailRunId}
    detail={runId => <TaskDetail key={runId} client={detailClient} runId={runId}
      timeline={timeline.timeline} timelineLoading={timeline.loading && !timeline.timeline}
      nowMs={timeline.displayObservedAt ? Date.parse(timeline.displayObservedAt) : null}
      step={location.step} origin={origin} active={active} sessionClient={api} tab={location.tab ?? "overview"}
      onTab={(tab, replace) => { const { tab: _tab, ...next } = location; go({ ...next, ...(tab === "session" ? { tab: "session" } : {}) }, replace ? "replace" : "push", origin); }}
      onStep={step => { go({ ...location, macroTaskId: selected, runId, step }, "replace", origin); }}
      onBack={backToTimeline} onOpenRun={next => openRun(next)} />}
    headerActions={stopControl} listToggle={listCollapsed ? <ListFold label={t("expand")} onClick={onExpandList}/> : undefined}
    buddies={snapshot.buddies} onOpen={(runId, key) => openRun(runId, key)} onClose={backToTimeline}
    onRetry={() => void timeline.retry()}
    onBackToList={() => { setShowNarrowList(true); if (listCollapsed) onExpandList(); }} /> : null;

  const disconnected = !!connectionError || !!list.error;
  const lastRead = list.verifiedAtMs ?? verifiedAtMs;
  const empty = !list.loading && !list.error && totals.all === 0 && !query && filter === "all";
  const detailPane = <aside data-slot="macro-detail" className={cn("col-start-2 flex h-full min-h-0 min-w-0 flex-col overflow-hidden bg-card", !listCollapsed && showNarrowList && !empty && "max-[760px]:hidden")} aria-label={t("detail")}>
    {selected
      ? <div className="flex min-h-0 flex-1 flex-col"><div className="flex min-h-0 min-w-0 flex-1 flex-col [container-type:inline-size]">{timelinePane}</div></div>
      : <>
        {listCollapsed && <div className="flex gap-3 px-4 py-3.5"><ListFold label={t("expand")} onClick={onExpandList}/>{listNavigation()}</div>}
        <div data-slot="macro-first-use" className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 bg-card px-6 pt-6 pb-26 text-center">{empty && <svg width="40" height="40" viewBox="3 3 58 58" className="fill-ink3 opacity-35" aria-hidden="true"><polygon points="12,13 22,13 22,61 12,54"/><polygon points="22,32 30,28 30,38 22,42"/><polygon points="42,3 52,10 52,51 42,51"/><polygon points="34,26 42,22 42,32 34,36"/><polygon points="30,28 34,26 34,36 30,38"/></svg>}<h2 className="text-base font-semibold">{t(empty ? "emptyTitle" : "choose")}</h2>
          {empty && <><p className="text-sm text-ink2">{t("emptyBody")}</p><div className="mt-4.5 flex flex-wrap justify-center gap-4.5 border-t pt-3.5 text-xs text-ink3 tabular-nums"><span>{t("harnesses", { n: formatNumber((snapshot.harnesses ?? []).filter(h => h.status === "ready").length) })}</span><span>{t("buddies", { n: formatNumber(snapshot.buddies.filter(b => b.enabled).length) })}</span><a href="#buddies" className="text-foreground underline">{t("buddy")}</a></div></>}
        </div>
      </>}
  </aside>;
  return <>
    <div className="flex h-full min-h-0 flex-col">
      {deletionNotice && <div ref={noticeRef} tabIndex={-1} data-slot="macro-delete-result" className="flex shrink-0 flex-wrap items-center gap-3 border-b bg-card px-4 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-6">
        <p role="status" className="min-w-0 flex-1 wrap-anywhere">{t(deletionNotice.skipped ? "macroHeader:deletion.skipped" : "macroHeader:deletion.deleted", { title: deletionNotice.title ?? t("macroHeader:unnamed"), n: formatNumber(deletionNotice.skipped) })}</p>
        {deletionNotice.skipped > 0 && <a href="#settings" className="text-foreground underline">{t("macroHeader:deletion.storage")}</a>}
        {(deletionNotice.refreshFailed || list.error) && <p role="alert" className="text-fail">{t("macroHeader:deletion.refreshFailed")}</p>}
        <Button variant="ghost" size="sm" onClick={() => setDeletionNotice(null)}>{t("macroHeader:deletion.dismiss")}</Button>
      </div>}
      {disconnected && <ConnectionStrip error={connectionError || list.error} verifiedAtMs={lastRead} retry={() => { if (connectionError) void refresh(); else list.retry(); }}/>}
      <Collapsible open={!listCollapsed} onOpenChange={open => open ? onExpandList() : onCollapseList()} asChild>
        <div data-slot="macro-workspace" className={cn("grid min-h-0 flex-1 transition-[grid-template-columns] duration-200 motion-reduce:transition-none max-[760px]:flex max-[760px]:flex-col", listCollapsed ? "grid-cols-[0px_minmax(0,1fr)]" : "grid-cols-[316px_minmax(0,1fr)]")}>
          <CollapsibleContent data-slot="macro-sidebar" className={cn("h-full min-h-0 min-w-0 w-full", !showNarrowList && !empty && "max-[760px]:hidden", empty && "max-[760px]:h-auto max-[760px]:shrink-0")}>{listPane}</CollapsibleContent>
          {detailPane}
        </div>
      </Collapsible>
    </div>
    {stop.confirming && <StopDialog summary={stop.confirming.summary} scope={stop.confirming.scope}
      onConfirm={() => stop.confirmStop(stop.confirming!.summary)}
      onCancel={stop.cancelConfirm} />}
    {deleteTarget && <MacroTaskDeleteDialog key={deleteTarget.macroTaskId} summary={deleteTarget}
      open={active && selected === deleteTarget.macroTaskId} returnFocus={menuButtonRef}
      api={api} snapshot={snapshot} authority={authority} writesAvailable={writesAvailable && !connectionError}
      onClose={() => setDeleteTarget(null)} onDeleted={(id, skipped) => deleted(id, skipped, deleteTarget)}/>}

  </>;
}
