import { describe, expect, it } from "vitest";
import {
  QUOTA_NEAR_LIMIT_PERCENT, artifactKindLabel, artifactStateView, configurationLockText,
  cumulativePatchView, formatCount, hostConclusionView, integrationHostPaths, parseQuota,
  parseTokenUsage, quotaView, tokenUsageView,
} from "./host-workflow";
import { finalArtifact } from "./integration";
import type { IntegrationRecord, Workflow } from "./workflow-types";

describe("per-execution token usage (ADR-018 §22)", () => {
  it("keeps partial native coverage visible and refuses fractional token counts", () => {
    const raw = { inputTokens: 100, cachedInputTokens: 60, outputTokens: 8, scope: "attempt",
      source: "codex/app-server-thread-token-usage", coverage: "native-root-thread", completeness: "partial" };
    const view = tokenUsageView(parseTokenUsage(raw));
    expect(view.text).toContain("部分记录");
    expect(view.title).toContain("原生主会话");
    expect(parseTokenUsage({ ...raw, inputTokens: 100.5 })).toBeNull();
    expect(parseTokenUsage({ ...raw, inputBasis: "excludes-cached" })).toBeNull();
  });
  it("keeps the cached input inside the input total instead of adding it again", () => {
    const usage = parseTokenUsage({
      inputTokens: 12000, cachedInputTokens: 9000, outputTokens: 1500,
      source: "dsh-native", scope: "attempt",
    })!;
    const view = tokenUsageView(usage);
    expect(view.recorded).toBe(true);
    expect(view.inputText).toBe("输入 12,000（含缓存 9,000）");
    expect(view.text).toBe("输入 12,000（含缓存 9,000） · 输出 1,500");
    // 12,000 is the recorded input; the cache is a subset and never becomes 21,000.
    expect(view.text).not.toContain("21,000");
    expect(view.title).toContain("单次执行");
    expect(view.title).toContain("dsh-native");
  });

  it("shows a partially recorded usage as unknown fields, never as 0", () => {
    const usage = parseTokenUsage({
      inputTokens: null, cachedInputTokens: null, outputTokens: 4200,
      source: "zcode-native", scope: "attempt",
    })!;
    const view = tokenUsageView(usage);
    expect(view.recorded).toBe(true);
    expect(view.text).toBe("输入 未知 · 输出 4,200");
    expect(view.text).not.toMatch(/输入 0\b/);
  });

  it("reports a missing usage as unknown rather than zero tokens", () => {
    expect(parseTokenUsage(null)).toBeNull();
    expect(parseTokenUsage(undefined)).toBeNull();
    const view = tokenUsageView(null);
    expect(view.recorded).toBe(false);
    expect(view.text).toBe("未记录（未知）");
    expect(view.inputText).toBe("未知");
    expect(view.outputText).toBe("未知");
  });

  it("refuses malformed, non-positive or contradictory usage records", () => {
    expect(parseTokenUsage({ inputTokens: 10, outputTokens: 5, source: "x", scope: "session" })).toBeNull();
    expect(parseTokenUsage({ inputTokens: 10, source: "", scope: "attempt" })).toBeNull();
    expect(parseTokenUsage({ inputTokens: "many", source: "x", scope: "attempt" })).toBeNull();
    expect(parseTokenUsage({ inputTokens: -3, source: "x", scope: "attempt" })).toBeNull();
    // The cached input is a subset of the input total; a larger value is not a fact.
    expect(parseTokenUsage({ inputTokens: 10, cachedInputTokens: 11, source: "x", scope: "attempt" })).toBeNull();
    expect(parseTokenUsage([{ inputTokens: 10, source: "x", scope: "attempt" }])).toBeNull();
  });

  it("formats counts deterministically", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(999)).toBe("999");
    expect(formatCount(1234567)).toBe("1,234,567");
  });
});

