import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import type { ObjectiveSummary, ObjectiveTimeline as ObjectiveTimelineData, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import { createTimelineLayout, TIMELINE_FOLD_THRESHOLD_MS } from "./objective-timeline-layout";
import { fitPixelsPerMinute, MAX_PIXELS_PER_MINUTE, scaleTimeline } from "./objective-timeline-scale";
import type { SpanOutcome, TimelineItem, FriendlyProfile } from "./objective-display";
import {
  buildChronology, clockSeconds, clockTime, configurationNamer, displayTitle, durationShort, eventClusterGlyph,
  eventClusterLabel, eventSentence, outcomeLabel, paletteIndex, rowStateInfo, spanFacts, rowLabelItem, settleItem,
  eventItem, toMs, configurationPalette, acceptanceWaitText, titleLineTooltip,
} from "./objective-display";
import { ObjectiveChronology } from "./ObjectiveChronology";
import { DelegationStrip, ObjectiveOverview } from "./ObjectiveOverview";
import { TimelineInspector } from "./TimelineInspector";
import { MarkerPopover, type MarkerClusterView } from "./MarkerPopover";
import type { InspectorSelection } from "./inspector-card";

/** Markers closer than this many actual track pixels merge into one numbered marker. */
const MARKER_MERGE_PX = 14;
/** Nominal track width before measurement (and in tests without layout). */
const FALLBACK_VIEWPORT_PX = 800;
const FALLBACK_LABEL_PX = 240;
/** The inspector drawer's default and keyboard-adjusted geometry (P2.1). */
const DRAWER_DEFAULT_PX = 168;
const DRAWER_MIN_PX = 44;
const DRAWER_STEP_PX = 16;
const ZOOM_STEP = 1.5;

type SpanFacts = { item: TimelineItem; startMs: number | null; endMs: number | null; recordedEndMs: number | null; outcome: SpanOutcome };
type MarkerCluster = MarkerClusterView & { head: string };

export type ObjectiveTimelineProps = {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimelineData | null;
  loading: boolean;
  error: string;
  stale: boolean;
  newRunIds: ReadonlySet<string>;
  hidden: boolean;
  active?: boolean;
  /** The delegation whose detail is currently open (accent marker on its row). */
  openedKey: string | null;
  openedRunId: string | null;
  /** The pinned inspector selection; a refresh never clears it. */
  selection: InspectorSelection | null;
  /** Objective-level actions rendered in the overview header (停止目标, U4). */
  headerActions?: ReactNode;
  /** Snapshot profiles for friendly configuration names (0.16 0.3). */
  profiles?: readonly FriendlyProfile[] | null;
  expandedGapIds: ReadonlySet<string>;
  onToggleGap: (gapId: string) => void;
  onSetExpanded: (gapIds: Set<string>) => void;
  onSelectItem: (item: TimelineItem) => void;
  onOpenItem: (item: TimelineItem) => void;
  onSelectRun: (runId: string) => void;
  onOpenRun: (runId: string) => void;
  onClearSelection: () => void;
  onRetry: () => void;
  onBackToList: () => void;
};

/** The horizontal separator resizing the inspector drawer (P2.1). */
function InspectorSeparator({ min, max, value, onChange, onReset }: {
  min: number; max: number; value: number;
  onChange: (value: number) => void;
  onReset: () => void;
}) {
  const dragging = useRef(false);
  const container = () => document.querySelector<HTMLElement>(".timeline-view");
  const move = (clientY: number) => {
    const rect = container()?.getBoundingClientRect();
    if (!rect) return;
    onChange(Math.round(Math.max(min, Math.min(max, rect.bottom - clientY))));
  };
  const step = (event: { key: string }) => {
    if (event.key === "ArrowUp") return Math.min(value + DRAWER_STEP_PX, max);
    if (event.key === "ArrowDown") return Math.max(value - DRAWER_STEP_PX, min);
    return null;
  };
  return <div className="dock-divider horizontal inspector-separator" role="separator"
    aria-orientation="horizontal" aria-label="调整检查器高度"
    aria-valuemin={min} aria-valuemax={max} aria-valuenow={value}
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
    onPointerMove={event => { if (dragging.current) move(event.clientY); }}
    onPointerUp={() => { dragging.current = false; }}
    onLostPointerCapture={() => { dragging.current = false; }} />;
}

/**
 * Right pane, layer one: the read-only work-objective timeline. The vertical
 * structure follows 0.16 P2.1 — a bounded header, a collapsible delegation
 * band, the toolbar, the timeline as the only row-scroll area (min 200px), a
 * keyboard/pointer separator and the inspector as an always-visible bottom
 * drawer. Occupancy and folding stay in the frozen layout module; the default
 * scale fits observed activity (适应窗口) and +/−/0 zoom by ×1.5 steps anchored
 * on the selection or viewport centre. Single clicks only select the pinned
 * inspector; Enter, double-click and the explicit 打开 controls open details.
 */
export function ObjectiveTimeline(props: ObjectiveTimelineProps) {
  const { timeline, loading, error, stale, hidden, openedKey, openedRunId, selection, active = true } = props;
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  const [asList, setAsList] = useState(false);
  const [openCluster, setOpenCluster] = useState<string | null>(null);
  const [clusterNotice, setClusterNotice] = useState<string | null>(null);
  useEffect(() => { if (hidden || !active) setOpenCluster(null); }, [hidden, active]);
  const rootRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const listToggleRef = useRef<HTMLButtonElement>(null);
  const [viewport, setViewport] = useState<number | null>(null);
  const savedScroll = useRef<{ top: number; left: number } | null>(null);
  const wasHidden = useRef(false);

  // Compact form at viewport heights of 800px or less; the narrow layout is
  // always compact (P2.1/P2.2).
  const [compactViewport, setCompactViewport] = useState(false);
  const [narrowViewport, setNarrowViewport] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const heightQuery = window.matchMedia("(max-height: 800px)");
    const widthQuery = window.matchMedia("(max-width: 760px)");
    const update = () => { setCompactViewport(heightQuery.matches); setNarrowViewport(widthQuery.matches); };
    update();
    // Older engines expose the legacy addListener API; guarded for tests.
    if (typeof heightQuery.addEventListener === "function") {
      heightQuery.addEventListener("change", update);
      widthQuery.addEventListener("change", update);
      return () => { heightQuery.removeEventListener("change", update); widthQuery.removeEventListener("change", update); };
    }
    return;
  }, []);

  // The delegation band (P2.1): default follows the viewport height, and the
  // user's toggle then applies to every objective for this page session.
  const [cardsCollapsed, setCardsCollapsed] = useState<boolean | null>(null);
  const cardsActuallyCollapsed = cardsCollapsed ?? (compactViewport || narrowViewport);

  // The Host event row is collapsible and defaults collapsed; the choice is
  // remembered per objective for this page session (P1.7).
  const [hostEventsOpenByObjective, setHostEventsOpenByObjective] = useState<Map<string, boolean>>(() => new Map());

  // The inspector drawer height (P2.1): page-session persistent.
  const [drawerHeight, setDrawerHeight] = useState<number | null>(null);
  const [drawerCollapsed, setDrawerCollapsed] = useState(false);
  const columnHeight = useRef<number | null>(null);
  useEffect(() => {
    const element = rootRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => { columnHeight.current = element.clientHeight; });
    observer.observe(element);
    columnHeight.current = element.clientHeight;
    return () => observer.disconnect();
  }, []);
  const drawerMax = Math.max(DRAWER_MIN_PX, Math.round((columnHeight.current ?? 400) * 0.45));
  const drawerValue = drawerCollapsed ? DRAWER_MIN_PX : Math.max(DRAWER_MIN_PX, Math.min(drawerHeight ?? DRAWER_DEFAULT_PX, drawerMax));

  // Zoom (P2.3): null means 适应窗口; a number is an absolute px/minute.
  const [zoom, setZoom] = useState<number | null>(null);
  const zoomAnchor = useRef<{ timeMs: number; screenX: number } | null>(null);

  const observedAtMs = toMs(timeline?.observedAt);
  const layout = useMemo(
    () => timeline ? createTimelineLayout(timeline, props.expandedGapIds) : null,
    [timeline, props.expandedGapIds],
  );
  const canvasKey = timeline?.objective.objectiveId ?? null;

  // Measure the scroll viewport (not the expanding track) once the async
  // canvas exists; zero hidden widths are ignored so saved geometry survives.
  const labelWidthPx = () => {
    const element = scrollRef.current;
    if (!element) return FALLBACK_LABEL_PX;
    const parsed = Number.parseFloat(getComputedStyle(element).getPropertyValue("--label-w"));
    return Number.isFinite(parsed) && parsed > 0 ? parsed : FALLBACK_LABEL_PX;
  };
  useEffect(() => {
    const element = scrollRef.current;
    if (!element || canvasKey === null) return;
    const update = () => {
      const width = element.clientWidth;
      // Track viewport floor 380 (design §1): with the label column the
      // timeline content stays ≥560 and scrolls horizontally below that.
      if (width > 0) setViewport(Math.max(380, Math.round(width - labelWidthPx())));
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [canvasKey, asList]);

  const effectiveViewport = viewport ?? FALLBACK_VIEWPORT_PX;
  const fitPpm = useMemo(
    () => layout ? fitPixelsPerMinute(layout, effectiveViewport) : Number.POSITIVE_INFINITY,
    [layout, effectiveViewport],
  );
  // Pixels are a pure function of the measured viewport, so content sizing can
  // never feed back into the measurement. Fit is clamped by MAX; a manual zoom
  // is clamped to [fit, MAX].
  const clampedZoom = zoom === null ? null : Math.max(0, Math.min(zoom, Math.max(fitPpm, MAX_PIXELS_PER_MINUTE)));
  const scaled = useMemo(
    () => layout ? scaleTimeline(layout, effectiveViewport, clampedZoom ?? undefined) : null,
    [layout, effectiveViewport, clampedZoom],
  );
  const currentPpm = clampedZoom ?? fitPpm;

  // Keep the anchor time at its screen position across a zoom change.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    const anchor = zoomAnchor.current;
    zoomAnchor.current = null;
    if (!element || !anchor || !scaled || !layout || layout.startMs === null) return;
    const percent = scaled.position(anchor.timeMs);
    if (percent === null) return;
    const labelW = labelWidthPx();
    const trackX = percent / 100 * scaled.widthPx;
    element.scrollLeft = Math.max(0, Math.round(labelW + trackX - anchor.screenX));
  }, [scaled, layout]);

  const spansByRun = useMemo(() => {
    const map = new Map<string, TimelineSpan[]>();
    if (!timeline) return map;
    for (const span of timeline.spans) {
      if (!map.has(span.runId)) map.set(span.runId, []);
      map.get(span.runId)!.push(span);
    }
    return map;
  }, [timeline]);
  const rowsById = useMemo(() => new Map((timeline?.rows ?? []).map(row => [row.runId, row])), [timeline]);

  // Colour slots persist per objective across refreshes: a configuration seen
  // once keeps its slot even when later data shuffles first appearances.
  const paletteCache = useRef(new Map<string, Map<string, number>>());
  const palette = useMemo(() => {
    if (!canvasKey || !spansByRun.size) return [];
    let slots = paletteCache.current.get(canvasKey);
    if (!slots) {
      slots = new Map<string, number>();
      paletteCache.current.set(canvasKey, slots);
    }
    return configurationPalette(spansByRun, slots, props.profiles);
  }, [spansByRun, canvasKey, props.profiles]);
  const namer = useMemo(() => configurationNamer(props.profiles), [props.profiles]);

  const factsBySpan = useMemo(() => {
    const map = new Map<string, SpanFacts>();
    if (!timeline) return map;
    for (const row of timeline.rows) {
      for (const span of spansByRun.get(row.runId) ?? []) {
        map.set(span.spanId, spanFacts(span, row, observedAtMs));
      }
    }
    return map;
  }, [timeline, spansByRun, observedAtMs]);

  const itemsByKey = useMemo(() => {
    const map = new Map<string, TimelineItem>();
    for (const facts of factsBySpan.values()) map.set(facts.item.key, facts.item);
    if (timeline) {
      for (const row of timeline.rows) {
        const label = rowLabelItem(row);
        map.set(label.key, label);
      }
      for (const row of timeline.rows) {
        const settle = settleItem(row);
        if (settle) map.set(settle.key, settle);
      }
      for (const event of timeline.events) {
        const item = eventItem(event, rowsById);
        map.set(item.key, item);
      }
    }
    return map;
  }, [factsBySpan, timeline, rowsById]);

  const clusters = useMemo<MarkerCluster[]>(() => {
    if (!timeline || !scaled) return [];
    const placed = timeline.events
      .map(event => {
        const percent = scaled.position(event.at);
        return percent === null ? null : { event, x: percent, xPx: percent / 100 * scaled.widthPx };
      })
      .filter((entry): entry is { event: TimelineEvent; x: number; xPx: number } => entry !== null)
      .sort((left, right) => left.xPx - right.xPx);
    const groups: { event: TimelineEvent; x: number; xPx: number }[][] = [];
    for (const entry of placed) {
      const group = groups[groups.length - 1];
      if (group && entry.xPx - group[group.length - 1]!.xPx < MARKER_MERGE_PX) group.push(entry);
      else groups.push([entry]);
    }
    return groups.map(group => ({
      key: `events:${group[0]!.event.seq}`,
      x: group[0]!.x,
      events: group.map(entry => entry.event),
      items: group.map(entry => eventItem(entry.event, rowsById)),
      head: eventClusterLabel(group.map(entry => entry.event)),
    }));
  }, [timeline, scaled, rowsById]);

  // A refresh that changes an open popover's membership closes it with a
  // notice instead of silently showing a different set of events.
  const clusterMembership = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    const membership = new Map(clusters.map(cluster => [cluster.key, cluster.events.map(event => event.seq).join(",")]));
    const previous = clusterMembership.current;
    clusterMembership.current = membership;
    if (openCluster !== null && previous.get(openCluster) !== undefined && previous.get(openCluster) !== membership.get(openCluster)) {
      setOpenCluster(null);
      setClusterNotice("事件分组已随刷新更新");
    }
  }, [clusters, openCluster]);

  const canFold = layout?.canFold ?? false;
  const eligibleGaps = useMemo(() =>
    (scaled?.gaps ?? []).filter(gap => gap.endMs - gap.startMs > TIMELINE_FOLD_THRESHOLD_MS),
    [scaled]);

  const chronology = useMemo(() => {
    if (!timeline || !layout) return [];
    return buildChronology(timeline, factsBySpan, layout.gaps, canFold);
  }, [timeline, layout, factsBySpan, canFold]);

  // Roving tabindex: one stop inside the canvas, defaulting to the first
  // delegation row's first execution span, then a rendered marker cluster,
  // then the first row label. Cluster buttons carry `events:` keys.
  const clusterKeys = useMemo(() => new Set(clusters.map(cluster => cluster.key)), [clusters]);
  useEffect(() => {
    if (!timeline) return;
    if (focusKey && (itemsByKey.has(focusKey) || clusterKeys.has(focusKey))) return;
    const rows = timeline.rows;
    let candidate: TimelineItem | null = null;
    for (const row of rows) {
      const spans = spansByRun.get(row.runId) ?? [];
      const execution = spans.map(span => factsBySpan.get(span.spanId)).find(facts => facts && facts.item.atMs !== null && facts.item.head === "执行片段");
      if (execution) { candidate = execution.item; break; }
    }
    candidate = candidate ?? [...factsBySpan.values()].find(facts => facts.item.atMs !== null)?.item ?? null;
    const fallbackKey = clusters[0]?.key ?? (rows[0] ? rowLabelItem(rows[0]).key : null);
    setFocusKey(candidate?.key ?? fallbackKey);
  }, [timeline, itemsByKey, factsBySpan, spansByRun, clusters, clusterKeys, focusKey]);

  // Stay mounted while hidden; restore both scroll axes and focus on return.
  useEffect(() => {
    const element = scrollRef.current;
    if (hidden) {
      wasHidden.current = true;
      if (element) savedScroll.current = { top: element.scrollTop, left: element.scrollLeft };
      return;
    }
    if (wasHidden.current) {
      wasHidden.current = false;
      if (element && savedScroll.current !== null) {
        element.scrollTop = savedScroll.current.top;
        element.scrollLeft = savedScroll.current.left;
      }
      savedScroll.current = null;
      if (focusKey) {
        // Canvas items and chronology entries share keys; focus whichever the
        // current view actually shows, then bring it into view.
        const matches = [...(rootRef.current?.querySelectorAll<HTMLElement>(`[data-key="${focusKey}"]`) ?? [])];
        const chronological = asList || window.matchMedia?.("(max-width: 760px)").matches === true;
        const target = matches.find(node => node.classList.contains("tl-entry") === chronological) ?? matches[0];
        if (target) {
          target.focus({ preventScroll: true });
          target.scrollIntoView?.({ block: "nearest", inline: "nearest" });
        }
      }
    }
  }, [hidden, focusKey, asList]);

  function requestClusterClose(focusMarker: boolean) {
    setOpenCluster(current => {
      if (current === null) return null;
      if (focusMarker) {
        const marker = rootRef.current?.querySelector<HTMLElement>(`[data-key="${current}"]`);
        marker?.focus();
      }
      return null;
    });
  }

  function itemAria(item: TimelineItem): string {
    return `${item.head}，${item.parts.join("，")}`;
  }

  /** The screen-x of a time instant inside the scroll viewport, or null when off-canvas. */
  function screenXAt(timeMs: number): number | null {
    const element = scrollRef.current;
    if (!element || !scaled) return null;
    const percent = scaled.position(timeMs);
    if (percent === null) return null;
    return labelWidthPx() + percent / 100 * scaled.widthPx - element.scrollLeft;
  }

  /** A zoom step keeps the anchor time (selection in view, else viewport centre) at its screen position. */
  function changeZoom(next: number | null) {
    const element = scrollRef.current;
    if (element && scaled && layout && layout.startMs !== null && layout.endMs !== null && !asList) {
      let anchorTime: number | null = null;
      let anchorScreenX: number | null = null;
      const selectedTime = selection?.type === "item"
        ? itemsByKey.get(selection.key)?.atMs ?? null
        : null;
      if (selectedTime !== null) {
        const x = screenXAt(selectedTime);
        if (x !== null && x >= 0 && x <= element.clientWidth) {
          anchorTime = selectedTime;
          anchorScreenX = x;
        }
      }
      if (anchorTime === null || anchorScreenX === null) {
        // The time at the viewport centre, found by bisection over the
        // monotonic position mapping (fold bands included).
        const centreX = element.scrollLeft + element.clientWidth / 2 - labelWidthPx();
        const percent = Math.max(0, Math.min(100, centreX / scaled.widthPx * 100));
        let lo = layout.startMs, hi = layout.endMs;
        for (let i = 0; i < 44; i += 1) {
          const mid = (lo + hi) / 2;
          const at = scaled.position(mid) ?? 0;
          if (at < percent) lo = mid; else hi = mid;
        }
        anchorTime = (lo + hi) / 2;
        anchorScreenX = element.clientWidth / 2;
      }
      zoomAnchor.current = { timeMs: anchorTime, screenX: anchorScreenX };
    }
    setZoom(next === null ? null : Math.max(0, next));
  }

  function zoomIn() {
    if (!Number.isFinite(currentPpm) || currentPpm >= MAX_PIXELS_PER_MINUTE) return;
    changeZoom(Math.min(currentPpm * ZOOM_STEP, MAX_PIXELS_PER_MINUTE));
  }
  function zoomOut() {
    if (zoom === null) return; // already 适应窗口
    const next = currentPpm / ZOOM_STEP;
    if (next <= fitPpm * 1.001) { changeZoom(null); return; }
    changeZoom(next);
  }
  const zoomAtMax = Number.isFinite(currentPpm) && currentPpm >= MAX_PIXELS_PER_MINUTE - 1e-9;
  const zoomAtFit = zoom === null || (Number.isFinite(fitPpm) && currentPpm <= fitPpm * 1.001);

  function onCanvasKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select")) return;
    // Keyboard zoom (P2.3): + / - / 0 while focus is inside the timeline grid;
    // no modifier so the browser's own zoom is untouched.
    if (!asList && (event.key === "+" || event.key === "=" || event.key === "-" || event.key === "0")) {
      event.preventDefault();
      if (event.key === "+" || event.key === "=") zoomIn();
      else if (event.key === "-") zoomOut();
      else changeZoom(null);
      return;
    }
    const item = target.closest<HTMLElement>(".tl-item");
    const grid = gridRef.current;
    if (!item || !grid || !grid.contains(item)) return;
    const rows = [...grid.querySelectorAll<HTMLElement>(".tl-row[data-nav]")]
      .map(row => [...row.querySelectorAll<HTMLElement>(".tl-item")].sort((left, right) => Number(left.dataset.x) - Number(right.dataset.x)));
    const rowIndex = rows.findIndex(row => row.includes(item));
    if (rowIndex < 0) return;
    const columnIndex = rows[rowIndex]!.indexOf(item);
    const anchor = Number(item.dataset.x);
    const key = item.dataset.key ?? "";
    let next: HTMLElement | null = null;
    if (event.key === "ArrowRight") next = rows[rowIndex]![columnIndex + 1] ?? null;
    else if (event.key === "ArrowLeft") next = rows[rowIndex]![columnIndex - 1] ?? null;
    else if (event.key === "Home") next = rows[rowIndex]![0] ?? null;
    else if (event.key === "End") next = rows[rowIndex]![rows[rowIndex]!.length - 1] ?? null;
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const row = rows[rowIndex + (event.key === "ArrowDown" ? 1 : -1)];
      if (row && row.length) {
        next = row.reduce((best, element) =>
          Math.abs(Number(element.dataset.x) - anchor) < Math.abs(Number(best.dataset.x) - anchor) ? element : best, row[0]!);
      }
    } else if (event.key === " ") {
      // Space selects the focused item (or opens the cluster popover).
      event.preventDefault();
      item.click();
      return;
    } else if (event.key === "Enter") {
      // Enter selects and opens the focused item; a merged cluster opens its popover.
      event.preventDefault();
      const cluster = clusters.find(candidate => candidate.key === key);
      if (cluster && cluster.items.length > 1) {
        setClusterNotice(null);
        setOpenCluster(current => (current === key ? null : key));
        return;
      }
      const resolved = itemsByKey.get(key);
      if (!resolved) return;
      // A row label keeps its run-level semantics on Enter: the whole
      // delegation stays selected when its detail opens.
      if (key.startsWith("row:")) props.onOpenRun(resolved.runId);
      else props.onOpenItem(resolved);
      return;
    } else if (event.key === "Escape") {
      event.preventDefault();
      listToggleRef.current?.focus();
      return;
    } else return;
    event.preventDefault();
    if (next) {
      setFocusKey(next.dataset.key ?? null);
      next.focus();
      next.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    }
  }

  const widthPx = scaled?.widthPx ?? FALLBACK_VIEWPORT_PX;
  const collapsedGapMs = (scaled?.gaps ?? []).reduce((total, gap) => total + (gap.collapsed ? gap.endMs - gap.startMs : 0), 0);
  const realMinutes = layout && layout.startMs !== null && layout.endMs !== null
    ? Math.max((layout.endMs - layout.startMs - collapsedGapMs) / 60000, 0)
    : 0;

  const ticks = useMemo(() => {
    if (!scaled || !layout || layout.startMs === null || layout.endMs === null || realMinutes <= 0) return [] as { at: number; label: string; left: number }[];
    const pxPerMinute = (widthPx - scaled.gaps.filter(gap => gap.collapsed).length * 64) / realMinutes;
    const steps = [15, 30, 60, 120, 240, 480, 1440];
    const stepMinutes = steps.find(step => step * pxPerMinute >= 64) ?? 1440;
    const stepMs = stepMinutes * 60000;
    const collapsedSpans = scaled.gaps.filter(gap => gap.collapsed);
    const result: { at: number; label: string; left: number }[] = [];
    for (let at = Math.ceil(layout.startMs / stepMs) * stepMs; at <= layout.endMs; at += stepMs) {
      const left = scaled.position(at);
      if (left === null) continue;
      if (collapsedSpans.some(gap => at > gap.startMs && at < gap.endMs)) continue;
      const date = new Date(at);
      const label = date.getHours() === 0 && date.getMinutes() === 0
        ? `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} 00:00`
        : clockTime(at);
      // Keep the complete label out of a folded band's fixed-width control.
      const x = left / 100 * widthPx;
      const labelWidth = label.length * 6 + 8;
      if (x + labelWidth > widthPx || collapsedSpans.some(gap =>
        x < gap.toPercent / 100 * widthPx && x + labelWidth > gap.fromPercent / 100 * widthPx)) continue;
      result.push({ at, label, left });
    }
    return result;
  }, [scaled, layout, realMinutes, widthPx]);

  const nowVisible = !!(layout && observedAtMs !== null && layout.endMs === observedAtMs);
  const nowLeft = nowVisible && scaled ? scaled.position(observedAtMs) : null;
  const activeKey = hoverKey ?? focusKey;
  const activeItem = activeKey ? itemsByKey.get(activeKey) ?? null : null;
  const activeCluster = activeKey ? clusters.find(cluster => cluster.key === activeKey) ?? null : null;
  const guideLeft = activeCluster !== null || activeItem?.guide === true
    ? (activeCluster ? activeCluster.x : scaled && activeItem && activeItem.atMs !== null ? scaled.position(activeItem.atMs) : null)
    : null;
  const selectedRunId = selection?.type === "run" ? selection.runId : null;
  // Selecting a delegation (from a row label or an overview card) brings its
  // row into view without moving the horizontal time position.
  useEffect(() => {
    if (selectedRunId === null || hidden) return;
    const row = rootRef.current?.querySelector<HTMLElement>(`[data-key="row:${selectedRunId}"]`);
    row?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [selectedRunId, hidden]);
  const selectedEventKeys = useMemo(() => {
    if (selection?.type !== "item") return null;
    return selection.key.startsWith("event:") ? selection.key : null;
  }, [selection]);
  const openClusterView = openCluster ? clusters.find(cluster => cluster.key === openCluster) ?? null : null;

  const truncation: string[] = [];
  if (timeline?.filtered) truncation.push(`已按筛选显示 ${timeline.rows.length} / ${timeline.totals.allRows} 个委派`);
  if (timeline?.truncated.rows) truncation.push(`委派行已截断：显示 ${timeline.rows.length} / ${timeline.totals.rows} 个`);
  if (timeline?.truncated.spans) truncation.push(`执行片段已截断：显示 ${timeline.spans.length} / ${timeline.totals.spans} 段`);
  if (timeline?.truncated.events) truncation.push(`Host 事件已截断：显示 ${timeline.events.length} / ${timeline.totals.events} 条`);
  const missingEvents = timeline && timeline.truncated.events ? Math.max(0, timeline.totals.events - timeline.events.length) : 0;

  function itemHandlers(key: string) {
    return {
      "data-key": key,
      onFocus: () => setFocusKey(key),
      onMouseEnter: () => setHoverKey(key),
      onMouseLeave: () => setHoverKey(current => (current === key ? null : current)),
      // A single click only pins the inspector; opening needs Enter, a double
      // click or an explicit 打开 control.
      onClick: () => {
        const item = itemsByKey.get(key);
        if (item) props.onSelectItem(item);
      },
      onDoubleClick: () => {
        const item = itemsByKey.get(key);
        if (item) props.onOpenItem(item);
      },
    };
  }

  function renderSpan(row: TimelineRow, span: TimelineSpan) {
    const facts = factsBySpan.get(span.spanId);
    if (!facts || !scaled || facts.item.atMs === null) return null;
    const reversed = facts.endMs !== null && facts.endMs < facts.startMs!;
    if (reversed) return null;
    const left = scaled.position(facts.startMs)!;
    const style = paletteIndex(palette, span.configuration);
    const right = facts.endMs !== null ? scaled.position(facts.endMs) : null;
    const width = right !== null ? Math.max(right - left, 0.3) : 0.3;
    const widthPxSpan = width / 100 * widthPx;
    const selected = selection?.type === "item" && selection.key === facts.item.key;
    const runHighlighted = selectedRunId === row.runId;
    const classes = ["tl-item", "sp", span.kind === "queue" ? "queue" : span.kind === "routing" ? "routing" : span.kind === "host" ? "wait" : "exec"];
    if (facts.outcome === "running") classes.push("running");
    if (facts.outcome === "failed") classes.push("failed");
    if (facts.outcome === "cancelled") classes.push("cancelled");
    if (facts.outcome === "unknown") classes.push("unknown");
    if (span.kind === "host" && span.endAt == null) classes.push("open");
    if (selected) classes.push("selected");
    if (runHighlighted) classes.push("run-member");
    if (span.kind === "execution" && style?.striped) classes.push("striped");
    const colorVars = span.kind === "execution" && style
      ? { "--c": `var(--cfg-${style.color})` } as CSSProperties : undefined;
    const positionStyle: CSSProperties = { left: `${left}%`, width: `${width}%`, ...colorVars };
    const handlers = itemHandlers(facts.item.key);
    const tabIndex = focusKey === facts.item.key ? 0 : -1;
    const aria = itemAria(facts.item);
    if (span.kind === "queue") {
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers} />;
    }
    if (span.kind === "routing") {
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
        {widthPxSpan >= 34 ? <span className="sp-text">路由</span> : null}
      </button>;
    }
    if (span.kind === "host") {
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
        <span className="sp-text">{widthPxSpan >= 110 ? `等待 Host · ${durationShort((facts.endMs ?? observedAtMs ?? 0) - facts.startMs!)}` : widthPxSpan >= 64 ? "等待 Host" : widthPxSpan >= 34 ? "等待" : ""}</span>
      </button>;
    }
    // The bar label uses the friendly name (0.16 0.3); the raw identity stays
    // in the aria label and tooltip.
    const shortModel = namer(span.configuration).text;
    const label = widthPxSpan >= 150
      ? `第${span.turnIndex ?? "?"}轮 · ${shortModel}${facts.outcome !== "finished" ? " · " + outcomeLabel(span, facts.outcome) : ""}`
      : widthPxSpan >= 72 ? `第${span.turnIndex ?? "?"}轮` : widthPxSpan >= 30 ? String(span.turnIndex ?? "·") : "";
    if (facts.outcome === "unknown" && facts.recordedEndMs !== null) {
      // Solid to the last recorded instant, dotted tail reserved through the
      // observation instant by the Host layout.
      const solidWidth = Math.max(scaled.position(facts.recordedEndMs)! - left, 0.3);
      const solidShare = Math.min(100, solidWidth / Math.max(width, 0.3) * 100);
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
        <span className="solid" style={{ width: `${solidShare}%` }}>{solidWidth / 100 * widthPx >= 72 ? `第${span.turnIndex ?? "?"}轮 · ${shortModel}` : ""}</span>
        <i className="end-mark warn" style={{ left: `calc(${solidShare}% - 8px)` }} aria-hidden="true">?</i>
        <span className="sp-text sp-tail" style={{ marginLeft: `calc(${solidShare}% + 10px)` }}>{width - solidWidth >= 4 ? "结束未确认" : ""}</span>
      </button>;
    }
    const endMark = facts.outcome === "failed" ? <i className="end-mark bad" aria-hidden="true">✕</i>
      : facts.outcome === "cancelled" ? <i className="end-mark muted" aria-hidden="true">⊘</i>
        : facts.outcome === "running" ? <i className="pulse" aria-hidden="true" /> : null;
    return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
      data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
      <span className="sp-text">{label}</span>{endMark}
    </button>;
  }

  const hostEventsTrackId = canvasKey ? `host-events-track-${canvasKey}` : "host-events-track";
  const hostEventsOpen = canvasKey !== null && (hostEventsOpenByObjective.get(canvasKey) ?? false) === true;

  const refreshAt = timeline?.observedAt ?? props.summary?.lastActivityAt;

  const body = loading && !timeline
    ? <div className="tl-body">
      <p className="tl-state" role="status">正在读取时间轴…</p>
      <div className="skeleton" style={{ width: "60%" }} />
      <div className="skeleton" style={{ width: "45%", marginLeft: "30%" }} />
      <div className="skeleton" style={{ width: "35%", marginLeft: "52%" }} />
    </div>
    : error && !timeline
      ? <div className="tl-body"><div className="tl-state" role="alert">读取时间轴失败：{error}
        <div style={{ marginTop: 12 }}><button className="button small-button" onClick={props.onRetry}>重试读取</button></div></div></div>
      : timeline && scaled
        ? <div className={"timeline-body" + (asList ? " as-list" : "")}>
          <div ref={scrollRef} className="tl-scroll"
            onScroll={() => { if (openCluster !== null) requestClusterClose(false); }}
            onKeyDown={onCanvasKeyDown}>
            <div ref={gridRef} className="tl-grid" role="group" aria-label="工作目标时间轴"
              style={{ width: `calc(var(--label-w) + ${Math.round(widthPx)}px)` }}>
              <div className="tl-row axis">
                <div className="tl-label">委派 / 时间</div>
                <div className="tl-track">
                  {ticks.map(tick => <span key={tick.at} className="tick" style={{ left: `${tick.left}%` }}>{tick.label}</span>)}
                  {canFold && eligibleGaps.map(gap => gap.collapsed
                    ? <button key={gap.id} type="button" className="fold-button" style={{ left: `${gap.fromPercent}%`, width: `${gap.toPercent - gap.fromPercent}%` }}
                      aria-label={`空闲 ${durationShort(gap.endMs - gap.startMs)}，${clockTime(gap.startMs)} 至 ${clockTime(gap.endMs)}，已折叠，展开`}
                      title={`${clockTime(gap.startMs)}–${clockTime(gap.endMs)} 没有任何片段或事件`}
                      onClick={() => props.onToggleGap(gap.id)}>
                      <span>空闲 {durationShort(gap.endMs - gap.startMs)}</span><span>展开</span>
                    </button>
                    : <button key={gap.id} type="button" className="collapse-button" style={{ left: `${gap.fromPercent + 0.4}%` }}
                      aria-label={`收起空闲 ${durationShort(gap.endMs - gap.startMs)}`}
                      onClick={() => props.onToggleGap(gap.id)}>收起空闲 {durationShort(gap.endMs - gap.startMs)}</button>)}
                  {nowVisible && nowLeft !== null && <span className="now-chip">现在 {clockTime(observedAtMs!)}</span>}
                </div>
              </div>
              <div className={"tl-row markers" + (hostEventsOpen ? "" : " collapsed")} data-nav="">
                <button type="button" className="tl-label markers-toggle"
                  aria-expanded={hostEventsOpen} aria-controls={hostEventsTrackId}
                  onClick={() => setHostEventsOpenByObjective(previous =>
                    new Map(previous).set(canvasKey ?? "", !hostEventsOpen))}>
                  <span>{hostEventsOpen ? "▾" : "▸"} Host 事件{timeline.events.length ? `（${timeline.events.length}）` : ""}</span>
                  {missingEvents > 0 && <span className="trunc-chip">‹ {missingEvents} 条事件未返回</span>}
                  {clusterNotice && <span className="trunc-chip">{clusterNotice}</span>}
                </button>
                {hostEventsOpen && <div className="tl-track" id={hostEventsTrackId}>
                  {clusters.map(cluster => {
                    const single = cluster.items.length === 1 ? cluster.items[0]! : null;
                    const glyph = eventClusterGlyph(cluster.events);
                    const isOpen = openCluster === cluster.key;
                    const clusterSelected = cluster.items.some(item => item.key === selectedEventKeys);
                    const classes = ["tl-item", "mk", cluster.items.length > 1 ? "cluster" : cluster.events[0]!.kind];
                    if (clusterSelected) classes.push("selected");
                    if (isOpen) classes.push("open");
                    const sentence = single
                      ? eventSentence(cluster.events[0]!, rowsById.get(cluster.events[0]!.runId) ?? null)
                      : null;
                    return <button key={cluster.key} type="button" className={classes.join(" ")} style={{ left: `clamp(9px, ${cluster.x}%, calc(100% - 9px))` }}
                      data-key={cluster.key} data-cluster-key={cluster.key} data-x={cluster.x} tabIndex={focusKey === cluster.key ? 0 : -1}
                      aria-expanded={cluster.items.length > 1 ? isOpen || undefined : undefined}
                      aria-controls={cluster.items.length > 1 && isOpen ? `popover-${cluster.key}` : undefined}
                      aria-label={single ? sentence ?? cluster.head : cluster.head}
                      title={single ? sentence ?? cluster.head : cluster.head}
                      onFocus={() => setFocusKey(cluster.key)}
                      onMouseEnter={() => setHoverKey(cluster.key)}
                      onMouseLeave={() => setHoverKey(current => (current === cluster.key ? null : current))}
                      onClick={() => {
                        // Opening the popover selects nothing (0.15 C3).
                        if (single) {
                          props.onSelectItem(single);
                          return;
                        }
                        setClusterNotice(null);
                        setOpenCluster(current => (current === cluster.key ? null : cluster.key));
                      }}
                      onDoubleClick={() => {
                        if (single) props.onOpenItem(single);
                      }}>
                      <span aria-hidden="true">{glyph.glyph}</span>
                      {glyph.count > 0 && <sup className="mk-count" aria-hidden="true">{glyph.count}</sup>}
                    </button>;
                  })}
                  {!hidden && active && openClusterView && openClusterView.items.length > 1 && <MarkerPopover
                    anchor={rootRef.current?.querySelector<HTMLElement>(`[data-cluster-key="${openClusterView.key}"]`) ?? null}
                    cluster={openClusterView} rowsById={rowsById} selectedEventKey={selectedEventKeys}
                    onSelect={item => { setFocusKey(item.key); props.onSelectItem(item); }}
                    onOpen={item => { setFocusKey(item.key); props.onOpenItem(item); requestClusterClose(false); }}
                    requestClose={requestClusterClose} />}
                </div>}
              </div>
              {timeline.rows.map(row => {
                const state = rowStateInfo(row);
                const label = rowLabelItem(row);
                // One line of ~40 characters for a task first-line fallback,
                // with the 取自任务首行 note instead of the full text (U3).
                const title = displayTitle(row.titleSource, row.title);
                const unplaced = (spansByRun.get(row.runId) ?? []).filter(span => {
                  const facts = factsBySpan.get(span.spanId);
                  return !facts || facts.item.atMs === null || facts.endMs === null
                    || (facts.endMs !== null && facts.endMs < facts.startMs!);
                }).length;
                const settle = settleItem(row);
                const settleLeft = settle && settle.atMs !== null ? scaled.position(settle.atMs) : null;
                const runSelected = selectedRunId === row.runId;
                const runOpened = openedRunId === row.runId;
                const settleSelected = selection?.type === "item" && selection.key === settle?.key;
                // The acceptance-wait dashed line (P1.8): only from a recorded,
                // confirmed execution end to the flag, never an invented time.
                const ownSpans = spansByRun.get(row.runId) ?? [];
                const waitText = settle ? acceptanceWaitText(row, ownSpans) : null;
                let waitLine: { from: number; to: number } | null = null;
                if (settle && settleLeft !== null && waitText !== null) {
                  const executions = ownSpans
                    .filter(span => span.kind === "execution")
                    .map(span => ({ span, endMs: toMs(span.endAt) }))
                    .filter((entry): entry is { span: TimelineSpan; endMs: number } => entry.endMs !== null)
                    .sort((left, right) => left.endMs - right.endMs);
                  const last = executions[executions.length - 1];
                  if (last && settle.atMs !== null && last.endMs <= settle.atMs) {
                    const from = scaled.position(last.endMs);
                    if (from !== null && settleLeft >= from) waitLine = { from, to: settleLeft };
                  }
                }
                return <div key={row.runId} className={"tl-row" + (runSelected ? " run-selected" : "")} data-nav="">
                  <button type="button"
                    className={"tl-item tl-label" + (row.kind === "helper" ? " helper" : "") + (runSelected ? " selected-run" : "") + (runOpened ? " opened-run" : "")}
                    style={{ "--depth": row.depth } as CSSProperties}
                    data-key={label.key} data-x={-1} tabIndex={focusKey === label.key ? 0 : -1}
                    title={titleLineTooltip(title)} aria-label={itemAria(label)}
                    onFocus={() => setFocusKey(label.key)}
                    onMouseEnter={() => setHoverKey(label.key)}
                    onMouseLeave={() => setHoverKey(current => (current === label.key ? null : current))}
                    onClick={() => props.onSelectRun(row.runId)}
                    onDoubleClick={() => props.onOpenRun(row.runId)}>
                    <span className="lbl-title">
                      {row.kind === "helper" && <span aria-hidden="true" className="muted">↳</span>}
                      <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
                      {props.newRunIds.has(row.runId) && <span className="new-mark">新</span>}
                      <span className="lbl-name">{title.text}</span>
                      {runOpened && <span className="opened-mark">详情</span>}
                    </span>
                    <span className="lbl-sub" title={namer(row.configuration).title}>{title.fromTask ? "取自任务首行 · " : ""}{state.label} · {row.kind === "helper" ? "协助任务 · " : ""}{namer(row.configuration).text}</span>
                    {unplaced > 0 && <span className="trunc-chip" title="这些片段的时间缺失或颠倒，未在时间轴上放置">⚠ {unplaced} 段时间缺失</span>}
                  </button>
                  <div className="tl-track">
                    {waitLine && <span className="accept-wait-line" style={{ left: `${waitLine.from}%`, width: `${Math.max(waitLine.to - waitLine.from, 0)}%` }}
                      aria-hidden="true" title={waitText ?? undefined} />}
                    {(spansByRun.get(row.runId) ?? []).map(span => renderSpan(row, span))}
                    {settle && settleLeft !== null && <button type="button"
                      className={"tl-item flag " + (row.acceptanceVerdict === "rejected" ? "reject" : "accept") + (settleSelected ? " selected" : "") + (runSelected ? " run-member" : "")}
                      style={{ left: `${settleLeft}%` }} data-x={settleLeft}
                      tabIndex={focusKey === settle.key ? 0 : -1} aria-label={itemAria(settle)} title={itemAria(settle)}
                      {...itemHandlers(settle.key)}>{row.acceptanceVerdict === "rejected" ? "!" : "✓"}</button>}
                  </div>
                </div>;
              })}
              <div className="tl-overlay" aria-hidden="true">
                {canFold && eligibleGaps.map(gap => gap.collapsed
                  ? <div key={gap.id} className="fold-band" style={{ left: `${gap.fromPercent}%`, width: `${gap.toPercent - gap.fromPercent}%` }} />
                  : <div key={gap.id} className="expanded-band" style={{ left: `${gap.fromPercent}%`, width: `${gap.toPercent - gap.fromPercent}%` }} />)}
                {nowVisible && nowLeft !== null && <div className="now-line" style={{ left: `${nowLeft}%` }} />}
                {guideLeft !== null && <div className="guide-line" style={{ left: `${guideLeft}%` }} />}
              </div>
            </div>
          </div>
          <ObjectiveChronology entries={chronology} palette={palette} selectedKey={selection?.type === "item" ? selection.key : null}
            openedKey={openedKey}
            onSelectItem={item => { setFocusKey(item.key); props.onSelectItem(item); }}
            onOpenItem={item => { setFocusKey(item.key); props.onOpenItem(item); }} />
        </div>
        : null;

  return <div className="timeline-view" ref={rootRef} hidden={hidden}>
    <ObjectiveOverview summary={props.summary} timeline={timeline} loading={loading} stale={stale}
      compact={compactViewport || narrowViewport} hidden={hidden}
      onBackToList={props.onBackToList} headerActions={props.headerActions} />
    {timeline && <DelegationStrip timeline={timeline} selectedRunId={selectedRunId}
      collapsed={cardsActuallyCollapsed}
      onToggleCollapsed={() => setCardsCollapsed(!cardsActuallyCollapsed)}
      onSelectRun={props.onSelectRun} onOpenRun={props.onOpenRun} />}
    <div className="tl-toolbar">
      <div className="legend" aria-label="执行配置图例">
        <span className="legend-title">执行配置</span>
        {palette.length ? palette.map(entry => <span key={entry.key} className="legend-item" title={entry.rawTitle ?? entry.label}>
          <span className={"swatch" + (entry.striped ? " striped" : "")} style={{ "--c": `var(--cfg-${entry.color})` } as CSSProperties} />{entry.label}
        </span>) : <span className="legend-item">—</span>}
      </div>
      <div className="legend" aria-label="状态图例">
        <span className="legend-item"><span className="swatch queue" />排队</span>
        <span className="legend-item"><span className="swatch routing" />路由</span>
        <span className="legend-item"><span className="swatch wait" />等待 Host</span>
        <span className="legend-item"><span className="glyph ok" aria-hidden="true">▸</span>执行中</span>
        <span className="legend-item"><span className="glyph bad" aria-hidden="true">✕</span>失败</span>
        <span className="legend-item"><span className="glyph" aria-hidden="true">⊘</span>已取消</span>
        <span className="legend-item"><span className="glyph warn" aria-hidden="true">?</span>结束未确认</span>
      </div>
      <div className="tl-tools">
        {canFold && eligibleGaps.length > 0 && <button type="button" className="button small-button"
          aria-pressed={eligibleGaps.every(gap => props.expandedGapIds.has(gap.id))}
          onClick={() => props.onSetExpanded(new Set(eligibleGaps.map(gap => gap.id)))}>展开全部空闲</button>}
        {canFold && eligibleGaps.length > 0 && props.expandedGapIds.size > 0 && <button type="button" className="button small-button"
          onClick={() => props.onSetExpanded(new Set())}>折叠空闲</button>}
        <button ref={listToggleRef} type="button" className="button small-button list-toggle" aria-pressed={asList}
          onClick={() => setAsList(current => !current)}>以列表查看</button>
        {!asList && <div className="tl-zoom" role="group" aria-label="时间轴缩放">
          <button type="button" className="button small-button" aria-label="缩小" disabled={zoomAtFit}
            title={zoomAtFit ? "已是适应窗口的最小刻度" : "缩小时间轴（键盘 -）"} onClick={zoomOut}>−</button>
          <button type="button" className="button small-button" aria-label="放大" disabled={zoomAtMax}
            title={zoomAtMax ? "已达最大刻度" : "放大时间轴（键盘 +）"} onClick={zoomIn}>+</button>
          <button type="button" className="button small-button" aria-pressed={zoomAtFit}
            title="缩放到有活动的时间段（键盘 0）" onClick={() => changeZoom(null)}>适应窗口</button>
        </div>}
        <span className={"refresh-state" + (stale ? " stale" : "")}
          title={stale && timeline
            ? `最近一次读取失败；显示的是 ${clockSeconds(timeline.observedAt)} 的数据`
            : `每 3 秒读取一次，不调用模型 · 数据截至 ${clockSeconds(refreshAt)}`}>
          {stale && timeline
            ? `读取失败 · 显示 ${clockTime(timeline.observedAt)} 的数据`
            : `自动刷新 · ${clockTime(refreshAt)}`}
        </span>
        {stale && <button type="button" className="button small-button" onClick={props.onRetry}>重试读取</button>}
      </div>
    </div>
    {timeline && truncation.length > 0 && <div className="banner trunc-banner" role="status">
      时间轴读取有边界：{truncation.join("；")}。可调整筛选或打开单个委派查看其完整记录。
    </div>}
    {body}
    <InspectorSeparator min={DRAWER_MIN_PX} max={drawerMax} value={drawerValue}
      onChange={value => { setDrawerCollapsed(false); setDrawerHeight(value); }}
      onReset={() => { setDrawerCollapsed(false); setDrawerHeight(null); }} />
    <div className={"inspector-dock" + (drawerCollapsed ? " collapsed" : "")} style={{ height: `${drawerValue}px` }}>
      <div className="inspector-dock-head">
        <span className="inspector-dock-title">检查器</span>
        <button type="button" className="button small-button"
          aria-expanded={!drawerCollapsed}
          onClick={() => setDrawerCollapsed(current => !current)}>{drawerCollapsed ? "展开" : "收起"}</button>
      </div>
      {!drawerCollapsed && <div className="inspector-dock-body">
        <TimelineInspector selection={selection}
          previewItem={activeItem}
          previewClusterHead={activeCluster ? activeCluster.head : null}
          timeline={timeline} itemsByKey={itemsByKey} profiles={props.profiles}
          truncatedEvents={!!timeline?.truncated.events}
          onOpen={props.onOpenItem} onSelectItem={props.onSelectItem} onSelectRun={props.onSelectRun}
          onUnpin={props.onClearSelection} />
      </div>}
    </div>
  </div>;
}
