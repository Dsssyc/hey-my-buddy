import { describe, expect, it } from "vitest";

/**
 * The rebuilt views take every word and every mark of punctuation from the
 * language files: a sentence joined in code with a Chinese full stop reaches the
 * English page too. This reads the sources of those views and of the shared
 * components and finds no character of the Chinese scripts and no full-width
 * punctuation outside comments.
 */
const sources = import.meta.glob<string>(["../buddies/*.{ts,tsx}", "../components/**/*.{ts,tsx}"], { query: "?raw", import: "default", eager: true });
const chinese = /[　-〿一-鿿＀-￯]/;
const support = /\.(test|testing|fixtures)\.tsx?$/;

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

describe("the rebuilt views' sources", () => {
  it("carry no text of one language", () => {
    const files = Object.keys(sources).filter(path => !support.test(path));
    expect(files.length).toBeGreaterThan(30);
    const found = files.flatMap(path => code(sources[path]).split("\n").filter(line => chinese.test(line)).map(line => `${path}: ${line.trim()}`));
    expect(found).toEqual([]);
  });
});
