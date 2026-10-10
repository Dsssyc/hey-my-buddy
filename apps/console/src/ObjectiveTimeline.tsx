import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import type { ObjectiveSummary, ObjectiveTimeline as ObjectiveTimelineData, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import { createTimelineLayout } from "./objective-timeline-layout";
import { fitPixelsPerMinute, MAX_PIXELS_PER_MINUTE, scaleTimeline, TIMELINE_IDLE_PX } from "./objective-timeline-scale";
import type { SpanOutcome, TimelineItem, FriendlyProfile } from "./objective-display";
import {
  buildChronology, clockTime, configurationNamer, displayTitle, durationShort, eventClusterGlyph,
  eventClusterLabel, eventSentence, outcomeLabel, paletteIndex, rowStateInfo, spanFacts, rowLabelItem, settleItem,
  eventItem, toMs, configurationPalette, acceptanceWaitMs, acceptanceWaitText, titleLineTooltip,
} from "./objective-display";
import { ObjectiveChronology } from "./ObjectiveChronology";
import { DelegationStrip, ObjectiveOverview } from "./ObjectiveOverview";
import { TimelineInspector } from "./TimelineInspector";
import { MarkerPopover, type MarkerClusterView } from "./MarkerPopover";
import { Popover } from "./Popover";
import { idleReadout, TimelineReadout } from "./TimelineReadout";
import { buildInspectorCard, type InspectorCard, type InspectorSelection } from "./inspector-card";

/** Markers closer than this many actual track pixels merge into one numbered marker. */
const MARKER_MERGE_PX = 14;
/** Nominal track width before measurement (and in tests without layout). */
const FALLBACK_VIEWPORT_PX = 800;
const FALLBACK_LABEL_PX = 240;
/**
 * Right-edge room after the last instant: flags, end marks, the running pulse
 * and minimum-width bars centred on the final time stay inside the canvas, so
 * 适应窗口 has no internal horizontal overflow.
 */
const TRACK_END_PX = 12;
/** The inspector drawer's default and keyboard-adjusted geometry (P2.1). */
const DRAWER_DEFAULT_PX = 168;
const DRAWER_MIN_PX = 44;
const DRAWER_STEP_PX = 16;
/** Below this the drawer body would be a sliver, so it folds to its title row. */
const DRAWER_USEFUL_PX = 96;
/** The timeline keeps this height before the drawer may take more (P2.1). */
const TIMELINE_MIN_PX = 200;
const ZOOM_STEP = 1.5;

/**
 * The natural (unshrunk) outer height of a fixed section of the column. Using
 * scrollHeight means a header the CSS fallback already compressed is still
 * counted at full size, so the drawer yields first and never locks the
 * header in its compressed form.
 */
function naturalHeight(element: HTMLElement): number {
  const style = getComputedStyle(element);
  const px = (value: string) => Number.parseFloat(value) || 0;
  const borders = px(style.borderTopWidth) + px(style.borderBottomWidth);
  const height = Math.max(element.offsetHeight, element.scrollHeight + borders);
  return height + px(style.marginTop) + px(style.marginBottom);
}

type SpanFacts = { item: TimelineItem; startMs: number | null; endMs: number | null; recordedEndMs: number | null; outcome: SpanOutcome };
type MarkerCluster = MarkerClusterView & { head: string };

export type ObjectiveTimelineProps = {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimelineData | null;
  /**
   * The display clock for layout and the 现在 marker: the response's server
   * anchor advanced by the client time since that anchor was read, so an
   * unchanged (304) poll keeps the wall clock moving without rewriting the
   * response's own `observedAt`. Absent reads as the response's anchor.
   */
  displayObservedAt?: string | null;
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
 * structure follows 0.16 P2.1 — a compact header, a collapsible delegation
 * band, the toolbar, the timeline as the only row-scroll area (min 200px), a
 * keyboard/pointer separator and the inspector as a summary-first bottom
 * drawer. Occupancy and folding stay in the frozen layout module; the default
 * scale fits observed activity (适应窗口) and +/−/0 zoom by ×1.5 steps anchored
 * on the selection or viewport centre. Single clicks only select the pinned
 * inspector; Enter, double-click and the explicit 打开 controls open details.
 */
export function ObjectiveTimeline(props: ObjectiveTimelineProps) {
  const { timeline, loading, error, stale, hidden, openedKey, openedRunId, selection, active = true } = props;
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const [asList, setAsList] = useState(false);
  const [legendOpen, setLegendOpen] = useState(false);
  const legendButtonRef = useRef<HTMLButtonElement>(null);
  const [openCluster, setOpenCluster] = useState<string | null>(null);
  const [clusterNotice, setClusterNotice] = useState<string | null>(null);
  useEffect(() => { if (hidden || !active) { setOpenCluster(null); setLegendOpen(false); } }, [hidden, active]);
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

  // The inspector drawer (P2.1/P2.2): the user's height and open/closed choice
  // persist for the page session, and the shown height is re-clamped from
  // reactive measurements. The drawer begins folded to its summary row; the
  // narrow layout caps an opened drawer at 40vh.
  const [drawerHeight, setDrawerHeight] = useState<number | null>(null);
  const [drawerCollapsed, setDrawerCollapsed] = useState<boolean>(true);
  const [columnBox, setColumnBox] = useState<{ column: number; reserved: number } | null>(null);
  const boxObserver = useRef<ResizeObserver | null>(null);
  const observedSections = useRef<Element[]>([]);
  useEffect(() => {
    const element = rootRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const column = element.clientHeight;
      if (column <= 0) return; // hidden: keep the last geometry
      let reserved = 0;
      for (const child of element.children) {
        if (!(child instanceof HTMLElement)) continue;
        if (child.matches(".timeline-body, .tl-body, .inspector-dock")) continue;
        reserved += naturalHeight(child);
      }
      reserved = Math.round(reserved);
      setColumnBox(previous => previous && previous.column === column && previous.reserved === reserved
        ? previous : { column, reserved });
    };
    const observer = new ResizeObserver(measure);
    boxObserver.current = observer;
    observer.observe(element);
    measure();
    return () => {
      observer.disconnect();
      boxObserver.current = null;
      observedSections.current = [];
    };
  }, []);
  // Header, band, toolbar and banner come and go with the data; observe the
  // current set so their own size changes re-clamp the drawer too.
  useEffect(() => {
    const element = rootRef.current;
    const observer = boxObserver.current;
    if (!element || !observer) return;
    const sections = [...element.children];
    const previous = observedSections.current;
    if (sections.length === previous.length && sections.every((section, index) => section === previous[index])) return;
    for (const section of previous) observer.unobserve(section);
    for (const section of sections) observer.observe(section);
    observedSections.current = sections;
  });
  const [viewportHeight, setViewportHeight] = useState(() =>
    typeof window === "undefined" || !Number.isFinite(window.innerHeight) ? 800 : window.innerHeight);
  useEffect(() => {
    const onResize = () => setViewportHeight(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const drawerShareMax = narrowViewport ? viewportHeight * 0.4 : (columnBox?.column ?? 400) * 0.45;
  const drawerRoom = columnBox ? columnBox.column - columnBox.reserved - TIMELINE_MIN_PX : Number.POSITIVE_INFINITY;
  const drawerFitMax = Math.min(drawerShareMax, drawerRoom);
  // An explicit 展开 in a short window still opens a usable drawer (within its
  // share); the CSS fallback then compresses header and timeline, and the
  // column scrolls as the last resort, so the drawer bottom stays reachable.
  const drawerMax = Math.max(DRAWER_MIN_PX, Math.round(drawerCollapsed === false
    ? Math.max(drawerFitMax, Math.min(drawerShareMax, DRAWER_USEFUL_PX))
    : drawerFitMax));
  // The summary row is the default on every viewport; an explicit expansion
  // keeps the drawer open until the user folds it again.
  const drawerActuallyCollapsed = drawerCollapsed;
  const drawerValue = drawerActuallyCollapsed ? DRAWER_MIN_PX : Math.max(DRAWER_MIN_PX, Math.min(drawerHeight ?? DRAWER_DEFAULT_PX, drawerMax));

  // Layout and the 现在 marker run on the display clock (the anchor advanced
  // by the client since its read); the response's own observedAt stays the
  // recorded source fact, shown verbatim in the failure tooltip.
  const observedAtMs = toMs(props.displayObservedAt ?? timeline?.observedAt);
  const layout = useMemo(
    () => timeline
      ? createTimelineLayout({ ...timeline, observedAt: props.displayObservedAt ?? timeline.observedAt })
      : null,
    [timeline, props.displayObservedAt],
  );
  const canvasKey = timeline?.objective.objectiveId ?? null;

  // Zoom (P2.3): null means 适应窗口; a number is an absolute px/minute. A
  // manual level and its pending anchor belong to one objective (review §5):
  // an actual objective switch starts from 适应窗口, while same-objective
  // polling and returning from a detail keep the level.
  const [zoomState, setZoomState] = useState<{ objectiveId: string; ppm: number } | null>(null);
  const zoom = zoomState !== null && zoomState.objectiveId === canvasKey ? zoomState.ppm : null;
  const zoomAnchor = useRef<{ objectiveId: string; timeMs: number; screenX: number } | null>(null);
  useEffect(() => {
    if (canvasKey === null) return;
    setZoomState(current => current !== null && current.objectiveId !== canvasKey ? null : current);
    if (zoomAnchor.current && zoomAnchor.current.objectiveId !== canvasKey) zoomAnchor.current = null;
  }, [canvasKey]);

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
      // The fit track is the actual remaining width (review §2): a 500px
      // column with 240px labels has a 260px track, never a floor that would
      // force horizontal overflow. Rounding down keeps a fractional label
      // column from adding a pixel of overflow.
      const track = width > 0 ? Math.floor(width - labelWidthPx()) : 0;
      if (track > 0) setViewport(track);
    };
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [canvasKey, asList]);

  // The time scale ends TRACK_END_PX before the canvas edge (see above).
  const effectiveViewport = Math.max(1, (viewport ?? FALLBACK_VIEWPORT_PX) - TRACK_END_PX);
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
    if (!element || !anchor || anchor.objectiveId !== canvasKey || !scaled || !layout || layout.startMs === null) return;
    const percent = scaled.position(anchor.timeMs);
    if (percent === null) return;
    const labelW = labelWidthPx();
    const trackX = percent / 100 * scaled.widthPx;
    element.scrollLeft = Math.max(0, Math.round(labelW + trackX - anchor.screenX));
  }, [scaled, layout, canvasKey]);

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
        map.set(span.spanId, spanFacts(span, row, observedAtMs, props.profiles));
      }
    }
    return map;
  }, [timeline, spansByRun, observedAtMs, props.profiles]);

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
  // Preserve the selected facts even when the drawer is folded and a later
  // filtered read omits that record. Opening the drawer can still show the
  // last recorded card with its missing-record warning.
  const cardCache = useRef<{ key: string; card: InspectorCard } | null>(null);
  const cardKey = selection ? selection.type === "run" ? `run:${selection.runId}` : selection.key : "";
  const currentCard = timeline && selection ? buildInspectorCard(selection, timeline, itemsByKey, props.profiles) : null;
  if (currentCard) cardCache.current = { key: cardKey, card: currentCard };
  const selectedCard = currentCard ?? (cardCache.current?.key === cardKey ? cardCache.current.card : null);

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
    (scaled?.gaps ?? []).filter(gap => gap.collapsed),
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
    return item.ariaLabel ?? `${item.head}，${item.parts.join("，")}`;
  }

  /** The screen-x of a time instant inside the scroll viewport, or null when off-canvas. */
  function screenXAt(timeMs: number): number | null {
    const element = scrollRef.current;
    if (!element || !scaled) return null;
    const percent = scaled.position(timeMs);
    if (percent === null) return null;
    return labelWidthPx() + percent / 100 * scaled.widthPx - element.scrollLeft;
  }

  /** A zoom step keeps the anchor time (selection in view, else the visible track centre) at its screen position. */
  function changeZoom(next: number | null) {
    const element = scrollRef.current;
    if (canvasKey === null) return;
    if (element && scaled && layout && layout.startMs !== null && layout.endMs !== null && !asList) {
      const labelW = labelWidthPx();
      let anchorTime: number | null = null;
      let anchorScreenX: number | null = null;
      const selectedTime = selection?.type === "item"
        ? itemsByKey.get(selection.key)?.atMs ?? null
        : null;
      if (selectedTime !== null) {
        const x = screenXAt(selectedTime);
        // The sticky label column covers the left labelWidth of the viewport,
        // so only a selection right of it is actually visible (review §4).
        if (x !== null && x >= labelW && x <= element.clientWidth) {
          anchorTime = selectedTime;
          anchorScreenX = x;
        }
      }
      if (anchorTime === null || anchorScreenX === null) {
        // The time at the centre of the visible track (right of the sticky
        // labels), found by bisection over the monotonic position mapping
        // (fold bands included).
        const screenX = labelW + Math.max(0, element.clientWidth - labelW) / 2;
        const trackX = element.scrollLeft + screenX - labelW;
        const percent = Math.max(0, Math.min(100, trackX / scaled.widthPx * 100));
        let lo = layout.startMs, hi = layout.endMs;
        for (let i = 0; i < 44; i += 1) {
          const mid = (lo + hi) / 2;
          const at = scaled.position(mid) ?? 0;
          if (at < percent) lo = mid; else hi = mid;
        }
        anchorTime = (lo + hi) / 2;
        anchorScreenX = screenX;
      }
      zoomAnchor.current = { objectiveId: canvasKey, timeMs: anchorTime, screenX: anchorScreenX };
    }
    setZoomState(next === null ? null : { objectiveId: canvasKey, ppm: Math.max(0, next) });
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
    // Keyboard zoom (P2.3): + / - / 0 while focus is inside the timeline grid
    // (not in the portalled Host event popup). Ctrl/Meta/Alt stay with the
    // browser's own shortcuts (review §3); Shift is allowed because Shift+= is
    // how "+" is typed.
    if (!asList && !event.ctrlKey && !event.metaKey && !event.altKey
      && gridRef.current?.contains(target) === true
      && (event.key === "+" || event.key === "=" || event.key === "-" || event.key === "0")) {
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
    // Fixed idle blocks leave the remaining pixels to real activity.
    const collapsedGapPx = scaled.gaps.filter(gap => gap.collapsed)
      .reduce((total, gap) => total + (gap.toPercent - gap.fromPercent) / 100 * widthPx, 0);
    const pxPerMinute = (widthPx - collapsedGapPx) / realMinutes;
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
  const activeKey = focusedKey ?? (selection?.type === "item" ? selection.key : null);
  const activeItem = activeKey ? itemsByKey.get(activeKey) ?? null : null;
  const activeCluster = focusedKey ? clusters.find(cluster => cluster.key === focusedKey) ?? null : null;
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
      onFocus: () => { setFocusKey(key); setFocusedKey(key); },
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
    if (span.kind === "routing" && span.routing?.routingMode) {
      classes.push(span.routing.routingMode);
    }
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
        {facts.outcome === "failed" && <i className="routing-cross" aria-hidden="true" />}
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
      // Compare the existing placed intervals on this row. Touching endpoints
      // and spans without a usable duration do not occupy the unknown tail.
      const tailOverlaps = facts.endMs !== null && facts.endMs > facts.recordedEndMs
        && (spansByRun.get(row.runId) ?? []).some(other => {
          if (other.spanId === span.spanId) return false;
          const next = factsBySpan.get(other.spanId);
          return next?.startMs != null && next.endMs !== null
            && next.endMs > next.startMs
            && next.startMs < facts.endMs! && next.endMs > facts.recordedEndMs!;
        });
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
        <span className="solid" style={{ width: `${solidShare}%` }} aria-hidden="true" />
        <span className="sp-text sp-solid-text" style={{ width: `${solidShare}%` }}>{solidWidth / 100 * widthPx >= 72 ? `第${span.turnIndex ?? "?"}轮 · ${shortModel}` : ""}</span>
        <i className="end-mark warn" style={{ left: `calc(${solidShare}% - 8px)` }} aria-hidden="true">?</i>
        <span className="sp-text sp-tail" style={{ marginLeft: `calc(${solidShare}% + 10px)` }}>{width - solidWidth >= 4 && !tailOverlaps ? "结束未确认" : ""}</span>
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
              style={{ width: `calc(var(--label-w) + ${Math.round(widthPx) + TRACK_END_PX}px)`, "--track-end": `${TRACK_END_PX}px` } as CSSProperties}>
              <div className="tl-row axis">
                <div className="tl-label">委派 / 时间</div>
                <div className="tl-track">
                  {ticks.map(tick => <span key={tick.at} className="tick" style={{ left: `${tick.left}%` }}>{tick.label}</span>)}
                  {eligibleGaps.map(gap => <span key={gap.id} className="idle-block" tabIndex={0} data-gap-id={gap.id}
                    style={{ left: `${gap.fromPercent}%`, width: `${TIMELINE_IDLE_PX}px` }}
                    aria-label={idleReadout(gap)}
                    title={idleReadout(gap)}>
                    <span aria-hidden="true">//</span>
                  </span>)}
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
                      onFocus={() => { setFocusKey(cluster.key); setFocusedKey(cluster.key); }}
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
                      <span className="tl-mark-text" aria-hidden="true">{glyph.glyph}
                        {glyph.count > 0 && <sup className="mk-count">{glyph.count}</sup>}
                      </span>
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
                // The acceptance-wait dashed line (P1.8): only from the last
                // execution's recorded end with an explicitly confirmed stop to
                // the flag — the same measurement the inspector shows — never
                // an invented time.
                const ownSpans = spansByRun.get(row.runId) ?? [];
                const waitMs = settle ? acceptanceWaitMs(row, ownSpans) : null;
                const waitText = settle ? acceptanceWaitText(row, ownSpans) : null;
                const acceptedMs = toMs(row.acceptedAt);
                let waitLine: { from: number; to: number } | null = null;
                if (settleLeft !== null && waitMs !== null && waitText !== null && acceptedMs !== null) {
                  const from = scaled.position(acceptedMs - waitMs);
                  if (from !== null && settleLeft >= from) waitLine = { from, to: settleLeft };
                }
                return <div key={row.runId} className={"tl-row" + (runSelected ? " run-selected" : "")} data-nav="">
                  <button type="button"
                    className={"tl-item tl-label" + (row.kind === "helper" ? " helper" : "") + (runSelected ? " selected-run" : "") + (runOpened ? " opened-run" : "")}
                    style={{ "--depth": row.depth } as CSSProperties}
                    data-key={label.key} data-x={-1} tabIndex={focusKey === label.key ? 0 : -1}
                    title={titleLineTooltip(title)} aria-label={itemAria(label)}
                    onFocus={() => { setFocusKey(label.key); setFocusedKey(label.key); }}
                    onClick={() => props.onSelectRun(row.runId)}
                    onDoubleClick={() => props.onOpenRun(row.runId)}>
                    <span className="lbl-title">
                      {row.kind === "helper" && <span aria-hidden="true" className="muted">↳</span>}
                      <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
                      {props.newRunIds.has(row.runId) && <span className="new-mark">新</span>}
                      <span className="lbl-name">{title.text}</span>
                      {runOpened && <span className="opened-mark">详情</span>}
                    </span>
                    <span className="lbl-sub" title={namer(row.configuration).title}>{state.label} · {row.kind === "helper" ? "协助任务 · " : ""}{namer(row.configuration).text}</span>
                    {unplaced > 0 && <span className="trunc-chip" title="片段时间缺失或异常，未定位">⚠ {unplaced} 段时间缺失</span>}
                  </button>
                  <div className="tl-track">
                    {waitLine && <span className="accept-wait-line" style={{ left: `${waitLine.from}%`, width: `${Math.max(waitLine.to - waitLine.from, 0)}%` }}
                      aria-hidden="true" title={waitText ?? undefined} />}
                    {(spansByRun.get(row.runId) ?? []).map(span => renderSpan(row, span))}
                    {settle && settleLeft !== null && <button type="button"
                      className={"tl-item flag " + (row.acceptanceVerdict === "rejected" ? "reject" : "accept") + (settleSelected ? " selected" : "") + (runSelected ? " run-member" : "")}
                      style={{ left: `${settleLeft}%` }} data-x={settleLeft}
                      tabIndex={focusKey === settle.key ? 0 : -1} aria-label={itemAria(settle)} title={itemAria(settle)}
                      {...itemHandlers(settle.key)}><span className="tl-mark-text" aria-hidden="true">{row.acceptanceVerdict === "rejected" ? "!" : "✓"}</span></button>}
                  </div>
                </div>;
              })}
              <div className="tl-overlay" aria-hidden="true">
                {eligibleGaps.map(gap => <div key={gap.id} className="fold-band"
                  style={{ left: `${gap.fromPercent}%`, width: `${TIMELINE_IDLE_PX}px` }} />)}
                {!hidden && active && !asList && <TimelineReadout gridRef={gridRef} scrollRef={scrollRef} scale={scaled} />}
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
      compact={compactViewport || narrowViewport}
      onBackToList={props.onBackToList} headerActions={props.headerActions} />
    {timeline && <DelegationStrip timeline={timeline} selectedRunId={selectedRunId}
      collapsed={cardsActuallyCollapsed}
      onToggleCollapsed={() => setCardsCollapsed(!cardsActuallyCollapsed)}
      onSelectRun={props.onSelectRun} onOpenRun={props.onOpenRun} />}
    <div className="tl-toolbar">
      <button ref={legendButtonRef} type="button" className="button small-button" aria-expanded={legendOpen}
        aria-controls="timeline-legend-popover" title="图例" onClick={() => setLegendOpen(current => !current)}>图例</button>
      {legendOpen && !hidden && active && <Popover anchor={legendButtonRef.current} id="timeline-legend-popover"
        label="时间轴图例" className="timeline-legend-popover" onClose={() => setLegendOpen(false)}>
      <div className="legend" aria-label="执行配置图例">
        <span className="legend-title">执行配置</span>
        {palette.length ? palette.map(entry => <span key={entry.key} className="legend-item" title={entry.rawTitle ?? entry.label}>
          <span className={"swatch" + (entry.striped ? " striped" : "")} style={{ "--c": `var(--cfg-${entry.color})` } as CSSProperties} />{entry.label}
        </span>) : <span className="legend-item">—</span>}
      </div>
      <div className="legend" aria-label="状态图例">
        <span className="legend-item"><span className="swatch queue" />排队</span>
        <span className="legend-item"><span className="swatch routing fast" />快速路由</span>
        <span className="legend-item"><span className="swatch routing review" />审阅路由</span>
        <span className="legend-item"><span className="swatch wait" />等待 Host</span>
        <span className="legend-item"><span className="glyph ok" aria-hidden="true">▸</span>执行中</span>
        <span className="legend-item"><span className="glyph bad" aria-hidden="true">✕</span>失败</span>
        <span className="legend-item"><span className="glyph" aria-hidden="true">⊘</span>已取消</span>
        <span className="legend-item"><span className="glyph warn" aria-hidden="true">?</span>结束未确认</span>
      </div>
      </Popover>}
      <div className="tl-tools">
        {!asList && <div className="tl-zoom" role="group" aria-label="时间轴缩放">
          <button type="button" className="button small-button" aria-label="缩小" disabled={zoomAtFit}
            title={zoomAtFit ? "已是适应窗口的最小刻度" : "缩小时间轴（键盘 -）"} onClick={zoomOut}>−</button>
          <button type="button" className="button small-button" aria-label="放大" disabled={zoomAtMax}
            title={zoomAtMax ? "已达最大刻度" : "放大时间轴（键盘 +）"} onClick={zoomIn}>+</button>
          <button type="button" className="button small-button" aria-pressed={zoomAtFit}
            title="缩放到有活动的时间段（键盘 0）" onClick={() => changeZoom(null)}>适应窗口</button>
        </div>}
        {stale && timeline && <span className="refresh-state stale" role="alert"
          title={`最近一次读取失败；显示的是 ${timeline.observedAt} 的数据`}>
          读取失败 · 显示 {clockTime(timeline.observedAt)} 的数据
        </span>}
        {stale && <button type="button" className="button small-button" onClick={props.onRetry}>重试读取</button>}
        {/* The view switch stays the toolbar's last control, at the far right. */}
        <div className="timeline-view-switch" role="group" aria-label="时间轴视图">
          <button ref={listToggleRef} type="button" className="button small-button list-toggle" aria-pressed={!asList}
            onClick={() => { setAsList(false); setLegendOpen(false); }}>时间轴</button>
          <button type="button" className="button small-button list-toggle" aria-pressed={asList}
            onClick={() => { setAsList(true); setLegendOpen(false); }}>列表</button>
        </div>
      </div>
    </div>
    {timeline && truncation.length > 0 && <div className="banner trunc-banner" role="status">
      时间轴记录不完整：{truncation.join("；")}。可调整筛选或打开委派详情。
    </div>}
    {body}
    {!drawerActuallyCollapsed && <InspectorSeparator min={DRAWER_MIN_PX} max={drawerMax} value={drawerValue}
      onChange={value => { setDrawerCollapsed(false); setDrawerHeight(value); }}
      onReset={() => { setDrawerCollapsed(false); setDrawerHeight(null); }} />}
    <div className={"inspector-dock" + (drawerActuallyCollapsed ? " collapsed" : "")} style={{ height: `${drawerValue}px` }}>
      <div className="inspector-dock-head">
        <span className="inspector-dock-title">{selectedCard
          ? [selectedCard.head, selectedCard.fields.find(field => field.label === "委派")?.value,
            selectedCard.fields.find(field => field.label === "时间")?.value].filter(Boolean).join(" · ")
          : selection ? "选中记录 · 当前读取范围外" : "检查器"}</span>
        <button type="button" className="icon-button inspector-dock-toggle"
          aria-label={drawerActuallyCollapsed ? "展开检查器" : "收起检查器"}
          title={drawerActuallyCollapsed ? "展开检查器" : "收起检查器"}
          aria-expanded={!drawerActuallyCollapsed}
          onClick={() => setDrawerCollapsed(!drawerActuallyCollapsed)}>{drawerActuallyCollapsed ? "▴" : "▾"}</button>
      </div>
      {!drawerActuallyCollapsed && <div className="inspector-dock-body">
        <TimelineInspector selection={selection} cachedCard={selectedCard}
          timeline={timeline} itemsByKey={itemsByKey} profiles={props.profiles}
          truncatedEvents={!!timeline?.truncated.events}
          onOpen={props.onOpenItem} onSelectItem={props.onSelectItem} onSelectRun={props.onSelectRun}
          onUnpin={props.onClearSelection} />
      </div>}
    </div>
  </div>;
}