describe("recorded harness quota (ADR-018 §23)", () => {
  const quota = {
    observedAt: "2026-09-29T08:00:00.000Z", source: "dsh-native", provider: "deepseek-official", stale: false,
    windows: [
      { name: "5h", usedPercent: 93, resetsAt: "2026-09-29T12:00:00.000Z" },
      { name: "weekly", usedPercent: 41, resetsAt: null },
    ],
  };

  it("parses the recorded observation and flags the window at or above the threshold", () => {
    const view = quotaView(parseQuota(quota))!;
    expect(view.source).toBe("dsh-native");
    expect(view.provider).toBe("deepseek-official");
    expect(view.stale).toBe(false);
    expect(view.alert).toBe(true);
    expect(view.windows[0]).toMatchObject({ name: "5h", usedText: "93%", nearLimit: true, atLimit: false });
    expect(view.windows[1]).toMatchObject({ name: "weekly", usedText: "41%", nearLimit: false });
    expect(QUOTA_NEAR_LIMIT_PERCENT).toBe(90);
    expect(view.note).toContain("不代表实时账户额度");
  });

  it("keeps an unknown window unknown and never turns it into 0% or a near-limit warning", () => {
    const view = quotaView(parseQuota({
      observedAt: "2026-09-29T08:00:00.000Z", source: "codex-native", stale: true,
      windows: [{ name: "5h", usedPercent: null, resetsAt: null }],
    }))!;
    expect(view.windows[0].usedText).toBe("未知");
    expect(view.windows[0].nearLimit).toBe(false);
    expect(view.alert).toBe(false);
    expect(view.stale).toBe(true);
    expect(view.note).toContain("可能已经过期");
  });

  it("marks a stale near-limit observation as the last observation, not as a live warning", () => {
    const view = quotaView(parseQuota({ ...quota, stale: true }))!;
    expect(view.alert).toBe(false);
    expect(view.stale).toBe(true);
    expect(view.note).toContain("最近一次");
  });

  it("keeps a native limit warning without inventing a utilization window", () => {
    const recorded = { ...quota, windows: [], reachedType: "usageLimitExceeded" };
    const view = quotaView(parseQuota(recorded))!;
    expect(view.alert).toBe(true);
    expect(view.limitReported).toBe(true);
    expect(view.windows).toEqual([]);
    expect(view.note).toContain("重置状态可能未知");
    expect(quotaView(parseQuota({ ...recorded, stale: true }))!.alert).toBe(false);
    expect(quotaView(parseQuota({ ...quota, windows: [], ordinaryUsageAllowed: false }))!.alert).toBe(true);
  });

  it("does not alert for an expired window beside a fresh lower window", () => {
    const view = quotaView(parseQuota({ ...quota, stale: false, windows: [
      { name: "expired", usedPercent: 99, resetsAt: "2026-09-28T00:00:00Z", stale: true },
      { name: "fresh", usedPercent: 25, resetsAt: null, stale: false },
    ] }))!;
    expect(view.alert).toBe(false);
    expect(view.windows[0].usedText).toBe("99%");
    expect(view.windows[0].nearLimit).toBe(false);
  });

  it("treats a missing or malformed observation as no observation", () => {
    expect(parseQuota(null)).toBeNull();
    expect(quotaView(null)).toBeNull();
    expect(parseQuota({ observedAt: "2026-09-29T08:00:00.000Z", windows: [] })).toBeNull();
    expect(parseQuota({ observedAt: "2026-09-29T08:00:00.000Z", source: "x", windows: "near-limit" })).toBeNull();
    expect(parseQuota({ observedAt: "2026-09-29T08:00:00.000Z", source: "x",
      windows: [{ name: "5h", usedPercent: "93", resetsAt: null }] })).toBeNull();
    expect(parseQuota({ observedAt: "", source: "x", windows: [] })).toBeNull();
  });
});

