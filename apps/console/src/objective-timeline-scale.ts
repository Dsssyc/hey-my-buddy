import type { TimelineLayout } from "./objective-timeline-layout";

const GAP_PX = 64;
const MIN_PIXELS_PER_MINUTE = 1.6;

/**
 * Fit the normalized layout to a scrollable track. Occupancy, gap identities,
 * folding and timestamps remain the layout module's decisions; this adapter
 * only makes each collapsed break 64px at the current viewport size.
 */
export function scaleTimeline(layout: TimelineLayout, viewportWidth: number): TimelineLayout & { widthPx: number } {
  const collapsed = layout.gaps.filter(gap => gap.collapsed);
  const foldedPercent = collapsed.reduce((sum, gap) => sum + gap.toPercent - gap.fromPercent, 0);
  const foldedMs = collapsed.reduce((sum, gap) => sum + gap.endMs - gap.startMs, 0);
  const durationMs = layout.startMs === null || layout.endMs === null ? 0 : layout.endMs - layout.startMs;
  const realMs = Math.max(0, durationMs - foldedMs);
  const viewport = Number.isFinite(viewportWidth) && viewportWidth > 0 ? viewportWidth : 800;
  // Retain the normalized layout's aggregate 40% fold budget. Many breaks grow
  // the scrollable track rather than making either a break or real time vanish.
  const widthPx = Math.max(viewport, collapsed.length * GAP_PX / 0.4,
    realMs / 60_000 * MIN_PIXELS_PER_MINUTE + collapsed.length * GAP_PX);
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
