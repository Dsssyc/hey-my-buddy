import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import type { TimelineEvent, TimelineRow } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { clockTime, eventSentence, eventVocab } from "./objective-display";

export type MarkerClusterView = {
  key: string;
  /** Horizontal percent of the anchor marker inside its track. */
  x: number;
  events: TimelineEvent[];
  items: TimelineItem[];
};

/** Keep the popover's left edge inside the track: subtract its own width at the right rail. */
const POPOVER_WIDTH_PX = 320;

/**
 * Non-modal popover under a merged Host-marker cluster (0.15 C3). Opening it
 * selects nothing; each row is selected individually and the popover stays
 * open for browsing. Enter or double-click (or the row's 打开 button) opens
 * that event's existing detail; Esc closes and returns focus to the marker.
 * Rows are the focusable elements, so arrow/Home/End move between buttons.
 */
export function MarkerPopover({ cluster, anchor, rowsById, selectedEventKey, onSelect, onOpen, requestClose }: {
  cluster: MarkerClusterView;
  anchor: HTMLElement | null;
  rowsById: ReadonlyMap<string, TimelineRow>;
  selectedEventKey: string | null;
  onSelect: (item: TimelineItem) => void;
  onOpen: (item: TimelineItem) => void;
  /** Ask the owner to close; `focusMarker` restores focus to the anchor (Esc). */
  requestClose: (focusMarker: boolean) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 8, top: 8, width: POPOVER_WIDTH_PX });
  useLayoutEffect(() => {
    if (!anchor || !root.current) return;
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(POPOVER_WIDTH_PX, window.innerWidth - 16);
    const height = root.current.getBoundingClientRect().height;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    const top = Math.max(8, rect.bottom + height + 4 <= window.innerHeight - 8
      ? rect.bottom + 4 : rect.top - height - 4);
    setPosition(previous => previous.left === left && previous.top === top && previous.width === width
      ? previous : { left, top, width });
  }, [anchor, cluster]);
  useEffect(() => {
    const close = () => requestClose(false);
    window.addEventListener("resize", close);
    return () => window.removeEventListener("resize", close);
  }, [requestClose]);

  // Focus enters the popover itself so keyboard navigation starts without
  // pre-selecting (let alone opening) any event.
  useEffect(() => {
    root.current?.querySelector<HTMLElement>("[data-row-index]")?.focus({ preventScroll: true });
  }, [cluster.key]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (root.current?.contains(target)) return;
      // The anchor marker toggles the popover itself; other clicks close it.
      if (target?.closest?.(`[data-cluster-key="${cluster.key}"]`)) return;
      requestClose(false);
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [cluster.key, requestClose]);

  function onKeyDown(event: ReactKeyboardEvent<HTMLElement>, item: TimelineItem) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      requestClose(true);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      onOpen(item);
      return;
    }
    const rows = [...(root.current?.querySelectorAll<HTMLElement>("[data-row-index]") ?? [])];
    const index = rows.findIndex(row => row === event.currentTarget);
    if (index < 0) return;
    let next: HTMLElement | null = null;
    if (event.key === "ArrowDown") next = rows[index + 1] ?? rows[index]!;
    else if (event.key === "ArrowUp") next = rows[index - 1] ?? rows[index]!;
    else if (event.key === "Home") next = rows[0] ?? null;
    else if (event.key === "End") next = rows[rows.length - 1] ?? null;
    if (next) {
      event.preventDefault();
      event.stopPropagation();
      next.focus();
    }
  }

  // Escape the timeline's scroll clipping; focusing the popup must not scroll
  // the canvas and immediately trigger its close-on-scroll handler.
  return createPortal(<div ref={root} id={`popover-${cluster.key}`} className="marker-popover" role="group"
    aria-label={`Host 事件 ${cluster.events.length} 条`}
    onKeyDown={event => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        requestClose(true);
      }
    }}
    style={position}>
    <div className="marker-popover-rows">
      {cluster.items.map((item, index) => {
        const event = cluster.events[index]!;
        const vocab = eventVocab(event.kind);
        const row = rowsById.get(event.runId);
        const selected = selectedEventKey === item.key;
        // P1.7: one natural-language sentence per event, the recorded summary
        // as a second weak line.
        const sentence = eventSentence(event, row ?? null);
        return <div key={item.key}
          className={"marker-popover-row" + (selected ? " selected" : "")}>
          <button type="button" className="marker-popover-main" data-row-index={index}
            title={event.actor ? `${sentence} · ${event.actor}` : sentence}
            aria-label={`${sentence}${event.actor ? `，actor ${event.actor}` : ""}`}
            onClick={() => onSelect(item)}
            onDoubleClick={() => onOpen(item)}
            onKeyDown={keyboard => onKeyDown(keyboard, item)}>
            <span className="glyph" aria-hidden="true">{vocab.glyph}</span>
            <time>{clockTime(event.at)}</time>
            <span className="marker-popover-title">{sentence}</span>
            {event.actor && <span className="muted">{event.actor}</span>}
            {event.summary && <span className="marker-popover-summary muted">{event.summary}</span>}
          </button>
          <button type="button" className="button small-button" aria-label={`打开 ${item.head}`}
            onClick={() => onOpen(item)}>打开</button>
        </div>;
      })}
    </div>
    <p className="marker-popover-hint">单击选中 · Enter 或双击打开详情 · Esc 关闭</p>
  </div>, document.body);
}
