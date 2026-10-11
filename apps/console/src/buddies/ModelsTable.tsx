import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Badge } from "@/components/ui/badge";
import { UnsavedMark } from "@/components/ui/state-mark";
import { formatDateTime } from "../i18n/format";
import { harnessStateText, modelIdentity, reasonTexts } from "./models";
import type { BuddyChip, HarnessBlock, ModelRow } from "./models";
import { tableCell, tableHandle, tableHead, tableRow } from "./styles";

const cell = tableCell, handle = tableHandle;
const part = "whitespace-nowrap before:mr-3.5 before:text-line2 before:content-['/']";

/** One buddy: on or off at a glance, with the preference in effect written on it. */
function Chip({ chip }: { chip: BuddyChip }) {
  const { t } = useTranslation("buddies");
  const { enabled, available } = chip.profile;
  return <span data-slot="buddy-chip" className={cn("inline-flex h-6 items-center gap-1.5 rounded-sm border px-[9px] font-mono text-xs",
    enabled ? "border-past text-foreground" : "text-ink3", !available && "border-dashed")}>
    <i className={cn("size-1.5 rounded-full border", enabled ? "border-live bg-live" : "border-ink3")} aria-hidden="true" />
    {chip.effort}
    <span className="sr-only">{t(enabled ? "models.enabledState" : "models.disabledState")}{available ? "" : ` ${t("models.unavailable")}`}</span>
    {chip.preference && <span className="font-sans text-ink2">{t(`preference.${chip.preference}`)}</span>}
    {chip.unsaved && <UnsavedMark label={t("unsaved")} />}
  </span>;
}

/**
 * The first cell of a harness's first row: the handle that folds its rows away,
 * its name, and its state when that state makes its buddies unavailable.
 */
function HarnessHandle({ block, onFold }: { block: HarnessBlock; onFold: (adapter: string, folded: boolean) => void }) {
  const { t } = useTranslation("buddies");
  const button = <button type="button" className={handle} aria-expanded={!block.folded}
    aria-label={t(block.folded ? "models.unfold" : "models.fold", { name: block.name })}
    onClick={event => { event.stopPropagation(); onFold(block.adapter, !block.folded); }}>
    <span className="inline-block w-4 text-[9px] text-ink3" aria-hidden="true">{block.folded ? "▶" : "▼"}</span>
    <b className="font-medium">{block.name}</b>
  </button>;
  return <>
    {button}
    {block.state && <Hint text={reasonTexts(block.reasons, t, true).join(" · ") || undefined}>
      <span className="ml-2.5 text-xs text-ink3">{harnessStateText(block.state, t)}</span>
    </Hint>}
  </>;
}

function Row({ block, index, selected, onSelect, onFold }: {
  block: HarnessBlock; index: number; selected: boolean;
  onSelect: (key: string) => void; onFold: (adapter: string, folded: boolean) => void;
}) {
  const { t } = useTranslation("buddies");
  const model: ModelRow = block.models[index];
  const first = index === 0;
  const sameProvider = index > 0 && block.models[index - 1].family.provider === model.family.provider;
  const occupancy = model.occupancy === null ? t("models.occupancyUnknown")
    : t("models.occupancy", { active: model.occupancy, limit: model.limit });
  return <tr data-selected={selected} onClick={() => onSelect(model.key)}
    className={cn(tableRow, !model.available && "text-ink3")}>
    <td className={cn(cell, "whitespace-nowrap pl-5", !first && "text-ink3")}>
      {first ? <HarnessHandle block={block} onFold={onFold} /> : <><span className="inline-block w-4" />{block.name}</>}
    </td>
    <td className={cn(cell, part, sameProvider ? "text-ink3" : "font-medium")}>{model.family.provider}</td>
    <td className={cn(cell, part)}>
      <button type="button" className={cn(handle, "font-medium")} aria-pressed={selected} aria-label={modelIdentity(model.family)}>
        {model.family.profiles[0].model}
      </button>
      {/* A harness that cannot work says so once, on its own row. */}
      {!model.available && !block.state && <Hint text={reasonTexts(model.reasons, t).join(" · ") || undefined}>
        <span className="ml-2.5 text-xs">{t("models.unavailable")}</span>
      </Hint>}
      {model.pending && <Hint text={model.pendingSince ? t("models.pendingSince", { time: formatDateTime(Date.parse(model.pendingSince)) }) : undefined}>
        <span className="ml-2.5 text-xs text-ink3">{t("models.pending")}</span>
      </Hint>}
      {model.fresh && <Badge className="ml-2.5 align-middle">{t("models.fresh")}</Badge>}
    </td>
    <td className={cn(cell, "border-r")}>
      <div className="flex">
        <span className="mr-3.5 flex h-7 items-center text-line2" aria-hidden="true">/</span>
        <div className="flex flex-wrap gap-x-1.5 gap-y-1 py-0.5">{model.chips.map(chip => <Chip key={chip.profile.profileId} chip={chip} />)}</div>
      </div>
    </td>
    <td className={cn(cell, "whitespace-nowrap pr-5 text-ink2 tabular-nums")} aria-label={occupancy}>
      {model.occupancy ?? "—"} / {Number.isNaN(model.limit) ? "—" : model.limit}
    </td>
  </tr>;
}

