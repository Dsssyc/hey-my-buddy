import { useEffect, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import type { TimelineEvent, TimelineRow } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { clockTime, eventVocab } from "./objective-display";

export type MarkerClusterView = {
  key: string;
  /** Horizontal percent of the anchor marker inside its track. */
  x: number;
  events: TimelineEvent[];
  items: TimelineItem[];
};

/**
 * Non-modal popover under a merged Host-marker cluster (0.15 C3). Opening it
 * selects nothing; each row is selected individually and the popover stays
 * open for browsing. Enter or double-click (or the row's 打开 button) opens
 * that event's existing detail; Esc closes and returns focus to the marker.
 */
export function MarkerPopover({ cluster, rowsById, selectedEventKey, onSelect, onOpen, requestClose }: {
  cluster: MarkerClusterView;
  rowsById: ReadonlyMap<string, TimelineRow>;
  selectedEventKey: string | null;
  onSelect: (item: TimelineItem) => void;
  onOpen: (item: TimelineItem) => void;
  /** Ask the owner to close; `focusMarker` restores focus to the anchor (Esc). */
  requestClose: (focusMarker: boolean) => void;
}) {
  const root = useRef<HTMLDivElement>(null);

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
    if (event.key === "ArrowDown") next = rows[index + 1] ?? null;
    else if (event.key === "ArrowUp") next = rows[index - 1] ?? null;
    else if (event.key === "Home") next = rows[0] ?? null;
    else if (event.key === "End") next = rows[rows.length - 1] ?? null;
    if (next) {
      event.preventDefault();
      next.focus();
    }
  }

  return <div ref={root} className="marker-popover" role="group" aria-label={`Host 事件 ${cluster.events.length} 条`}
    style={{ left: `clamp(9px, ${cluster.x}%, calc(100% - 9px))` }}>
    <div className="marker-popover-rows">
      {cluster.items.map((item, index) => {
        const event = cluster.events[index]!;
        const vocab = eventVocab(event.kind);
        const row = rowsById.get(event.runId);
        const selected = selectedEventKey === item.key;
        return <div key={item.key} data-row-index={index}
          className={"marker-popover-row" + (selected ? " selected" : "")}>
          <button type="button" className="marker-popover-main"
            aria-label={`${item.head}，${row?.title ?? `委派 ${event.runId}`}，${clockTime(event.at)}${event.actor ? `，actor ${event.actor}` : ""}`}
            onClick={() => onSelect(item)}
            onDoubleClick={() => onOpen(item)}
            onKeyDown={keyboard => onKeyDown(keyboard, item)}>
            <span className="glyph" aria-hidden="true">{vocab.glyph}</span>
            <time>{clockTime(event.at)}</time>
            <span className="marker-popover-title">
              {row?.kind === "helper" ? <span aria-hidden="true" className="muted">↳ </span> : null}
              Host {event.label || vocab.label} · {row?.title ?? `委派 ${event.runId}`}
            </span>
            {event.actor && <span className="muted">{event.actor}</span>}
          </button>
          <button type="button" className="button small-button" aria-label={`打开 ${item.head}`}
            onClick={() => onOpen(item)}>打开</button>
        </div>;
      })}
    </div>
    <p className="marker-popover-hint">单击选中 · Enter 或双击打开详情 · Esc 关闭</p>
  </div>;
}
