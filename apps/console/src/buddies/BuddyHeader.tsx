import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { harnessName } from "../buddy-display";
import type { BuddyAttention } from "./attention";
import { buddyHash } from "./navigation";
import type { BuddyLocation, BuddyView } from "./navigation";

const sectionClass = "relative flex items-center gap-1.5 pb-3 whitespace-nowrap text-ink2 outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-[current=page]:font-medium aria-[current=page]:text-foreground aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-0 aria-[current=page]:after:-bottom-px aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-foreground";
const tableClass = "flex items-center gap-1.5 border-l border-line2 px-3 py-[5px] whitespace-nowrap text-ink2 outline-none first:border-l-0 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-[current=page]:bg-foreground aria-[current=page]:text-card";

/** What each mark on the Buddy page says, in words: on the roles section and on the harness table. */
export function attentionHints(attention: BuddyAttention, t: TFunction<"buddies">): { roles?: string; harness?: string } {
  return {
    roles: attention.roles ? t(`attention.${attention.roles}`) : undefined,
    harness: attention.quota.length ? t("attention.quota", { names: attention.quota.map(harnessName).join("、") }) : undefined,
  };
}

/**
 * The Buddy page's own navigation: its two sections, and inside the catalogue
 * its two tables. A mark shows where something waits for the user; it says what
 * on hover, on focus and to a screen reader.
 */
export function BuddyHeader({ location, attention, onNavigate }: {
  location: BuddyLocation; attention: BuddyAttention; onNavigate: (next: BuddyLocation) => void;
}) {
  const { t } = useTranslation("buddies");
  const hints = attentionHints(attention, t);
  const inCatalog = location.view !== "roles";
  function link(view: BuddyView, label: string, current: boolean, className: string, hint?: string) {
    const anchor = <a key={view} href={buddyHash({ view })} aria-current={current ? "page" : undefined} className={className}
      onClick={event => { event.preventDefault(); onNavigate({ view }); }}>
      {label}
      {hint && <span className="size-1.5 rounded-full bg-hand-mark" aria-hidden="true" />}
      {hint && <span className="sr-only">{hint}</span>}
    </a>;
    return hint ? <Tooltip key={view}><TooltipTrigger asChild>{anchor}</TooltipTrigger><TooltipContent>{hint}</TooltipContent></Tooltip> : anchor;
  }
  return <div className="shrink-0 border-b bg-card">
    <nav aria-label={t("sections.label")} className="flex gap-[22px] border-b px-3 pt-3.5 sm:px-6 [&:last-child]:border-b-0">
      {link(inCatalog ? location.view : "models", t("sections.catalog"), inCatalog, sectionClass, inCatalog ? undefined : hints.harness)}
      {link("roles", t("sections.roles"), !inCatalog, sectionClass, hints.roles)}
    </nav>
    {inCatalog && <nav aria-label={t("catalog.label")} className="flex px-3 py-3 sm:px-6">
      <div className="flex overflow-hidden rounded-md border border-line2">
        {link("models", t("catalog.models"), location.view === "models", tableClass)}
        {link("harness", t("catalog.harness"), location.view === "harness", tableClass, hints.harness)}
      </div>
    </nav>}
  </div>;
}
