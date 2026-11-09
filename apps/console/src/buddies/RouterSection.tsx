import { useId } from "react";
import type { ComponentProps, ReactNode } from "react";
import { UnsavedMark } from "@/components/ui/state-mark";

/** The selected Router work uses three open sections, with no nested containers. */
export function RouterSection({ name, unsaved, children, ...props }: ComponentProps<"section"> & { name: string; unsaved?: string }) {
  const id = useId();
  return <section role="group" aria-labelledby={id} className="border-b py-6 last:border-b-0" {...props}>
    <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold"><span id={id}>{name}</span>{unsaved && <UnsavedMark label={unsaved} />}</h3>
    {children}
  </section>;
}

export function RouterField({ name, unsaved, children }: { name: string; unsaved?: string; children: ReactNode }) {
  const id = useId();
  return <div role="group" aria-labelledby={id} className="grid grid-cols-[220px_minmax(0,1fr)] items-start gap-x-6 gap-y-2 py-3">
    <span className="flex items-center gap-2 pt-1.5 text-xs text-ink3"><span id={id}>{name}</span>{unsaved && <UnsavedMark label={unsaved} />}</span>
    <div className="min-w-0">{children}</div>
  </div>;
}
