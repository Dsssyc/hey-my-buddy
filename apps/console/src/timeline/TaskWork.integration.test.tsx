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
it("remembers the selected time view and defaults to timeline even on a narrow screen", () => {
  const first = setup();
  expect(screen.getByRole("radio", { name: "时间轴" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("radio", { name: "清单" }));
  expect(screen.getByRole("region", { name: "清单" })).toBeTruthy();
  expect(localStorage.getItem("hey-my-buddy.console.view.timeline.chronological")).toBe("true");
  first.unmount();
  setup();
  expect(screen.getByRole("radio", { name: "清单" }).getAttribute("aria-checked")).toBe("true");
  const item = document.querySelector('[data-slot=chronological-item]')!;
  fireEvent.click(item);
});
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
