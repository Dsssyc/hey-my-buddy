import { describe, expect, it } from "vitest";
import type { TimelineRow, TimelineSpan } from "./macro-task-types";
import { acceptanceWaitMs, buddyLabel, rowStateInfo, spanOutcome } from "./macro-task-display";

describe("0.16 friendly buddy names (0.3)", () => {
  const config = (model: string, effort: string | null = "high") => ({
    harness: "claude", provider: "anthropic", model, effort,
  });

  it("prefers a matching snapshot buddy name, then known ids, then the raw id", () => {
    const buddies = [{ harness: "claude", provider: "anthropic", model: "claude-opus-5-5", label: "Claude Opus 5.5 · high", effort: "high" }];
    expect(buddyLabel(config("claude-opus-5-5"), buddies)).toBe("Claude Opus 5.5 · high");
    expect(buddyLabel(config("claude-sonnet-5"))).toBe("Claude Sonnet 5 · high");
    expect(buddyLabel(config("glm-5.3", "max"))).toBe("GLM-5.3 · max");
    expect(buddyLabel(config("my-own-model"))).toBe("my-own-model · high");
    expect(buddyLabel(null)).toBe("buddy 未记录");
  });

  it("omits the effort half when it is empty, and keeps every native name including off", () => {
    expect(buddyLabel(config("glm-5.3", ""))).toBe("GLM-5.3");
    expect(buddyLabel(config("glm-5.3", null))).toBe("GLM-5.3");
    expect(buddyLabel(config("glm-5.3", "off"))).toBe("GLM-5.3 · off");
  });

});

describe("recorded row state", () => {
  it("attributes Host waits to a recorded Lead owner", () => {
    expect(rowStateInfo({ state: "awaiting-host", rootLeadRunId: "lead-1" } as TimelineRow).label).toBe("等待 Lead");
    expect(rowStateInfo({ state: "waiting-owned" } as TimelineRow).label).toBe("等待名下的微任务");
  });
});

describe("0.16 acceptance wait (P1.8)", () => {
  const row: TimelineRow = {
    runId: "r1", parentRunId: null, rootRunId: "r1", rootLeadRunId: null, title: "修复标题错位", titleSource: "title", taskSummary: null,
    summary: null, createdAt: "2026-09-26T01:00:00Z", state: "accepted", status: "completed", category: "ended",
    shutdownConfirmed: true, depth: 0, kind: "micro-task", buddy: null,
    acceptedAt: "2026-09-26T07:25:00Z", acceptanceVerdict: "accepted", reviewRejectedAt: null, reviewRejectionNote: null,
  };
  const execution = (startAt: string, endAt: string | null, overrides: Partial<TimelineSpan> = {}): TimelineSpan => ({
    spanId: "e", runId: "r1", kind: "execution", startAt, endAt, state: "finished",
    attemptId: "a", turnId: null, turnIndex: 1, requestId: null, buddy: null,
    expectedSeconds: 1800, reminders: null, inquiries: [],
    shutdownConfirmed: true, uncertain: false, clockSkew: false, ...overrides,
  });

  it("measures the wait only from a recorded, confirmed end at or before the acceptance", () => {
    expect(acceptanceWaitMs(row, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z")])).toBe(2 * 3600_000);
    // An unconfirmed stop never draws a line or a wait.
    expect(acceptanceWaitMs(row, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z", { uncertain: true, state: "uncertain", shutdownConfirmed: false })])).toBeNull();
    // A missing end time is unknown, not zero.
    expect(acceptanceWaitMs(row, [execution("2026-09-26T05:20:00Z", null)])).toBeNull();
    // An end after the acceptance is corrupt, never a negative wait.
    expect(acceptanceWaitMs(row, [execution("2026-09-26T07:00:00Z", "2026-09-26T08:00:00Z")])).toBeNull();
    expect(acceptanceWaitMs({ ...row, acceptedAt: null }, [execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z")])).toBeNull();
  });

  it("requires the last execution's shutdownConfirmed === true; a finished span with an end is not confirmation", () => {
    const ended = (shutdownConfirmed: boolean | null | undefined) =>
      execution("2026-09-26T05:20:00Z", "2026-09-26T05:25:00Z", { shutdownConfirmed } as Partial<TimelineSpan>);
    expect(acceptanceWaitMs(row, [ended(true)])).toBe(2 * 3600_000);
    expect(acceptanceWaitMs(row, [ended(null)])).toBeNull();
    expect(acceptanceWaitMs(row, [ended(false)])).toBeNull();
    expect(acceptanceWaitMs(row, [ended(undefined)])).toBeNull();
    // Only the last stop counts: an earlier confirmed end cannot stand in for
    // a later unconfirmed one, nor for a later execution without an end.
    const earlier = execution("2026-09-26T03:00:00Z", "2026-09-26T03:10:00Z", { spanId: "e0" });
    expect(acceptanceWaitMs(row, [earlier, ended(null)])).toBeNull();
    expect(acceptanceWaitMs(row, [earlier, execution("2026-09-26T05:20:00Z", null, { spanId: "e2", state: "running" })])).toBeNull();
    expect(acceptanceWaitMs(row, [ended(null), { ...earlier, spanId: "e3", startAt: "2026-09-26T06:00:00Z", endAt: "2026-09-26T06:25:00Z" }])).toBe(3600_000);
  });

});

describe("routing outcome precedence", () => {
  it("keeps an abstention distinct from failure and gives unconfirmed shutdown precedence", () => {
    const span = { kind: "routing", state: "finished", disposition: "abstention", resultStatus: "ok", error: "no target" } as TimelineSpan;
    expect(spanOutcome(span)).toBe("abstention");
    expect(spanOutcome({ ...span, uncertain: true })).toBe("unknown");
  });
});
