import { describe, expect, it } from "vitest";
import { makeDraft, publication, setAnnotation, setConcurrencyLimit, setPreference } from "./draft";
import {
  attentionIssues,
  blockingIssues,
  decisionAttention,
  decisionCandidates,
  executionCandidates,
  hasDecisionCapability,
  hasExecutionCapability,
  isDecisionCandidate,
} from "./policy";
import type { Profile, Snapshot, WriterGrant } from "./types";

const worker: Profile = {
  profileId: "dsh:deepseek-official:deepseek-flash:off",
  label: "flash · off",
  adapter: "dsh",
  provider: "deepseek-official",
  model: "deepseek-flash",
  effort: "off",
  available: true,
  enabled: true,
  capabilities: ["execution:dsh", "effort:off", "decision"],
  contextWindow: null,
  description: "",
  source: "catalog",
};
const retired: Profile = {
  ...worker,
  profileId: "dsh:deepseek-official:retired-model:max",
  model: "retired-model",
  effort: "max",
  available: false,
  enabled: false,
  unavailableReason: "provider paused",
};
const disabled: Profile = { ...worker, profileId: "dsh:deepseek-official:disabled:low", enabled: false };
const coder: Profile = {
  ...worker,
  profileId: "dsh:deepseek-official:no-decision:high",
  capabilities: ["execution:dsh"],
};
const otherHarness: Profile = { ...worker, profileId: "zcode:bigmodel-api:glm:high", adapter: "zcode" };

function snapshot(profiles: Profile[] = [worker], extra: Partial<Snapshot> = {}): Snapshot {
  return {
    csrfToken: "csrf",
    tableRevision: 3,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: worker.profileId },
    profiles,
    preferences: [],
    cards: [],
    annotations: [],
    evidence: [],
    decisions: [],
    sampleCounts: {},
    modelConcurrency: [],
    tasks: { runs: [], total: 0 },
    capabilities: { evaluationWriteGate: true },
    ...extra,
  } as Snapshot;
}
const grant = (): WriterGrant => ({
  writerId: "w", generation: 1, writerToken: "t", phase: "writing", expiresAt: "", tableRevision: 3,
});

describe("decision capability", () => {
  it("accepts a declared decision capability and never infers it from coding ability", () => {
    expect(hasDecisionCapability(worker)).toBe(true);
    expect(hasDecisionCapability({ ...worker, capabilities: ["decision"] })).toBe(true);
    expect(hasDecisionCapability(coder)).toBe(false);
    expect(hasDecisionCapability(undefined)).toBe(false);
    expect(isDecisionCandidate(coder)).toBe(false);
    expect(isDecisionCandidate(disabled)).toBe(false);
    expect(isDecisionCandidate({ ...worker, available: false })).toBe(false);
  });

  it("accepts only the recorded `decision` token, never an invented alias", () => {
    // Host populates the capability token exactly as `decision`.
    expect(hasDecisionCapability({ ...worker, capabilities: ["decision:dsh"] })).toBe(false);
    expect(hasDecisionCapability({ ...worker, capabilities: ["decision/dsh"] })).toBe(false);
    expect(hasDecisionCapability({ ...worker, capabilities: ["DECISION"] })).toBe(true);
    expect(hasDecisionCapability({ ...worker, capabilities: [" decision "] })).toBe(true);
    expect(hasDecisionCapability({ ...worker, capabilities: ["decision:"] })).toBe(false);
    expect(decisionCandidates([{ ...worker, capabilities: ["decision:dsh"] }])).toEqual([]);
  });

  it("offers enabled, available, decision-capable candidates in a stable order", () => {
    expect(decisionCandidates([coder, otherHarness, disabled, worker, retired]).map(p => p.profileId))
      .toEqual([worker.profileId, otherHarness.profileId]);
  });
});

