import { useCallback, useEffect, useLayoutEffect, useState } from "react";

export type Theme = "light" | "dark";
/** The user's display choice; `system` follows the operating system setting. */
export type ThemeChoice = Theme | "system";

/** The only value this console persists: a display preference, never a credential. */
export const THEME_STORAGE_KEY = "hey-my-buddy.console.theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

/**
 * Reads the stored display choice. Storage can be unavailable (private mode,
 * disabled cookies) or hold an unrelated value; both fall back to the light
 * theme instead of failing the page.
 */
export function readStoredChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "dark" || stored === "system" ? stored : "light";
  } catch {
    return "light";
  }
}

export function storeChoice(choice: ThemeChoice): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    // The choice still applies for this page; it just cannot be remembered.
  }
}

/** The operating system's scheme; an environment without media queries is light. */
export function systemTheme(): Theme {
  try {
    return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

export function resolveTheme(choice: ThemeChoice): Theme {
  return choice === "system" ? systemTheme() : choice;
}

/** Resolved theme of the stored choice. */
export function readStoredTheme(): Theme {
  return resolveTheme(readStoredChoice());
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
  const [choice, setChoiceState] = useState<ThemeChoice>(readStoredChoice);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const theme = choice === "system" ? system : choice;
  // A layout effect applies the theme in the same commit, before the browser
  // paints and before any caller can observe an unthemed document.
  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);
  // Following the system tracks its changes while the page stays open.
  useEffect(() => {
    if (choice !== "system" || typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(DARK_QUERY);
    const changed = () => setSystem(query.matches ? "dark" : "light");
    changed();
    // Older engines ship only the deprecated MediaQueryList.addListener pair;
    // preferring addEventListener when present keeps modern behavior first.
    if (typeof query.addEventListener !== "function") {
      const legacy = query as MediaQueryList & {
        addListener?: (listener: (event: MediaQueryListEvent) => void) => void;
        removeListener?: (listener: (event: MediaQueryListEvent) => void) => void;
      };
      legacy.addListener?.(changed);
      return () => legacy.removeListener?.(changed);
    }
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, [choice]);
  const setChoice = useCallback((next: ThemeChoice) => {
    storeChoice(next);
    setChoiceState(next);
  }, []);
  return { choice, theme, setChoice };
}
