import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { reasonText } from "../i18n/reason";
import { formatDuration } from "../i18n/format";
import { Ask } from "./ask";
import { BasisFold, endingText } from "./facts";
import { Band, Facts, Fold, Section, Lines, Paths, MomentRange, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import type { ContentTabs } from "./parts";
import type { Step } from "./steps";
import type { Artifact, Basis, Context } from "./types";
import { LiveActivity } from "./LiveActivity";
import { Usage } from "./usage";

/** The turn the span stands for: the compact record holds the newest, the audit all of them. */
function turnOf(ctx: Context, attemptId: string) {
  return ctx.workflow.turns.find(item => item.attemptId === attemptId) ?? ctx.audit?.turns.find(item => item.attemptId === attemptId) ?? null;
}

/** The bounds the turn's buddy was chosen within, where they are recorded. */
function basisOf(ctx: Context, attemptId: string, decisionId: string | null): Basis | null {
  if (decisionId !== null) return ctx.bases[decisionId] ?? null;
  return ctx.workflow.currentTurn?.attemptId === attemptId && ctx.workflow.routing?.decisionId === null ? ctx.workflow.routing.basis : null;
}

/** The sealed output shares the step's label column; opening it reveals the evidence. */
function Seal({ artifact, ctx }: { artifact: Artifact; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const [open, setOpen] = useState(false);
  const integration = ctx.workflow.integrations.find(record => record.artifactId === artifact.artifactId);
  const patch = artifact.cumulativePatch;
  const short = (commit: string | null) => (commit ? commit.slice(0, 7) : null);
  const files = artifact.changedPaths.length;
  const unknown = t("unrecorded");
  return <Section name={t("seal.name")}><Collapsible open={open} onOpenChange={setOpen}>
    <CollapsibleTrigger aria-label={`${t("seal.name")} · ${short(artifact.outputCommit) ?? unknown}`} className="flex w-full flex-wrap items-baseline gap-x-2.5 gap-y-1 text-left text-xs">
      <Disclosure open={open} className="self-center text-ink2" />
      <span className="min-w-0 font-mono wrap-anywhere">{short(artifact.outputCommit) ?? unknown}</span>
      <span className="whitespace-nowrap font-mono text-ink2">{patch?.baseCommit && artifact.outputCommit ? `${short(patch.baseCommit)} → ${short(artifact.outputCommit)}` : null}</span>
      <span className="text-ink2">
        {artifact.partial ? `${t("seal.partial")} · ` : artifact.kind === "resolved-output" ? `${t("seal.resolved")} · ` : null}
        {t("seal.files", { n: files >= 32 ? "32+" : files })}
      </span>
    </CollapsibleTrigger>
    <CollapsibleContent>
      <div className="space-y-3 py-3">
        <Facts items={[
          [t("seal.id"), artifact.artifactId, true],
          [t("seal.sha"), artifact.manifestSha256, true],
          ...(artifact.diffPath ? [[t("seal.diff"), artifact.diffPath, true] as [string, string, boolean]] : []),
          ...(artifact.partial ? [[t("seal.note"), t("seal.partialNote")] as [string, string]] : []),
          [t("seal.integration.name"), integration ? t(`seal.integration.${integration.state === "not-required" ? "notRequired" : "verified"}`) : t("seal.integration.none")],
        ]} />
        {patch && <div>
          <p className="mb-1.5 text-xs text-ink3">{t("seal.patch")}</p>
          <Facts items={[
            [t("seal.base"), patch.baseCommit ?? unknown, true], [t("seal.output"), patch.outputCommit ?? unknown, true],
            [t("seal.path"), patch.path ?? unknown, true], [t("seal.sha"), patch.sha256 ?? unknown, true],
            ...(patch.changedPaths.length ? [[t("seal.paths"), <Paths key="paths" items={patch.changedPaths} />] as [string, ReactNode]] : []),
          ]} />
        </div>}
        {integration && integration.hostPaths.length > 0 && <div>
          <p className="mb-1.5 text-xs text-ink3">{t("seal.host")}</p>
          <Paths items={integration.hostPaths} />
        </div>}
      </div>
    </CollapsibleContent>
  </Collapsible></Section>;
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
  const running = step.outcome === "running";
  const failed = step.outcome === "failed";
  const rejected = turn?.state === "rejected";
  const rejectionCode = turn?.providerRejection?.code;
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
  const note = rejected ? [t(`providerRejection.${rejectionCode!}`), t("turn.rerouted")].join(" · ")
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
    <Band role="Worker" identity={quad(buddy) ?? <span className="font-sans text-ink3">{t("unrecorded")}</span>}
      end={!turn?.routing ? <span className="text-ink3">{t("origin.unrecorded")}</span>
        : <span>{
          turn.routing.decisionId === null ? (ctx.workflow.buddyLocked ? t("origin.locked") : t("origin.host"))
            : ctx.routing.find(entry => entry.decisionId === turn.routing!.decisionId)?.source === "single-candidate" ? t("origin.single") : null}</span>}
      second={second}
      secondEnd={<span className="inline-flex flex-wrap items-baseline gap-x-1.5">
        {turn?.sessionId ? <CopyableIdentifier value={turn.sessionId} /> : <span>{t("session.unrecorded")}</span>}
        {turn && <span>· {turn.resumeMode === "native-session" ? t("session.resumed") : t("session.fresh")}</span>}
      </span>} />
    {asked.length > 0 && <Section name={t("delegation.inquiries")}>
      {cut && <p className="text-xs text-ink3">{t("inquiry.more")}</p>}
      {asked.map(inquiry => <Ask key={inquiry.messageId} inquiry={inquiry} />)}
    </Section>}
    {!running && <Section name={t("result.name")}>
      {rejected ? <p className="text-ink2">{t("turn.rerouted")}</p> : request
        ? <p>{t("turn.requestNext")}</p> : result === null || result.summary === null
          ? <p className="text-ink3">{ctx.audit ? t("unrecorded") : t("loading")}</p>
          : <Fold text={result.summary || t("unrecorded")} />}
      {!request && result && result.remaining.length > 0 && <Lines items={result.remaining} className="mt-2 text-ink2" />}
      {basis && <BasisFold basis={basis} />}
    </Section>}
    {sealed.map(artifact => <Seal key={artifact.artifactId} artifact={artifact} ctx={ctx} />)}
  </Pane>;
}
