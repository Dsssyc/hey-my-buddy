import { describe, expect, it } from "vitest";
import type { ObjectiveSummary, TimelineEvent, TimelineRow, TimelineSpan } from "./objective-types";
import {
  acceptanceWaitMs, acceptanceWaitText, configurationLabel, configurationNamer, eventClusterGlyph,
  eventClusterLabel, eventSentence, objectiveProgressText, objectiveStateLabel, spanFacts,
} from "./objective-display";

function summary(overrides: Partial<ObjectiveSummary> = {}): ObjectiveSummary {
  return {
    objectiveId: "obj-x", kind: "objective", title: "示例目标", titleSource: "objective",
    description: null, summary: null,
    project: { id: "p", path: null, label: "p" }, sourceHostId: "codex", currentHostIds: ["codex"],
    createdAt: "2026-09-26T01:00:00Z", lastActivityAt: "2026-09-26T08:00:00Z", lastActivitySeq: 1,
    state: "active", counts: { roots: 3, helpers: 0, accepted: 1, active: 1, host: 0, review: 0, ended: 2 },
    matchingRuns: 3, rootRunIds: [],
    ...overrides,
  };
}

describe("0.16 status vocabulary (0.2)", () => {
  it("derives 已完成 only from full acceptance counts, never from Worker summaries", () => {
    expect(objectiveStateLabel(summary({ state: "ended", counts: { roots: 3, helpers: 0, accepted: 3, active: 0, host: 0, review: 0, ended: 3 }, summary: "全部完成" })))
      .toBe("已完成");
    expect(objectiveStateLabel(summary({ state: "ended", counts: { roots: 3, helpers: 0, accepted: 2, active: 0, host: 0, review: 0, ended: 3 }, summary: "全部完成" })))
      .toBe("已结束");
    expect(objectiveStateLabel(summary({ state: "host" }))).toBe("等待 Host");
    expect(objectiveStateLabel(summary({ state: "review" }))).toBe("等待验收");
  });

  it("composes the header progress sentence", () => {
    expect(objectiveProgressText(summary({ state: "ended", counts: { roots: 3, helpers: 0, accepted: 3, active: 0, host: 0, review: 0, ended: 3 } })))
      .toBe("已完成 · 3 / 3 个委派已验收");
    expect(objectiveProgressText(summary())).toBe("进行中 · 1 / 3 个委派已验收");
  });
});

describe("0.16 friendly configuration names (0.3)", () => {
  const config = (model: string, effort: string | null = "high") => ({
    adapter: "claude", provider: "anthropic", model, effort,
  });

  it("prefers a matching snapshot profile name, then known ids, then the raw id", () => {
    const profiles = [{ adapter: "claude", provider: "anthropic", model: "claude-opus-5-5", label: "Claude Opus 5.5 · high", effort: "high" }];
    expect(configurationLabel(config("claude-opus-5-5"), profiles)).toBe("Claude Opus 5.5 · high");
    expect(configurationLabel(config("claude-sonnet-5"))).toBe("Claude Sonnet 5 · high");
    expect(configurationLabel(config("glm-5.3", "max"))).toBe("GLM-5.3 · max");
    expect(configurationLabel(config("my-own-model"))).toBe("my-own-model · high");
    expect(configurationLabel(null)).toBe("配置未记录");
  });

  it("omits the effort half when it is default or empty, and maps off", () => {
    expect(configurationLabel(config("glm-5.3", ""))).toBe("GLM-5.3");
    expect(configurationLabel(config("glm-5.3", null))).toBe("GLM-5.3");
    expect(configurationNamer(null)(config("glm-5.3", "off")).text).toBe("GLM-5.3 · 非思考");
  });

  it("keeps the raw adapter/provider/model/effort identity for tooltips", () => {
    expect(configurationNamer(null)(config("claude-opus-5-5")).title).toBe("claude / anthropic / claude-opus-5-5 / high");
  });
});

describe("0.16 Host event sentences and cluster glyphs (P1.7)", () => {
  const row = (title: string): TimelineRow => ({
    runId: "r1", parentRunId: null, rootRunId: "r1", title, titleSource: "title", taskSummary: null, summary: null,
    createdAt: "2026-09-26T01:00:00Z", state: "delivered", status: "completed", category: "review",
    shutdownConfirmed: true, depth: 0, kind: "goal", configuration: null, acceptedAt: null, acceptanceVerdict: null,
  });
  const event = (kind: string, at = "2026-09-26T05:20:00Z", extra: Partial<TimelineEvent> = {}): TimelineEvent => ({
    seq: 1, runId: "r1", kind, at, label: "", summary: "", actor: null,
    attemptId: null, requestId: null, artifactId: null, ...extra,
  });

  it("writes one natural-language sentence per kind with the delegation's short title", () => {
    expect(eventSentence(event("dispatch"), row("修复标题"))).toMatch(/^\d{2}:\d{2} Host 派发「修复标题」$/);
    expect(eventSentence(event("decide"), row("修复标题"))).toContain("Host 批准 / 拒绝了「修复标题」的请求");
    expect(eventSentence(event("decide", "2026-09-26T05:20:00Z", { eventKind: "workflow.request_approved" }), row("修复标题")))
      .toContain("Host 批准了「修复标题」的请求");
    expect(eventSentence(event("continue"), row("修复标题"))).toContain("Host 让「修复标题」继续");
    expect(eventSentence(event("integrate"), row("修复标题"))).toContain("Host 整合了「修复标题」的产出");
    expect(eventSentence(event("accept"), row("修复标题"))).toContain("Host 验收通过「修复标题」");
    expect(eventSentence(event("reject"), row("修复标题"))).toContain("Host 对「修复标题」提出验收问题");
    expect(eventSentence(event("cancel"), row("修复标题"))).toContain("Host 取消了「修复标题」");
    expect(eventSentence(event("takeover"), row("修复标题"))).toContain("Host 接管了「修复标题」");
  });

  it("labels clusters by kind with counts and picks the shared glyph", () => {
    const dispatches = [event("dispatch"), event("dispatch", "2026-09-26T05:21:00Z")];
    expect(eventClusterLabel(dispatches)).toBe("Host 事件 2 条：派发 2，按 Enter 列出");
    expect(eventClusterGlyph(dispatches)).toEqual({ glyph: "▶", count: 2 });
    const mixed = [event("dispatch"), event("accept", "2026-09-26T05:21:00Z")];
    expect(eventClusterGlyph(mixed)).toEqual({ glyph: "≡", count: 2 });
    expect(eventClusterLabel(mixed)).toBe("Host 事件 2 条：派发、验收，按 Enter 列出");
    expect(eventClusterGlyph([event("dispatch")])).toEqual({ glyph: "▶", count: 0 });
  });
});

