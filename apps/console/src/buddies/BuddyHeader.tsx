import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { harnessName } from "../buddy-display";
import { formatList } from "../i18n/format";
import type { BuddyAttention } from "./attention";
import { buddyHash } from "./navigation";
import type { BuddyLocation, BuddyView } from "./navigation";

const sectionClass = "relative flex items-center gap-1.5 self-stretch whitespace-nowrap text-ink2 outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-[current=page]:font-medium aria-[current=page]:text-foreground aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-0 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-foreground";

/** What each mark on the Buddy page says, in words: on the roles and directory sections. */
export function attentionHints(attention: BuddyAttention, t: TFunction<"buddies">): { roles?: string; harness?: string } {
  return {
    roles: attention.roles ? t(`attention.${attention.roles}`) : undefined,
    harness: attention.quota.length ? t("attention.quota", { names: formatList(attention.quota.map(harnessName)) }) : undefined,
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

/** The Buddy page's two sections: the catalogue and the roles. */
export function BuddyHeader({ location, attention, onNavigate }: {
  location: BuddyLocation; attention: BuddyAttention; onNavigate: (next: BuddyLocation) => void;
}) {
  const { t } = useTranslation("buddies");
  const hints = attentionHints(attention, t);
  const inCatalog = location.view !== "roles";
  return <nav aria-label={t("sections.label")} className="flex shrink-0 self-stretch items-center gap-[22px] bg-card">
    <ViewLink view={inCatalog ? location.view : "models"} label={t("sections.catalog")} current={inCatalog} className={sectionClass}
      hint={hints.harness} onNavigate={onNavigate} />
    <ViewLink view="roles" label={t("sections.roles")} current={!inCatalog} className={sectionClass} hint={hints.roles} onNavigate={onNavigate} />
  </nav>;
}
