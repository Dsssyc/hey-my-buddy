import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { refreshVisibleReads, useGlobalRefresh } from "./global-refresh";

describe("global refresh", () => {
  it("awaits all visible readers and reports a failed read", async () => {
    const first = vi.fn(async () => {});
    const second = vi.fn(async () => { throw new Error("时间轴读取失败"); });
    const hidden = vi.fn(async () => {});
    const a = renderHook(() => useGlobalRefresh(first, true));
    const b = renderHook(() => useGlobalRefresh(second, true));
    const c = renderHook(() => useGlobalRefresh(hidden, false));
    await expect(refreshVisibleReads()).rejects.toThrow("时间轴读取失败");
    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();
    expect(hidden).not.toHaveBeenCalled();
    a.unmount(); b.unmount(); c.unmount();
    await expect(refreshVisibleReads()).resolves.toBeUndefined();
  });
});
