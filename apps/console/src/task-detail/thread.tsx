import { cn } from "cn";
import type { ReactNode } from "react";

/** The initiating text, marked by a fine neutral rule. */
export function Quote({ children, className }: { children: ReactNode; className?: string }) {
  return <blockquote data-slot="request-quote" className={cn("min-w-0 border-l-2 border-line2 pl-3.5 py-0.5", className)}>{children}</blockquote>;
}
