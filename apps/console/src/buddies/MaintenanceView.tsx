import i18n from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StateMark } from "@/components/ui/state-mark";
import { errorText } from "../api";
import type { ConsoleApi } from "../api";
import { useCopyState } from "../i18n/use-copy-state";
import { formatShortDateTime } from "../i18n/format";
import type { Configuration, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { buddyQuad, withConfiguration } from "./roles";
import { DomainTree } from "./DomainTree";
import { MaintenanceActivity } from "./MaintenanceActivity";
import { RouterSection, RouterField } from "./RouterSection";
import { RouterFailure } from "./RouterFailure";
import { RouterTimeRange, RouterUsage, sumRouterUsage, routerUsageTotal } from "./RouterUsage";
import { RecordRegion } from "./RecordRegion";

const liveT = i18n.getFixedT(null, "buddies");

/** Maintenance settings stay in the shared draft; record actions retain their own write gate. */
export function MaintenanceView({ snapshot, editor, api, refresh, mutationsAvailable, active = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean; active?: boolean;
}) {
  const { t } = useTranslation("buddies");
  const detailT = useTranslation("taskDetail").t;
  const runs = [...(snapshot.maintenance?.runs ?? [])].sort((a, b) => Number(b.state === "running") - Number(a.state === "running") || Date.parse(b.startedAt) - Date.parse(a.startedAt));
  const config = snapshot.configuration === null ? null : editor.view.configuration;
  const [choosing, setChoosing] = useState(false), [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false), [stopping, setStopping] = useState<string | null>(null);
  const [feedbackScope, setFeedbackScope] = useState<"maintenance" | "domain">("maintenance");
  const [note, setNote] = useCopyState(), [error, setError] = useCopyState();
  const locked = !editor.sessionWritable || !editor.editing || !config;
  const actionLocked = locked || !mutationsAvailable || busy;
  const dirty = !!config && !!snapshot.configuration && config.maintenanceBuddyId !== snapshot.configuration.maintenanceBuddyId;
  const dirtyInterval = !!config && !!snapshot.configuration && !Object.is(config.maintenanceIntervalSeconds, snapshot.configuration.maintenanceIntervalSeconds);
  const selected = editor.view.buddies.find(b => b.buddyId === config?.maintenanceBuddyId);
  const candidates = editor.view.buddies.filter(b => b.enabled && b.available && snapshot.harnesses?.some(h => h.harness === b.harness && h.permissions?.none));
  const validInterval = config?.maintenanceIntervalSeconds !== undefined && Number.isSafeInteger(config.maintenanceIntervalSeconds) && config.maintenanceIntervalSeconds > 0;
  const latest = runs.find(run => run.finishedAt);
  const nextAt = config?.maintenanceBuddyId && validInterval && latest?.finishedAt && !runs.some(run => run.state === "running")
    ? Date.parse(latest.finishedAt) + config.maintenanceIntervalSeconds! * 1000 : null;
  const maximum = Math.max(0, ...runs.map(run => (routerUsageTotal(sumRouterUsage(run.usage?.attempts ?? [])) ?? 0)));
  function change(patch: Partial<Configuration>) { if (!locked) editor.update(draft => withConfiguration(draft, patch)); }
  async function command(operation: string, params: object): Promise<boolean> {
    if (actionLocked) return false;
    setFeedbackScope(operation === "task_cancel" ? "maintenance" : "domain");
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setNote("");
    try {
      await api.command(operation, params, snapshot.csrfToken);
      setNote(() => liveT(operation === "task_cancel" ? "maintenance.stopRequested" : operation === "domain_merge" ? "maintenance.merged" : "maintenance.saved"));
      if (!await refresh()) setError(() => liveT("maintenance.readFailed"));
      return true;
    } catch (failure) { setError(() => errorText(failure)); return false; }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  async function stop(taskId: string) {
    setStopping(taskId);
    if (!await command("task_cancel", { runId: taskId })) setStopping(null);
  }
  return <div data-slot="maintenance-plane" aria-busy={busy} className="min-w-0 space-y-4">
    <RouterSection name={t("routerWork.settings")}>
      <div className="router-settings">
      <RouterField name={t("maintenance.buddy")} help={t("maintenance.dataFlow")} unsaved={dirty ? t("unsaved") : undefined}>
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <span className="min-w-0 font-mono text-xs wrap-anywhere">{selected ? buddyQuad(selected) : config?.maintenanceBuddyId ?? t("maintenance.none")}</span>
          <Button variant="outline" size="sm" disabled={locked} onClick={() => setChoosing(true)}>{t("maintenance.choose")}</Button>
          {!config?.maintenanceBuddyId && <span className="text-xs text-ink2">{t("routerWork.notSet")}</span>}
        </div>
      </RouterField>
      <RouterField name={t("maintenance.interval")} unsaved={dirtyInterval ? t("unsaved") : undefined}>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="h-8 w-24 bg-card tabular-nums" type="number" min={1} step={1} aria-label={t("maintenance.interval")}
            value={config?.maintenanceIntervalSeconds === undefined || Number.isNaN(config.maintenanceIntervalSeconds) ? "" : config.maintenanceIntervalSeconds}
            readOnly={locked} aria-invalid={config?.maintenanceIntervalSeconds !== undefined && !validInterval || undefined}
            onChange={e => change({ maintenanceIntervalSeconds: e.target.value === "" ? NaN : Number(e.target.value) })} />
          <span className="text-xs text-ink2">{t("maintenance.seconds")}</span>
          {nextAt !== null && <span className="text-xs text-ink2" data-slot="maintenance-next">{t("maintenance.nextRun", { when: formatShortDateTime(nextAt) })}</span>}
          {config?.maintenanceIntervalSeconds !== undefined && !validInterval && <p className="text-xs text-fail" role="alert">{t("maintenance.intervalInvalid")}</p>}
        </div>
      </RouterField>
      </div>
    </RouterSection>
    <RouterSection name={t("roles.records.name")}>
      {feedbackScope === "maintenance" && note && <p role="status" className="mb-3 text-xs">{note}</p>}
      {feedbackScope === "maintenance" && error && <p role="alert" className="mb-3 text-xs text-fail">{error}</p>}
      {!runs.length ? <p className="text-xs text-ink3">{t("maintenance.noRecords")}</p> : <RecordRegion aria-label={t("maintenance.records")} data-slot="maintenance-records-region"><table className="w-full min-w-[980px] table-fixed text-xs" aria-label={t("maintenance.records")}>
        <thead><tr className="router-record-row text-left text-ink3"><th className="w-8 font-normal" aria-label={t("maintenance.result")} /><th className="w-[27%] font-normal">{t("routerWork.timeRange")}</th><th className="w-[9%] font-normal">{t("maintenance.trigger")}</th><th className="w-[15%] font-normal">{t("routerWork.usage")}</th><th className="font-normal">{t("maintenance.reason")}</th><th className="w-20" /></tr></thead>
        <tbody>{runs.map(run => <tr key={run.maintenanceRunId} className={`router-record-row ${run.state === "running" ? "running-record" : ""}`} data-slot="maintenance-record">
          <td><span aria-label={t(`maintenance.${run.state}`)}><StateMark kind={run.state === "running" ? "live" : run.state === "failed" ? "fail" : "done"} /></span></td>
          <td className="pr-3"><RouterTimeRange startedAt={run.startedAt} finishedAt={run.finishedAt} /></td>
          <td className="pr-3 text-ink2">{t(run.triggerType === "manual" ? "maintenance.manual" : "maintenance.intervalTrigger")}</td>
          <td className="pr-3"><RouterUsage floating usage={sumRouterUsage(run.usage?.attempts ?? [])} maximum={maximum}
            preferenceKey={`maintenance-run.${run.maintenanceRunId}`} label={`${t("maintenance.records")} ${formatShortDateTime(Date.parse(run.startedAt))}`} /></td>
          <td className="pr-3"><div className="truncate text-fail">{run.state === "failed" && run.reason && <RouterFailure failure={run.reason} />}{run.state === "running" && active && <MaintenanceActivity taskId={run.taskId} api={api} />}</div></td>
          <td className="text-right">{run.state === "running" && <Button variant="ghost" size="sm" disabled={actionLocked || stopping === run.taskId}
            onClick={() => void stop(run.taskId)}>{stopping === run.taskId ? detailT("state.stopping") : t("maintenance.stop")}</Button>}</td>
        </tr>)}</tbody>
      </table></RecordRegion>}
    </RouterSection>
    <RouterSection name={t("maintenance.domains")}>
      <DomainTree nodes={snapshot.domainNodes ?? []} locked={actionLocked} command={command} error={feedbackScope === "domain" ? error : ""} note={feedbackScope === "domain" ? note : ""} clearFeedback={() => { setError(""); setNote(""); }} />
    </RouterSection>
    <Dialog open={choosing} onOpenChange={setChoosing}><DialogContent aria-describedby={undefined} className="sm:max-w-xl">
      <DialogTitle>{t("maintenance.choose")}</DialogTitle>
      <Input type="search" aria-label={t("maintenance.search")} value={query} onChange={e => setQuery(e.target.value)} />
      <ScrollArea className="h-72"><ul className="divide-y divide-line">
        <li><button className="w-full px-2 py-3 text-left hover:bg-sunk transition-colors duration-150 motion-reduce:transition-none" disabled={locked} onClick={() => { change({ maintenanceBuddyId: null }); setChoosing(false); }}>{t("maintenance.none")}</button></li>
        {candidates.filter(b => buddyQuad(b).toLowerCase().includes(query.trim().toLowerCase())).map(b => <li key={b.buddyId}><button disabled={locked} className="w-full px-2 py-3 text-left font-mono text-xs wrap-anywhere hover:bg-sunk transition-colors duration-150 motion-reduce:transition-none" onClick={() => { change({ maintenanceBuddyId: b.buddyId }); setChoosing(false); }}>{buddyQuad(b)}</button></li>)}
        {candidates.length === 0 && <li className="px-2 py-3 text-xs text-ink3">{t("maintenance.noCandidates")}</li>}
      </ul></ScrollArea>
    </DialogContent></Dialog>
  </div>;
}
