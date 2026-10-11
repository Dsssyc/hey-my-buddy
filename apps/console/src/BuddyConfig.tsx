import { useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { RoutingStatusBar } from "./RoutingStatusBar";
import { HarnessStatus } from "./HarnessStatus";
import { buddyAttention } from "./buddies/attention";
import { BuddyHeader, CatalogPick } from "./buddies/BuddyHeader";
import { ModelsView } from "./buddies/ModelsView";
import { buddyHash, buddyLocation } from "./buddies/navigation";
import type { BuddyLocation } from "./buddies/navigation";

/**
 * The Buddy page: the catalogue (its models and its harnesses) and the roles.
 * Each view has an address. The models table is the rebuilt view; the harness
 * table and the roles still show their earlier components.
 */
export function BuddyConfig({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [location, setLocation] = useState<BuddyLocation>(() => buddyLocation(window.location.hash) ?? { view: "models" });
  const handledLocation = useRef<BuddyLocation | null>(null);
  const attention = buddyAttention(editor.view, snapshot);
  useEffect(() => {
    const changed = () => {
      const next = buddyLocation(window.location.hash);
      if (next) setLocation(next);
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    if (!active || location.view !== "roles" || !location.target || handledLocation.current === location) return;
    const target = location.target === "health" ? document.querySelector<HTMLElement>(".routing-status[aria-label='路由健康']")
      : document.getElementById("router-current");
    target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
    handledLocation.current = location;
  }, [location, active]);
  function navigate(next: BuddyLocation) {
    setLocation(next);
    window.location.hash = buddyHash(next);
  }
  return <div className="flex h-full min-h-0 flex-col">
    <BuddyHeader location={location} attention={attention} onNavigate={navigate} />
    <ModelsView snapshot={snapshot} editor={editor} api={api} refresh={refresh} active={active && location.view === "models"}
      mutationsAvailable={mutationsAvailable} location={location} attention={attention} onNavigate={navigate} hidden={location.view !== "models"} />
    <div className="buddy-section-page flex-1 p-3 sm:px-6 sm:py-4" hidden={location.view === "models"}>
      <section className="buddy-section" hidden={location.view !== "roles"} aria-label="Router 分区">
        <RoutingStatusBar data={editor.view} snapshot={snapshot} editor={editor} onShowRouter={profileId => navigate({ view: "models", target: profileId })} expanded />
      </section>
      <section className="buddy-section" hidden={location.view !== "harness"} aria-label="Harness 分区">
        <div className="mb-3 flex"><CatalogPick location={location} attention={attention} onNavigate={navigate} /></div>
        <HarnessStatus snapshot={snapshot} api={api} refresh={refresh} location={location} active={active && location.view === "harness"}
          mutationsAvailable={mutationsAvailable} sessionWritable={editor.sessionWritable} />
      </section>
    </div>
  </div>;
}
