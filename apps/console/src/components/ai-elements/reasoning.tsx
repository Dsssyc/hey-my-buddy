import { cn } from "cn";
import type { ComponentProps } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { MessageResponse } from "./message";

export function Reasoning({ className, defaultOpen = false, ...props }: ComponentProps<typeof Collapsible>) {
  return <Collapsible data-slot="session-reasoning" defaultOpen={defaultOpen} className={cn("group/reasoning min-w-0", className)} {...props} />;
}

export function ReasoningTrigger({ children, className, ...props }: ComponentProps<typeof CollapsibleTrigger>) {
  return <CollapsibleTrigger className={cn("flex w-full items-center gap-1.5 py-1 text-left text-sm", className)} {...props}>
    <Disclosure open={false} className="group-data-[state=open]/reasoning:rotate-90" />
    {children}
  </CollapsibleTrigger>;
}

export function ReasoningContent({ children, className, ...props }: Omit<ComponentProps<typeof CollapsibleContent>, "children"> & { children: string }) {
  return <CollapsibleContent className={cn("min-w-0 pl-6 text-sm text-ink2", className)} {...props}>
    <MessageResponse>{children}</MessageResponse>
  </CollapsibleContent>;
}
