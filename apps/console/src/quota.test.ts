import { describe, expect, it } from "vitest";
import { parseQuota, quotaView, QUOTA_NEAR_LIMIT_PERCENT } from "./quota";

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
    expect(view.note).toContain("非实时");
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
    expect(view.note).toContain("已过期");
  });

  it("marks a stale near-limit observation as the last observation, not as a live warning", () => {
    const view = quotaView(parseQuota({ ...quota, stale: true }))!;
    expect(view.alert).toBe(false);
    expect(view.stale).toBe(true);
    expect(view.note).toContain("已过期");
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

