import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { setLanguageChoice } from "./index";
import { reasonText } from "./reason";
import zh from "./zh/reasons";
import i18n from "i18next";

const codes = JSON.parse(readFileSync("../../src/hey_my_buddy/protocol/reason-codes.json", "utf8")) as Record<string, string[]>;
const samples: Record<string, unknown> = {
  access: "read-only", adapter: "codex", bound: "unavailable", buddy: { harness: "codex", provider: "openai", model: "sample", effort: "high" },
  buddyId: "buddy-1", bytes: 12345, catalogReadAt: "2026-10-18T01:02:03Z", cause: { code: "harness.not-found", params: { operation: "probe" } },
  category: "OSError", message: "file write unavailable", truncated: false,
  collection: "buddies", conflictIds: ["conflict-1"], continuationId: "continuation-1", count: 1234, disposition: "completed", enabled: false,
  errorCode: "SAMPLE", errors: ["summary-citation-outside-batch"], harness: "codex", kind: "permission", leadRunId: "lead-1", legalEfforts: ["high", "max"], limit: 12000,
  missingCapabilities: ["decision"], operation: "probe", recordId: "record-1",
  processes: [{ pid: 321, startedAt: 456, executable: "python" }],
  unreadableProcesses: [{ pid: 654, startedAt: 789, executable: "python" }],
  requiredCapability: "decision", retryAt: "2026-10-18T01:02:03Z", shape: "object", total: 12,
  unfinished: [{ runId: "micro-1", state: "executing" }], utf16Units: 32768,
};

afterEach(() => setLanguageChoice("browser"));

describe("the program reason catalog", () => {
  for (const [language, router, tools, deadline, count] of [
    ["zh", "Router 超出了这次调用的时限或工具调用上限。", "只读调用超出了工具调用上限。", "Router 超出时限", "超出时限或工具调用上限"],
    ["en", "The Router exceeded this call's time limit or tool-call limit.", "The read-only call exceeded its tool-call limit.", "Router exceeded its time limit", "Time or tool-call limit exceeded"],
  ] as const) it(`names the actual execution limits in ${language}`, () => {
    setLanguageChoice(language);
    expect(reasonText({ code: "router-budget-exhausted", params: {} })).toBe(router);
    expect(reasonText({ code: "readonly-budget-exhausted", params: {} })).toBe(tools);
    expect(i18n.t("taskDetail:termination.deadline")).toBe(deadline);
    expect(i18n.t("buddies:roles.records.budgetExhausted")).toBe(count);
  });
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

  for (const language of ["zh", "en"] as const) it(`shows executable and PID without kernel start counters in ${language}`, () => {
    setLanguageChoice(language);
    const text = reasonText({ code: "checkout.process-blockers", params: {
      processes: [{ pid: 321, startedAt: 9876543210, executable: "python" }],
      unreadableProcesses: [{ pid: 654, startedAt: 9876543211, executable: "node" }],
    } });
    expect(text).toContain(language === "zh" ? "python（321）" : "python (321)");
    expect(text).toContain(language === "zh" ? "node（654）" : "node (654)");
    expect(text).not.toMatch(/987654321|startedAt|\{|\}/);
  });

  it("formats every current candidate bound in both languages", () => {
    const bounds = ["identity-changed", "disabled", "unavailable", "constraints", "capability-missing",
      "excluded", "not-pinned", "harness-unavailable", "quota-exhausted", "not-a-candidate"];
    for (const language of ["zh", "en"] as const) {
      setLanguageChoice(language);
      for (const bound of bounds) {
        const text = reasonText({ code: "router-out-of-bounds", params: { bound } });
        expect(text, `${language}: ${bound}`).not.toContain(bound);
        expect(text).not.toMatch(/shell:|\{\{/);
      }
    }
  });

  it("reads optional publication and routing facts without unfinished interpolation", () => {
    for (const code of ["decision-publication-failed", "router-review-unsupported", "router-no-tool-unsupported", "bridge-refused"]) {
      expect(reasonText({ code, params: {} })).not.toMatch(/\{\{/);
    }
  });
});
