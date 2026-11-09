import { useTranslation } from "react-i18next";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StateMark } from "@/components/ui/state-mark";
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
type Status = "unset" | "unknown" | "running" | "completed" | "failed";

function workStatus(work: Work, snapshot: Snapshot, editor: Editor): Status {
  const config = snapshot.configuration === null ? null : editor.view.configuration;
  if (work === "routing") {
    if (!config?.routerBuddyIds.length) return "unset";
    const health = snapshot.routingHealth;
    if (!health || !health.sampleCount) return "unknown";
    const failures = [...health.recentFailures.map(row => Date.parse(row.at)), ...(health.routers ?? []).flatMap(row => row.lastError ? [Date.parse(row.lastError.at)] : [])];
    const successes = [health.lastSuccessAt, ...(health.routers ?? []).map(row => row.lastAnsweredAt)].flatMap(at => at ? [Date.parse(at)] : []);
    if (failures.length && (!successes.length || Math.max(...failures) > Math.max(...successes))) return "failed";
    return successes.length ? "completed" : "unknown";
  }
  if (!config?.[work === "maintenance" ? "maintenanceBuddyId" : "profileBuddyId"]) return "unset";
  const runs = work === "maintenance" ? snapshot.maintenance?.runs ?? []
    : snapshot.models.map(model => model.latestProfileRun).filter(run => run !== null);
  const latest = [...runs].sort((a, b) => Date.parse(b.startedAt) - Date.parse(a.startedAt))[0];
  return latest?.state ?? "unknown";
}

/** The Router's three jobs share their draft, and show one selected job at a time. */
export function RouterView({ snapshot, editor, location, onNavigate, hidden, active, api, refresh, mutationsAvailable }: {
  snapshot: Snapshot; editor: Editor; location: BuddyLocation; onNavigate: (next: BuddyLocation) => void;
  hidden: boolean; active: boolean; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const work = (location.view === "router" ? location.target ?? "routing" : "routing") as Work;
  const status = workStatus(work, snapshot, editor);
  return <section hidden={hidden} aria-label={t("sections.router")} className="flex min-h-0 min-w-0 flex-1 bg-card">
    <nav aria-label={t("routerWork.jobs")} className="w-[260px] shrink-0 border-r py-3">
      {ROUTER_WORK.map(job => {
        const state = workStatus(job, snapshot, editor);
        const mark = state === "unset" || state === "unknown" ? "idle" : state === "completed" ? "done" : state === "running" ? "live" : "fail";
        return <a key={job} href={buddyHash({ view: "router", target: job })} aria-current={work === job ? "page" : undefined}
          className="block border-b px-6 py-5 outline-none transition-colors duration-150 hover:bg-sunk aria-[current=page]:bg-past-wash focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
          onClick={event => { event.preventDefault(); onNavigate({ view: "router", target: job }); }}>
          <strong className="text-base font-medium">{t(`routerWork.${job}`)}</strong>
          <span className="mt-2 flex items-center gap-2 text-xs text-ink2"><StateMark kind={mark} />{state === "completed" && job === "maintenance" ? t("routerWork.status.completed_maintenance") : state === "failed" && job === "maintenance" ? t("routerWork.status.failed_maintenance") : t(`routerWork.status.${state}`)}</span>
        </a>;
      })}
    </nav>
    <ScrollArea className="min-h-0 min-w-0 flex-1" key={work}>
      <div className="min-w-0 px-8 pt-6 pb-10" data-slot="router-detail">
        <header className="border-b pb-5"><h2 className="text-lg font-semibold">{t(`routerWork.${work}`)}</h2>
          <p className="mt-1.5 text-xs text-ink2">{t(`routerWork.description.${work}`)}</p>
          {status === "unset" && <p className="mt-3 text-xs text-ink2">{t("routerWork.notSet")}</p>}
        </header>
        {work === "routing" ? <RoutingView snapshot={snapshot} editor={editor} onNavigate={onNavigate} />
          : work === "maintenance" ? <MaintenanceView snapshot={snapshot} editor={editor} api={api} refresh={refresh} mutationsAvailable={mutationsAvailable} active={active && !hidden} />
            : <ProfileSettingsView snapshot={snapshot} editor={editor} />}
      </div>
    </ScrollArea>
  </section>;
}
