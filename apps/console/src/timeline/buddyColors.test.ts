import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { labToHcl, rgbHexToLab } from "iwanthue/helpers";
import { buddyColorKey } from "../macro-task-display";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";

const source = macroTaskTimelineFixture().spans.find(span => span.kind === "execution")!;
const spans = Array.from({ length: 40 }, (_, index) => ({
  ...source,
  startAt: new Date(Date.UTC(2026, 0, 1, index)).toISOString(),
  buddy: { ...source.buddy!, model: `model-${index}` },
}));
const css = readFileSync("src/tokens.css", "utf8");
const tokens = [...css.matchAll(/--(?:fail|bar-fail|hand|hand-mark|bar-hand|live|brand-mark|past): (#[0-9a-f]{6});/g)].map(match => match[1]!);
const executionInks = [...css.matchAll(/--execution-ink: (#[0-9a-f]{6});/g)].map(match => match[1]!);
const lightInks = [...css.matchAll(/--execution-ink-light: (#[0-9a-f]{6});/g)].map(match => match[1]!);
const labDistance = (left: readonly number[], right: readonly number[]) =>
  Math.hypot(left[0]! - right[0]!, left[1]! - right[1]!, left[2]! - right[2]!);
const hue = (lab: [number, number, number]) => labToHcl(lab)[0];
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

describe("curated buddy palette", () => {
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
    expect(fresh.buddyColorPrefix(12)).toEqual(fresh.BUDDY_CORE_COLORS.map(color => color.hex));
  });

  it("keeps all twelve core colours apart and measures every ordered prefix", async () => {
    const { BUDDY_CORE_COLORS, buddyColorPrefix } = await import("./buddyColors");
    expect(BUDDY_CORE_COLORS).toHaveLength(12);
    for (const key of ["hex", "name"] as const) expect(new Set(BUDDY_CORE_COLORS.map(color => color[key])).size).toBe(12);
    const labs = buddyColorPrefix(12).map(rgbHexToLab);
    // Bounds are measured on the displayed 8-bit values, for every prefix 2–12.
    expect(leastPairDistance(labs, 1)).toBe(Infinity);
    const prefixBounds = [97.8, 61.9, 55.9, 49.9, 37.2, 28.9, 28.3, 28.0, 27.6, 27.4, 27.2];
    for (const [index, threshold] of prefixBounds.entries()) {
      expect(leastPairDistance(labs, index + 2), `prefix ${index + 2}`).toBeGreaterThanOrEqual(threshold);
    }
    expect(leastPairDistance(labs, 12)).toBeGreaterThanOrEqual(25);
  });

  it("extends beyond twelve while respecting semantic colours, core distances and both themes' inks", async () => {
    const { buddyColorPrefix, buddyUsesLightInk, BUDDY_STATUS_DISTANCE, BUDDY_EXTENSION_CORE_DISTANCE } = await import("./buddyColors");
    expect(BUDDY_STATUS_DISTANCE).toBe(25);
    expect(BUDDY_EXTENSION_CORE_DISTANCE).toBe(12);
    expect(tokens).toHaveLength(16);
    expect(executionInks).toHaveLength(2);
    expect(lightInks).toHaveLength(2);
    const palette = buddyColorPrefix(40);
    expect(new Set(palette).size).toBe(40);
    const labs = palette.map(rgbHexToLab);
    for (const [index, color] of palette.entries()) {
      const lab = labs[index]!;
      const angle = hue(lab);
      expect(angle >= 115 && angle <= 220, `${color} belongs to green`).toBe(false);
      expect(angle >= 30 && angle <= 115 && lab[0] < 76, `${color} is dark yellow/orange/brown`).toBe(false);
      for (const token of tokens) expect(labDistance(lab, rgbHexToLab(token)), `${color} / ${token}`).toBeGreaterThanOrEqual(BUDDY_STATUS_DISTANCE);
      if (index >= 12) for (const core of labs.slice(0, 12)) expect(labDistance(lab, core)).toBeGreaterThanOrEqual(BUDDY_EXTENSION_CORE_DISTANCE);
      const inks = buddyUsesLightInk(color) ? lightInks : executionInks;
      for (const ink of inks) expect((Math.max(luminance(color), luminance(ink)) + 0.05) / (Math.min(luminance(color), luminance(ink)) + 0.05), `${color} / ${ink}`).toBeGreaterThanOrEqual(4.5);
    }
    // Both ink branches must be exercised by the core set.
    expect(palette.slice(0, 12).some(buddyUsesLightInk)).toBe(true);
    expect(palette.slice(0, 12).some(color => !buddyUsesLightInk(color))).toBe(true);
  });
});
