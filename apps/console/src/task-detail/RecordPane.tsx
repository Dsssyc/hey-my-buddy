import { useTranslation } from "react-i18next";
import { Section } from "@/components/section";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { formatDuration } from "../i18n/format";
import { parseTokenUsage } from "./parse";
import { endingOf, finalText } from "./evidence";
import { Band, Facts, Fold, Moment, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import type { Step } from "./steps";
import { Node, Quote, Thread } from "./thread";
import type { Task } from "../types";
import { Usage } from "./usage";

/**
 * A run with no collaboration record: a command a Host ran, or a Router's own
 * calculation. It has one step. A command's band names no buddy, because there
 * is none; a calculation's names the Router that was asked.
 */
export function RecordPane({ step, task }: { step: Step; task: Task }) {
  const { t } = useTranslation("taskDetail");
  const calculation = step.kind === "calculation";
  const attemptId = task.selectedAttempt?.attemptId ?? "";
  const ending = endingOf(task, attemptId);
  const start = Date.parse(task.selectedAttempt?.startedAt ?? task.createdAt);
  const end = task.selectedAttempt?.finishedAt ? Date.parse(task.selectedAttempt.finishedAt) : NaN;
  const failed = task.status === "failed";
  const running = ["queued", "running", "cancelling"].includes(task.status);
  const text = finalText(task);
  return <Pane>
    <PaneTitle title={t(calculation ? "step.calculation" : "step.record")}
      state={running ? t("state.running") : failed ? t("state.failed") : t("state.ended")} tone={running ? "live" : failed ? "fail" : "past"}
      note={ending?.cause ? t("ending.cause", { cause: t(`termination.${ending.cause}`, { defaultValue: ending.cause }) }) : undefined}>
      <span className="font-mono tabular-nums"><Moment at={Number.isFinite(start) ? start : task.createdAt} seconds /> – {Number.isFinite(end) ? <Moment at={end} seconds /> : null}</span>
      {Number.isFinite(start) && Number.isFinite(end) && <b className="text-sm font-normal text-foreground tabular-nums">{formatDuration(end - start, true)}</b>}
    </PaneTitle>
    <Band role={calculation ? "Router" : t("record.command")} plain={!calculation}
      identity={calculation ? quad(task.lineage?.buddy) ?? undefined : undefined}
      second={<Usage usage={parseTokenUsage(task.tokenUsage ?? task.selectedAttempt?.tokenUsage)} />} />
    <Thread>
      {!calculation && <Node mark="input"><Quote><Fold text={task.task} /></Quote></Node>}
      <Node mark="result">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink3">{t("result.name")}</p>
          <Fold text={text ?? t("unrecorded")} className="text-foreground" />
        </div>
      </Node>
    </Thread>
    <Section name={t("delegation.source")} className="border-t">
      <Facts items={[[t("delegation.id"), <CopyableIdentifier key="id" value={task.runId} />], [t("delegation.cwd"), task.cwd, true]]} />
    </Section>
  </Pane>;
}
