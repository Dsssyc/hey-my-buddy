import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNumberViewPreference } from "../view-preferences";
import { Hint } from "../components/hint";

const KEY = "timeline-name-width";
export const DEFAULT_NAME_WIDTH = 272;
const MIN = 200;
const MAX = 440;
const bounded = (width: number) => Math.max(MIN, Math.min(MAX, width));

export function useNameWidth() {
  const [storedWidth, setWidth] = useNumberViewPreference(KEY, DEFAULT_NAME_WIDTH);
  const width = bounded(storedWidth);
  const change = (next: number) => setWidth(bounded(next));
  return [width, change] as const;
}

/** One separator works with pointer, keyboard and reset, in either names view. */
export function NameDivider({ width, onChange }: { width: number; onChange: (width: number) => void }) {
  const { t } = useTranslation("tasks");
  const [drag, setDrag] = useState<{ x: number; width: number } | null>(null);
  return <Hint text={t("resizeNames")}><div data-slot="timeline-name-divider" role="separator" tabIndex={0}
    aria-label={t("resizeNames")} aria-orientation="vertical" aria-valuemin={MIN} aria-valuemax={MAX} aria-valuenow={width}
    className="absolute inset-y-0 z-40 w-[7px] touch-none outline-none focus-visible:ring-2 focus-visible:ring-focus before:absolute before:inset-y-0 before:left-[3px] before:border-l before:border-line2"
    style={{ left: width - 3, cursor: "col-resize" }}
    onDoubleClick={() => onChange(DEFAULT_NAME_WIDTH)}
    onPointerDown={event => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); setDrag({ x: event.clientX, width }); }}
    onPointerMove={event => { if (drag) onChange(drag.width + event.clientX - drag.x); }}
    onPointerUp={event => { event.currentTarget.releasePointerCapture(event.pointerId); setDrag(null); }}
    onLostPointerCapture={() => setDrag(null)}
    onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      onChange(event.key === "Home" ? MIN : event.key === "End" ? MAX : width + (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 40 : 10));
    }}/></Hint>;
}