/** A folded harness is one row: how many models it has and how many of its buddies are on. */
function FoldedRow({ block, onFold }: { block: HarnessBlock; onFold: (adapter: string, folded: boolean) => void }) {
  const { t } = useTranslation("buddies");
  return <tr className={cn("bg-sunk", block.state && "text-ink3")}>
    <td className={cn(cell, "whitespace-nowrap pl-5")}><HarnessHandle block={block} onFold={onFold} /></td>
    <td className={cn(cell, part, "text-ink3")}>
      {block.providers.length === 1 ? block.providers[0] : t("models.providerCount", { n: block.providers.length })}
    </td>
    <td className={cn(cell, part, "text-ink3 tabular-nums")}>{t("models.modelCount", { n: block.models.length })}</td>
    <td className={cn(cell, part, "border-r text-ink3 tabular-nums")}>{t("models.enabled")} {block.enabled} / {block.total}</td>
    <td className={cell} />
  </tr>;
}

/**
 * The models table. A row is one model; its first three cells and each chip in
 * the fourth make up the four parts of a buddy, so the slashes between the
 * cells read as one identity. The selected row is the one the side column
 * edits; it is tinted where the two stand side by side.
 */
export function ModelsTable({ blocks, current, onSelect, onFold }: {
  blocks: HarnessBlock[]; current: string | null;
  onSelect: (key: string) => void; onFold: (adapter: string, folded: boolean) => void;
}) {
  const { t } = useTranslation("buddies");
  const head = tableHead;
  return <table className="w-full min-w-[720px] border-separate border-spacing-0" aria-label={t("models.table")}>
    <thead className="sticky top-0 z-10 bg-card">
      <tr>
        <th scope="colgroup" colSpan={4} className="px-3 py-3.5 pl-9 text-left font-medium">{t("models.columns.buddy")}</th>
        <td />
      </tr>
      <tr>
        <th scope="col" className={cn(head, "w-[176px] pl-9")}>{t("models.columns.harness")}</th>
        <th scope="col" className={cn(head, "w-[150px]")}>{t("models.columns.provider")}</th>
        <th scope="col" className={cn(head, "w-[152px]")}>{t("models.columns.model")}</th>
        <th scope="col" className={cn(head, "border-r")}>{t("models.columns.effort")}</th>
        <th scope="col" className={cn(head, "w-[72px] pr-5")}>{t("models.columns.concurrency")}</th>
      </tr>
    </thead>
    {blocks.map(block => <tbody key={block.adapter}>
      {block.folded ? <FoldedRow block={block} onFold={onFold} />
        : block.models.map((model, index) => <Row key={model.key} block={block} index={index}
          selected={model.key === current} onSelect={onSelect} onFold={onFold} />)}
    </tbody>)}
  </table>;
}
