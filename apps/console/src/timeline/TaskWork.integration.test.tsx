import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "../components/ui/tooltip";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import { TaskWork, type TaskWorkProps } from "./TaskWork";
import { createTimelineScale, timelineScaleInput } from "./scale";
import { visibleTimeline } from "./visibleTimeline";
import { TIMELINE_OUTSET_PX } from "./trackMarks";

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
function setup() {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(prefers-reduced-motion: reduce)", addEventListener() {}, removeEventListener() {} }));
  const timeline = macroTaskTimelineFixture();
  const props: TaskWorkProps = { readGoals: vi.fn(), summary: timeline.macroTask, timeline, displayObservedAt: timeline.observedAt, loading: false, error: "", stale: false, newRunIds: new Set(), active: true, openedRunId: null, detail: () => null, onOpen: vi.fn(), onClose: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn() };
  return { props, ...render(<TooltipProvider delayDuration={0}><TaskWork {...props}/></TooltipProvider>) };
}
it("supports zoom shortcuts and resets the scale on macro navigation", () => {
  const { props, rerender } = setup();
  const canvas = () => Number(document.querySelector('[data-slot=timeline-canvas]')!.getAttribute("data-ppm"));
  const fitted = canvas();
  const row = document.querySelector('[data-slot=timeline-row-name]')!;
  fireEvent.keyDown(row, { key: "+" });
  expect(canvas()).toBeGreaterThan(fitted);
  fireEvent.keyDown(row, { key: "0" });
  expect(canvas()).toBe(fitted);
  fireEvent.keyDown(document.querySelector('[data-slot=zoom-ruler]')!, { key: "+" });
  expect(canvas()).toBeGreaterThan(fitted);
  rerender(<TooltipProvider delayDuration={0}><TaskWork {...props} timeline={{ ...props.timeline!, macroTask: { ...props.timeline!.macroTask, macroTaskId: "other" } }}/></TooltipProvider>);
  expect(canvas()).toBe(fitted);
});

it("refits on filtering, shares the saved habit across macro tasks and keeps subsequent manual zoom", () => {
  const { props, rerender } = setup();
  const canvas = () => Number(document.querySelector('[data-slot=timeline-canvas]')!.getAttribute("data-ppm"));
  const ruler = () => document.querySelector('[data-slot=zoom-ruler]')!;
  fireEvent.keyDown(ruler(), { key: "+" });
  fireEvent.click(screen.getByRole("button", { name: "未结束" }));
  const expected = createTimelineScale(timelineScaleInput(visibleTimeline(props.timeline!, true)), 1088 - 272 - TIMELINE_OUTSET_PX * 2).pixelsPerMinute;
  expect(canvas()).toBe(expected);
  expect(document.querySelector('[data-slot=timeline-row-count]')!.textContent).toBe("2");
  expect([...document.querySelectorAll('[data-slot=timeline-row-name]')].map(node => node.getAttribute("data-run-id"))).toEqual(["r4", "r6"]);
  fireEvent.keyDown(ruler(), { key: "+" });
  const manual = canvas();
  expect(manual).toBeGreaterThan(expected);
  rerender(<TooltipProvider delayDuration={0}><TaskWork {...props} displayObservedAt="2026-09-26T08:13:00Z"/></TooltipProvider>);
  expect(canvas()).toBe(manual);
  rerender(<TooltipProvider delayDuration={0}><TaskWork {...props} timeline={{ ...props.timeline!, macroTask: { ...props.timeline!.macroTask, macroTaskId: "other" } }}/></TooltipProvider>);
  expect(screen.getByRole("button", { name: "未结束" }).getAttribute("aria-pressed")).toBe("true");
  expect(canvas()).toBe(expected);
  cleanup(); setup();
  expect(screen.getByRole("button", { name: "未结束" }).getAttribute("aria-pressed")).toBe("true");
});

it("leaves the filter available when all rows have ended", () => {
  const { props, rerender } = setup();
  const timeline = { ...props.timeline!, rows: props.timeline!.rows.map(row => ({ ...row, category: "ended" as const })) };
  rerender(<TooltipProvider delayDuration={0}><TaskWork {...props} timeline={timeline}/></TooltipProvider>);
  fireEvent.click(screen.getByRole("button", { name: "未结束" }));
  expect(screen.getByText("没有未结束的微任务")).toBeTruthy();
  expect(document.querySelector('[data-slot=timeline-row-count]')!.textContent).toBe("0");
  fireEvent.click(screen.getByRole("button", { name: "全部" }));
  expect(document.querySelectorAll('[data-slot=timeline-row-name]')).toHaveLength(6);
});

it("keeps a correction hint closed when detail moves focus, but opens it for keyboard navigation and hover", async () => {
  const { props, rerender } = setup();
  const timeline = props.timeline!;
  const target = timeline.rows[0]!;
  const correction = { ...timeline.rows[1]!, fixes: target.runId, fixesTitle: target.title, fixesTitleSource: target.titleSource };
  const next = { ...props, timeline: { ...timeline, rows: [target, correction] } };
  const show = (openedRunId: string | null) => rerender(<TooltipProvider delayDuration={0}><TaskWork {...next} openedRunId={openedRunId}/></TooltipProvider>);
  show(null);
  show(correction.runId);
  // The list beside the detail holds the row; moving focus to it opens no tooltip.
  const openName = document.querySelector<HTMLButtonElement>(`[data-key="row:${correction.runId}"]`)!;
  expect(document.activeElement).toBe(openName);
  expect(screen.queryByRole("tooltip")).toBeNull();
  show(null);
  const closedName = document.querySelector<HTMLButtonElement>(`[data-key="row:${correction.runId}"]`)!;
  expect(document.activeElement).toBe(closedName);
  expect(screen.queryByRole("tooltip")).toBeNull();

  const first = document.querySelector<HTMLButtonElement>(`[data-key="row:${target.runId}"]`)!;
  const user = userEvent.setup();
  first.focus();
  await user.keyboard("{ArrowDown}");
  expect(document.activeElement).toBe(closedName);
  expect((await screen.findByRole("tooltip")).textContent).toContain(target.title);
  await user.keyboard("{Escape}");
  await user.hover(closedName);
  expect((await screen.findByRole("tooltip")).textContent).toContain(target.title);
});
