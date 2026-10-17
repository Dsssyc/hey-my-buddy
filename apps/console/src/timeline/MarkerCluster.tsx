import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { clockTime } from "../macro-task-display";
import type { TimelineEvent, TimelineSpan } from "../macro-task-types";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";

export type TimelineMarker = { kind: "event"; event: TimelineEvent; x: number } | {
  kind: "inquiry"; inquiry: NonNullable<TimelineSpan["inquiries"]>[number]; span: TimelineSpan; x: number;
};

export const markerKey = (marker: TimelineMarker) => marker.kind === "event" ? `event:${marker.event.seq}` : `inquiry:${marker.inquiry.inquiryId}`;
export const markerAt = (marker: TimelineMarker) => marker.kind === "event" ? marker.event.at : marker.inquiry.createdAt;

export function MarkerCluster({ markers, x, y, runId, taskTitle, selected, onSelect, tabKey, boundary, onOpen, onFocusMarker, markerName, markerReadout }: {
  markers: TimelineMarker[]; x: number; y: number; runId: string; taskTitle: string; selected: boolean; onSelect: (runId: string, key: string) => void; tabKey?: string; boundary?: HTMLElement | null;
  onOpen: (runId: string, key: string) => void; onFocusMarker: (marker: TimelineMarker | null) => void;
  markerName: (marker: TimelineMarker) => string; markerReadout: (marker: TimelineMarker) => string;
}) {
  const { t } = useTranslation("tasks");
  const [open, setOpen] = useState(false);
  const trigger = useRef<SVGGElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  const first = markers[0]!;
  const key = `cluster:${markerKey(first)}`;
  const label = t("drawing.eventCluster", { count: markers.length });
  const openMarker = (marker: TimelineMarker) => {
    setOpen(false);
    onOpen(runId, markerKey(marker));
  };
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><g ref={trigger} role="button" tabIndex={tabKey === key ? 0 : -1} data-slot="event-cluster" data-at={Date.parse(markerAt(first))} data-key={key}
      aria-label={`${label} · ${taskTitle}`} onClick={() => onSelect(runId, markerKey(first))} onPointerEnter={() => onFocusMarker(first)} onPointerLeave={() => onFocusMarker(null)} onFocus={() => onFocusMarker(first)} onBlur={() => onFocusMarker(null)}
      onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); setOpen(true); } }}
      className="group/fragment cursor-pointer outline-none">
      {selected && <rect data-slot="selected-item" x={x - 12} y={y - 12} width={24} height={24} fill="none" className="pointer-events-none stroke-ink3"/>}
      <circle cx={x} cy={y} r={9} className="fill-card stroke-past" strokeWidth={1.5}/>
      <text x={x} y={y + 3.5} textAnchor="middle" aria-hidden="true" className="pointer-events-none fill-past text-xs font-semibold">{markers.length}</text>
      <rect aria-hidden="true" data-slot="fragment-focus" x={x - 11} y={y - 11} width={22} height={22} fill="none" strokeWidth={2} className={`pointer-events-none stroke-ink opacity-0 ${selected ? "" : "group-focus/fragment:opacity-100"}`}/>
    </g></PopoverTrigger>
    <PopoverContent collisionBoundary={boundary ?? undefined} avoidCollisions sideOffset={8} aria-label={label} className="w-72 max-w-(--radix-popover-content-available-width) max-h-(--radix-popover-content-available-height) overflow-y-auto rounded-none p-1"
      onOpenAutoFocus={event => { event.preventDefault(); items.current[0]?.focus(); }}
      onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }}>
      <div className="border-b px-2 py-1 text-xs text-ink3">{label}</div>
      {markers.map((marker, index) => <button key={markerKey(marker)} ref={element => { items.current[index] = element; }} type="button"
        className="block w-full cursor-pointer px-2 py-2 text-left text-xs text-ink hover:bg-sunk focus-visible:bg-sunk focus-visible:outline-none"
        aria-label={markerReadout(marker)}
        onPointerEnter={() => onFocusMarker(marker)} onPointerLeave={() => onFocusMarker(null)} onFocus={() => onFocusMarker(marker)} onBlur={() => onFocusMarker(null)}
        onKeyDown={keyEvent => {
          if (keyEvent.key === "ArrowDown" || keyEvent.key === "ArrowUp") {
            keyEvent.preventDefault(); keyEvent.stopPropagation();
            items.current[(index + (keyEvent.key === "ArrowDown" ? 1 : markers.length - 1)) % markers.length]?.focus();
          }
        }}
        onClick={() => openMarker(marker)}>
        <span className="font-medium">{markerName(marker)}</span><span className="mx-1 text-ink3">·</span><span className="text-ink2">{clockTime(markerAt(marker))}</span>
        <span className="block truncate text-ink3">{taskTitle}</span>
      </button>)}
    </PopoverContent>
  </Popover>;
}
