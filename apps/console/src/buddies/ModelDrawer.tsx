import { cn } from "cn";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Input } from "@/components/ui/input";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { StateMark, UnsavedMark } from "@/components/ui/state-mark";
import { Switch } from "@/components/ui/switch";
import {
  MODEL_SERVICE_CONCURRENCY_DEFAULT,
  MODEL_SERVICE_CONCURRENCY_MAX,
  MODEL_SERVICE_CONCURRENCY_MIN,
  concurrencyEntryFor,
} from "../console-data";
import { modelServiceNoteText, setConcurrencyLimit, setModelServiceNote, setPreference } from "../draft";
import { formatDateTime } from "../i18n/format";
import type { ConsoleView, PreferenceMode, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { CoolingStatus } from "./cooling";
import { ConcurrencyNotice } from "./ConcurrencyNotice";
import { modelIdentity, reasonTexts } from "./models";
import type { BuddyChip, ModelRow } from "./models";

const MODES = ["prefer", "pin", "exclude"] as const satisfies readonly PreferenceMode[];
const field = "h-8 rounded-md border border-input bg-card px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-disabled:opacity-50 aria-invalid:border-destructive";
const choice = "h-8 px-2 text-xs";

/** The addressable panel for the selected buddy, below its reasoning-effort strip. */
export function buddyRowId(buddyId: string): string {
  return `buddy-${buddyId}`;
}

function BuddySettings({ chip, data, recorded, editor, modelAvailable }: {
  chip: BuddyChip; data: ConsoleView; recorded: Snapshot; editor: Editor; modelAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const editing = editor.editing;
  const id = useId();
  const { buddy, effort } = chip;
  const preference = data.preferences.find(entry => entry.buddyId === buddy.buddyId);
  const published = recorded.preferences.find(entry => entry.buddyId === buddy.buddyId);
  const baselineMode = (editor.baseline ?? recorded).preferences.find(entry => entry.buddyId === buddy.buddyId)?.mode;
  const pinLocked = baselineMode !== "pin" && (!buddy.available || !buddy.enabled);
  const editingLock = !editor.sessionWritable ? t("pane.locked.session") : !editing ? t("pane.locked.saving") : undefined;
  // A retired buddy can be switched off, but cannot be switched on again.
  const canToggle = editing && (buddy.available || buddy.enabled);
  const enableLock = editingLock ?? (!canToggle ? t("pane.locked.retired") : undefined);
  const enableUnsaved = recorded.buddies.find(entry => entry.buddyId === buddy.buddyId)?.enabled !== buddy.enabled;
  const preferenceUnsaved = preference?.mode !== published?.mode;
  const noteUnsaved = (preference?.note ?? "") !== (published?.note ?? "");
  const unavailableReason = reasonTexts(buddy.unavailableReason ? [buddy.unavailableReason] : [], t).join(" · ");
  const name = (text: string, unsaved: boolean, target: { id: string } | { htmlFor: string }) =>
    <span className="flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-ink3">
      <label {...target}>{text}</label>{unsaved && <UnsavedMark label={t("unsaved")} />}
    </span>;

  function setBuddyPreference(value: string) {
    if (!editing || value === "pin" && pinLocked) return;
    const mode = value as PreferenceMode | "";
    editor.update(draft => setPreference(draft, buddy.buddyId, mode, mode ? preference?.note ?? "" : ""));
  }

  return <div id={buddyRowId(buddy.buddyId)} role="group" aria-label={effort} tabIndex={-1} data-slot="buddy-panel"
    className="mt-4 flex-1 border border-line bg-card p-3 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2 text-xs">
      <span className="min-w-0 font-mono wrap-anywhere text-ink2">
        {buddy.harness} / {buddy.provider} / {buddy.model} / <strong className="font-semibold text-foreground">{effort}</strong>
      </span>
      <CoolingStatus providers={buddy.providerCooling ? [buddy.providerCooling] : []} />
      {modelAvailable && !buddy.available && <Hint text={unavailableReason}><span className="text-ink3">{t("pane.unavailable")}</span></Hint>}
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3 max-[600px]:flex-col max-[600px]:items-stretch">
      <div className="flex items-center gap-2.5 max-[600px]:justify-between">
        {name(t("models.enabled"), enableUnsaved, { htmlFor: `${id}-enabled` })}
        <Hint text={enableLock}><Switch id={`${id}-enabled`} checked={buddy.enabled} aria-label={t("pane.enable", { effort })}
          className="h-8 w-[34px] bg-transparent px-0.5 before:inset-x-0 before:inset-y-0 after:absolute after:inset-x-0.5 after:inset-y-2 after:rounded-full after:bg-line2 after:transition-colors after:duration-150 after:motion-reduce:transition-none aria-checked:bg-transparent aria-checked:after:bg-live [&_[data-slot=switch-thumb]]:relative [&_[data-slot=switch-thumb]]:z-10"
          aria-disabled={!canToggle || undefined} onCheckedChange={enabled => {
            if (canToggle) editor.update(draft => ({ ...draft, buddies: draft.buddies.map(entry => entry.buddyId === buddy.buddyId ? { ...entry, enabled } : entry) }));
          }} /></Hint>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2.5 max-[600px]:justify-between">
        {name(t("pane.preference"), preferenceUnsaved, { id: `${id}-preference` })}
        <Segmented value={preference?.mode ?? ""} aria-label={t("pane.buddyPreference", { effort })} onValueChange={setBuddyPreference}
          className="flex-wrap bg-card max-[600px]:max-w-full">
          <Hint text={editingLock}><SegmentedItem value="" aria-disabled={!editing || undefined} className={choice}>{t("preference.unset")}</SegmentedItem></Hint>
          {MODES.map(mode => <Hint key={mode} text={editingLock ?? (mode === "pin" && pinLocked ? t("pane.locked.pinBuddy") : t(`preferenceHint.${mode}`))}>
            <SegmentedItem value={mode} aria-disabled={!editing || mode === "pin" && pinLocked || undefined} className={choice}>{t(`preference.${mode}`)}</SegmentedItem>
          </Hint>)}
        </Segmented>
      </div>
      <div className="flex min-w-40 flex-1 items-center gap-2.5 max-[600px]:min-w-0">
        {name(t("pane.note"), noteUnsaved, { htmlFor: `${id}-note` })}
        <Hint text={editingLock ?? (!preference ? t("pane.noteNone") : undefined)}>
          <Input id={`${id}-note`} className="h-8 min-w-0 flex-1 bg-card text-sm shadow-none"
            aria-label={t("pane.buddyNote", { effort })} value={preference?.note ?? ""} readOnly={!editing || !preference}
            aria-disabled={!editing || !preference || undefined}
            onChange={event => {
              if (editing && preference) editor.update(draft => setPreference(draft, buddy.buddyId, preference.mode, event.target.value));
            }} />
        </Hint>
      </div>
    </div>
  </div>;
}

/** A model's shared settings and one selected buddy, unfolded below its row. */
export function ModelDrawer({ row, harnessState, data, recorded, editor, selectedBuddyId, onSelectBuddy }: {
  row: ModelRow; harnessState?: string; data: ConsoleView; recorded: Snapshot; editor: Editor;
  selectedBuddyId: string; onSelectBuddy: (id: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const editing = editor.editing;
  const id = useId();
  const { modelService, key } = row;
  const member = modelService.buddies[0];
  const selected = row.chips.find(chip => chip.buddy.buddyId === selectedBuddyId)!;
  const note = modelServiceNoteText(data, member);
  const publishedLimit = concurrencyEntryFor(recorded.modelServiceConcurrency, member)?.limit ?? MODEL_SERVICE_CONCURRENCY_DEFAULT;
  const limitInvalid = !Number.isInteger(row.limit) || row.limit < MODEL_SERVICE_CONCURRENCY_MIN || row.limit > MODEL_SERVICE_CONCURRENCY_MAX;
  const buddyIds = new Set(modelService.buddies.map(buddy => buddy.buddyId));
  const mine = (issue: { modelService?: string; buddyId: string }) => issue.modelService === key || (!issue.modelService && buddyIds.has(issue.buddyId));
  const blocking = editor.blocking.filter(mine);
  const attention = editor.attention.filter(issue => !issue.router && mine(issue));
  const state = !row.available ? "unavailable" : row.pending ? "pending" : "available";
  const reasons = [harnessState, ...reasonTexts(row.reasons, t, !!harnessState)].filter(Boolean).join(" · ");
  const editingLock = !editor.sessionWritable ? t("pane.locked.session") : !editing ? t("pane.locked.saving") : undefined;
  const name = (text: string, unsaved: boolean, target: { id: string } | { htmlFor: string }) =>
    <div className="flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-ink3">
      <label {...target}>{text}</label>{unsaved && <UnsavedMark label={t("unsaved")} />}
    </div>;

  return <section role="region" aria-label={modelIdentity(modelService)} data-slot="model-drawer"
    className="border-t border-l-2 border-t-line border-l-live bg-sunk p-4 min-[900px]:pl-14">
    {blocking.length > 0 && <p className="mb-3 border-l-2 border-fail pl-3 text-xs text-fail" role="alert">{blocking.map(issue => issue.message).join(" ")}</p>}
    {attention.length > 0 && <p className="mb-3 border-l-2 border-hand-mark pl-3 text-xs text-hand" role="status">{attention.map(issue => issue.message).join(" ")}</p>}
    <div className="grid grid-cols-[minmax(0,1fr)] gap-y-4 min-[900px]:grid-cols-[minmax(0,0.8fr)_minmax(0,1.7fr)]">
      <div className="flex min-w-0 flex-col min-[900px]:pr-7">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 text-xs text-ink2">
          <h3 className="text-xs font-medium text-ink2">{t("pane.model")}</h3>
          <Hint text={!row.available ? reasons : undefined}><span className="flex flex-wrap items-center gap-1.5">
            <StateMark kind={row.available ? "live" : "idle"} />{t(`pane.${state}`)}
            {row.pending && row.pendingSince && <time dateTime={row.pendingSince}>{formatDateTime(Date.parse(row.pendingSince))}</time>}
          </span></Hint>
        </div>
        <div className="grid flex-1 grid-cols-[max-content_minmax(0,1fr)] grid-rows-[auto_auto_minmax(64px,1fr)] items-center gap-x-3 gap-y-2.5">
          {name(t("pane.concurrency"), !Object.is(row.limit, publishedLimit), { htmlFor: `${id}-limit` })}
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            <Hint text={editingLock}><Input id={`${id}-limit`} type="number" inputMode="numeric" className="h-8 w-16 bg-card px-2.5 text-sm tabular-nums shadow-none"
              min={MODEL_SERVICE_CONCURRENCY_MIN} max={MODEL_SERVICE_CONCURRENCY_MAX} step={1} value={Number.isNaN(row.limit) ? "" : row.limit}
              readOnly={!editing} aria-disabled={!editing || undefined} aria-label={t("pane.concurrencyLimit")}
              aria-invalid={limitInvalid || undefined} aria-describedby={`${id}-occupancy ${id}-concurrency-notice`} onChange={event => {
                if (!editing) return;
                // Keep the typed draft value, including an empty or invalid limit.
                const value = event.target.value === "" ? NaN : Number(event.target.value);
                editor.update(draft => setConcurrencyLimit(draft, member, value));
              }} /></Hint>
            <span id={`${id}-occupancy`} className="text-xs text-ink3 tabular-nums">
              {row.occupancy === null ? t("pane.occupancyUnknown") : t("pane.occupancy", { n: row.occupancy })}
              {limitInvalid && <span className="ml-2 text-fail">{t("pane.range", { min: MODEL_SERVICE_CONCURRENCY_MIN, max: MODEL_SERVICE_CONCURRENCY_MAX })}</span>}
            </span>
          </div>
          <ConcurrencyNotice id={`${id}-concurrency-notice`} />
          <div className="self-start pt-1.5">{name(t("pane.note"), note !== modelServiceNoteText(recorded, member), { htmlFor: `${id}-note` })}</div>
          <Hint text={editingLock}><textarea id={`${id}-note`} rows={3} maxLength={4000} value={note}
            readOnly={!editing} aria-disabled={!editing || undefined} className={cn(field, "h-full min-h-16 w-full resize-y self-stretch py-1.5")}
            onChange={event => {
              if (editing) editor.update(draft => setModelServiceNote(draft, member, event.target.value));
            }} /></Hint>
        </div>
      </div>
      <div className="flex min-w-0 flex-col border-t border-line pt-4 min-[900px]:border-t-0 min-[900px]:border-l min-[900px]:pt-0 min-[900px]:pl-7">
        <div className="mb-3 flex flex-wrap items-baseline gap-3 text-xs">
          <h3 className="text-xs font-medium text-ink2">{t("models.columns.buddy")}</h3>
          <span className="text-ink3 tabular-nums">{row.chips.length} · {t("models.enabled")} {row.chips.filter(chip => chip.buddy.enabled).length}</span>
        </div>
        <div role="group" aria-label={t("models.columns.effort")}
          className="grid auto-cols-[minmax(0,1fr)] grid-flow-col border border-line2 bg-sunk max-[600px]:grid-flow-row max-[600px]:grid-cols-3">
          {row.chips.map(chip => <div key={chip.buddy.buddyId}
            className="relative h-12 min-w-0 border-l border-line first:border-l-0 max-[600px]:border-t max-[600px]:nth-[-n+3]:border-t-0 max-[600px]:nth-[3n+1]:border-l-0">
            <button type="button" aria-pressed={chip.buddy.buddyId === selectedBuddyId} data-slot="buddy-stop"
            aria-label={chip.effort} onClick={() => onSelectBuddy(chip.buddy.buddyId)}
            className={cn("peer relative flex h-full w-full min-w-0 flex-col justify-center gap-0.5 px-3 text-left text-xs text-ink3 outline-none transition-[color,background-color,box-shadow] duration-150 hover:bg-foreground/5 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset motion-reduce:transition-none aria-pressed:z-10 aria-pressed:ring-[1.5px] aria-pressed:ring-foreground aria-pressed:ring-inset",
              chip.buddy.enabled && "bg-card font-medium text-foreground before:absolute before:-top-px before:right-0 before:left-0 before:h-[3px] before:bg-live")}>
            <span>{chip.effort}</span>
            <span className="min-h-4 font-normal text-ink2">{chip.buddy.enabled ? chip.preference ? t(`preference.${chip.preference}`) : t("models.enabled") : ""}</span>
            {chip.unsaved && <UnsavedMark label={t("unsaved")} className="absolute top-1.5 right-1.5" />}
            </button>
            <span aria-hidden="true" data-slot="buddy-pointer"
              className="pointer-events-none absolute -bottom-1.5 left-1/2 z-10 size-2.5 -translate-x-1/2 rotate-45 border-r border-b border-foreground bg-card opacity-0 transition-opacity duration-150 peer-aria-pressed:opacity-100 motion-reduce:transition-none max-[600px]:hidden" />
          </div>)}
        </div>
        <BuddySettings chip={selected} data={data} recorded={recorded} editor={editor} modelAvailable={row.available} />
      </div>
    </div>
  </section>;
}
