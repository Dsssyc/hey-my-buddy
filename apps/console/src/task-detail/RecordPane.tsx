import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { Section } from "@/components/section";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { taskHost } from "../console-data";
import { endingText, sessionHint } from "./facts";
import { Band, Facts, Fold, LinkButton, Pane } from "./parts";
import { quad } from "./names";
import type { Step } from "./steps";
import { Node, Quote, Thread } from "./thread";
import type { TaskView } from "./types";
import { Usage } from "./usage";

/**
 * A run with no collaboration record: a command a Host ran, or a Router's own
 * calculation. It has one step. A command's band names no buddy, because there
 * is none; a calculation's names the Router that was asked. How it ended is
 * written as a turn's is.
 */
export function RecordPane({ step, view, onOpenRun }: { step: Step; view: TaskView; onOpenRun: (runId: string, macroTaskId?: string) => void }) {
  const { t } = useTranslation("taskDetail");
  const { task, attempt } = view;
  const calculation = step.kind === "calculation";
  const routing = calculation ? view.routing : null;
  const note = attempt?.ending ? endingText(attempt.ending, t) : "";
  const source = calculation ? task.lineage?.routingTask : null;
  return <Pane>
    <Band role={calculation ? "Router" : t("record.command")} plain={!calculation}
      identity={calculation ? quad(task.lineage?.buddy) ?? undefined : undefined}
      end={routing?.routingMode ? t(`routing.mode.${routing.routingMode}`) : undefined}
      second={<Usage usage={attempt?.tokenUsage ?? null} />}
      secondEnd={attempt?.session?.id ? <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
        <span>{t("session.name")}</span><CopyableIdentifier value={attempt.session.id} hint={sessionHint(attempt.session, t)} />
      </span> : undefined} />
    {note && <p className="px-6 pt-4 text-xs text-ink2 wrap-anywhere">{note}</p>}
    <Thread>
      {!calculation && <Node mark="input"><Quote><Fold text={task.task} /></Quote></Node>}
      <Node mark="result">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink3">{routing ? t("routing.chosen") : t("result.name")}</p>
          {routing ? <>
            <p className="font-mono text-xs wrap-anywhere">{quad(routing.selectedBuddy) ?? t("unrecorded")}</p>
            <div className="mt-1"><Fold text={routing.reason ?? t("unrecorded")} className="text-foreground" limit={240} /></div>
          </> : <Fold text={attempt?.finalText ?? t("unrecorded")} className="text-foreground" />}
        </div>
      </Node>
    </Thread>
    <Section name={t("delegation.source")} className="border-t [&>div:first-child]:text-xs min-[640px]:[&_dl]:grid-cols-[72px_minmax(0,1fr)]">
      <Facts items={[
        ...(source ? [[t("record.microTask"), <LinkButton key="micro-task" hint={source.titleSource === "first-line" ? t("title.fromTask", { title: source.title }) : undefined} onClick={() => onOpenRun(source.runId, source.macroTaskId ?? `run:${task.lineage!.rootRunId}`)}>{source.title ?? t("untitled")}</LinkButton>] as [string, ReactNode]] : []),
        ...(source?.macroTaskTitle ? [[t("record.macroTask"), source.macroTaskTitle] as [string, ReactNode]] : []),
        [calculation ? t("record.host") : t("delegation.originalDelegator"), taskHost(task)],
        [t("record.id"), <CopyableIdentifier key="id" value={task.runId} />],
        ...(!calculation ? [[t("delegation.cwd"), task.cwd, true] as [string, ReactNode, boolean]] : []),
      ]} />
    </Section>
  </Pane>;
}
