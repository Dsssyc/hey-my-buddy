import { useEffect, useMemo, useState } from "react";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { ObjectiveFilter, ObjectiveSummary } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { Tasks } from "./Tasks";
import { RecordDrafts } from "./record-drafts";
import { SplitView } from "./SplitView";
import { ObjectiveList } from "./ObjectiveList";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import { RunDetailPane } from "./RunDetailPane";
import type { DetailTarget } from "./RunDetailPane";
import { useObjectiveList } from "./use-objective-list";
import { useObjectiveTimeline } from "./use-objective-timeline";
import type { AuthorityLatch } from "./console-session";

const EMPTY_SET: ReadonlySet<string> = new Set();

/**
 * Content of the 委派记录 tab. The primary view browses governed delegations
 * as work objectives; the clearly labelled secondary switch keeps the raw,
 * non-governed task history (command and external records included) reachable
 * instead of silently removing it.
 */
export function Objectives({ snapshot, api, refresh, active = true, authority, writesAvailable = true }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active?: boolean;
  authority?: AuthorityLatch; writesAvailable?: boolean;
}) {
  const [view, setView] = useState<"objectives" | "records">("objectives");
  return <div className="history-switch">
    <div className="history-switch-bar">
      <div className="segmented" aria-label="记录视图">
        <button type="button" aria-pressed={view === "objectives"} onClick={() => setView("objectives")}
          title="按工作目标归档浏览受治理的委派树">工作目标</button>
        <button type="button" aria-pressed={view === "records"} onClick={() => setView("records")}
          title="未纳入工作目标的历史执行记录，包括命令与外部记录">全部执行记录</button>
      </div>
      <span className="small muted">工作目标只收录受治理委派；命令与外部记录保留在“全部执行记录”。</span>
    </div>
    <div className="history-view" hidden={view !== "objectives"}>
      <ObjectivesWorkspace snapshot={snapshot} api={api} refresh={refresh}
        active={active && view === "objectives"} authority={authority} writesAvailable={writesAvailable} />
    </div>
    <div className="history-view" hidden={view !== "records"}>
      <Tasks snapshot={snapshot} api={api} refresh={refresh} active={active && view === "records"}
        authority={authority} writesAvailable={writesAvailable} />
    </div>
  </div>;
}

