import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "../components/ui/tooltip";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import { TaskWork, type TaskWorkProps } from "./TaskWork";

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
function setup() {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const timeline = macroTaskTimelineFixture();
  const props: TaskWorkProps = { summary: timeline.macroTask, timeline, displayObservedAt: timeline.observedAt, loading: false, error: "", stale: false, newRunIds: new Set(), active: true, openedRunId: null, detail: () => null, onOpen: vi.fn(), onClose: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn() };
  return { props, ...render(<TooltipProvider><TaskWork {...props}/></TooltipProvider>) };
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
  fireEvent.click(screen.getByRole("button", { name: "放大（+）" }));
  expect(canvas()).toBeGreaterThan(fitted);
  rerender(<TooltipProvider><TaskWork {...props} timeline={{ ...props.timeline!, macroTask: { ...props.timeline!.macroTask, macroTaskId: "other" } }}/></TooltipProvider>);
  expect(canvas()).toBe(fitted);
});
