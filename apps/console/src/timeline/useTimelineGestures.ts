import { useEffect, useRef } from "react";

/** Bind native, non-passive gestures to the scrolling timeline viewport. */
export function useTimelineGestures(
  viewport: HTMLElement | null,
  ruler: HTMLElement | null,
  zoomAt: (factor: number, clientX: number) => void,
  fit: () => void,
  wholeWheel = false,
) {
  const callbacks = useRef({ zoomAt, fit });
  callbacks.current = { zoomAt, fit };
  useEffect(() => {
    if (!viewport || !ruler) return;
    let pinchDistance: number | null = null;
    let pinching = false;
    let lastTap: { time: number; x: number; y: number } | null = null;
    const onWheel = (event: WheelEvent) => {
      if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)
        || !(event.ctrlKey || wholeWheel || ruler.contains(event.target as Node))) return;
      event.preventDefault();
      const delta = event.deltaY || event.deltaX;
      if (delta !== 0) callbacks.current.zoomAt(Math.exp(-Math.max(-400, Math.min(400, delta)) * .002), event.clientX);
    };
    const distance = (touches: TouchList) => Math.hypot(
      touches[0]!.clientX - touches[1]!.clientX,
      touches[0]!.clientY - touches[1]!.clientY,
    );
    const onTouchStart = (event: TouchEvent) => {
      if (event.touches.length !== 2 || !ruler.contains(event.target as Node)) return;
      pinchDistance = distance(event.touches);
      pinching = true;
      lastTap = null;
    };
    const onTouchMove = (event: TouchEvent) => {
      if (pinchDistance === null || event.touches.length !== 2) return;
      event.preventDefault();
      const next = distance(event.touches);
      if (pinchDistance > 0 && next > 0) callbacks.current.zoomAt(next / pinchDistance, (event.touches[0]!.clientX + event.touches[1]!.clientX) / 2);
      pinchDistance = next;
    };
    const onTouchEnd = (event: TouchEvent) => {
      if (pinching) {
        if (event.touches.length < 2) pinchDistance = null;
        if (event.touches.length === 0) pinching = false;
        return;
      }
      if (event.touches.length || event.changedTouches.length !== 1 || !ruler.contains(event.target as Node)) return;
      const touch = event.changedTouches[0]!;
      const now = performance.now();
      if (lastTap && now - lastTap.time < 350 && Math.hypot(touch.clientX - lastTap.x, touch.clientY - lastTap.y) < 24) {
        event.preventDefault();
        callbacks.current.fit();
        lastTap = null;
      } else lastTap = { time: now, x: touch.clientX, y: touch.clientY };
    };
    const onTouchCancel = () => { pinchDistance = null; pinching = false; lastTap = null; };
    const onDoubleClick = (event: MouseEvent) => {
      if (ruler.contains(event.target as Node)) { event.preventDefault(); callbacks.current.fit(); }
    };
    viewport.addEventListener("wheel", onWheel, { passive: false });
    viewport.addEventListener("touchstart", onTouchStart, { passive: true });
    viewport.addEventListener("touchmove", onTouchMove, { passive: false });
    viewport.addEventListener("touchend", onTouchEnd, { passive: false });
    viewport.addEventListener("touchcancel", onTouchCancel);
    viewport.addEventListener("dblclick", onDoubleClick);
    return () => {
      viewport.removeEventListener("wheel", onWheel);
      viewport.removeEventListener("touchstart", onTouchStart);
      viewport.removeEventListener("touchmove", onTouchMove);
      viewport.removeEventListener("touchend", onTouchEnd);
      viewport.removeEventListener("touchcancel", onTouchCancel);
      viewport.removeEventListener("dblclick", onDoubleClick);
    };
  }, [viewport, ruler, wholeWheel]);
}
