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
    expect(fresh.buddyColorPrefix(10)).toEqual(fresh.BUDDY_CORE_COLORS);
  });

  it("keeps all ten core colours apart and measures every ordered prefix", async () => {
    const { BUDDY_CORE_COLORS, buddyColorPrefix } = await import("./buddyColors");
    expect(BUDDY_CORE_COLORS).toHaveLength(10);
    expect(new Set(BUDDY_CORE_COLORS).size).toBe(10);
    const labs = buddyColorPrefix(10).map(rgbHexToLab);
    // Bounds are measured on the displayed 8-bit values, for every prefix 2–10.
    expect(leastPairDistance(labs, 1)).toBe(Infinity);
    const prefixBounds = [116.4, 82.9, 65.5, 58.3, 48.3, 46.9, 46.7, 46.3, 38.8];
    for (const [index, threshold] of prefixBounds.entries()) {
      expect(leastPairDistance(labs, index + 2), `prefix ${index + 2}`).toBeGreaterThanOrEqual(threshold);
    }
    expect(leastPairDistance(labs, 10)).toBeGreaterThanOrEqual(25);
  });

  it("excludes muddy warm colours while allowing clean red, coral, orange and green", async () => {
    const { isUnwantedBuddyColor } = await import("./buddyColors");
    for (const color of ["#8b4513", "#808000", "#999229", "#b27f4e"]) expect(isUnwantedBuddyColor(rgbHexToLab(color)), color).toBe(true);
    for (const color of ["#ff0000", "#ff7f50", "#fa881a", "#117b2d", "#acec6e"]) expect(isUnwantedBuddyColor(rgbHexToLab(color)), color).toBe(false);
  });

  it("extends beyond ten while respecting semantic and rejected colours, core distances and both themes' inks", async () => {
    const { buddyColorPrefix, buddyUsesLightInk, BUDDY_STATUS_DISTANCE, BUDDY_EXTENSION_CORE_DISTANCE, BUDDY_REJECTED_COLORS } = await import("./buddyColors");
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
      const chroma = Math.hypot(lab[1], lab[2]);
      expect(angle >= 70 && angle <= 135 && lab[0] < 76, `${color} is dark yellow/olive`).toBe(false);
      expect(angle >= 30 && angle < 70 && (chroma < 55 || angle >= 45 && lab[0] < 55), `${color} is brown`).toBe(false);
      for (const token of tokens) expect(labDistance(lab, rgbHexToLab(token)), `${color} / ${token}`).toBeGreaterThanOrEqual(BUDDY_STATUS_DISTANCE);
      for (const rejected of BUDDY_REJECTED_COLORS) expect(labDistance(lab, rgbHexToLab(rejected)), `${color} / rejected ${rejected}`).toBeGreaterThanOrEqual(BUDDY_STATUS_DISTANCE);
      if (index >= 10) for (const core of labs.slice(0, 10)) expect(labDistance(lab, core)).toBeGreaterThanOrEqual(BUDDY_EXTENSION_CORE_DISTANCE);
      const inks = buddyUsesLightInk(color) ? lightInks : executionInks;
      for (const ink of inks) expect((Math.max(luminance(color), luminance(ink)) + 0.05) / (Math.min(luminance(color), luminance(ink)) + 0.05), `${color} / ${ink}`).toBeGreaterThanOrEqual(4.5);
    }
    // Both ink branches must be exercised by the core set.
    expect(palette.slice(0, 10).some(buddyUsesLightInk)).toBe(true);
    expect(palette.slice(0, 10).some(color => !buddyUsesLightInk(color))).toBe(true);
  });
});
