import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Section } from "@/components/section";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { actorText, workflowOwnerText } from "../actor";
import { taskHost, taskProject } from "../console-data";
import { displayTitle } from "../macro-task-display";
import type { Step } from "./steps";
import { Facts, Fold, LinkButton, Moment, Pane, PaneTitle } from "./parts";
import type { Context } from "./types";

/**
 * The delegation: the task as it was given, where it came from, the helpers it
 * has and the micro tasks it fixes or is fixed by. The one step every micro task
 * has, whether or not the timeline read still holds its row.
 */
export function DelegationPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const { task, workflow, timeline, row } = ctx;
  const title = (runId: string) => {
    const found = timeline?.rows.find(item => item.runId === runId);
    return found ? displayTitle(found.titleSource, found.title).text : runId;
  };
  const project = taskProject(task);
  const parent = task.lineage?.parentRunId ?? null;
  const fixes = row?.fixes ?? null;
  const fixedBy = row?.fixedBy ?? [];
  const sender = step.events[0]?.actor;
  return <Pane>
    <PaneTitle title={t("step.delegation")}>
      <span>{sender ? actorText(sender) : null}</span>
      <Moment at={step.at ?? task.createdAt} seconds />
    </PaneTitle>
    <Section name={t("delegation.task")} className="border-t"><Fold text={task.task} limit={400} /></Section>
    <Section name={t("delegation.source")}>
      <Facts items={[
        [t("delegation.originalDelegator"), taskHost(task)],
        [t("delegation.currentOwner"), workflowOwnerText(task, workflow.hostId)],
        [t("delegation.project"), <span key="project" title={project.path ?? undefined}>{project.label}</span>],
        [t("delegation.id"), <CopyableIdentifier key="id" value={task.runId} />],
        ...(parent ? [[t("delegation.parent"), <LinkButton key="parent" title={parent} onClick={() => ctx.open(parent)}>{title(parent)}</LinkButton>] as [string, ReactNode]] : []),
        [t("delegation.cwd"), workflow.cwd ?? task.cwd, true],
        ...(workflow.delegationAuthority ? [[t("delegation.authority"), t("delegation.authorityYes")] as [string, string]] : []),
      ]} />
    </Section>
    {workflow.children.length > 0 && <Section name={t("delegation.helpers")}>
      <ul className="space-y-1.5">{workflow.children.map(child => <li key={child.taskId} className="flex flex-wrap items-baseline gap-x-2">
        <LinkButton title={child.taskId} onClick={() => ctx.open(child.taskId)}>{child.role === "router" ? t("delegation.calculation") : title(child.taskId)}</LinkButton>
        <span className="text-xs text-ink3">{t(`delegation.helperState.${child.state}`, { defaultValue: child.state })}</span>
      </li>)}</ul>
    </Section>}
    {(fixes || fixedBy.length > 0) && <Section name={t("delegation.fixes")}>
      <ul className="space-y-1.5">
        {fixes && <li className="flex items-baseline gap-1.5"><span aria-hidden="true" className="text-ink3">↪</span>
          <span className="sr-only">{t("delegation.fixesTarget")}</span><LinkButton title={fixes} onClick={() => ctx.open(fixes)}>{title(fixes)}</LinkButton></li>}
        {fixedBy.length > 0 && <li className="flex items-baseline gap-1.5"><span aria-hidden="true" className="text-ink3">↩</span>
          <span className="tabular-nums">{t("delegation.fixedBy", { n: fixedBy.length })}</span>
          <span className="flex flex-wrap gap-x-3">{fixedBy.map(id => <LinkButton key={id} title={id} onClick={() => ctx.open(id)}>{title(id)}</LinkButton>)}</span></li>}
      </ul>
    </Section>}
  </Pane>;
}
