import { describe, expect, it } from "vitest";

const sources = import.meta.glob<string>(["../**/*.{tsx,css}", "!../**/*.test.tsx", "!../**/*.fixtures.tsx", "!../**/*.testing.tsx"], { query: "?raw", import: "default", eager: true });

describe("the console's shared design vocabulary", () => {
  it("uses theme colours, the four text sizes and component explanations", () => {
    const violations: string[] = [];
    for (const [path, source] of Object.entries(sources)) {
      if (path.endsWith("tokens.css")) continue;
      if (/#[\da-f]{3,8}\b|\brgba?\(|!important|\b(?:bg|text|border)-(?:black|white|(?:red|green|blue|gray|slate|amber|orange|purple)-\d+)/i.test(source)) violations.push(`${path}: colour or important`);
      if (/<[a-z][^>]*\stitle\s*=/s.test(source)) violations.push(`${path}: native title`);
      if (/\btext-(?:[2-9]?xl|\[(?!12px\]|14px\]|16px\]|18px\])\d+(?:px|rem|em)\])|fontSize\s*:/.test(source)) violations.push(`${path}: text size`);
      if (/--(?:canvas|subtle|muted|faint|line-strong|accent(?:-wash|-dark|-soft|-line)?|on-accent|warning(?:-wash|-line)?|danger(?:-wash|-line)?|on-danger|focus|edit-(?:surface|line|wash)|pref-[\w-]+)\b/.test(source)) violations.push(`${path}: retired token`);
    }
    expect(violations).toEqual([]);
  });
});
