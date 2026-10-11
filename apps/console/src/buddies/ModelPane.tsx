import { cn } from "cn";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { StateMark, UnsavedMark } from "@/components/ui/state-mark";
import { Switch } from "@/components/ui/switch";
import {
  MODEL_CONCURRENCY_DEFAULT,
  MODEL_CONCURRENCY_MAX,
  MODEL_CONCURRENCY_MIN,
  concurrencyEntryFor,
  familyKey,
  recordedSampleCount,
} from "../console-data";
import { familyAnnotationText, setConcurrencyLimit, setFamilyAnnotation, setFamilyPreference, setPreferenceOverride } from "../draft";
import { formatDateTime } from "../i18n/format";
import type { ConsoleView, OverrideMode, PreferenceMode, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { modelIdentity, reasonTexts } from "./models";
import type { BuddyChip, ModelRow } from "./models";

const MODES = ["prefer", "pin", "exclude"] as const satisfies readonly PreferenceMode[];
const field = "h-8 rounded-md border border-input bg-card px-2.5 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 aria-invalid:border-destructive";
const blockHead = "flex items-center justify-between border-t px-5 pt-4 pb-1.5 text-xs text-ink3";

/** The DOM id of one buddy's row in the side column, so that an address can land on it. */
export function buddyRowId(profileId: string): string {
  return `buddy-${profileId}`;
}

/** One level: its switch, its own preference where it departs from the model's, and its samples. */
function EffortRow({ chip, data, recorded, editor, alone }: {
  chip: BuddyChip; data: ConsoleView; recorded: Snapshot; editor: Editor;
  /** This buddy cannot work while others of its model can, so it says so itself. */
  alone: boolean;
}) {
  const { t } = useTranslation("buddies");
  const { profile, effort } = chip;
  const override = data.preferenceOverrides.find(entry => entry.profileId === profile.profileId);
  // A buddy that is gone can still be switched off, never on again.
  const canToggle = editor.editing && (profile.available || profile.enabled);
  const locked = !editor.sessionWritable ? t("pane.locked.session")
    : !editor.editing ? t("pane.locked.saving")
      : !canToggle ? t("pane.locked.retired") : undefined;
  // Pinning is offered to a buddy that can work; one pinned before stays editable.
  const baselineMode = (editor.baseline ?? recorded).preferenceOverrides.find(entry => entry.profileId === profile.profileId)?.mode;
  const pinLocked = baselineMode !== "pin" && (!profile.available || !profile.enabled);
  function setOverride(mode: OverrideMode | "") {
    editor.update(draft => setPreferenceOverride(draft, profile.profileId, mode, mode ? override?.reason ?? "" : ""));
  }
  return <div id={buddyRowId(profile.profileId)} tabIndex={-1} role="group" aria-label={effort}
    className="grid grid-cols-[34px_64px_1fr_auto] items-center gap-x-2.5 gap-y-1.5 px-5 py-[7px] outline-none focus-visible:bg-sunk">
    <Hint text={locked}>
      <Switch checked={profile.enabled} aria-label={t("pane.enable", { effort })} aria-disabled={!canToggle || undefined}
        onCheckedChange={enabled => {
          if (canToggle) editor.update(draft => ({ ...draft, profiles: draft.profiles.map(entry => entry.profileId === profile.profileId ? { ...entry, enabled } : entry) }));
        }} />
    </Hint>
    <span className="flex items-center gap-1.5 font-mono text-xs">{effort}{chip.unsaved && <UnsavedMark label={t("unsaved")} />}</span>
    <Select value={override?.mode ?? "follow"} disabled={!editor.editing}
      onValueChange={value => setOverride(value === "follow" ? "" : value as OverrideMode)}>
      <SelectTrigger size="sm" aria-label={t("pane.override", { effort })} className="w-full bg-card px-2 text-xs shadow-none data-[size=sm]:h-7"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectItem value="follow">{t("pane.follow")}</SelectItem>
        <SelectItem value="none">{t("preference.none")}</SelectItem>
        {MODES.map(mode => <SelectItem key={mode} value={mode} disabled={mode === "pin" && pinLocked}>
          {t(`preference.${mode}`)}{mode === "pin" && pinLocked && <span className="text-xs text-ink3">{t("pane.locked.pinShort")}</span>}
        </SelectItem>)}
      </SelectContent>
    </Select>
    <span className="text-xs text-ink2 tabular-nums">{recordedSampleCount(data, profile.profileId)}</span>
    {override && <input className={cn(field, "col-span-3 col-start-2 h-7 text-xs")} aria-label={t("pane.overrideReason", { effort })}
      placeholder={t("pane.reason")} maxLength={500} value={override.reason} readOnly={!editor.editing}
      onChange={event => editor.update(draft => setPreferenceOverride(draft, profile.profileId, override.mode, event.target.value))} />}
    {alone && <p className="col-span-3 col-start-2 text-xs text-ink3">
      {[t("pane.unavailable"), ...reasonTexts(profile.unavailableReason?.trim() ? [profile.unavailableReason.trim()] : [], t)].join(" · ")}</p>}
  </div>;
}

/**
 * The side column: the small settings of the selected row. Its levels come
 * first, each a buddy of its own; below them is what all of them share as one
 * model. Every control edits the page's draft, which the save bar publishes.
 */
export function ModelPane({ row, harnessState, data, recorded, editor, onBack, className }: {
  row: ModelRow;
  /** The words for the harness's state when that state is why this model cannot work. */
  harnessState?: string;
  /** The view being edited, with the retained rows that were read. */
  data: ConsoleView;
  /** What is published, with the same retained rows. */
  recorded: Snapshot;
  editor: Editor;
  onBack: () => void;
  className?: string;
}) {
  const { t } = useTranslation("buddies");
  const id = useId();
  const { family, key } = row;
  const member = family.profiles[0];
  const preference = data.familyPreferences.find(entry => familyKey(entry) === key);
  const published = recorded.familyPreferences.find(entry => familyKey(entry) === key);
  const baselineMode = editor.baseline?.familyPreferences.find(entry => familyKey(entry) === key)?.mode ?? published?.mode;
  const pinLocked = baselineMode !== "pin" && !family.profiles.some(profile => profile.available && profile.enabled);
  const preferenceUnsaved = preference?.mode !== published?.mode || (preference?.reason ?? "") !== (published?.reason ?? "");
  const note = familyAnnotationText(data, member);
  const publishedLimit = concurrencyEntryFor(recorded.modelConcurrency, member)?.limit ?? MODEL_CONCURRENCY_DEFAULT;
  const limitInvalid = !Number.isInteger(row.limit) || row.limit < MODEL_CONCURRENCY_MIN || row.limit > MODEL_CONCURRENCY_MAX;
  const ids = new Set(family.profiles.map(profile => profile.profileId));
  const mine = (issue: { family?: string; profileId: string }) => issue.family === key || (!issue.family && ids.has(issue.profileId));
  const blocking = editor.blocking.filter(mine);
  // What concerns the Router is said in the roles section.
  const attention = editor.attention.filter(issue => !issue.router && mine(issue));
  const state = !row.available ? "unavailable" : row.pending ? "pending" : "available";
  // A field's name, with the mark of a change that is not saved yet.
  const name = (text: string, unsaved: boolean, target: { id: string } | { htmlFor: string }) =>
    <div className="flex min-w-[76px] shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-ink3">
      <label {...target}>{text}</label>{unsaved && <UnsavedMark label={t("unsaved")} />}
    </div>;

  return <aside aria-labelledby={`${id}-title`} className={className}>
    <div className="px-5 pt-[18px] pb-3.5">
      <Button variant="outline" size="sm" className="mb-3 xl:hidden" onClick={onBack}><span aria-hidden="true">‹</span>{t("pane.back")}</Button>
      <h2 id={`${id}-title`} className="font-mono text-xs break-all" aria-label={modelIdentity(family)}>
        <span className="text-ink3">{family.adapter} / {family.provider} /</span><br />
        <span className="text-sm">{member.model}</span>
      </h2>
      {blocking.length > 0 && <p className="mt-3 border-l-2 border-fail pl-3 text-xs text-fail" role="alert">{blocking.map(issue => issue.message).join(" ")}</p>}
      {attention.length > 0 && <p className="mt-3 border-l-2 border-hand-mark pl-3 text-xs text-hand" role="status">{attention.map(issue => issue.message).join(" ")}</p>}
    </div>

    <div className={blockHead}><b className="font-medium text-foreground">{t("pane.efforts")}</b><span>{t("pane.samples")}</span></div>
    {row.chips.map(chip => <EffortRow key={chip.profile.profileId} chip={chip} data={data} recorded={recorded} editor={editor}
      alone={row.available && !chip.profile.available} />)}

    <div className={`${blockHead} mt-2.5`}>
      <b className="font-medium text-foreground">{t("pane.model")}</b>
      <span className="flex items-center gap-1.5">
        <StateMark kind={row.available ? "live" : "idle"} />{t(`pane.${state}`)}
        {row.pending && row.pendingSince && <time dateTime={row.pendingSince}>{formatDateTime(Date.parse(row.pendingSince))}</time>}
      </span>
    </div>
    {!row.available && (harnessState || row.reasons.length > 0) && <p className="px-5 pb-1.5 text-xs text-ink3">
      {[harnessState, ...reasonTexts(row.reasons, t, !!harnessState)].filter(Boolean).join(" · ")}</p>}
    <div className="grid grid-cols-[76px_1fr] items-center gap-x-3 gap-y-2 px-5 pt-1 pb-4">
      {/* The four choices, with the reason under them, go below their name where the two do not fit on one line. */}
      <div className="col-span-2 flex flex-wrap items-start gap-x-3 gap-y-2">
      <div className="flex h-8 items-center">{name(t("pane.defaultPreference"), preferenceUnsaved, { id: `${id}-preference` })}</div>
      <div className="flex min-w-max flex-1 flex-col gap-2">
      <Segmented aria-labelledby={`${id}-preference`} value={preference?.mode ?? "none"} disabled={!editor.editing}
        onValueChange={value => {
          if (value === "pin" && pinLocked) return;
          const mode = value === "none" ? "" : value as PreferenceMode;
          editor.update(draft => setFamilyPreference(draft, member, mode, mode ? preference?.reason ?? "" : ""));
        }}>
        <SegmentedItem value="none">{t("preference.none")}</SegmentedItem>
        {MODES.map(mode => <Hint key={mode} text={mode === "pin" && pinLocked ? t("pane.locked.pinModel") : t(`preferenceHint.${mode}`)}>
          <SegmentedItem value={mode} aria-disabled={mode === "pin" && pinLocked || undefined}>{t(`preference.${mode}`)}</SegmentedItem>
        </Hint>)}
      </Segmented>
      {preference && <input className={cn(field, "text-xs")} aria-label={t("pane.defaultReason")} placeholder={t("pane.reason")}
        maxLength={500} value={preference.reason} readOnly={!editor.editing}
        onChange={event => editor.update(draft => setFamilyPreference(draft, member, preference.mode, event.target.value))} />}
      </div>
      </div>

      {name(t("pane.concurrency"), !Object.is(row.limit, publishedLimit), { htmlFor: `${id}-limit` })}
      <div className="flex items-center gap-2.5">
        <Input id={`${id}-limit`} type="number" inputMode="numeric" className="h-8 w-16 bg-card px-2.5 tabular-nums shadow-none"
          min={MODEL_CONCURRENCY_MIN} max={MODEL_CONCURRENCY_MAX} step={1} value={Number.isNaN(row.limit) ? "" : row.limit}
          disabled={!editor.editing} aria-label={t("pane.concurrencyLimit")} aria-invalid={limitInvalid || undefined} aria-describedby={`${id}-occupancy`}
          onChange={event => {
            // What is typed stays in the draft even when it is not a limit, so a save cannot publish an earlier valid prefix.
            const value = event.target.value === "" ? NaN : Number(event.target.value);
            editor.update(draft => setConcurrencyLimit(draft, member, value));
          }} />
        <span id={`${id}-occupancy`} className="text-xs text-ink3 tabular-nums">
          {row.occupancy === null ? t("pane.occupancyUnknown") : t("pane.occupancy", { n: row.occupancy })}
          {limitInvalid && <span className="ml-2.5 text-fail">{t("pane.range", { min: MODEL_CONCURRENCY_MIN, max: MODEL_CONCURRENCY_MAX })}</span>}
        </span>
      </div>

      {name(t("pane.note"), note !== familyAnnotationText(recorded, member), { htmlFor: `${id}-note` })}
      <textarea id={`${id}-note`} rows={1} maxLength={4000} value={note} readOnly={!editor.editing}
        className={cn(field, "field-sizing-content h-auto max-h-40 min-h-8 w-full resize-none py-[5px]")}
        onChange={event => editor.update(draft => setFamilyAnnotation(draft, member, event.target.value))} />
    </div>
  </aside>;
}
