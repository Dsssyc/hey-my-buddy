import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { setLanguageChoice } from "./index";
import { reasonText } from "./reason";
import zh from "./zh/reasons";

const codes = JSON.parse(readFileSync("../../src/hey_my_buddy/protocol/reason-codes.json", "utf8")) as Record<string, string[]>;
const samples: Record<string, unknown> = {
  access: "read-only", adapter: "codex", bound: "unavailable", buddy: { harness: "codex", provider: "openai", model: "sample", effort: "high" },
  buddyId: "buddy-1", bytes: 12345, catalogReadAt: "2026-10-18T01:02:03Z", cause: { code: "harness.not-found", params: { operation: "probe" } },
  category: "OSError", message: "file write unavailable", truncated: false,
  collection: "buddies", conflictIds: ["conflict-1"], continuationId: "continuation-1", count: 1234, disposition: "completed", enabled: false,
  errorCode: "SAMPLE", harness: "codex", kind: "permission", leadRunId: "lead-1", legalEfforts: ["high", "max"], limit: 12000,
  missingCapabilities: ["decision"], operation: "probe", paths: ["src/first.ts", "src/second.ts"], recordId: "record-1",
  requiredCapability: "decision", retryAt: "2026-10-18T01:02:03Z", shape: "object", total: 12,
  unfinished: [{ runId: "micro-1", state: "executing" }],
};

afterEach(() => setLanguageChoice("browser"));

describe("the program reason catalog", () => {
  it("has exactly the protocol's codes as its Chinese leaves", () => {
    expect(Object.values(zh).every(value => typeof value === "string")).toBe(true);
    expect(Object.keys(zh).sort()).toEqual(Object.keys(codes).sort());
  });

  for (const language of ["zh", "en"] as const) it(`formats every protocol code and its sample parameters in ${language}`, () => {
    setLanguageChoice(language);
    for (const [code, names] of Object.entries(codes)) {
      expect(names.every(name => Object.hasOwn(samples, name)), code).toBe(true);
      const text = reasonText({ code, params: Object.fromEntries(names.map(name => [name, samples[name]])) });
      expect(text, code).not.toBe(code);
      expect(text, code).not.toMatch(/\{\{|shell:|reasons:|\[object Object\]/);
      expect(text.trim(), code).not.toBe("");
    }
  });

  it("reads optional publication and routing facts without unfinished interpolation", () => {
    for (const code of ["decision-publication-failed", "router-review-unsupported", "router-no-tool-unsupported", "bridge-refused"]) {
      expect(reasonText({ code, params: {} })).not.toMatch(/\{\{/);
    }
  });
});
