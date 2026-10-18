import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { workflowOwnerText } from "./actor";
import {
  MODEL_SERVICE_CONCURRENCY_DEFAULT,
  concurrencyEntryFor,
  concurrencyLimit,
  modelServices,
  preferredVariant,
  taskHost,
  taskProject,
} from "./console-data";
import type { Preference, Buddy, Task } from "./types";

const buddy = (buddyId: string, harness: string, provider: string, effort: string,
  effortRank: number | null = null): Buddy => ({
  buddyId, harness, provider, model: "shared-model", effort, effortRank, label: "Shared model", available: true,
  enabled: true, capabilities: [], contextWindow: null, source: "fixture", description: "",
});

afterEach(async () => { await i18n.changeLanguage("zh"); });

describe("console grouping and recorded attribution", () => {
  it("keeps the recorded delegator distinct from a later Host owner, including Lead-owned helper trees", async () => {
    const task = { lineage: { sourceHostId: null, currentHostId: "host-after-takeover", rootLeadRunId: "root-lead",
      leadOwnership: { leadRunId: "direct-lead" } } } as Task;
    expect(taskHost(task)).toBe("Lead · direct-lead");
    expect(workflowOwnerText(task)).toBe("host-after-takeover");
    task.lineage!.leadOwnership = null;
    expect(taskHost(task)).toBe("Lead · root-lead");
    task.lineage!.sourceHostId = "original-host";
    expect(taskHost(task)).toBe("original-host");
    await i18n.changeLanguage("en");
    expect(taskHost({} as Task)).toBe("Delegator not recorded");
    expect(taskHost({ lineage: { rootLeadRunId: "lead-run" } } as Task)).toBe("Lead · lead-run");
  });
  it("orders each model's efforts by its native catalog rank, then puts unranked efforts last by name", () => {
    const native = [
      { effort: "max", effortRank: 0 },
      { effort: "turbo", effortRank: null },
      { effort: "low", effortRank: 2 },
      { effort: "default", effortRank: null },
      { effort: "high", effortRank: 1 },
    ];
    expect(modelServices(native.map(({ effort, effortRank }) =>
      buddy(effort, "codex", "provider", effort, effortRank)))[0]
      .buddies.map(item => item.effort)).toEqual(["max", "high", "low", "default", "turbo"]);
  });

  it("groups efforts while keeping the same model name from different harnesses and providers separate", () => {
    const groupedServices = modelServices([buddy("dsh-low", "dsh", "provider-a", "low"),
      buddy("dsh-high", "dsh", "provider-a", "high"), buddy("other-provider", "dsh", "provider-b", "high"),
      buddy("other-harness", "zcode", "provider-a", "high")]);
    expect(groupedServices).toHaveLength(3);
    expect(groupedServices.map(f => f.buddies.map(p => p.buddyId).sort()).sort()).toEqual([
      ["dsh-high", "dsh-low"], ["other-harness"], ["other-provider"],
    ].sort());
  });

  it("prefers an available variant when the model service also holds a retired one", () => {
    const retiredEnabled = { ...buddy("retired", "dsh", "provider-a", "max"), available: false, enabled: true };
    const availableDisabled = { ...buddy("available", "dsh", "provider-a", "low"), enabled: false };
    const modelService = modelServices([retiredEnabled, availableDisabled])[0];
    // Inspecting the model service never lands on a retired variant while one is listed.
    expect(preferredVariant(modelService, []).buddyId).toBe("available");
    expect(preferredVariant(modelService, [{ buddyId: "retired", mode: "prefer", note: "历史" } as Preference]).buddyId)
      .toBe("available");
    // When every variant is retired, an enabled variant still wins over a disabled one.
    const allRetired = modelServices([retiredEnabled, { ...availableDisabled, available: false }])[0];
    expect(preferredVariant(allRetired, [{ buddyId: "available", mode: "prefer", note: "历史" } as Preference]).buddyId)
      .toBe("retired");
  });

  it("selects the preferred buddy from the native effort order without changing that order", () => {
    const modelService = modelServices([
      buddy("high", "codex", "openai", "high", 1),
      buddy("low", "codex", "openai", "low", 0),
    ])[0];
    expect(modelService.buddies.map(row => row.buddyId)).toEqual(["low", "high"]);
    expect(preferredVariant(modelService, [{ buddyId: "high", mode: "prefer", note: "coding" }]).buddyId).toBe("high");
    expect(preferredVariant(modelService, []).buddyId).toBe("low");
  });

  it("uses backend source metadata and never guesses a project or Host from execution cwd or owner", () => {
    const task = { runId: "micro-task", cwd: "/runtime/worktrees/generated", owner: "worker:guess" } as Task;
    expect(taskProject(task)).toBeNull();
    expect(taskHost(task)).toBe("未记录委派方");
    task.lineage = { kind: "helper", sourceHostId: "codex-original", currentHostId: "codex-new",
      parentRunId: "parent", rootRunId: "root", rootLeadRunId: null, routingTask: null,
      project: { id: "repo-id", path: "/source/project", label: "Project" }, buddy: null };
    expect(taskProject(task)).toEqual(task.lineage.project);
    expect(taskHost(task)).toBe("codex-original");
  });
});

describe("shared model concurrency", () => {
  it("accepts only integer limits within 1–32 and defaults to 2", () => {
    expect(MODEL_SERVICE_CONCURRENCY_DEFAULT).toBe(2);
    expect(concurrencyLimit(1)).toBe(1);
    expect(concurrencyLimit(32)).toBe(32);
    for (const invalid of [0, 33, -2, 2.5, "4", null, undefined]) {
      expect(concurrencyLimit(invalid)).toBeNull();
    }
  });

  it("finds a model service entry by the exact harness/provider/model tuple, ignoring effort", () => {
    const entries = [
      { harness: "dsh", provider: "p", model: "m", limit: 2, active: 1 },
      { harness: "dsh", provider: "p", model: "other", limit: 3, active: 0 },
    ];
    const withEffort = { harness: "dsh", provider: "p", model: "m", effort: "high" };
    expect(concurrencyEntryFor(entries, withEffort)).toBe(entries[0]);
    expect(concurrencyEntryFor(entries, { harness: "zcode", provider: "p", model: "m" })).toBeNull();
    expect(concurrencyEntryFor(entries, { harness: "dsh", provider: "q", model: "m" })).toBeNull();
    expect(concurrencyEntryFor(undefined, withEffort)).toBeNull();
  });
});
