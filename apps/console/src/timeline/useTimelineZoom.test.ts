import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTimelineZoom } from "./useTimelineZoom";
import type { ScaleInput } from "./scale";

const minute = 60_000;
const input: ScaleInput = { domain: { startMs: 0, endMs: 120 * minute }, busy: [{ startMs: 0, endMs: 10 * minute }, { startMs: 110 * minute, endMs: 120 * minute }], instants: [10 * minute, 110 * minute], canFold: true, nowAtEnd: true };
afterEach(() => vi.unstubAllGlobals());
function setup(selectedAt: number | null = null) {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const viewport = { current: document.createElement("div") };
  const hook = renderHook(({ macroId }) => useTimelineZoom(input, 640, macroId, viewport, selectedAt, 120 * minute), { initialProps: { macroId: "one" } });
  return { ...hook, viewport };
}
describe("timeline scale zoom", () => {
  it("clamps at fit and 60 pixels per minute, keeping every break 54 pixels wide", () => {
    const { result } = setup();
    const fitted = result.current.scale!.pixelsPerMinute;
    act(() => result.current.change(-1));
    expect(result.current.scale!.pixelsPerMinute).toBe(fitted);
    for (let n = 0; n < 8; n++) act(() => result.current.change(1));
    expect(result.current.scale!.pixelsPerMinute).toBe(60);
    expect(result.current.atMaximum).toBe(true);
    expect(result.current.scale!.breaks.map(gap => gap.endX - gap.startX)).toEqual([54]);
    act(() => result.current.change(0));
    expect(result.current.scale!.pixelsPerMinute).toBe(fitted);
    expect(result.current.atMinimum).toBe(true);
  });
  it.each([
    [8 * minute, 8 * minute, "active or selected fragment"],
    [null, 120 * minute, "now"],
  ] as const)("preserves the screen position of the %s anchor (%s: %s)", (selected, at, _name) => {
    const { result, viewport } = setup(selected);
    const screenX = () => result.current.scale!.toX(at)! + 24 - viewport.current.scrollLeft;
    const before = screenX();
    act(() => result.current.change(1));
    expect(screenX()).toBeCloseTo(before, 6);
  });
  it("returns to fit and clears scrolling when changing macro tasks", () => {
    const { result, rerender, viewport } = setup(113 * minute);
    const fitted = result.current.scale!.pixelsPerMinute;
    act(() => result.current.change(1));
    expect(viewport.current.scrollLeft).toBeGreaterThan(0);
    rerender({ macroId: "two" });
    expect(result.current.scale!.pixelsPerMinute).toBe(fitted);
    expect(viewport.current.scrollLeft).toBe(0);
  });
  it("continues to fit live bounds after zooming back to the minimum", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    const viewport = { current: document.createElement("div") };
    const { result, rerender } = renderHook(({ endMs }) => useTimelineZoom({ ...input, canFold: false, domain: { startMs: 0, endMs } }, 640, "live", viewport, null, endMs), { initialProps: { endMs: 120 * minute } });
    const fitted = result.current.scale!.pixelsPerMinute;
    act(() => result.current.change(1));
    act(() => result.current.change(-1));
    rerender({ endMs: 240 * minute });
    expect(result.current.scale!.pixelsPerMinute).toBeCloseTo(fitted / 2);
    expect(result.current.atMinimum).toBe(true);
  });

  it("keeps the instant under the gesture pointer fixed and clamps gesture zoom", () => {
    const { result, viewport } = setup();
    vi.spyOn(viewport.current, "getBoundingClientRect").mockReturnValue({ left: 100 } as DOMRect);
    viewport.current.scrollLeft = 40;
    const at = result.current.scale!.toTime(200 + 40 - 24)!;
    const screenX = () => result.current.scale!.toX(at)! + 24 - viewport.current.scrollLeft;
    expect(screenX()).toBeCloseTo(200);
    act(() => result.current.zoomAt(2, 300));
    expect(result.current.scale!.pixelsPerMinute).toBeGreaterThan(0);
    expect(screenX()).toBeCloseTo(200, 6);
    act(() => result.current.zoomAt(1e6, 300));
    expect(result.current.scale!.pixelsPerMinute).toBe(60);
    act(() => result.current.zoomAt(1e-6, 300));
    expect(result.current.atMinimum).toBe(true);
  });

  it("animates scale changes over 180ms unless reduced motion is requested", () => {
    const { result } = setup();
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    let frame: FrameRequestCallback = () => {};
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frame = callback; return 1; });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.spyOn(performance, "now").mockReturnValue(0);
    const before = result.current.scale!.pixelsPerMinute;
    act(() => result.current.change(1));
    expect(result.current.scale!.pixelsPerMinute).toBe(before);
    act(() => frame(90));
    expect(result.current.scale!.pixelsPerMinute).toBeGreaterThan(before);
    expect(result.current.scale!.pixelsPerMinute).toBeLessThan(before * 2);
    act(() => frame(180));
    expect(result.current.scale!.pixelsPerMinute).toBeCloseTo(before * 2);
    vi.restoreAllMocks();
  });
});
