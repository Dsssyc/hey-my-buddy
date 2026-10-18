import { useEffect, useState } from "react";

/**
 * A scroll viewport together with its measured width: the ResizeObserver lives
 * here so a view that must not measure itself (the micro task detail) can
 * still fit its scale to the room it actually has.
 */
export function useViewport<T extends HTMLElement = HTMLDivElement>(defaultWidth: number) {
  const [viewport, setViewport] = useState<T | null>(null);
  const [width, setWidth] = useState(defaultWidth);
  useEffect(() => {
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const measure = () => { if (viewport.clientWidth > 0) setWidth(viewport.clientWidth); };
    const observer = new ResizeObserver(measure);
    observer.observe(viewport); measure();
    return () => observer.disconnect();
  }, [viewport]);
  return { viewport, setViewport, width };
}
