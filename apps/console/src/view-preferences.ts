import { useCallback, useState } from "react";

/** Browser display habits only; no credentials or blackboard writes. */
export const VIEW_PREFERENCE_PREFIX = "hey-my-buddy.console.view.";

export function readViewPreference(name: string): boolean {
  try { return window.localStorage.getItem(VIEW_PREFERENCE_PREFIX + name) === "true"; }
  catch { return false; }
}

export function storeViewPreference(name: string, value: boolean): void {
  try { window.localStorage.setItem(VIEW_PREFERENCE_PREFIX + name, String(value)); }
  catch { /* The current view still works when storage is unavailable. */ }
}

export function useViewPreference(name: string) {
  const [value, setValue] = useState(() => readViewPreference(name));
  const setPreference = useCallback((next: boolean) => {
    storeViewPreference(name, next);
    setValue(next);
  }, [name]);
  // Navigation may reveal a filtered target without changing the saved habit.
  return [value, setPreference, setValue] as const;
}
