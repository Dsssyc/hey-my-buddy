import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import type { ObjectiveFilter, ObjectiveSummary, TimelineRow } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { CATEGORY_LABEL, categoryTone } from "./objective-display";
import type { InspectorSelection } from "./inspector-card";
import { Tasks } from "./Tasks";
import { SplitView } from "./SplitView";
import { ObjectiveList } from "./ObjectiveList";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import { RunDetailPane } from "./RunDetailPane";
import type { DetailTarget } from "./RunDetailPane";
import { useObjectiveList } from "./use-objective-list";
import { useObjectiveTimeline } from "./use-objective-timeline";
import { stopStatus, stoppableSummary, useObjectiveStop } from "./objective-stop";
import type { AuthorityLatch } from "./console-session";
import { READ_ONLY_ACTION_REFUSAL } from "./console-session";

const EMPTY_SET: ReadonlySet<string> = new Set();

/* 0.15.1 U1 geometry (docs/design/objective-browser-0.15.1.md §1–2): the
   viewport picks the layout; above 760px an open detail collapses the list to
   a 48px rail and docks the detail beside the timeline. D = detail column,
   R = measured right-column width. */
const RAIL_PX = 48;
const DIVIDER_PX = 10;
const DETAIL_MIN_PX = 360;
const DETAIL_DEFAULT_MAX_PX = 600;
const DETAIL_HARD_MAX_PX = 720;
const TIMELINE_MIN_PX = 320;

const clamp = (value: number, min: number, max: number) => Math.round(Math.max(min, Math.min(value, max)));

function detailDefault(rightWidth: number): number {
  return clamp(Math.round(rightWidth * 0.4), DETAIL_MIN_PX, DETAIL_DEFAULT_MAX_PX);
}
function detailMax(rightWidth: number): number {
  return Math.max(DETAIL_MIN_PX, Math.min(DETAIL_HARD_MAX_PX, rightWidth - DIVIDER_PX - TIMELINE_MIN_PX));
}

/**
 * The timeline|detail separator (§2): pointer drag with live clamping,
 * keyboard ←/→ 20px (Shift 80px), Home/End to the bounds, Enter or a
 * double-click back to the viewport default. The remembered width lives in
 * page memory only.
 */
