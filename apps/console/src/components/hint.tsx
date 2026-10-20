import { Tooltip as TooltipPrimitive, Slot } from "radix-ui";
import type { ReactElement, ComponentProps } from "react";
import { Tooltip, TooltipContent } from "@/components/ui/tooltip";

/** Tooltip state belongs to the tooltip; the target may have its own state (tabs, disclosures). */
function HintTarget({ children, "data-state": tooltipState, ...props }: ComponentProps<typeof Slot.Root> & { "data-state"?: string }) {
  void tooltipState;
  return <Slot.Root {...props}>{children}</Slot.Root>;
}

/**
 * A few words that explain a control, shown on hover and on keyboard focus and
 * read out as the control's description. Without text the control stands alone:
 * no popup and no description, but the control keeps its place in the tree, so
 * focus and the DOM node survive the text coming and going.
 * The trigger is Radix's own, not the `data-slot` one in `ui/tooltip`: that one
 * would overwrite the control's own `data-slot` through `asChild`.
 * Focus that a dialog or a script moves to the control does not show them: only
 * focus the keyboard brought, which the browser draws a ring for.
 */
export function Hint({ text, children }: { text?: string; children: ReactElement }) {
  return <Tooltip>
    <TooltipPrimitive.Trigger asChild aria-description={text || undefined}
      onFocus={event => { if (!(event.target as Element).matches(":focus-visible")) event.preventDefault(); }}><HintTarget>{children}</HintTarget></TooltipPrimitive.Trigger>
    {text && <TooltipContent>{text}</TooltipContent>}
  </Tooltip>;
}
