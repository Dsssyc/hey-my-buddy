import type { TimelineLayout } from "./objective-timeline-layout";

const GAP_PX = 64;
/** The largest share of a fitted track all folded breaks may occupy together. */
const GAP_BUDGET_SHARE = 0.4;
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

/**
 * The width of one folded break on a track of `trackPx`: at most GAP_PX, and
 * all breaks together at most 40% of the actual track, so 适应窗口 always
 * fits the viewport instead of forcing horizontal overflow. Times and counts
 * are never dropped to make room — the breaks shrink.
 */
function fitGapWidth(trackPx: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(GAP_PX, trackPx * GAP_BUDGET_SHARE / count);
}

/**
 * The 适应窗口 scale in pixels per real minute (0.16 P2.3): computed from the
 * available track width with no minimum px/minute floor, so a first open never
 * forces horizontal scrolling.
 */
export function fitPixelsPerMinute(layout: TimelineLayout, viewportWidth: number): number {
  const { foldedMs, durationMs } = layoutNumbers(layout);
  const collapsed = collapsedCount(layout);
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  const gapPx = fitGapWidth(viewport, collapsed);
  const realMinutes = realMs / 60_000;
  if (realMinutes <= 0) return Number.POSITIVE_INFINITY;
  return Math.max((viewport - collapsed * gapPx) / realMinutes, 0);
}

/**
 * Fit the normalized layout to a scrollable track. Occupancy, gap identities,
 * folding and timestamps remain the layout module's decisions; this adapter
 * only sizes each collapsed break — at most 64px, and together at most 40% of
 * the actual track while fitting.
 *
 * Without `pixelsPerMinute` the view fits the viewport exactly (适应窗口):
 * the track is the viewport width and breaks shrink within their budget. With
 * `pixelsPerMinute` the real-time scale is fixed at that px/minute and the
 * canvas may scroll horizontally; folded breaks widen continuously from their
 * fit width back to 64px as the zoom grows, so no zoom step jumps. The value
 * is clamped to at least the fit scale so zoom never inverts.
 */
export function scaleTimeline(layout: TimelineLayout, viewportWidth: number, pixelsPerMinute?: number): TimelineLayout & { widthPx: number } {
  const collapsed = layout.gaps.filter(gap => gap.collapsed);
  const { foldedMs, foldedPercent, durationMs } = layoutNumbers(layout);
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  let widthPx: number;
  let gapPx: number;
  const fitPpm = fitPixelsPerMinute(layout, viewport);
  if (pixelsPerMinute !== undefined && Number.isFinite(pixelsPerMinute) && Number.isFinite(fitPpm) && fitPpm > 0) {
    const ppm = Math.max(pixelsPerMinute, fitPpm);
    // Breaks grow with the zoom factor from their fit width until 64px, so
    // the zoom level equal to fit renders exactly as fit.
    gapPx = Math.min(GAP_PX, fitGapWidth(viewport, collapsed.length) * ppm / fitPpm);
    widthPx = Math.max(viewport, realMs / 60_000 * ppm + collapsed.length * gapPx);
  } else {
    widthPx = viewport;
    gapPx = fitGapWidth(viewport, collapsed.length);
  }
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
