import { useId, type ReactNode } from "react";
import { StateMark } from "@/components/ui/state-mark";
import { useViewPreference } from "../view-preferences";
import "./QuantBar.css";

type Tone = "green" | "amber" | "pink" | "sample-pink" | "green-dark" | "orange" | "gray";
type Part = { value: number | null; tone: Tone };

/** A shared comparison scale. Values absent from the source stay absent from the plot. */
export function QuantBar({ parts, maximum, width = 144 }: {
  parts: Part[]; maximum: number; width?: number;
}) {
  const known = parts.filter(part => part.value !== null && part.value > 0);
  const room = width - Math.max(0, known.length - 1) * 2;
  let x = 0;
  return <svg data-slot="quant-bar" width={width} height={8} viewBox={`0 0 ${width} 8`} aria-hidden="true" className="shrink-0">
    <rect width={width} height={8} className="fill-line" />
    {known.map((part, index) => {
      const size = maximum > 0 ? part.value! / maximum * room : 0;
      const left = x; x += size + 2;
      return <rect key={index} x={left} width={size} height={8} data-value={part.value} data-tone={part.tone} className="quant-bar-part" />;
    })}
  </svg>;
}

/** The graphic itself opens its exact values; the saved state is a console view preference. */
export function QuantBarDisclosure({ parts, maximum, width, label, preferenceKey, children }: {
  parts: Part[]; maximum: number; width?: number; label: string; preferenceKey: string; children: ReactNode;
}) {
  const detailId = useId();
  const [open, setOpen] = useViewPreference(`quant-bar.${preferenceKey}`);
  const hasFailure = parts.some(part => (part.tone === "pink" || part.tone === "sample-pink") && (part.value ?? 0) > 0);
  return <div data-slot="quant-disclosure" className="inline-block max-w-full align-middle">
    <button type="button" data-slot="quant-bar-trigger" aria-label={label} aria-expanded={open} aria-controls={open ? detailId : undefined}
      className="quant-bar-trigger" onClick={() => setOpen(!open)}><QuantBar parts={parts} maximum={maximum} width={width} />{hasFailure && <StateMark kind="fail" />}</button>
    {open && <div id={detailId} data-slot="quant-bar-detail" className="mt-2 text-xs tabular-nums text-ink2">{children}</div>}
  </div>;
}
