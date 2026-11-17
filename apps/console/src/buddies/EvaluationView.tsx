import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StateMark } from "@/components/ui/state-mark";
import type { ConsoleApi } from "../api";
import { harnessName } from "../buddy-display";
import { modelServiceKey, modelServices } from "../console-data";
import { formatList, formatNumber } from "../i18n/format";
import { ReasonMessage } from "../i18n/ReasonMessage";
import { Message } from "../i18n/Message";
import { BuddyIdentityName } from "../components/AliasTag";
import { UnderlineNav } from "../components/UnderlineNav";
import type { EvaluationStatistics, KindEvaluation, Model, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { HarnessLogo } from "./HarnessLogo";
import { DomainEvaluation, ModelPortrait } from "./EvaluationContent";
import { EvaluationNames } from "./EvaluationNames";
import { harnessBlocks } from "./models";
import { buddyHash } from "./navigation";
import type { BuddyLocation, EvaluationIdentity } from "./navigation";
import { RouterUsage, sumRouterUsage } from "./RouterUsage";
import { useProfileActions } from "./use-profile-actions";

const zero: EvaluationStatistics = { samples: 0, successes: 0, failures: 0, firstPasses: 0, reworkRounds: 0, firstPassRate: null, failureRate: null, medianDeliverySeconds: null,
  usage: { samples: 0, medianInputTokens: null, medianCachedInputTokens: null, medianOutputTokens: null } };
const key = ({ harness, modelId, effort }: EvaluationIdentity) => JSON.stringify([harness, modelId, effort]);

/** Disabled actions retain a hover and keyboard target for their Router setup hint. */
function ActionButton({ lock, description, disabled = false, children, onClick, variant = "outline" }: {
  lock?: React.ReactNode; description?: string; disabled?: boolean; children: ReactNode; onClick: () => void; variant?: "default" | "outline";
}) {
  return <Hint text={lock} description={description}><span className="inline-flex" tabIndex={lock ? 0 : undefined}><Button variant={variant} size="sm" aria-description={description ?? (typeof lock === "string" ? lock : undefined)} disabled={!!lock || disabled} onClick={onClick}>{children}</Button></span></Hint>;
}

/** Evaluation is shared by provider; profiles are shared by every use of the model. */
export function EvaluationView({ snapshot, editor, api, refresh, mutationsAvailable, location, onNavigate, hidden }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; mutationsAvailable: boolean;
  location: BuddyLocation; onNavigate: (location: BuddyLocation) => void; hidden: boolean;
}) {
  const { t } = useTranslation("buddies");
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const [lastSelection, setLastSelection] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<Model[] | null>(null);
  const data = editor.view;
  const actions = useProfileActions({ snapshot, editor, api, refresh, mutationsAvailable });
  const evaluations = new Map((data.kindEvaluations ?? []).map(row => [key(row), row]));
  const using = new Map<string, typeof data.buddies>();
  for (const buddy of data.buddies) {
    const correspondence = data.modelCorrespondences.find(row => modelServiceKey(row) === modelServiceKey(buddy));
    if (!correspondence) continue;
    const identity = { harness: buddy.harness, modelId: correspondence.modelId, effort: buddy.effort };
    const id = key(identity);
    using.set(id, [...(using.get(id) ?? []), buddy]);
    if (!evaluations.has(id)) {
      const model = data.models.find(row => row.modelId === identity.modelId)!;
      evaluations.set(id, { ...identity, modelDisplayName: model.displayName, modelNames: model.names.filter(name => name.harness === buddy.harness), previousGroups: [], summary: null,
        statistics: { overall: zero, byDomain: {}, byDomainTaskType: {}, infrastructureFailures: [] } });
    }
  }
  const previous = new Set([...evaluations.values()].flatMap(row => row.previousGroups.map(group => key({ ...row, modelId: group.modelId }))));
  const current = [...evaluations.values()].filter(row => !previous.has(key(row)));
  const rows = current.filter(row => (all || using.get(key(row))?.some(buddy => buddy.enabled)) && `${row.harness} ${row.modelDisplayName} ${row.effort} ${row.modelNames.filter(name => name.harness === row.harness).map(name => name.model).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));
  const explicit = location.view === "evaluation" ? location.evaluation : undefined;
  const selected = explicit ? evaluations.get(key(explicit)) : rows.find(row => key(row) === lastSelection) ?? rows[0];
  const selectedModel = data.models.find(row => row.modelId === selected?.modelId);
  const blocks = harnessBlocks({ listed: modelServices(data.buddies), data, recorded: snapshot, harnesses: snapshot.harnesses ?? [], folded: {}, fresh: new Set() });
  const harnesses = [...new Set([...blocks.map(block => block.harness), ...rows.map(row => row.harness)])];
  const models = data.models.filter(model => rows.some(row => row.modelId === model.modelId));
  const missing = models.filter(model => !model.profile && model.latestProfileRun?.state !== "running");
  const profileBuddy = snapshot.buddies.find(row => row.buddyId === snapshot.configuration?.profileBuddyId);
  const inProfile = location.view === "evaluation" && location.profile;
  const detailKey = JSON.stringify([selected ? key(selected) : null, !!inProfile]);
  const visible = useRef(!hidden);
  useEffect(() => { visible.current = !hidden; }, [hidden]);
  const [panel, setPanel] = useState({ key: detailKey, animated: false });
  if (panel.key !== detailKey) setPanel({ key: detailKey, animated: visible.current && !hidden });
  const providers = selected ? [...new Set((using.get(key(selected)) ?? []).map(buddy => buddy.provider))] : [];
  useEffect(() => { if (explicit) { setLastSelection(key(explicit)); if (!using.get(key(explicit))?.some(buddy => buddy.enabled)) setAll(true); setQuery(""); } }, [explicit?.harness, explicit?.modelId, explicit?.effort]);
  const select = (row: KindEvaluation, profile = false) => {
    setLastSelection(key(row));
    onNavigate({ view: "evaluation", evaluation: { harness: row.harness, modelId: row.modelId, effort: row.effort }, ...(profile ? { profile: true } : {}) });
  };
  const maintenanceLock = actions.maintenanceLock ?? (snapshot.maintenance?.runs.some(run => run.state === "running") ? t("modelProfile.state.running") : undefined);
  return <><section hidden={hidden} aria-label={t("sections.evaluation")} className="view-presence flex min-h-0 min-w-0 flex-1 bg-card">
    <aside className="flex w-[310px] shrink-0 flex-col border-r" data-slot="evaluation-list">
      <div className="space-y-4 border-b px-5 py-4 text-xs">
        <div className="flex items-center justify-between gap-2"><span className="text-ink2">{t("evaluation.profileCount", { built: models.filter(model => model.profile).length, total: models.length })}</span><ActionButton lock={actions.profileLock} description={actions.profileLockDescription} disabled={!missing.length} onClick={() => setConfirmation(missing)}>{t("evaluation.batchBuild")}</ActionButton></div>
        <div className="flex items-center justify-between gap-2"><span className="text-ink2">{t("evaluation.pendingMaintenance", { n: snapshot.maintenance?.pendingCount ?? 0 })}</span><ActionButton lock={maintenanceLock} disabled={!snapshot.maintenance?.pendingCount} onClick={() => void actions.maintain()}>{t("maintenance.runNow")}</ActionButton></div>
      </div>
      <div className="space-y-3 border-b px-5 py-4"><Input type="search" className="h-8" aria-label={t("evaluation.search")} placeholder={t("evaluation.search")} value={query} onChange={event => setQuery(event.target.value)} />
        <label className="flex items-center gap-2 text-xs text-ink2"><Checkbox checked={all} onCheckedChange={value => setAll(value === true)} />{t("evaluation.showAll")}</label>
      </div>
      <ScrollArea className="min-h-0 flex-1"><nav aria-label={t("evaluation.list")} className="py-3">
        {!rows.length && <p className="px-5 py-3 text-xs text-ink3">{t("evaluation.noMatches")}</p>}
        {harnesses.map(harness => {
          const group = rows.filter(row => row.harness === harness);
          return group.length > 0 && <section key={harness} className="mb-3"><h2 className="flex items-center gap-2 px-5 py-2 text-sm font-medium"><HarnessLogo harness={harness} />{harnessName(harness)}</h2>
            {group.map(row => <a key={key(row)} href={buddyHash({ view: "evaluation", evaluation: row })} aria-current={selected && key(selected) === key(row) ? "page" : undefined} className="flex items-center gap-2 px-5 py-3 text-xs outline-none transition-colors duration-150 hover:bg-sunk focus-visible:ring-2 focus-visible:ring-ring aria-[current=page]:bg-past-wash motion-reduce:transition-none" onClick={event => { event.preventDefault(); select(row); }}>
              <span className="min-w-0 flex-1 wrap-anywhere">{row.modelDisplayName} / {row.effort}</span>{!data.models.find(model => model.modelId === row.modelId)?.profile && <Hint text={t("modelProfile.notBuilt")}><span aria-label={t("modelProfile.notBuilt")} className="size-2 shrink-0 rounded-full border border-ink2" /></Hint>}<span className="text-ink3 tabular-nums">{formatNumber(row.statistics.overall.samples)}</span>
            </a>)}
          </section>;
        })}
      </nav></ScrollArea>
    </aside>
    <ScrollArea className="min-h-0 min-w-0 flex-1"><div data-slot="evaluation-detail" className="min-w-0 px-8 pt-6 pb-10">
      {selected && selectedModel ? <>
        <header className="flex flex-wrap items-baseline gap-x-5 gap-y-2"><h2 className="text-lg font-semibold">{selected.harness} / {selected.modelDisplayName} / {selected.effort}</h2>{providers.length > 0 && <span className="text-xs text-ink3">{t("evaluation.providers", { names: formatList(providers) })}</span>}</header>
        <UnderlineNav aria-label={t("evaluation.detailTabs")} className="mb-6 mt-5 flex gap-6 border-b text-sm">{[false, true].map(profile => <a key={String(profile)} href={buddyHash({ view: "evaluation", evaluation: selected, ...(profile ? { profile: true } : {}) })} aria-current={!!inProfile === profile ? "page" : undefined} className="relative pb-2 text-ink2 outline-none aria-[current=page]:text-foreground aria-[current=page]:after:absolute aria-[current=page]:after:inset-x-0 aria-[current=page]:after:bottom-0 aria-[current=page]:after:h-0.5 aria-[current=page]:after:bg-foreground focus-visible:ring-2 focus-visible:ring-ring" onClick={event => { event.preventDefault(); select(selected, profile); }}>{t(profile ? "modelProfile.title" : "evaluation.domains")}</a>)}</UnderlineNav>
        <div key={detailKey} className={panel.animated ? "panel-enter" : undefined}>
        {inProfile ? <ModelPortrait model={selectedModel}>
          <ActionButton lock={actions.profileLock} description={actions.profileLockDescription} disabled={selectedModel.latestProfileRun?.state === "running"} onClick={() => setConfirmation([selectedModel])}>{t(selectedModel.latestProfileRun?.state === "running" ? "modelProfile.state.running" : selectedModel.profile ? "modelProfile.rebuild" : "modelProfile.build")}</ActionButton>
          {selectedModel.latestProfileRun?.state === "failed" && <span className="ml-3 text-xs text-fail">{t("evaluation.buildFailedShort")}</span>}
        </ModelPortrait> : <DomainEvaluation key={key(selected)} evaluation={selected} evaluations={[...evaluations.values()]} domains={data.domainNodes ?? []}><EvaluationNames evaluation={selected} data={data} editor={editor} /></DomainEvaluation>}
        </div>
      </> : <p className="text-sm text-ink3">{t("evaluation.noEvidence")}</p>}
    </div></ScrollArea>
    <Dialog open={confirmation !== null} onOpenChange={open => { if (!open) setConfirmation(null); }}><DialogContent>
      <DialogTitle>{t("evaluation.confirmBuild")}</DialogTitle><DialogDescription><Message ns="buddies" id="modelProfile.quota" values={{ name: profileBuddy ? <BuddyIdentityName buddy={profileBuddy} /> : "" }} /></DialogDescription>
      <ul className="max-h-64 list-inside list-disc overflow-auto text-sm">{confirmation?.map(model => <li key={model.modelId}>{model.displayName}</li>)}</ul><p className="text-sm">{t("evaluation.buildCount", { n: confirmation?.length ?? 0 })}</p>
      <DialogFooter><Button variant="outline" onClick={() => setConfirmation(null)}>{t("evaluation.cancel")}</Button><Hint text={actions.profileLock} description={actions.profileLockDescription}><Button disabled={!!actions.profileLock} onClick={() => { const batch = confirmation!; setConfirmation(null); void actions.build(batch); }}>{t("evaluation.confirm")}</Button></Hint></DialogFooter>
    </DialogContent></Dialog>
  </section>
    <div className="fixed right-6 bottom-16 z-40 flex max-w-lg flex-col gap-2" data-slot="profile-notices">{actions.notices.map(notice => <div key={notice.id} role="status" data-slot="profile-notice" className="banner-enter overlay-surface border bg-popover p-4 text-xs shadow-md">
      <p className="flex items-center gap-2">{notice.run && <StateMark kind={notice.run.state === "failed" ? "fail" : "done"} />}<strong>{notice.model}</strong>{notice.run ? t(`modelProfile.state.${notice.run.state}`) : t("evaluation.buildFailedShort")}</p>
      {notice.run?.reason && <p className="mt-2 text-fail"><ReasonMessage reason={notice.run.reason} /></p>}{notice.message && <p className="mt-2">{notice.message()}</p>}
      {notice.run && <div className="mt-2 flex flex-wrap items-center gap-3"><RouterUsage usage={sumRouterUsage(notice.run.usage?.attempts ?? [])}
        label={`${notice.model} · ${t("routerProfiles.tokens")}`} /><a className="underline underline-offset-2" href={buddyHash({ view: "router", target: "profiles", profileRunId: notice.run.profileRunId })} onClick={event => { event.preventDefault(); onNavigate({ view: "router", target: "profiles", profileRunId: notice.run!.profileRunId }); }}>{t("evaluation.viewRun")}</a></div>}
    </div>)}</div>
  </>;
}
