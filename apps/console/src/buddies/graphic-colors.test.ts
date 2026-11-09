import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// OKLab from Björn Ottosson's definition (2021-01-25 matrices):
// https://bottosson.github.io/posts/oklab/#converting-from-linear-srgb-to-oklab
// Machado, Oliveira and Fernandes (2009), severity 1.0, linear sRGB:
// https://www.inf.ufrgs.br/~oliveira/pubs_files/CVD_Simulation/CVD_Simulation.html
const simulations = {
  normal: null,
  protanopia: [[.152286, 1.052583, -.204868], [.114503, .786281, .099216], [-.003882, -.048116, 1.051998]],
  deuteranopia: [[.367322, .860646, -.227968], [.280085, .672501, .047413], [-.011820, .042940, .968881]],
};
const linear = (hex: string) => hex.slice(1).match(/../g)!.map(channel => {
  const value = parseInt(channel, 16) / 255;
  return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
});
function oklab(rgb: number[]) {
  const [r, g, b] = rgb;
  const l = Math.cbrt(.4122214708 * r + .5363325363 * g + .0514459929 * b);
  const m = Math.cbrt(.2119034982 * r + .6806995451 * g + .1073969566 * b);
  const s = Math.cbrt(.0883024619 * r + .2817188376 * g + .6299787005 * b);
  return [.2104542553 * l + .7936177850 * m - .0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + .4505937099 * s,
    .0259040371 * l + .7827717662 * m - .8086757660 * s];
}
function distance(left: string, right: string, matrix: number[][] | null) {
  const convert = (color: string) => {
    const rgb = linear(color);
    return oklab(matrix ? matrix.map(row => Math.max(0, Math.min(1, row.reduce((sum, value, index) => sum + value * rgb[index], 0)))) : rgb);
  };
  const a = convert(left), b = convert(right);
  return 100 * Math.hypot(...a.map((value, index) => value - b[index]));
}
function contrast(left: string, right: string) {
  const luminance = (hex: string) => linear(hex).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  const [low, high] = [luminance(left), luminance(right)].sort((a, b) => a - b);
  return (high + .05) / (low + .05);
}
const css = readFileSync("src/tokens.css", "utf8");
const blocks = css.match(/\{[^}]+\}/g)!;
const groups = [
  ["green", "amber", "sample-pink"], // The three ordered sample outcomes.
  ["green", "sample-pink"], // Zero rework leaves first-pass and failure adjacent.
  ["green", "pink"], // Router answers and missing answers.
  ["green-dark", "green", "orange"], // Input, cached input, output.
  ["green", "pink", "gray"], // Tool success, failure, unrecorded outcome.
  ["green", "gray"], // Tools without failures still distinguish unknown results.
  ["green-dark", "orange"], // Usage with no uncached input.
];

// The cases read the actual theme tokens, so a cosmetic edit cannot silently
// remove the distinctions that a segmented graph needs.
for (const [index, theme] of ["light", "dark"].entries()) describe(`Buddy graphics in ${theme}`, () => {
  const values = new Map([...blocks[index].matchAll(/--([\w-]+): (#[a-f0-9]{6});/g)].map(match => [match[1], match[2]]));
  const token = (name: string) => {
    expect(values.has(name), `missing ${name}`).toBe(true);
    return values.get(name)!;
  };
  for (const group of groups) for (let part = 1; part < group.length; part++) {
    const pair = group.slice(part - 1, part + 1);
    for (const [vision, matrix] of Object.entries(simulations)) it(`${pair.join(" / ")} separates under ${vision}`, () => {
      const [left, right] = pair.map(name => token(`buddy-${name}`));
      expect(distance(left, right, matrix)).toBeGreaterThanOrEqual(vision === "normal" ? 15 : 8);
    });
  }
  for (let band = 0; band < 5; band++) {
    it(`domain band ${band} distinguishes its own count and its children`, () => {
      const own = token(`buddy-domain-${band}`), child = token(`buddy-domain-${band}-child`);
      for (const [vision, matrix] of Object.entries(simulations))
        expect(distance(own, child, matrix), vision).toBeGreaterThanOrEqual(vision === "normal" ? 15 : 8);
      for (const background of ["paper", "surface", "sunk"]) for (const color of [own, child])
        expect(contrast(color, token(background)), `${color} on ${background}`).toBeGreaterThanOrEqual(3);
      expect(oklab(linear(own))[0]).toBeGreaterThan(oklab(linear(child))[0]);
    });
  }
});
