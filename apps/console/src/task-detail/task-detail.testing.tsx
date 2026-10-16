import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { MacroTaskTimeline } from "../macro-task-types";
import { TaskDetail } from "./TaskDetail";
import type { TaskDetailClient } from "./client";
import { at, client, timeline } from "./task-detail.fixtures";
import type { Replies } from "./task-detail.fixtures";

export type Options = { replies?: Replies; timeline?: MacroTaskTimeline | null; timelineLoading?: boolean; step?: number | null; origin?: string | null; active?: boolean; backTo?: "timeline" | "records"; client?: TaskDetailClient };

/** The page owns the step and the detail asks it to change: this holds the step as the address would. */
function Harness({ options, onStep, onBack, onOpenRun }: { options: Options; onStep: (step: number) => void; onBack: () => void; onOpenRun: (runId: string) => void }) {
  const [step, setStep] = useState<number | null>(options.step ?? null);
  const reads = options.client ?? client(options.replies);
  return <TooltipProvider><TaskDetail client={reads} runId="m1" timeline={options.timeline === undefined ? timeline() : options.timeline}
    timelineLoading={options.timelineLoading ?? false} nowMs={Date.parse(at("11:30"))} step={step} origin={options.origin ?? null} active={options.active ?? true} backTo={options.backTo}
    onStep={next => { onStep(next); setStep(next); }} onBack={onBack} onOpenRun={onOpenRun} /></TooltipProvider>;
}

export function open(options: Options = {}) {
  const calls = { onStep: vi.fn(), onBack: vi.fn(), onOpenRun: vi.fn() };
  const reads = options.client ?? client(options.replies);
  const view = render(<Harness options={{ ...options, client: reads }} {...calls} />);
  return { ...calls, reads: reads as ReturnType<typeof client>, view, user: userEvent.setup() };
}

export const rail = () => screen.getByRole("navigation", { name: "步骤" });
export const selected = () => within(rail()).getByRole("button", { current: "step" });
export const pane = () => document.querySelector("[data-slot=detail-pane]") as HTMLElement;
/** The pane can arrive before the mock's subsequent workflow and audit reads. Flush their effects before assertions. */
export async function ready() {
  await waitFor(() => expect(pane()).toBeTruthy());
  await act(async () => {});
  return pane();
}
export const step = (name: string | RegExp) => within(rail()).getByRole("button", { name });

