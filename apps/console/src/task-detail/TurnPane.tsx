import { cn } from "cn";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { StateMark } from "@/components/ui/state-mark";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { reasonText } from "../i18n/reason";
import { formatDuration, formatRelative } from "../i18n/format";
import { Ask } from "./ask";
import { endingText, Origin, sessionHint } from "./facts";
import { Band, Facts, Fold, Lines, MomentRange, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import type { Step } from "./steps";
import { Node, Quote, Thread } from "./thread";
import type { Activity, Artifact, Basis, Context } from "./types";
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

/** The turn's sealed output: a band; opened, the evidence it is sealed by. */
function Seal({ artifact, ctx }: { artifact: Artifact; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const [open, setOpen] = useState(false);
  const integration = ctx.workflow.integrations.find(record => record.artifactId === artifact.artifactId);
  const patch = artifact.cumulativePatch;
  const short = (commit: string | null) => (commit ? commit.slice(0, 7) : null);
  const files = artifact.changedPaths.length;
  const unknown = t("unrecorded");
  return <Collapsible open={open} onOpenChange={setOpen} className="border-t">
    <CollapsibleTrigger className="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-2.5 gap-y-1 bg-sunk px-6 py-2.5 text-left text-xs">
      <span className="flex items-center gap-1 text-ink2"><Disclosure open={open} />{t("seal.name")}</span>
      <span className="min-w-0 font-mono wrap-anywhere">{short(artifact.outputCommit) ?? unknown}</span>
      <span className="whitespace-nowrap font-mono text-ink2">{patch?.baseCommit && artifact.outputCommit ? `${short(patch.baseCommit)} → ${short(artifact.outputCommit)}` : null}</span>
      <span className="col-start-2 col-span-2 text-ink2">
        {artifact.partial ? `${t("seal.partial")} · ` : artifact.kind === "resolved-output" ? `${t("seal.resolved")} · ` : null}
        {t("seal.files", { n: files >= 32 ? "32+" : files })}
      </span>
    </CollapsibleTrigger>
    <CollapsibleContent>
      <div className="space-y-3 px-6 py-3">
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
            ...(patch.changedPaths.length ? [[t("seal.paths"), <Lines key="paths" items={patch.changedPaths} className="font-mono text-xs" />] as [string, ReactNode]] : []),
          ]} />
        </div>}
        {integration && integration.hostPaths.length > 0 && <div>
          <p className="mb-1.5 text-xs text-ink3">{t("seal.host")}</p>
          <Lines items={integration.hostPaths} className="font-mono text-xs" />
        </div>}
      </div>
    </CollapsibleContent>
  </Collapsible>;
}

/** The row that stays under a running turn: what it is doing and on what ground that is known, and when it last said so. */
function Live({ activity, stuck, nowMs }: { activity: Activity | null; stuck: boolean; nowMs: number }) {
  const { t } = useTranslation("taskDetail");
  const heartbeat = !activity || activity.basis === "phase" || activity.basis === "unknown";
  const phase = heartbeat ? t("activity.unknown") : t(`activity.${activity.phase}`);
  const doing = !heartbeat && ["starting", "waiting-model", "streaming-model", "tool-running", "finishing"].includes(activity.phase);
  const at = activity?.observedAt ? Date.parse(activity.observedAt) : NaN;
  const ground = activity ? [
    t(`activity.basis.${activity.basis}`),
    activity.phase !== "unknown" && heartbeat ? t(`activity.${activity.phase}`) : null,
    activity.lastToolAt ? t("activity.lastTool", { when: formatRelative(Date.parse(activity.lastToolAt), nowMs) }) : null,
    activity.lastNativeAt ? t("activity.lastNative", { when: formatRelative(Date.parse(activity.lastNativeAt), nowMs) }) : null,
    activity.eventSeq !== null ? t("activity.event", { n: activity.eventSeq }) : null,
  ].filter(Boolean).join(" · ") : t("activity.basis.unknown");
  return <div data-slot="detail-live" className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t px-6 py-2.5 text-xs">
    <StateMark kind={doing ? "live" : "idle"} />
    <Hint text={ground}><span tabIndex={0} className={cn("rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50", doing ? "text-ink state-breathe" : "text-ink3")}>{phase}</span></Hint>
    {activity?.toolName && !heartbeat && <span className="min-w-0 font-mono text-ink2 wrap-anywhere">{activity.toolName}</span>}
    <span className={cn("ml-auto whitespace-nowrap tabular-nums", stuck ? "text-hand" : "text-ink3")}>
      {Number.isFinite(at) ? t("activity.reported", { when: formatRelative(at, nowMs) }) : t("activity.notReported")}
    </span>
  </div>;
}

/**
 * One turn of the micro task: who ran it and what it cost, then what happened in
 * it, top to bottom, and the output it sealed. What the Worker did inside the
 * session (reasoning, tool calls) is read from the session itself, which the
 * console cannot read yet, so the process holds no such node.
 */
