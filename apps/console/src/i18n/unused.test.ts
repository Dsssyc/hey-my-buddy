import { describe, expect, it } from "vitest";
import zhBuddies from "./zh/buddies";
import zhSettings from "./zh/settings";
import enSettings from "./en/settings";
import zhStorage from "./zh/settings-storage";
import zhTaskDetail from "./zh/taskDetail";

/**
 * A word that no view says is dead text in two languages: the copy of a page holds
 * only keys that the page's sources use, whole or as the prefix of a key built from a
 * state (`preference.${mode}`). Replaced views take their words with them.
 */
const pages = {
  settings: import.meta.glob<string>(["../Settings.tsx", "../settings/*.{ts,tsx}"], { query: "?raw", import: "default", eager: true }),
  buddies: import.meta.glob<string>(["../App.tsx", "../BuddyConfig.tsx", "../buddies/*.{ts,tsx}", "../components/**/*.{ts,tsx}"], { query: "?raw", import: "default", eager: true }),
  taskDetail: import.meta.glob<string>(["../task-detail/*.{ts,tsx}", "../components/**/*.{ts,tsx}"], { query: "?raw", import: "default", eager: true }),
};
const support = /\.(test|testing|fixtures)\.tsx?$/;
const sourceOf = (sources: Record<string, string>) => Object.entries(sources).filter(([path]) => !support.test(path)).map(([, source]) => source).join("\n");

function leaves(node: unknown, path: string[] = []): string[] {
  return node !== null && typeof node === "object"
    ? Object.entries(node).flatMap(([key, child]) => leaves(child, [...path, key]))
    : [path.join(".")];
}

function unused(copy: unknown, text: string): string[] {
  return leaves(copy).filter(key => {
    const parts = key.split(".");
    // A key is used whole, or by a prefix that a state completes: `harness.status.${key}`.
    const prefixes = parts.map((_, index) => `${parts.slice(0, index + 1).join(".")}${index + 1 < parts.length ? "." : ""}`);
    return !text.includes(`"${key}"`) && !text.includes(`'${key}'`) && !prefixes.slice(0, -1).some(prefix => text.includes(`\`${prefix}\${`) || text.includes(`"${prefix}" +`));
  });
}

describe("the copy of the pages", () => {
  it("has no retired settings copy", () => {
    const source = sourceOf(pages.settings).replaceAll("settingsStorage:", "");
    expect(unused(zhSettings, source)).toEqual([]);
    expect(unused(zhStorage, source)).toEqual([]);
  });

  it("has no key of the Buddy page that no view uses", () => {
    expect(unused(zhBuddies, sourceOf(pages.buddies))).toEqual([]);
  });

  it("has no key of the detail of a micro task that no view uses", () => {
    expect(unused(zhTaskDetail, sourceOf(pages.taskDetail))).toEqual([]);
  });

  it("uses language autonyms and has no other Chinese text or full-width punctuation in English namespaces", () => {
    for (const settings of [zhSettings, enSettings]) expect([settings.chinese, settings.english]).toEqual(["中文", "English"]);
    const chinese = /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/;
    const words = (node: unknown): string[] => node !== null && typeof node === "object" ? Object.values(node).flatMap(words) : [String(node)];
    const english = import.meta.glob<{ default: Record<string, unknown> }>("./en/*.ts", { eager: true });
    const translated = Object.entries(english).map(([path, module]) => {
      if (path !== "./en/settings.ts") return module.default;
      const { chinese: _autonym, ...copy } = module.default;
      return copy;
    });
    expect(words(translated).filter(word => chinese.test(word))).toEqual([]);
  });
});
