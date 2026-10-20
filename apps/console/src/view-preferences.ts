import { useCallback, useState } from "react";

/** Browser display habits only; no credentials or blackboard writes. */
export const VIEW_PREFERENCE_PREFIX = "hey-my-buddy.console.view.";

function readStoredPreference(name: string): string | null {
  try { return window.localStorage.getItem(VIEW_PREFERENCE_PREFIX + name); }
  catch { return null; }
}

export function readViewPreference(name: string): boolean {
  return readStoredPreference(name) === "true";
}

export function readNumberViewPreference(name: string, defaultValue: number): number {
  const stored = readStoredPreference(name);
  if (stored === null || stored.trim() === "") return defaultValue;
  const value = Number(stored);
  return Number.isFinite(value) ? value : defaultValue;
}

export function storeViewPreference(name: string, value: boolean | number): void {
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

export function useNumberViewPreference(name: string, defaultValue: number) {
  const [value, setValue] = useState(() => readNumberViewPreference(name, defaultValue));
  const setPreference = useCallback((next: number) => {
    storeViewPreference(name, next);
    setValue(next);
  }, [name]);
  return [value, setPreference] as const;
}
