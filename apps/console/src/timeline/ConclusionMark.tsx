const RADIUS_PX = 8;
const STROKE_PX = 1;
/** Space from the glyph's centre to its full outer stroke. */
export const CONCLUSION_OUTSET_PX = RADIUS_PX + STROKE_PX / 2;

/** The circle and its outcome stroke are the same in a timeline row and its legend. */
export function ConclusionMark({ kind, x, y }: { kind: "accepted" | "failed"; x: number; y: number }) {
  return <g data-slot="conclusion-glyph" data-kind={kind}>
    <circle cx={x} cy={y} r={RADIUS_PX} strokeWidth={STROKE_PX} className="fill-card stroke-past"/>
    <path d={kind === "accepted" ? `M${x - 4} ${y}l3 3 5-6` : `M${x - 3} ${y - 3}l6 6m0-6l-6 6`}
      fill="none" className={kind === "accepted" ? "stroke-past" : "stroke-fail"} strokeWidth={1.8}/>
  </g>;
}
