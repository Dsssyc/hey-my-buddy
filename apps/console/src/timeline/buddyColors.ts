import type { LABColor } from "iwanthue";
import { labToRgb, labToRgbHex, validateRgb } from "iwanthue/helpers";
import { buddyColorKey } from "../macro-task-display";
import type { TimelineSpan } from "../macro-task-types";
import { parseTimelineInstant } from "./instant";

export const BUDDY_EXCLUDED_HUE_RANGES = [[320, 80], [100, 200]] as const;

type Candidate = { lab: LABColor; hex: string };

// A fixed D65 Lab grid makes the palette reproducible. Hue intervals occupied
// by failure, human handoff, the live ruler and the brand mark are excluded.
// The order also breaks equal-distance ties deterministically.
const candidates: Candidate[] = [];
const nearestDistanceSquared: number[] = [];
const colors: string[] = [];

const seed: LABColor = [60, 33 * Math.cos(250 * Math.PI / 180), 33 * Math.sin(250 * Math.PI / 180)];

function squaredDistance(left: LABColor, right: LABColor): number {
  return (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2 + (left[2] - right[2]) ** 2;
}

function prepareCandidates(): void {
  if (colors.length) return;
  const usedHex = new Set([labToRgbHex(seed)]);
  for (let lightness = 54; lightness <= 66; lightness++) {
    for (let chroma = 24; chroma <= 48; chroma += 2) {
      for (let hue = 0; hue < 360; hue += 2) {
        if (BUDDY_EXCLUDED_HUE_RANGES.some(([start, end]) => start <= end ? hue >= start && hue <= end : hue >= start || hue <= end)) continue;
        const radians = hue * Math.PI / 180;
        const lab: LABColor = [lightness, chroma * Math.cos(radians), chroma * Math.sin(radians)];
        if (!validateRgb(labToRgb(lab))) continue;
        const hex = labToRgbHex(lab);
        if (usedHex.has(hex)) continue;
        usedHex.add(hex);
        candidates.push({ lab, hex });
        nearestDistanceSquared.push(squaredDistance(lab, seed));
      }
    }
  }
  colors.push(labToRgbHex(seed));
}

function extendPalette(): void {
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
  if (count > 0) prepareCandidates();
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

/** CIE Lab lightness changes; chromatic coordinates stay fixed for a running execution. */
export function runningBuddyColor(color: string): string {
  return `lab(from ${color} calc(l + 9) a b)`;
}

/** The luminous leading edge keeps the same chromatic coordinates. */
export function leadingBuddyColor(color: string): string {
  return `lab(from ${color} calc(l + 27) a b)`;
}
