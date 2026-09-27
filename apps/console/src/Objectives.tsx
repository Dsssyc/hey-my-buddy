import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { ObjectiveFilter, ObjectiveSummary } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import type { InspectorSelection } from "./inspector-card";
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

/** Right-pane width at which a docked detail gains its own column. */
const DOCK_SIDE_MIN_PX = 1000;
const DOCK_DETAIL_MIN_PX = 440;
const DOCK_DETAIL_MAX_PX = 640;
const DOCK_DETAIL_DEFAULT_PX = 520;

/** The docked detail's resizable separator; reuses SplitView's divider rules. */
function DockDivider({ orientation, min, max, value, onChange, label }: {
  orientation: "vertical" | "horizontal";
  min: number; max: number; value: number;
  onChange: (value: number) => void;
  label: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const container = () => root.current?.parentElement;
  const resize = (next: number) => onChange(Math.round(Math.max(min, Math.min(next, max))));
  const move = (clientX: number, clientY: number) => {
    const rect = container()?.getBoundingClientRect();
    if (!rect) return;
    if (orientation === "vertical") resize(Math.min(max, rect.right - clientX));
    // The divider sits above the detail pane, so the pointer's distance from
    // the container's bottom edge is the detail height it controls.
    else resize(Math.min(max, rect.bottom - clientY));
  };
  const arrows = orientation === "vertical" ? ["ArrowLeft", "ArrowRight"] : ["ArrowUp", "ArrowDown"];
  const grow = orientation === "vertical" ? "ArrowLeft" : "ArrowDown";
  return <div ref={root} className={`dock-divider ${orientation}`} role="separator"
    aria-label={label} aria-orientation={orientation === "vertical" ? "vertical" : "horizontal"}
    aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} tabIndex={0}
    onKeyDown={event => {
      if (!arrows.includes(event.key)) return;
      event.preventDefault();
      resize(value + (event.key === grow ? 20 : -20));
    }}
    onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (dragging.current) move(event.clientX, event.clientY); }}
    onPointerUp={() => { dragging.current = false; }}
    onLostPointerCapture={() => { dragging.current = false; }} />;
}

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
  // The pinned inspector selection, kept per objective so switching back restores it.
  const [selectionByObjective, setSelectionByObjective] = useState<Map<string, InspectorSelection>>(() => new Map());
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

  // Detail placement: the right pane keeps the timeline visible above 760px —
  // side by side once it is wide enough, stacked otherwise. Only the ≤760px
  // viewport keeps the layer switch where the detail replaces the timeline.
  const paneRef = useRef<HTMLElement>(null);
  const [paneWidth, setPaneWidth] = useState<number | null>(null);
  const [narrowViewport, setNarrowViewport] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 760px)");
    const update = () => setNarrowViewport(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const element = paneRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth > 0) setPaneWidth(element.clientWidth);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const [dockDetailWidth, setDockDetailWidth] = useState(DOCK_DETAIL_DEFAULT_PX);
  const [dockDetailHeight, setDockDetailHeight] = useState<number | null>(null);
  const detailOpen = !!detail;
  const dockSide = detailOpen && !narrowViewport && (paneWidth ?? 0) >= DOCK_SIDE_MIN_PX;
  const dockStack = detailOpen && !narrowViewport && !dockSide;
  const dockDetailMax = Math.max(DOCK_DETAIL_MIN_PX, Math.min(DOCK_DETAIL_MAX_PX, (paneWidth ?? DOCK_SIDE_MIN_PX) - 530));

  function setSelection(next: InspectorSelection | null) {
    if (!selected) return;
    setSelectionByObjective(previous => {
      const map = new Map(previous);
      if (next === null) map.delete(selected);
      else map.set(selected, next);
      return map;
    });
  }
  const selection = selected ? selectionByObjective.get(selected) ?? null : null;

  function selectObjective(objectiveId: string | null) {
    // While a detail holds an unconfirmed operation, no selection change — not
    // even re-clicking the selected objective — may unmount it.
    if (locked) return;
    setSelected(objectiveId);
    setDetail(null);
  }
  function selectItem(item: TimelineItem) {
    // Selection is read-only and always allowed, including while locked.
    setSelection({ type: "item", key: item.key });
  }
  function selectRun(runId: string) {
    setSelection({ type: "run", runId });
  }
  function openItem(item: TimelineItem) {
    // Locked blocks another delegation's detail; the same delegation's other
    // sections stay reachable.
    if (locked && detail && detail.runId !== item.runId) return;
    setSelection({ type: "item", key: item.key });
    setDetail({ runId: item.runId, section: item.section, locator: item.locator, key: item.key });
  }
  function openRun(runId: string) {
    if (locked && detail && detail.runId !== runId) return;
    const row = timeline.timeline?.rows.find(candidate => candidate.runId === runId) ?? null;
    setSelection({ type: "run", runId });
    setDetail({ runId, section: "overview", key: `row:${runId}`, locator: row ? `委派 · ${row.title}` : undefined });
  }
  function backToTimeline() {
    if (locked) return;
    setDetail(null);
  }
  function navigateRun(runId: string) {
    if (locked) return;
    setDetail({ runId });
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

  // Esc closes the detail while this tab owns the page and the keystroke did
  // not start inside a field, a dialog, or the timeline canvas (whose Escape
  // only hands focus to the toolbar).
  useEffect(() => {
    if (!detail || !active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || locked) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select")) return;
      if (document.querySelector(".dialog-backdrop")) return;
      if (target?.closest(".timeline-view") && !target.closest(".run-view")) return;
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

  const timelinePane = selected ? <ObjectiveTimeline
    active={active}
    summary={selectedSummary}
    timeline={timeline.timeline}
    loading={timeline.loading}
    error={timeline.error}
    stale={timeline.stale}
    newRunIds={timeline.newRunIds}
    hidden={detailOpen && !dockSide && !dockStack}
    openedKey={detail?.key ?? null}
    openedRunId={detail?.runId ?? null}
    selection={selection}
    locked={locked}
    expandedGapIds={expanded}
    onToggleGap={toggleGap}
    onSetExpanded={setExpanded}
    onSelectItem={selectItem}
    onOpenItem={openItem}
    onSelectRun={selectRun}
    onOpenRun={openRun}
    onClearSelection={() => setSelection(null)}
    onRetry={() => void timeline.retry()}
    onBackToList={() => selectObjective(null)} /> : null;
  const detailNode = detail && selectedSummary ? <RunDetailPane
    objectiveTitle={selectedSummary.title}
    target={detail}
    snapshot={snapshot} api={api} refresh={reload} active={active}
    authority={authority} writesAvailable={writesAvailable}
    mode={dockSide || dockStack ? "dock" : "layer"}
    locked={locked} onBack={backToTimeline} onNavigate={navigateRun} onLockChange={setLocked}
    rowTitleFor={runId => timeline.timeline?.rows.find(row => row.runId === runId)?.title ?? null} /> : null;

  // One persistent wrapper (with keys per pane) so crossing the layer/dock
  // threshold reconciles instead of remounting: the timeline keeps its scroll
  // and focus, and the open detail keeps its ambiguous-command identity.
  const stageClass = dockSide ? "right-dock side" : dockStack ? "right-dock stack" : "right-stage";
  const detailPane = <aside className="panel detail-panel" ref={paneRef} aria-label="工作目标详情">
    {selected
      ? <div className={stageClass}
        style={dockSide
          ? ({ "--dock-detail-width": `${Math.min(dockDetailWidth, dockDetailMax)}px` } as CSSProperties)
          : dockStack && dockDetailHeight !== null
            ? ({ "--dock-detail-height": `${dockDetailHeight}px` } as CSSProperties)
            : undefined}>
        <div key="timeline" className="right-stage-pane">{timelinePane}</div>
        {dockSide && <DockDivider key="divider" orientation="vertical" min={DOCK_DETAIL_MIN_PX} max={dockDetailMax}
          value={Math.min(dockDetailWidth, dockDetailMax)} onChange={setDockDetailWidth} label="调整详情列宽度" />}
        {dockStack && <DockDivider key="divider" orientation="horizontal" min={180} max={Math.max(220, (paneRef.current?.clientHeight ?? 600) * 0.75)}
          value={dockDetailHeight ?? Math.round((paneRef.current?.clientHeight ?? 600) * 0.45)}
          onChange={setDockDetailHeight} label="调整详情高度" />}
        {detailNode && <div key="detail" className="right-stage-pane detail-stage">{detailNode}</div>}
      </div>
      : <div className="detail-placeholder">
        <h2>选择一个工作目标</h2>
        <p>查看各委派的排队、执行、等待 Host 与验收时间。只读，不调用模型。</p>
      </div>}
  </aside>;
  return <RecordDrafts><SplitView selected={!!selected} list={listPane} detail={detailPane} /></RecordDrafts>;
}
