import { cn } from "cn";
import type { ReactNode } from "react";

/**
 * The process of one execution, top to bottom in time: one line, and on it a
 * node for each thing that happened. A diamond is something put in (a task, a
 * Host's input; hollow for an inquiry), a dot is a message, a square is the
 * result.
 */
export function Thread({ children, className }: { children: ReactNode; className?: string }) {
  return <div data-slot="detail-thread" className={cn("relative px-6 py-2.5 before:absolute before:top-[30px] before:bottom-[22px] before:left-[33.25px] before:border-l-[1.5px] before:border-line2", className)}>{children}</div>;
}

const marks = {
  input: "left-[5px] size-2 rotate-45 bg-past",
  ask: "left-[5px] size-2 rotate-45 border-[1.3px] border-ink2 bg-card",
  message: "left-1.5 size-[7px] rounded-full bg-line2",
  result: "left-1 size-2.5 bg-past",
};

export function Node({ mark, children, className }: { mark: keyof typeof marks; children: ReactNode; className?: string }) {
  return <div className={cn("relative flex items-baseline gap-2 py-1.5 pl-8 leading-relaxed", className)}>
    <i aria-hidden="true" className={cn("absolute top-[15px] shadow-[0_0_0_4px_var(--surface)]", marks[mark])} />
    {children}
  </div>;
}

/** What was put in, set apart on a pale ground: a quotation. */
export function Quote({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("min-w-0 flex-1 bg-sunk px-3 py-2 text-ink2", className)}>{children}</div>;
}
