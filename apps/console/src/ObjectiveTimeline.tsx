import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import type { ObjectiveSummary, ObjectiveTimeline, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import { createTimelineLayout, TIMELINE_FOLD_THRESHOLD_MS } from "./objective-timeline-layout";
import type { TimelineGap, TimelineLayout } from "./objective-timeline-layout";
import type { ConfigurationStyle, SpanOutcome, TimelineItem } from "./objective-display";
import {
  CATEGORY_LABEL, COUNT_ORDER, categoryTone, clockSeconds, clockTime, configurationLabel, durationShort,
  eventVocab, outcomeLabel, paletteIndex, rowStateInfo, spanFacts, rowLabelItem, settleItem,
  eventItem, toMs, totalDelegations, uncertainTailEnd, buildChronology, configurationPalette,
} from "./objective-display";
import { ObjectiveChronology } from "./ObjectiveChronology";
import { Badge } from "./ui";
import { excerpt } from "./task-state";

/** Markers closer than this share of the track merge into one numbered marker. */
const MARKER_MERGE_PERCENT = 1.6;
/** Nominal track width for density decisions before measurement (and in tests). */
const FALLBACK_TRACK_PX = 800;
const LABEL_COLUMN_PX = 240;

function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const update = () => setWidth(element.clientWidth);
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

type SpanFacts = { item: TimelineItem; startMs: number | null; endMs: number | null; outcome: SpanOutcome };
type MarkerCluster = { key: string; x: number; events: TimelineEvent[]; items: TimelineItem[]; lines: string[]; head: string };

export type ObjectiveTimelineProps = {
  summary: ObjectiveSummary | null;
  timeline: ObjectiveTimeline | null;
  loading: boolean;
  error: string;
  stale: boolean;
  newRunIds: ReadonlySet<string>;
  hidden: boolean;
  openedKey: string | null;
  openedRunId: string | null;
  locked: boolean;
  expandedGapIds: ReadonlySet<string>;
  onToggleGap: (gapId: string) => void;
  onSetExpanded: (gapIds: Set<string>) => void;
  onOpenItem: (item: TimelineItem) => void;
  onRetry: () => void;
  onBackToList: () => void;
};

/**
 * Right pane, layer one: the read-only work-objective timeline. All placement
 * and folding come from the frozen pure layout module; this component only
 * renders recorded facts, keeps focus/expanded state across refreshes, and
 * stays mounted (hidden) while a delegation detail is open.
 */
export function ObjectiveTimeline(props: ObjectiveTimelineProps) {
  const { timeline, loading, error, stale, hidden, openedKey, openedRunId } = props;
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [hoverKey, setHoverKey] = useState<string | null>(null);
  const [asList, setAsList] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const listToggleRef = useRef<HTMLButtonElement>(null);
  const [overlayRef, overlayWidth] = useElementWidth<HTMLDivElement>();
  const savedScroll = useRef<number | null>(null);
  const wasHidden = useRef(false);

  const observedAtMs = toMs(timeline?.observedAt) ?? null;
  const layout: TimelineLayout | null = useMemo(
    () => timeline ? createTimelineLayout(timeline, props.expandedGapIds) : null,
    [timeline, props.expandedGapIds],
  );

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
  const palette = useMemo(() => configurationPalette(spansByRun), [spansByRun]);

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
      for (const row of timeline.rows) map.set(rowLabelItem(row).key, rowLabelItem(row));
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
    if (!timeline || !layout) return [];
    const placed = timeline.events
      .map(event => ({ event, x: layout.position(event.at) }))
      .filter((entry): entry is { event: TimelineEvent; x: number } => entry.x !== null)
      .sort((left, right) => left.x - right.x);
    const groups: { event: TimelineEvent; x: number }[][] = [];
    for (const entry of placed) {
      const group = groups[groups.length - 1];
      if (group && entry.x - group[group.length - 1]!.x < MARKER_MERGE_PERCENT) group.push(entry);
      else groups.push([entry]);
    }
    return groups.map(group => {
      const items = group.map(entry => eventItem(entry.event, rowsById));
      return {
        key: `events:${group[0]!.event.seq}`,
        x: group[0]!.x,
        events: group.map(entry => entry.event),
        items,
        lines: items.map(item => `${item.head}，${item.parts.join("，")}`),
        head: group.length > 1 ? `Host 事件（${group.length} 条）` : items[0]!.head,
      };
    });
  }, [timeline, layout, rowsById]);

  const canFold = layout?.canFold ?? false;
  const eligibleGaps = useMemo(() =>
    (layout?.gaps ?? []).filter(gap => gap.endMs - gap.startMs > TIMELINE_FOLD_THRESHOLD_MS),
    [layout]);

  const chronology = useMemo(() => {
    if (!timeline || !layout) return [];
    return buildChronology(timeline, factsBySpan, layout.gaps, canFold);
  }, [timeline, layout, factsBySpan, canFold]);

  // Roving tabindex: one stop inside the canvas, defaulting to the first
  // delegation row's first execution span. Cluster keys are valid stops too.
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
    candidate = candidate ?? (clusters[0] ? clusters[0].items[0] ?? null : null);
    candidate = candidate ?? (rows[0] ? rowLabelItem(rows[0]) : null);
    setFocusKey(candidate?.key ?? null);
  }, [timeline, itemsByKey, factsBySpan, spansByRun, clusters, clusterKeys, focusKey]);

  // Stay mounted while hidden; restore scroll and focus on return.
  useEffect(() => {
    const element = scrollRef.current;
    if (hidden) {
      wasHidden.current = true;
      if (element) savedScroll.current = element.scrollTop;
      return;
    }
    if (wasHidden.current) {
      wasHidden.current = false;
      if (element && savedScroll.current !== null) element.scrollTop = savedScroll.current;
      savedScroll.current = null;
      if (focusKey) {
        const target = element?.querySelector<HTMLElement>(`[data-key="${focusKey}"]`);
        target?.focus();
      }
    }
  }, [hidden, focusKey]);

  function itemAria(item: TimelineItem): string {
    return `${item.head}，${item.parts.join("，")}`;
  }

  function onCanvasKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    const item = target.closest<HTMLElement>(".tl-item");
    const grid = gridRef.current;
    if (!item || !grid || !grid.contains(item)) return;
    const rows = [...grid.querySelectorAll<HTMLElement>(".tl-row[data-nav]")]
      .map(row => [...row.querySelectorAll<HTMLElement>(".tl-item")].sort((left, right) => Number(left.dataset.x) - Number(right.dataset.x)));
    const rowIndex = rows.findIndex(row => row.includes(item));
    if (rowIndex < 0) return;
    const columnIndex = rows[rowIndex]!.indexOf(item);
    const anchor = Number(item.dataset.x);
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
      event.preventDefault();
      item.click();
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

  const trackWidthPx = Math.max(overlayWidth, FALLBACK_TRACK_PX);
  const visibleMinutes = layout && layout.endMs !== null && layout.startMs !== null
    ? ((layout.endMs - layout.startMs) - (layout?.gaps ?? []).reduce((total, gap) => total + (gap.collapsed ? gap.endMs - gap.startMs : 0), 0)) / 60000
    : 0;
  const trackMinPx = Math.ceil(visibleMinutes * 1.6);

  const ticks = useMemo(() => {
    if (!layout || layout.startMs === null || layout.endMs === null || visibleMinutes <= 0) return [] as { at: number; label: string; left: number }[];
    const pxPerMinute = trackWidthPx / Math.max(visibleMinutes, 1);
    const steps = [15, 30, 60, 120, 240, 480, 1440];
    const stepMinutes = steps.find(step => step * pxPerMinute >= 64) ?? 1440;
    const stepMs = stepMinutes * 60000;
    const collapsedSpans = (layout.gaps ?? []).filter(gap => gap.collapsed);
    const result: { at: number; label: string; left: number }[] = [];
    for (let at = Math.ceil(layout.startMs / stepMs) * stepMs; at <= layout.endMs; at += stepMs) {
      const left = layout.position(at);
      if (left === null) continue;
      if (collapsedSpans.some(gap => at > gap.startMs && at < gap.endMs)) continue;
      const date = new Date(at);
      const clock = clockTime(at);
      const label = date.getHours() === 0 && date.getMinutes() === 0
        ? `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")} 00:00`
        : clock;
      result.push({ at, label, left });
    }
    return result;
  }, [layout, trackWidthPx, visibleMinutes]);

  const nowVisible = !!(layout && observedAtMs !== null && layout.endMs === observedAtMs);
  const nowLeft = nowVisible && layout ? layout.position(observedAtMs) : null;
  const activeKey = hoverKey ?? focusKey;
  const activeItem = activeKey ? itemsByKey.get(activeKey) ?? null : null;
  const activeCluster = activeKey ? clusters.find(cluster => cluster.key === activeKey) ?? null : null;
  const guideLeft = activeCluster !== null || (activeItem?.guide === true)
    ? (activeCluster ? activeCluster.x : layout && activeItem?.atMs !== null && activeItem ? layout.position(activeItem.atMs) : null)
    : null;
  const summary = timeline?.objective ?? props.summary;

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
      onClick: () => {
        const item = itemsByKey.get(key);
        if (item) props.onOpenItem(item);
      },
    };
  }

  function renderSpan(row: TimelineRow, span: TimelineSpan) {
    const facts = factsBySpan.get(span.spanId);
    if (!facts || facts.item.atMs === null) return null;
    const reversed = facts.endMs !== null && facts.endMs < facts.startMs!;
    if (reversed) return null;
    const left = layout!.position(facts.startMs)!;
    const style = paletteIndex(palette, span.configuration);
    const uncertainTail = facts.outcome === "unknown" && span.endAt != null && observedAtMs !== null
      ? uncertainTailEnd(toMs(span.endAt)!, observedAtMs, layout?.gaps ?? []) : null;
    const visualEnd = uncertainTail ?? facts.endMs;
    const right = visualEnd !== null ? layout!.position(visualEnd) : null;
    const width = right !== null ? Math.max(right - left, 0.4) : 0.4;
    const widthPx = (width / 100) * trackWidthPx;
    const selected = openedKey === facts.item.key;
    const classes = ["tl-item", "sp", span.kind === "queue" ? "queue" : span.kind === "routing" ? "routing" : span.kind === "host" ? "wait" : "exec"];
    if (facts.outcome === "running") classes.push("running");
    if (facts.outcome === "failed") classes.push("failed");
    if (facts.outcome === "cancelled") classes.push("cancelled");
    if (facts.outcome === "unknown") classes.push("unknown");
    if (span.kind === "host" && span.endAt == null) classes.push("open");
    if (selected) classes.push("selected");
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
        {widthPx >= 34 ? <span className="sp-text">路由</span> : null}
      </button>;
    }
    if (span.kind === "host") {
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
        <span className="sp-text">{widthPx >= 110 ? `等待 Host · ${durationShort((facts.endMs ?? observedAtMs ?? 0) - facts.startMs!)}` : widthPx >= 64 ? "等待 Host" : widthPx >= 34 ? "等待" : ""}</span>
      </button>;
    }
    const shortModel = span.configuration?.model || span.configuration?.provider || "";
    const label = widthPx >= 150
      ? `第${span.turnIndex ?? "?"}轮 · ${shortModel}${facts.outcome !== "finished" ? " · " + outcomeLabel(span, facts.outcome) : ""}`
      : widthPx >= 72 ? `第${span.turnIndex ?? "?"}轮` : widthPx >= 30 ? String(span.turnIndex ?? "·") : "";
    if (facts.outcome === "unknown" && uncertainTail !== null) {
      const solidWidth = Math.max(layout!.position(toMs(span.endAt)!)! - left, 0.4);
      const solidShare = Math.min(100, (solidWidth / Math.max(width, 0.4)) * 100);
      return <button key={facts.item.key} type="button" className={classes.join(" ")} style={positionStyle}
        data-x={left} tabIndex={tabIndex} aria-label={aria} title={aria} {...handlers}>
        <span className="solid" style={{ width: `${solidShare}%` }}>{solidWidth / 100 * trackWidthPx >= 72 ? `第${span.turnIndex ?? "?"}轮 · ${shortModel}` : ""}</span>
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
      : timeline && layout
        ? <div className={"timeline-body" + (asList ? " as-list" : "")}>
          <div ref={scrollRef} className="tl-scroll" onKeyDown={onCanvasKeyDown}>
            <div ref={gridRef} className="tl-grid" role="group" aria-label="工作目标时间轴"
              style={{ "--track-min": `${trackMinPx}px` } as CSSProperties}>
              <div className="tl-row axis">
                <div className="tl-label">委派 / 时间</div>
                <div className="tl-track">
                  {ticks.map(tick => <span key={tick.at} className="tick" style={{ left: `${tick.left}%` }}>{tick.label}</span>)}
                  {canFold && eligibleGaps.map(gap => gap.collapsed
                    ? <button key={gap.id} type="button" className="fold-button" style={{ left: `${(gap.fromPercent + gap.toPercent) / 2}%` }}
                      aria-label={`空闲 ${durationShort(gap.endMs - gap.startMs)}，${clockTime(gap.startMs)} 至 ${clockTime(gap.endMs)}，已折叠，展开`}
                      title={`${clockTime(gap.startMs)}–${clockTime(gap.endMs)} 没有任何片段或事件`}
                      onClick={() => props.onToggleGap(gap.id)}>
                      <span>空闲</span><span>{durationShort(gap.endMs - gap.startMs)}</span><span>展开</span>
                    </button>
                    : <button key={gap.id} type="button" className="collapse-button" style={{ left: `${gap.fromPercent + 0.4}%` }}
                      aria-label={`收起空闲 ${durationShort(gap.endMs - gap.startMs)}`}
                      onClick={() => props.onToggleGap(gap.id)}>收起空闲 {durationShort(gap.endMs - gap.startMs)}</button>)}
                  {nowVisible && nowLeft !== null && <span className="now-chip" style={{ left: `${nowLeft}%` }}>现在 {clockTime(observedAtMs!)}</span>}
                </div>
              </div>
              <div className="tl-row markers" data-nav="">
                <div className="tl-label"><span>Host 事件</span>
                  {missingEvents > 0 && <span className="trunc-chip">‹ {missingEvents} 条事件未返回</span>}</div>
                <div className="tl-track">
                  {clusters.map(cluster => {
                    const single = cluster.items.length === 1 ? cluster.items[0]! : null;
                    const vocab = single ? eventVocab(single ? cluster.events[0]!.kind : "") : null;
                    const classes = ["tl-item", "mk", cluster.items.length > 1 ? "cluster" : cluster.events[0]!.kind];
                    return <button key={cluster.key} type="button" className={classes.join(" ")} style={{ left: `${cluster.x}%` }}
                      data-key={cluster.key} data-x={cluster.x} tabIndex={focusKey === cluster.key ? 0 : -1}
                      aria-label={cluster.lines.join("；")} title={cluster.lines.join("；")}
                      onFocus={() => setFocusKey(cluster.key)}
                      onMouseEnter={() => setHoverKey(cluster.key)}
                      onMouseLeave={() => setHoverKey(current => (current === cluster.key ? null : current))}
                      onClick={() => { if (single) props.onOpenItem(single); }}>
                      {cluster.items.length > 1 ? cluster.items.length : vocab?.glyph}
                    </button>;
                  })}
                </div>
              </div>
              {timeline.rows.map(row => {
                const state = rowStateInfo(row);
                const label = rowLabelItem(row);
                const unplaced = (spansByRun.get(row.runId) ?? []).filter(span => {
                  const facts = factsBySpan.get(span.spanId);
                  return !facts || facts.item.atMs === null || (facts.endMs !== null && facts.endMs < facts.startMs!);
                }).length;
                const settle = settleItem(row);
                const settleLeft = settle && settle.atMs !== null ? layout.position(settle.atMs) : null;
                return <div key={row.runId} className="tl-row" data-nav="">
                  <button type="button"
                    className={"tl-item tl-label" + (row.kind === "helper" ? " helper" : "") + (openedRunId === row.runId ? " selected-run" : "")}
                    style={{ "--depth": row.depth } as CSSProperties}
                    data-x={-1} tabIndex={focusKey === label.key ? 0 : -1}
                    title={row.title} aria-label={itemAria(label)}
                    {...itemHandlers(label.key)}>
                    <span className="lbl-title">
                      {row.kind === "helper" && <span aria-hidden="true" className="muted">↳</span>}
                      <span className={"st " + state.glyphClass} aria-hidden="true">{state.glyph}</span>
                      {props.newRunIds.has(row.runId) && <span className="new-mark">新</span>}
                      <span>{row.title}</span>
                    </span>
                    <span className="lbl-sub">{state.label} · {row.kind === "helper" ? "协助任务 · " : ""}{configurationLabel(row.configuration)}</span>
                    {unplaced > 0 && <span className="trunc-chip" title="这些片段的时间缺失或颠倒，未在时间轴上放置">⚠ {unplaced} 段时间缺失</span>}
                  </button>
                  <div className="tl-track">
                    {(spansByRun.get(row.runId) ?? []).map(span => renderSpan(row, span))}
                    {settle && settleLeft !== null && <button type="button"
                      className={"tl-item flag " + (row.acceptanceVerdict === "rejected" ? "reject" : "accept")}
                      style={{ left: `${settleLeft}%` }} data-x={settleLeft}
                      tabIndex={focusKey === settle.key ? 0 : -1} aria-label={itemAria(settle)} title={itemAria(settle)}
                      {...itemHandlers(settle.key)}>{row.acceptanceVerdict === "rejected" ? "!" : "✓"}</button>}
                  </div>
                </div>;
              })}
              <div ref={overlayRef} className="tl-overlay" aria-hidden="true">
                {canFold && eligibleGaps.map(gap => gap.collapsed
                  ? <div key={gap.id} className="fold-band" style={{ left: `${gap.fromPercent}%`, width: `${gap.toPercent - gap.fromPercent}%` }} />
                  : <div key={gap.id} className="expanded-band" style={{ left: `${gap.fromPercent}%`, width: `${gap.toPercent - gap.fromPercent}%` }} />)}
                {nowVisible && nowLeft !== null && <div className="now-line" style={{ left: `${nowLeft}%` }} />}
                {guideLeft !== null && <div className="guide-line" style={{ left: `${guideLeft}%` }} />}
              </div>
            </div>
          </div>
          <ObjectiveChronology entries={chronology} palette={palette} selectedKey={openedKey}
            onOpenItem={item => { setFocusKey(item.key); props.onOpenItem(item); }} />
          <div className="inspector" aria-live="polite">
            {activeCluster
              ? <span className="cluster-lines">
                <strong>{activeCluster.head}</strong>
                {activeCluster.items.map((item, index) => <span key={item.key} className="cluster-line">
                {activeCluster.lines[index]}
                  <button type="button" className="button small-button" onClick={() => props.onOpenItem(item)}>打开</button>
                </span>)}
              </span>
              : activeItem
                ? <><strong>{activeItem.head}</strong><span>{activeItem.parts.join(" · ")}</span></>
                : <span className="muted">悬停或聚焦片段查看记录。</span>}
            <span className="hint">Enter 打开委派详情 · 方向键移动</span>
          </div>
        </div>
        : null;

  return <div className="timeline-view" hidden={hidden}>
    {summary && <header className="detail-header tl-head">
      <div className="row-between">
        <button type="button" className="button small-button narrow-back" onClick={props.onBackToList}>返回工作目标</button>
        <span className="small muted truncate" title={summary.project.path || summary.project.id}>{summary.project.label}</span>
        <span className="chip-row">
          {summary.kind === "standalone" && <Badge tone="neutral">独立委派</Badge>}
          <Badge tone={categoryTone(summary.state)}>{CATEGORY_LABEL[summary.state]}</Badge>
        </span>
      </div>
      <h2 title={summary.title}>{excerpt(summary.title, 100)}</h2>
      <p className="assignment-line">
        <span>来源 Host：{summary.sourceHostId || "未记录"}</span>
        <span>共 {totalDelegations(summary.counts)} 个委派{summary.counts.helpers ? `（含 ${summary.counts.helpers} 个协助任务）` : ""}</span>
        <span>最近活动 {clockTime(summary.lastActivityAt)}</span>
      </p>
      <span className="count-line">
        {COUNT_ORDER.filter(entry => summary.counts[entry.key] > 0).map(entry =>
          <span key={entry.key} className={`cnt cnt-${entry.key}`}><span aria-hidden="true">{entry.glyph}</span>{entry.label} {summary.counts[entry.key]}</span>)}
        <span className="muted">共 {totalDelegations(summary.counts)} 个委派</span>
      </span>
      <p className="tl-note">{summary.kind === "standalone"
        ? "这条旧记录没有工作目标，按字段为空的规则单独显示，不与其他记录合并。"
        : "工作目标只用于归档与浏览，不调度任务，也不作为验收条件。"}</p>
    </header>}
    <div className="tl-toolbar">
      <div className="legend" aria-label="执行配置图例">
        <span className="legend-title">执行配置</span>
        {palette.length ? palette.map(entry => <span key={entry.key} className="legend-item">
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
        <span className={"refresh-state" + (stale ? " stale" : "")}>
          {stale && timeline
            ? `显示的是 ${clockSeconds(timeline.observedAt)} 的数据 · 最近一次读取失败`
            : `每 3 秒只读刷新 · 截至 ${clockSeconds(timeline?.observedAt ?? props.summary?.lastActivityAt)}`}
        </span>
        {stale && <button type="button" className="button small-button" onClick={props.onRetry}>重试读取</button>}
      </div>
    </div>
    {timeline && truncation.length > 0 && <div className="banner trunc-banner" role="status">
      时间轴读取有边界：{truncation.join("；")}。可调整筛选或打开单个委派查看其完整记录。
    </div>}
    {body}
  </div>;
}
