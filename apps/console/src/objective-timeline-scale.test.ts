import { describe, expect, it } from "vitest";
import type { ObjectiveTimeline, TimelineSpan } from "./objective-types";
import { createTimelineLayout } from "./objective-timeline-layout";
import { fitPixelsPerMinute, scaleTimeline, TIMELINE_IDLE_PX } from "./objective-timeline-scale";

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

const gapPx = (gap: { fromPercent: number; toPercent: number }, widthPx: number) => (gap.toPercent - gap.fromPercent) * widthPx / 100;

describe("timeline track pixel scale", () => {
  it("keeps every idle break at exactly 32px across widths, counts and zoom", () => {
    expect(TIMELINE_IDLE_PX).toBe(32);
    for (const count of [2, 4, 25, 80]) {
      const layout = createTimelineLayout(data(count));
      const original = JSON.stringify(layout.gaps);
      for (const viewport of [20, 100, 260, 400, 800, 1600]) {
        const fitPpm = fitPixelsPerMinute(layout, viewport);
        for (const ppm of [undefined, fitPpm, fitPpm * 1.5, 60, 0, -1, Number.NaN]) {
          const scaled = scaleTimeline(layout, viewport, ppm);
          expect(Number.isFinite(scaled.widthPx)).toBe(true);
          expect(scaled.widthPx).toBeGreaterThanOrEqual(viewport);
          for (const gap of scaled.gaps) {
            expect(gapPx(gap, scaled.widthPx)).toBeCloseTo(32, 7);
            expect(scaled.position(gap.startMs)).toBeCloseTo(gap.fromPercent, 8);
            expect(scaled.position(gap.endMs)).toBeCloseTo(gap.toPercent, 8);
          }
          expect(scaled.position(scaled.startMs)).toBeCloseTo(0);
          expect(scaled.position(scaled.endMs)).toBeCloseTo(100);
        }
      }
      expect(JSON.stringify(layout.gaps)).toBe(original);
    }
  });

  it("allows necessary overflow and preserves positive real activity and monotonic times", () => {
    const layout = createTimelineLayout(data(80));
    const scaled = scaleTimeline(layout, 260);
    expect(scaled.widthPx).toBe(79 * 32 + 64);
    expect(fitPixelsPerMinute(layout, 260)).toBeCloseTo(64 / 800, 8);
    let previous = -1;
    for (let minute = 0; minute <= 10280; minute++) {
      const current = scaled.position(at(minute))!;
      expect(Number.isFinite(current)).toBe(true);
      expect(current).toBeGreaterThanOrEqual(previous - 1e-9);
      previous = current;
    }
    for (const span of data(80).spans) {
      expect(scaled.position(span.endAt)! - scaled.position(span.startAt)!).toBeGreaterThan(0);
    }
  });

  it("fits ordinary narrow views and clamps lower zoom back to fit", () => {
    const layout = createTimelineLayout(data(4));
    for (const viewport of [260, 380, 800]) {
      const fit = scaleTimeline(layout, viewport);
      expect(fit.widthPx).toBe(viewport);
      expect(fitPixelsPerMinute(layout, viewport)).toBeCloseTo((viewport - 3 * 32) / 40, 8);
      const lower = scaleTimeline(layout, viewport, 0.01);
      expect(lower.widthPx).toBe(fit.widthPx);
      expect(lower.position(at(135))).toBeCloseTo(fit.position(at(135))!, 8);
    }
  });

  it("keeps incomplete and unreliable reads proportional and never invents idle", () => {
    for (const slice of [{ ...data(), scopeComplete: false },
      { ...data(), spans: data().spans.map(span => ({ ...span, clockSkew: true })) }]) {
      const scaled = scaleTimeline(createTimelineLayout(slice), 100);
      expect(scaled.widthPx).toBe(100);
      expect(scaled.canFold).toBe(false);
      expect(scaled.gaps.every(gap => !gap.collapsed)).toBe(true);
      expect(scaled.position(at(70))).toBeCloseTo(50);
      expect(scaled.position("bad timestamp")).toBeNull();
    }
  });

  it("handles invalid viewports, empty and point-only layouts without inventing extent", () => {
    for (const viewport of [Number.NaN, Infinity, -10, 0]) {
      const empty = scaleTimeline(createTimelineLayout({ ...data(), spans: [] }), viewport);
      expect(empty.widthPx).toBe(800);
      expect(empty.startMs).toBeNull();
      expect(empty.position(at(0))).toBeNull();
    }
    const point = data(1);
    point.spans[0].endAt = point.spans[0].startAt;
    const scaled = scaleTimeline(createTimelineLayout(point), 800);
    expect(scaled.position(at(0))).toBe(50);
    expect(scaled.startMs).toBe(scaled.endMs);
  });
});
