import { describe, expect, it } from "vitest";
import {
  MODEL_CONCURRENCY_DEFAULT,
  concurrencyEntryFor,
  concurrencyLimit,
  modelFamilies,
  preferredVariant,
  taskHost,
  taskProject,
} from "./console-data";
import type { Preference, Profile, Task } from "./types";

const profile = (profileId: string, adapter: string, provider: string, effort: string): Profile => ({
  profileId, adapter, provider, model: "shared-model", effort, label: "Shared model", available: true,
  enabled: true, capabilities: [], contextWindow: null, source: "fixture", description: "",
});

describe("console grouping and recorded attribution", () => {
  it("orders extended thinking levels by intensity in family labels and variant tabs", () => {
    for (const adapter of ["claude", "codex"]) {
      const levels = ["ultra", "max", "high", "medium", "xhigh", "low"];
      const profiles = levels.map(effort => profile(`${adapter}-${effort}`, adapter, "provider", effort));
      expect(modelFamilies(profiles)[0].profiles.map(item => item.effort))
        .toEqual(["low", "medium", "high", "xhigh", "max", "ultra"]);
      expect(profiles.map(item => item.effort)).toEqual(levels);
    }
  });

  it("keeps default and non-thinking modes ahead of ranked efforts and retains unknown native levels", () => {
    const levels = ["future", "ultra", "minimal", "off", "default", "none"];
    expect(modelFamilies(levels.map(effort => profile(effort, "codex", "provider", effort)))[0]
      .profiles.map(item => item.effort)).toEqual(["default", "none", "off", "minimal", "ultra", "future"]);
  });

  it("groups efforts while keeping the same model name from different harnesses and providers separate", () => {
    const families = modelFamilies([profile("dsh-low", "dsh", "provider-a", "low"),
      profile("dsh-high", "dsh", "provider-a", "high"), profile("other-provider", "dsh", "provider-b", "high"),
      profile("other-harness", "zcode", "provider-a", "high")]);
    expect(families).toHaveLength(3);
    expect(families.map(f => f.profiles.map(p => p.profileId).sort()).sort()).toEqual([
      ["dsh-high", "dsh-low"], ["other-harness"], ["other-provider"],
    ].sort());
  });

  it("prefers an available variant when the family also holds a retired one", () => {
    const retiredEnabled = { ...profile("retired", "dsh", "provider-a", "max"), available: false, enabled: true };
    const availableDisabled = { ...profile("available", "dsh", "provider-a", "low"), enabled: false };
    const family = modelFamilies([retiredEnabled, availableDisabled])[0];
    // Inspecting the family never lands on a retired variant while one is listed.
    expect(preferredVariant(family, []).profileId).toBe("available");
    expect(preferredVariant(family, [{ profileId: "retired", mode: "prefer", reason: "历史" } as Preference]).profileId)
      .toBe("available");
    // When every variant is retired, an enabled variant still wins over a disabled one.
    const allRetired = modelFamilies([retiredEnabled, { ...availableDisabled, available: false }])[0];
    expect(preferredVariant(allRetired, [{ profileId: "available", mode: "prefer", reason: "历史" } as Preference]).profileId)
      .toBe("retired");
  });

  it("selects the preferred buddy from effort order without changing that order", () => {
    const family = modelFamilies([
      profile("high", "codex", "openai", "high"),
      profile("low", "codex", "openai", "low"),
    ])[0];
    expect(family.profiles.map(row => row.profileId)).toEqual(["low", "high"]);
    expect(preferredVariant(family, [{ profileId: "high", mode: "prefer", reason: "coding" }]).profileId).toBe("high");
    expect(preferredVariant(family, []).profileId).toBe("low");
  });

  it("uses backend source metadata and never guesses a project or Host from execution cwd or owner", () => {
    const task = { runId: "goal", cwd: "/runtime/worktrees/generated", owner: "worker:guess" } as Task;
    expect(taskProject(task)).toMatchObject({ id: "unknown", label: "未记录项目", path: null });
    expect(taskHost(task)).toBe("未记录委派方");
    task.delegation = { kind: "helper", sourceHostId: "codex-original", currentHostId: "codex-new",
      parentRunId: "parent", rootRunId: "root", project: { id: "repo-id", path: "/source/project", label: "Project" }, configuration: null };
    expect(taskProject(task)).toEqual(task.delegation.project);
    expect(taskHost(task)).toBe("codex-original");
  });
});

describe("shared model concurrency", () => {
  it("accepts only integer limits within 1–32 and defaults to 2", () => {
    expect(MODEL_CONCURRENCY_DEFAULT).toBe(2);
    expect(concurrencyLimit(1)).toBe(1);
    expect(concurrencyLimit(32)).toBe(32);
    for (const invalid of [0, 33, -2, 2.5, "4", null, undefined]) {
      expect(concurrencyLimit(invalid)).toBeNull();
    }
  });

  it("finds a family entry by the exact adapter/provider/model tuple, ignoring effort", () => {
    const entries = [
      { adapter: "dsh", provider: "p", model: "m", limit: 2, active: 1 },
      { adapter: "dsh", provider: "p", model: "other", limit: 3, active: 0 },
    ];
    const withEffort = { adapter: "dsh", provider: "p", model: "m", effort: "high" };
    expect(concurrencyEntryFor(entries, withEffort)).toBe(entries[0]);
    expect(concurrencyEntryFor(entries, { adapter: "zcode", provider: "p", model: "m" })).toBeNull();
    expect(concurrencyEntryFor(entries, { adapter: "dsh", provider: "q", model: "m" })).toBeNull();
    expect(concurrencyEntryFor(undefined, withEffort)).toBeNull();
  });
});
