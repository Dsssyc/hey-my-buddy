import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./Settings";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { createApi } from "./api";

const editor = {
  editing: false, configurationDirty: false, sessionWritable: true, mode: null, draft: null,
  setDraft: vi.fn(),
} as unknown as Editor;
const api = createApi("/test", vi.fn() as unknown as typeof fetch);

function snapshot(routingHealth: Snapshot["routingHealth"]): Snapshot {
  return {
    csrfToken: "csrf", consoleSession: { id: "s", canWrite: false, reason: "superseded" }, tableRevision: 1,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: null },
    profiles: [], cards: [], preferences: [], annotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { runs: [], total: 0 }, capabilities: {},
    routingHealth,
  };
}

afterEach(() => cleanup());

describe("settings routing status (R4)", () => {
  it("says the summary is unavailable when the snapshot carries no routingHealth", () => {
    render(<Settings snapshot={snapshot(undefined)} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
    expect(status.textContent).toContain("路由摘要暂不可用");
    // Missing data is never rendered as a zero-failure claim.
    expect(status.textContent).not.toContain("失败 0 次");
  });

  it("reports an empty window as no recorded samples rather than zero failures", () => {
    render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 0, failureCount: 0, consecutiveFailures: 0,
      abstentionCount: 0, cancelledCount: 0, staleCount: 0,
      lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
    })} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
    expect(status.textContent).toContain("窗口内暂无已记录样本（窗口上限 20 次）");
    expect(status.textContent).not.toContain("失败 0 次");
  });

  it("shows the window counts, separated non-failures, last success and bounded recent failures", () => {
    render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 18, failureCount: 4, consecutiveFailures: 2,
      abstentionCount: 3, cancelledCount: 1, staleCount: 2,
      lastSuccessAt: "2026-09-25T08:02:00Z", lastSuccessDecisionId: "dec-41",
      recentFailures: [
        { decisionId: "dec-48", runId: "run-a", at: "2026-09-27T09:41:00Z", code: "call-failed" },
        { decisionId: "dec-47", runId: null, at: "2026-09-27T09:12:00Z", code: "needs-host" },
        { decisionId: "dec-46", runId: "run-b", at: "2026-09-27T08:44:00Z", code: "timeout" },
      ],
    })} editor={editor} api={api} />);
    const status = screen.getByLabelText("路由状态");
    expect(status.textContent).toContain("最近 18 次中失败 4 次（窗口上限 20 次）");
    expect(status.textContent).toContain("连续失败 2 次");
    expect(status.textContent).toContain("弃权 3 次");
    expect(status.textContent).toContain("取消 1 次");
    expect(status.textContent).toContain("过期 2 次");
    expect(status.textContent).toContain("不计为失败");
    expect(status.textContent).not.toContain("无成功记录");
    expect(status.textContent).toMatch(/最后一次成功：09-25 \d{2}:\d{2} · dec-41/);
    expect(status.querySelectorAll(".routing-failures li").length).toBe(3);
    expect(status.textContent).toContain("call-failed");
    expect(status.textContent).toContain("needs-host");
    expect(status.textContent).toContain("委派未记录");
    // Read-only note; no retry or routing control appears.
    expect(status.textContent).toContain("读取不触发模型");
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });

  it("stays visible for read-only sessions and never depends on the draft", () => {
    const view = render(<Settings snapshot={snapshot({
      windowSize: 20, sampleCount: 5, failureCount: 1, consecutiveFailures: 1,
      abstentionCount: 0, cancelledCount: 0, staleCount: 0,
      lastSuccessAt: "2026-09-27T07:00:00Z", lastSuccessDecisionId: null, recentFailures: [],
    })} editor={editor} api={api} />);
    expect(view.container.querySelector(".routing-status")).toBeTruthy();
    expect(screen.getByLabelText("路由状态").textContent).toContain("最近 5 次中失败 1 次");
  });
});
