/** A shared comparison scale. Values absent from the source stay absent from the plot. */
export function QuantBar({ parts, maximum, width = 144 }: {
  parts: { value: number | null; tone: "ink" | "muted" | "faint" | "fail" }[]; maximum: number; width?: number;
}) {
  const known = parts.filter(part => part.value !== null && part.value > 0);
  const room = width - Math.max(0, known.length - 1) * 2;
  let x = 0;
  return <svg data-slot="quant-bar" width={width} height={8} viewBox={`0 0 ${width} 8`} aria-hidden="true" className="shrink-0">
    <rect width={width} height={8} className="fill-line" />
    {known.map((part, index) => {
      const size = maximum > 0 ? part.value! / maximum * room : 0;
      const left = x; x += size + 2;
      return <rect key={index} x={left} width={size} height={8} data-value={part.value} data-tone={part.tone}
        className={{ ink: "fill-ink", muted: "fill-ink2", faint: "fill-ink3", fail: "fill-fail" }[part.tone]} />;
    })}
  </svg>;
}
