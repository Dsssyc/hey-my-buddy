import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ConsoleApi } from "./api";
import { createAuthorityLatch } from "./console-session";
import { macroTaskSummary } from "./macro-task-fixtures";
import { canRetryEnd, parseWorkReply, pauseLabel, useMacroTaskControl, workFeedback } from "./macro-task-control";
import type { WorkAction } from "./macro-task-types";
import { setLanguageChoice } from "./i18n";
import type { Snapshot } from "./types";

const snapshot = { csrfToken: "csrf", consoleSession: { id: "session" } } as Snapshot;
const at = "2026-11-11T09:00:00Z";
const summary = macroTaskSummary();
const receipt = (action: WorkAction, id = summary.macroTaskId) => action === "pause" ? { macroTaskId: id, pausedAt: at, duplicate: false }
  : action === "resume" ? { macroTaskId: id, duplicate: false }
  : { macroTaskId: id, duplicate: false, results: [{ runId: "r4", concluded: true, state: "concluded", reclaim: {} }] };
const apiFor = (command: ReturnType<typeof vi.fn>) => ({ command } as unknown as ConsoleApi);
afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("macro task control identities and receipts", () => {
  it.each(["pause", "resume", "end"] as const)("retries an unknown %s with the same request and keeps acknowledgement if refresh fails", async action => {
    const command = vi.fn().mockRejectedValueOnce(new ApiError("NETWORK", "lost reply")).mockResolvedValue(receipt(action));
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => { throw new Error("read failed"); }));
    await act(async () => result.current.run(summary, action));
    expect(result.current.entries.get(summary.macroTaskId)?.[action]?.phase).toBe("unknown");
    expect(workFeedback(result.current.entries.get(summary.macroTaskId)![action]!)).toContain("结果未知");
    await act(async () => result.current.run(summary, action));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
    expect(command.mock.calls[0]).toEqual([`macro_task_${action}`, { macroTaskId: summary.macroTaskId, commandId: expect.any(String) }, "csrf"]);
    expect(result.current.entries.get(summary.macroTaskId)?.[action]?.phase).toBe("acknowledged");
  });

  it("retains a pause identity across a resume and a refused replay, then resolves it in the new language", async () => {
    const command = vi.fn().mockRejectedValueOnce(new ApiError("NETWORK", "lost reply"))
      .mockResolvedValueOnce(receipt("resume")).mockRejectedValueOnce(new ApiError("CONSOLE_SESSION_EXPIRED", "expired"));
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    await act(async () => result.current.run(summary, "pause"));
    const id = result.current.entries.get(summary.macroTaskId)!.pause!.commandId;
    await act(async () => result.current.run(summary, "resume"));
    await act(async () => result.current.run(summary, "pause"));
    const retained = result.current.entries.get(summary.macroTaskId)!.pause!;
    expect(retained.phase).toBe("unknown");
    expect(retained.commandId).toBe(id);
    act(() => setLanguageChoice("en"));
    expect(workFeedback(retained)).toBe("The pause result is unknown and may have taken effect; retry the same request or refresh to check.");
    expect(command).toHaveBeenCalledTimes(3);
  });

  it("keeps pending commands independent across macro tasks and ignores a duplicate click", async () => {
    const resolvers: ((value: unknown) => void)[] = [];
    const command = vi.fn().mockImplementation(() => new Promise(resolve => resolvers.push(resolve)));
    const api = apiFor(command);
    const other = macroTaskSummary({ macroTaskId: "macro-2" });
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    act(() => { result.current.run(summary, "pause"); result.current.run(summary, "pause"); result.current.run(other, "pause"); });
    expect(command).toHaveBeenCalledTimes(2);
    expect(result.current.busy(summary.macroTaskId)).toBe(true);
    await act(async () => { resolvers[0]!(receipt("pause")); resolvers[1]!(receipt("pause", other.macroTaskId)); });
    expect(result.current.busy(other.macroTaskId)).toBe(false);
  });

  it("does not dispatch after the writer is lost at confirmation and mints a fresh identity when it returns", async () => {
    const command = vi.fn().mockResolvedValue(receipt("end"));
    const api = apiFor(command), authority = createAuthorityLatch();
    const { result, rerender } = renderHook(({ snap }) => useMacroTaskControl(api, snap, true, authority, async () => {}), { initialProps: { snap: snapshot } });
    act(() => result.current.requestEnd(summary));
    authority.lose("session"); rerender({ snap: snapshot });
    act(() => result.current.confirmEnd());
    expect(command).not.toHaveBeenCalled();
    expect(result.current.entries.get(summary.macroTaskId)?.end?.commandId).toBe("");
    rerender({ snap: { ...snapshot, consoleSession: { id: "new-session" } } });
    await act(async () => result.current.run(summary, "end"));
    expect(command.mock.calls[0]![1].commandId).not.toBe("");
  });

  it("retries only unconfirmed end results with the same receipt identity", async () => {
    const partial = { macroTaskId: summary.macroTaskId, duplicate: false, results: [
      { runId: "a", state: "concluded", concluded: true, reclaim: {} },
      { runId: "b", concluded: false, error: { code: "SHUTDOWN_UNCONFIRMED", unconfirmedCount: 1 } },
    ] };
    const command = vi.fn().mockResolvedValueOnce(partial).mockResolvedValueOnce({ ...partial, duplicate: true });
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    await act(async () => result.current.run(summary, "end"));
    const entry = result.current.entries.get(summary.macroTaskId)!.end!;
    expect(workFeedback(entry)).toBe("已结束 1 个微任务 · 1 个微任务的停止未确认，没有写结论，可以重试");
    expect(canRetryEnd(entry)).toBe(true);
    await act(async () => result.current.run(summary, "end"));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
  });

  it("leaves a malformed acknowledgement unknown and replayable", async () => {
    const command = vi.fn().mockResolvedValueOnce({ ...receipt("pause"), macroTaskId: "wrong" }).mockResolvedValueOnce(receipt("pause"));
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    await act(async () => result.current.run(summary, "pause"));
    expect(result.current.entries.get(summary.macroTaskId)?.pause?.phase).toBe("unknown");
    await act(async () => result.current.run(summary, "pause"));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
  });

  it("rejects retired and contradictory reply forms at the boundary", () => {
    expect(() => parseWorkReply("pause", summary.macroTaskId, { macroTaskId: summary.macroTaskId, paused: { pausedAt: at }, duplicate: false })).toThrow(ApiError);
    expect(() => parseWorkReply("resume", summary.macroTaskId, { ...receipt("resume"), paused: null })).toThrow(ApiError);
    for (const results of [
      [{ runId: "r1", concluded: true, state: "cancelled", reclaim: {} }],
      [{ runId: "r1", concluded: false, error: { code: "OTHER" } }],
      [{ runId: "r1", concluded: true, state: "concluded", reclaim: {} }, { runId: "r1", concluded: false, error: { code: "SHUTDOWN_UNCONFIRMED" } }],
    ]) expect(() => parseWorkReply("end", summary.macroTaskId, { macroTaskId: summary.macroTaskId, duplicate: false, results })).toThrow(ApiError);
  });

  it("reads pause evidence and time from view facts in both languages", () => {
    expect(pauseLabel(null)).toBeNull();
    for (const [lang, pausing, paused] of [["zh", "暂停中", "已暂停 · "], ["en", "Pausing", "Paused · "]] as const) {
      act(() => setLanguageChoice(lang));
      expect(pauseLabel({ pausedAt: at, shutdownConfirmed: false })).toBe(pausing);
      expect(pauseLabel({ pausedAt: at, shutdownConfirmed: true })).toMatch(new RegExp(`^${paused}\\d{2}:\\d{2}$`));
    }
  });
});
