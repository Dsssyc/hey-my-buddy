import i18n from "i18next";
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, createApi, errorText, parseReminderSettings, parseRuntimeVersion, type ConsoleApi } from "../api";
import { useHarnessActions } from "../buddies/use-harness-actions";
import { TooltipProvider } from "../components/ui/tooltip";
import { ReminderSection } from "../settings/ReminderSection";
import { client } from "../task-detail/task-detail.fixtures";
import { parseTask } from "../task-detail/parse";
import { useTaskDetail } from "../task-detail/use-task-detail";
import type { Snapshot } from "../types";
import { useConsole } from "../use-console";
import { macroTaskSummary } from "../macro-task-fixtures";
import { stopStatus, useMacroTaskStop } from "../macro-task-stop";
import { setLanguageChoice } from "./index";
import { useCopyState } from "./use-copy-state";

// Messages remain visible throughout each switch; reads and writes must not restart.
afterEach(() => { cleanup(); setLanguageChoice("browser"); vi.restoreAllMocks(); });

it("resolves retained internal API failures in the new language and preserves server messages", async () => {
  const network = createApi("", vi.fn().mockRejectedValue(new TypeError("offline")));
  const fallback = createApi("", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false }), { status: 403 })));
  const raw = createApi("", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false,
    error: { code: "CUSTOM", message: "服务端原文 / server text" } }), { status: 400 })));
  const failures = await Promise.all([network.snapshot().catch(error => error), fallback.snapshot().catch(error => error), raw.snapshot().catch(error => error)]);
  let malformed: unknown;
  try { parseRuntimeVersion({}); } catch (error) { malformed = error; }
  expect(errorText(failures[0])).toBe(i18n.t("common:error.network"));
  act(() => setLanguageChoice("en"));
  expect(failures[0].message).toBe("Cannot reach the local blackboard; refresh and retry.");
  expect(failures[1].message).toBe("The request did not succeed.");
  expect(errorText(malformed)).toBe("The version information is not recognised; check the service version.");
  expect(errorText(failures[2])).toBe("服务端原文 / server text");
});

it("updates a failed snapshot read without restarting its request or clearing feedback", async () => {
  const api = createApi("", vi.fn().mockRejectedValue(new TypeError("offline")));
  const snapshot = vi.spyOn(api, "snapshot");
  const { result } = renderHook(() => useConsole(api));
  await waitFor(() => expect(result.current.error).toBe(i18n.t("common:error.network")));
  act(() => setLanguageChoice("en"));
  expect(result.current.error).toBe("Cannot reach the local blackboard; refresh and retry.");
  expect(snapshot).toHaveBeenCalledTimes(1);
  expect(result.current.snapshot).toBeNull();
});

it("updates a malformed save error while preserving the reminder draft and input identity", async () => {
  const command = vi.fn().mockResolvedValue({});
  const settings = { silenceSeconds: 900, revision: 1, updatedAt: "2026-10-15T00:00:00Z" };
  render(<TooltipProvider><ReminderSection api={{ command } as unknown as ConsoleApi} csrfToken="csrf"
    settings={settings} refresh={vi.fn()} unavailable={false} /></TooltipProvider>);
  const input = screen.getByRole("spinbutton", { name: "静默提醒阈值（分钟）" }) as HTMLInputElement;
  const user = userEvent.setup();
  await user.clear(input); await user.type(input, "20");
  await user.click(screen.getByRole("button", { name: "保存" }));
  expect((await screen.findByRole("alert")).textContent).toContain(i18n.t("common:error.reminders"));
  act(() => setLanguageChoice("en"));
  expect(screen.getByRole("alert").textContent).toContain("The reminder settings are not recognised; refresh and retry.");
  expect(screen.getByRole("spinbutton")).toBe(input);
  expect(input.value).toBe("20");
  expect(command).toHaveBeenCalledTimes(1);
});

it("updates a retained task-detail parser error without starting another read", async () => {
  const reads = client();
  vi.spyOn(reads, "task").mockImplementation(async () => ({ value: parseTask({}, "m1"), verifiedAtMs: null }));
  const { result } = renderHook(() => useTaskDetail(reads, "m1", true, []));
  await waitFor(() => expect(result.current.error).not.toBe(""));
  const chinese = result.current.error;
  act(() => setLanguageChoice("en"));
  expect(result.current.error).not.toBe(chinese);
  expect(result.current.error).toBe("The reply is incomplete (task.runId); check the service version.");
  expect(result.current.error).not.toMatch(/[\u4e00-\u9fff]/);
  expect(reads.task).toHaveBeenCalledTimes(1);
});

it("updates a completed harness check notice including its status without repeating the action", async () => {
  const harnessRefresh = vi.fn().mockResolvedValue([{ harness: "codex", status: "ready" }]);
  const api = { harnessRefresh } as unknown as ConsoleApi;
  const snapshot = { csrfToken: "csrf", harnesses: [] } as unknown as Snapshot;
  const { result } = renderHook(() => useHarnessActions({ snapshot, api, refresh: async () => null, mutationsAvailable: true, sessionWritable: true }));
  await act(async () => result.current.run({ kind: "check", harness: "codex" }));
  const chinese = result.current.note;
  expect(chinese).toContain("Codex");
  act(() => setLanguageChoice("en"));
  expect(result.current.note).not.toBe(chinese);
  expect(result.current.note).toContain("Codex");
  expect(result.current.note).not.toMatch(/[\u4e00-\u9fff]/);
  expect(harnessRefresh).toHaveBeenCalledTimes(1);
});

it("keeps literal errors literal and translates standard refusal codes after display", () => {
  const failure = new ApiError("REVISION_CONFLICT", "Server detail");
  const { result } = renderHook(() => useCopyState());
  act(() => result.current[1](() => errorText(failure)));
  act(() => setLanguageChoice("en"));
  expect(result.current[0]).toBe("The record was updated; refresh, check it and try again.");
  act(() => result.current[1](() => errorText(new Error("原始消息"))));
  act(() => setLanguageChoice("zh"));
  expect(result.current[0]).toBe("原始消息");
});

it("updates an already refused stop while preserving its request identity", async () => {
  const command = vi.fn().mockRejectedValue(new ApiError("REVISION_CONFLICT", "Server detail"));
  const api = { command } as unknown as ConsoleApi;
  const snapshot = { csrfToken: "csrf", consoleSession: { id: "session", canWrite: true, reason: null } } as Snapshot;
  const summary = macroTaskSummary();
  const { result } = renderHook(() => useMacroTaskStop(api, snapshot, true, undefined, async () => {}));
  await act(async () => result.current.confirmStop(summary));
  const retained = result.current.entries.get(summary.macroTaskId)!;
  expect(retained.phase).toBe("refused");
  act(() => setLanguageChoice("en"));
  expect(stopStatus(result.current.entries.get(summary.macroTaskId), null)?.detail)
    .toBe("The record was updated; refresh, check it and try again.");
  expect(result.current.entries.get(summary.macroTaskId)?.commandId).toBe(retained.commandId);
  expect(command).toHaveBeenCalledTimes(1);
});
