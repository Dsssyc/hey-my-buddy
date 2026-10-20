import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { UnsavedMark } from "@/components/ui/state-mark";
import { concurrencyEntryFor, MODEL_SERVICE_CONCURRENCY_DEFAULT } from "../console-data";
import { modelServiceNoteText } from "../draft";
import { formatDateTime } from "../i18n/format";
import type { ConsoleView, Snapshot } from "../types";
import { CoolingStatus } from "./cooling";
import { modelIdentity, reasonTexts } from "./models";
import type { ModelRow } from "./models";

/** One full-width keyboard handle: the trigger of the model's drawer. Only enabled buddies appear on its second part. */
export function ModelLine({ row, harnessState, data, recorded, expanded, onSelect }: {
  row: ModelRow; harnessState: string | null; data: ConsoleView; recorded: Snapshot; expanded: boolean; onSelect: () => void;
}) {
  const { t } = useTranslation("buddies");
  const member = row.modelService.buddies[0];
  const enabled = row.chips.filter(chip => chip.buddy.enabled);
  const changed = row.chips.some(chip => chip.unsaved)
    || modelServiceNoteText(data, member) !== modelServiceNoteText(recorded, member)
    || !Object.is(row.limit, concurrencyEntryFor(recorded.modelServiceConcurrency, member)?.limit ?? MODEL_SERVICE_CONCURRENCY_DEFAULT);
  const occupancy = row.occupancy === null ? t("models.occupancyUnknown") : t("models.occupancy", { active: row.occupancy, limit: row.limit });
  // The open row keeps its neutral tint under the pointer: hover deepens it.
  return <div data-slot="model-row" className={cn("border-t border-l-2 border-l-transparent transition-colors duration-150 motion-reduce:transition-none", expanded && "border-l-foreground bg-selection")}>
    <CollapsibleTrigger asChild>
      <button type="button" aria-label={modelIdentity(row.modelService)} aria-description={enabled.length > 0 ? t("models.enabled") : undefined} onClick={onSelect}
        className="disclosure-trigger grid min-h-10 w-full grid-cols-[16px_20px_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 px-3 py-2 text-left motion-reduce:transition-none sm:grid-cols-[16px_20px_minmax(0,1fr)_minmax(0,1.5fr)_64px] sm:px-4">
        <Disclosure open={expanded} />
        {enabled.length > 0 ? <Hint text={t("models.enabled")}><span className="mx-auto size-2 rounded-full bg-foreground" aria-hidden="true" /></Hint> : <span className="mx-auto size-2" aria-hidden="true" />}
        <span className={cn("flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1", !enabled.length && "text-ink3")}>
          <span className="wrap-anywhere font-medium">{member.model}</span>
          {!row.available && !harnessState && <Hint text={reasonTexts(row.reasons, t).join(" · ") || undefined}>
            <span className="text-xs text-ink3">{t("models.unavailable")}</span>
          </Hint>}
          <CoolingStatus providers={member.providerCooling ? [member.providerCooling] : []} />
          {row.pending && <Hint text={row.pendingSince ? t("models.pendingSince", { time: formatDateTime(Date.parse(row.pendingSince)) }) : undefined}>
            <span className="text-xs text-ink3">{t("models.pending")}</span>
          </Hint>}
          {row.fresh && <span className="border border-line2 px-1 text-xs text-ink2">{t("models.fresh")}</span>}
          {changed && !expanded && <UnsavedMark label={t("unsaved")} />}
        </span>
        <span className="col-start-3 row-start-2 flex min-h-4 min-w-0 flex-wrap items-center gap-x-6 gap-y-1 text-xs sm:col-start-auto sm:row-start-auto">
          {enabled.map(chip => <span key={chip.buddy.buddyId} data-slot="enabled-effort" className="inline-flex items-center gap-1.5">
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
