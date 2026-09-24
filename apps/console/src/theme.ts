import { useCallback, useLayoutEffect, useState } from "react";

export type Theme = "light" | "dark";

/** The only value this console persists: a display preference, never a credential. */
export const THEME_STORAGE_KEY = "hey-my-buddy.console.theme";

/**
 * Reads the stored display preference. Storage can be unavailable (private mode,
 * disabled cookies) or hold an unrelated value; both fall back to the light theme
 * instead of failing the page.
 */
export function readStoredTheme(): Theme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

export function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The choice still applies for this page; it just cannot be remembered.
  }
}

/** Applies the theme to the document so CSS tokens and color-scheme switch. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export function applyStoredTheme(): Theme {
  const theme = readStoredTheme();
  applyTheme(theme);
  return theme;
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  // A layout effect applies the theme in the same commit, before the browser
  // paints and before any caller can observe an unthemed document.
  useLayoutEffect(() => {
    applyTheme(theme);
    storeTheme(theme);
  }, [theme]);
  const toggle = useCallback(
    () => setTheme((current) => (current === "dark" ? "light" : "dark")),
    [],
  );
  return { theme, setTheme, toggle };
}
