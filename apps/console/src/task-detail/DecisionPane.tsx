import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ReasonMessage } from "../i18n/ReasonMessage";
import { actorText } from "../actor";
import type { Actor } from "../types";
import { BasisFold } from "./facts";
import { Facts, Fold, Lines, Moment, Pane, PaneTitle } from "./parts";
import { Quote } from "./thread";
import type { Step } from "./steps";
import type { Context } from "./types";
import { InputFailureFacts } from "./InputFailureFacts";

/** The boundary and the response recorded for this step. */
export function DecisionPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const { workflow, audit } = ctx;
  const event = step.events[0] ?? null;
  const { requestId } = step;
  const compact = requestId ? workflow.requests.find(item => item.requestId === requestId) ?? null : null;
  const recorded = audit?.requests.find(item => item.requestId === requestId);
  const full = recorded ?? (workflow.activeRequest?.requestId === requestId ? workflow.activeRequest : null);
  const resolution = step.kind === "decision" ? recorded?.resolution : null;
  const conflict = resolution && workflow.workspaceConflicts.find(item => item.conflictId === resolution.conflictId);
  const action = resolution?.action ?? conflict?.action;
  const request = full ?? compact;
  const continuation = step.kind === "continue" && event?.continuationId ? audit?.continuations.find(item => item.continuationId === event.continuationId) ?? null : null;
  const open = step.pending || request?.state === "open";
  const wait = t(ctx.view.lead ? "wait.lead" : "wait.host");
  const decided = request && request.state !== "open" ? request : null;
  const who: Actor | null = step.kind === "continue" ? event?.actor ?? null
    : resolution?.actor ?? event?.actor ?? conflict?.actor ?? null;
  const when = step.kind === "continue" ? event?.at ?? null : decided?.decidedAt ?? event?.at ?? null;
  const more = step.pending ? workflow.counts.openRequests - 1 : 0;
  const entry = request?.decisionId ? ctx.routing.find(item => item.decisionId === request.decisionId) ?? null : null;
  const currentBoundary = full?.routing && full.requestId === workflow.activeRequest?.requestId ? workflow.routing : null;
  const basis = currentBoundary?.basis ?? (entry ? ctx.bases[entry.decisionId] : null);
  const title = t(step.kind === "continue" ? "step.continue" : "step.decision");
  const resolutionNote = resolution ? resolution.note ?? (resolution.action === null ? conflict?.note : null) : null;
  const currentRequest = requestId !== null && requestId === workflow.activeRequest?.requestId;
  const inputFailure = full?.inputFailure ?? compact?.inputFailure ?? (currentRequest ? ctx.view.inputFailure : undefined);
  const reason = full?.reason ?? compact?.reason ?? (currentRequest ? ctx.view.requestReason : null);
  return <Pane title={
    <PaneTitle title={title} state={open ? wait : decided && step.kind !== "continue" ? t(`decision.state.${decided.state}`, { defaultValue: decided.state }) : undefined} tone={open ? "hand" : "past"}>
      {who && <span>{actorText(who)}</span>}
      {when && <Moment at={when} itemKey={step.key} label={title} />}
    </PaneTitle>}>
    <div className="space-y-3 px-6 text-sm leading-[1.7]">
      {request && <Quote>
        <p data-slot="request-kind" className="mb-1 text-xs text-ink3">{t(request.routing ? "decision.kind.routing" : reason?.code === "workspace-scope-violation" ? "decision.kind.workspace" : "decision.kind.attention")}</p>
        {full ? <>
          {inputFailure ? <InputFailureFacts failure={inputFailure} /> : <Fold text={reason ? "" : full.summary ?? t("unrecorded")}>{reason && <ReasonMessage reason={reason} />}</Fold>}
          <div className="mt-2"><Facts items={[
            ...(full.attempted ? [[t("decision.attempted"), <Fold key="attempted" text={full.attempted} />] as [string, ReactNode]] : []),
            ...(full.neededWork.length ? [[t("decision.needed"), <Lines key="needed" items={full.neededWork} />] as [string, ReactNode]] : []),
            ...(full.acceptance ? [[t("decision.acceptance"), <Fold key="acceptance" text={full.acceptance} />] as [string, ReactNode]] : []),
            ...(full.expectedArtifacts.length ? [[t("decision.expected"), <Lines key="expected" items={full.expectedArtifacts} />] as [string, ReactNode]] : []),
          ]} /></div>
          {more > 0 && <p className="mt-2 text-xs text-ink3">{t("decision.more", { n: more })}</p>}
          {full.preparationError && <p className="mt-2 text-fail"><code className="font-mono text-xs">{full.preparationError.code}</code> · {full.preparationError.message}</p>}
        </> : inputFailure ? <InputFailureFacts failure={inputFailure} /> : <p className="text-ink3">{reason ? <ReasonMessage reason={reason} /> : audit ? t("unrecorded") : t("loading")}</p>}
        {basis && <BasisFold basis={basis} />}
        {entry?.source && entry.source !== "model-selection" && <p className="mt-2 text-xs text-ink3">{t(`routing.sources.${entry.source}`, { defaultValue: entry.source })}</p>}
      </Quote>}
      {continuation && <div data-slot="host-response" className="space-y-2">
        <Fold text={continuation.input} />
        {continuation.note && <Fold text={continuation.note} />}
      </div>}
      {resolution && <div data-slot="workspace-resolution" className="space-y-2">
        <Facts items={[
          ...(action ? [[t("decision.resolution.action"), t(`decision.resolution.actions.${action}`)] as [string, string]] : []),
          [t("decision.resolution.result"), t(`decision.resolution.states.${resolution.state}`)],
        ]} />
        {resolutionNote && <Fold text={resolutionNote} />}
        {resolution.continuationBlocked && <p className="text-ink3">{t("decision.resolution.blocked")}</p>}
      </div>}
    </div>
  </Pane>;
}