describe("execution capability admission", () => {
  const codex: Profile = {
    ...worker,
    profileId: "codex:openai:gpt-5:high",
    adapter: "codex",
    provider: "openai",
    model: "gpt-5",
    effort: "high",
    capabilities: ["execution:codex", "input:text"],
  };

  it("accepts exactly the adapter's published `execution:<adapter>` token", () => {
    // `catalog.proposed_profiles` publishes this token for every executable
    // adapter, so Codex is admitted without a console-side harness list.
    expect(hasExecutionCapability(worker)).toBe(true);
    expect(hasExecutionCapability(codex)).toBe(true);
    expect(hasExecutionCapability(coder)).toBe(true);
    // A decision-only row, a mismatched adapter and invented aliases never pass.
    expect(hasExecutionCapability({ ...worker, capabilities: ["decision"] })).toBe(false);
    expect(hasExecutionCapability(otherHarness)).toBe(false);
    expect(hasExecutionCapability({ ...worker, capabilities: [] })).toBe(false);
    expect(hasExecutionCapability({ ...worker, capabilities: ["EXECUTION:DSH"] })).toBe(true);
    expect(hasExecutionCapability({ ...worker, capabilities: [" execution:dsh "] })).toBe(true);
    expect(hasExecutionCapability({ ...worker, capabilities: ["execution:"] })).toBe(false);
    expect(hasExecutionCapability({ ...worker, capabilities: ["execution:dsh:extra"] })).toBe(false);
    expect(hasExecutionCapability(undefined)).toBe(false);
  });

  it("offers only enabled, available, execution-capable candidates in a stable order", () => {
    expect(executionCandidates([worker, codex, otherHarness, disabled, retired]).map(p => p.profileId))
      .toEqual([codex.profileId, worker.profileId]);
    expect(executionCandidates([{ ...codex, capabilities: ["decision"] }])).toEqual([]);
    expect(executionCandidates([{ ...codex, available: false }])).toEqual([]);
  });
});

describe("blocking new changes", () => {
  it("blocks enabling a configuration that is not currently available", () => {
    const baseline = makeDraft(snapshot([worker, retired]));
    const draft = {
      ...baseline,
      profiles: baseline.profiles.map(p => p.profileId === retired.profileId ? { ...p, enabled: true } : p),
    };
    const issues = blockingIssues(baseline, draft);
    expect(issues).toHaveLength(1);
    expect(issues[0].profileId).toBe(retired.profileId);
    expect(issues[0].message).toContain("不能新启用");
  });

  it("allows disabling an unavailable configuration and editing its opinion", () => {
    const enabledRetired = { ...retired, enabled: true };
    const baseline = makeDraft(snapshot([worker, enabledRetired], {
      preferences: [{ profileId: retired.profileId, mode: "pin", reason: "以前固定" }],
    }));
    let draft = {
      ...baseline,
      profiles: baseline.profiles.map(p => p.profileId === retired.profileId ? { ...p, enabled: false } : p),
    };
    draft = setAnnotation(draft, retired.profileId, "保留经验");
    expect(blockingIssues(baseline, draft)).toEqual([]);
    const patch = publication(baseline, draft, grant(), "c");
    expect(patch.profileSettings).toEqual([{ profileId: retired.profileId, enabled: false }]);
    expect(patch.annotationChanges).toEqual([{ profileId: retired.profileId, text: "保留经验" }]);
  });

  it("blocks a new pin for an unavailable configuration but allows prefer or exclude", () => {
    const baseline = makeDraft(snapshot([worker, retired]));
    const pinned = setPreference(baseline, retired.profileId, "pin");
    expect(blockingIssues(baseline, pinned)[0].message).toContain("不能设为固定选择");
    expect(blockingIssues(baseline, setPreference(baseline, retired.profileId, "prefer"))).toEqual([]);
    expect(blockingIssues(baseline, setPreference(baseline, retired.profileId, "exclude"))).toEqual([]);
  });

  it("blocks a new pin for an available but disabled configuration, matching the board", () => {
    const baseline = makeDraft(snapshot([worker, disabled]));
    const pinned = setPreference(baseline, disabled.profileId, "pin");
    expect(blockingIssues(baseline, pinned)[0].message).toContain("停用状态");
    // An existing pin on a disabled configuration is attention, never a blocker.
    const stale = { ...baseline, preferences: [{ profileId: disabled.profileId, mode: "pin" as const, reason: "以前固定" }] };
    expect(blockingIssues(stale, stale)).toEqual([]);
    expect(attentionIssues(stale)[0].message).toContain("停用状态");
  });

  it("allows a reason-only edit of an existing unavailable pin", () => {
    const baseline = makeDraft(snapshot([worker, retired], {
      preferences: [{ profileId: retired.profileId, mode: "pin", reason: "旧依据" }],
    }));
    // The mode does not transition into pin, so current legality is not required.
    const reasonOnly = setPreference(baseline, retired.profileId, "pin", "新依据");
    expect(blockingIssues(baseline, reasonOnly)).toEqual([]);
    const patch = publication(baseline, reasonOnly, grant(), "c");
    expect(patch.preferenceChanges).toEqual([
      { profileId: retired.profileId, mode: "pin", reason: "新依据" },
    ]);
    // The same edit from prefer into pin is a transition and stays blocked.
    const soft = setPreference(baseline, retired.profileId, "prefer", "旧依据");
    expect(blockingIssues(soft, setPreference(soft, retired.profileId, "pin", "旧依据"))[0].message)
      .toContain("不能设为固定选择");
  });

  it("blocks a new decision selector unless it is available, enabled and decision-capable", () => {
    const baseline = makeDraft(snapshot([worker, retired, disabled, coder]));
    const select = (profileId: string) => ({
      ...baseline,
      configuration: { ...baseline.configuration, decisionProfileId: profileId },
    });
    expect(blockingIssues(baseline, select(retired.profileId))[0].message).toContain("不能新设为决策模型");
    expect(blockingIssues(baseline, select(disabled.profileId))[0].message).toContain("停用状态");
    expect(blockingIssues(baseline, select(coder.profileId))[0].message).toContain("没有声明决策能力");
    expect(blockingIssues(baseline, select(worker.profileId))).toEqual([]);
    expect(blockingIssues(baseline, {
      ...baseline,
      configuration: { ...baseline.configuration, decisionProfileId: null },
    })).toEqual([]);
  });
});

