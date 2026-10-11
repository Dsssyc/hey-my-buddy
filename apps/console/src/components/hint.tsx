import type { ReactElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * A few words that explain a control, shown on hover and on keyboard focus and
 * read out as the control's description. Without text the control stands alone.
 * Focus that a dialog or a script moves to the control does not show them: only
 * focus the keyboard brought, which the browser draws a ring for.
 */
export function Hint({ text, children }: { text?: string; children: ReactElement }) {
  return text
    ? <Tooltip><TooltipTrigger asChild aria-description={text}
      onFocus={event => { if (!event.currentTarget.matches(":focus-visible")) event.preventDefault(); }}>{children}</TooltipTrigger><TooltipContent>{text}</TooltipContent></Tooltip>
    : children;
}
