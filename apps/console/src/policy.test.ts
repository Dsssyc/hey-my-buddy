import { describe, expect, it } from "vitest";
import {
  makeDraft,
  publication,
  setConcurrencyLimit,
  setModelServiceNote as setNote,
  setPreference,
} from "./draft";
import {
  attentionIssues,
  blockingIssues,
  decisionAttention,
  decisionCandidates,
  executionCandidates,
  hasDecisionCapability,
  hasExecutionCapability,
  isDecisionCandidate,
  routerRefusal,
  fastRouterCandidates,
  isFastRouterCandidate,
} from "./policy";
import type { Profile, Snapshot } from "./types";

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
    csrfToken: "csrf", eventCursor: 0,
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerProfileIds: [worker.profileId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles,
    preferences: [],

    modelServiceNotes: [],
    evidence: [],
    decisions: [],

    modelServiceConcurrency: [],
    tasks: { runs: [], total: 0 },
    capabilities: {},
    ...extra,
  } as Snapshot;
}
describe("decision capability", () => {
  it("keeps fast and review Router eligibility separate", () => {
    const fast = { ...coder, capabilities: ["execution:dsh", "routing:fast"] };
    expect(isFastRouterCandidate(fast)).toBe(true);
    expect(isDecisionCandidate(fast)).toBe(false);
    expect(isFastRouterCandidate(worker)).toBe(false);
    expect(isDecisionCandidate(worker)).toBe(true);
    expect(fastRouterCandidates([fast, { ...fast, enabled: false }, { ...fast, available: false }])).toHaveLength(1);
    expect(routerRefusal(fast, "review")).toContain("所属 Harness 尚不具备本地只读路由资格");
    expect(routerRefusal(worker, "fast")).toContain("所属 Harness 尚不支持无工具路由调用");
    const baseline = makeDraft(snapshot([worker, fast]));
    expect(blockingIssues(baseline, { ...baseline, configuration: { ...baseline.configuration!, routerProfileIds: [fast.profileId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "fast" } })).toEqual([]);
    expect(blockingIssues(baseline, { ...baseline, configuration: { ...baseline.configuration!, defaultRoutingMode: "fast" } })).toEqual([]);
    expect(blockingIssues(baseline, { ...baseline, configuration: { ...baseline.configuration!, routerProfileIds: [fast.profileId], routerRetryIntervalSeconds: 600} })).toEqual([]);
  });
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

  it("explains why an effort's tag menu cannot make it the Router", () => {
    expect(routerRefusal(worker)).toBeNull();
    expect(routerRefusal(coder)).toContain("所属 Harness 尚不具备本地只读路由资格");
    // The capability is reported first: it is the one reason the user cannot fix.
    expect(routerRefusal({ ...coder, enabled: false })).toContain("所属 Harness 尚不具备本地只读路由资格");
    expect(routerRefusal({ ...worker, available: false })).toContain("不可用");
    expect(routerRefusal(disabled)).toContain("未启用");
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
    expect(issues[0].message).toContain("无法启用");
  });

  it("allows disabling an unavailable configuration and editing its model service note", () => {
    const enabledRetired = { ...retired, enabled: true };
    const baseline = makeDraft(snapshot([worker, enabledRetired], {
      preferences: [{ profileId: retired.profileId, mode: "pin", reason: "以前固定" }],
    }));
    let draft = {
      ...baseline,
      profiles: baseline.profiles.map(p => p.profileId === retired.profileId ? { ...p, enabled: false } : p),
    };
    draft = setNote(draft, retired, "保留经验");
    expect(blockingIssues(baseline, draft)).toEqual([]);
    const patch = publication(baseline, draft, "c");
    expect(patch.profileSettings).toEqual([{ profileId: retired.profileId, enabled: false }]);
    expect(patch.modelServiceNoteChanges).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "retired-model", text: "保留经验" },
    ]);
  });

  it("blocks a new pin for an unavailable configuration but allows prefer or exclude", () => {
    const baseline = makeDraft(snapshot([worker, retired]));
    const pinned = setPreference(baseline, retired.profileId, "pin");
    expect(blockingIssues(baseline, pinned)[0].message).toContain("无法固定");
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
    const patch = publication(baseline, reasonOnly, "c");
    expect(patch.preferenceChanges).toEqual([
      { profileId: retired.profileId, mode: "pin", reason: "新依据" },
    ]);
    // The same edit from prefer into pin is a transition and stays blocked.
    const soft = setPreference(baseline, retired.profileId, "prefer", "旧依据");
    expect(blockingIssues(soft, setPreference(soft, retired.profileId, "pin", "旧依据"))[0].message)
      .toContain("无法固定");
  });

  it("allows published complete Router identities while runtime eligibility is temporarily missing", () => {
    const baseline = makeDraft(snapshot([worker, retired, disabled, coder]));
    const select = (profileId: string) => ({
      ...baseline,
      configuration: { ...baseline.configuration!, routerProfileIds: [profileId], routerRetryIntervalSeconds: 600},
    });
    expect(blockingIssues(baseline, select(retired.profileId))).toEqual([]);
    expect(blockingIssues(baseline, select(disabled.profileId))).toEqual([]);
    expect(blockingIssues(baseline, select(coder.profileId))).toEqual([]);
    expect(blockingIssues(baseline, select(worker.profileId))).toEqual([]);
    expect(blockingIssues(baseline, {
      ...baseline,
      configuration: { ...baseline.configuration!, routerProfileIds: [] },
    })).toEqual([]);
    expect(blockingIssues(baseline, select("unknown"))[0].message).toContain("已不在当前目录中");
  });

  it("saves review mode when the first item is fast-only and the next item supports review", () => {
    const first = { ...coder, capabilities: ["routing:fast"] };
    const baseline = makeDraft(snapshot([first, worker], { configuration: { routerProfileIds: [first.profileId, worker.profileId], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "fast", routingBudget: "standard",
    } }));
    const changed = { ...baseline, configuration: { ...baseline.configuration!, defaultRoutingMode: "review" as const } };
    expect(blockingIssues(baseline, changed)).toEqual([]);
    expect(publication(baseline, changed, "mode").configuration).toEqual({ defaultRoutingMode: "review" });
  });
});

