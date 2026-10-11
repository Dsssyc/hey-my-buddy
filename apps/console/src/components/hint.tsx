import type { ReactElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * A few words that explain a control, shown on hover and on keyboard focus and
 * read out as the control's description. Without text the control stands alone.
 */
export function Hint({ text, children }: { text?: string; children: ReactElement }) {
  return text
    ? <Tooltip><TooltipTrigger asChild aria-description={text}>{children}</TooltipTrigger><TooltipContent>{text}</TooltipContent></Tooltip>
    : children;
}
