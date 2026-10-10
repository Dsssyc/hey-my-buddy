import type { TimelineLayout } from "./objective-timeline-layout";

/** Every proven idle break keeps this pixel width, at every zoom and count. */
export const TIMELINE_IDLE_PX = 32;
/** Keep real activity readable when fixed breaks alone consume the viewport. */
const MIN_REAL_TRACK_PX = 64;
/** The largest zoom level (0.16 P2.3): +60px per minute is disabled beyond this. */
export const MAX_PIXELS_PER_MINUTE = 60;

function layoutNumbers(layout: TimelineLayout): { foldedMs: number; foldedPercent: number; durationMs: number } {
  const collapsed = layout.gaps.filter(gap => gap.collapsed);
  const foldedPercent = collapsed.reduce((sum, gap) => sum + gap.toPercent - gap.fromPercent, 0);
  const foldedMs = collapsed.reduce((sum, gap) => sum + gap.endMs - gap.startMs, 0);
  const durationMs = layout.startMs === null || layout.endMs === null ? 0 : layout.endMs - layout.startMs;
  return { foldedMs, foldedPercent, durationMs };
}

function collapsedCount(layout: TimelineLayout): number {
  return layout.gaps.filter(gap => gap.collapsed).length;
}

/** Fit real activity into the room left after fixed idle blocks, allowing necessary overflow. */
export function fitPixelsPerMinute(layout: TimelineLayout, viewportWidth: number): number {
  const { foldedMs, durationMs } = layoutNumbers(layout);
  const collapsed = collapsedCount(layout);
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  const gapTotalPx = collapsed * TIMELINE_IDLE_PX;
  const realMinutes = realMs / 60_000;
  if (realMinutes <= 0) return Number.POSITIVE_INFINITY;
  const realPx = collapsed > 0 ? Math.max(viewport - gapTotalPx, MIN_REAL_TRACK_PX) : viewport;
  return realPx / realMinutes;
}

/**
 * Size the existing monotonic map with fixed 32px idle blocks. Occupancy,
 * eligibility and times remain the layout's decisions. Fit may scroll when
 * many blocks leave too little room for real activity; neither blocks nor the
 * real-time axis are squeezed to zero. Manual zoom clamps to the fit scale.
 */
export function scaleTimeline(layout: TimelineLayout, viewportWidth: number, pixelsPerMinute?: number): TimelineLayout & { widthPx: number } {
  const collapsed = layout.gaps.filter(gap => gap.collapsed);
  const { foldedMs, foldedPercent, durationMs } = layoutNumbers(layout);
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  const fitPpm = fitPixelsPerMinute(layout, viewport);
  const ppm = pixelsPerMinute !== undefined && Number.isFinite(pixelsPerMinute)
    ? Math.max(pixelsPerMinute, fitPpm) : fitPpm;
  const gapPx = TIMELINE_IDLE_PX;
  const widthPx = realMs > 0
    ? Math.max(viewport, realMs / 60_000 * ppm + collapsed.length * gapPx)
    : viewport;
  const realScale = (widthPx - collapsed.length * gapPx) / (100 - foldedPercent);

  function remap(percent: number): number {
    let previous = 0, pixels = 0;
    for (const gap of collapsed) {
      if (percent < gap.fromPercent) return 100 * (pixels + (percent - previous) * realScale) / widthPx;
      pixels += (gap.fromPercent - previous) * realScale;
      if (percent <= gap.toPercent) {
        const fraction = (percent - gap.fromPercent) / (gap.toPercent - gap.fromPercent);
        return 100 * (pixels + fraction * gapPx) / widthPx;
      }
      pixels += gapPx;
      previous = gap.toPercent;
    }
    return 100 * (pixels + (percent - previous) * realScale) / widthPx;
  }

  return {
    ...layout,
    widthPx,
    gaps: layout.gaps.map(gap => ({ ...gap, fromPercent: remap(gap.fromPercent), toPercent: remap(gap.toPercent) })),
    position(timestamp) {
      const percent = layout.position(timestamp);
      return percent === null ? null : remap(percent);
    },
  };
}
