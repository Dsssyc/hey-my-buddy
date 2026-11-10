import type { ComponentProps } from "react";
import { cn } from "cn";
import { ScrollArea } from "@/components/ui/scroll-area";
import "./RecordRegion.css";

/** Histories grow with their contents until six rows, then keep their headings in view. */
export function RecordRegion({ header = true, className, children, ...props }: ComponentProps<typeof ScrollArea> & { header?: boolean }) {
  return <ScrollArea {...props} role="region" type="auto" orientation="both" reserveScrollbarGutter
    className={cn("bounded-records min-w-0 max-w-full", !header && "bounded-records--headless", className)}
    viewportProps={{ tabIndex: 0, "aria-label": props["aria-label"], className: "[&>div]:block!" }}>
    {children}
  </ScrollArea>;
}
