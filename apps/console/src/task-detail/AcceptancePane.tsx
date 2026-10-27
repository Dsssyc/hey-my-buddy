import { reasonText } from "../i18n/reason";
import { useTranslation } from "react-i18next";
import { useContext } from "react";
import { Section } from "@/components/section";
import { actorText } from "../actor";
import { Facts, Fold, Lines, Moment, Pane, PaneTitle } from "./parts";
import type { Step } from "./steps";
import type { Context } from "./types";
import { DetailItem, DetailLinks } from "./linking";
import { eventDetails, eventName } from "../macro-task-display";

const FINAL_KINDS = ["output", "resolved-output"];
const short = (value: string | null, length = 8) => (value ? value.slice(0, length) : null);
const ellipsis = (hash: string) => `${hash.slice(0, 8)}…${hash.slice(-4)}`;

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
  const acceptanceKey = keyFor("accept", row?.acceptedAt ?? task.acceptedAt);
  const integrationKey = keyFor("integrate", integration?.createdAt);
  const rejectionKey = keyFor("reject", rejectedAt);
  const conclusionKey = keyFor("conclude", conclusion?.createdAt);
  const shown = new Set([acceptanceKey, artifact ? integrationKey : undefined, rejectedAt ? rejectionKey : undefined, conclusion ? conclusionKey : undefined]);
  const otherEvents = step.events.filter(event => links.items.has(`event:${event.seq}`) && !shown.has(`event:${event.seq}`));
  return <Pane>
    <DetailItem itemKey={acceptanceKey} label={t("step.acceptance")}>
    <PaneTitle title={t("step.acceptance")} state={state} tone={step.pending ? "hand" : "past"}>
      {settledAt && <Moment at={settledAt} itemKey={accepted ? acceptanceKey : rejectedAt ? rejectionKey : conclusionKey} label={t("step.acceptance")} />}
    </PaneTitle>
    </DetailItem>
    {otherEvents.map(event => <DetailItem key={event.seq} itemKey={`event:${event.seq}`} label={eventName(event)} className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1 border-b px-6 py-3 text-xs text-ink2">
      <span>{eventName(event)}</span><Moment at={event.at} itemKey={`event:${event.seq}`} label={eventName(event)}/>
      {eventDetails(event) && <span className="min-w-0 wrap-anywhere">{eventDetails(event)}</span>}
    </DetailItem>)}
    {artifact && <Section name={t("acceptance.artifact")} className="border-t">
      <Facts items={[
        [t("seal.commit"), artifact.outputCommit ?? unrecorded, true],
        [t("acceptance.files"), String(artifact.changedPaths.length >= 32 ? "32+" : artifact.changedPaths.length)],
        [t("seal.sha"), ellipsis(artifact.manifestSha256), true],
      ]} />
    </Section>}
    {artifact && <DetailItem itemKey={integrationKey} label={t("acceptance.integration")} className="border-b last:border-b-0"><Section name={t("acceptance.integration")}>
      {integration ? <div className="space-y-3">
        <Facts items={[
          [t("acceptance.state"), integration.state === "not-required" ? t("acceptance.notRequired") : t("acceptance.verified")],
          ...(integration.state === "not-required" && integration.note ? [[t("acceptance.why"), integration.note] as [string, string]] : []),
          ...(integration.state !== "not-required" ? [
            [t("acceptance.strategy"), integration.strategy] as [string, string],
            ...(integration.target ? [[t("acceptance.target"), `${integration.target.path} @ ${integration.target.ref}`, true] as [string, string, boolean]] : []),
            ...(integration.beforeCommit && integration.afterCommit ? [[t("acceptance.commits"), `${short(integration.beforeCommit)} → ${short(integration.afterCommit)}`, true] as [string, string, boolean]] : []),
          ] : []),
          [t("acceptance.recordedBy"), <span key="by">{integration.actor ? actorText(integration.actor) : unrecorded} · <Moment at={integration.createdAt} itemKey={integrationKey} label={t("acceptance.integration")} /></span>],
        ]} />
        {integration.hostPaths.length > 0 && <div>
          <p className="mb-1.5 text-xs text-ink3">{t("acceptance.hostPaths", { n: integration.hostPaths.length })}</p>
          <Lines items={integration.hostPaths} className="font-mono text-xs" />
        </div>}
      </div> : <p className="text-ink2">{t("acceptance.noIntegration")}</p>}
    </Section></DetailItem>}
    {rejectedAt && <DetailItem itemKey={rejectionKey} label={t("acceptance.rejection")} className="border-b last:border-b-0"><Section name={t("acceptance.rejection")}>
      <Facts items={[
        [t("decision.when"), <Moment key="at" at={rejectedAt} itemKey={rejectionKey} label={t("acceptance.rejection")} />],
        [t("acceptance.note"), rejection ? <Fold key="note" text={rejection} /> : unrecorded],
      ]} />
    </Section></DetailItem>}
    {conclusion && <DetailItem itemKey={conclusionKey} label={t("acceptance.conclusion")} className="border-b last:border-b-0"><Section name={t("acceptance.conclusion")}>
      <div className="space-y-3">
        <Facts items={[
          [t("acceptance.outcome"), `${t(`acceptance.status.${conclusion.executionStatus}`, { defaultValue: conclusion.executionStatus })} · ${t("acceptance.notCounted")}`],
          [t("acceptance.recordedBy"), <span key="by">{conclusion.actor ? actorText(conclusion.actor) : unrecorded} · <Moment at={conclusion.createdAt} itemKey={conclusionKey} label={t("acceptance.conclusion")} /></span>],
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
