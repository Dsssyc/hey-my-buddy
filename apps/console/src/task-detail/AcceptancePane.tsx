import { reasonText } from "../i18n/reason";
import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { useContext } from "react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { Section } from "./parts";
import { actorText } from "../actor";
import { Facts, Fold, Paths, Moment, Pane, PaneTitle } from "./parts";
import type { Step } from "./steps";
import type { Context } from "./types";
import { DetailItem, DetailLinks } from "./linking";
import { eventDetails, eventName } from "../macro-task-display";

const FINAL_KINDS = ["output", "resolved-output"];
const short = (value: string | null, length = 8) => (value ? value.slice(0, length) : null);

/**
 * The end of a micro task: the output it was accepted on and the evidence that
 * it was integrated, the review that sent it back, and, for one that failed or
 * was cancelled, what the Host concluded, which never counts as acceptance.
 * Only exceptions are written: a stop that is not confirmed, or an ending that was
 * not the normal one.
 */
export function AcceptancePane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const links = useContext(DetailLinks)!;
  const { view, workflow, row } = ctx;
  const { task } = view;
  const unrecorded = t("unrecorded");
  const artifact = workflow.artifacts.find(item => item.artifactId === workflow.finalArtifactId && FINAL_KINDS.includes(item.kind)) ?? null;
  const integration = artifact ? workflow.integrations.find(item => item.artifactId === artifact.artifactId && ["verified", "not-required"].includes(item.state)) ?? null : null;
  const conclusion = workflow.hostConclusion;
  const accepted = !!(row?.acceptedAt ?? task.acceptedAt);
  const rejectedAt = row?.reviewRejectedAt ?? task.reviewRejectedAt;
  const rejection = row?.reviewRejectionNote ?? task.reviewRejectionNote;
  const settledAt = row?.acceptedAt ?? task.acceptedAt ?? rejectedAt ?? conclusion?.createdAt ?? null;
  const ending = view.attempt?.ending ?? null;
  const unconfirmed = workflow.shutdown && (!workflow.shutdown.selfConfirmed || workflow.shutdown.unconfirmedCount > 0);
  const state = step.pending ? t("wait.acceptance") : accepted ? t("state.accepted") : rejectedAt && !accepted ? t("acceptance.rejected") : t("state.ended");
  const keyFor = (kind: string, when: string | null | undefined) => {
    const event = step.events.find(event => event.kind === kind && (kind === "integrate"
      ? event.integrationId === integration?.integrationId : when && Date.parse(event.at) === Date.parse(when)));
    return event ? `event:${event.seq}` : undefined;
  };
  const actor = !accepted && !rejectedAt ? conclusion?.actor : step.events.find(event => event.kind === (accepted ? "accept" : "reject"))?.actor;
  const acceptanceKey = keyFor("accept", row?.acceptedAt ?? task.acceptedAt);
  const integrationKey = keyFor("integrate", integration?.createdAt);
  const rejectionKey = keyFor("reject", rejectedAt);
  const conclusionKey = keyFor("conclude", conclusion?.createdAt);
  const shown = new Set([acceptanceKey, artifact ? integrationKey : undefined, rejectedAt ? rejectionKey : undefined, conclusion ? conclusionKey : undefined]);
  const otherEvents = step.events.filter(event => links.items.has(`event:${event.seq}`) && !shown.has(`event:${event.seq}`));
  return <Pane title={
    <DetailItem itemKey={acceptanceKey} label={t("step.acceptance")}>
    <PaneTitle title={t("step.acceptance")} state={state} tone={step.pending ? "hand" : "past"}>
      {actor && <span>{actorText(actor)}</span>}
      {settledAt && <Moment at={settledAt} itemKey={accepted ? acceptanceKey : rejectedAt ? rejectionKey : conclusionKey} label={t("step.acceptance")} />}
    </PaneTitle>
    </DetailItem>}>
    {otherEvents.map(event => <DetailItem key={event.seq} itemKey={`event:${event.seq}`} label={eventName(event)} className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1 border-b px-6 py-3 text-xs text-ink2">
      <span>{eventName(event)}</span><Moment at={event.at} itemKey={`event:${event.seq}`} label={eventName(event)}/>
      {eventDetails(event) && <span className="min-w-0 wrap-anywhere">{eventDetails(event)}</span>}
    </DetailItem>)}
    {artifact && <Section name={t("acceptance.artifact")}>
      <Collapsible>
        <CollapsibleTrigger className="inline-flex max-w-full flex-wrap items-baseline gap-x-2 text-left text-sm">
          <Disclosure open={false} className="self-center [[data-state=open]>&]:rotate-90" />
          <code className="font-mono text-xs">{short(artifact.outputCommit, 12) ?? unrecorded}</code>
          <span className="text-ink3">· {t("seal.files", { n: artifact.changedPaths.length >= 32 ? "32+" : artifact.changedPaths.length })}</span>
        </CollapsibleTrigger>
        <CollapsibleContent><div className="pt-3"><Facts items={[
          [t("seal.commit"), artifact.outputCommit ? <CopyableIdentifier key="commit" value={artifact.outputCommit} /> : unrecorded],
          [t("seal.sha"), artifact.manifestSha256, true],
          [t("seal.paths"), <Paths key="paths" items={artifact.changedPaths} />],
        ]} /></div></CollapsibleContent>
      </Collapsible>
    </Section>}
    {artifact && <DetailItem itemKey={integrationKey} label={t("acceptance.integration")}><Section name={t("acceptance.integration")}>
      {integration ? <Collapsible>
        <CollapsibleTrigger className="inline-flex max-w-full flex-wrap items-baseline gap-x-2 text-left text-sm">
          <Disclosure open={false} className="self-center [[data-state=open]>&]:rotate-90" />
          <span>{t(integration.state === "not-required" ? "acceptance.notRequired" : "acceptance.verified")}</span>
          {integration.target?.ref && <code className="font-mono text-xs text-ink3">· {integration.target.ref}</code>}
        </CollapsibleTrigger>
        <CollapsibleContent><div className="space-y-3 pt-3">
          <Facts items={[
            ...(integration.state !== "not-required" ? [
              [t("acceptance.strategy"), integration.strategy] as [string, string],
              ...(integration.target ? [[t("acceptance.target"), `${integration.target.path} @ ${integration.target.ref}`, true] as [string, string, boolean]] : []),
              ...(integration.beforeCommit && integration.afterCommit ? [[t("acceptance.commits"), `${short(integration.beforeCommit)} → ${short(integration.afterCommit)}`, true] as [string, string, boolean]] : []),
            ] : []),
            [t("acceptance.recordedBy"), <span key="by">{integration.actor ? actorText(integration.actor) : unrecorded} · <Moment at={integration.createdAt} itemKey={integrationKey} label={t("acceptance.integration")} /></span>],
          ]} />
          {integration.note && <Fold text={integration.note} />}
          {integration.hostPaths.length > 0 && <div>
            <p className="mb-1.5 text-xs text-ink3">{t("acceptance.hostPaths", { n: integration.hostPaths.length })}</p>
            <Paths items={integration.hostPaths} />
          </div>}
        </div></CollapsibleContent>
      </Collapsible> : <p className="text-ink2">{t("acceptance.noIntegration")}</p>}
    </Section></DetailItem>}
    {accepted && view.acceptanceNote && <Section name={t("acceptance.note")}><Fold text={view.acceptanceNote} /></Section>}
    {rejectedAt && <DetailItem itemKey={rejectionKey} label={t("acceptance.rejection")} className="border-b last:border-b-0"><Section name={t("acceptance.rejection")}>
      <Facts items={[
        ...(rejectedAt !== settledAt ? [[t("acceptance.recordedAt"), <Moment key="at" at={rejectedAt} itemKey={rejectionKey} label={t("acceptance.rejection")} />] as [string, ReactNode]] : []),
        [t("acceptance.note"), rejection ? <Fold key="note" text={rejection} /> : unrecorded],
      ]} />
    </Section></DetailItem>}
    {conclusion && <DetailItem itemKey={conclusionKey} label={t("acceptance.conclusion")} className="border-b last:border-b-0"><Section name={t("acceptance.conclusion")}>
      <div className="space-y-3">
        <Facts items={[
          [t("acceptance.outcome"), `${t(`acceptance.status.${conclusion.executionStatus}`, { defaultValue: conclusion.executionStatus })} · ${t("acceptance.notCounted")}`],
          [t("acceptance.version"), conclusion.runRevision === null ? unrecorded : `V${conclusion.runRevision}`],
          ...(conclusion.attemptId ? [[t("acceptance.attempt"), conclusion.attemptId, true] as [string, string, boolean]] : []),
          ...(conclusion.artifactId ? [[t("acceptance.artifactRef"), conclusion.artifactId, true] as [string, string, boolean]] : []),
        ]} />
        <Fold text={conclusion.reason ? reasonText(conclusion.reason) : conclusion.note ?? unrecorded} />
      </div>
    </Section></DetailItem>}
    {(unconfirmed || ending?.cause) && <Section name={t("acceptance.exception")}>
      <ul className="space-y-1.5">
        {unconfirmed && <li className="text-hand">{t("acceptance.unconfirmed", { n: workflow.shutdown!.unconfirmedCount })}</li>}
        {ending?.cause && <li>{t("ending.cause", { cause: t(`termination.${ending.cause}`, { defaultValue: ending.cause }) })}</li>}
      </ul>
    </Section>}
  </Pane>;
}
