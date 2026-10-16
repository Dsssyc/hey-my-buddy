import { describe, expect, it } from "vitest";

/**
 * Nothing on the Buddy page changes colour in a jump or opens without moving,
 * and nothing moves for a reader who asks for less motion. What a transition is
 * made of is computed style, which the browser checks assert; this reads the
 * class lists for the rule they all follow: a list that changes colour on hover
 * or on a state carries a transition of 100 to 150ms and switches it off under
 * `motion-reduce`, and what opens and closes does so through the Collapsible.
 */
const views = import.meta.glob<string>(["../App.tsx", "../buddies/*.tsx", "../task-detail/*.tsx"], { query: "?raw", import: "default", eager: true });
const shared = import.meta.glob<string>(["./ui/*.tsx"], { query: "?raw", import: "default", eager: true });
const support = /\.(test|testing|fixtures)\.tsx?$/;
const changes = /\b(?:hover|aria-pressed|aria-checked|aria-expanded|aria-selected):(?:[a-z-]+:)*(?:bg|text|border|ring|opacity)-/;
const underline = /^(?:hover|focus-visible):underline$/;

/** Every string literal that is a class list, the way the components write them. */
function classLists(source: string): string[] {
  return [...source.matchAll(/"([^"\n]*)"/g)].map(match => match[1]).filter(list => /[a-z]+-[a-z0-9[]/.test(list) && list.includes(" "));
}

describe("the colour changes of the Buddy page", () => {
  it("each class list that changes colour carries a transition and gives it up under reduced motion", () => {
    const bare: string[] = [];
    for (const [path, source] of Object.entries(views).filter(([path]) => !support.test(path))) {
      for (const list of classLists(source)) {
        if (!changes.test(list) || list.split(" ").every(name => !changes.test(name) || underline.test(name))) continue;
        if (!/\btransition(?:-[a-z[\],-]+)?\b/.test(list) || !list.includes("motion-reduce:transition-none")) bare.push(`${path}: ${list.slice(0, 90)}`);
        else expect(list, path).toMatch(/\bduration-(?:100|150)\b/);
      }
    }
    expect(bare).toEqual([]);
  });

  it("each shared component that changes colour does the same", () => {
    const bare: string[] = [];
    for (const [path, source] of Object.entries(shared).filter(([path]) => !support.test(path))) {
      // Floating surfaces and tabs are not on this page; they enter with the library's own animation.
      if (/tooltip|dialog|popover|select|tabs|badge/.test(path)) continue;
      if (!changes.test(source)) continue;
      if (!source.includes("motion-reduce:transition-none") || !/\bduration-(?:100|150)\b/.test(source)) bare.push(path);
    }
    expect(bare).toEqual([]);
  });

  it("the Collapsible moves for 200ms on the height Radix measures, unless motion is reduced, and its chevron turns in step", () => {
    const source = shared["./ui/collapsible.tsx"];
    expect(source).toContain("--radix-collapsible-content-height");
    expect(source).toContain("animation-duration-200");
    expect(source).toContain("motion-safe:data-[state=open]:animate-collapsible-down");
    expect(source).toContain("motion-safe:data-[state=closed]:animate-collapsible-up");
    expect(source).toMatch(/transition-transform duration-200 motion-reduce:transition-none/);
    // No height of ours: the page measures nothing.
    for (const [path, view] of Object.entries(views).filter(([path]) => !support.test(path)))
      expect(view, path).not.toMatch(/getBoundingClientRect|scrollHeight|offsetHeight|ResizeObserver/);
  });
});
