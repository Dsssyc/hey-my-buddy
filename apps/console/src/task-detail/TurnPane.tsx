import { cn } from "cn";
import { useState } from "react";
import type { ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { StateMark } from "@/components/ui/state-mark";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { formatDuration, formatRelative } from "../i18n/format";
import { actorRoleText } from "../actor";
import { activityOf, endingOf, remindersOf, sessionStorageOf } from "./evidence";
import type { Activity, Ending } from "./evidence";
import { Band, Facts, Fold, Lines, Moment, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import type { Step } from "./steps";
import { Node, Quote, Thread } from "./thread";
import type { Artifact, Context, Inquiry } from "./types";
import { Usage } from "./usage";

/** What the Worker's band says, at its right end, of how the buddy came to be the one. */
function Origin({ ctx, decisionId, recorded }: { ctx: Context; decisionId: string | null; recorded: boolean }) {
  const { t } = useTranslation("taskDetail");
  if (!recorded) return <span className="text-ink3">{t("origin.unrecorded")}</span>;
  if (decisionId === null) return <>{ctx.workflow.buddyLocked ? t("origin.locked") : t("origin.host")}</>;
  return ctx.routing.find(entry => entry.decisionId === decisionId)?.source === "single-candidate" ? <>{t("origin.single")}</> : null;
}

/** Why an execution did not end as it should: the cause, who stopped it and why, a native limit. */
function endingText(ending: Ending, t: TFunction<"taskDetail">): string {
  return [
    ending.cause ? t("ending.cause", { cause: t(`termination.${ending.cause}`, { defaultValue: ending.cause }) }) : null,
    ending.stoppedBy ? t(`stop.${ending.stoppedBy}`) : null,
    ending.actor ? `${actorRoleText(ending.actor.role)} · ${ending.actor.id}` : null,
    ending.reason,
    ending.quota ? t(`quota.${ending.quota}`) : null,
  ].filter(Boolean).join(" · ");
}

/** One inquiry put to the running execution, and the answer it got. */
function Ask({ inquiry }: { inquiry: Inquiry }) {
  const { t } = useTranslation("taskDetail");
  const word = <span className="whitespace-nowrap">{t(`inquiry.${inquiry.state}`)}</span>;
  return <>
    <Node mark="ask">
      <Quote>
        <div className="flex items-baseline gap-2 text-xs text-ink3">
          <span className="min-w-0">{t("inquiry.head", { who: inquiry.author })} · <Moment at={inquiry.createdAt} seconds /></span>
          <span className="ml-auto">{inquiry.reason && (inquiry.state === "discarded" || inquiry.state === "unavailable")
            ? <Hint text={inquiry.reason}><span tabIndex={0} className="rounded-sm underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{word}</span></Hint>
            : word}</span>
        </div>
        <Fold text={inquiry.question} />
      </Quote>
    </Node>
    {inquiry.answer && <Node mark="message">
      <div className="min-w-0 flex-1">
        <Fold text={inquiry.answer.text} className="text-foreground" />
        {inquiry.answer.truncated && <p className="text-xs text-ink3">{t("inquiry.truncated")}</p>}
      </div>
      <Moment at={inquiry.answer.at} seconds className="text-xs text-ink3" />
    </Node>}
  </>;
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
    <CollapsibleTrigger className="grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-2.5 gap-y-1 bg-sunk px-6 py-2.5 text-left text-xs outline-none transition-colors duration-150 hover:bg-live-wash focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset motion-reduce:transition-none">
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

/** The row that stays under a running turn: what it is doing, and when it last said so. */
function Live({ activity, stuck, nowMs }: { activity: Activity | null; stuck: boolean; nowMs: number }) {
  const { t } = useTranslation("taskDetail");
  const phase = !activity || activity.heartbeat ? t("activity.unknown") : t(`activity.${activity.phase}`);
  const doing = activity !== null && !activity.heartbeat && ["starting", "waiting-model", "streaming-model", "tool-running", "finishing"].includes(activity.phase);
  const at = activity?.observedAt ? Date.parse(activity.observedAt) : NaN;
  return <div data-slot="detail-live" className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t px-6 py-2.5 text-xs">
    <StateMark kind={doing ? "live" : "idle"} />
    <span className={doing ? "text-live" : "text-ink3"}>{phase}</span>
    {activity?.toolName && !activity.heartbeat && <span className="min-w-0 font-mono text-ink2 wrap-anywhere">{activity.toolName}</span>}
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
  const turn = ctx.workflow.turns.find(item => item.attemptId === attemptId) ?? null;
  const current = ctx.workflow.currentTurn?.attemptId === attemptId ? ctx.workflow.currentTurn : null;
  const audited = ctx.audit?.turns.find(item => item.turnId === (turn?.turnId ?? span.turnId)) ?? null;
  const first = (turn?.turnIndex ?? step.turnIndex) === 1 || step.turnIndex === 1;
  const continuation = first ? null : ctx.audit?.continuations.find(item => item.turnId === (turn?.turnId ?? span.turnId)) ?? null;
  const asked = (ctx.inquiries?.items ?? []).filter(item => item.attemptId === attemptId);
  const sealed = ctx.workflow.artifacts.filter(item => item.attemptId === attemptId && ["output", "resolved-output", "partial-output"].includes(item.kind));
  const running = step.outcome === "running";
  const reminders = remindersOf(ctx.task, attemptId);
  const activity = activityOf(ctx.task, attemptId);
  const ending = endingOf(ctx.task, attemptId);
  const storage = sessionStorageOf(ctx.task, attemptId);
  const summary = audited?.summary ?? current?.summary ?? null;
  const remaining = audited?.remaining ?? current?.remaining ?? [];
  const buddy = turn?.buddy ?? span.buddy;
  const sessionId = turn?.sessionId ?? null;
  const failed = step.outcome === "failed";
  const start = step.at;
  const hint = [
    storage?.scope ? t(`session.storage.${storage.scope}`, { defaultValue: storage.scope }) : t("session.storageUnknown"),
    storage?.visibility ? t(`session.visibility.${storage.visibility}`, { defaultValue: storage.visibility }) : t("session.visibilityUnknown"),
  ].join(" · ");
  const expected = turn?.expectedSeconds ?? null;
  const resume = turn ? (turn.resumeMode === "native-session" ? t("session.resumed") : t("session.fresh")) : null;
  return <Pane footer={running ? <Live activity={activity} stuck={reminders.possiblyStuck} nowMs={ctx.nowMs} /> : undefined}>
    <PaneTitle title={t("step.turn", { n: step.turnIndex })} state={running ? t("state.running") : failed ? t("state.failed") : t("state.ended")}
      tone={running ? "live" : failed ? "fail" : "past"}
      warnings={[...(reminders.overdue ? [shell("reminders.overdue")] : []), ...(reminders.possiblyStuck ? [shell("reminders.possiblyStuck")] : [])]}
      note={ending ? endingText(ending, t) : failed && span.error ? span.error : undefined}>
      <span className="font-mono tabular-nums">
        {start !== null ? <Moment at={start} seconds /> : t("unrecorded")} – {span.endAt ? <Moment at={span.endAt} seconds /> : null}
      </span>
      {running && expected !== null && <span className="tabular-nums text-ink3">{t("turn.expected", { time: formatDuration(expected * 1000) })}</span>}
      {step.ms !== null && <b className="text-sm font-normal text-foreground tabular-nums">{formatDuration(step.ms, true)}</b>}
    </PaneTitle>
    <Band role="Worker" identity={quad(buddy) ?? <span className="font-sans text-ink3">{t("unrecorded")}</span>}
      end={<Origin ctx={ctx} decisionId={turn?.routing?.decisionId ?? null} recorded={turn?.routing != null} />}
      second={running && activity
        ? <span className="inline-flex gap-x-3.5">
          <span>{t("activity.modelTurns", { n: activity.modelTurns ?? t("unknown") })}</span>
          <span>{t("activity.toolCalls", { n: activity.toolCalls ?? t("unknown") })}</span>
        </span>
        : <Usage usage={turn?.tokenUsage ?? null} />}
      secondEnd={<span className="inline-flex flex-wrap items-baseline gap-x-1.5">
        <span>{t("session.name")}</span>
        {sessionId ? <CopyableIdentifier value={sessionId} hint={hint} /> : <span>{t("session.unrecorded")}</span>}
        {resume && <span>· {resume}</span>}
      </span>} />
    <Thread>
      {first
        ? <Node mark="input"><Quote><Fold text={ctx.task.task} /></Quote></Node>
        : continuation && <Node mark="input"><Quote><Fold text={continuation.input} /></Quote></Node>}
      {asked.map(inquiry => <Ask key={inquiry.messageId} inquiry={inquiry} />)}
      {!running && <Node mark="result">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink3">{t("result.name")}</p>
          {summary === null ? <p className="text-ink3">{ctx.audit ? t("unrecorded") : t("loading")}</p> : <Fold text={summary || t("unrecorded")} className="text-foreground" />}
          {remaining.length > 0 && <Lines items={remaining} className="mt-2 text-ink2" />}
        </div>
      </Node>}
    </Thread>
    {ctx.inquiries?.more && asked.length > 0 && <p className="px-6 text-xs text-ink3">{t("inquiry.more")}</p>}
    {sealed.map(artifact => <Seal key={artifact.artifactId} artifact={artifact} ctx={ctx} />)}
  </Pane>;
}
