import { Hint } from "../components/hint";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { clockTime } from "../macro-task-display";
import type { TimelineSpan } from "../macro-task-types";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { ClusterMark } from "./ClusterMark";
import type { EventAction } from "./events";

export type TimelineMarker = EventAction & { kind: "event"; x: number } | {
  kind: "inquiry"; inquiry: NonNullable<TimelineSpan["inquiries"]>[number]; span: TimelineSpan; x: number;
};

export const markerKey = (marker: TimelineMarker) => marker.kind === "event" ? `event:${marker.event.seq}` : `inquiry:${marker.inquiry.inquiryId}`;
export const markerAt = (marker: TimelineMarker) => marker.kind === "event" ? marker.event.at : marker.inquiry.createdAt;
export const markerHasKey = (marker: TimelineMarker, key: string | null | undefined) => markerKey(marker) === key
  || marker.kind === "event" && !!marker.relatedEvents?.some(event => `event:${event.seq}` === key);

export function MarkerCluster({ markers, x, y, hitPath, runId, taskTitle, tabKey, boundary, onOpen, onSelect, onFocusMarker, markerName, markerReadout, selectedKey, highlightedKey }: {
  markers: TimelineMarker[]; x: number; y: number; hitPath: string; runId: string; taskTitle: string; tabKey?: string | null; boundary?: HTMLElement | null;
  onOpen: (runId: string, key: string) => void; onFocusMarker: (marker: TimelineMarker | null, source: "hover" | "focus") => void;
  onSelect: (runId: string, key: string) => void;
  markerName: (marker: TimelineMarker) => string; markerReadout: (marker: TimelineMarker) => string;
  /** The cluster is selected when it holds the selected event or inquiry. */
  selectedKey?: string | null; highlightedKey?: string | null;
}) {
  const { t } = useTranslation("tasks");
  const [open, setOpen] = useState(false);
  const trigger = useRef<SVGGElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  const first = markers[0]!;
  const key = `cluster:${markerKey(first)}`;
  const label = t("drawing.eventCluster", { count: markers.length });
  const selected = markers.some(marker => markerHasKey(marker, selectedKey));
  const openMarker = (marker: TimelineMarker) => {
    setOpen(false);
    onSelect(runId, markerKey(marker));
    onOpen(runId, markerKey(marker));
  };
  return <Popover open={open} onOpenChange={setOpen}>
    <Hint text={label}><PopoverTrigger asChild><g ref={trigger} role="button" tabIndex={tabKey === key ? 0 : -1} data-slot="event-cluster" data-at={Date.parse(markerAt(first))} data-key={key} aria-pressed={selected}
      aria-label={`${label} · ${taskTitle}`} onPointerEnter={() => onFocusMarker(first, "hover")} onPointerLeave={() => onFocusMarker(null, "hover")} onFocus={() => onFocusMarker(first, "focus")} onBlur={() => onFocusMarker(null, "focus")}
      onClick={() => onSelect(runId, markerKey(first))}
      onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); onSelect(runId, markerKey(first)); setOpen(true); } }}
      className="group/fragment outline-none">
      <path data-slot="marker-hit" d={hitPath} fill="transparent"/>
      <ClusterMark count={markers.length} x={x} y={y} selected={selected || markers.some(marker => markerHasKey(marker, highlightedKey))} focusFrame/>
    </g></PopoverTrigger></Hint>
    <PopoverContent collisionBoundary={boundary ?? undefined} avoidCollisions sideOffset={8} aria-label={label} className="w-72 max-w-(--radix-popover-content-available-width) max-h-(--radix-popover-content-available-height) overflow-y-auto rounded-none p-1"
      onOpenAutoFocus={event => { event.preventDefault(); items.current[0]?.focus(); }}
      onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }}>
      <div className="border-b px-2 py-1 text-xs text-ink3">{label}</div>
      {markers.map((marker, index) => <button key={markerKey(marker)} ref={element => { items.current[index] = element; }} type="button"
        className="block w-full px-2 py-2 text-left text-xs text-ink hover:bg-sunk focus-visible:bg-sunk focus-visible:outline-none"
        aria-label={markerReadout(marker)}
        onPointerEnter={() => onFocusMarker(marker, "hover")} onPointerLeave={() => onFocusMarker(null, "hover")} onFocus={() => onFocusMarker(marker, "focus")} onBlur={() => onFocusMarker(null, "focus")}
        onKeyDown={keyEvent => {
          if (keyEvent.key === "ArrowDown" || keyEvent.key === "ArrowUp") {
            keyEvent.preventDefault(); keyEvent.stopPropagation();
            items.current[(index + (keyEvent.key === "ArrowDown" ? 1 : markers.length - 1)) % markers.length]?.focus();
          }
        }}
        onClick={() => openMarker(marker)}>
        <span className="font-medium">{markerName(marker)}</span><span className="mx-1 text-ink3">·</span><span className="text-ink2">{clockTime(markerAt(marker))}</span>
        <span className="block truncate text-ink3">{taskTitle}</span>
        {marker.kind === "event" && marker.relatedEvents?.length && <span data-slot="paired-event-details" className="block text-ink2">{markerReadout(marker)}</span>}
      </button>)}
    </PopoverContent>
  </Popover>;
}
