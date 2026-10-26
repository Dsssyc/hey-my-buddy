/** Room for a failed narrow bar's cross beyond its tail, including its paper. */
export const TIMELINE_OUTSET_PX = 18;

/** One pattern is shared by folded tracks and the legend sample. */
export function IdlePattern({ id }: { id: string }) {
  return <defs><pattern data-slot="idle-pattern" id={id} patternUnits="userSpaceOnUse" width={14} height={14}>
    <rect width={14} height={14} className="fill-sunk"/>
    <path d="M-2 2l4-4M0 14l14-14M12 16l4-4" fill="none" stroke="var(--line)" strokeOpacity={0.4}/>
  </pattern></defs>;
}

export function WaitingDiamond({ x, y }: { x: number; y: number }) {
  return <path pointerEvents="none" data-slot="waiting-diamond" d={`M${x} ${y - 5.5}l5.5 5.5-5.5 5.5-5.5-5.5Z`} className="fill-hand-mark"/>;
}
