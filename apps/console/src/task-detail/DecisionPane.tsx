import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { reasonText } from "../i18n/reason";
import { actorText } from "../actor";
import type { Actor } from "../types";
import { BasisFold } from "./facts";
import { Facts, Fold, Lines, LinkButton, Moment, Pane, PaneTitle } from "./parts";
import { Quote } from "./thread";
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
  const who: Actor | null = step.kind === "continue" ? event?.actor ?? null
    : (full && "decision" in full ? full.decision?.actor : null) ?? event?.actor ?? null;
  const when = step.kind === "continue" ? event?.at ?? null : decided?.decidedAt ?? event?.at ?? null;
  const more = step.pending ? workflow.counts.openRequests - 1 : 0;
  const entry = full && "decisionId" in full && full.decisionId ? ctx.routing.find(item => item.decisionId === full.decisionId) ?? null : null;
  const currentBoundary = full?.routing && full.requestId === workflow.activeRequest?.requestId ? workflow.routing : null;
  const basis = currentBoundary?.basis ?? (entry ? ctx.bases[entry.decisionId] : null);
  const title = t(step.kind === "continue" ? "step.continue" : "step.decision");
  const decisionNote = step.kind === "decision" && full && "decision" in full ? full.decision?.note : null;
  return <Pane title={
    <PaneTitle title={title} state={open ? wait : decided && step.kind !== "continue" ? t(`decision.state.${decided.state}`, { defaultValue: decided.state }) : undefined} tone={open ? "hand" : "past"}>
      {who && <span>{actorText(who)}</span>}
      {when && <Moment at={when} itemKey={step.key} label={title} />}
    </PaneTitle>}>
    <div className="space-y-3 px-6 text-sm leading-[1.7]">
      {request && <Quote>
        <p data-slot="request-kind" className="mb-1 text-xs text-ink3">{t(`decision.kind.${kindKey(request)}`)}</p>
        {full ? <>
          <Fold text={full.reason ? reasonText(full.reason) : full.summary ?? t("unrecorded")} />
          <div className="mt-2"><Facts items={[
            ...(full.attempted ? [[t("decision.attempted"), <Fold key="attempted" text={full.attempted} />] as [string, ReactNode]] : []),
            ...(full.neededWork.length ? [[t("decision.needed"), <Lines key="needed" items={full.neededWork} />] as [string, ReactNode]] : []),
            ...(full.acceptance ? [[t("decision.acceptance"), <Fold key="acceptance" text={full.acceptance} />] as [string, ReactNode]] : []),
            ...(full.expectedArtifacts.length ? [[t("decision.expected"), <Lines key="expected" items={full.expectedArtifacts} />] as [string, ReactNode]] : []),
          ]} /></div>
          {full.childTaskId && <p className="mt-2"><LinkButton onClick={() => ctx.open(full.childTaskId!)}>{t("decision.child")}</LinkButton></p>}
          {more > 0 && <p className="mt-2 text-xs text-ink3">{t("decision.more", { n: more })}</p>}
          {full.preparationError && <p className="mt-2 text-fail"><code className="font-mono text-xs">{full.preparationError.code}</code> · {full.preparationError.message}</p>}
        </> : <p className="text-ink3">{audit ? t("unrecorded") : t("loading")}</p>}
        {basis && <BasisFold basis={basis} />}
        {entry?.source && entry.source !== "model-selection" && <p className="mt-2 text-xs text-ink3">{t(`routing.sources.${entry.source}`, { defaultValue: entry.source })}</p>}
      </Quote>}
      {continuation && <div data-slot="host-response" className="space-y-2">
        <Fold text={continuation.input} />
        {continuation.note && <Fold text={continuation.note} />}
      </div>}
      {decisionNote && <div data-slot="host-response"><Fold text={decisionNote} /></div>}
    </div>
  </Pane>;
}