describe("stale settings needing attention", () => {
  it("reports a stale decision setting without blocking an unrelated opinion", () => {
    const baseline = makeDraft(snapshot([worker, retired], {
      configuration: { revision: 2, decisionProfileId: retired.profileId },
    }));
    expect(decisionAttention(baseline)?.message).toContain("需要处理");
    expect(blockingIssues(baseline, baseline)).toEqual([]);
    const withOpinion = setAnnotation(baseline, worker.profileId, "只改意见");
    expect(blockingIssues(baseline, withOpinion)).toEqual([]);
    const patch = publication(baseline, withOpinion, grant(), "c");
    expect(patch).not.toHaveProperty("configuration");
    expect(patch.annotationChanges).toEqual([{ profileId: worker.profileId, text: "只改意见" }]);
  });

  it("reports a stale pin while leaving other patches untouched", () => {
    const baseline = makeDraft(snapshot([worker, retired], {
      preferences: [{ profileId: retired.profileId, mode: "pin", reason: "以前固定" }],
    }));
    const issues = attentionIssues(baseline);
    expect(issues).toHaveLength(1);
    expect(issues[0].profileId).toBe(retired.profileId);
    expect(issues[0].message).toContain("固定选择");
    expect(blockingIssues(baseline, baseline)).toEqual([]);
  });

  it("is silent when every recorded setting is still legal", () => {
    const baseline = makeDraft(snapshot([worker]));
    expect(attentionIssues(baseline)).toEqual([]);
    expect(decisionAttention(baseline)).toBeNull();
  });

  it("treats a missing decision profile as needing attention, not as an error", () => {
    const baseline = makeDraft(snapshot([worker], {
      configuration: { revision: 2, decisionProfileId: "gone:model:off" },
    }));
    expect(decisionAttention(baseline)?.message).toContain("已不在目录中");
    expect(blockingIssues(baseline, baseline)).toEqual([]);
  });
});

describe("model concurrency limits", () => {
  it("blocks an out-of-range limit while every legal limit stays publishable", () => {
    const baseline = makeDraft(snapshot([worker]));
    for (const limit of [0, 33, 2.5]) {
      const invalid = {
        ...baseline,
        modelConcurrency: [{ adapter: worker.adapter, provider: worker.provider, model: worker.model, limit }],
      };
      const issues = blockingIssues(baseline, invalid);
      expect(issues).toHaveLength(1);
      expect(issues[0].message).toContain("1–32");
      expect(issues[0].profileId).toBe(worker.profileId);
    }
    // A limit edit never depends on availability: the retired configuration's
    // family setting stays publishable within the range.
    const retiredBase = makeDraft(snapshot([worker, retired]));
    const raised = setConcurrencyLimit(retiredBase, retired, 32);
    expect(blockingIssues(retiredBase, raised)).toEqual([]);
    const patch = publication(retiredBase, raised, grant(), "c");
    expect(patch.modelConcurrency).toEqual([
      { adapter: retired.adapter, provider: retired.provider, model: retired.model, limit: 32 },
    ]);
  });
});
