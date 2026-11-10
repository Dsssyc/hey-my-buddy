import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { buddyAttention } from "./buddies/attention";
import { BuddyHeader } from "./buddies/BuddyHeader";
import { CatalogView } from "./buddies/CatalogView";
import { EvaluationView } from "./buddies/EvaluationView";
import { RouterView } from "./buddies/RouterView";
import { buddyHash, buddyLocation } from "./buddies/navigation";
import type { BuddyLocation } from "./buddies/navigation";

/**
 * The Buddy page: directory, Router and evaluation.
 * Visited evaluation content stays mounted so selections and profile notifications
 * survive a change of section.
 */
export function BuddyConfig({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [location, setLocation] = useState<BuddyLocation>(() => buddyLocation(window.location.hash) ?? { view: "models" });
  const [evaluationVisited, setEvaluationVisited] = useState(location.view === "evaluation");
  const attention = buddyAttention(editor.view, snapshot);
  useEffect(() => {
    const changed = () => {
      const next = buddyLocation(window.location.hash);
      if (next) { setLocation(next); if (next.view === "evaluation") setEvaluationVisited(true); }
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  function navigate(next: BuddyLocation) {
    if (next.view === "evaluation") setEvaluationVisited(true);
    setLocation(next);
    window.location.hash = buddyHash(next);
  }
  useEffect(() => {
    if (editor.aliasRefusal) navigate({ view: "models", targetKind: "buddy", target: editor.aliasRefusal.buddyId });
  }, [editor.aliasRefusal]);
  const shared = { snapshot, location, onNavigate: navigate };
  return <div className="flex h-full min-h-0 flex-col">
    <BuddyHeader location={location} attention={attention} onNavigate={navigate}
      harnessCount={new Set([...(snapshot.harnesses ?? []).map(row => row.harness), ...editor.view.buddies.map(buddy => buddy.harness)]).size}
      enabled={editor.view.buddies.filter(buddy => buddy.enabled).length} total={editor.view.buddies.length} />
    <CatalogView {...shared} editor={editor} api={api} refresh={refresh} mutationsAvailable={mutationsAvailable}
      active={active && location.view === "models"} hidden={location.view !== "models"} />
    <RouterView {...shared} attention={attention} editor={editor} api={api} refresh={refresh} mutationsAvailable={mutationsAvailable} active={active && location.view === "router"} hidden={location.view !== "router"} />
    {evaluationVisited && <EvaluationView {...shared} editor={editor} api={api} refresh={refresh} mutationsAvailable={mutationsAvailable} hidden={location.view !== "evaluation"} />}
  </div>;
}
