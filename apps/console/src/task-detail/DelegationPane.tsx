import { cn } from "cn";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Section } from "@/components/section";
import { StateMark } from "@/components/ui/state-mark";
import { CopyableIdentifier } from "../components/CopyableIdentifier";
import { actorText, workflowOwnerText } from "../actor";
import { taskHost, taskProject } from "../console-data";
import { displayTitle } from "../macro-task-display";
import type { Step } from "./steps";
import { Ask } from "./ask";
import { Facts, Fold, LinkButton, Moment, Pane, PaneTitle } from "./parts";
import { Thread } from "./thread";
import type { Context } from "./types";

/** The word and the mark a helper micro task's state has. */
const HELPER_WORD = {
  active: { key: "running", mark: "live" }, attention: { key: "attention", mark: "hand" }, succeeded: { key: "done", mark: "done" },
  failed: { key: "failed", mark: "fail" }, cancelled: { key: "ended", mark: "idle" },
} as const;

/**
 * The delegation: the task as it was given, where it came from, the helpers it
 * has and the micro tasks it fixes or is fixed by. The one step every micro task
 * has, whether or not the timeline read still holds its row.
 */
export function DelegationPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const { view, workflow, timeline, row } = ctx;
  const { task } = view;
  const title = (runId: string) => {
    const found = timeline?.rows.find(item => item.runId === runId);
    return found ? displayTitle(found.titleSource, found.title).text : runId;
  };
  const project = taskProject(task);
  const parent = task.lineage?.parentRunId ?? null;
  const fixes = row?.fixes ?? null;
  const fixedBy = row?.fixedBy ?? [];
  const sender = step.events[0]?.actor;
  const asked = ctx.asked.delegation ?? [];
  return <Pane>
    <PaneTitle title={t("step.delegation")}>
      <span>{sender ? actorText(sender) : null}</span>
      <Moment at={step.at ?? task.createdAt} />
    </PaneTitle>
    <Section name={t("delegation.task")} className="border-t"><Fold text={task.task} limit={400} /></Section>
    <Section name={t("delegation.source")}>
      <Facts items={[
        [t("delegation.originalDelegator"), taskHost(task)],
        [t("delegation.currentOwner"), workflowOwnerText(task, workflow.hostId)],
        [t("delegation.project"), <Hint key="project" text={project?.path ?? undefined}><span tabIndex={0} className="rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{project?.label ?? t("delegation.unknownProject")}</span></Hint>],
        [t("delegation.id"), <CopyableIdentifier key="id" value={task.runId} />],
        ...(parent ? [[t("delegation.parent"), <LinkButton key="parent" hint={parent} onClick={() => ctx.open(parent)}>{title(parent)}</LinkButton>] as [string, ReactNode]] : []),
        [t("delegation.cwd"), workflow.cwd ?? task.cwd, true],
        ...(workflow.delegationAuthority ? [[t("delegation.authority"), t("delegation.authorityYes")] as [string, string]] : []),
      ]} />
    </Section>
    {workflow.children.length > 0 && <Section name={t("delegation.helpers")}>
      <ul className="space-y-1.5">{workflow.children.map(child => {
        const word = HELPER_WORD[child.state];
        return <li key={child.taskId} className="flex flex-wrap items-center gap-x-2">
          <StateMark kind={word.mark} />
          <LinkButton hint={child.taskId} onClick={() => ctx.open(child.taskId)}>{child.role === "router" ? t("delegation.calculation") : title(child.taskId)}</LinkButton>
          <span className={cn("text-xs", word.mark === "hand" ? "text-hand" : word.mark === "fail" ? "text-fail" : "text-ink2")}>
            {t(`state.${word.key === "attention" ? (view.lead ? "lead" : "host") : word.key}`)}
          </span>
        </li>;
      })}</ul>
    </Section>}
    {(fixes || fixedBy.length > 0) && <Section name={t("delegation.fixes")}>
      <ul className="space-y-1.5">
        {fixes && <li className="flex items-baseline gap-1.5"><span aria-hidden="true" className="text-ink3">↪</span>
          <span className="sr-only">{t("delegation.fixesTarget")}</span><LinkButton hint={fixes} onClick={() => ctx.open(fixes)}>{title(fixes)}</LinkButton></li>}
        {fixedBy.length > 0 && <li className="flex items-baseline gap-1.5"><span aria-hidden="true" className="text-ink3">↩</span>
          <span className="tabular-nums">{t("delegation.fixedBy", { n: fixedBy.length })}</span>
          <span className="flex flex-wrap gap-x-3">{fixedBy.map(id => <LinkButton key={id} hint={id} onClick={() => ctx.open(id)}>{title(id)}</LinkButton>)}</span></li>}
      </ul>
    </Section>}
    {asked.length > 0 && <Section name={t("delegation.inquiries")}>
      <Thread className="px-0">{asked.map(inquiry => <Ask key={inquiry.messageId} inquiry={inquiry} />)}</Thread>
    </Section>}
  </Pane>;
}
