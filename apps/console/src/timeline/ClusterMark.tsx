import { useLayoutEffect, useRef, useState } from "react";

/** A single digit uses the same 16 px circle as a conclusion; longer counts gain measured room. */
export function clusterHalfWidth(count: number, textWidth: number): number {
  return count < 10 ? 8 : Math.max(9, textWidth / 2 + 4);
}

/** The numbered marker used by both the timeline and its legend. */
export function ClusterMark({ count, x, y, selected = false, focusFrame = false }: {
  count: number; x: number; y: number; selected?: boolean; focusFrame?: boolean;
}) {
  const text = useRef<SVGTextElement>(null);
  const [halfWidth, setHalfWidth] = useState(count < 10 ? 8 : 9);
  useLayoutEffect(() => {
    let disposed = false;
    const measure = () => {
      if (disposed) return;
      const width = text.current!.getComputedTextLength();
      // Hidden SVG text reports zero; measure again when its SVG is revealed.
      if (width > 0) setHalfWidth(clusterHalfWidth(count, width));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(text.current!.ownerSVGElement!);
    measure();
    document.fonts?.ready.then(measure);
    return () => { disposed = true; observer.disconnect(); };
  }, [count]);
  const frameWidth = halfWidth * 2 + 6;
  return <g data-slot="cluster-mark" aria-hidden="true">
    {selected && <rect data-slot="selected-item" x={x - halfWidth - 3} y={y - 11} width={frameWidth} height={22} rx={11} fill="none" className="pointer-events-none stroke-focus"/>}
    {halfWidth === 8
      ? <circle cx={x} cy={y} r={8} className="fill-card stroke-past" strokeWidth={1}/>
      : <rect x={x - halfWidth} y={y - 8} width={halfWidth * 2} height={16} rx={8} className="fill-card stroke-past" strokeWidth={1}/>}
    <text ref={text} x={x} y={y + 3.5} textAnchor="middle" className="pointer-events-none fill-past text-xs font-semibold">{count}</text>
    {focusFrame && <rect data-slot="fragment-focus" x={x - halfWidth - 3} y={y - 11} width={frameWidth} height={22} rx={11} fill="none" strokeWidth={2}
      className={`pointer-events-none stroke-ink opacity-0${selected ? "" : " group-focus/fragment:opacity-100"}`}/>}
  </g>;
}
