import { useEffect, useId, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatDuration, formatNumber } from "../i18n/format";
import { reasonText } from "../i18n/reason";
import type { Configuration, Model, ProfileRun, ProfileToolSummary, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { buddyQuad, withConfiguration } from "./roles";
import { QuantBarDisclosure } from "./QuantBar";
import { RouterSection } from "./RouterSection";
import { RouterFailure } from "./RouterFailure";
import { RouterTimeRange, RouterUsage, routerUsageTotal, sumRouterUsage } from "./RouterUsage";
import "./RecordRegion.css";

function RunMark({ state }: { state: ProfileRun["state"] }) {
  const { t } = useTranslation("buddies");
  return <span role="img" aria-label={t(`modelProfile.state.${state}`)}><StateMark kind={state === "running" ? "live" : state === "failed" ? "fail" : "done"} /></span>;
}

function ToolOutcome({ tool, maximum, preferenceKey }: { tool: ProfileToolSummary; maximum: number; preferenceKey: string }) {
  const { t } = useTranslation("buddies");
  const success = tool.calls - tool.failed - tool.unknown;
  const allFailed = tool.calls > 0 && tool.failed === tool.calls;
  const label = `${t("routerProfiles.succeeded")} ${formatNumber(success)}; ${t("routerProfiles.failed")} ${formatNumber(tool.failed)}; ${t("routerProfiles.unknownCalls")} ${formatNumber(tool.unknown)}; ${t("routerProfiles.calls", { n: tool.calls })}`;
  return <tr className={allFailed ? "bg-fail/10 text-fail" : undefined}>
    <th scope="row" className="py-2 pr-5 text-left font-normal">
      <span className="inline-flex items-center gap-2 font-mono wrap-anywhere">{allFailed && <RunMark state="failed" />}{tool.toolName ?? t("modelProfile.unknownTool")}{allFailed && <span className="font-sans font-semibold">· {t("routerProfiles.allFailed")}</span>}</span>
    </th>
    <td className="py-2"><QuantBarDisclosure preferenceKey={preferenceKey} label={`${tool.toolName ?? t("modelProfile.unknownTool")}: ${label}`}
      maximum={maximum} parts={[{ value: success, tone: "green" }, { value: tool.failed, tone: "pink" }, { value: tool.unknown, tone: "gray" }]}>
      <span aria-label={label} className="inline-flex flex-wrap items-center gap-2">
        {t("routerProfiles.succeeded")} {formatNumber(success)} · {tool.failed > 0 && <StateMark kind="fail" />}{t("routerProfiles.failed")} {formatNumber(tool.failed)} · {t("routerProfiles.unknownCalls")} {formatNumber(tool.unknown)}
      </span>
    </QuantBarDisclosure></td>
  </tr>;
}

function RunDetail({ run }: { run: ProfileRun }) {
  const { t } = useTranslation("buddies");
  const attempts = run.usage?.attempts ?? [];
  const maximum = Math.max(1, ...attempts.map(attempt => routerUsageTotal(attempt.tokenUsage) ?? 0));
  const tools = run.toolSummary;
  return <div className="space-y-5 pb-5 pt-4 text-xs">
    <p className="font-mono text-ink2 wrap-anywhere">{buddyQuad(run.buddy)}</p>
    {run.reason && <p className="border-l-2 border-fail bg-fail/10 px-4 py-3 text-fail" role="alert"><RouterFailure failure={run.reason} /></p>}
    <div>
      <h4 className="mb-2 font-medium">{t("routerProfiles.toolOutcomes")}</h4>
      {tools === null ? <p className="text-ink3">{t("routerProfiles.allUnknownTools")}</p>
        : tools.length === 0 ? <p className="text-ink3">{t("routerProfiles.noTools")}</p>
          : <div className="overflow-x-auto"><table className="w-full min-w-[400px] border-collapse text-left">
            <thead className="border-b text-ink3"><tr><th className="py-2 font-normal">{t("routerProfiles.tool")}</th><th className="py-2 font-normal">{t("routerProfiles.succeeded")} / {t("routerProfiles.failed")} / {t("routerProfiles.unknownCalls")}</th></tr></thead>
            <tbody className="divide-y divide-line">{tools.map((tool, index) => <ToolOutcome key={`${tool.toolName}-${index}`} tool={tool} maximum={Math.max(1, ...tools.map(row => row.calls))} preferenceKey={`profile-tool.${run.profileRunId}.${tool.toolName ?? "unknown"}.${index}`} />)}</tbody>
          </table></div>}
    </div>
    <div>
      <h4 className="mb-2 font-medium">{t("routerProfiles.attempts")}</h4>
      {attempts.length ? <div className="overflow-x-auto"><table className="w-full min-w-[470px] border-collapse text-left">
        <thead className="border-b text-ink3"><tr><th className="py-2 font-normal">{t("routerProfiles.attemptHeader")}</th><th className="py-2 font-normal">{t("routerProfiles.duration")}</th><th className="py-2 font-normal">{t("routerProfiles.tokens")}</th></tr></thead>
        <tbody className="divide-y divide-line">{attempts.map((attempt, index) => <tr key={attempt.attemptId}>
          <td className="py-2 pr-4">{t("routerProfiles.attempt", { n: index + 1 })}</td>
          <td className="py-2 pr-4 tabular-nums">{attempt.elapsedMs === null ? t("routerProfiles.unknown") : formatDuration(attempt.elapsedMs)}</td>
          <td className="py-2"><RouterUsage usage={attempt.tokenUsage} maximum={maximum} preferenceKey={`profile-attempt.${run.profileRunId}.${attempt.attemptId}`} label={`${t("routerProfiles.attempt", { n: index + 1 })} · ${t("routerProfiles.tokens")}`} /></td>
        </tr>)}</tbody>
      </table></div> : <p className="text-ink3">{t("routerProfiles.noUsage")}</p>}
    </div>
  </div>;
}

function ProfileRecords({ models, profileRunId }: { models: Model[]; profileRunId?: string }) {
  const { t } = useTranslation("buddies");
  const runs = models.filter(model => model.latestProfileRun !== null).sort((a, b) => Date.parse(b.latestProfileRun!.startedAt) - Date.parse(a.latestProfileRun!.startedAt));
  if (!runs.length) return <p className="text-sm text-ink3">{t("routerProfiles.noRuns")}</p>;
  const newestFailure = runs.find(model => model.latestProfileRun!.state === "failed")?.latestProfileRun?.profileRunId;
  const maximum = Math.max(1, ...runs.map(model => routerUsageTotal(sumRouterUsage(model.latestProfileRun!.usage?.attempts ?? [])) ?? 0));
  return <div data-slot="profile-records" className="record-region record-region--profile" role="region" tabIndex={0} aria-label={t("routerProfiles.records")}><table className="w-full min-w-[640px] border-collapse text-left text-xs">
      <thead className="border-b text-ink3"><tr><th className="w-8 py-2 font-normal"><span className="sr-only">{t("routerProfiles.records")}</span></th><th className="py-2 font-normal">{t("routerProfiles.model")}</th><th className="py-2 font-normal">{t("routerProfiles.period")}</th><th className="py-2 font-normal">{t("routerProfiles.tokens")}</th></tr></thead>
      <tbody className="divide-y divide-line">{runs.map(model => <RunRow key={model.latestProfileRun!.profileRunId} model={model}
        initiallyOpen={model.latestProfileRun!.profileRunId === newestFailure} selected={model.latestProfileRun!.profileRunId === profileRunId} maximum={maximum} />)}</tbody>
    </table></div>;
}

function RunRow({ model, initiallyOpen, selected, maximum }: { model: Model; initiallyOpen: boolean; selected: boolean; maximum: number }) {
  const { t } = useTranslation("buddies");
  const detailId = useId();
  const run = model.latestProfileRun!;
  const [open, setOpen] = useState(initiallyOpen || selected);
  const handle = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (selected) { setOpen(true); handle.current?.focus(); handle.current?.scrollIntoView?.({ block: "nearest" }); } }, [selected]);
  return <>
    <tr>
      <td className="py-3 align-top"><RunMark state={run.state} /></td>
      <td className="py-3 pr-4 align-top"><button ref={handle} type="button" aria-expanded={open} aria-controls={detailId}
        className="inline-flex items-center gap-2 text-left hover:underline focus-visible:underline"
        onClick={() => setOpen(value => !value)}><ChevronRight aria-hidden="true" size={16} strokeWidth={1.5} className={`shrink-0 transition-transform duration-150 motion-reduce:transition-none ${open ? "rotate-90" : ""}`} />{model.displayName}</button>
        <span className="mt-1 block text-ink3">{t("routerProfiles.corrections", { n: run.correctionRounds })}</span></td>
      <td className="py-3 pr-4 align-top"><RouterTimeRange startedAt={run.startedAt} finishedAt={run.finishedAt} /></td>
      <td className="py-3 align-top"><RouterUsage usage={sumRouterUsage(run.usage?.attempts ?? [])} maximum={maximum} preferenceKey={`profile-run.${run.profileRunId}`} label={`${model.displayName} · ${t("routerProfiles.tokens")}`} /></td>
    </tr>
    {open && <tr id={detailId}><td colSpan={4} className="border-t border-line bg-sunk/30 px-5"><RunDetail run={run} /></td></tr>}
  </>;
}

