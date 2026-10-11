import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "../api";
import { HarnessStatus } from "../HarnessStatus";
import type { Snapshot } from "../types";
import type { BuddyAttention } from "./attention";
import { CatalogPick } from "./BuddyHeader";
import type { BuddyLocation } from "./navigation";

/**
 * The harness table of the catalogue. It still shows the earlier component,
 * under the choice between the catalogue's two tables.
 */
export function HarnessView({ snapshot, api, refresh, active, mutationsAvailable, sessionWritable, location, attention, onNavigate, hidden }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  /** The page is in front and this is the view it shows. */
  active: boolean; mutationsAvailable: boolean; sessionWritable: boolean;
  location: BuddyLocation; attention: BuddyAttention; onNavigate: (next: BuddyLocation) => void; hidden: boolean;
}) {
  const { t } = useTranslation("buddies");
  return <section hidden={hidden} aria-label={t("catalog.harness")} className="buddy-section-page flex-1 p-3 sm:px-6 sm:py-4">
    <div className="flex"><CatalogPick location={location} attention={attention} onNavigate={onNavigate} /></div>
    <div className="buddy-section">
      <HarnessStatus snapshot={snapshot} api={api} refresh={refresh} location={location} active={active}
        mutationsAvailable={mutationsAvailable} sessionWritable={sessionWritable} />
    </div>
  </section>;
}
