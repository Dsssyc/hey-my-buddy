import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { readViewPreference, storeViewPreference, useViewPreference, VIEW_PREFERENCE_PREFIX } from "./view-preferences";

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });
it("only restores standard stored booleans; absent and damaged values are unchecked", () => {
  expect(readViewPreference("example")).toBe(false);
  for (const value of ["garbage", "1", "{}", "null", "TRUE", "false"]) {
    localStorage.setItem(VIEW_PREFERENCE_PREFIX + "example", value);
    expect(readViewPreference("example")).toBe(false);
  }
  storeViewPreference("example", true);
  expect(readViewPreference("example")).toBe(true);
  storeViewPreference("example", false);
  expect(readViewPreference("example")).toBe(false);
});
it("does not throw when storage is unavailable and keeps the current view usable", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(() => readViewPreference("example")).not.toThrow();
  expect(() => storeViewPreference("example", true)).not.toThrow();
  const { result } = renderHook(() => useViewPreference("example"));
  expect(result.current[0]).toBe(false);
  act(() => result.current[1](true));
  expect(result.current[0]).toBe(true);
  expect(readViewPreference("example")).toBe(false);
});

it("keeps navigation overrides local while manual choices survive remount", () => {
  let view = renderHook(() => useViewPreference("example"));
  act(() => view.result.current[1](true));
  expect(readViewPreference("example")).toBe(true);
  const writes = vi.spyOn(Storage.prototype, "setItem");
  act(() => view.result.current[2](false));
  expect(view.result.current[0]).toBe(false);
  expect(readViewPreference("example")).toBe(true);
  expect(writes).not.toHaveBeenCalled();
  view.unmount();
  view = renderHook(() => useViewPreference("example"));
  expect(view.result.current[0]).toBe(true);
  act(() => view.result.current[1](false));
  act(() => view.result.current[2](true));
  expect(view.result.current[0]).toBe(true);
  expect(readViewPreference("example")).toBe(false);
  view.unmount();
  view = renderHook(() => useViewPreference("example"));
  expect(view.result.current[0]).toBe(false);
});
