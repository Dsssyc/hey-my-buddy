import { useEffect, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import type { TimelineEvent, TimelineRow } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { clockTime, eventSentence, eventVocab } from "./objective-display";
import { Popover } from "./Popover";

export type MarkerClusterView = {
  key: string;
  /** Horizontal percent of the anchor marker inside its track. */
  x: number;
  events: TimelineEvent[];
  items: TimelineItem[];
};

/**
 * Non-modal popover under a merged Host-marker cluster (0.15 C3), reusing the
 * shared popover lifecycle (0.16 0.4): one popover at a time — opening the
 * hierarchy help or the list filter closes this popup — placement against the
 * marker clamped into the viewport, and focus leaving the popup closing it.
 * Opening it selects nothing; each row is selected individually and the
 * popover stays open for browsing. Enter or double-click (or the row's 打开
 * button) opens that event's existing detail; Esc closes and returns focus to
 * the marker. Rows stay the focusable elements, so arrow/Home/End move between
 * buttons, and every row keeps its honest natural-language sentence.
 */
export function MarkerPopover({ cluster, anchor, rowsById, selectedEventKey, onSelect, onOpen, requestClose }: {
  cluster: MarkerClusterView;
  anchor: HTMLElement | null;
  rowsById: ReadonlyMap<string, TimelineRow>;
  selectedEventKey: string | null;
  onSelect: (item: TimelineItem) => void;
  onOpen: (item: TimelineItem) => void;
  /** Ask the owner to close; Esc returns focus to the anchor through the shared popover. */
  requestClose: (focusMarker: boolean) => void;
}) {
  const root = useRef<HTMLDivElement>(null);

  // Focus enters the popover itself so keyboard navigation starts without
  // pre-selecting (let alone opening) any event.
  useEffect(() => {
    root.current?.querySelector<HTMLElement>("[data-row-index]")?.focus({ preventScroll: true });
  }, [cluster.key]);

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

  return <Popover id={`popover-${cluster.key}`} anchor={anchor}
    label={`Host 事件 ${cluster.events.length} 条`}
    onClose={() => requestClose(false)}
    className="marker-popover" width="min(320px, calc(100vw - 16px))">
    <div ref={root} className="marker-popover-rows">
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
  </Popover>;
}
