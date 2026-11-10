import { cn } from "cn";
import { CircleHelp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useId } from "react";
import type { ComponentProps, ReactNode } from "react";
import { UnsavedMark } from "@/components/ui/state-mark";

/** One work card shares its shape and heading height with the other Router cards. */
export function RouterSection({ name, unsaved, help, actions, children, className, ...props }: ComponentProps<"section"> & { name: string; unsaved?: string; help?: ReactNode; actions?: ReactNode }) {
  const id = useId();
  return <section role="group" aria-labelledby={id} data-slot="router-card" className={cn("min-w-0 rounded-(--radius-card) border bg-card", className)} {...props}>
    <header data-slot="router-card-header" className="flex h-12 items-center justify-between gap-4 px-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold"><RouterLabel id={id} name={name} help={help} unsaved={unsaved} /></h3>
      {actions}
    </header>
    <div className="min-w-0 px-4 pb-4">{children}</div>
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
    <PopoverAnchor asChild><span className="flex self-stretch items-center">{content}</span></PopoverAnchor>
    <PopoverContent role="dialog" aria-label={label} side="bottom" align="start" sideOffset={8} collisionPadding={16} className="max-w-[calc(100vw-2rem)] text-sm leading-relaxed">{help}</PopoverContent>
  </Popover>;
}

export function RouterField({ name, unsaved, help, children, className }: { name: string; unsaved?: string; help?: ReactNode; children: ReactNode; className?: string }) {
  const id = useId();
  return <div role="group" aria-labelledby={id} data-slot="router-field" className={cn("grid min-h-11 grid-cols-[max-content_minmax(0,1fr)] items-center gap-x-4 py-1.5", className)}>
    <RouterLabel id={id} name={name} help={help} unsaved={unsaved} className="text-sm text-ink2" />
    <div className="min-w-0">{children}</div>
  </div>;
}
