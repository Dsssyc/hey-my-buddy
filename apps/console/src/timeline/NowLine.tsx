/** A single non-interactive ruler above the complete row stack, centred on the axis instant. */
export function NowLine({ x }: { x: number }) {
  return <div data-slot="timeline-now" aria-hidden="true" className="pointer-events-none absolute inset-y-0 z-30 border-l-2 border-live opacity-80" style={{ left: x - 1 }}/>;
}
