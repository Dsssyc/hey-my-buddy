import { describe, expect, it } from "vitest";

/** User content stays in records; built-in copy and punctuation belong to language namespaces. */
const sources = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../i18n/**", "!../**/*-fixtures.ts"], { query: "?raw", import: "default", eager: true });
const chinese = /[　-〿一-鿿＀-￯]/;
const support = /\.(test|testing|fixtures)\.tsx?$/;

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

describe("all console production sources", () => {
  it("carry no text of one language", () => {
    const files = Object.keys(sources).filter(path => !support.test(path));
    expect(files.length).toBeGreaterThan(30);
    const found = files.flatMap(path => code(sources[path]).split("\n").filter(line => chinese.test(line)).map(line => `${path}: ${line.trim()}`));
    expect(found).toEqual([]);
  });
});
