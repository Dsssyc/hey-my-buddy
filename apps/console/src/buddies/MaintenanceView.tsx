import i18n from "i18next";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Section } from "@/components/section";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { z } from "zod";
import { isReason } from "../reason";
import { ApiError, errorText } from "../api";
import type { ConsoleApi } from "../api";
import { useCopyState } from "../i18n/use-copy-state";
import { reasonText } from "../i18n/reason";
import { formatDateTime, formatNumber } from "../i18n/format";
import type { Configuration, Snapshot } from "../types";
import type { Reason } from "../reason";
import type { Editor } from "../use-editor";
import { buddyQuad, withConfiguration } from "./roles";
import { DomainTree } from "./DomainTree";
import { MaintenanceActivity } from "./MaintenanceActivity";

const liveT = i18n.getFixedT(null, "buddies");

const startReplySchema = z.object({ status: z.enum(["running", "not-configured", "no-new-evidence", "unavailable", "settings-changed"]), reason: z.custom<Reason>(isReason).optional(), runId: z.string().optional() })
  .refine(reply => reply.status !== "unavailable" || reply.reason !== undefined);

export function MaintenanceView({ snapshot, editor, api, refresh, mutationsAvailable }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const detailT = useTranslation("taskDetail").t;
  const runs = snapshot.maintenance?.runs ?? [];
  const config = editor.view.configuration;
  const [choosing, setChoosing] = useState(false), [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState<string | null>(null);
  const [feedbackScope, setFeedbackScope] = useState<"maintenance" | "domain">("maintenance");
  const [note, setNote] = useCopyState(), [error, setError] = useCopyState();
  const locked = !editor.sessionWritable || !editor.editing || !config;
  const actionLocked = locked || !mutationsAvailable || busy;
  const dirty = !!config && !!snapshot.configuration && (config.maintenanceBuddyId !== snapshot.configuration.maintenanceBuddyId || !Object.is(config.maintenanceIntervalSeconds, snapshot.configuration.maintenanceIntervalSeconds));
  const selected = editor.view.buddies.find(b => b.buddyId === config?.maintenanceBuddyId);
  const candidates = editor.view.buddies.filter(b => b.enabled && b.available && snapshot.harnesses?.some(h => h.harness === b.harness && h.permissions?.none));
  const validInterval = config?.maintenanceIntervalSeconds !== undefined && Number.isSafeInteger(config.maintenanceIntervalSeconds) && config.maintenanceIntervalSeconds > 0;
  function change(patch: Partial<Configuration>) { if (!locked) editor.update(draft => withConfiguration(draft, patch)); }
  async function command(operation: string, params: object): Promise<boolean> {
    if (actionLocked) return false;
    setFeedbackScope(operation === "maintenance_start" || operation === "task_cancel" ? "maintenance" : "domain");
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setNote("");
    try {
      if (operation === "maintenance_start") {
        const parsed = startReplySchema.safeParse(await api.command(operation, params, snapshot.csrfToken));
        if (!parsed.success) throw new ApiError("INVALID_RESPONSE", () => liveT("maintenance.invalidReply"));
        const result = parsed.data;
        setNote(() => result.status === "unavailable" ? liveT("maintenance.unavailable", { reason: reasonText(result.reason!) })
          : liveT(result.status === "settings-changed" ? "maintenance.settingsChanged" : result.status === "not-configured" ? "maintenance.notConfigured" : result.status === "no-new-evidence" ? "maintenance.noData" : result.runId ? "maintenance.started" : "maintenance.alreadyRunning"));
      } else {
        await api.command(operation, params, snapshot.csrfToken);
        setNote(() => liveT(operation === "task_cancel" ? "maintenance.stopRequested" : operation === "domain_merge" ? "maintenance.merged" : "maintenance.saved"));
      }
      if (!await refresh()) setError(() => liveT("maintenance.readFailed"));
      return true;
    } catch (failure) { setError(() => errorText(failure)); return false; }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  async function stop(taskId: string) {
    setStopping(taskId);
    if (!await command("task_cancel", { runId: taskId })) setStopping(null);
  }
  return <div data-slot="maintenance-plane" aria-busy={busy}>
    <h3 className="px-6 pt-6 pb-2 text-base font-semibold">{t("maintenance.title")}</h3>
    <Section name={t("maintenance.buddy")} hint={t("maintenance.dataFlow")} unsaved={dirty ? t("unsaved") : undefined}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 font-mono text-xs wrap-anywhere">{selected ? buddyQuad(selected) : config?.maintenanceBuddyId ?? t("maintenance.none")}</span>
        <Button variant="outline" size="sm" disabled={locked} onClick={() => setChoosing(true)}>{t("maintenance.choose")}</Button>
      </div>
    </Section>
    <Section name={t("maintenance.interval")}>
      <div className="flex flex-wrap items-center gap-2">
        <Input className="h-8 w-28 bg-card tabular-nums" type="number" min={1} step={1} aria-label={t("maintenance.interval")}
          value={config?.maintenanceIntervalSeconds === undefined || Number.isNaN(config.maintenanceIntervalSeconds) ? "" : config.maintenanceIntervalSeconds}
          readOnly={locked} aria-invalid={config?.maintenanceIntervalSeconds !== undefined && !validInterval || undefined}
          onChange={e => change({ maintenanceIntervalSeconds: e.target.value === "" ? NaN : Number(e.target.value) })} />
        <span className="text-xs text-ink2">{t("maintenance.seconds")}</span>
        {config?.maintenanceIntervalSeconds !== undefined && !validInterval && <p className="text-xs text-fail" role="alert">{t("maintenance.intervalInvalid")}</p>}
      </div>
    </Section>
    <Section name={t("maintenance.now")}>
      <div className="flex flex-wrap items-center gap-3">
        <Hint text={dirty ? t("maintenance.unsaved") : undefined}><Button variant="outline" size="sm" disabled={actionLocked || dirty}
          onClick={() => void command("maintenance_start", {})}>{t("maintenance.now")}</Button></Hint>
        <span data-slot="maintenance-pending" className="text-xs whitespace-nowrap text-ink2">{snapshot.maintenance ? t("maintenance.pending", { n: snapshot.maintenance.pendingCount }) : t("maintenance.unknown")}</span>
      </div>
      {feedbackScope === "maintenance" && note && <p role="status" className="mt-2 text-xs">{note}</p>}
      {feedbackScope === "maintenance" && error && <p role="alert" className="mt-2 text-xs text-fail">{error}</p>}
    </Section>
    <Section name={t("maintenance.records")}>
      {!runs.length ? <p className="text-xs text-ink3">{t("maintenance.noRecords")}</p> : <ul className="divide-y divide-line">
        {runs.map((run, index) => {
          const buddy = run.buddy;
          return <li key={run.maintenanceRunId} className="space-y-2 py-3 first:pt-0 text-xs" data-slot="maintenance-record">
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <time dateTime={run.startedAt}>{formatDateTime(Date.parse(run.startedAt))}</time>
              {run.finishedAt && <time dateTime={run.finishedAt}>{formatDateTime(Date.parse(run.finishedAt))}</time>}
              <span className="text-ink2">{t(run.triggerType === "manual" ? "maintenance.manual" : "maintenance.intervalTrigger")}</span>
              <span className={run.state === "failed" ? "text-fail" : "text-foreground"}>{t(`maintenance.${run.state}`)}</span>
              {run.state === "running" && <Button variant="outline" size="sm" disabled={actionLocked || stopping === run.taskId}
                onClick={() => void stop(run.taskId)}>{stopping === run.taskId ? detailT("state.stopping") : t("maintenance.stop")}</Button>}
            </div>
            <p className="font-mono wrap-anywhere text-ink2">{buddy ? buddyQuad(buddy) : run.buddyId}</p>
            {run.state === "running" && <MaintenanceActivity key={run.taskId} taskId={run.taskId} api={api} />}
            {run.usage?.attempts.map(attempt => <dl key={attempt.attemptId} className="flex flex-wrap gap-x-5 gap-y-1 tabular-nums text-ink2" aria-label={t("maintenance.usage")}>
              {([['input', attempt.tokenUsage?.inputTokens], ['cached', attempt.tokenUsage?.cachedInputTokens], ['output', attempt.tokenUsage?.outputTokens]] as const).map(([name, n]) =>
                <div key={name} className="flex gap-2"><dt>{t(`maintenance.${name}`)}</dt><dd>{n == null ? t("maintenance.unknown") : formatNumber(n)}</dd></div>)}
            </dl>)}
            {run.reason && <p className="wrap-anywhere text-fail">{reasonText(run.reason)}</p>}
            {index === 0 && run.state === "failed" && <div className="flex flex-wrap items-center gap-x-4 gap-y-2" data-slot="maintenance-retry">
              <span className="text-ink2">{!snapshot.configuration?.maintenanceBuddyId ? t("maintenance.notConfigured") : run.finishedAt && snapshot.configuration.maintenanceIntervalSeconds !== undefined
                ? t("maintenance.nextRun", { when: formatDateTime(Date.parse(run.finishedAt) + snapshot.configuration.maintenanceIntervalSeconds * 1000) })
                : t("maintenance.unknown")}</span>
              <Hint text={dirty ? t("maintenance.unsaved") : undefined}><Button variant="outline" size="sm" disabled={actionLocked || dirty || runs.some(item => item.state === "running")}
                onClick={() => void command("maintenance_start", {})}>{t("maintenance.runNow")}</Button></Hint>
            </div>}
          </li>;
        })}
      </ul>}
    </Section>
    <Section name={t("maintenance.domains")}>
      <DomainTree nodes={snapshot.domainNodes ?? []} locked={actionLocked} command={command} error={feedbackScope === "domain" ? error : ""} note={feedbackScope === "domain" ? note : ""} clearFeedback={() => { setError(""); setNote(""); }} />
    </Section>
    <Dialog open={choosing} onOpenChange={setChoosing}><DialogContent aria-describedby={undefined} className="sm:max-w-xl">
      <DialogTitle>{t("maintenance.choose")}</DialogTitle>
      <Input type="search" aria-label={t("maintenance.search")} value={query} onChange={e => setQuery(e.target.value)} />
      <ScrollArea className="h-72"><ul className="divide-y divide-line">
        <li><button className="w-full px-2 py-3 text-left hover:bg-sunk transition-colors duration-150 motion-reduce:transition-none" onClick={() => { change({ maintenanceBuddyId: null }); setChoosing(false); }}>{t("maintenance.none")}</button></li>
        {candidates.filter(b => buddyQuad(b).toLowerCase().includes(query.trim().toLowerCase())).map(b => <li key={b.buddyId}><button className="w-full px-2 py-3 text-left font-mono text-xs wrap-anywhere hover:bg-sunk transition-colors duration-150 motion-reduce:transition-none" onClick={() => { change({ maintenanceBuddyId: b.buddyId }); setChoosing(false); }}>{buddyQuad(b)}</button></li>)}
        {candidates.length === 0 && <li className="px-2 py-3 text-xs text-ink3">{t("maintenance.noCandidates")}</li>}
      </ul></ScrollArea>
    </DialogContent></Dialog>
  </div>;
}