/** The selected Profiles work: assignment and observed profile runs. */
export function ProfileSettingsView({ snapshot, editor, profileRunId }: { snapshot: Snapshot; editor: Editor; profileRunId?: string }) {
  const { t } = useTranslation("buddies");
  const [choosing, setChoosing] = useState(false);
  const [query, setQuery] = useState("");
  const config = snapshot.configuration === null ? null : editor.view.configuration;
  const locked = !config ? t("roles.upgrade.locked") : !editor.sessionWritable ? t("pane.locked.session") : !editor.editing ? t("pane.locked.saving") : undefined;
  const selected = editor.view.buddies.find(buddy => buddy.buddyId === config?.profileBuddyId);
  const candidates = editor.view.buddies.filter(buddy => buddy.enabled && buddy.available &&
    snapshot.harnesses?.some(harness => harness.harness === buddy.harness && harness.permissions?.web === "enforced"));
  const dirty = !!config && !!snapshot.configuration && config.profileBuddyId !== snapshot.configuration.profileBuddyId;
  const models = editor.view.models;
  function change(patch: Partial<Configuration>) { if (!locked) editor.update(draft => withConfiguration(draft, patch)); }
  return <div data-slot="profile-plane" className="min-w-0">
    <RouterSection name={t("routerProfiles.assigned")} unsaved={dirty ? t("unsaved") : undefined}>
      <div className="flex flex-wrap items-center justify-between gap-3"><span className="min-w-0 font-mono text-xs wrap-anywhere">{selected ? buddyQuad(selected) : config?.profileBuddyId ?? t("profile.none")}</span>
        <Hint text={locked}><Button variant="outline" size="sm" disabled={!!locked} onClick={() => setChoosing(true)}>{t("profile.choose")}</Button></Hint></div>
      {!config?.profileBuddyId && <p className="mt-2 text-xs text-ink2">{t("routerProfiles.notConfigured")}</p>}
      {snapshot.configuration?.profileUnavailableReason && config?.profileBuddyId && config.profileBuddyId === snapshot.configuration.profileBuddyId && <p role="status" className="mt-2 text-xs text-ink2">{reasonText(snapshot.configuration.profileUnavailableReason)}</p>}
      <p data-slot="profile-consequence" className="mt-2 max-w-3xl text-xs leading-relaxed text-ink2">{t("profile.dataFlow")}</p>
    </RouterSection>
    <RouterSection name={t("routerProfiles.records")}><ProfileRecords models={models} profileRunId={profileRunId} /></RouterSection>
    <Dialog open={choosing && !locked} onOpenChange={setChoosing}><DialogContent aria-describedby={undefined} className="sm:max-w-xl">
      <DialogTitle>{t("profile.choose")}</DialogTitle>
      <Input type="search" aria-label={t("profile.search")} value={query} onChange={event => setQuery(event.target.value)} />
      <ScrollArea className="h-72"><ul className="divide-y divide-line">
        <li><button disabled={!!locked} className="w-full px-2 py-3 text-left transition-colors duration-150 hover:bg-sunk motion-reduce:transition-none" onClick={() => { change({ profileBuddyId: null }); setChoosing(false); }}>{t("profile.none")}</button></li>
        {candidates.filter(buddy => buddyQuad(buddy).toLowerCase().includes(query.trim().toLowerCase())).map(buddy => <li key={buddy.buddyId}>
          <button disabled={!!locked} className="w-full px-2 py-3 text-left font-mono text-xs wrap-anywhere transition-colors duration-150 hover:bg-sunk motion-reduce:transition-none" onClick={() => { change({ profileBuddyId: buddy.buddyId }); setChoosing(false); }}>{buddyQuad(buddy)}</button>
        </li>)}
        {candidates.length === 0 && <li className="px-2 py-3 text-xs text-ink3">{t("profile.noCandidates")}</li>}
      </ul></ScrollArea>
    </DialogContent></Dialog>
  </div>;
}
