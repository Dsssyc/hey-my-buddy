import { useEffect, useState } from "react";
import { cn } from "cn";
import { Hint } from "@/components/hint";
import type { BuddyAttention } from "./attention";
import { useTranslation } from "react-i18next";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ConsoleApi } from "../api";
import type { Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { MaintenanceView } from "./MaintenanceView";
import { ProfileSettingsView } from "./ProfileSettingsView";
import { RoutingView } from "./RoutingView";
import { buddyHash } from "./navigation";
import type { BuddyLocation } from "./navigation";

export const ROUTER_WORK = ["routing", "maintenance", "profiles"] as const;
type Work = typeof ROUTER_WORK[number];


/** The Router's three jobs share their draft, and show one selected job at a time. */
export function RouterView({ snapshot, editor, location, onNavigate, attention, hidden, active, api, refresh, mutationsAvailable }: {
  snapshot: Snapshot; editor: Editor; attention: BuddyAttention; location: BuddyLocation; onNavigate: (next: BuddyLocation) => void;
  hidden: boolean; active: boolean; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const work = (location.view === "router" ? location.target ?? "routing" : "routing") as Work;
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => setScrolled(false), [work]);
  return <section hidden={hidden} aria-label={t("sections.router")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
    <nav data-slot="router-works" data-scrolled={scrolled} aria-label={t("routerWork.jobs")} className={cn("flex shrink-0 items-center gap-6 border-b px-8 pt-6 pb-4 text-ink3 transition-colors duration-150 motion-reduce:transition-none", scrolled ? "border-border" : "border-transparent")}>
      {ROUTER_WORK.map(job => {
        const reason = job === "routing" && attention.roles ? t(`attention.${attention.roles}`) : undefined;
        const description = [t(`routerWork.description.${job}`), reason].filter(Boolean).join("\n");
        return <Hint key={job} text={description} side="bottom"><a href={buddyHash({ view: "router", target: job })} aria-current={work === job ? "page" : undefined}
          className="flex items-center gap-2 rounded-sm text-lg whitespace-nowrap outline-none transition-colors duration-150 hover:text-foreground aria-[current=page]:font-semibold aria-[current=page]:text-foreground focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
          onClick={event => { event.preventDefault(); onNavigate({ view: "router", target: job }); }}>
          {t(`routerWork.${job}`)}
          {reason && <span className="size-1.5 rounded-full bg-hand-mark" aria-hidden="true" />}
        </a></Hint>;
      })}
    </nav>
    {/* The vertical viewport keeps its width; each record region owns horizontal scrolling. */}
    <ScrollArea className="min-h-0 min-w-0 flex-1" key={work} viewportProps={{ className: "[&>div]:block!", onScroll: event => setScrolled(event.currentTarget.scrollTop > 0) }}>
      <div className="min-w-0 px-8 pb-10" data-slot="router-detail">
        {work === "routing" ? <RoutingView snapshot={snapshot} editor={editor} onNavigate={onNavigate} />
          : work === "maintenance" ? <MaintenanceView snapshot={snapshot} editor={editor} api={api} refresh={refresh} mutationsAvailable={mutationsAvailable} active={active && !hidden} />
            : <ProfileSettingsView snapshot={snapshot} editor={editor} profileRunId={location.profileRunId} />}
      </div>
    </ScrollArea>
  </section>;
}
