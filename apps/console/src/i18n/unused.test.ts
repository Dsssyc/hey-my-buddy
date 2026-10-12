import { describe, expect, it } from "vitest";
import zh from "./zh/buddies";

/**
 * A word that no view says is dead text in two languages: the copy of the Buddy
 * page holds only keys that the page's sources use, whole or as the prefix of a
 * key built from a state (`preference.${mode}`). Replaced views take their words
 * with them.
 */
const sources = import.meta.glob<string>(["../App.tsx", "../BuddyConfig.tsx", "../buddies/*.{ts,tsx}", "../components/**/*.{ts,tsx}"], { query: "?raw", import: "default", eager: true });
const support = /\.(test|testing|fixtures)\.tsx?$/;
const text = Object.entries(sources).filter(([path]) => !support.test(path)).map(([, source]) => source).join("\n");

function leaves(node: unknown, path: string[] = []): string[] {
  return node !== null && typeof node === "object"
    ? Object.entries(node).flatMap(([key, child]) => leaves(child, [...path, key]))
    : [path.join(".")];
}

describe("the Buddy copy", () => {
  it("has no key that no view uses", () => {
    const unused = leaves(zh).filter(key => {
      const parts = key.split(".");
      // A key is used whole, or by a prefix that a state completes: `harness.status.${key}`.
      const prefixes = parts.map((_, index) => `${parts.slice(0, index + 1).join(".")}${index + 1 < parts.length ? "." : ""}`);
      return !text.includes(`"${key}"`) && !text.includes(`'${key}'`) && !prefixes.slice(0, -1).some(prefix => text.includes(`\`${prefix}\${`) || text.includes(`"${prefix}" +`));
    });
    expect(unused).toEqual([]);
  });
});
