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
import { placeInquiries } from "./inquiries";
import { modelEffort } from "./names";
import { Rail } from "./Rail";
import { RoutingPane } from "./RoutingPane";
import { buildSteps, defaultStep, stepForItem } from "./steps";
import type { Step, Steps } from "./steps";
import { TurnPane } from "./TurnPane";
import type { Context, TaskView, Workflow } from "./types";
import { useTaskDetail } from "./use-task-detail";

type Props = {
  client: TaskDetailClient;
  runId: string;
  /** The macro task's timeline the page has read; null while it is read or when it could not be. */
  timeline: MacroTaskTimeline | null;
  timelineLoading: boolean;
  /** The display clock, in epoch milliseconds: open waits run to it. Without one the detail reads the clock as it draws. */
  nowMs: number | null;
  /** The step the address names, counted from 1. */
  step: number | null;
  /** The timeline item the detail was opened from: it names the step to open on. */
  origin: string | null;
  active: boolean;
  /** The step the detail shows changed, by the reader or by the detail choosing a first one. */
  onStep: (step: number) => void;
  onBack: () => void;
  onOpenRun: (runId: string, macroTaskId?: string) => void;
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
function elapsedEnd(view: TaskView, workflow: Workflow | null, nowMs: number): number {
  if (workflow && ["executing", "awaiting-host", "waiting-helpers", "waiting-owned", "delivered"].includes(workflow.state)) return nowMs;
  if (!workflow && ["queued", "running", "cancelling"].includes(view.task.status)) return nowMs;
  const ended = Date.parse(view.task.acceptedAt ?? view.updatedAt);
  return Number.isFinite(ended) ? ended : nowMs;
}

/**
 * The detail of one micro task, organised by turn: its row of the timeline
 * stood on end on the left, and on the right what the chosen step has to say.
 * It reads through the client it is given, hands the address back to its owner,
 * and writes nothing.
 */
export function TaskDetail({ client, runId, timeline, timelineLoading, nowMs, step, origin, active, onStep, onBack, onOpenRun }: Props) {
  const { t } = useTranslation("taskDetail");
  const now = nowMs ?? Date.now();
  const decisionIds = useMemo(() => (timeline?.spans ?? []).flatMap(span => (span.runId === runId && span.kind === "routing" && span.decisionId ? [span.decisionId] : [])), [timeline, runId]);
  const read = useTaskDetail(client, runId, active, decisionIds);
  const { view, workflow } = read;
  const row = timeline?.rows.find(candidate => candidate.runId === runId) ?? null;
  const steps: Steps | null = useMemo(() => {
    if (view === null) return null;
    return timelineLoading ? null : buildSteps(timeline, runId, now);
  }, [view, timeline, timelineLoading, runId, now]);
  const asked = useMemo(() => (steps && read.inquiries ? placeInquiries(read.inquiries.items, steps, now) : {}), [steps, read.inquiries, now]);

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
  if (!view) {
    return bare(<div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
      {read.error
        ? <>
          <h2 className="text-base font-semibold">{t("failed")}</h2>
          <p role="alert" className="max-w-prose text-fail wrap-anywhere">{read.error}</p>
          <div className="flex gap-2">
            <Button variant="outline" onClick={read.refresh}>{t("retry")}</Button>
            <Button variant="outline" onClick={onBack}>{t("back.timeline")}</Button>
          </div>
        </>
        : <p role="status" className="text-ink2">{t("loading")}</p>}
    </div>);
  }
  const ctx: Context | null = workflow ? {
    view, workflow, audit: read.audit, routing: read.routing, bases: read.bases, inquiries: read.inquiries, asked, timeline, row, nowMs: now, open: onOpenRun,
  } : null;
  const current = steps !== null && chosen !== null ? steps.steps[chosen] ?? null : null;
  const attempt = view.attempt;
  const buddyName = (item: Step) => modelEffort(item.kind === "routing"
    ? read.routing.find(entry => entry.decisionId === item.span?.decisionId)?.routerBuddy
    : item.span?.buddy);
  const reminder = (item: Step) => (attempt !== null && item.kind === "turn" && item.outcome === "running" && attempt.attemptId === item.span?.attemptId
    ? attempt.possiblyStuck ? "possiblyStuck" as const : attempt.overdue ? "overdue" as const : null : null);
  return bare(<>
    <Header view={view} workflow={workflow} turns={workflow ? workflow.counts.turns : null} endMs={elapsedEnd(view, workflow, now)} nowMs={now}
      verifiedAtMs={read.verifiedAtMs} error={read.error} busy={read.busy} onBack={onBack} onRefresh={read.refresh} />
    {steps && current && ctx
      ? <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)] max-[760px]:flex-none max-[760px]:grid-cols-1 max-[760px]:grid-rows-[auto_auto]">
        <ScrollArea className="min-h-0 bg-background max-[760px]:border-b min-[761px]:border-r max-[760px]:[&>[data-slot=scroll-area-viewport]]:max-h-[40dvh]">
          <div className="w-0 min-w-full">
            <Rail result={steps} selected={chosen!} lead={view.lead} reminder={reminder} onSelect={index => onStep(index + 1)} buddyName={buddyName} />
          </div>
        </ScrollArea>
        <div key={current.key} className="flex min-h-0 min-w-0 flex-col">
          {pane(current, ctx)}
        </div>
      </div>
      : <p role="status" className="p-6 text-ink2">{t("loading")}</p>}
  </>);
}
