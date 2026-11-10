import { IdCard, Route, Wrench } from "lucide-react";
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
const workIcons = { routing: Route, maintenance: Wrench, profiles: IdCard };

/** The Router's three jobs share their draft, and show one selected job at a time. */
export function RouterView({ snapshot, editor, location, onNavigate, attention, hidden, active, api, refresh, mutationsAvailable }: {
  snapshot: Snapshot; editor: Editor; attention: BuddyAttention; location: BuddyLocation; onNavigate: (next: BuddyLocation) => void;
  hidden: boolean; active: boolean; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const work = (location.view === "router" ? location.target ?? "routing" : "routing") as Work;
  return <section hidden={hidden} aria-label={t("sections.router")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-card">
    <nav data-slot="router-work-toolbar" aria-label={t("routerWork.jobs")} className="flex min-h-12 shrink-0 items-center gap-2 border-b bg-card px-6 py-2 text-ink2">
      {ROUTER_WORK.map(job => {
        const Icon = workIcons[job];
        const reason = job === "routing" && attention.roles ? t(`attention.${attention.roles}`) : undefined;
        const description = [t(`routerWork.description.${job}`), reason].filter(Boolean).join("\n");
        return <Hint key={job} text={description} side="bottom"><a href={buddyHash({ view: "router", target: job })} aria-current={work === job ? "page" : undefined}
          className="flex h-8 items-center gap-2 rounded-sm px-3 text-sm whitespace-nowrap outline-none transition-colors duration-150 hover:bg-sunk aria-[current=page]:bg-past-wash aria-[current=page]:font-semibold aria-[current=page]:text-foreground focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
          onClick={event => { event.preventDefault(); onNavigate({ view: "router", target: job }); }}>
          <Icon size={16} aria-hidden="true" />{t(`routerWork.${job}`)}
          {reason && <span className="size-1.5 rounded-full bg-hand-mark" aria-hidden="true" />}
        </a></Hint>;
      })}
    </nav>
    <ScrollArea className="min-h-0 min-w-0 flex-1" key={work}>
      <div className="min-w-0 px-8 pb-10" data-slot="router-detail">
        {work === "routing" ? <RoutingView snapshot={snapshot} editor={editor} onNavigate={onNavigate} />
          : work === "maintenance" ? <MaintenanceView snapshot={snapshot} editor={editor} api={api} refresh={refresh} mutationsAvailable={mutationsAvailable} active={active && !hidden} />
            : <ProfileSettingsView snapshot={snapshot} editor={editor} profileRunId={location.profileRunId} />}
      </div>
    </ScrollArea>
  </section>;
}
