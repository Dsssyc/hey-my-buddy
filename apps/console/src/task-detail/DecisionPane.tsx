import { useTranslation } from "react-i18next";
import { Section } from "@/components/section";
import { reasonText } from "../i18n/reason";
import { actorText } from "../actor";
import type { Actor } from "../types";
import { BasisFold } from "./facts";
import { Facts, Fold, Lines, LinkButton, Moment, Pane, PaneTitle } from "./parts";
import type { Step } from "./steps";
import type { AuditRequest, CompactRequest, Context, OpenRequest } from "./types";

type Known = (OpenRequest | AuditRequest) & { decision?: AuditRequest["decision"]; decisionId?: string | null };

/** The kind of a request is the name of the decision it asks for. */
function kindKey(request: CompactRequest): "routing" | "assistance" | "attention" | "helper-attention" | "helper-report" | "other" {
  if (request.routing) return "routing";
  const { kind } = request;
  return kind === "assistance" || kind === "attention" || kind === "helper-attention" || kind === "helper-report" ? kind : "other";
}

/**
 * A Host's decision: the request that was put to it and what was decided. A
 * continuation is the Host handing the micro task another turn. The blackboard's
 * own sentence for why it waits is not shown: the kind of the request says it.
 */
export function DecisionPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const { workflow, audit } = ctx;
  const event = step.events[0] ?? null;
  // A continuation names no request: the wait it ended does.
  const requestId = step.span?.requestId ?? event?.requestId ?? step.waits.find(wait => wait.requestId !== null)?.requestId ?? null;
  const compact = requestId ? workflow.requests.find(item => item.requestId === requestId) ?? null : null;
  const full: Known | null = requestId
    ? audit?.requests.find(item => item.requestId === requestId) ?? (workflow.activeRequest?.requestId === requestId ? workflow.activeRequest : null)
    : null;
  const request = full ?? compact;
  const continuation = step.kind === "continue" && event?.continuationId ? audit?.continuations.find(item => item.continuationId === event.continuationId) ?? null : null;
  const open = step.pending || request?.state === "open";
  const wait = t(ctx.view.lead ? "wait.lead" : "wait.host");
  const decided = request && request.state !== "open" ? request : null;
  const who: Actor | null = (full && "decision" in full ? full.decision?.actor : null) ?? event?.actor ?? null;
  const when = decided?.decidedAt ?? event?.at ?? null;
  const more = step.pending ? workflow.counts.openRequests - 1 : 0;
  const entry = full && "decisionId" in full && full.decisionId ? ctx.routing.find(item => item.decisionId === full.decisionId) ?? null : null;
  const currentBoundary = full?.routing && full.requestId === workflow.activeRequest?.requestId ? workflow.routing : null;
  const basis = currentBoundary?.basis ?? (entry ? ctx.bases[entry.decisionId] : null);
  const title = step.kind === "continue" ? t("step.continue") : request ? t(`decision.kind.${kindKey(request)}`) : t("step.decision");
  return <Pane>
    <PaneTitle title={title} state={open ? wait : undefined} tone="hand">
      {when && <Moment at={when} />}
    </PaneTitle>
    {full && <>
      <Section name={t("decision.summary")} className="border-t">
        <Fold text={full.reason ? reasonText(full.reason) : full.summary ?? t("unrecorded")} />
        {full.childTaskId && <p className="mt-2"><LinkButton hint={full.childTaskId} onClick={() => ctx.open(full.childTaskId!)}>{t("decision.child")}</LinkButton></p>}
        {more > 0 && <p className="mt-2 text-xs text-ink3">{t("decision.more", { n: more })}</p>}
        {full.preparationError && <p className="mt-2 text-fail"><code className="font-mono text-xs">{full.preparationError.code}</code> · {full.preparationError.message}</p>}
      </Section>
      {basis && <div className="border-t px-4 py-4 sm:px-6"><BasisFold basis={basis} /></div>}
      {full.attempted && <Section name={t("decision.attempted")}><Fold text={full.attempted} /></Section>}
      {full.neededWork.length > 0 && <Section name={t("decision.needed")}><Lines items={full.neededWork} /></Section>}
      {full.acceptance && <Section name={t("decision.acceptance")}><Fold text={full.acceptance} /></Section>}
      {full.expectedArtifacts.length > 0 && <Section name={t("decision.expected")}><Lines items={full.expectedArtifacts} /></Section>}
      {entry?.source && entry.source !== "model-selection" && <Section name={t("decision.source")}>{t(`routing.sources.${entry.source}`, { defaultValue: entry.source })}</Section>}
    </>}
    {!full && request && <Section name={t("decision.summary")} className="border-t"><p className="text-ink3">{audit ? t("unrecorded") : t("loading")}</p></Section>}
    {continuation && <Section name={t("decision.continuation")} className="border-t">
      <Facts items={[
        [t("decision.who"), who ? actorText(who) : t("unrecorded")],
        ...(continuation.note ? [[t("decision.reason"), continuation.note] as [string, string]] : []),
      ]} />
    </Section>}
    <Section name={t("decision.decided")} className={full || continuation ? undefined : "border-t"}>
      {open ? <p className="text-hand">{wait}</p> : <Facts items={[
        [t("decision.what"), step.kind === "continue" ? t("step.continue") : decided ? t(`decision.state.${decided.state}`, { defaultValue: decided.state }) : t("unrecorded")],
        [t("decision.who"), who ? actorText(who) : t("unrecorded")],
        [t("decision.when"), <Moment key="when" at={when} />],
        ...(full && "decision" in full && full.decision?.note ? [[t("decision.reason"), full.decision.note] as [string, string]] : []),
      ]} />}
    </Section>
  </Pane>;
}