export function TurnPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const shell = useTranslation("shell").t;
  const span = step.span!;
  const attemptId = span.attemptId ?? "";
  const turn = turnOf(ctx, attemptId);
  const attempt = ctx.view.attempt?.attemptId === attemptId ? ctx.view.attempt : null;
  const result = ctx.audit?.turns.find(item => item.attemptId === attemptId) ?? (ctx.workflow.currentTurn?.attemptId === attemptId ? ctx.workflow.currentTurn : null);
  const index = turn?.turnIndex ?? step.turnIndex;
  const continuation = index === 1 ? null : ctx.audit?.continuations.find(item => item.turnId === turn?.turnId) ?? null;
  const asked = ctx.asked[step.key] ?? [];
  const sealed = ctx.workflow.artifacts.filter(item => item.attemptId === attemptId);
  const running = step.outcome === "running";
  const failed = step.outcome === "failed";
  const rejected = turn?.state === "rejected";
  const unknown = step.outcome === "unknown";
  const reminders = { overdue: attempt?.overdue === true, possiblyStuck: attempt?.possiblyStuck === true };
  const expected = turn?.expectedSeconds ?? null;
  const buddy = turn?.buddy ?? span.buddy;
  const earliest = ctx.inquiries?.items[0] ? Date.parse(ctx.inquiries.items[0].createdAt) : null;
  const cut = ctx.inquiries?.more === true && earliest !== null && step.at !== null && step.at < earliest;
  const second = running && attempt?.activity
    ? <span className="inline-flex gap-x-3.5">
      <span>{t("activity.modelTurns", { n: attempt.activity.modelTurns ?? t("unknown") })}</span>
      <span>{t("activity.toolCalls", { n: attempt.activity.toolCalls ?? t("unknown") })}</span>
    </span>
    : <Usage usage={turn?.tokenUsage ?? null} />;
  const note = rejected ? [t("turn.quotaRefused"), t("turn.rerouted")].join(" · ")
    : [attempt?.releaseReason ? reasonText(attempt.releaseReason) : null, attempt?.ending ? endingText(attempt.ending, t) : null, failed && span.error ? span.error : null].filter(Boolean).join(" · ");
  return <Pane footer={running ? <Live activity={attempt?.activity ?? null} stuck={reminders.possiblyStuck} nowMs={ctx.nowMs} /> : undefined}>
    <PaneTitle title={t("step.turn", { n: index })}
      state={rejected ? t("turn.rejected") : running ? t("state.running") : failed ? t("state.failed") : unknown ? t("state.unknown") : t("state.ended")}
      tone={running ? "live" : rejected ? "past" : failed ? "fail" : unknown ? "hand" : "past"}
      warnings={[...(reminders.overdue ? [shell("reminders.overdue")] : []), ...(reminders.possiblyStuck ? [shell("reminders.possiblyStuck")] : [])]}
      note={note || undefined}>
      <MomentRange start={step.at} end={span.endAt}/>
      {expected !== null && <span className="tabular-nums text-ink3">{t("turn.expected", { time: formatDuration(expected * 1000) })}</span>}
      {step.ms !== null && <b className="text-sm font-normal text-foreground tabular-nums">{formatDuration(step.ms, "fine")}</b>}
    </PaneTitle>
    <Band role="Worker" identity={quad(buddy) ?? <span className="font-sans text-ink3">{t("unrecorded")}</span>}
      end={!turn?.routing ? <span className="text-ink3">{t("origin.unrecorded")}</span>
        : <Origin basis={basisOf(ctx, attemptId, turn.routing.decisionId)}>{
          turn.routing.decisionId === null ? (ctx.workflow.buddyLocked ? t("origin.locked") : t("origin.host"))
            : ctx.routing.find(entry => entry.decisionId === turn.routing!.decisionId)?.source === "single-candidate" ? t("origin.single") : null}</Origin>}
      second={second}
      secondEnd={<span className="inline-flex flex-wrap items-baseline gap-x-1.5">
        <span>{t("session.name")}</span>
        {turn?.sessionId ? <CopyableIdentifier value={turn.sessionId} hint={sessionHint(attempt?.session ?? null, t)} /> : <span>{t("session.unrecorded")}</span>}
        {turn && <span>· {turn.resumeMode === "native-session" ? t("session.resumed") : t("session.fresh")}</span>}
      </span>} />
    <Thread>
      {index === 1
        ? <Node mark="input"><Quote><Fold text={ctx.view.task.task} /></Quote></Node>
        : continuation && <Node mark="input"><Quote><Fold text={continuation.input} /></Quote></Node>}
      {cut && <p className="py-1.5 pl-8 text-xs text-ink3">{t("inquiry.more")}</p>}
      {asked.map(inquiry => <Ask key={inquiry.messageId} inquiry={inquiry} highlighted={ctx.highlightedInquiry === inquiry.inquiryId} />)}
      {!running && <Node mark="result">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink3">{t("result.name")}</p>
          {rejected ? <p className="text-ink2">{t("turn.rerouted")}</p> : result === null || result.summary === null
            ? <p className="text-ink3">{ctx.audit ? t("unrecorded") : t("loading")}</p>
            : <Fold text={result.summary || t("unrecorded")} className="text-foreground" />}
          {result && result.remaining.length > 0 && <Lines items={result.remaining} className="mt-2 text-ink2" />}
        </div>
      </Node>}
    </Thread>
    {sealed.map(artifact => <Seal key={artifact.artifactId} artifact={artifact} ctx={ctx} />)}
  </Pane>;
}
