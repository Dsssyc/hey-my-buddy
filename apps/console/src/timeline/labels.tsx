import { useLayoutEffect, useRef, useState } from "react";

export type Interval = { left: number; right: number };
export type Label = Interval & { text: string; y: number; className: string };

/** Place a measured candidate in free space. Full, short, then no label. */
export function fitLabel(candidates: readonly string[], bounds: Interval, occupied: readonly Interval[], measure: (text: string) => number, preferred = bounds.left): (Interval & { text: string }) | null {
  const free: Interval[] = [];
  let left = bounds.left;
  for (const item of [...occupied].sort((a, b) => a.left - b.left)) {
    if (item.right <= left || item.left >= bounds.right) continue;
    if (item.left > left) free.push({ left, right: Math.min(bounds.right, item.left - 4) });
    left = Math.max(left, item.right + 4);
  }
  if (left < bounds.right) free.push({ left, right: bounds.right });
  for (const text of candidates) {
    const width = measure(text);
    if (!text || !Number.isFinite(width)) continue;
    const room = free.filter(part => part.right - part.left >= width)
      .sort((a, b) => Math.abs(Math.max(a.left, Math.min(preferred, a.right - width)) - preferred) - Math.abs(Math.max(b.left, Math.min(preferred, b.right - width)) - preferred))[0];
    if (room) {
      const x = Math.max(room.left, Math.min(preferred, room.right - width));
      return { text, left: x, right: x + width };
    }
  }
  return null;
}

/** Measure with the same SVG text style that will be drawn, after font loading. */
export function useTextMeasure(candidates: readonly string[]) {
  const ref = useRef<SVGGElement>(null);
  const [widths, setWidths] = useState<Record<string, number>>({});
  const key = JSON.stringify([...new Set(candidates)]);
  useLayoutEffect(() => {
    let disposed = false;
    const measure = () => {
      if (disposed) return;
      const next: Record<string, number> = {};
      for (const text of ref.current!.querySelectorAll("text")) next[text.textContent!] = text.getComputedTextLength();
      setWidths(next);
    };
    measure();
    document.fonts?.ready.then(measure);
    return () => { disposed = true; };
  }, [key]);
  return {
    measure: (text: string) => widths[text] ?? Infinity,
    ruler: <g ref={ref} visibility="hidden" aria-hidden="true" pointerEvents="none">{(JSON.parse(key) as string[]).map(text => <text key={text}>{text}</text>)}</g>,
  };
}
