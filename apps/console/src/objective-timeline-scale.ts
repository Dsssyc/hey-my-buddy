import type { TimelineLayout } from "./objective-timeline-layout";

const GAP_PX = 64;
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
 * The 适应窗口 scale in pixels per real minute (0.16 P2.3): computed from the
 * available track width with no minimum px/minute floor, so a first open never
 * forces horizontal scrolling. The only floor keeps the folded breaks within
 * their 40% budget of the canvas.
 */
export function fitPixelsPerMinute(layout: TimelineLayout, viewportWidth: number): number {
  const { foldedMs, durationMs } = layoutNumbers(layout);
  const collapsed = collapsedCount(layout);
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  // Retain the normalized layout's aggregate 40% fold budget: many breaks grow
  // the canvas rather than making either a break or real time vanish.
  const fitWidth = Math.max(viewport, collapsed * GAP_PX / 0.4);
  const realMinutes = realMs / 60_000;
  if (realMinutes <= 0) return Number.POSITIVE_INFINITY;
  return Math.max((fitWidth - collapsed * GAP_PX) / realMinutes, 0);
}

/**
 * Fit the normalized layout to a scrollable track. Occupancy, gap identities,
 * folding and timestamps remain the layout module's decisions; this adapter
 * only makes each collapsed break 64px at the current viewport size.
 *
 * Without `pixelsPerMinute` the view fits the viewport (适应窗口). With it the
 * real-time scale is fixed at that px/minute — folded breaks stay 64px — and
 * the canvas may scroll horizontally; the value is clamped to at least the
 * fit scale and at most MAX_PIXELS_PER_MINUTE so zoom never inverts.
 */
export function scaleTimeline(layout: TimelineLayout, viewportWidth: number, pixelsPerMinute?: number): TimelineLayout & { widthPx: number } {
  const collapsed = layout.gaps.filter(gap => gap.collapsed);
  const { foldedMs, foldedPercent, durationMs } = layoutNumbers(layout);
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  const fitWidth = Math.max(viewport, collapsed.length * GAP_PX / 0.4);
  let widthPx: number;
  if (pixelsPerMinute !== undefined && Number.isFinite(pixelsPerMinute)) {
    const zoomed = realMs / 60_000 * pixelsPerMinute + collapsed.length * GAP_PX;
    widthPx = Math.max(fitWidth, zoomed);
  } else {
    widthPx = fitWidth;
  }
  const realScale = (widthPx - collapsed.length * GAP_PX) / (100 - foldedPercent);

  function remap(percent: number): number {
    let previous = 0, pixels = 0;
    for (const gap of collapsed) {
      if (percent < gap.fromPercent) return 100 * (pixels + (percent - previous) * realScale) / widthPx;
      pixels += (gap.fromPercent - previous) * realScale;
      if (percent <= gap.toPercent) {
        const fraction = (percent - gap.fromPercent) / (gap.toPercent - gap.fromPercent);
        return 100 * (pixels + fraction * GAP_PX) / widthPx;
      }
      pixels += GAP_PX;
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
