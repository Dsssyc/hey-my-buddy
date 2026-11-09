import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { BuddyAttention } from "./attention";
import { buddyHash } from "./navigation";
import type { BuddyLocation, BuddyView } from "./navigation";

const sectionClass = "relative flex h-8 items-center gap-1.5 whitespace-nowrap text-ink2 outline-none transition-colors duration-150 hover:text-foreground motion-reduce:transition-none focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-[current=page]:font-medium aria-[current=page]:text-foreground aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-0 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-foreground";

/** What each mark on the Buddy page says, in words: on the Router and directory sections. */
export function attentionHints(attention: BuddyAttention, t: TFunction<"buddies">): { roles?: string } {
  return {
    roles: attention.roles ? t(`attention.${attention.roles}`) : undefined,
  };
}

/** A link to one view of the page; a mark on it says where something waits, and what on hover, on focus and to a screen reader. */
function ViewLink({ view, label, current, className, hint, onNavigate }: {
  view: BuddyView; label: string; current: boolean; className: string; hint?: string; onNavigate: (next: BuddyLocation) => void;
}) {
  const anchor = <a href={buddyHash({ view })} aria-current={current ? "page" : undefined} className={className}
    onClick={event => { event.preventDefault(); onNavigate({ view }); }}>
    {label}
    {hint && <span className="size-1.5 rounded-full bg-hand-mark" aria-hidden="true" />}
    {hint && <span className="sr-only">{hint}</span>}
  </a>;
  return hint ? <Tooltip><TooltipTrigger asChild>{anchor}</TooltipTrigger><TooltipContent>{hint}</TooltipContent></Tooltip> : anchor;
}

/** The Buddy page's two sections: the catalogue and the Router. */
export function BuddyHeader({ location, attention, onNavigate, harnessCount, enabled, total }: {
  harnessCount: number; enabled: number; total: number; location: BuddyLocation; attention: BuddyAttention; onNavigate: (next: BuddyLocation) => void;
}) {
  const { t } = useTranslation("buddies");
  const hints = attentionHints(attention, t);
  const inCatalog = location.view !== "router";
  return <div data-slot="buddy-header" className="flex h-12 shrink-0 items-center justify-between gap-6 border-b bg-card px-6"><nav aria-label={t("sections.label")} className="flex shrink-0 items-center gap-[22px] self-start bg-card">
    <ViewLink view={inCatalog ? location.view : "models"} label={t("sections.catalog")} current={inCatalog} className={sectionClass}
      onNavigate={onNavigate} />
    <ViewLink view="router" label={t("sections.router")} current={!inCatalog} className={sectionClass} hint={hints.roles} onNavigate={onNavigate} />
  </nav><span className="whitespace-nowrap text-xs text-ink2 tabular-nums" role="status">{t("sections.summary", { n: harnessCount, enabled, total })}</span></div>;
}
