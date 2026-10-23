import type { LABColor } from "iwanthue";
import { labToRgb, labToRgbHex, rgbHexToLab, validateRgb } from "iwanthue/helpers";
import tokens from "../tokens.css?raw";
import { buddyColorKey } from "../macro-task-display";
import type { TimelineSpan } from "../macro-task-types";
import { parseTimelineInstant } from "./instant";

/** Curated sRGB colours; the order spreads every prefix, especially the first six. */
export const BUDDY_CORE_COLORS = [
  "#025387", // 深蓝 / Deep blue
  "#fa881a", // 橙色 / Orange
  "#117b2d", // 叶绿 / Leaf green
  "#ffc5d9", // 浅粉 / Light pink
  "#93205b", // 深玫红 / Deep rose
  "#66c8e4", // 浅青蓝 / Light cyan
  "#6137a5", // 深紫 / Deep violet
  "#acec6e", // 浅柠绿 / Light lime
  "#fad58e", // 浅金黄 / Light gold
  "#baabfe", // 浅紫 / Lilac
] as const;

/** Developer-rejected colours are reserved just like semantic colours. */
export const BUDDY_REJECTED_COLORS = [
  "#db0129", // 正红 / Crimson: rejected by the developer as too vivid.
  "#fd5878", // 珊瑚红 / Coral: rejected as too similar to the failure cross.
] as const;

export const BUDDY_STATUS_DISTANCE = 25;
export const BUDDY_EXTENSION_CORE_DISTANCE = 12;

// Both themes come from the same tokens that the console renders. No semantic
// hue bands: distance leaves room for individual colours near reserved hues.
const statusLabs = [...tokens.matchAll(/--(?:fail|bar-fail|hand|hand-mark|bar-hand|live|brand-mark|past): (#[0-9a-f]{6});/g)]
  .map(match => rgbHexToLab(match[1]!));
const reservedLabs = [...statusLabs, ...BUDDY_REJECTED_COLORS.map(rgbHexToLab)];
const coreLabs = BUDDY_CORE_COLORS.map(rgbHexToLab);
type Candidate = { lab: LABColor; hex: string };
const candidates: Candidate[] = [];
const nearestDistanceSquared: number[] = [];
const colors: string[] = [...BUDDY_CORE_COLORS];

function squaredDistance(left: LABColor, right: LABColor): number {
  return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2 + (left[2] - right[2]) ** 2;
}

/** One lightness rule for execution text in both themes. */
export function buddyUsesLightInk(color: string): boolean {
  return rgbHexToLab(color)[0] < 52;
}

/** Exclude muddy warm browns and low-lightness yellow/olive; saturated red and orange stay eligible. */
export function isUnwantedBuddyColor([lightness, a, b]: LABColor): boolean {
  const hue = (Math.atan2(b, a) * 180 / Math.PI + 360) % 360;
  const chroma = Math.hypot(a, b);
  return hue >= 70 && hue <= 135 && lightness < 76
    || hue >= 30 && hue < 70 && (chroma < 55 || hue >= 45 && lightness < 55);
}

function prepareCandidates(): void {
  const usedHex = new Set(colors);
  // The extension keeps the existing deterministic farthest-point Lab grid.
  // Measure the rounded sRGB output, which is what the user actually sees.
  for (let lightness = 34; lightness <= 88; lightness += 2) {
    // Neither foreground reaches 4.5:1 in the gap between the two lightness tiers.
    if (lightness === 50 || lightness === 52) continue;
    for (let chroma = 26; chroma <= 84; chroma += 2) {
      for (let hue = 0; hue < 360; hue += 2) {
        const radians = hue * Math.PI / 180;
        const point: LABColor = [lightness, chroma * Math.cos(radians), chroma * Math.sin(radians)];
        if (!validateRgb(labToRgb(point))) continue;
        const hex = labToRgbHex(point);
        if (usedHex.has(hex)) continue;
        const lab = rgbHexToLab(hex);
        if (isUnwantedBuddyColor(lab)) continue;
        if (reservedLabs.some(reserved => squaredDistance(lab, reserved) < BUDDY_STATUS_DISTANCE ** 2)) continue;
        const distance = Math.min(...coreLabs.map(core => squaredDistance(lab, core)));
        if (distance < BUDDY_EXTENSION_CORE_DISTANCE ** 2) continue;
        usedHex.add(hex);
        candidates.push({ lab, hex });
        nearestDistanceSquared.push(distance);
      }
    }
  }
}

function extendPalette(): void {
  if (!candidates.length) prepareCandidates();
  let winner = -1;
  let largestDistance = -1;
  for (let index = 0; index < candidates.length; index++) {
    const distance = nearestDistanceSquared[index]!;
    if (distance > largestDistance) {
      winner = index;
      largestDistance = distance;
    }
  }
  if (winner < 0) throw new RangeError("Buddy color grid exhausted");
  const chosen = candidates[winner]!;
  colors.push(chosen.hex);
  nearestDistanceSquared[winner] = -1;
  for (let index = 0; index < candidates.length; index++) {
    if (nearestDistanceSquared[index]! < 0) continue;
    nearestDistanceSquared[index] = Math.min(nearestDistanceSquared[index]!, squaredDistance(candidates[index]!.lab, chosen.lab));
  }
}

/** The shared sequence, also used by the execution legend's colour sample. */
export function buddyColorPrefix(count: number): readonly string[] {
  while (colors.length < count) extendPalette();
  return colors.slice(0, count);
}

/** Assign the shared palette in order of each buddy's first execution. */
export function buddyColorsFor(spans: readonly TimelineSpan[]): ReadonlyMap<string, string> {
  const keys = [...new Set(spans.filter(span => span.kind === "execution" && parseTimelineInstant(span.startAt) !== null)
    .sort((left, right) => parseTimelineInstant(left.startAt)! - parseTimelineInstant(right.startAt)!)
    .map(span => buddyColorKey(span.buddy)))];
  const palette = buddyColorPrefix(keys.length);
  return new Map(keys.map((key, index) => [key, palette[index]!]));
}

/** Keep text contrast and reserve headroom for pale colours' moving edge. */
export function runningBuddyColor(color: string): string {
  const lightness = buddyUsesLightInk(color) ? "calc(l - 4)" : "min(85, calc(l + 9))";
  return `lab(from ${color} ${lightness} a b)`;
}

/** The moving leading edge stays on the same side of the ink threshold. */
export function leadingBuddyColor(color: string): string {
  return `lab(from ${color} calc(l + ${buddyUsesLightInk(color) ? -12 : 27}) a b)`;
}
