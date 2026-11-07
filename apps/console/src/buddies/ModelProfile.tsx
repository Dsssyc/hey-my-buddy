import i18n from "i18next";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { UnsavedMark } from "@/components/ui/state-mark";
import { ApiError, errorText } from "../api";
import type { ConsoleApi } from "../api";
import { modelServiceKey } from "../console-data";
import { modelNoteText, setModelNote, stageModelCorrespondence } from "../draft";
import { formatDateTime, formatNumber, formatDuration } from "../i18n/format";
import { reasonText } from "../i18n/reason";
import { useCopyState } from "../i18n/use-copy-state";
import { isReason } from "../reason";
import type { Reason } from "../reason";
import type { ConsoleView, ModelCorrespondence, ModelCorrespondenceChange, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { buddyQuad } from "./roles";

const liveT = i18n.getFixedT(null, "buddies");
const startReply = z.discriminatedUnion("status", [
  z.object({ status: z.literal("unavailable"), reason: z.custom<Reason>(isReason) }),
  z.object({ status: z.literal("running"), profileRunId: z.string(), runId: z.string(), buddyId: z.string(), usesQuota: z.literal(true).optional() }),
]);

function Correspondence({ entry, data, editor }: { entry: ModelCorrespondence; data: ConsoleView; editor: Editor }) {
  const { t } = useTranslation("buddies");
  const pending = editor.draft?.modelCorrespondenceChanges.find(change => modelServiceKey(change) === modelServiceKey(entry));
  const [target, setTarget] = useState(pending?.action === "map" ? pending.modelId : entry.modelId);
  const [generation, setGeneration] = useState(pending?.action === "generation" ? pending.displayName : "");
  const [mode, setMode] = useState<"map" | "generation">(pending?.action === "generation" ? "generation" : "map");
  const locked = !editor.sessionWritable || !editor.editing;
  const identity = `${entry.harness} / ${entry.provider} / ${entry.model}`;
  function stage(change: ModelCorrespondenceChange) {
    if (!locked) editor.update(draft => stageModelCorrespondence(draft, change));
  }
  const service = { harness: entry.harness, provider: entry.provider, model: entry.model };
  return <li className="min-w-0 space-y-2 py-3" data-slot="model-correspondence">
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="font-mono wrap-anywhere">{identity}</span>
      <span className="text-ink2">{t(`modelProfile.source.${entry.source}`)}</span>
      <time className="text-ink3" dateTime={entry.createdAt}>{formatDateTime(Date.parse(entry.createdAt))}</time>
    </div>
    {entry.evidenceUrl && <p className="wrap-anywhere text-ink2">{entry.evidenceUrl} · {entry.evidenceDate}</p>}
    <div className="grid grid-cols-2 items-center gap-2 sm:flex sm:flex-wrap">
      <select aria-label={t("modelProfile.actionFor", { name: identity })} value={mode} disabled={locked}
        className="col-span-2 h-8 w-full max-w-full rounded-md border border-input bg-card px-2 text-xs sm:w-auto" onChange={event => setMode(event.target.value as "map" | "generation")}>
        <option value="map">{t("modelProfile.map")}</option><option value="generation">{t("modelProfile.generation")}</option>
      </select>
      {mode === "map" ? <select aria-label={t("modelProfile.targetFor", { name: identity })} value={target} disabled={locked}
        className="col-span-2 h-8 min-w-0 w-full max-w-full rounded-md border border-input bg-card px-2 text-sm sm:w-auto sm:flex-1 sm:max-w-72" onChange={event => setTarget(event.target.value)}>
        {data.models.map(model => <option key={model.modelId} value={model.modelId}>{model.displayName}{data.models.some(other => other.modelId !== model.modelId && other.displayName === model.displayName) ? ` · ${model.modelId}` : ""}</option>)}
      </select> : <Input aria-label={t("modelProfile.newNameFor", { name: identity })} value={generation} maxLength={256} disabled={locked}
        className="col-span-2 h-8 min-w-0 w-full max-w-full bg-card sm:w-auto sm:flex-1 sm:max-w-72" onChange={event => setGeneration(event.target.value)} />}
      <Button variant="outline" size="sm" className="col-span-2 h-8 min-w-0 sm:col-span-1" disabled={locked || mode === "generation" && !generation.trim()}
        onClick={() => stage(mode === "map" ? { ...service, action: "map", modelId: target } : { ...service, action: "generation", displayName: generation.trim() })}>{t("modelProfile.apply")}</Button>
      <Button variant="outline" size="sm" className="col-span-2 h-8 min-w-0 sm:col-span-1" disabled={locked || !entry.canUndo} onClick={() => stage({ ...service, action: "undo", correspondenceId: entry.correspondenceId })}>{t("modelProfile.undo")}</Button>
    </div>
    {pending && <p role="status" className="flex flex-wrap items-center gap-2 text-ink2"><UnsavedMark label={t("unsaved")} />
      {pending.action === "undo" ? t("modelProfile.pendingUndo") : pending.action === "generation" ? t("modelProfile.pendingGeneration", { name: pending.displayName })
        : t("modelProfile.pendingMap", { name: data.models.find(model => model.modelId === pending.modelId)!.displayName })}</p>}
  </li>;
}

/** Shared model facts live alongside the service's settings in the existing drawer. */
export function ModelProfile({ modelId, snapshot, data, editor, api, refresh, mutationsAvailable }: {
  modelId: string; snapshot: Snapshot; data: ConsoleView; editor: Editor; api?: ConsoleApi;
  refresh?: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const id = useId();
  const [busy, setBusy] = useState(false), [note, setNote] = useCopyState(), [error, setError] = useCopyState();
  const model = data.models.find(row => row.modelId === modelId)!;
  const profile = model.profile, run = model.latestProfileRun;
  const buddy = snapshot.buddies.find(row => row.buddyId === snapshot.configuration?.profileBuddyId);
  const unavailable = snapshot.configuration?.profileUnavailableReason;
  const dirty = editor.changeCount > 0;
  const locked = !editor.sessionWritable || !editor.editing;
  const actionLocked = !api || !refresh || locked || !mutationsAvailable || busy || dirty || !!unavailable || !buddy || run?.state === "running";
  const text = modelNoteText(data, modelId);
  async function build() {
    if (actionLocked || !api || !refresh) return;
    setBusy(true); editor.setAuxiliaryBusy(true); setNote(""); setError("");
    try {
      const parsed = startReply.safeParse(await api.command("profile_start", { modelId }, snapshot.csrfToken));
      if (!parsed.success) throw new ApiError("INVALID_RESPONSE", () => liveT("modelProfile.invalidReply"));
      const result = parsed.data;
      setNote(() => result.status === "unavailable" ? reasonText(result.reason) : liveT("modelProfile.started", {
        name: buddyQuad(snapshot.buddies.find(row => row.buddyId === result.buddyId) ?? buddy!),
      }));
      if (!await refresh()) setError(() => liveT("modelProfile.readFailed"));
    } catch (failure) { setError(() => errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  return <div className="mt-5 min-w-0 space-y-5 border-t border-line pt-4 text-xs" data-slot="model-profile" aria-busy={busy}>
    <div className="space-y-2">
      <h3 className="text-xs font-semibold">{t("modelProfile.title")} · {model.displayName}</h3>
      {profile ? <>
        <p className="wrap-anywhere text-sm">{profile.positioning}</p>
        <time className="block text-ink3" dateTime={profile.builtAt}>{formatDateTime(Date.parse(profile.builtAt))}</time>
        {(["strength", "weakness"] as const).map(polarity => <div key={polarity} className="space-y-2">
          <h4 className="font-medium text-ink2">{t(`modelProfile.${polarity}`)}</h4>
          <ul className="space-y-2">{profile.claims.filter(claim => claim.polarity === polarity).map((claim, index) => <li key={index} className="space-y-1">
            <p className="wrap-anywhere">{claim.claim}</p>
            <p className="wrap-anywhere text-ink3">{t(`modelProfile.claimSource.${claim.sourceKind}`)} · {claim.sourceDate} · {claim.sourceUrl}</p>
          </li>)}</ul>
        </div>)}
      </> : <p className="text-ink3">{t("modelProfile.notBuilt")}</p>}
      <p data-slot="profile-quota" className="wrap-anywhere text-ink2">{buddy ? t("modelProfile.quota", { name: buddyQuad(buddy) }) : t("modelProfile.noBuddy")}</p>
      {unavailable && <p className="text-ink2">{reasonText(unavailable)}</p>}
      {dirty && <p className="text-ink2">{t("modelProfile.saveFirst")}</p>}
      <Button variant="outline" size="sm" disabled={actionLocked} onClick={() => void build()}>{t(profile ? "modelProfile.rebuild" : "modelProfile.build")}</Button>
      {note && <p role="status" className="wrap-anywhere">{note}</p>}{error && <p role="alert" className="wrap-anywhere text-fail">{error}</p>}
    </div>
    {run && <div data-slot="profile-run" className="space-y-2">
      <h3 className="text-xs font-medium text-ink2">{t("modelProfile.latestRun")}</h3>
      <p className="flex flex-wrap gap-x-4 gap-y-1"><span>{t(`modelProfile.state.${run.state}`)}</span><time dateTime={run.startedAt}>{formatDateTime(Date.parse(run.startedAt))}</time>
        {run.finishedAt && <time dateTime={run.finishedAt}>{formatDateTime(Date.parse(run.finishedAt))}</time>}<span>{t("modelProfile.corrections", { n: run.correctionRounds })}</span></p>
      <p className="font-mono wrap-anywhere text-ink2">{buddyQuad(run.buddy)}</p>
      {run.reason && <p className="wrap-anywhere text-fail">{reasonText(run.reason)}</p>}
      {run.usage?.attempts.map(attempt => <dl key={attempt.attemptId} className="flex flex-wrap gap-x-5 gap-y-1 text-ink2 tabular-nums" aria-label={t("modelProfile.usage")}>
        <div className="flex gap-2"><dt>{t("modelProfile.duration")}</dt><dd>{attempt.elapsedMs == null ? t("maintenance.unknown") : formatDuration(attempt.elapsedMs)}</dd></div>
        <div className="flex gap-2"><dt>{t("modelProfile.toolCalls")}</dt><dd>{attempt.toolCalls == null ? t("maintenance.unknown") : formatNumber(attempt.toolCalls)}</dd></div>
        {([["input", attempt.tokenUsage?.inputTokens], ["cached", attempt.tokenUsage?.cachedInputTokens], ["output", attempt.tokenUsage?.outputTokens]] as const).map(([key, value]) => <div key={key} className="flex gap-2"><dt>{t(`maintenance.${key}`)}</dt><dd>{value == null ? t("maintenance.unknown") : formatNumber(value)}</dd></div>)}
      </dl>)}
      {!run.usage && <p className="text-ink3">{t("modelProfile.usage")} · {t("maintenance.unknown")}</p>}
      {run.toolSummary === null ? <p data-slot="profile-tool-summary" className="text-ink3">{t("modelProfile.toolOutcomesUnknown")}</p>
        : run.toolSummary.length > 0 && <ul data-slot="profile-tool-summary" className="space-y-1 text-ink2 tabular-nums">
          {run.toolSummary.map(tool => <li key={tool.toolName} className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
            <span className="min-w-0 font-mono wrap-anywhere">{tool.toolName ?? t("modelProfile.unknownTool")}</span>
            <dl className="flex flex-wrap gap-x-5 gap-y-1">
              <div className="flex gap-2"><dt>{t("modelProfile.toolCalls")}</dt><dd>{formatNumber(tool.calls)}</dd></div>
              <div className="flex gap-2"><dt>{t("modelProfile.toolFailed")}</dt><dd className={tool.calls > 0 && tool.failed === tool.calls ? "text-fail" : undefined}>{formatNumber(tool.failed)}</dd></div>
              <div className="flex gap-2"><dt>{t("modelProfile.toolUnknown")}</dt><dd>{formatNumber(tool.unknown)}</dd></div>
            </dl>
          </li>)}
        </ul>}
    </div>}
    <div className="space-y-2">
      <label htmlFor={`${id}-note`} className="flex items-center gap-2 font-medium text-ink2">{t("modelProfile.note")}{text !== modelNoteText(snapshot, modelId) && <UnsavedMark label={t("unsaved")} />}</label>
      <textarea id={`${id}-note`} rows={3} maxLength={4000} value={text} readOnly={locked} aria-disabled={locked || undefined}
        className="w-full resize-y rounded-md border border-input bg-card px-2.5 py-1.5 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        onChange={event => { if (!locked) editor.update(draft => setModelNote(draft, modelId, event.target.value)); }} />
    </div>
    <div><h3 className="text-xs font-medium text-ink2">{t("modelProfile.correspondences")}</h3>
      <ul className="divide-y divide-line">{data.modelCorrespondences.filter(entry => entry.modelId === modelId).map(entry => <Correspondence key={entry.correspondenceId} entry={entry} data={data} editor={editor} />)}</ul>
    </div>
  </div>;
}
