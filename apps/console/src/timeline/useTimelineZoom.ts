import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { createTimelineScale } from "./scale";
import type { ScaleInput } from "./scale";

const MAX_PPM = 60;
const DURATION = 180;

/** Zoom the actual time scale while keeping the chosen time at its screen coordinate. */
export function useTimelineZoom(input: ScaleInput | null, available: number, macroId: string | undefined,
  viewport: RefObject<HTMLDivElement | null>, anchorAt: number | null, nowMs: number,
  /** Where scale x=0 sits in the viewport's own coordinates, so anchors hold beside sticky columns. */
  originX = 24) {
  const fit = useMemo(() => input ? createTimelineScale(input, Math.max(1, available)) : null, [input, available]);
  const minimum = Math.min(MAX_PPM, fit?.pixelsPerMinute ?? 0);
  const [zoom, setZoom] = useState<{ macroId: string | undefined; ppm: number | null }>({ macroId, ppm: null });
  const ppm = zoom.macroId === macroId && zoom.ppm !== null ? Math.max(minimum, Math.min(MAX_PPM, zoom.ppm)) : minimum;
  const scale = useMemo(() => input ? createTimelineScale(input, Math.max(1, available), ppm) : null, [input, available, ppm]);
  const frame = useRef<number | null>(null);
  const target = useRef<number | null>(null);
  const anchor = useRef<{ at: number; screenX: number } | null>(null);
  const cancel = () => { if (frame.current !== null) cancelAnimationFrame(frame.current); frame.current = null; };
  useEffect(() => () => cancel(), []);
  useLayoutEffect(() => {
    cancel(); target.current = null; anchor.current = null;
    setZoom({ macroId, ppm: null });
    if (viewport.current) viewport.current.scrollLeft = 0;
  }, [macroId]);
  useLayoutEffect(() => {
    if (anchor.current && scale && viewport.current) viewport.current.scrollLeft = scale.toX(anchor.current.at)! + originX - anchor.current.screenX;
    if (frame.current === null) anchor.current = null;
  }, [scale]);

  const change = (direction: -1 | 0 | 1) => {
    if (!scale || !viewport.current) return;
    const next = direction === 0 ? minimum : Math.max(minimum, Math.min(MAX_PPM, (target.current ?? ppm) * (direction === 1 ? 2 : .5)));
    cancel(); target.current = next;
    const at = anchorAt ?? nowMs;
    anchor.current = { at, screenX: scale.toX(at)! + originX - viewport.current.scrollLeft };
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || next === ppm) {
      target.current = null;
      setZoom({ macroId, ppm: next === minimum ? null : next });
      return;
    }
    const started = performance.now();
    const animate = (time: number) => {
      const fraction = Math.min(1, (time - started) / DURATION);
      const eased = 1 - (1 - fraction) ** 3;
      setZoom({ macroId, ppm: fraction === 1 && next === minimum ? null : ppm + (next - ppm) * eased });
      if (fraction < 1) frame.current = requestAnimationFrame(animate);
      else { frame.current = null; target.current = null; }
    };
    frame.current = requestAnimationFrame(animate);
  };
  /** Keep the instant under a wheel or pinch gesture at that screen coordinate. */
  const zoomAt = (factor: number, clientX: number) => {
    if (!scale || !viewport.current || !Number.isFinite(factor) || factor <= 0) return;
    const view = viewport.current;
    const screenX = clientX - view.getBoundingClientRect().left;
    const at = scale.toTime(screenX + view.scrollLeft - originX);
    if (at === null) return;
    const next = Math.max(minimum, Math.min(MAX_PPM, (target.current ?? ppm) * factor));
    if (next === ppm) return;
    cancel(); target.current = null;
    anchor.current = { at, screenX };
    setZoom({ macroId, ppm: next === minimum ? null : next });
  };
  return { scale, change, zoomAt, atMinimum: ppm <= minimum, atMaximum: ppm >= MAX_PPM };
}
