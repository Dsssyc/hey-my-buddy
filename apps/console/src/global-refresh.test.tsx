import { describe, expect, it, vi } from "vitest";
import { waitForRead } from "./global-refresh";

describe("local overlapping read wait", () => {
  it("waits for the existing read to settle without starting another read", async () => {
    vi.useFakeTimers();
    try {
      let pending = true, finished = false;
      const wait = waitForRead(() => pending).then(() => { finished = true; });
      await vi.advanceTimersByTimeAsync(100);
      expect(finished).toBe(false);
      pending = false;
      await vi.advanceTimersByTimeAsync(50);
      await wait;
      expect(finished).toBe(true);
    } finally { vi.useRealTimers(); }
  });
  it("bounds an unresponsive existing read and returns a retryable error", async () => {
    vi.useFakeTimers();
    try {
      const result = expect(waitForRead(() => true)).rejects.toThrow("现有读取尚未完成");
      await vi.advanceTimersByTimeAsync(30000);
      await result;
    } finally { vi.useRealTimers(); }
  });
});
