import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { workflowOwnerText } from "./actor";
import {
  MODEL_SERVICE_CONCURRENCY_DEFAULT,
  concurrencyEntryFor,
  concurrencyLimit,
  hasModelBuddy,
  modelServices,
  preferredVariant,
  taskHost,
  taskExecutor,
  taskProject,
} from "./console-data";
import type { Preference, Buddy, Task } from "./types";

const buddy = (buddyId: string, harness: string, provider: string, effort: string): Buddy => ({
  buddyId, harness, provider, model: "shared-model", effort, label: "Shared model", available: true,
  enabled: true, capabilities: [], contextWindow: null, source: "fixture", description: "",
});

const taskFixture = (overrides: Partial<Task> = {}): Task => ({
  runId: "run-1", task: "Run checks", status: "queued", owner: null, cwd: "~/project", revision: 1,
  createdAt: "2026-10-15T00:00:00Z", acceptedAt: null, acceptanceVerdict: null,
  reviewRejectedAt: null, reviewRejectionNote: null, ...overrides,
});

afterEach(async () => { await i18n.changeLanguage("zh"); });

describe("console grouping and recorded attribution", () => {
  it("has no model buddy for the command and external adapters and retains pending model assignment", () => {
    expect(taskExecutor(taskFixture({ spec: { adapter: "command" },
      lineage: { kind: "execution", sourceHostId: null, currentHostId: null, parentRunId: null, rootRunId: null,
        rootLeadRunId: null, project: { id: "project", label: "project", path: "~/project" },
        buddy: { harness: "codex", provider: "openai", model: "gpt-6", effort: "high" } } }))).toBeNull();
    expect(taskExecutor(taskFixture({ spec: { adapter: "external" } }))).toBeNull();
    expect(hasModelBuddy(taskFixture(), { adapter: "command" })).toBe(false);
    expect(hasModelBuddy(taskFixture(), null)).toBe(true);
    expect(taskExecutor(taskFixture({ spec: { adapter: "codex" } }))).toBe("执行 buddy 待确定");
    expect(taskExecutor({ lineage: { buddy: { harness: "codex", model: "gpt-6", effort: "high" } } } as Task))
      .toBe("codex · gpt-6 / high");
  });

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
  it("orders extended thinking levels by intensity in model service labels and variant tabs", () => {
    for (const harness of ["claude", "codex"]) {
      const levels = ["ultra", "max", "high", "medium", "xhigh", "low"];
      const buddies = levels.map(effort => buddy(`${harness}-${effort}`, harness, "provider", effort));
      expect(modelServices(buddies)[0].buddies.map(item => item.effort))
        .toEqual(["low", "medium", "high", "xhigh", "max", "ultra"]);
      expect(buddies.map(item => item.effort)).toEqual(levels);
    }
  });

  it("keeps default and non-thinking modes ahead of ranked efforts and retains unknown native levels", () => {
    const levels = ["future", "ultra", "minimal", "off", "default", "none"];
    expect(modelServices(levels.map(effort => buddy(effort, "codex", "provider", effort)))[0]
      .buddies.map(item => item.effort)).toEqual(["default", "none", "off", "minimal", "ultra", "future"]);
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
    expect(preferredVariant(modelService, [{ buddyId: "retired", mode: "prefer", reason: "历史" } as Preference]).buddyId)
      .toBe("available");
    // When every variant is retired, an enabled variant still wins over a disabled one.
    const allRetired = modelServices([retiredEnabled, { ...availableDisabled, available: false }])[0];
    expect(preferredVariant(allRetired, [{ buddyId: "available", mode: "prefer", reason: "历史" } as Preference]).buddyId)
      .toBe("retired");
  });

  it("selects the preferred buddy from effort order without changing that order", () => {
    const modelService = modelServices([
      buddy("high", "codex", "openai", "high"),
      buddy("low", "codex", "openai", "low"),
    ])[0];
    expect(modelService.buddies.map(row => row.buddyId)).toEqual(["low", "high"]);
    expect(preferredVariant(modelService, [{ buddyId: "high", mode: "prefer", reason: "coding" }]).buddyId).toBe("high");
    expect(preferredVariant(modelService, []).buddyId).toBe("low");
  });

  it("uses backend source metadata and never guesses a project or Host from execution cwd or owner", () => {
    const task = { runId: "micro-task", cwd: "/runtime/worktrees/generated", owner: "worker:guess" } as Task;
    expect(taskProject(task)).toMatchObject({ id: "unknown", label: "未记录项目", path: null });
    expect(taskHost(task)).toBe("未记录委派方");
    task.lineage = { kind: "helper", sourceHostId: "codex-original", currentHostId: "codex-new",
      parentRunId: "parent", rootRunId: "root", rootLeadRunId: null, project: { id: "repo-id", path: "/source/project", label: "Project" }, buddy: null };
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
