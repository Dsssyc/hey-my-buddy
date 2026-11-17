import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TOOLTIP_DELAY_MS, TooltipProvider } from "../components/ui/tooltip";
import { ZoomRuler } from "./ZoomRuler";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function setup() {
  const view = render(
    <TooltipProvider>
      <div data-radix-scroll-area-viewport="">
        <ZoomRuler atMinimum onChange={vi.fn()} viewportWidth={800}>
          <div data-zoom-exclude="">Filter</div>
          <div>Axis</div>
        </ZoomRuler>
      </div>
    </TooltipProvider>,
  );
  const viewport = view.container.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]")!;
  const ruler = view.container.querySelector<HTMLElement>('[data-slot="zoom-ruler"]')!;
  viewport.getBoundingClientRect = () => new DOMRect(40, 0, 800, 300);
  Object.defineProperty(viewport, "clientWidth", { value: 800, configurable: true });
  ruler.getBoundingClientRect = () => new DOMRect(40, 0, 1200, 32);
  return {
    ruler,
    excluded: ruler.querySelector<HTMLElement>("[data-zoom-exclude]")!,
    anchor: ruler.querySelector<HTMLElement>('span[aria-hidden="true"]')!,
  };
}

describe("ZoomRuler", () => {
  it("opens only after the pointer rests for TOOLTIP_DELAY_MS and resets the dwell timer while sliding", () => {
    vi.useFakeTimers();
    const { ruler, anchor } = setup();

    fireEvent.pointerMove(ruler, { clientX: 120, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS - 100); });
    expect(screen.queryByRole("tooltip")).toBeNull();

    // Moving before the delay expires restarts the dwell timer at the new X.
    fireEvent.pointerMove(ruler, { clientX: 260, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS - 1); });
    expect(screen.queryByRole("tooltip")).toBeNull();

    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByRole("tooltip")).toBeTruthy();
    expect(anchor.style.left).toBe("220px");
  });

  it("ignores sub-3px jitter while open, dismisses on slide/wheel/pointerdown/exclude/leave, and re-anchors after resting again", () => {
    vi.useFakeTimers();
    const { ruler, excluded, anchor } = setup();

    fireEvent.pointerMove(ruler, { clientX: 200, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS); });
    expect(screen.getByRole("tooltip")).toBeTruthy();
    expect(anchor.style.left).toBe("160px");

    // 2px optical sensor tremor leaves the open hint alone.
    fireEvent.pointerMove(ruler, { clientX: 202, clientY: 16 });
    expect(screen.getByRole("tooltip")).toBeTruthy();

    // Sliding >= 3px along the axis dismisses immediately and waits for another full dwell.
    fireEvent.pointerMove(ruler, { clientX: 212, clientY: 16 });
    expect(screen.queryByRole("tooltip")).toBeNull();
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS - 1); });
    expect(screen.queryByRole("tooltip")).toBeNull();
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByRole("tooltip")).toBeTruthy();
    expect(anchor.style.left).toBe("172px");

    // Wheeling dismisses and prevents same-position pointermove from reopening until moved.
    fireEvent.wheel(ruler, { deltaY: -100 });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.pointerMove(ruler, { clientX: 212, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS); });
    expect(screen.queryByRole("tooltip")).toBeNull();

    // Reopen, then dismiss via pointerDown, [data-zoom-exclude], and pointerLeave.
    fireEvent.pointerMove(ruler, { clientX: 300, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS); });
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.pointerDown(ruler);
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.pointerMove(ruler, { clientX: 340, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS); });
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.pointerMove(excluded, { clientX: 50, clientY: 16 });
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.pointerMove(ruler, { clientX: 400, clientY: 16 });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS); });
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.pointerLeave(ruler);
    expect(screen.queryByRole("tooltip")).toBeNull();

    // Native pointerleave without pointerout (when a hovered descendant detaches) cancels pending dwell and closes.
    fireEvent.pointerMove(ruler, { clientX: 440, clientY: 16 });
    act(() => { ruler.dispatchEvent(new Event("pointerleave")); });
    act(() => { vi.advanceTimersByTime(TOOLTIP_DELAY_MS); });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens immediately centered in the viewport on focus-visible and closes on blur", () => {
    const { ruler, anchor } = setup();
    const originalMatches = ruler.matches.bind(ruler);
    vi.spyOn(ruler, "matches").mockImplementation(selector => selector === ":focus-visible" || originalMatches(selector));

    fireEvent.focus(ruler);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    expect(anchor.style.left).toBe("400px");

    fireEvent.blur(ruler);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
