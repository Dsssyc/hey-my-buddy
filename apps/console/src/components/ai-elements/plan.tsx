import { cn } from "cn";
import type { ComponentProps } from "react";

export function Plan({ className, ...props }: ComponentProps<"section">) {
  return <section data-slot="session-plan" className={cn("min-w-0 border-t border-line2", className)} {...props} />;
}

export function PlanHeader({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="session-plan-header" className={cn("py-2 text-sm font-medium", className)} {...props} />;
}

export function PlanContent({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="session-plan-content" className={cn("min-w-0 pb-3 pl-6 text-sm", className)} {...props} />;
}
