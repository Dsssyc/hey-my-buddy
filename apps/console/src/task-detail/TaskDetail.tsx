import { useEffect, useMemo } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { MacroTaskTimeline } from "../macro-task-types";
import { AcceptancePane } from "./AcceptancePane";
import type { TaskDetailClient } from "./client";
import { DecisionPane } from "./DecisionPane";
import { DelegationPane } from "./DelegationPane";
import { EventPane } from "./EventPane";
import { Header } from "./Header";
import { modelEffort } from "./names";
import { Rail } from "./Rail";
import { RecordPane } from "./RecordPane";
import { RoutingPane } from "./RoutingPane";
import { buildSteps, defaultStep, recordSteps, stepForItem } from "./steps";
import type { Step, Steps } from "./steps";
import { TurnPane } from "./TurnPane";
import type { Context } from "./types";
import { useTaskDetail } from "./use-task-detail";

type Props = {
  client: TaskDetailClient;
  runId: string;
  /** The macro task's timeline the page has read; null while it is read or when it could not be. */
  timeline: MacroTaskTimeline | null;
  timelineLoading: boolean;
  /** The display clock, in epoch milliseconds: open waits run to it. */
  nowMs: number;
  /** The step the address names, counted from 1. */
  step: number | null;
  /** The timeline item the detail was opened from: it names the step to open on. */
  origin: string | null;
  active: boolean;
  /** What going back leads to: the macro task's timeline, or the list of records. */
  backTo?: "timeline" | "records";
  /** The step the detail shows changed, by the reader or by the detail choosing a first one. */
  onStep: (step: number) => void;
  onBack: () => void;
  onOpenRun: (runId: string) => void;
};

function pane(step: Step, ctx: Context) {
  switch (step.kind) {
    case "delegation": return <DelegationPane step={step} ctx={ctx} />;
    case "routing": return <RoutingPane step={step} ctx={ctx} />;
    case "turn": return <TurnPane step={step} ctx={ctx} />;
    case "decision": case "continue": return <DecisionPane step={step} ctx={ctx} />;
    case "acceptance": return <AcceptancePane step={step} ctx={ctx} />;
    default: return <EventPane step={step} ctx={ctx} />;
  }
}

/** Where a micro task's elapsed time ends: now while it is open, else when its last word was said. */
function elapsedEnd(ctx: Pick<Context, "task" | "workflow" | "nowMs">): number {
  const { task, workflow, nowMs } = ctx;
  if (["executing", "awaiting-host", "waiting-helpers", "waiting-owned", "delivered"].includes(workflow.state)) return nowMs;
  const ended = Date.parse(task.acceptedAt ?? task.updatedAt as string);
  return Number.isFinite(ended) ? ended : nowMs;
}

/**
 * The detail of one micro task, organised by turn: its row of the timeline
 * stood on end on the left, and on the right what the chosen step has to say.
 * It reads through the client it is given, hands the address back to its owner,
 * and writes nothing.
 */
export function TaskDetail({ client, runId, timeline, timelineLoading, nowMs, step, origin, active, backTo = "timeline", onStep, onBack, onOpenRun }: Props) {
  const { t } = useTranslation("taskDetail");
  const read = useTaskDetail(client, runId, active);
  const { task, workflow } = read;
  const collaboration = task ? !!task.workflow : null;
  const row = timeline?.rows.find(candidate => candidate.runId === runId) ?? null;
  const lead = (row?.rootLeadRunId ?? task?.lineage?.rootLeadRunId ?? null) !== null;
  const steps: Steps | null = useMemo(() => {
    if (collaboration === false) return recordSteps(task!.lineage?.kind === "decision" ? "calculation" : "record");
    if (collaboration === null || timelineLoading) return null;
    return buildSteps(timeline, runId, nowMs);
  }, [collaboration, task, timeline, timelineLoading, runId, nowMs]);

  const chosen = steps === null ? null : step !== null && step <= steps.steps.length ? step - 1
    : (origin !== null ? stepForItem(steps, origin, timeline) : null) ?? defaultStep(steps);
  // The address names the step it settled on, in place of the one it was opened with.
  useEffect(() => {
    if (steps !== null && chosen !== null && step !== chosen + 1) onStep(chosen + 1);
  }, [steps === null, chosen, step]);

  // Esc goes back, unless something above the detail has taken the key or a field is being written in.
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [role=dialog]")) return;
      event.preventDefault();
      onBack();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, onBack]);

  // The view brings its own hints, so that it stands in any page.
  const bare = (body: ReactNode) => <TooltipProvider delayDuration={300}>
    <section data-slot="task-detail" aria-label={t("label")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-card max-[760px]:flex-none">{body}</section>
  </TooltipProvider>;
  if (!task) {
    return bare(<div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
      {read.error
        ? <>
          <h2 className="text-base font-semibold">{t("failed")}</h2>
          <p role="alert" className="max-w-prose text-fail wrap-anywhere">{read.error}</p>
          <div className="flex gap-2">
            <Button variant="outline" onClick={read.refresh}>{t("retry")}</Button>
            <Button variant="outline" onClick={onBack}>{t(`back.${backTo}`)}</Button>
          </div>
        </>
        : <p role="status" className="text-ink2">{t("loading")}</p>}
    </div>);
  }
  const ready = collaboration === false || (workflow !== null && steps !== null);
  const ctx: Context | null = workflow && steps ? {
    task, workflow, audit: read.audit, routing: read.routing, inquiries: read.inquiries, timeline, row, nowMs, lead, open: onOpenRun,
  } : null;
  const current = steps !== null && chosen !== null ? steps.steps[chosen] ?? null : null;
  const buddyName = (item: Step) => modelEffort(item.span?.buddy ?? null);
  return bare(<>
    <Header task={task} workflow={workflow} lead={lead} turns={workflow ? workflow.turns.length : null}
      endMs={ctx ? elapsedEnd(ctx) : nowMs} verifiedAtMs={read.verifiedAtMs} error={read.error} backTo={backTo} onBack={onBack} onRefresh={read.refresh} />
    {ready && steps && current
      ? <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)] max-[760px]:flex-none max-[760px]:grid-cols-1 max-[760px]:grid-rows-[auto_auto]">
        <ScrollArea className="min-h-0 bg-background max-[760px]:border-b min-[761px]:border-r max-[760px]:[&>[data-slot=scroll-area-viewport]]:max-h-[40dvh]">
          <div className="w-0 min-w-full">
            <Rail result={steps} selected={chosen!} lead={lead} onSelect={index => onStep(index + 1)} buddyName={buddyName} />
          </div>
        </ScrollArea>
        <div key={current.key} className="flex min-h-0 min-w-0 flex-col">
          {collaboration === false ? <RecordPane step={current} task={task} /> : pane(current, ctx!)}
        </div>
      </div>
      : <p role="status" className="p-6 text-ink2">{t("loading")}</p>}
  </>);
}
