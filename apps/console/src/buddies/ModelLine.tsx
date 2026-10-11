import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { UnsavedMark } from "@/components/ui/state-mark";
import { concurrencyEntryFor, familyKey, MODEL_CONCURRENCY_DEFAULT } from "../console-data";
import { familyAnnotationText } from "../draft";
import { formatDateTime } from "../i18n/format";
import type { ConsoleView, Snapshot } from "../types";
import { modelIdentity, reasonTexts } from "./models";
import type { ModelRow } from "./models";

/** One full-width keyboard handle. Only enabled buddies appear on its second part. */
export function ModelLine({ row, harnessState, data, recorded, expanded, onSelect }: {
  row: ModelRow; harnessState: string | null; data: ConsoleView; recorded: Snapshot; expanded: boolean; onSelect: () => void;
}) {
  const { t } = useTranslation("buddies");
  const member = row.family.profiles[0];
  const enabled = row.chips.filter(chip => chip.profile.enabled);
  const preference = data.familyPreferences.find(entry => familyKey(entry) === row.key);
  const published = recorded.familyPreferences.find(entry => familyKey(entry) === row.key);
  const changed = row.chips.some(chip => chip.unsaved)
    || preference?.mode !== published?.mode || (preference?.reason ?? "") !== (published?.reason ?? "")
    || familyAnnotationText(data, member) !== familyAnnotationText(recorded, member)
    || !Object.is(row.limit, concurrencyEntryFor(recorded.modelConcurrency, member)?.limit ?? MODEL_CONCURRENCY_DEFAULT);
  const occupancy = row.occupancy === null ? t("models.occupancyUnknown") : t("models.occupancy", { active: row.occupancy, limit: row.limit });
  return <div data-slot="model-row" className={cn("border-t", expanded && "border-l-2 border-l-live bg-live-wash")}>
    <button type="button" aria-label={modelIdentity(row.family)} aria-expanded={expanded} onClick={onSelect}
      className="grid min-h-10 w-full grid-cols-[16px_20px_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 px-3 py-2 text-left outline-none hover:bg-sunk focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset sm:grid-cols-[16px_20px_minmax(0,1fr)_minmax(0,1.5fr)_64px] sm:px-4">
      <span className="inline-flex size-4 items-center justify-center text-xs leading-none text-ink2" aria-hidden="true">{expanded ? "▼" : "▶"}</span>
      <span className={cn("mx-auto size-2 rounded-full", enabled.length > 0 && "bg-live")} aria-hidden="true" />
      <span className={cn("flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1", !enabled.length && "text-ink3")}>
        <span className="wrap-anywhere font-medium">{member.model}</span>
        {!row.available && !harnessState && <Hint text={reasonTexts(row.reasons, t).join(" · ") || undefined}>
          <span className="text-xs text-ink3">{t("models.unavailable")}</span>
        </Hint>}
        {row.pending && <Hint text={row.pendingSince ? t("models.pendingSince", { time: formatDateTime(Date.parse(row.pendingSince)) }) : undefined}>
          <span className="text-xs text-ink3">{t("models.pending")}</span>
        </Hint>}
        {row.fresh && <span className="border border-line2 px-1 text-xs text-ink2">{t("models.fresh")}</span>}
        {changed && !expanded && <UnsavedMark label={t("unsaved")} />}
      </span>
      <span className="col-start-3 row-start-2 flex min-h-4 min-w-0 flex-wrap items-center gap-x-6 gap-y-1 text-xs sm:col-start-auto sm:row-start-auto">
        {enabled.map(chip => <span key={chip.profile.profileId} data-slot="enabled-effort" className="inline-flex items-center gap-1.5">
          <span>{chip.effort}</span>
          {chip.preference && <span className="text-ink2">{t(`preference.${chip.preference}`)}</span>}
        </span>)}
      </span>
      <span className="col-start-4 row-start-1 whitespace-nowrap text-right text-xs text-ink2 tabular-nums sm:col-start-auto sm:row-start-auto" aria-label={occupancy}>
        {row.occupancy ?? "—"} / {Number.isNaN(row.limit) ? "—" : row.limit}
      </span>
    </button>
  </div>;
}
