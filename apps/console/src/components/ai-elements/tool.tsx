import { cn } from "cn";
import type { ComponentProps } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";

export function Tool({ className, defaultOpen = true, ...props }: ComponentProps<typeof Collapsible>) {
  return <Collapsible data-slot="session-tool" defaultOpen={defaultOpen} className={cn("group/tool min-w-0 border-t border-line2", className)} {...props} />;
}

export function ToolHeader({ title, category, status, className, ...props }: Omit<ComponentProps<typeof CollapsibleTrigger>, "children" | "title"> & { title: string; category: string; status: string }) {
  return <CollapsibleTrigger className={cn("grid w-full min-w-0 grid-cols-[16px_minmax(0,1fr)] items-start gap-2 py-2 text-left text-sm hover:bg-sunk focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)} {...props}>
    <Disclosure open={false} className="group-data-[state=open]/tool:rotate-90" />
    <span className="min-w-0">
      <span className="block break-words font-medium">{title}</span>
      <span className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-ink2"><span>{category}</span><span>{status}</span></span>
    </span>
  </CollapsibleTrigger>;
}

export function ToolContent({ className, ...props }: ComponentProps<typeof CollapsibleContent>) {
  return <CollapsibleContent className={cn("min-w-0 pb-3 pl-6 text-sm", className)} {...props} />;
}
