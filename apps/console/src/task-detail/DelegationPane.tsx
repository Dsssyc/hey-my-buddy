import { useTranslation } from "react-i18next";
import { Section } from "./parts";
import { actorText, workflowOwnerText } from "../actor";
import { taskProject } from "../console-data";
import { displayTitle } from "../macro-task-display";
import type { Step } from "./steps";
import { Ask } from "./ask";
import { Fold, LinkButton, Moment, Pane, PaneTitle } from "./parts";
import type { Context } from "./types";

/**
 * The task as delegated, its owner, its owned micro tasks and its repair links.
 * The delegation is present even when the timeline read has no row for the task.
 */
export function DelegationPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const { view, workflow, timeline, row } = ctx;
  const { task } = view;
  const title = (runId: string) => {
    const found = timeline?.rows.find(item => item.runId === runId);
    if (found) return displayTitle(found.titleSource, found.title).text;
    return row?.fixes === runId ? displayTitle(row.fixesTitleSource!, row.fixesTitle!).text : runId;
  };
  const project = taskProject(task);
  const leadRunId = task.lineage?.leadOwnership?.leadRunId ?? null;
  const fixes = row?.fixes ?? null;
  const fixedBy = row?.fixedBy ?? [];
  const sender = step.events[0]?.actor;
  const owner = workflowOwnerText(task);
  const asked = ctx.asked.delegation ?? [];
  return <Pane title={
    <PaneTitle title={t("step.delegation")}>
      <span>{sender ? actorText(sender) : owner}</span>
      <Moment at={step.at ?? task.createdAt} />
    </PaneTitle>}>
    <Section name={t("delegation.task")}><Fold text={task.task} /></Section>
    <Section name={t("delegation.owner")}>{owner}</Section>
    <Section name={t("delegation.project")}>{project?.label ?? t("delegation.unknownProject")}</Section>
    <Section name={t("delegation.cwd")}><span className="font-mono text-xs">{workflow.cwd ?? task.cwd}</span></Section>
    {leadRunId && <Section name={t("delegation.parent")}><LinkButton onClick={() => ctx.open(leadRunId)}>{title(leadRunId)}</LinkButton></Section>}
    {workflow.delegationAuthority && <Section name={t("delegation.authority")}>{t("delegation.authorityYes")}</Section>}
    {workflow.ownedMicroTasks.length > 0 && <Section name={t("delegation.owned")}>
      <ul className="space-y-1.5">{workflow.ownedMicroTasks.map(({ runId }) =>
        <li key={runId}><LinkButton onClick={() => ctx.open(runId)}>{title(runId)}</LinkButton></li>)}</ul>
    </Section>}
    {(fixes || fixedBy.length > 0) && <Section name={t("delegation.fixes")}>
      <ul className="space-y-1.5">
        {fixes && <li className="flex items-baseline gap-1.5"><span aria-hidden="true" className="text-ink3">↪</span>
          <span className="sr-only">{t("delegation.fixesTarget")}</span><LinkButton onClick={() => ctx.open(fixes)}>{title(fixes)}</LinkButton></li>}
        {fixedBy.length > 0 && <li className="flex items-baseline gap-1.5"><span aria-hidden="true" className="text-ink3">↩</span>
          <span className="tabular-nums">{t("delegation.fixedBy", { n: fixedBy.length })}</span>
          <span className="flex flex-wrap gap-x-3">{fixedBy.map(id => <LinkButton key={id} onClick={() => ctx.open(id)}>{title(id)}</LinkButton>)}</span></li>}
      </ul>
    </Section>}
    {asked.length > 0 && <Section name={t("delegation.inquiries")}>
      <div className="space-y-4">{asked.map(inquiry => <Ask key={inquiry.messageId} inquiry={inquiry} />)}</div>
    </Section>}
  </Pane>;
}