describe("Host conclusion of a failed or cancelled goal (ADR-018 §16)", () => {
  it("labels the recorded outcome without turning it into an acceptance", () => {
    const view = hostConclusionView({
      conclusionId: "concl-1", attemptId: "att-1", executionStatus: "cancelled",
      note: "已把未完成的改动提取为提交", evidence: ["diff 已保存", "diff 已保存", "检出已保留"],
      artifactId: "art-partial", integrationId: null, actor: "host-a", createdAt: "2026-09-29T09:00:00.000Z",
      ownerGeneration: 2, runRevision: 5,
    })!;
    expect(view.statusLabel).toBe("已取消");
    expect(view.cancelled).toBe(true);
    expect(view.failed).toBe(false);
    expect(view.note).toBe("已把未完成的改动提取为提交");
    expect(view.evidence).toEqual(["diff 已保存", "检出已保留"]);
    expect(view.references).toEqual([
      { label: "被审查执行", value: "att-1" },
      { label: "关联成果", value: "art-partial" },
    ]);
    expect(view.ownerGeneration).toBe(2);
    expect(view.runRevision).toBe(5);
  });

  it("shows a failed conclusion as failed and keeps unknown fields unknown", () => {
    const view = hostConclusionView({
      conclusionId: "concl-2", attemptId: null, executionStatus: "failed", note: "",
      evidence: ["额度耗尽"], artifactId: null, integrationId: "int-1", actor: "host-b",
      createdAt: "2026-09-29T09:10:00.000Z", ownerGeneration: 1, runRevision: 3,
    })!;
    expect(view.failed).toBe(true);
    expect(view.statusLabel).toBe("失败");
    expect(view.note).toBe("未记录");
    expect(view.references).toEqual([{ label: "整合记录", value: "int-1" }]);
  });

  it("returns null for a missing or unusable conclusion", () => {
    expect(hostConclusionView(null)).toBeNull();
    expect(hostConclusionView({ note: "没有 ID" })).toBeNull();
  });
});

describe("partial outputs and cumulative patches (ADR-018 §17, §20)", () => {
  it("labels a partial output as partial, unverified and non-final", () => {
    const state = artifactStateView({ kind: "partial-output", partial: true, verified: false, final: false });
    expect(state.partial).toBe(true);
    expect(state.verified).toBe(false);
    expect(state.final).toBe(false);
    expect(state.text).toContain("未验证");
    expect(state.text).toContain("非最终");
    expect(artifactKindLabel("partial-output")).toBe("部分成果（未验证、非最终）");
  });

  it("never treats a partial output as the recorded final artifact", () => {
    const workflow = {
      finalArtifactId: "art-partial",
      artifacts: [{ artifactId: "art-partial", kind: "partial-output", partial: true, verified: false, final: false }],
    } as unknown as Workflow;
    expect(finalArtifact(workflow)).toBeNull();
  });

  it("keeps a recorded cumulative patch and names unknown fields instead of inventing them", () => {
    const patch = cumulativePatchView({
      baseCommit: "base-1", outputCommit: "out-1", path: "/x/cumulative.diff",
      sha256: "a".repeat(64), changedPaths: ["a.py", "a.py", "b.py"],
    })!;
    expect(patch.complete).toBe(true);
    expect(patch.changedPaths).toEqual(["a.py", "b.py"]);
    const incomplete = cumulativePatchView({ baseCommit: "base-1" })!;
    expect(incomplete.complete).toBe(false);
    expect(incomplete.outputCommit).toBe("未记录");
    expect(incomplete.sha256).toBe("未记录");
    expect(cumulativePatchView(null)).toBeNull();
    expect(cumulativePatchView({})).toBeNull();
  });

  it("lists the Host's own supplementary paths separately from the artifact's", () => {
    const record = { verification: { verified: true, hostPaths: ["docs/a.md", "", "docs/a.md", "src/b.py"] } } as unknown as IntegrationRecord;
    expect(integrationHostPaths(record)).toEqual(["docs/a.md", "src/b.py"]);
    expect(integrationHostPaths(null)).toEqual([]);
    expect(integrationHostPaths({ verification: {} } as IntegrationRecord)).toEqual([]);
    expect(integrationHostPaths({ verification: { hostPaths: "docs/a.md" } } as unknown as IntegrationRecord)).toEqual([]);
  });
});

describe("explicit configuration lock (ADR-018 §18)", () => {
  it("states lock, host choice and unknown separately", () => {
    expect(configurationLockText(true)).toContain("不能更换");
    expect(configurationLockText(false)).toContain("可由 Host");
    expect(configurationLockText(undefined)).toBeNull();
    expect(configurationLockText(null)).toBeNull();
  });
});