describe("0.16 span-facts locator (T4)", () => {
  const row: TimelineRow = {
    runId: "r1", parentRunId: null, rootRunId: "r1", title: "修复标题错位", titleSource: "title", taskSummary: null,
    summary: null, createdAt: "2026-09-26T01:00:00Z", state: "delivered", status: "completed", category: "review",
    shutdownConfirmed: true, depth: 0, kind: "goal",
    configuration: { adapter: "claude", provider: "anthropic", model: "claude-opus-5-5", effort: "high" },
    acceptedAt: null, acceptanceVerdict: null,
  };
  const span = (overrides: Partial<TimelineSpan> = {}): TimelineSpan => ({
    spanId: "s1", runId: "r1", kind: "execution", startAt: "2026-09-26T05:29:00Z", endAt: "2026-09-26T05:34:00Z",
    state: "finished", attemptId: "a1", turnId: null, turnIndex: 1, requestId: null,
    configuration: row.configuration, shutdownConfirmed: true, uncertain: false, clockSkew: false, ...overrides,
  });

  it("states only the round and time; no title and no configuration", () => {
    expect(spanFacts(span(), row, null).item.locator).toMatch(/^第 1 轮 \d{2}:\d{2}–\d{2}:\d{2}$/);
    expect(spanFacts(span(), row, null).item.locator).not.toContain("修复标题错位");
    expect(spanFacts(span(), row, null).item.locator).not.toContain("claude");
    expect(spanFacts(span({ kind: "queue" }), row, null).item.locator).toMatch(/^排队 \d{2}:\d{2}–\d{2}:\d{2}$/);
    expect(spanFacts(span({ kind: "host" }), row, null).item.locator).toMatch(/^等待 Host \d{2}:\d{2}–\d{2}:\d{2}$/);
    expect(spanFacts(span({ endAt: null }), row, null).item.locator).toMatch(/^第 1 轮 \d{2}:\d{2} 起 · 结束时间缺失$/);
    expect(spanFacts(span({ startAt: null, endAt: null }), row, null).item.locator).toBe("第 1 轮 时间未记录");
  });
});

describe("0.16 acceptance wait (P1.8)", () => {
  const row: TimelineRow = {
    runId: "r1", parentRunId: null, rootRunId: "r1", title: "修复标题错位", titleSource: "title", taskSummary: null,
    summary: null, createdAt: "2026-09-26T01:00:00Z", state: "accepted", status: "completed", category: "ended",
    shutdownConfirmed: true, depth: 0, kind: "goal", configuration: null,
    acceptedAt: "2026-09-26T07:25:00Z", acceptanceVerdict: "accepted",
  };
  const execution = (startAt: string, endAt: string | null, overrides: Partial<TimelineSpan> = {}): TimelineSpan => ({
    spanId: "e", runId: "r1", kind: "execution", startAt, endAt, state: "finished",
    attemptId: "a", turnId: null, turnIndex: 1, requestId: null, configuration: null,
    shutdownConfirmed: true, uncertain: false, clockSkew: false, ...overrides,
  });

  it("measures the wait only from a recorded, confirmed end at or before the acceptance", () => {
    expect(acceptanceWaitMs(row, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z")])).toBe(2 * 3600_000);
    expect(acceptanceWaitText(row, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z")])).toBe("等待验收 2 小时");
    // An unconfirmed stop never draws a line or a wait.
    expect(acceptanceWaitMs(row, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z", { uncertain: true, state: "uncertain", shutdownConfirmed: false })])).toBeNull();
    // A missing end time is unknown, not zero.
    expect(acceptanceWaitMs(row, [execution("2026-09-26T05:20:00Z", null)])).toBeNull();
    // An end after the acceptance is corrupt, never a negative wait.
    expect(acceptanceWaitMs(row, [execution("2026-09-26T07:00:00Z", "2026-09-26T08:00:00Z")])).toBeNull();
    expect(acceptanceWaitMs({ ...row, acceptedAt: null }, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z")])).toBeNull();
  });

  it("words a rejected verdict as a pending conclusion", () => {
    expect(acceptanceWaitText({ ...row, acceptanceVerdict: "rejected" }, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z")]))
      .toContain("等待验收结论");
  });
});