function ObjectivesWorkspace({ snapshot, api, refresh, active, authority, writesAvailable }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active: boolean;
  authority?: AuthorityLatch; writesAvailable: boolean;
}) {
  const [filter, setFilter] = useState<ObjectiveFilter>("all");
  const [query, setQuery] = useState(""), [projectId, setProjectId] = useState(""), [hostId, setHostId] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<DetailTarget | null>(null);
  // The span a detail was last opened from keeps its outline after returning.
  const [lastOpened, setLastOpened] = useState<{ key: string | null; runId: string | null }>({ key: null, runId: null });
  const [locked, setLocked] = useState(false);
  const [expandedByObjective, setExpandedByObjective] = useState<Map<string, Set<string>>>(() => new Map());
  const list = useObjectiveList(api, { query, projectId, hostId, filter }, active);
  const timeline = useObjectiveTimeline(api, selected, active);
  // The list row is freshest, but a list refresh must never unmount an open
  // detail: the timeline read's summary covers the gap, and a snapshot of the
  // last seen summary covers the moments before the timeline read lands.
  const listedSummary = list.rows.find(row => row.objectiveId === selected)
    ?? (timeline.timeline?.objective.objectiveId === selected ? timeline.timeline.objective : null)
    ?? null;
  const [summarySnapshot, setSummarySnapshot] = useState<ObjectiveSummary | null>(null);
  useEffect(() => {
    if (listedSummary) setSummarySnapshot(listedSummary);
    else if (!selected) setSummarySnapshot(null);
  }, [listedSummary, selected]);
  const selectedSummary = listedSummary
    ?? (summarySnapshot?.objectiveId === selected ? summarySnapshot : null);
  const choices = useMemo(() => ({
    projects: [...new Map(list.rows.map(row => [row.project.id, row.project])).values()],
    hosts: [...new Set(list.rows.map(row => row.sourceHostId).filter((id): id is string => !!id))].sort(),
  }), [list.rows]);

  function selectObjective(objectiveId: string | null) {
    // While a detail holds an unconfirmed operation, no selection change — not
    // even re-clicking the selected objective — may unmount it.
    if (locked) return;
    setSelected(objectiveId);
    setDetail(null);
    setLastOpened({ key: null, runId: null });
  }
  function openItem(item: TimelineItem) {
    if (locked) return;
    setDetail({ runId: item.runId, section: item.section, locator: item.locator, key: item.key });
    setLastOpened({ key: item.key, runId: item.runId });
  }
  function backToTimeline() {
    if (locked) return;
    setDetail(null);
  }
  function navigateRun(runId: string) {
    if (locked) return;
    setDetail({ runId });
    setLastOpened({ key: null, runId });
  }
  function toggleGap(gapId: string) {
    setExpandedByObjective(previous => {
      const current = previous.get(selected ?? "") ?? new Set<string>();
      const next = new Set(current);
      if (next.has(gapId)) next.delete(gapId); else next.add(gapId);
      return new Map(previous).set(selected ?? "", next);
    });
  }
  function setExpanded(gapIds: Set<string>) {
    setExpandedByObjective(previous => new Map(previous).set(selected ?? "", gapIds));
  }

  // Esc returns from the detail to the timeline while this tab owns the page,
  // unless the user is typing in a field or a modal dialog owns the keyboard.
  useEffect(() => {
    if (!detail || !active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || locked) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select")) return;
      if (document.querySelector(".dialog-backdrop")) return;
      event.preventDefault();
      setDetail(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [detail, active, locked]);

  const reload = async () => {
    const result = await refresh();
    list.reset();
    void timeline.retry();
    return result;
  };

  const expanded = selected ? expandedByObjective.get(selected) ?? EMPTY_SET : EMPTY_SET;
  const listPane = <ObjectiveList
    rows={list.rows} total={list.total} loading={list.loading} error={list.error}
    nextCursor={list.nextCursor} reorder={list.reorder}
    filter={filter} query={query} projectId={projectId} hostId={hostId} choices={choices}
    selected={selected} locked={locked}
    onFilterChange={setFilter} onQueryChange={setQuery} onProjectChange={setProjectId} onHostChange={setHostId}
    onSelect={selectObjective} onRefresh={list.reset} onRetry={list.retry} onMore={list.more}
    onApplyReorder={list.applyReorder} />;
  const detailPane = <aside className="panel detail-panel" aria-label="工作目标详情">
    {selected ? <>
      <ObjectiveTimeline
        summary={selectedSummary}
        timeline={timeline.timeline}
        loading={timeline.loading}
        error={timeline.error}
        stale={timeline.stale}
        newRunIds={timeline.newRunIds}
        hidden={!!detail}
        openedKey={detail?.key ?? lastOpened.key}
        openedRunId={detail?.runId ?? lastOpened.runId}
        locked={locked}
        expandedGapIds={expanded}
        onToggleGap={toggleGap}
        onSetExpanded={setExpanded}
        onOpenItem={openItem}
        onRetry={() => void timeline.retry()}
        onBackToList={() => selectObjective(null)} />
      {detail && selectedSummary && <RunDetailPane
        objectiveTitle={selectedSummary.title}
        target={detail}
        snapshot={snapshot} api={api} refresh={reload} active={active}
        authority={authority} writesAvailable={writesAvailable}
        locked={locked} onBack={backToTimeline} onNavigate={navigateRun} onLockChange={setLocked}
        rowTitleFor={runId => timeline.timeline?.rows.find(row => row.runId === runId)?.title ?? null} />}
    </> : <div className="detail-placeholder">
      <h2>选择一个工作目标</h2>
      <p>查看各委派的排队、执行、等待 Host 与验收时间。只读，不调用模型。</p>
    </div>}
  </aside>;
  return <RecordDrafts><SplitView selected={!!selected} list={listPane} detail={detailPane} /></RecordDrafts>;
}
