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

const gapPx = (gap: { fromPercent: number; toPercent: number }, widthPx: number) => (gap.toPercent - gap.fromPercent) * widthPx / 100;

describe("timeline track pixel scale", () => {
  it("keeps a folded gap at 64px when the track has room, without changing its times", () => {
    const layout = createTimelineLayout(data());
    const original = JSON.stringify(layout.gaps);
    for (const viewport of [400, 800, 1600, 3200]) {
      const scaled = scaleTimeline(layout, viewport);
      expect(scaled.widthPx).toBe(viewport);
      expect(scaled.gaps[0].id).toBe(layout.gaps[0].id);
      expect(gapPx(scaled.gaps[0], scaled.widthPx)).toBeCloseTo(64, 8);
      expect(scaled.position(scaled.startMs)).toBeCloseTo(0);
      expect(scaled.position(scaled.endMs)).toBeCloseTo(100);
    }
    expect(JSON.stringify(layout.gaps)).toBe(original);
  });

  it("fits three breaks into a 380px track: the track stays 380px and breaks share at most 40%", () => {
    const layout = createTimelineLayout(data(4));
    expect(layout.gaps.filter(gap => gap.collapsed)).toHaveLength(3);
    const scaled = scaleTimeline(layout, 380);
    // Fit wins over the fixed band width: no 480px canvas, no overflow.
    expect(scaled.widthPx).toBe(380);
    const bands = scaled.gaps.filter(gap => gap.collapsed).map(gap => gapPx(gap, 380));
    for (const band of bands) expect(band).toBeLessThanOrEqual(64 + 1e-9);
    expect(bands.reduce((sum, band) => sum + band, 0)).toBeCloseTo(380 * 0.4, 6);
    expect(scaled.position(scaled.endMs)).toBeCloseTo(100);
  });

  it("fits a narrow 260px track (500px column minus 240px labels) without a 380px floor", () => {
    const layout = createTimelineLayout(data(4));
    const scaled = scaleTimeline(layout, 260);
    expect(scaled.widthPx).toBe(260);
    const total = scaled.gaps.filter(gap => gap.collapsed).reduce((sum, gap) => sum + gapPx(gap, 260), 0);
    expect(total).toBeCloseTo(104, 6);
    expect(fitPixelsPerMinute(layout, 260)).toBeCloseTo((260 - 104) / 40, 8);
  });

  it("shrinks many breaks inside the fitted track and preserves a monotonic axis", () => {
    const scaled = scaleTimeline(createTimelineLayout(data(25)), 800);
    expect(scaled.widthPx).toBe(800);
    for (const gap of scaled.gaps) {
      expect(gapPx(gap, scaled.widthPx)).toBeCloseTo(800 * 0.4 / 24, 8);
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

  it("scales real time by an explicit px/minute while folded breaks return to 64px", () => {
    const layout = createTimelineLayout(data());
    const fit = scaleTimeline(layout, 800);
    const zoomed = scaleTimeline(layout, 800, 60);
    expect(zoomed.widthPx).toBeGreaterThan(fit.widthPx);
    for (const gap of zoomed.gaps) {
      if (gap.collapsed) expect(gapPx(gap, zoomed.widthPx)).toBeCloseTo(64, 8);
    }
    // A zoom below the fit scale clamps back to fit.
    const floored = scaleTimeline(layout, 800, 0.01);
    expect(floored.widthPx).toBe(fit.widthPx);
    expect(floored.position(at(135))).toBeCloseTo(fit.position(at(135))!, 8);
  });

  it("widens shrunken breaks continuously from the fit width back to 64px as zoom grows", () => {
    const layout = createTimelineLayout(data(4));
    const fitPpm = fitPixelsPerMinute(layout, 380);
    let previousBand = 0;
    let previousWidth = 0;
    for (const factor of [1, 1.05, 1.2, 1.5, 2.25, 5]) {
      const scaled = scaleTimeline(layout, 380, fitPpm * factor);
      const band = gapPx(scaled.gaps.find(gap => gap.collapsed)!, scaled.widthPx);
      expect(band).toBeGreaterThanOrEqual(previousBand - 1e-9);
      expect(band).toBeLessThanOrEqual(64 + 1e-9);
      expect(scaled.widthPx).toBeGreaterThanOrEqual(previousWidth);
      previousBand = band;
      previousWidth = scaled.widthPx;
    }
    expect(gapPx(scaleTimeline(layout, 380, fitPpm).gaps.find(gap => gap.collapsed)!, 380)).toBeCloseTo(380 * 0.4 / 3, 6);
    expect(previousBand).toBeCloseTo(64, 8);
  });

  it("reports the fit scale in px/minute from the available width", () => {
    const layout = createTimelineLayout(data());
    const ppm = fitPixelsPerMinute(layout, 800);
    const fit = scaleTimeline(layout, 800);
    expect(ppm).toBeGreaterThan(0);
    expect(Number.isFinite(ppm)).toBe(true);
    expect(fit.widthPx).toBe(800);
    expect(ppm).toBeCloseTo((800 - 64) / 20, 8);
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
