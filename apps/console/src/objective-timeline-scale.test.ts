import { describe, expect, it } from "vitest";
import type { ObjectiveTimeline, TimelineSpan } from "./objective-types";
import { createTimelineLayout } from "./objective-timeline-layout";
import { fitPixelsPerMinute, scaleTimeline } from "./objective-timeline-scale";

const start = Date.parse("2026-09-27T00:00:00Z");
const at = (minute: number) => new Date(start + minute * 60_000).toISOString();
function data(count = 2): Pick<ObjectiveTimeline, "spans" | "events" | "observedAt" | "scopeComplete"> {
  return {
    spans: Array.from({ length: count }, (_, i): TimelineSpan => ({
      spanId: `span-${i}`, runId: "run", kind: "execution", startAt: at(i * 130), endAt: at(i * 130 + 10),
      state: "finished", attemptId: `attempt-${i}`, turnId: null, turnIndex: i + 1, requestId: null,
      configuration: null, shutdownConfirmed: true, uncertain: false, clockSkew: false,
    })),
    events: [], observedAt: at(10000), scopeComplete: true,
  };
}

describe("timeline track pixel scale", () => {
  it("keeps folded gaps exactly 64px across viewport widths without changing their times", () => {
    const layout = createTimelineLayout(data());
    const original = JSON.stringify(layout.gaps);
    for (const viewport of [400, 800, 1600, 3200]) {
      const scaled = scaleTimeline(layout, viewport);
      expect(scaled.widthPx).toBeGreaterThanOrEqual(viewport);
      expect(scaled.gaps[0].id).toBe(layout.gaps[0].id);
      expect((scaled.gaps[0].toPercent - scaled.gaps[0].fromPercent) * scaled.widthPx / 100).toBeCloseTo(64, 8);
      expect(scaled.position(scaled.startMs)).toBeCloseTo(0);
      expect(scaled.position(scaled.endMs)).toBeCloseTo(100);
    }
    expect(JSON.stringify(layout.gaps)).toBe(original);
  });

  it("grows the track for many breaks and preserves a monotonic axis", () => {
    const scaled = scaleTimeline(createTimelineLayout(data(25)), 800);
    expect(scaled.widthPx).toBeGreaterThan(800);
    for (const gap of scaled.gaps) {
      expect((gap.toPercent - gap.fromPercent) * scaled.widthPx / 100).toBeCloseTo(64, 8);
      expect(scaled.position(gap.startMs)).toBeCloseTo(gap.fromPercent, 8);
      expect(scaled.position(gap.endMs)).toBeCloseTo(gap.toPercent, 8);
    }
    let previous = -1;
    for (let minute = 0; minute < 3140; minute++) {
      const current = scaled.position(at(minute))!;
      expect(current).toBeGreaterThanOrEqual(previous - 1e-9);
      previous = current;
    }
  });

  it("restores proportional elapsed time for expanded gaps and never changes uncertainty", () => {
    const layout = createTimelineLayout(data());
    const expanded = createTimelineLayout(data(), new Set(layout.gaps.map(gap => gap.id)));
    const scaled = scaleTimeline(expanded, 100);
    // 适应窗口 has no minimum px/minute floor (0.16 P2.3): the track is exactly
    // the available width instead of forcing horizontal overflow.
    expect(scaled.widthPx).toBe(100);
    expect(scaled.gaps[0].collapsed).toBe(false);
    expect(scaled.position(at(70))).toBeCloseTo(50);
    expect(scaled.canFold).toBe(expanded.canFold);
    expect(scaled.position("bad timestamp")).toBeNull();
  });

  it("scales real time by an explicit px/minute while folded breaks stay 64px", () => {
    const layout = createTimelineLayout(data());
    const fit = scaleTimeline(layout, 800);
    const zoomed = scaleTimeline(layout, 800, 60);
    expect(zoomed.widthPx).toBeGreaterThan(fit.widthPx);
    for (const gap of zoomed.gaps) {
      if (gap.collapsed) {
        expect((gap.toPercent - gap.fromPercent) * zoomed.widthPx / 100).toBeCloseTo(64, 8);
      }
    }
    // A zoom below the fit scale clamps back to fit.
    const floored = scaleTimeline(layout, 800, 0.01);
    expect(floored.widthPx).toBe(fit.widthPx);
  });

  it("reports the fit scale in px/minute from the available width", () => {
    const layout = createTimelineLayout(data());
    const ppm = fitPixelsPerMinute(layout, 800);
    const fit = scaleTimeline(layout, 800);
    expect(ppm).toBeGreaterThan(0);
    expect(Number.isFinite(ppm)).toBe(true);
    expect(fit.widthPx).toBeGreaterThanOrEqual(800);
  });

  it("handles empty and point-only layouts without inventing extent", () => {
    const empty = scaleTimeline(createTimelineLayout({ ...data(), spans: [] }), Number.NaN);
    expect(empty.widthPx).toBe(800);
    expect(empty.startMs).toBeNull();
    expect(empty.position(at(0))).toBeNull();
    const point = data(1);
    point.spans[0].endAt = point.spans[0].startAt;
    const scaled = scaleTimeline(createTimelineLayout(point), 800);
    expect(scaled.position(at(0))).toBe(50);
    expect(scaled.startMs).toBe(scaled.endMs);
  });
});
