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
  requiredCapability: "decision", runId: "micro-1", retryAt: "2026-10-18T01:02:03Z", seconds: 60, shape: "object", total: 12,
  unfinished: [{ runId: "micro-1", state: "executing" }], utf16Units: 32768, version: "0.2.2-rc.1",
};

afterEach(() => setLanguageChoice("browser"));

describe("the program reason catalog", () => {
  it("names the owning micro task for a helper cascade in both languages", () => {
    const reason = { code: "micro-task.owned-concluded", params: { runId: "root-1" } };
    setLanguageChoice("zh");
    expect(reasonText(reason)).toBe("所属微任务 root-1 已作结论，此微任务随之结束。");
    setLanguageChoice("en");
    expect(reasonText(reason)).toBe("Owning micro task root-1 was concluded, ending this micro task.");
  });
  for (const [language, router, count] of [
    ["zh", "Router 的模型在 60 秒内没有开始作答。", "模型未开始作答"],
    ["en", "The Router's model did not start responding within 60 seconds.", "Model did not start responding"],
  ] as const) it(`names the model response wait in ${language}`, () => {
    setLanguageChoice(language);
    expect(reasonText({ code: "router-not-responding", params: { seconds: 60 } })).toBe(router);
    expect(i18n.t("buddies:roles.records.notResponding")).toBe(count);
  });
  it("explains the historical Router budget reason in both languages", () => {
    setLanguageChoice("zh");
    expect(reasonText({ code: "router-budget-exhausted", params: {} })).toBe("此 Router 调用（0.65.0 之前）达到时限或工具调用上限。");
    setLanguageChoice("en");
    expect(reasonText({ code: "router-budget-exhausted", params: {} })).toBe("Before 0.65.0, this Router call reached its time or tool-call limit.");
  });
  it("explains maintenance validation issues without leaking identifiers into the visible reason", () => {
    const reason = { code: "maintenance-answer-invalid", params: { errors: [
      "answer.summaries[kind].citations[0]: enum", "newNodes[node]: parent cycle", "future-validation-code",
    ] } };
    setLanguageChoice("zh");
    const chinese = reasonText(reason);
    expect(chinese).toContain("摘要引用了本批次之外的微任务");
    expect(chinese).toContain("领域节点的上级关系形成环");
    expect(chinese).toContain("维护回答未通过校验");
    expect(chinese).not.toMatch(/enum|newNodes|future-validation-code/);
    setLanguageChoice("en");
    const english = reasonText(reason);
    expect(english).toContain("A summary cites a micro task outside this batch");
    expect(english).toContain("Domain node parents form a cycle");
    expect(english).toContain("The maintenance answer failed validation");
    expect(english).not.toMatch(/enum|newNodes|future-validation-code/);
    expect(reasonText({ code: "profile-answer-invalid", params: { errors: ["profile.model: required"] } })).toContain("profile.model: required");
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
