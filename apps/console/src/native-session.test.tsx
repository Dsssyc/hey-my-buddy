import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { NativeSessionView, nativeSessionEvidence } from "./native-session";
import type { Task } from "./types";

afterEach(cleanup);

/** The frozen receipt envelope: task -> selectedAttempt -> result -> result. */
function taskWith(result: Record<string, unknown>, adapter = "dsh"): Task {
  return {
    runId: "run", task: "目标", status: "completed", owner: "host", cwd: "/repo", revision: 1,
    createdAt: "2026-09-25", acceptedAt: null, acceptanceVerdict: null,
    selectedAttempt: { attemptId: "attempt-a", adapter, result: { status: "ok", result, shutdownConfirmed: true } },
  } as Task;
}

describe("native session evidence from the attempt receipt", () => {
  it("reads the ZCode private-session facts from the recorded envelope", () => {
    const evidence = nativeSessionEvidence(taskWith({
      nativeSession: {
        adapter: "zcode", sessionId: "sess-z", captured: true, storageScope: "task-private",
        storageOwner: "buddy-attempt", nativeAppVisibility: "not-listed-in-native-app",
        resumeMode: "native-session", bindingPresent: true, resumable: true, note: "私有原生根",
      },
    }, "zcode"));
    expect(evidence).toMatchObject({
      adapter: "zcode", sessionId: "sess-z", storageScope: "task-private", resumeMode: "native-session",
      bindingPresent: true, resumable: true, note: "私有原生根",
    });
  });

  it("reads the Codex native-session facts from the same envelope", () => {
    const evidence = nativeSessionEvidence(taskWith({
      nativeSession: {
        adapter: "codex", sessionId: "thread-9", captured: true, storageScope: "harness-user-store",
        storageOwner: "harness-user-store", nativeAppVisibility: "unknown", resumeMode: "native-session",
        resumable: true, note: "Codex owns the native thread in its configured home",
      },
    }, "codex"));
    expect(evidence).toMatchObject({
      adapter: "codex", sessionId: "thread-9", storageScope: "harness-user-store", storageOwner: "harness-user-store",
      appVisibility: "unknown", resumeMode: "native-session", resumable: true,
    });
  });

  it("does not treat retired top-level Codex storage fields as native session evidence", () => {
    // Every adapter now records its facts in `nativeSession`; the old
    // `nativeSessionStorage`/`nativeAppVisible` shape is not part of the
    // current single contract and stays unknown.
    expect(nativeSessionEvidence(taskWith(
      { nativeSessionStorage: "codex-home", nativeAppVisible: null, sessionId: "thread-9" }, "codex",
    ))).toBeNull();
  });

  it("never reads `nativeSession` from any other level of the receipt", () => {
    // A value one level up is not part of the frozen envelope and stays unknown.
    expect(nativeSessionEvidence(taskWith({ sessionId: "loose" }))).toBeNull();
    const wrong: Task = { ...taskWith({}), selectedAttempt: { attemptId: "a", result: { nativeSession: { sessionId: "wrong-level" } } } } as Task;
    expect(nativeSessionEvidence(wrong)).toBeNull();
    // Out-of-shape values do not become invented facts either.
    expect(nativeSessionEvidence(taskWith({ nativeSession: { sessionId: 7, resumable: "yes" } }))?.sessionId).toBeNull();
    expect(nativeSessionEvidence(taskWith({ nativeSession: "not-an-object" }))).toBeNull();
  });

  it("renders the ZCode private storage and visibility without an invented open link", () => {
    render(<NativeSessionView task={taskWith({
      nativeSession: {
        adapter: "zcode", sessionId: "sess-z", storageScope: "task-private", storageOwner: "buddy-attempt",
        nativeAppVisibility: "not-listed-in-native-app", resumeMode: "native-session", bindingPresent: true,
        resumable: true, note: "私有原生根",
      },
    })} turnSessionId="turn-session" />);
    const view = screen.getByRole("region", { name: "原生会话（只读）" });
    expect(view.textContent).toContain("sess-z");
    expect(view.textContent).toContain("Buddy 私有存储");
    expect(view.textContent).toContain("未在原生 App 中列出");
    expect(view.textContent).toContain("接续原生会话");
    expect(view.textContent).toContain("已记录为可恢复");
    expect(view.textContent).toContain("私有绑定存在");
    expect(view.textContent).toContain("私有原生根");
    // No fabricated launcher: a private store cannot be opened from this page.
    expect(screen.queryByRole("link")).toBeNull();
    expect(view.textContent).toContain("私有原生库不能从本页面打开");
  });

  it("renders the Codex native session as harness-owned with unverified visibility", () => {
    render(<NativeSessionView task={taskWith({
      nativeSession: {
        adapter: "codex", sessionId: "thread-9", captured: true, storageScope: "harness-user-store",
        storageOwner: "harness-user-store", nativeAppVisibility: "unknown", resumeMode: "native-session",
        resumable: true, note: "Codex owns the native thread in its configured home",
      },
    }, "codex")} />);
    const view = screen.getByRole("region", { name: "原生会话（只读）" });
    expect(view.textContent).toContain("thread-9");
    expect(view.textContent).toContain("Harness 用户存储");
    expect(view.textContent).toContain("接续原生会话");
    expect(view.textContent).toContain("已记录为可恢复");
    // `unknown` visibility and a missing binding stay unknown, never "visible".
    expect(view.textContent).toContain("未记录（未知）");
    expect(view.textContent).not.toContain("私有绑定存在");
    expect(view.textContent).toContain("Codex owns the native thread");
  });

  it("keeps missing storage and visibility unknown while showing the turn session id", () => {
    render(<NativeSessionView task={taskWith({})} turnSessionId="turn-session" />);
    const view = screen.getByRole("region", { name: "原生会话（只读）" });
    expect(view.textContent).toContain("turn-session");
    expect(view.textContent).toContain("未记录（未知）");
  });
});
