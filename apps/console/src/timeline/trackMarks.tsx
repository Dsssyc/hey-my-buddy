/** Room for a failed narrow bar's cross beyond its tail, including its paper. */
export const TIMELINE_OUTSET_PX = 18;

/** Extra hit width lives above and below the drawing, leaving adjacent short fragments reachable. */
export function fragmentHitPath(start: number, end: number, middle: number, minX: number, maxX: number) {
  const width = Math.max(28, end - start + 6);
  const x = Math.max(minX, Math.min(maxX - width, (start + end) / 2 - width / 2));
  const right = Math.max(start + 1, end);
  return `M${x} ${middle - 14}H${x + width}V${middle - 11}H${right}V${middle + 11}H${x + width}V${middle + 14}H${x}V${middle + 11}H${start}V${middle - 11}H${x}Z`;
}

/** One pattern is shared by folded tracks and the legend sample. */
export function IdlePattern({ id }: { id: string }) {
  return <defs><pattern data-slot="idle-pattern" id={id} patternUnits="userSpaceOnUse" width={14} height={14}>
    <rect width={14} height={14} className="fill-sunk"/>
    <path d="M-2 2l4-4M0 14l14-14M12 16l4-4" fill="none" stroke="var(--line)" strokeOpacity={0.4}/>
  </pattern></defs>;
}

export function WaitingDiamond({ x, y, kind = "hand" }: { x: number; y: number; kind?: "hand" | "review" }) {
  return <path pointerEvents="none" data-slot="waiting-diamond" data-kind={kind} d={`M${x} ${y - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`}
    fill={kind === "review" ? "none" : undefined} strokeWidth={kind === "review" ? 1.5 : undefined} className={kind === "review" ? "stroke-hand-mark" : "fill-hand-mark"}/>;
}
