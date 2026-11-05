import { useTranslation } from "react-i18next";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { reasonText } from "../i18n/reason";
import { formatDuration } from "../i18n/format";
import { Ask } from "./ask";
import { BasisFold, endingText } from "./facts";
import { Band, Facts, Fold, Section, Lines, MomentRange, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import type { ContentTabs } from "./parts";
import type { Step } from "./steps";
import type { Basis, Context } from "./types";
import { DeliveryEvidence } from "./DeliveryEvidence";
import { Hint } from "@/components/hint";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { LiveActivity } from "./LiveActivity";
import { Usage } from "./usage";
import { InputFailureFacts } from "./InputFailureFacts";

/** The turn the span stands for: the compact record holds the newest, the audit all of them. */
function turnOf(ctx: Context, attemptId: string) {
  return ctx.workflow.turns.find(item => item.attemptId === attemptId) ?? ctx.audit?.turns.find(item => item.attemptId === attemptId) ?? null;
}

/** The bounds the turn's buddy was chosen within, where they are recorded. */
function basisOf(ctx: Context, attemptId: string, decisionId: string | null): Basis | null {
  if (decisionId !== null) return ctx.bases[decisionId] ?? null;
  return ctx.workflow.currentTurn?.attemptId === attemptId && ctx.workflow.routing?.decisionId === null ? ctx.workflow.routing.basis : null;
}

/**
 * One turn of the micro task: who ran it and what it cost, then what happened in
 * it and the output it sealed. Reasoning and tool calls belong to the session
 * tab; this overview keeps the inquiries and returned result.
 */
export function TurnPane({ step, ctx, tabs }: { step: Step; ctx: Context; tabs?: ContentTabs }) {
  const { t } = useTranslation("taskDetail");
  const shell = useTranslation("shell").t;
  const span = step.span!;
  const attemptId = span.attemptId ?? "";
  const turn = turnOf(ctx, attemptId);
  const attempt = ctx.view.attempt?.attemptId === attemptId ? ctx.view.attempt : null;
  const result = ctx.audit?.turns.find(item => item.attemptId === attemptId) ?? (ctx.workflow.currentTurn?.attemptId === attemptId ? ctx.workflow.currentTurn : null);
  const index = turn?.turnIndex ?? step.turnIndex;
  const asked = ctx.asked[step.key] ?? [];
  const sealed = ctx.workflow.artifacts.filter(item => item.attemptId === attemptId);
  const fixed = sealed.find(item => item.kind === "result") ?? null;
  const none = turn?.checkout === "none";
  const running = step.outcome === "running";
  const failed = step.outcome === "failed";
  const rejected = turn?.state === "rejected";
  const rejectionCode = turn?.providerRejection?.code;
  const inputFailure = turn?.inputFailure ?? result?.inputFailure ?? (attempt ? ctx.view.inputFailure : undefined);
  const unknown = step.outcome === "unknown";
  const reminders = { overdue: attempt?.overdue === true, possiblyStuck: attempt?.possiblyStuck === true };
  const expected = turn?.expectedSeconds ?? null;
  const buddy = turn?.buddy ?? span.buddy;
  const earliest = ctx.inquiries?.items[0] ? Date.parse(ctx.inquiries.items[0].createdAt) : null;
  const cut = ctx.inquiries?.more === true && earliest !== null && step.at !== null && step.at < earliest;
  const basis = turn?.routing ? basisOf(ctx, attemptId, turn.routing.decisionId) : null;
  const request = ["assistance", "attention"].includes(result?.disposition ?? "");
  const second = running && attempt?.activity
    ? <span className="inline-flex gap-x-3.5">
      <span>{t("activity.modelTurns", { n: attempt.activity.modelTurns ?? t("unknown") })}</span>
      <span>{t("activity.toolCalls", { n: attempt.activity.toolCalls ?? t("unknown") })}</span>
    </span>
    : <Usage usage={turn?.tokenUsage ?? null} />;
  const note = inputFailure ? t(`inputFailure.stage.${inputFailure.stage}`)
    : rejected && rejectionCode ? [t(`providerRejection.${rejectionCode}`), t("turn.rerouted")].join(" · ")
    : [attempt?.releaseReason ? reasonText(attempt.releaseReason) : null,
      attempt?.ending ? endingText(attempt.ending, t) : rejectionCode ? t(`providerRejection.${rejectionCode}`) : null,
      failed && span.error ? span.error : null].filter(Boolean).join(" · ");
  return <Pane tabs={tabs} footer={running ? <LiveActivity className="border-t px-6 py-2.5" activity={attempt?.activity ?? null} stuck={reminders.possiblyStuck} nowMs={ctx.nowMs} /> : undefined} title={
    <PaneTitle title={t("step.turn", { n: index })}
      state={rejected ? t("turn.rejected") : running ? t("state.running") : failed ? t("state.failed") : unknown ? t("state.unknown") : t("state.ended")}
      tone={running ? "live" : rejected ? "past" : failed ? "fail" : unknown ? "hand" : "past"}
      warnings={[...(reminders.overdue ? [shell("reminders.overdue")] : []), ...(reminders.possiblyStuck ? [shell("reminders.possiblyStuck")] : [])]}
      note={note || undefined}>
      <MomentRange start={step.at} end={span.endAt} itemKey={step.key} label={t("step.turn", { n: index })}/>
      {expected !== null && <span className="tabular-nums text-ink3">{t("turn.expected", { time: formatDuration(expected * 1000) })}</span>}
      {step.ms !== null && <b className="text-sm font-normal text-foreground tabular-nums">{formatDuration(step.ms, "fine")}</b>}
    </PaneTitle>}>
    {!none && <Band role="Worker" identity={quad(buddy) ?? <span className="font-sans text-ink3">{t("unrecorded")}</span>}
      end={!turn?.routing ? <span className="text-ink3">{t("origin.unrecorded")}</span>
        : <span>{
          turn.routing.decisionId === null ? t("origin.host")
            : ctx.routing.find(entry => entry.decisionId === turn.routing!.decisionId)?.source === "single-candidate" ? t("origin.single") : null}</span>}
      second={second}
      secondEnd={<span className="inline-flex flex-wrap items-baseline gap-x-1.5">
        {turn?.sessionId ? <CopyableIdentifier value={turn.sessionId} /> : <span>{t("session.unrecorded")}</span>}
        {turn && <span>· {turn.resumeMode === "native-session" ? t("session.resumed") : t("session.fresh")}</span>}
      </span>} />}
    {none && <>
      <Section name={t("delivery.access")}>{turn.access}</Section>
      <Section name={t("delivery.cwd")}><code>{ctx.workflow.cwd ?? ctx.view.task.cwd}</code></Section>
      <Section name={t("delivery.checkout")}>none</Section>
      {(turn.access === "write" || turn.access === "full") && <Section name={t("delivery.changes")}>
        <Hint delayFocus text={t("delivery.noCopy")}><button type="button" className="text-left text-sm text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring">{t("delivery.originalFolder")}</button></Hint>
      </Section>}
    </>}
    {inputFailure && <Section name={t("inputFailure.name")}><InputFailureFacts failure={inputFailure} /></Section>}
    {asked.length > 0 && <Section name={t("delegation.inquiries")}>
      {cut && <p className="text-xs text-ink3">{t("inquiry.more")}</p>}
      {asked.map(inquiry => <Ask key={inquiry.messageId} inquiry={inquiry} />)}
    </Section>}
    {!none && !running && <Section name={t("result.name")}>
      {rejected ? <p className="text-ink2">{t("turn.rerouted")}</p> : request
        ? <p>{t("turn.requestNext")}</p> : result === null || result.summary === null
          ? <p className="text-ink3">{ctx.audit ? t("unrecorded") : t("loading")}</p>
          : <Fold text={result.summary || t("unrecorded")} />}
      {!request && result && result.remaining.length > 0 && <Lines items={result.remaining} className="mt-2 text-ink2" />}
      {basis && <BasisFold basis={basis} />}
    </Section>}
    {sealed.map(artifact => <DeliveryEvidence key={artifact.artifactId} artifact={artifact} />)}
    {none && fixed && <Section name={t("delivery.execution")}><Collapsible>
      <CollapsibleTrigger className="flex items-center gap-2 text-left text-sm"><Disclosure open={false} className="text-ink2 [[data-state=open]>&]:rotate-90"/>{fixed.runId}</CollapsibleTrigger>
      <CollapsibleContent><Facts items={[
        [t("delivery.runId"), fixed.runId!, true], [t("delivery.turnId"), turn!.turnId, true],
        [t("delivery.attemptId"), fixed.attemptId, true], [t("delivery.generation"), String(fixed.generation)],
      ]}/></CollapsibleContent>
    </Collapsible></Section>}
    {none && !running && <Section name={t("delivery.content")}>
      {result?.summary === null || !result ? <p className="text-ink3">{ctx.audit ? t("unrecorded") : t("loading")}</p> : <Fold text={result.summary || t("unrecorded")} />}
      {result && result.remaining.length > 0 && <Lines items={result.remaining} className="mt-2 text-ink2" />}
    </Section>}
  </Pane>;
}
