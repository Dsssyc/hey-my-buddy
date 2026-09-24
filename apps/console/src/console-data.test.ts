import { describe, expect, it } from "vitest";
import { modelFamilies, recordedSampleCount, taskHost, taskProject } from "./console-data";
import type { Card, Profile, Snapshot, Task } from "./types";

const profile = (profileId: string, adapter: string, provider: string, effort: string): Profile => ({
  profileId, adapter, provider, model: "shared-model", effort, label: "Shared model", available: true,
  enabled: true, capabilities: [], contextWindow: null, source: "fixture", description: "",
});

describe("console grouping and recorded attribution", () => {
  it("groups efforts while keeping the same model name from different harnesses and providers separate", () => {
    const families = modelFamilies([profile("dsh-low", "dsh", "provider-a", "low"),
      profile("dsh-high", "dsh", "provider-a", "high"), profile("other-provider", "dsh", "provider-b", "high"),
      profile("other-harness", "zcode", "provider-a", "high")]);
    expect(families).toHaveLength(3);
    expect(families.map(f => f.profiles.map(p => p.profileId).sort()).sort()).toEqual([
      ["dsh-high", "dsh-low"], ["other-harness"], ["other-provider"],
    ].sort());
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

describe("recorded sample counts", () => {
  const snapshot = (sampleCounts: Record<string, number>) => ({ sampleCounts } as unknown as Snapshot);

  it("reads the count from the snapshot map even without published card prose", () => {
    expect(recordedSampleCount(snapshot({ p: 7 }), "p")).toBe(7);
    expect(recordedSampleCount(snapshot({ p: 0 }), "p")).toBe(0);
  });

  it("reports zero for a profile the map does not name and ignores a legacy card field", () => {
    expect(recordedSampleCount(snapshot({ other: 9 }), "p")).toBe(0);
    expect(recordedSampleCount(snapshot({}), "p")).toBe(0);
    const legacyCard = { profileId: "p", sampleCount: 3 } as unknown as Snapshot["cards"][number];
    expect(recordedSampleCount({ ...snapshot({}), cards: [legacyCard] } as Snapshot, "p")).toBe(0);
    // A board that has not shipped the map yet degrades to zero, never crashes.
    expect(recordedSampleCount({} as Snapshot, "p")).toBe(0);
  });
});
