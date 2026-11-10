import { AliasTag } from "../components/AliasTag";
import { reasonText } from "../i18n/reason";
import { ReasonMessage, ReasonMessages } from "../i18n/ReasonMessage";
import { Joined } from "../i18n/Message";
import { cn } from "cn";
import { useId, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { StateMark, UnsavedMark } from "@/components/ui/state-mark";
import { Switch } from "@/components/ui/switch";
import {
  MODEL_SERVICE_CONCURRENCY_DEFAULT,
  MODEL_SERVICE_CONCURRENCY_MAX,
  MODEL_SERVICE_CONCURRENCY_MIN,
  concurrencyEntryFor,
  concurrencyLimit,
} from "../console-data";
import { setConcurrencyLimit, setPreference } from "../draft";
import { formatDateTime } from "../i18n/format";
import type { ConsoleView, PreferenceMode, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { modelServiceKey } from "../console-data";
import { buddyHash } from "./navigation";
import type { BuddyLocation } from "./navigation";
import { ConcurrencyNotice } from "./ConcurrencyNotice";
import { modelIdentity, reasonTexts, pendingHint } from "./models";
import type { BuddyChip, ModelRow } from "./models";

const MODES = ["prefer", "pin", "exclude"] as const satisfies readonly PreferenceMode[];
const choice = "h-8 px-2 text-xs";

/** The addressable panel for the selected buddy, beside its reasoning-effort list. */
export function buddyRowId(buddyId: string): string {
  return `buddy-${buddyId}`;
}

function BuddySettings({ chip, data, recorded, editor, modelAvailable, onNavigate }: {
  chip: BuddyChip; data: ConsoleView; recorded: Snapshot; editor: Editor; modelAvailable: boolean; onNavigate: (location: BuddyLocation) => void;
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
  const preferenceUnsaved = preference?.mode !== published?.mode;
  const aliasUnsaved = buddy.alias !== recorded.buddies.find(entry => entry.buddyId === buddy.buddyId)?.alias;
  const aliasError = editor.aliasRefusal?.buddyId === buddy.buddyId ? editor.aliasRefusal.reason : null;
  const noteUnsaved = (preference?.note ?? "") !== (published?.note ?? "");
  const modelId = data.modelCorrespondences.find(entry => modelServiceKey(entry) === modelServiceKey(buddy))?.modelId;
  const evaluation = data.kindEvaluations?.find(entry => entry.harness === buddy.harness && entry.modelId === modelId && entry.effort === buddy.effort);
  const unavailableReason = buddy.unavailableReason ? <ReasonMessage reason={buddy.unavailableReason} /> : null;
  const name = (text: string, unsaved: boolean, target: { id: string } | { htmlFor: string }) =>
    <span className="col-start-1 flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-ink3">
      <label {...target}>{text}</label>{unsaved && <UnsavedMark label={t("unsaved")} />}
    </span>;

  function setBuddyPreference(value: string) {
    if (!editing || value === "pin" && pinLocked) return;
    const mode = value as PreferenceMode | "";
    editor.update(draft => setPreference(draft, buddy.buddyId, mode, mode ? preference?.note ?? "" : ""));
  }

  return <div id={buddyRowId(buddy.buddyId)} role="group" aria-label={effort} tabIndex={-1} data-slot="buddy-panel"
    className="min-w-0 border-t border-line pt-3 min-[900px]:border-t-0 min-[900px]:border-l min-[900px]:pt-0 min-[900px]:pl-7 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
    <div className="grid grid-cols-[max-content_minmax(0,max-content)_minmax(0,1fr)] items-center gap-x-4 gap-y-3">
      <span className="flex h-5 items-center text-xs text-ink3">{t("pane.samples")}</span><span className="col-span-2 flex min-h-5 flex-wrap items-center gap-3 text-sm"><span data-slot="buddy-samples" className="tabular-nums">{modelId ? <a className="underline decoration-line2 underline-offset-4 hover:decoration-foreground" href={buddyHash({ view: "evaluation", evaluation: { harness: buddy.harness, modelId, effort: buddy.effort } })} onClick={event => { event.preventDefault(); onNavigate({ view: "evaluation", evaluation: { harness: buddy.harness, modelId, effort: buddy.effort } }); }}>{t("pane.sampleCount", { n: evaluation ? evaluation.statistics.overall.samples : 0 })}</a> : "—"}</span>
        {modelAvailable && !buddy.available && <Hint text={unavailableReason} description={buddy.unavailableReason ? reasonText(buddy.unavailableReason) : undefined}><span className="text-xs text-ink3">{t("pane.unavailable")}</span></Hint>}</span>
      <div className="contents">
        {name(t("pane.preference"), preferenceUnsaved, { id: `${id}-preference` })}
        <Segmented value={preference?.mode ?? ""} aria-label={t("pane.buddyPreference", { effort })} onValueChange={setBuddyPreference}
          className="w-fit flex-wrap bg-card">
          <Hint text={editingLock}><SegmentedItem value="" aria-disabled={!editing || undefined} className={choice}>{t("preference.unset")}</SegmentedItem></Hint>
          {MODES.map(mode => <Hint key={mode} text={editingLock ?? (mode === "pin" && pinLocked ? t("pane.locked.pinBuddy") : undefined)}>
            <SegmentedItem value={mode} aria-disabled={!editing || mode === "pin" && pinLocked || undefined} className={choice}>{t(`preference.${mode}`)}</SegmentedItem>
          </Hint>)}
        </Segmented>
      </div>
      <div className="contents">
        <div className="col-start-1 self-start pt-2">{name(t("pane.alias"), aliasUnsaved, { htmlFor: `${id}-alias` })}</div>
        <div className="w-0 min-w-full" data-slot="buddy-alias">
          <Hint text={editingLock}><Input id={`${id}-alias`} className="h-8 w-full min-w-0 bg-card text-sm shadow-none"
            value={buddy.alias ?? ""} readOnly={!editing} aria-disabled={!editing || undefined}
            aria-invalid={!!aliasError || undefined} aria-describedby={aliasError ? `${id}-alias-error` : undefined}
            onChange={event => {
              if (editing) editor.update(draft => ({ ...draft, buddies: draft.buddies.map(entry => entry.buddyId === buddy.buddyId
                ? { ...entry, alias: event.target.value === "" ? null : event.target.value } : entry) }));
            }} /></Hint>
        </div>
        {aliasError && <p id={`${id}-alias-error`} data-slot="alias-error" className="col-span-2 col-start-2 -mt-2 w-full max-w-[28rem] text-xs text-fail break-normal [overflow-wrap:normal]" role="alert"><ReasonMessage reason={aliasError} /></p>}
      </div>
      <div className="contents">
        {name(t("pane.note"), noteUnsaved, { htmlFor: `${id}-note` })}
        <Hint text={editingLock ?? (!preference ? t("pane.noteNone") : undefined)}>
          <Input id={`${id}-note`} className="col-span-2 h-8 w-full max-w-[28rem] min-w-0 bg-card text-sm shadow-none"
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

function EffortRow({ chip, selected, onSelect, editor }: {
  chip: BuddyChip; selected: boolean; onSelect: () => void; editor: Editor;
}) {
  const { t } = useTranslation("buddies");
  const { buddy, effort } = chip;
  const editing = editor.editing;
  const editingLock = !editor.sessionWritable ? t("pane.locked.session") : !editing ? t("pane.locked.saving") : undefined;
  // A retired buddy can be switched off, but cannot be switched on again.
  const canToggle = editing && (buddy.available || buddy.enabled);
  const enableLock = editingLock ?? (!canToggle ? t("pane.locked.retired") : undefined);
  return <div data-slot="buddy-choice" data-selected={selected}
    className={cn("relative transition-colors duration-150 motion-reduce:transition-none", selected ? "bg-past-wash" : "hover:bg-foreground/5")}>
    <Hint text={enableLock}><Switch checked={buddy.enabled} aria-label={t("pane.enable", { effort })}
      className="absolute top-1 left-1 z-10 h-8 w-[34px] bg-transparent px-0.5 before:inset-x-0 before:inset-y-0 after:absolute after:inset-x-0.5 after:inset-y-2 after:rounded-full after:bg-line2 after:transition-colors after:duration-150 after:motion-reduce:transition-none aria-checked:bg-transparent aria-checked:after:bg-foreground [&_[data-slot=switch-thumb]]:relative [&_[data-slot=switch-thumb]]:z-10"
      aria-disabled={!canToggle || undefined} onCheckedChange={enabled => {
        if (canToggle) editor.update(draft => ({ ...draft, buddies: draft.buddies.map(entry => entry.buddyId === buddy.buddyId ? { ...entry, enabled } : entry) }));
      }} /></Hint>
    <button type="button" aria-pressed={selected} data-slot="buddy-stop" aria-label={effort} onClick={onSelect}
      className="flex h-10 w-full items-center gap-2 pr-10 pl-12 text-left text-sm whitespace-nowrap outline-none transition-[color,background-color,box-shadow] duration-150 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset motion-reduce:transition-none">
      <span className={cn("inline-flex items-center", buddy.enabled ? "text-foreground" : "text-ink3", selected && "font-medium")}>{effort}<AliasTag alias={buddy.alias} /></span>
      {chip.preference && <span className="text-xs text-ink2">{t(`preference.${chip.preference}`)}</span>}
      {chip.unsaved && <UnsavedMark label={t("unsaved")} />}
    </button>
  </div>;
}

/** A model's shared settings and one selected buddy, unfolded below its row. */
export function ModelDrawer({ row, harnessState, data, recorded, editor, selectedBuddyId, onSelectBuddy, onNavigate }: {
  row: ModelRow; harnessState?: string; data: ConsoleView; recorded: Snapshot; editor: Editor;
  selectedBuddyId: string; onSelectBuddy: (id: string) => void;
  onNavigate: (location: BuddyLocation) => void;
}) {
  const { t } = useTranslation("buddies");
  const editing = editor.editing;
  const id = useId();
  const listViewport = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const viewport = listViewport.current!;
    if (getComputedStyle(viewport).overflowY === "visible") return;
    const selected = viewport.querySelector<HTMLElement>('[data-slot="buddy-stop"][aria-pressed="true"]')!;
    const item = selected.getBoundingClientRect(), frame = viewport.getBoundingClientRect();
    if (item.top < frame.top) viewport.scrollTop -= frame.top - item.top;
    else if (item.bottom > frame.bottom) viewport.scrollTop += item.bottom - frame.bottom;
  }, [selectedBuddyId]);
  const { modelService, key } = row;
  const member = modelService.buddies[0];
  const selected = row.chips.find(chip => chip.buddy.buddyId === selectedBuddyId)!;
  const publishedLimit = concurrencyEntryFor(recorded.modelServiceConcurrency, member)?.limit ?? MODEL_SERVICE_CONCURRENCY_DEFAULT;
  const raised = row.limit > publishedLimit;
  const limitInvalid = concurrencyLimit(row.limit) === null;
  const buddyIds = new Set(modelService.buddies.map(buddy => buddy.buddyId));
  const mine = (issue: { modelService?: string; buddyId: string }) => issue.modelService === key || (!issue.modelService && buddyIds.has(issue.buddyId));
  const blocking = editor.blocking.filter(issue => mine(issue) && !(limitInvalid && issue.modelService === key));
  const attention = editor.attention.filter(issue => !issue.router && mine(issue));
  const state = !row.available ? "unavailable" : row.pending ? "pending" : "available";
  const visibleReasons = row.reasons.filter(reason => !harnessState || !reason.code.startsWith("harness."));
  const reasons = harnessState || visibleReasons.length
    ? <Joined items={[...(harnessState ? [harnessState] : []), ...(visibleReasons.length ? [<ReasonMessages reasons={visibleReasons} />] : [])]} /> : undefined;
  const editingLock = !editor.sessionWritable ? t("pane.locked.session") : !editing ? t("pane.locked.saving") : undefined;
  const name = (text: string, unsaved: boolean, target: { id: string } | { htmlFor: string }) =>
    <div className="flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-ink3">
      <label {...target}>{text}</label><span className="inline-flex size-1.5 shrink-0">{unsaved && <UnsavedMark label={t("unsaved")} />}</span>
    </div>;

  return <section role="region" aria-label={modelIdentity(modelService)} data-slot="model-drawer"
    className="border-t border-l-2 border-t-line border-l-foreground bg-sunk px-4 py-3 min-[900px]:pl-14">
    {blocking.length > 0 && <p className="mb-3 border-l-2 border-fail pl-3 text-xs text-fail" role="alert"><Joined items={blocking.map(issue => issue.message)} separator=" " /></p>}
    {attention.length > 0 && <p className="mb-3 border-l-2 border-hand-mark pl-3 text-xs text-hand" role="status"><Joined items={attention.map(issue => issue.message)} separator=" " /></p>}
    <div data-slot="model-service" className="border-b border-line pb-3">
      <div data-slot="model-service-line" className="flex flex-wrap items-center gap-x-8 gap-y-3">
        <h3 className="text-xs font-medium text-ink2">{t("pane.model")}</h3>
        <div className="flex items-center gap-3">
          <span className="text-xs text-ink3">{t("pane.status")}</span>
          <Hint text={row.pending ? pendingHint(row, t) : !row.available ? reasons : undefined} description={row.pending ? pendingHint(row, t) : !row.available ? [harnessState, ...reasonTexts(row.reasons, t, !!harnessState)].filter(Boolean).join(" · ") : undefined}><span className="flex items-center gap-1.5 text-sm">
            <StateMark kind={row.available && !row.pending ? "live" : "idle"} className="!animate-none" />{t(`pane.${state}`)}
          </span></Hint>
        </div>
        {row.pending && row.pendingSince && <div className="flex items-center gap-3"><span className="text-xs text-ink3">{t("pane.firstMissing")}</span><time className="text-sm tabular-nums" dateTime={row.pendingSince}>{formatDateTime(Date.parse(row.pendingSince))}</time></div>}
        <div className="flex flex-wrap items-center gap-3">
          {name(t("pane.concurrency"), !Object.is(row.limit, publishedLimit), { htmlFor: `${id}-limit` })}
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            <span data-slot="concurrency-meter" role="meter" aria-label={t("pane.concurrency")}
              aria-valuemin={0} aria-valuemax={!limitInvalid ? row.limit : undefined} aria-valuenow={row.occupancy ?? undefined}
              aria-valuetext={row.occupancy === null ? t("models.occupancyUnknown") : t("models.occupancy", { active: row.occupancy, limit: row.limit })}
              className="h-2 w-[120px] bg-line">
              {row.occupancy !== null && !limitInvalid && <span className="block h-full bg-ink2 transition-[width] duration-150 motion-reduce:transition-none" style={{ width: `${Math.min(100, row.occupancy / row.limit * 100)}%` }} />}
            </span>
            <span id={`${id}-occupancy`} className="text-sm tabular-nums" aria-label={row.occupancy === null ? t("models.occupancyUnknown") : t("pane.occupancy", { n: row.occupancy })}>{row.occupancy ?? "—"} /</span>
            <Hint text={editingLock}><Input id={`${id}-limit`} type="number" inputMode="numeric" className="h-8 w-16 bg-card px-2 text-sm tabular-nums shadow-none"
              min={MODEL_SERVICE_CONCURRENCY_MIN} max={MODEL_SERVICE_CONCURRENCY_MAX} step={1} value={Number.isNaN(row.limit) ? "" : row.limit}
              readOnly={!editing} aria-disabled={!editing || undefined} aria-label={t("pane.concurrencyLimit")}
              aria-invalid={limitInvalid || undefined} aria-describedby={`${id}-occupancy${raised ? ` ${id}-concurrency-notice` : ""}${limitInvalid ? ` ${id}-range` : ""}`} onChange={event => {
                if (!editing) return;
                const value = event.target.value === "" ? NaN : Number(event.target.value);
                editor.update(draft => setConcurrencyLimit(draft, member, value));
              }} /></Hint>
          </div>
        </div>
      </div>
      {limitInvalid && <p id={`${id}-range`} className="mt-2 text-xs text-fail" role="alert">{t("pane.range")}</p>}
      {raised && <div className="mt-3 max-w-2xl"><ConcurrencyNotice id={`${id}-concurrency-notice`} /></div>}
    </div>
    <div data-slot="buddy-columns" className="grid grid-cols-[minmax(0,1fr)] gap-y-4 pt-3 min-[900px]:grid-cols-[max-content_minmax(0,1fr)]">
      <div data-slot="buddy-list" className="flex min-h-0 min-w-0 flex-col min-[900px]:h-0 min-[900px]:min-h-full">
        <div className="mb-3 flex h-5 shrink-0 items-center gap-3 pr-10 text-xs whitespace-nowrap">
          <h3 className="text-xs font-medium text-ink2">{t("models.columns.buddy")}</h3>
          <span className="text-ink3 tabular-nums">{row.chips.length} · {t("models.enabled")} {row.chips.filter(chip => chip.buddy.enabled).length}</span>
        </div>
        <ScrollArea type="always" className="min-[900px]:min-h-0 min-[900px]:flex-1 [&>[data-slot=scroll-area-scrollbar]]:pointer-events-auto [&>[data-slot=scroll-area-scrollbar]]:opacity-100 max-[900px]:overflow-visible! max-[900px]:[&>[data-slot=scroll-area-scrollbar]]:hidden"
          viewportProps={{ ref: listViewport, "data-slot": "buddy-list-viewport", className: "max-[900px]:h-auto max-[900px]:overflow-visible!" }}>
          <div role="group" aria-label={t("models.columns.effort")}>
            {row.chips.map(chip => <EffortRow key={chip.buddy.buddyId} chip={chip} selected={chip.buddy.buddyId === selectedBuddyId}
              editor={editor} onSelect={() => onSelectBuddy(chip.buddy.buddyId)} />)}
          </div>
        </ScrollArea>
      </div>
      <BuddySettings chip={selected} data={data} recorded={recorded} editor={editor} modelAvailable={row.available} onNavigate={onNavigate} />
    </div>
  </section>;
}
