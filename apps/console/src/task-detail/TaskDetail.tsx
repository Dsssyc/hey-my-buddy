import { useEffect, useMemo } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { MacroTaskTimeline } from "../macro-task-types";
import { AcceptancePane } from "./AcceptancePane";
import type { TaskDetailClient } from "./client";
import { DecisionPane } from "./DecisionPane";
import { DelegationPane } from "./DelegationPane";
import { EventPane } from "./EventPane";
import { DetailBack, Header } from "./Header";
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
  /** The expanded timeline row owns the step rail's place in the persistent name column. */
  railTarget: HTMLElement | null;
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
 * The detail of one micro task, organised by turn: its timeline row holds the
 * step rail, and the detail area says what the chosen step has to say.
 * It reads through the client it is given, hands the address back to its owner,
 * and writes nothing.
 */
export function TaskDetail({ client, runId, timeline, timelineLoading, railTarget, nowMs, step, origin, active, onStep, onBack, onOpenRun }: Props) {
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

  // Detail keys leave fields and dialogs to their own controls.
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (!["Escape", "ArrowLeft", "ArrowRight"].includes(event.key) || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [role=dialog]")) return;
      if (event.key === "Escape") { event.preventDefault(); onBack(); return; }
      if (steps === null || chosen === null) return;
      event.preventDefault();
      const next = Math.max(0, Math.min(steps.steps.length - 1, chosen + (event.key === "ArrowRight" ? 1 : -1)));
      if (next !== chosen) onStep(next + 1);
      railTarget?.querySelector<HTMLButtonElement>(`[data-step="${next + 1}"]`)?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, onBack, steps, chosen, onStep, railTarget]);

  // The view brings its own hints, so that it stands in any page.
  const bare = (body: ReactNode) => <TooltipProvider delayDuration={300}>
    <section data-slot="task-detail" aria-label={t("label")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-card [container-type:inline-size]">{body}</section>
  </TooltipProvider>;
  if (!view) {
    return bare(<><header data-slot="detail-header" className="shrink-0 border-b bg-card px-4 py-3 sm:px-6"><DetailBack onBack={onBack}/></header><div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
      {read.error
        ? <>
          <h2 className="text-base font-semibold">{t("failed")}</h2>
          <p role="alert" className="max-w-prose text-fail wrap-anywhere">{read.error}</p>
          <Button variant="outline" onClick={read.refresh}>{t("retry")}</Button>
        </>
        : <p role="status" className="text-ink2">{t("loading")}</p>}
    </div></>);
  }
  const ctx: Context | null = workflow ? {
    view, workflow, audit: read.audit, routing: read.routing, bases: read.bases, inquiries: read.inquiries, asked, timeline, row, highlightedInquiry: origin?.startsWith("inquiry:") ? origin.slice(8) : null, nowMs: now, open: onOpenRun,
  } : null;
  const current = steps !== null && chosen !== null ? steps.steps[chosen] ?? null : null;
  const attempt = view.attempt;
  const buddyName = (item: Step) => modelEffort(item.kind === "routing"
    ? read.routing.find(entry => entry.decisionId === item.span?.decisionId)?.routerBuddy
    : item.span?.buddy);
  const reminder = (item: Step) => (attempt !== null && item.kind === "turn" && item.outcome === "running" && attempt.attemptId === item.span?.attemptId
    ? attempt.possiblyStuck ? "possiblyStuck" as const : attempt.overdue ? "overdue" as const : null : null);
  return bare(<>
    {railTarget && steps && current && ctx && createPortal(
      <Rail result={steps} selected={chosen!} lead={view.lead} reminder={reminder} onSelect={index => onStep(index + 1)} buddyName={buddyName} />,
      railTarget,
    )}
    <Header view={view} workflow={workflow} turns={workflow ? workflow.counts.turns : null} endMs={elapsedEnd(view, workflow, now)} nowMs={now}
      verifiedAtMs={read.verifiedAtMs} error={read.error} busy={read.busy} onRefresh={read.refresh} onBack={onBack} />
    {steps && current && ctx
      ? <div key={current.key} className="flex min-h-0 min-w-0 flex-1 flex-col">{pane(current, ctx)}</div>
      : <p role="status" className="p-6 text-ink2">{t("loading")}</p>}
  </>);
}
