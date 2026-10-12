import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { buddyAttention } from "./buddies/attention";
import { BuddyHeader } from "./buddies/BuddyHeader";
import { CatalogView } from "./buddies/CatalogView";
import { RolesView } from "./buddies/RolesView";
import { buddyHash, buddyLocation } from "./buddies/navigation";
import type { BuddyLocation } from "./buddies/navigation";

/**
 * The Buddy page: one catalogue and the roles.
 * Both views stay mounted, so
 * that what is selected and what is typed survive a change of view.
 */
export function BuddyConfig({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [location, setLocation] = useState<BuddyLocation>(() => buddyLocation(window.location.hash) ?? { view: "models" });
  const attention = buddyAttention(editor.view, snapshot);
  useEffect(() => {
    const changed = () => {
      const next = buddyLocation(window.location.hash);
      if (next) setLocation(next);
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  function navigate(next: BuddyLocation) {
    setLocation(next);
    window.location.hash = buddyHash(next);
  }
  const shared = { snapshot, location, onNavigate: navigate };
  return <div className="flex h-full min-h-0 flex-col">
    {location.view === "roles" && <div className="flex min-h-12 border-b bg-card px-3 sm:px-6"><BuddyHeader location={location} attention={attention} onNavigate={navigate} /></div>}
    <CatalogView {...shared} editor={editor} api={api} refresh={refresh} attention={attention} mutationsAvailable={mutationsAvailable}
      active={active && location.view === "models"} hidden={location.view !== "models"} />
    <RolesView {...shared} editor={editor} active={active && location.view === "roles"} hidden={location.view !== "roles"} />
  </div>;
}
