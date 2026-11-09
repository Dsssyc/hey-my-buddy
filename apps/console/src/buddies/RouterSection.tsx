import { CircleHelp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
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

export function RouterField({ name, unsaved, help, children }: { name: string; unsaved?: string; help?: string; children: ReactNode }) {
  const id = useId();
  const { t } = useTranslation("buddies");
  return <div role="group" aria-labelledby={id} className="grid grid-cols-[220px_minmax(0,1fr)] items-start gap-x-6 gap-y-2 py-3">
    <span className={`flex items-center gap-2 text-xs text-ink3 ${help ? "min-h-8" : "pt-1.5"}`}><span id={id}>{name}</span>{help && <Popover><PopoverTrigger asChild><Button type="button" variant="ghost" size="icon-sm" data-slot="router-setting-help" aria-label={t("routerWork.settingHelp", { name })}><CircleHelp aria-hidden="true" /></Button></PopoverTrigger><PopoverContent role="dialog" aria-label={t("routerWork.settingHelp", { name })} side="bottom" align="end" sideOffset={8} className="max-w-[calc(100vw-2rem)] text-sm leading-relaxed">{help}</PopoverContent></Popover>}{unsaved && <UnsavedMark label={unsaved} />}</span>
    <div className="min-w-0">{children}</div>
  </div>;
}
