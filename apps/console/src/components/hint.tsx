import { Tooltip as TooltipPrimitive } from "radix-ui";
import type { ReactElement } from "react";
import { Tooltip, TooltipContent } from "@/components/ui/tooltip";

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
      onFocus={event => { if (!event.currentTarget.matches(":focus-visible")) event.preventDefault(); }}>{children}</TooltipPrimitive.Trigger>
    {text && <TooltipContent>{text}</TooltipContent>}
  </Tooltip>;
}
