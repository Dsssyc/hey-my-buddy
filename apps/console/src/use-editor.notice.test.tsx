import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useEditor, SUCCESS_NOTICE_MS, NOTICE_FADE_MS } from "./use-editor";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";

// The fading decision reads the reader's motion preference at the moment the
// note leaves; jsdom has no matchMedia of its own.
beforeEach(() => vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query })));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

const snapshot = { eventCursor: 8, csrfToken: "csrf", consoleSession: { id: "session" },
  buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], configuration: null,
  decisions: [], tasks: { pendingCount: 0 }, capabilities: {} } as Snapshot;

/** The editor on a writable page, with the clock in the test's hands. */
function editor(reducedMotion = false) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: reducedMotion && query.includes("prefers-reduced-motion"), media: query }));
  vi.useFakeTimers();
  return renderHook(() => useEditor({} as ConsoleApi, snapshot, async () => snapshot, true, ""));
}

describe("success notes leave on their own", () => {
  it("shows one for about four seconds, then fades it out", async () => {
    const { result } = editor();
    await act(async () => result.current.discard());
    expect(result.current.notice).toEqual({ text: "草稿已放弃", fading: false });
    act(() => vi.advanceTimersByTime(SUCCESS_NOTICE_MS - 1));
    expect(result.current.notice!.fading).toBe(false);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.notice).toEqual({ text: "草稿已放弃", fading: true });
    act(() => vi.advanceTimersByTime(NOTICE_FADE_MS - 1));
    expect(result.current.notice).not.toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.notice).toBeNull();
  });

  it("disappears at once when the reader asks for reduced motion", async () => {
    const { result } = editor(true);
    await act(async () => result.current.discard());
    act(() => vi.advanceTimersByTime(SUCCESS_NOTICE_MS));
    expect(result.current.notice).toBeNull();
  });

  it("takes a showing note back the moment the reader edits again", async () => {
    const { result } = editor();
    await act(async () => result.current.discard());
    act(() => vi.advanceTimersByTime(SUCCESS_NOTICE_MS / 2));
    act(() => result.current.update(draft => draft));
    expect(result.current.notice).toBeNull();
    act(() => vi.advanceTimersByTime(SUCCESS_NOTICE_MS * 2));
    expect(result.current.notice).toBeNull();
  });

  it("keeps a note that asks the reader to check something", async () => {
    const { result } = editor();
    await act(async () => result.current.reloadLatest());
    expect(result.current.notice!.text).toBe("已重新读取设置；请核对后保存。");
    act(() => vi.advanceTimersByTime(SUCCESS_NOTICE_MS * 2));
    expect(result.current.notice!.text).toBe("已重新读取设置；请核对后保存。");
    expect(result.current.notice!.fading).toBe(false);
  });
});
