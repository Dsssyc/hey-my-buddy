import { useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

export function SplitView({ selected, list, detail }: { selected: boolean; list: ReactNode; detail: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [width, setWidth] = useState(330);
  const resize = (value: number) => setWidth(Math.round(Math.max(260, Math.min(value, 480, (root.current?.clientWidth || 1000) * .5))));
  return <div ref={root} className={`workspace-grid ${selected ? "has-selection" : ""}`}
    style={{ "--list-width": `${width}px` } as CSSProperties}>
    {list}
    <div className="pane-divider" role="separator" aria-label="调整列表宽度" aria-orientation="vertical"
      aria-valuemin={260} aria-valuemax={480} aria-valuenow={width} tabIndex={0}
      onKeyDown={event => { if (["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); resize(width + (event.key === "ArrowRight" ? 20 : -20)); } }}
      onPointerDown={event => { dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={event => { if (dragging.current && root.current) resize(event.clientX - root.current.getBoundingClientRect().left); }}
      onPointerUp={() => { dragging.current = false; }} onLostPointerCapture={() => { dragging.current = false; }} />
    {detail}
  </div>;
}
