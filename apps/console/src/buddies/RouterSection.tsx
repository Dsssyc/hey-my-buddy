import { CircleHelp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useId } from "react";
import type { ComponentProps, ReactNode } from "react";
import { UnsavedMark } from "@/components/ui/state-mark";

/** The selected Router work uses three open sections, with no nested containers. */
export function RouterSection({ name, unsaved, help, children, ...props }: ComponentProps<"section"> & { name: string; unsaved?: string; help?: ReactNode }) {
  const id = useId();
  return <section role="group" aria-labelledby={id} className="border-b py-6 last:border-b-0" {...props}>
    <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold"><RouterLabel id={id} name={name} help={help} unsaved={unsaved} /></h3>
    {children}
  </section>;
}

/** The label anchors the card; the question mark alone opens it. */
function RouterLabel({ id, name, help, unsaved, className = "" }: { id: string; name: string; help?: ReactNode; unsaved?: string; className?: string }) {
  const { t } = useTranslation("buddies");
  const label = t("routerWork.settingHelp", { name });
  const content = <span className={`flex items-center gap-2 ${className}`}>
    <span id={id}>{name}</span>
    {help && <PopoverTrigger asChild><Button type="button" variant="ghost" size="icon-sm" data-slot="router-setting-help" aria-label={label}><CircleHelp aria-hidden="true" /></Button></PopoverTrigger>}
    {unsaved && <UnsavedMark label={unsaved} />}
  </span>;
  if (!help) return content;
  return <Popover>
    <PopoverAnchor asChild><span className="self-stretch">{content}</span></PopoverAnchor>
    <PopoverContent role="dialog" aria-label={label} side="bottom" align="start" sideOffset={8} collisionPadding={16} className="max-w-[calc(100vw-2rem)] text-sm leading-relaxed">{help}</PopoverContent>
  </Popover>;
}

export function RouterField({ name, unsaved, help, children }: { name: string; unsaved?: string; help?: ReactNode; children: ReactNode }) {
  const id = useId();
  return <div role="group" aria-labelledby={id} className="grid grid-cols-[220px_minmax(0,1fr)] items-start gap-x-6 gap-y-2 py-3">
    <RouterLabel id={id} name={name} help={help} unsaved={unsaved} className={`text-xs text-ink3 ${help ? "min-h-8" : "pt-1.5"}`} />
    <div className="min-w-0">{children}</div>
  </div>;
}
