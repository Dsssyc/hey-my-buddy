import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ConsoleApi } from "./api";
import { createAuthorityLatch } from "./console-session";
import { macroTaskSummary } from "./macro-task-fixtures";
import { parseWorkReply, useMacroTaskControl, workFeedback } from "./macro-task-control";
import type { WorkAction } from "./macro-task-types";
import { setLanguageChoice } from "./i18n";
import type { Snapshot } from "./types";

const snapshot = { csrfToken: "csrf", consoleSession: { id: "session" } } as Snapshot;
const summary = macroTaskSummary();
const receipt = (id = summary.macroTaskId) => ({ macroTaskId: id, duplicate: false });
const apiFor = (command: ReturnType<typeof vi.fn>) => ({ command } as unknown as ConsoleApi);
afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("macro task control receipts and identities", () => {
  it.each(["pause", "resume", "end"] as const)("replays an unknown %s with the same request, without treating a failed refresh as command failure", async action => {
    const command = vi.fn().mockRejectedValueOnce(new ApiError("NETWORK", "lost reply")).mockResolvedValue(receipt());
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => { throw new Error("read failed"); }));
    await act(async () => result.current.run(summary, action));
    const unknown = result.current.entries.get(summary.macroTaskId)![action]!;
    expect(unknown.phase).toBe("unknown");
    expect(workFeedback(unknown)).toContain("结果未知");
    await act(async () => result.current.run(summary, action));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
    expect(command.mock.calls[0]).toEqual([`macro_task_${action}`, { macroTaskId: summary.macroTaskId, commandId: expect.any(String) }, "csrf"]);
    const acknowledged = result.current.entries.get(summary.macroTaskId)![action]!;
    expect(acknowledged.phase).toBe("acknowledged");
    expect(workFeedback(acknowledged)).toBeNull();
  });

  it("keeps the unknown notice while its replay is pending", async () => {
    let finish!: (value: unknown) => void;
    const command = vi.fn().mockRejectedValueOnce(new ApiError("NETWORK", "lost reply"))
      .mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    await act(async () => result.current.run(summary, "end"));
    act(() => result.current.run(summary, "end"));
    expect(result.current.busy(summary.macroTaskId)).toBe(true);
    expect(workFeedback(result.current.entries.get(summary.macroTaskId)!.end!)).toContain("结果未知");
    act(() => result.current.run(summary, "end"));
    expect(command).toHaveBeenCalledTimes(2);
    await act(async () => finish({ ...receipt(), duplicate: true }));
    expect(result.current.busy(summary.macroTaskId)).toBe(false);
    expect(workFeedback(result.current.entries.get(summary.macroTaskId)!.end!)).toBeNull();
  });

  it("retains a pause identity across resume and a refused replay, and translates the one-line notice", async () => {
    const command = vi.fn().mockRejectedValueOnce(new ApiError("NETWORK", "lost reply"))
      .mockResolvedValueOnce(receipt()).mockRejectedValueOnce(new ApiError("CONSOLE_SESSION_EXPIRED", "expired"));
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

  it("keeps pending commands independent across units and ignores repeat clicks", async () => {
    const resolvers: ((value: unknown) => void)[] = [];
    const command = vi.fn().mockImplementation(() => new Promise(resolve => resolvers.push(resolve)));
    const api = apiFor(command), other = macroTaskSummary({ macroTaskId: "macro-2" });
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    act(() => { result.current.run(summary, "pause"); result.current.run(summary, "pause"); result.current.run(other, "pause"); });
    expect(command).toHaveBeenCalledTimes(2);
    expect(result.current.busy(summary.macroTaskId)).toBe(true);
    await act(async () => { resolvers[0]!(receipt()); resolvers[1]!(receipt(other.macroTaskId)); });
    expect(result.current.busy(other.macroTaskId)).toBe(false);
  });

  it("retains the end confirmation while busy and closes it when the command settles", async () => {
    let settle!: (value: unknown) => void;
    const command = vi.fn().mockImplementation(() => new Promise(resolve => { settle = resolve; }));
    const api = apiFor(command), refresh = vi.fn().mockResolvedValue(null);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, refresh));
    act(() => result.current.requestEnd(summary));
    act(() => { result.current.confirmEnd(); result.current.confirmEnd(); result.current.cancelConfirm(); });
    expect(result.current.confirming).toBe(summary);
    expect(result.current.busy(summary.macroTaskId)).toBe(true);
    expect(command).toHaveBeenCalledTimes(1);
    await act(async () => settle(receipt()));
    expect(result.current.confirming).toBeNull();
    expect(refresh).toHaveBeenCalledTimes(1);
    act(() => result.current.requestEnd(summary));
    await act(async () => { result.current.confirmEnd(); settle(receipt()); });
    expect(command.mock.calls[0]![1].commandId).not.toBe(command.mock.calls[1]![1].commandId);
  });

  it("closes the dialog on refusal and displays only its reason", async () => {
    const command = vi.fn().mockRejectedValue(new ApiError("REVISION_CONFLICT", "raw"));
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    act(() => result.current.requestEnd(summary));
    await act(async () => result.current.confirmEnd());
    expect(result.current.confirming).toBeNull();
    expect(workFeedback(result.current.entries.get(summary.macroTaskId)!.end!)).toBe("记录已更新；请刷新核对后重试。");
  });

  it("does not send after authority is lost at confirmation and mints a new identity after login", async () => {
    const command = vi.fn().mockResolvedValue(receipt());
    const api = apiFor(command), authority = createAuthorityLatch();
    const { result, rerender } = renderHook(({ snap }) => useMacroTaskControl(api, snap, true, authority, async () => {}), { initialProps: { snap: snapshot } });
    act(() => result.current.requestEnd(summary));
    authority.lose("session"); rerender({ snap: snapshot });
    act(() => result.current.confirmEnd());
    expect(command).not.toHaveBeenCalled();
    expect(result.current.confirming).toBeNull();
    expect(result.current.entries.get(summary.macroTaskId)?.end?.commandId).toBe("");
    rerender({ snap: { ...snapshot, consoleSession: { id: "new-session" } } });
    act(() => result.current.requestEnd(summary));
    await act(async () => result.current.confirmEnd());
    expect(command.mock.calls[0]![1].commandId).not.toBe("");
  });

  it("accepts only the common receipt and leaves malformed replies unknown and replayable", async () => {
    expect(parseWorkReply(summary.macroTaskId, receipt())).toEqual(receipt());
    for (const value of [null, {}, { ...receipt(), macroTaskId: "wrong" }, { ...receipt(), duplicate: 1 },
      { ...receipt(), pausedAt: "2026-11-12T00:00:00Z" }, { ...receipt(), results: [] }]) {
      expect(() => parseWorkReply(summary.macroTaskId, value)).toThrow(ApiError);
    }
    const command = vi.fn().mockResolvedValueOnce({ ...receipt(), results: [] }).mockResolvedValueOnce(receipt());
    const api = apiFor(command);
    const { result } = renderHook(() => useMacroTaskControl(api, snapshot, true, undefined, async () => {}));
    await act(async () => result.current.run(summary, "end" as WorkAction));
    expect(result.current.entries.get(summary.macroTaskId)?.end?.phase).toBe("unknown");
    await act(async () => result.current.run(summary, "end"));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
  });
});
