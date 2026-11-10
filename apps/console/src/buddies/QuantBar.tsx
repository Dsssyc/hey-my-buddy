import { useId, useState, type ReactNode } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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

type DisclosureProps = {
  parts: Part[]; maximum: number; width?: number; label: string; children: ReactNode;
};

/** Floating details are transient; only inline expansion is a saved display habit. */
export function QuantBarDisclosure({ floating = false, preferenceKey, ...props }: DisclosureProps & { preferenceKey: string; floating?: boolean }) {
  return floating ? <FloatingDisclosure {...props} /> : <InlineDisclosure {...props} preferenceKey={preferenceKey} />;
}

function FloatingDisclosure(props: DisclosureProps) {
  const [open, setOpen] = useState(false);
  return <DisclosureContent {...props} floating open={open} onOpenChange={setOpen} />;
}

function InlineDisclosure({ preferenceKey, ...props }: DisclosureProps & { preferenceKey: string }) {
  const [open, setOpen] = useViewPreference(`quant-bar.${preferenceKey}`);
  return <DisclosureContent {...props} floating={false} open={open} onOpenChange={setOpen} />;
}

function DisclosureContent({ parts, maximum, width, label, children, floating, open, onOpenChange }: DisclosureProps & {
  floating: boolean; open: boolean; onOpenChange: (open: boolean) => void;
}) {
  const detailId = useId();
  const hasFailure = parts.some(part => (part.tone === "pink" || part.tone === "sample-pink") && (part.value ?? 0) > 0);
  const trigger = <button type="button" data-slot="quant-bar-trigger" aria-label={label} aria-expanded={open} aria-controls={open ? detailId : undefined}
    className="quant-bar-trigger" onClick={floating ? undefined : () => onOpenChange(!open)}><QuantBar parts={parts} maximum={maximum} width={width} />{hasFailure && <StateMark kind="fail" />}</button>;
  return <div data-slot="quant-disclosure" className="inline-block max-w-full align-middle">
    {floating ? <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent id={detailId} data-slot="quant-bar-detail" aria-label={label} align="start" collisionPadding={16} onOpenAutoFocus={event => event.preventDefault()}
        className="w-max max-w-[min(28rem,calc(100vw-2rem))] text-xs tabular-nums text-ink2">{children}</PopoverContent>
    </Popover> : <>{trigger}{open && <div id={detailId} data-slot="quant-bar-detail" className="mt-2 text-xs tabular-nums text-ink2">{children}</div>}</>}
  </div>;
}
