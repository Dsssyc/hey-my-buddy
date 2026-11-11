import { describe, expect, it } from "vitest";
import i18n from "i18next";
import type { TimelineRow, TimelineSpan } from "./macro-task-types";
import { acceptanceWaitMs, buddyLabel, buddyNameParts, rowStateInfo, spanOutcome } from "./macro-task-display";

describe("recorded model names", () => {
  const config = (model: string, effort: string | null = "high") => ({
    harness: "claude", provider: "anthropic", model, effort,
  });

  it("uses every recorded model id without a display-name mapping", () => {
    for (const model of ["claude-opus-5-5", "claude-sonnet-5", "glm-5.3", "my-own-model"]) {
      expect(buddyNameParts(config(model))).toEqual({ name: model, effort: "high" });
      expect(buddyLabel(config(model))).toBe(`${model} · high`);
    }
    expect(buddyLabel(null)).toBe("buddy 未记录");
  });

  it("omits absent efforts and keeps native off", () => {
    expect(buddyLabel(config("glm-5.3", ""))).toBe("glm-5.3");
    expect(buddyLabel(config("glm-5.3", null))).toBe("glm-5.3");
    expect(buddyLabel(config("glm-5.3", "off"))).toBe("glm-5.3 · off");
  });
});

describe("recorded row state", () => {
  it("shows a paused queued row as paused", async () => {
    try {
      for (const [language, label] of [["zh", "已暂停"], ["en", "Paused"]] as const) {
        await i18n.changeLanguage(language);
        expect(rowStateInfo({ state: "executing", status: "queued", queueReason: "paused" } as TimelineRow)).toMatchObject({ label, settled: false });
      }
    } finally { await i18n.changeLanguage("zh"); }
  });
  it("attributes Host waits to a recorded Lead owner", () => {
    expect(rowStateInfo({ state: "awaiting-host", rootLeadRunId: "lead-1" } as TimelineRow).label).toBe("等待 Lead");
    expect(rowStateInfo({ state: "waiting-owned" } as TimelineRow).label).toBe("等待名下的微任务");
  });
  it("keeps a cancelled Lead stopping while its owned execution has no stop evidence", () => {
    const lead = { runId: "lead", state: "cancelled", status: "cancelled", shutdownConfirmed: true } as TimelineRow;
    const owned = { runId: "owned", rootLeadRunId: lead.runId, state: "cancelled", status: "cancelling", shutdownConfirmed: false } as TimelineRow;
    expect(rowStateInfo(owned, [lead, owned]).label).toBe("正在停止");
    expect(rowStateInfo(lead, [lead, owned]).label).toBe("正在停止");
    expect(rowStateInfo(lead, [lead, { ...owned, status: "cancelled", shutdownConfirmed: true }]).label).toBe("已取消，等待 Host");
  });
  it("keeps unconcluded failures and cancellations waiting for their recorded owner in both languages", async () => {
    try {
      for (const language of ["zh", "en"] as const) {
        await i18n.changeLanguage(language);
        for (const [state, rootLeadRunId, zh, en] of [
          ["failed", null, "失败，等待 Host", "Failed, waiting for Host"],
          ["cancelled", null, "已取消，等待 Host", "Cancelled, waiting for Host"],
          ["failed", "lead", "失败，等待 Lead", "Failed, waiting for Lead"],
          ["cancelled", "lead", "已取消，等待 Lead", "Cancelled, waiting for Lead"],
        ] as const) {
          expect(rowStateInfo({ state, rootLeadRunId } as TimelineRow)).toMatchObject({
            label: language === "zh" ? zh : en, tone: "amber", settled: false,
          });
        }
      }
    } finally { await i18n.changeLanguage("zh"); }
  });
  it("keeps failed and cancelled helpers terminal regardless of their root's owner", async () => {
    try {
      for (const language of ["zh", "en"] as const) {
        await i18n.changeLanguage(language);
        for (const rootLeadRunId of [null, "lead"]) {
          expect(rowStateInfo({ kind: "helper", state: "failed", rootLeadRunId } as TimelineRow)).toMatchObject({
            label: language === "zh" ? "执行失败" : "Execution failed", tone: "red", glyph: "✕", settled: false,
          });
          expect(rowStateInfo({ kind: "helper", state: "cancelled", rootLeadRunId } as TimelineRow)).toMatchObject({
            label: language === "zh" ? "已取消" : "Cancelled", tone: "neutral", settled: true,
          });
        }
      }
    } finally { await i18n.changeLanguage("zh"); }
  });
  it("uses the execution's active state before a root's failed or cancelled result", () => {
    for (const state of ["failed", "cancelled"]) {
      for (const status of ["queued", "running", "cancelling", "reconciliation-needed"]) {
        const row = rowStateInfo({ kind: "micro-task", state, status, category: "active" } as TimelineRow);
        expect(row.label).not.toContain("等待 Host");
        expect(row.settled).toBe(false);
      }
    }
  });
});

describe("0.16 acceptance wait (P1.8)", () => {
  const row: TimelineRow = {
    runId: "r1", parentRunId: null, rootRunId: "r1", rootLeadRunId: null, delegationAuthority: false, title: "修复标题错位", titleSource: "title", taskSummary: null,
    summary: null, createdAt: "2026-09-26T01:00:00Z", state: "accepted", status: "completed", category: "ended",
    conclusionStatus: null,
    shutdownConfirmed: true, depth: 0, kind: "micro-task", buddy: null,
    acceptedAt: "2026-09-26T07:25:00Z", reviewRejectedAt: null, reviewRejectionNote: null,
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

describe("rerouted execution", () => {
  it("keeps a confirmed pre-model rejection distinct from a failed task", () => {
    const span = { kind: "execution", state: "rejected", resultStatus: "failed", shutdownConfirmed: true } as TimelineSpan;
    expect(spanOutcome(span)).toBe("rejected");
    expect(spanOutcome({ ...span, uncertain: true })).toBe("unknown");
  });
});
