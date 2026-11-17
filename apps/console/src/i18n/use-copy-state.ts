import { useCallback, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** Retain message arguments and resolve existing feedback in the current language. */
export function useCopyState<T extends ReactNode = string>() {
  useTranslation("common");
  const [copy, setCopy] = useState<T | (() => T) | "">("");
  const set = useCallback((next: T | (() => T)) => setCopy(() => next), []);
  return [typeof copy === "function" ? copy() : copy, set] as const;
}
