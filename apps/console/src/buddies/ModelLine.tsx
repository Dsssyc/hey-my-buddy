import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { UnsavedMark } from "@/components/ui/state-mark";
import { concurrencyEntryFor, MODEL_SERVICE_CONCURRENCY_DEFAULT } from "../console-data";
import { modelServiceNoteText } from "../draft";
import { formatDateTime } from "../i18n/format";
import type { ConsoleView, Snapshot } from "../types";
import { modelIdentity, reasonTexts } from "./models";
import type { ModelRow } from "./models";

/** One full-width keyboard handle: the trigger of the model's drawer. Only enabled buddies appear on its second part. */
export function ModelLine({ row, harnessState, data, recorded, expanded, onSelect }: {
  row: ModelRow; harnessState: string | null; data: ConsoleView; recorded: Snapshot; expanded: boolean; onSelect: () => void;
}) {
  const { t } = useTranslation("buddies");
  const member = row.modelService.profiles[0];
  const enabled = row.chips.filter(chip => chip.profile.enabled);
  const changed = row.chips.some(chip => chip.unsaved)
    || modelServiceNoteText(data, member) !== modelServiceNoteText(recorded, member)
    || !Object.is(row.limit, concurrencyEntryFor(recorded.modelServiceConcurrency, member)?.limit ?? MODEL_SERVICE_CONCURRENCY_DEFAULT);
  const occupancy = row.occupancy === null ? t("models.occupancyUnknown") : t("models.occupancy", { active: row.occupancy, limit: row.limit });
  // The open row keeps its tint under the pointer: hover only deepens it.
  return <div data-slot="model-row" className={cn("border-t border-l-2 border-l-transparent transition-colors duration-150 motion-reduce:transition-none", expanded && "border-l-live bg-live-wash")}>
    <CollapsibleTrigger asChild>
      <button type="button" aria-label={modelIdentity(row.modelService)} onClick={onSelect}
        className="grid min-h-10 w-full grid-cols-[16px_20px_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 px-3 py-2 text-left outline-none transition-colors duration-150 hover:bg-sunk aria-expanded:hover:bg-live/15 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset motion-reduce:transition-none sm:grid-cols-[16px_20px_minmax(0,1fr)_minmax(0,1.5fr)_64px] sm:px-4">
        <Disclosure open={expanded} />
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
    </CollapsibleTrigger>
  </div>;
}
