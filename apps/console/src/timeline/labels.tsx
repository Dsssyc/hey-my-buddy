import { useLayoutEffect, useRef, useState } from "react";

export type Interval = { left: number; right: number };
export type Occupancy = Interval & { kind: "label" | "obstacle" };
export type Label = Interval & { kind: "label"; text: string; y: number; className: string; eventKey?: string };

/** Text neighbours need twelve pixels; shapes need four. Full, short, then no label. */
export function fitLabel(candidates: readonly string[], bounds: Interval, occupied: readonly Occupancy[], measure: (text: string) => number, preferred = bounds.left): (Interval & { kind: "label"; text: string }) | null {
  const free: Interval[] = [];
  let left = bounds.left;
  const blocked = occupied.map(item => {
    const gap = item.kind === "label" ? 12 : 4;
    return { left: item.left - gap, right: item.right + gap };
  }).sort((a, b) => a.left - b.left);
  for (const item of blocked) {
    if (item.right <= left || item.left >= bounds.right) continue;
    if (item.left > left) free.push({ left, right: Math.min(bounds.right, item.left) });
    left = Math.max(left, item.right);
  }
  if (left < bounds.right) free.push({ left, right: bounds.right });
  for (const text of candidates) {
    const width = measure(text);
    if (!text || !Number.isFinite(width)) continue;
    const room = free.filter(part => part.right - part.left >= width)
      .sort((a, b) => Math.abs(Math.max(a.left, Math.min(preferred, a.right - width)) - preferred) - Math.abs(Math.max(b.left, Math.min(preferred, b.right - width)) - preferred))[0];
    if (room) {
      const x = Math.max(room.left, Math.min(preferred, room.right - width));
      return { kind: "label", text, left: x, right: x + width };
    }
  }
  return null;
}

/** Event names stay beside their mark: right, left, or omitted, never shifted. */
export function fitAdjacentLabel(text: string, mark: Interval, bounds: Interval, occupied: readonly Occupancy[], measure: (text: string) => number): (Interval & { kind: "label"; text: string }) | null {
  const width = measure(text);
  if (!text || !Number.isFinite(width)) return null;
  for (const left of [mark.right + 4, mark.left - 4 - width]) {
    const right = left + width;
    if (left < bounds.left || right > bounds.right) continue;
    if (occupied.some(item => {
      const gap = item.kind === "label" ? 12 : 4;
      return left < item.right + gap && right > item.left - gap;
    })) continue;
    return { kind: "label", text, left, right };
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
      for (const text of ref.current!.querySelectorAll("text")) {
        const value = text.textContent!, width = text.getComputedTextLength();
        // display:none yields zero for nonempty SVG text. It is unmeasured,
        // not free to fit into any interval.
        if (!value || width > 0) next[value] = width;
      }
      setWidths(next);
    };
    // The SVG's size changes when an enclosing page is hidden or revealed,
    // even when neither its candidate strings nor its recorded scale changes.
    const observer = new ResizeObserver(measure);
    observer.observe(ref.current!.ownerSVGElement!);
    measure();
    document.fonts?.ready.then(measure);
    return () => { disposed = true; observer.disconnect(); };
  }, [key]);
  return {
    measure: (text: string) => widths[text] ?? Infinity,
    ruler: <g ref={ref} visibility="hidden" aria-hidden="true" pointerEvents="none">{(JSON.parse(key) as string[]).map(text => <text key={text}>{text}</text>)}</g>,
  };
}
