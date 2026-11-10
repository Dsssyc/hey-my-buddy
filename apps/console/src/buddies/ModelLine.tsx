import { ReasonMessages } from "../i18n/ReasonMessage";
import { BuddyName } from "../components/AliasTag";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { CollapsibleTrigger } from "@/components/ui/collapsible";
import { UnsavedMark } from "@/components/ui/state-mark";
import { concurrencyEntryFor, MODEL_SERVICE_CONCURRENCY_DEFAULT } from "../console-data";
import type { ConsoleView, Snapshot } from "../types";
import { modelIdentity, reasonTexts, pendingHint } from "./models";
import type { ModelRow } from "./models";

/** One full-width keyboard handle: the trigger of the model's drawer. Only enabled buddies appear on its second part. */
export function ModelLine({ row, harnessState, data, recorded, expanded, onSelect, showProvider }: {
  showProvider: boolean; row: ModelRow; harnessState: string | null; data: ConsoleView; recorded: Snapshot; expanded: boolean; onSelect: () => void;
}) {
  const { t } = useTranslation("buddies");
  const member = row.modelService.buddies[0];
  const enabled = row.chips.filter(chip => chip.buddy.enabled);
  const changed = row.chips.some(chip => chip.unsaved)
    || !!data.modelCorrespondenceChanges?.some(entry => entry.harness === member.harness && entry.provider === member.provider && entry.model === member.model)
    || !Object.is(row.limit, concurrencyEntryFor(recorded.modelServiceConcurrency, member)?.limit ?? MODEL_SERVICE_CONCURRENCY_DEFAULT);
  const modelId = data.modelCorrespondences.find(entry => entry.harness === member.harness && entry.provider === member.provider && entry.model === member.model)?.modelId;
  const noProfile = enabled.length > 0 && !data.models.find(model => model.modelId === modelId)?.profile;
  const occupancy = row.occupancy === null ? t("models.occupancyUnknown") : t("models.occupancy", { active: row.occupancy, limit: row.limit });
  return <div data-slot="model-row" className="border-t">
    <CollapsibleTrigger asChild>
      <button type="button" aria-label={modelIdentity(row.modelService)} aria-description={enabled.length > 0 ? t("models.enabled") : undefined} onClick={onSelect}
        className="grid min-h-10 w-full cursor-pointer grid-cols-[20px_minmax(0,1fr)_max-content] items-center gap-x-2 gap-y-1 px-3 py-2 text-left outline-none transition-colors duration-150 hover:bg-sunk focus-visible:ring-2 focus-visible:ring-ring aria-[expanded=true]:bg-past-wash motion-reduce:transition-none sm:grid-cols-[20px_minmax(0,1fr)_minmax(0,1.5fr)_max-content] sm:px-4">
        {enabled.length > 0 ? <Hint text={noProfile ? t("models.noProfile") : undefined}><span data-slot="usage-mark" aria-label={noProfile ? t("models.noProfile") : undefined} className={cn("mx-auto size-2 rounded-full", noProfile ? "border border-foreground" : "bg-foreground")} /></Hint> : <span className="mx-auto size-2" aria-hidden="true" />}
        <span className={cn("flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1", !enabled.length && "text-ink3")}>
          <span className="wrap-anywhere font-medium">{member.model}</span>
          {showProvider && <span data-slot="model-provider" className="text-xs text-ink3">{member.provider}</span>}
          {!row.available && !harnessState && <Hint text={row.reasons.length ? <ReasonMessages reasons={row.reasons} /> : undefined} description={reasonTexts(row.reasons, t).join(" · ") || undefined}>
            <span className="text-xs text-ink3">{t("models.unavailable")}</span>
          </Hint>}
          {row.pending && <Hint text={pendingHint(row, t)}><span className="text-xs text-ink3">{t("models.pending")}</span></Hint>}
          {row.fresh && <span className="border border-line2 px-1 text-xs text-ink2">{t("models.fresh")}</span>}
          {changed && !expanded && <UnsavedMark label={t("unsaved")} />}
        </span>
        <span className="col-start-2 row-start-2 flex min-h-4 min-w-0 flex-wrap items-center gap-x-6 gap-y-1 text-xs sm:col-start-auto sm:row-start-auto">
          {enabled.map(chip => <span key={chip.buddy.buddyId} data-slot="enabled-effort" className="inline-flex items-center gap-1.5">
            <BuddyName buddy={chip.buddy}><span>{chip.effort}</span></BuddyName>
            {chip.preference && <span className="text-ink2">{t(`preference.${chip.preference}`)}</span>}
          </span>)}
        </span>
        <span className="col-start-3 row-start-1 inline-flex items-center justify-end gap-2 whitespace-nowrap text-right text-xs text-ink2 tabular-nums sm:col-start-auto sm:row-start-auto" aria-label={occupancy}>
          {row.occupancy === null ? "—" : <>
            <span data-slot="model-occupancy-meter" aria-hidden="true" className="h-1.5 w-12 shrink-0 bg-line">
              {!Number.isNaN(row.limit) && row.limit > 0 && <span className="block h-full bg-ink2 transition-[width] duration-150 motion-reduce:transition-none" style={{ width: `${Math.min(100, row.occupancy / row.limit * 100)}%` }} />}
            </span>
            <span>{row.occupancy} / {Number.isNaN(row.limit) ? "—" : row.limit}</span>
          </>}
        </span>
      </button>
    </CollapsibleTrigger>
  </div>;
}
