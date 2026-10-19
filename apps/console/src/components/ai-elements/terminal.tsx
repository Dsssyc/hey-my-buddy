import { cn } from "cn";
import Ansi from "./ansi.cjs";
import type { ComponentProps } from "react";

export function Terminal({ output, className, ...props }: Omit<ComponentProps<"div">, "children"> & { output: string }) {
  return <div data-slot="session-terminal" className={cn("min-w-0 overflow-x-auto bg-sunk p-3 text-foreground", className)} {...props}>
    <pre className="m-0 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed"><Ansi linkify={false}>{output}</Ansi></pre>
  </div>;
}
