/** Every status stroke touching a buddy fill has the track's paper on both sides. */
export function IsolatedStatusMark({ slot, d, stroke, paper, width = 2, dashed = false }: {
  slot: string; d: string; stroke: string; paper: string; width?: number; dashed?: boolean;
}) {
  return <g pointerEvents="none" data-slot="isolated-status-mark">
    <path data-slot="status-mark-paper" d={d} fill="none" className={paper} strokeWidth={width + 3} strokeLinecap="round" strokeLinejoin="round"/>
    <path data-slot={slot} d={d} fill="none" className={stroke} strokeWidth={width} strokeDasharray={dashed ? "3 2" : undefined} strokeLinecap={dashed ? "butt" : "round"} strokeLinejoin="round"/>
  </g>;
}
