import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { RoutingStatusBar } from "../RoutingStatusBar";
import type { Snapshot } from "../types";
import type { Editor } from "../use-editor";
import type { BuddyLocation } from "./navigation";

/**
 * The roles of the Buddy page: who holds the Router role, with that role's
 * settings and records. It still shows the earlier component.
 */
export function RolesView({ snapshot, editor, active, location, onNavigate, hidden }: {
  snapshot: Snapshot; editor: Editor;
  /** The page is in front and this is the view it shows. */
  active: boolean; location: BuddyLocation; onNavigate: (next: BuddyLocation) => void; hidden: boolean;
}) {
  const { t } = useTranslation("buddies");
  const handledLocation = useRef<BuddyLocation | null>(null);
  // An address that names a part of this view lands on it.
  useEffect(() => {
    if (!active || !location.target || handledLocation.current === location) return;
    const target = location.target === "health" ? document.querySelector<HTMLElement>(".routing-status[aria-label='路由健康']")
      : document.getElementById("router-current");
    target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
    handledLocation.current = location;
  }, [location, active]);
  return <section hidden={hidden} aria-label={t("sections.roles")} className="buddy-section-page flex-1 p-3 sm:px-6 sm:py-4">
    <div className="buddy-section">
      <RoutingStatusBar data={editor.view} snapshot={snapshot} editor={editor} expanded
        onShowRouter={profileId => onNavigate({ view: "models", target: profileId })} />
    </div>
  </section>;
}
