import { useId, type ReactNode } from "react";

/** The mockup's row of a name and its controls, stacked only on narrow screens. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return <section aria-labelledby={id} data-slot="settings-section" className="grid min-w-0 grid-cols-1 items-start gap-x-5 gap-y-2 border-b px-4 py-[18px] last:border-b-0 sm:grid-cols-[112px_minmax(0,1fr)] sm:px-6">
    <h2 id={id} className="pt-[5px] text-sm font-normal text-ink2">{title}</h2>
    <div className="min-w-0">{children}</div>
  </section>;
}