function TimelineDetailDivider({ min, max, value, timelineWidth, onChange, onReset }: {
  min: number; max: number; value: number; timelineWidth: number;
  onChange: (value: number) => void;
  onReset: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const container = () => root.current?.parentElement;
  const move = (clientX: number) => {
    const rect = container()?.getBoundingClientRect();
    if (!rect) return;
    onChange(clamp(rect.right - clientX - DIVIDER_PX / 2, min, max));
  };
  const step = (event: { key: string; shiftKey: boolean }) => {
    const delta = event.shiftKey ? 80 : 20;
    if (event.key === "ArrowLeft") return clamp(value + delta, min, max); // 详情加宽
    if (event.key === "ArrowRight") return clamp(value - delta, min, max);
    return null;
  };
  return <div ref={root} className="dock-divider vertical" role="separator" id="timeline-detail-separator"
    aria-orientation="vertical" aria-controls="timeline-detail-column"
    aria-label="调整时间轴与详情宽度"
    aria-valuemin={min} aria-valuemax={max} aria-valuenow={value}
    aria-valuetext={`详情 ${value} 像素，时间轴 ${timelineWidth} 像素`}
    tabIndex={0}
    onKeyDown={event => {
      if (event.key === "Home") { event.preventDefault(); onChange(min); return; }
      if (event.key === "End") { event.preventDefault(); onChange(max); return; }
      if (event.key === "Enter") { event.preventDefault(); onReset(); return; }
      const next = step(event);
      if (next !== null) { event.preventDefault(); onChange(next); }
    }}
    onDoubleClick={onReset}
    onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={event => { if (dragging.current) move(event.clientX); }}
    onPointerUp={() => { dragging.current = false; }}
    onLostPointerCapture={() => { dragging.current = false; }} />;
}

/** The objective stop's explicit confirmation: names the objective and its scope. */
function StopDialog({ summary, onConfirm, onCancel }: {
  summary: ObjectiveSummary;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
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
  const roots = summary.counts.roots;
  const helpers = summary.counts.helpers;
  const scope = summary.kind === "standalone"
    ? "将请求取消这条未归档委派及其协助任务。"
    : `将请求取消该目标当前全部未验收委派和协助任务（${roots} 个委派中已验收 ${summary.counts.accepted} 个保留${helpers ? `，另有 ${helpers} 个协助任务` : ""}）。`;
  return <div className="dialog-backdrop">
    <div className="dialog stop-dialog" role="dialog" aria-modal="true" aria-labelledby="stop-objective-title" aria-describedby="stop-objective-body">
      <h2 id="stop-objective-title">停止工作目标</h2>
      <p id="stop-objective-body">
        目标：{summary.title}。{scope}服务器按记录解析完整范围，不受当前筛选或截断影响；取消以真实停止证据为准，确认前显示“正在停止”。
      </p>
      <div className="actions">
        <button type="button" className="button danger" onClick={onConfirm}>确认停止目标</button>
        <button ref={cancelRef} type="button" className="button" onClick={onCancel}>取消</button>
      </div>
    </div>
  </div>;
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
      <Tasks snapshot={snapshot} api={api} refresh={refresh} active={active && view === "records"} />
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

  // The viewport (never the pane) picks the layout (§1). Above 760px an open
  // detail docks beside the timeline and the list collapses to the rail; at
  // 760px or below the detail layers over the chronological view.
  const paneRef = useRef<HTMLElement>(null);
  const railButtonRef = useRef<HTMLButtonElement>(null);
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
  const detailOpen = !!detail;
  // R = the timeline|detail stage width = pane minus the rail column.
  const rightWidth = Math.max((paneWidth ?? 0) - RAIL_PX, 0);
  const [detailWidth, setDetailWidth] = useState<number | null>(null);
  const maxDetail = detailMax(rightWidth > 0 ? rightWidth : 800);
  const dockDetailWidth = clamp(detailWidth ?? detailDefault(rightWidth > 0 ? rightWidth : 800), DETAIL_MIN_PX, maxDetail);

  // Opening a detail above 760px collapses the list to the expandable rail;
  // closing it restores the full list. The drawer itself is a transient
  // overlay that never changes the right-column geometry.
  const [listRail, setListRail] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const railActive = listRail && !narrowViewport;
  const wasDocking = useRef(false);
  useEffect(() => {
    const docking = detailOpen && !narrowViewport;
    if (docking && !wasDocking.current) { setListRail(true); setDrawerOpen(false); }
    if (!docking && wasDocking.current) { setListRail(false); setDrawerOpen(false); }
    wasDocking.current = docking;
  }, [detailOpen, narrowViewport]);

  // Objective-level stop: the one retained write, with its explicit
  // confirmation and a retained command identity. Nothing here locks browsing.
  const reload = async () => {
    const result = await refresh();
    list.reset();
    void timeline.retry();
    return result;
  };
  const stop = useObjectiveStop(api, snapshot, writesAvailable, authority, reload);
  const stopEntry = selected ? stop.entries.get(selected) : undefined;
  const stopState = stopStatus(stopEntry, timeline.timeline);

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
    if (drawerOpen) {
      // Inside the drawer: the same objective only closes the drawer; another
      // objective also closes the detail and restores the full list (§1).
      setDrawerOpen(false);
      railButtonRef.current?.focus();
      if (objectiveId === selected) return;
      setListRail(false);
    }
    setSelected(objectiveId);
    setDetail(null);
  }
  function selectItem(item: TimelineItem) {
    setSelection({ type: "item", key: item.key });
  }
  function selectRun(runId: string) {
    setSelection({ type: "run", runId });
  }
  function openItem(item: TimelineItem) {
    setSelection({ type: "item", key: item.key });
    setDetail({ runId: item.runId, section: item.section, locator: item.locator, key: item.key });
  }
  function openRun(runId: string) {
    const row = timeline.timeline?.rows.find(candidate => candidate.runId === runId) ?? null;
    setSelection({ type: "run", runId });
    setDetail({ runId, section: "overview", key: `row:${runId}`, locator: row ? `委派 · ${row.title}` : undefined });
  }
  function backToTimeline() {
    setDetail(null);
  }
  function navigateRun(runId: string) {
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

  // Esc closes the transient drawer first, then the detail, while this tab
  // owns the page and the keystroke did not start inside a field or a dialog.
  useEffect(() => {
    if ((!detail && !drawerOpen) || !active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select")) return;
      if (document.querySelector(".dialog-backdrop")) return;
      event.preventDefault();
      if (drawerOpen) {
        setDrawerOpen(false);
        railButtonRef.current?.focus();
        return;
      }
      if (narrowViewport && detail) setDetail(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [detail, drawerOpen, narrowViewport, active]);
  // Clicking outside the expanded drawer closes it and restores focus (§1).
  useEffect(() => {
    if (!drawerOpen) return;
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".list-drawer, .rail-panel")) return;
      setDrawerOpen(false);
      railButtonRef.current?.focus();
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [drawerOpen]);

  const expanded = selected ? expandedByObjective.get(selected) ?? EMPTY_SET : EMPTY_SET;

  // The header's stop control: one objective-level action, its honest status,
  // and the replay entry for a lost reply. Read-only sessions see the reason.
  let stopControl: ReactNode = null;
  if (selectedSummary) {
    const stopping = stopEntry?.phase === "stopping";
    const showButton = stoppableSummary(selectedSummary);
    stopControl = <span className="stop-control">
      {showButton && <button type="button" className="button small-button danger"
        disabled={!stop.writable || stopping}
        title={!stop.writable ? READ_ONLY_ACTION_REFUSAL : stopping ? "停止请求已发出，等待回复。" : "请求取消该目标全部未验收委派及协助任务（需确认）。"}
        onClick={() => stop.requestStop(selectedSummary)}>停止目标</button>}
      {stopState && <span className={`stop-status stop-${stopState.phase}`} role="status"
        title={stopState.detail}>{stopState.label}</span>}
      {stopState?.phase === "unknown" && stop.writable && !stopping
        && <button type="button" className="button small-button"
          title="按原命令 ID 重试；服务会去重，不会扩大取消范围。"
          onClick={() => stop.retryStop(selectedSummary)}>重试停止</button>}
      {stopState?.phase === "refused"
        && <button type="button" className="button small-button" onClick={() => stop.dismissStop(selectedSummary.objectiveId)}>知道了</button>}
    </span>;
  }
  // The same honest status line also appears in a docked detail's overview.
  const stopStatusNode = stopState
    ? <p className={`stop-status stop-${stopState.phase} detail-stop-status`} role="status" title={stopState.detail}>
      停止状态：{stopState.label}。{stopState.detail}
    </p>
    : null;

  const listPane = <ObjectiveList
    rows={list.rows} total={list.total} loading={list.loading} error={list.error}
    nextCursor={list.nextCursor} reorder={list.reorder}
    filter={filter} query={query} projectId={projectId} hostId={hostId} choices={choices}
    selected={selected}
    rail={railActive} railState={selectedSummary} railButtonRef={railButtonRef}
    onToggleRail={() => {
      setDrawerOpen(current => {
        if (current) railButtonRef.current?.focus();
        return !current;
      });
    }}
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
    hidden={detailOpen && narrowViewport}
    openedKey={detail?.key ?? null}
    openedRunId={detail?.runId ?? null}
    selection={selection}
    headerActions={stopControl}
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

  // The detail's fixed three-row overview comes from the timeline's own row
  // read when the run is in scope; the raw fallback lives in WorkflowPanel.
  const detailRow = detail ? timeline.timeline?.rows.find(row => row.runId === detail.runId) ?? null : null;
  const detailNode = detail && selectedSummary ? <RunDetailPane
    objectiveTitle={selectedSummary.title}
    target={detail}
    snapshot={snapshot} api={api} refresh={reload} active={active}
    mode={narrowViewport ? "layer" : "dock"}
    onBack={backToTimeline} onNavigate={navigateRun}
    stopStatusNode={stopStatusNode} overviewRow={detailRow}
    rowTitleFor={runId => timeline.timeline?.rows.find(row => row.runId === runId)?.title ?? null} /> : null;

  // One persistent wrapper so crossing the layer/dock threshold reconciles
  // instead of remounting: the timeline keeps its scroll and focus.
  const stageStyle = detailOpen && !narrowViewport
    ? ({ "--dock-detail-width": `${dockDetailWidth}px` } as CSSProperties)
    : undefined;
  const detailPane = <aside className="panel detail-panel" ref={paneRef} aria-label="工作目标详情">
    {selected
      ? <div className={detailOpen && !narrowViewport ? "right-dock side" : "right-stage"} style={stageStyle}>
        <div key="timeline" className="right-stage-pane">{timelinePane}</div>
        {detailOpen && !narrowViewport && <TimelineDetailDivider key="divider"
          min={DETAIL_MIN_PX} max={maxDetail} value={dockDetailWidth}
          timelineWidth={Math.max(0, rightWidth - DIVIDER_PX - dockDetailWidth)}
          onChange={setDetailWidth} onReset={() => setDetailWidth(null)} />}
        {detailNode && <div key="detail" id="timeline-detail-column" className="right-stage-pane detail-stage">{detailNode}</div>}
      </div>
      : <div className="detail-placeholder">
        <h2>选择一个工作目标</h2>
        <p>查看各委派的排队、执行、等待 Host 与验收时间。只读，不调用模型。</p>
      </div>}
  </aside>;
  return <>
    <div className="workspace-wrap">
      <SplitView selected={!!selected} rail={railActive} list={listPane} detail={detailPane} />
      {railActive && drawerOpen && <div className="list-drawer" role="dialog" aria-label="工作目标列表（抽屉）">
        <ObjectiveList
          rows={list.rows} total={list.total} loading={list.loading} error={list.error}
          nextCursor={list.nextCursor} reorder={list.reorder}
          filter={filter} query={query} projectId={projectId} hostId={hostId} choices={choices}
          selected={selected}
          rail={false} railState={null}
          onFilterChange={setFilter} onQueryChange={setQuery} onProjectChange={setProjectId} onHostChange={setHostId}
          onSelect={selectObjective} onRefresh={list.reset} onRetry={list.retry} onMore={list.more}
          onApplyReorder={list.applyReorder} />
      </div>}
    </div>
    {stop.confirming && <StopDialog summary={stop.confirming}
      onConfirm={() => stop.confirmStop(stop.confirming!)}
      onCancel={stop.cancelConfirm} />}
  </>;
}
