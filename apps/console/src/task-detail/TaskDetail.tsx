import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { MacroTaskTimeline } from "../macro-task-types";
import { AcceptancePane } from "./AcceptancePane";
import type { TaskDetailClient } from "./client";
import { DecisionPane } from "./DecisionPane";
import { DelegationPane } from "./DelegationPane";
import { EventPane } from "./EventPane";
import { DetailBack, Header } from "./Header";
import { placeInquiries } from "./inquiries";
import { RoutingPane } from "./RoutingPane";
import { StepStrip } from "./StepStrip";
import { buildSteps, defaultStep, stepForItem } from "./steps";
import type { Step, Steps } from "./steps";
import { TurnPane } from "./TurnPane";
import type { Context, TaskView, Workflow } from "./types";
import { useTaskDetail } from "./use-task-detail";
import { ExecutionTabs } from "./ExecutionTabs";
import type { SessionClient } from "../session-view/use-session";
import type { DetailTab } from "./location";

type Props = {
  client: TaskDetailClient;
  sessionClient: SessionClient;
  tab: DetailTab;
  onTab: (tab: DetailTab, replace?: boolean) => void;
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
 * The detail of one micro task, organised by turn: its steps sit as its own
 * timeline row in a strip along the top, and the area below says what the
 * chosen step has to say. It reads through the client it is given, hands the
 * address back to its owner, and writes nothing.
 */
export function TaskDetail({ client, sessionClient, tab, onTab, runId, timeline: recordedTimeline, timelineLoading, nowMs, step, origin, active, onStep, onBack, onOpenRun }: Props) {
  const { t } = useTranslation("taskDetail");
  const now = nowMs ?? Date.now();
  const stripRef = useRef<HTMLDivElement>(null);
  const keyboardFocusPending = useRef(false);
  const decisionIds = useMemo(() => (recordedTimeline?.spans ?? []).flatMap(span => (span.runId === runId && span.kind === "routing" && span.decisionId ? [span.decisionId] : [])), [recordedTimeline, runId]);
  const read = useTaskDetail(client, runId, active, decisionIds);
  const { view, workflow } = read;
  // Execution shutdown and workflow rejection are distinct facts. The macro
  // timeline records the finished process; this detail also reads its turn.
  const timeline = useMemo(() => {
    if (!recordedTimeline || !workflow) return recordedTimeline;
    const rejected = new Set([...workflow.turns, ...(read.audit?.turns ?? [])]
      .filter(turn => turn.state === "rejected").map(turn => turn.attemptId));
    return { ...recordedTimeline, spans: recordedTimeline.spans.map(span =>
      span.runId === runId && span.kind === "execution" && span.attemptId != null && rejected.has(span.attemptId)
        ? { ...span, state: "rejected" } : span) };
  }, [recordedTimeline, workflow, read.audit, runId]);
  const row = timeline?.rows.find(candidate => candidate.runId === runId) ?? null;
  const steps: Steps | null = useMemo(() => {
    if (view === null) return null;
    return timelineLoading ? null : buildSteps(timeline, runId, now);
  }, [view, timeline, timelineLoading, runId, now]);
  const asked = useMemo(() => (steps && read.inquiries ? placeInquiries(read.inquiries.items, steps, now) : {}), [steps, read.inquiries, now]);

  const chosen = steps === null ? null : step !== null && step <= steps.steps.length ? step - 1
    : (origin !== null ? stepForItem(steps, origin, timeline) : null) ?? defaultStep(steps);
  const current = steps !== null && chosen !== null ? steps.steps[chosen] ?? null : null;
  const execution = current?.kind === "turn" || current?.kind === "routing" ? current.span : null;
  // The address names the step it settled on, in place of the one it was opened with.
  useEffect(() => {
    if (steps !== null && chosen !== null && step !== chosen + 1) onStep(chosen + 1);
  }, [steps === null, chosen, step]);
  useEffect(() => {
    if (current && !execution && tab === "session") onTab("overview", true);
  }, [current?.key, execution, tab]);
  useLayoutEffect(() => {
    if (!keyboardFocusPending.current) return;
    keyboardFocusPending.current = false;
    stripRef.current?.querySelector<SVGElement>('[aria-pressed=true]')?.focus({ preventScroll: true });
  }, [chosen]);

  // Detail keys leave fields and dialogs to their own controls.
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (!["Escape", "ArrowLeft", "ArrowRight"].includes(event.key) || event.defaultPrevented) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [role=dialog], [role=tablist]")) return;
      if (event.key === "Escape") { event.preventDefault(); onBack(); return; }
      if (steps === null || chosen === null) return;
      event.preventDefault();
      const next = Math.max(0, Math.min(steps.steps.length - 1, chosen + (event.key === "ArrowRight" ? 1 : -1)));
      if (next !== chosen) {
        keyboardFocusPending.current = true;
        onStep(next + 1);
      } else stripRef.current?.querySelector<SVGElement>('[aria-pressed=true]')?.focus({ preventScroll: true });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, onBack, steps, chosen, onStep]);

  const bare = (body: ReactNode) => <section data-slot="task-detail" tabIndex={-1} aria-label={t("label")} className="flex min-h-0 min-w-0 flex-1 flex-col bg-card outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [container-type:inline-size]">{body}</section>;
  if (!view) {
    return bare(<><header data-slot="detail-header" className="shrink-0 bg-card py-2 pl-1.5 pr-4 sm:pr-6"><DetailBack onBack={onBack}/></header><div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
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
  return bare(<>
    <Header view={view} workflow={workflow} turns={workflow ? workflow.counts.turns : null} endMs={elapsedEnd(view, workflow, now)} nowMs={now}
      verifiedAtMs={read.verifiedAtMs} error={read.error} busy={read.busy} onRefresh={read.refresh} onBack={onBack} />
    {steps && !steps.complete && <p className="border-b bg-card px-4 py-2 text-xs text-ink3 sm:px-6">{t("strip.truncated")}</p>}
    {steps && current && ctx && row && <div ref={stripRef} data-slot="detail-step-band" className="shrink-0 border-y border-line bg-background py-3">
      <StepStrip row={row} timeline={timeline!} steps={steps} selected={chosen!} onStep={onStep} nowMs={now}/>
    </div>}
    {steps && current && ctx
      ? <div key={current.key} className="flex min-h-0 min-w-0 flex-1 flex-col">{execution
        ? <ExecutionTabs routingMode={execution.kind === "routing" ? execution.routing?.routingMode ?? null : undefined} taskText={view.task.task} checkoutRoot={workflow?.cwd ?? view.task.cwd} client={sessionClient} taskId={execution.kind === "routing" ? execution.decisionTaskId! : runId} attemptId={execution.attemptId!}
          harness={(execution.kind === "routing" ? execution.routing?.routerBuddy?.harness : execution.buddy?.harness) ?? t("unrecorded")} active={active} tab={tab} onTab={onTab}>{pane(current, ctx)}</ExecutionTabs>
        : pane(current, ctx)}</div>
      : <p role="status" className="p-6 text-ink2">{t("loading")}</p>}
  </>);
}
