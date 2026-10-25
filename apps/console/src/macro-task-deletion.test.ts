import { describe, expect, it } from "vitest";
import { parseMacroTaskDeletionPreview, parseMacroTaskDeletionResult } from "./macro-task-deletion";

const preview = {
  macroTaskId: "macro-fixture", microTaskCount: 3, unconcludedFailureCount: 1,
  scope: { artifacts: { count: 14, bytes: 6_500_000 }, sessions: { count: 11, bytes: 50_331_648 }, checkouts: 3, gitRefs: 12 },
  blockerTotal: 0, blockers: [], backupRetention: "until-backup-rotation", deletionState: null,
};
const confirmed = { macroTaskId: preview.macroTaskId, microTaskCount: 3, confirm: true as const };
const receipt = { deletionId: "del-fixture", macroTaskId: preview.macroTaskId,
  microTaskCount: 3, duplicate: false, state: "deleted", deletedAt: "2026-10-18T08:00:00Z" };
const blocker = { runId: "run-fixture", taskId: "run-fixture", state: "completed", runState: "delivered",
  title: "作者提供的标题", titleSource: "title",
  reason: { code: "macro-task-deletion.review", params: {} }, attemptIds: [] };

describe("macro task deletion boundary", () => {
  it("reads current structured blockers and explicitly bounded lists", () => {
    const blocked = { ...preview, blockerTotal: 51, blockers: Array.from({ length: 50 }, (_, index) => ({ ...blocker, taskId: `task-${index}` })) };
    expect(parseMacroTaskDeletionPreview(preview.macroTaskId, blocked)).toEqual(blocked);
    expect(parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, deletionState: "deleting" }).deletionState).toBe("deleting");
  });

  it.each([
    { name: "known zero", scope: { artifacts: { count: 0, bytes: 0 }, sessions: { count: 0, bytes: 0 }, checkouts: 0, gitRefs: 0 } },
    { name: "unknown", scope: { artifacts: { count: null, bytes: null }, sessions: { count: null, bytes: null }, checkouts: null, gitRefs: null } },
    { name: "independently known", scope: { artifacts: { count: 0, bytes: null }, sessions: { count: null, bytes: 0 }, checkouts: 3, gitRefs: null } },
  ])("preserves $name scope facts", ({ scope }) => {
    expect(parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, scope }).scope).toEqual(scope);
  });

  it("rejects malformed or incomplete scope facts at the response boundary", () => {
    for (const value of [undefined, null, {}, { ...preview.scope, checkouts: -1 }, { ...preview.scope, gitRefs: Number.NaN },
      { ...preview.scope, artifacts: { count: 0 } }, { ...preview.scope, sessions: null },
      { ...preview.scope, checkouts: 0, extra: 1 }]) {
      expect(() => parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, scope: value })).toThrow();
    }
    for (const value of [undefined, -1, 0.5, true, "0", Number.MAX_SAFE_INTEGER + 1]) {
      for (const field of ["checkouts", "gitRefs"]) {
        expect(() => parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, scope: { ...preview.scope, [field]: value } })).toThrow();
      }
      for (const group of ["artifacts", "sessions"] as const) for (const field of ["count", "bytes"]) {
        const scope = { ...preview.scope, [group]: { ...preview.scope[group], [field]: value } };
        expect(() => parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, scope })).toThrow();
      }
    }
  });

  it("reads author titles, task titles and explicitly unnamed blockers", () => {
    const blockers = [blocker, { ...blocker, taskId: "task-title", title: "任务首行内容", titleSource: "task" },
      { ...blocker, taskId: "unnamed", title: null, titleSource: "none" }];
    expect(parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, blockerTotal: 3, blockers }).blockers).toEqual(blockers);
    for (const value of [{ ...blocker, title: undefined }, { ...blocker, title: 3 }, { ...blocker, title: null },
      { ...blocker, titleSource: "none" }, { ...blocker, titleSource: "macro-task" }, { ...blocker, titleSource: null }]) {
      expect(() => parseMacroTaskDeletionPreview(preview.macroTaskId, { ...preview, blockerTotal: 1, blockers: [value] })).toThrow();
    }
  });

  it("refuses mismatched targets, invalid counts and retired reason fields", () => {
    for (const value of [null, { ...preview, macroTaskId: "macro-other" }, { ...preview, microTaskCount: -1 },
      { ...preview, microTaskCount: true }, { ...preview, unconcludedFailureCount: 4 },
      { ...preview, blockerTotal: 1 }, { ...preview, backupRetention: "forever" },
      { ...preview, deletionState: "archived" }, { ...preview, deletionState: "deleted" },
      { ...preview, blockerTotal: 1, blockers: [{ ...blocker, reasonCode: "review" }] },
      { ...preview, blockerTotal: 1, blockers: [{ ...blocker, reason: "review" }] },
      { ...preview, blockerTotal: 1, blockers: [{ ...blocker, reason: { code: "macro-task-deletion.other", params: {} } }] },
    ]) expect(() => parseMacroTaskDeletionPreview(preview.macroTaskId, value)).toThrow();
  });

  it("distinguishes a completed receipt from a resumable physical failure", () => {
    expect(parseMacroTaskDeletionResult(confirmed, receipt)).toEqual(receipt);
    const partial = { ...receipt, state: "deleting", deletedAt: null, objectKind: "native-session",
      reason: { code: "macro-task-deletion.physical-failed", params: { errorCode: "IO_ERROR" } } };
    expect(parseMacroTaskDeletionResult(confirmed, partial)).toEqual(partial);
    const skipped = [
      { kind: "ref", path: "refs/synthetic/retained", workspaceId: "ws-fixture", reason: { code: "macro-task-deletion.ref-changed", params: {} } },
      { kind: "allocation", path: "/synthetic/allocation", workspaceId: "ws-changed", reason: { code: "macro-task-deletion.repository-changed", params: {} } },
      { kind: "ref", path: "/synthetic/missing-repository/.git", workspaceId: "ws-missing", reason: { code: "macro-task-deletion.repository-missing", params: {} } },
      { objectKind: "private-root", path: "/synthetic/retained", reason: { code: "macro-task-deletion.owner-unproven", params: {} } },
    ];
    expect(parseMacroTaskDeletionResult(confirmed, { ...receipt, duplicate: true, skipped })).toEqual({ ...receipt, duplicate: true, skipped });
  });

  it("never accepts another scope or an incomplete acknowledgement as deletion", () => {
    for (const value of [null, { ...receipt, macroTaskId: "macro-other" }, { ...receipt, microTaskCount: 4 },
      { ...receipt, deletedAt: null }, { ...receipt, deletedAt: "yesterday" }, { ...receipt, state: "deleting" },
      { ...receipt, duplicate: undefined }, { ...receipt, reasonCode: "physical-failed" },
      { ...receipt, skipped: [{}] },
    ]) expect(() => parseMacroTaskDeletionResult(confirmed, value)).toThrow();
  });
});