describe("stale settings needing attention", () => {
  it("reports a stale Router with its resolving action without blocking an unrelated note", () => {
    const baseline = makeDraft(snapshot([worker, retired], {
      configuration: { routerProfileIds: [retired.profileId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    }));
    const attention = decisionAttention(baseline)!;
    expect(attention.router).toBe(true);
    expect(attention.message).toContain("设为 Router");
    expect(blockingIssues(baseline, baseline)).toEqual([]);
    const withNote = setNote(baseline, worker, "只改备注");
    expect(blockingIssues(baseline, withNote)).toEqual([]);
    const patch = publication(baseline, withNote, "c");
    expect(patch).not.toHaveProperty("configuration");
    expect(patch.modelServiceNoteChanges).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "只改备注" },
    ]);
  });

  it("reports a stale buddy pin while leaving other patches untouched", () => {
    const baseline = makeDraft(snapshot([worker, retired], {
      preferences: [{ profileId: retired.profileId, mode: "pin", reason: "以前固定" }],
    }));
    const issues = attentionIssues(baseline);
    expect(issues).toHaveLength(1);
    expect(issues[0].profileId).toBe(retired.profileId);
    expect(issues[0].message).toContain("固定选择");
    expect(issues[0].message).toContain("不设");
    expect(blockingIssues(baseline, baseline)).toEqual([]);
  });

  it("is silent when every recorded setting is still legal", () => {
    const baseline = makeDraft(snapshot([worker]));
    expect(attentionIssues(baseline)).toEqual([]);
    expect(decisionAttention(baseline)).toBeNull();
  });

  it("treats a missing decision profile as needing attention, not as an error", () => {
    const baseline = makeDraft(snapshot([worker], {
      configuration: { routerProfileIds: ["gone:model:off"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    }));
    expect(decisionAttention(baseline)?.message).toContain("已不在目录中");
    expect(blockingIssues(baseline, baseline)).toEqual([]);
  });
});

describe("the Router retry interval", () => {
  it("blocks an out-of-range interval while every legal value stays publishable", () => {
    const baseline = makeDraft(snapshot([worker]));
    for (const seconds of [0, -5, 2.5, 2147483648, NaN]) {
      const invalid = {
        ...baseline,
        configuration: { ...baseline.configuration!, routerRetryIntervalSeconds: seconds },
      };
      const issues = blockingIssues(baseline, invalid);
      expect(issues).toHaveLength(1);
      expect(issues[0].message).toContain("1–2147483647");
    }
    const changed = {
      ...baseline,
      configuration: { ...baseline.configuration!, routerRetryIntervalSeconds: 300 },
    };
    expect(blockingIssues(baseline, changed)).toEqual([]);
    expect(publication(baseline, changed, "i").configuration).toEqual({ routerRetryIntervalSeconds: 300 });
  });
});

describe("model concurrency limits", () => {
  it("blocks an out-of-range limit while every legal limit stays publishable", () => {
    const baseline = makeDraft(snapshot([worker]));
    for (const limit of [0, 33, 2.5]) {
      const invalid = {
        ...baseline,
        modelServiceConcurrency: [{ adapter: worker.adapter, provider: worker.provider, model: worker.model, limit }],
      };
      const issues = blockingIssues(baseline, invalid);
      expect(issues).toHaveLength(1);
      expect(issues[0].message).toContain("1–32");
      expect(issues[0].profileId).toBe(worker.profileId);
    }
    // A limit edit never depends on availability: the retired configuration's
    // model service setting stays publishable within the range.
    const retiredBase = makeDraft(snapshot([worker, retired]));
    const raised = setConcurrencyLimit(retiredBase, retired, 32);
    expect(blockingIssues(retiredBase, raised)).toEqual([]);
    const patch = publication(retiredBase, raised, "c");
    expect(patch.modelServiceConcurrency).toEqual([
      { adapter: retired.adapter, provider: retired.provider, model: retired.model, limit: 32 },
    ]);
  });
});
