import { fireEvent, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useTimelineGestures } from "./useTimelineGestures";

function setup(wholeWheel = false) {
  const viewport = document.createElement("div");
  const ruler = document.createElement("div");
  const track = document.createElement("div");
  viewport.append(ruler, track);
  const zoomAt = vi.fn();
  const fit = vi.fn();
  const hook = renderHook(() => useTimelineGestures(viewport, ruler, zoomAt, fit, wholeWheel));
  return { viewport, ruler, track, zoomAt, fit, ...hook };
}

describe("timeline gestures", () => {
  it("zooms vertical ruler wheel and Ctrl+wheel, while Shift and horizontal wheel keep native panning", () => {
    const { ruler, track, zoomAt, unmount } = setup();
    fireEvent.wheel(ruler, { deltaY: -100, clientX: 140 });
    expect(zoomAt).toHaveBeenCalledWith(expect.any(Number), 140);
    expect(zoomAt.mock.calls[0]![0]).toBeGreaterThan(1);
    fireEvent.wheel(track, { deltaY: -100 });
    fireEvent.wheel(ruler, { deltaX: 120, deltaY: 20 });
    fireEvent.wheel(ruler, { shiftKey: true, deltaY: -100 });
    expect(zoomAt).toHaveBeenCalledTimes(1);
    fireEvent.wheel(track, { ctrlKey: true, deltaY: 100, clientX: 220 });
    expect(zoomAt).toHaveBeenCalledTimes(2);
    expect(zoomAt.mock.calls[1]![0]).toBeLessThan(1);
    unmount();
  });

  it("allows detail wheel zoom anywhere and fits on a ruler double-click", () => {
    const { ruler, track, zoomAt, fit, unmount } = setup(true);
    fireEvent.wheel(track, { deltaY: -100, clientX: 90 });
    fireEvent.doubleClick(ruler);
    expect(zoomAt).toHaveBeenCalledOnce();
    expect(fit).toHaveBeenCalledOnce();
    unmount();
  });

  it("keeps pinch distance across a render and uses the latest zoom callback", () => {
    const viewport = document.createElement("div");
    const ruler = document.createElement("div");
    viewport.append(ruler);
    const first = vi.fn();
    const second = vi.fn();
    const { rerender, unmount } = renderHook(({ callback }) => useTimelineGestures(viewport, ruler, callback, vi.fn()),
      { initialProps: { callback: first } });
    const touch = (type: string, left: number, right: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(event, "touches", { value: [{ clientX: left, clientY: 0 }, { clientX: right, clientY: 0 }] });
      ruler.dispatchEvent(event);
    };
    touch("touchstart", 0, 100);
    rerender({ callback: second });
    touch("touchmove", 0, 150);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith(1.5, 75);
    unmount();
  });
});
