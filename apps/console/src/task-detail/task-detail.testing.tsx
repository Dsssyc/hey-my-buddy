import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { MacroTaskTimeline } from "../macro-task-types";
import { TaskDetail } from "./TaskDetail";
import type { TaskDetailClient } from "./client";
import { at, client, timeline } from "./task-detail.fixtures";
import type { Replies } from "./task-detail.fixtures";

export type Options = { replies?: Replies; timeline?: MacroTaskTimeline | null; timelineLoading?: boolean; step?: number | null; origin?: string | null; active?: boolean; client?: TaskDetailClient };

/** Lets a test move the address's step the way the page would. */
export type StepHandle = { set: (step: number | null) => void };

/** The page owns the step and the detail asks it to change: this holds the step as the address would. */
function Harness({ options, steps, onStep, onBack, onOpenRun }: { options: Options; steps: StepHandle; onStep: (step: number) => void; onBack: () => void; onOpenRun: (runId: string) => void }) {
  const [step, setStep] = useState<number | null>(options.step ?? null);
  const [tab, setTab] = useState<"overview" | "session">("overview");
  steps.set = setStep;
  const reads = options.client ?? client(options.replies);
  return <TooltipProvider delayDuration={0}><TaskDetail client={reads} sessionClient={{ session: async () => ({}) }} tab={tab} onTab={setTab} runId="m1" timeline={options.timeline === undefined ? timeline() : options.timeline}
    timelineLoading={options.timelineLoading ?? false} nowMs={Date.parse(at("11:30"))} step={step} origin={options.origin ?? null} active={options.active ?? true}
    onStep={next => { onStep(next); setStep(next); }} onBack={onBack} onOpenRun={onOpenRun} /></TooltipProvider>;
}

export function open(options: Options = {}) {
  const calls = { onStep: vi.fn(), onBack: vi.fn(), onOpenRun: vi.fn() };
  const steps: StepHandle = { set: () => {} };
  const reads = options.client ?? client(options.replies);
  const view = render(<Harness options={{ ...options, client: reads }} steps={steps} {...calls} />);
  return { ...calls, reads: reads as ReturnType<typeof client>, view, user: userEvent.setup(),
    atStep: (next: number) => { act(() => steps.set(next)); },
    /** A later read or address change, as the page would deliver it. */
    reread: (next: Options) => { view.rerender(<Harness options={{ ...next, client: reads }} steps={steps} {...calls} />); } };
}

/** The steps strip along the top of the detail: the micro task's own row of the macro task. */
export const strip = () => screen.getByRole("group", { name: "这个微任务的各步" });
export const stripSlice = (key: string) => strip().querySelector(`[data-key="${key}"]`) as SVGElement | null;
/** What the chosen step calls itself in the pane below the strip. */
export const chosenStep = () => (pane().querySelector("h3") ?? pane()).textContent!;
export const pane = () => document.querySelector("[data-slot=detail-pane]") as HTMLElement;
/** The pane can arrive before the mock's subsequent workflow and audit reads. Flush their effects before assertions. */
export async function ready() {
  await waitFor(() => expect(pane()).toBeTruthy());
  await act(async () => {});
  return pane();
}
export const step = (name: string | RegExp) => within(pane()).getByRole("heading", { name });
