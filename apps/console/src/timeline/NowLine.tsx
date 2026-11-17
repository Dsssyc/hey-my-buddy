/** The row segment shares its instant and stroke with the sticky axis segment. */
export function NowLine({ x }: { x: number }) {
  return <div data-slot="timeline-now" aria-hidden="true" className="pointer-events-none absolute inset-y-0 z-30 border-l-2 border-live opacity-80" style={{ left: x - 1 }}/>;
}

/** SVG paint order keeps the ruler above ticks and below the axis lettering. */
export function NowAxisLine({ x, height }: { x: number; height: number }) {
  return <line data-slot="timeline-now-axis" pointerEvents="none" x1={x} x2={x} y1={0} y2={height} strokeWidth={2} className="stroke-live opacity-80"/>;
}
