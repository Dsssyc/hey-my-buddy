import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { labToHcl, labToRgbHex, rgbHexToLab } from "iwanthue/helpers";
import { buddyColorKey } from "../macro-task-display";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";

const source = macroTaskTimelineFixture().spans.find(span => span.kind === "execution")!;
const spans = Array.from({ length: 40 }, (_, index) => ({
  ...source,
  startAt: new Date(Date.UTC(2026, 0, 1, index)).toISOString(),
  buddy: { ...source.buddy!, model: `model-${index}` },
}));
const tokens = [...readFileSync("src/tokens.css", "utf8")
  .matchAll(/--(?:fail|hand|hand-mark|live|brand-mark): (#[0-9a-f]{6});/g)].map(match => match[1]!);
const executionInks = [...readFileSync("src/tokens.css", "utf8")
  .matchAll(/--execution-ink: (#[0-9a-f]{6});/g)].map(match => match[1]!);
const labDistance = (left: readonly number[], right: readonly number[]) =>
  Math.hypot(left[0]! - right[0]!, left[1]! - right[1]!, left[2]! - right[2]!);
const hue = (lab: [number, number, number]) => labToHcl(lab)[0];
const hueDistance = (left: number, right: number) => Math.min(Math.abs(left - right), 360 - Math.abs(left - right));
const luminance = (color: string) => {
  const components = color.match(/[a-f0-9]{2}/gi)!.map(component => {
    const value = parseInt(component, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return components[0]! * 0.2126 + components[1]! * 0.7152 + components[2]! * 0.0722;
};
const leastPairDistance = (labs: readonly (readonly number[])[], count: number) => {
  let least = Infinity;
  for (let index = 0; index < count; index++) {
    for (let earlier = 0; earlier < index; earlier++) {
      least = Math.min(least, labDistance(labs[index]!, labs[earlier]!));
    }
  }
  return least;
};

describe("derived buddy palette", () => {
  it("extends one deterministic shared prefix across views and polls", async () => {
    vi.resetModules();
    const { buddyColorsFor, buddyColorPrefix } = await import("./buddyColors");
    const first = buddyColorsFor(spans.slice(0, 6));
    expect(buddyColorsFor([...spans.slice(0, 6)].reverse())).toEqual(first);
    const full = buddyColorsFor(spans);
    for (const [key, color] of first) expect(full.get(key)).toBe(color);
    expect(buddyColorsFor([...spans].reverse())).toEqual(full);
    expect(buddyColorPrefix(6)).toEqual([...first.values()]);
    const otherMacro = spans.map(span => ({ ...span, buddy: { ...span.buddy, model: `other-${span.buddy.model}` } }));
    expect([...buddyColorsFor(otherMacro).values()]).toEqual([...full.values()]);
    expect(full.get(buddyColorKey(spans[0]!.buddy))).toBe([...first.values()][0]);
    vi.resetModules();
    const fresh = await import("./buddyColors");
    let previous: readonly string[] = [];
    for (let count = 1; count <= 40; count++) {
      const next = fresh.buddyColorPrefix(count);
      expect(next.slice(0, -1)).toEqual(previous);
      previous = next;
    }
    expect(previous).toEqual([...full.values()]);
    expect(fresh.buddyColorsFor([...spans].reverse())).toEqual(full);
    const seed: [number, number, number] = [60, 33 * Math.cos(250 * Math.PI / 180), 33 * Math.sin(250 * Math.PI / 180)];
    expect(fresh.buddyColorPrefix(1)).toEqual([labToRgbHex(seed)]);
  });

  it("keeps forty colours distinct, legible and clear of semantic hue ranges", async () => {
    vi.resetModules();
    const { buddyColorPrefix, runningBuddyColor, BUDDY_EXCLUDED_HUE_RANGES } = await import("./buddyColors");
    const excluded = (angle: number) => BUDDY_EXCLUDED_HUE_RANGES.some(([start, end]) => start <= end ? angle >= start && angle <= end : angle >= start || angle <= end);
    const palette = buddyColorPrefix(40);
    expect(new Set(palette).size).toBe(40);
    const labs = palette.map(rgbHexToLab);
    for (const [count, threshold] of [[4, 28.5], [6, 28], [8, 18.5], [12, 16], [20, 13], [40, 9]]) {
      expect(leastPairDistance(labs, count)).toBeGreaterThanOrEqual(threshold!);
    }
    expect(tokens).toHaveLength(10);
    expect(executionInks).toHaveLength(2);
    for (const token of tokens) {
      const angle = hue(rgbHexToLab(token));
      expect(excluded(angle)).toBe(true);
      expect(Math.min(...BUDDY_EXCLUDED_HUE_RANGES.flatMap(range => [...range]).map(boundary => hueDistance(angle, boundary)))).toBeGreaterThanOrEqual(30);
    }
    for (const color of palette) {
      const lab = rgbHexToLab(color);
      const [lightness, a, b] = lab;
      const angle = hue(lab);
      // The library emits 8-bit sRGB; tolerate its rounding at gamut edges.
      expect(lightness).toBeGreaterThan(53); expect(lightness).toBeLessThan(67);
      expect(Math.hypot(a, b)).toBeGreaterThan(23); expect(Math.hypot(a, b)).toBeLessThan(49);
      expect(excluded(angle)).toBe(false);
      for (const ink of executionInks) expect((luminance(color) + 0.05) / (luminance(ink) + 0.05)).toBeGreaterThanOrEqual(4.5);
      expect(runningBuddyColor(color)).toBe(`lab(from ${color} calc(l + 9) a b)`);
    }
  });
});
