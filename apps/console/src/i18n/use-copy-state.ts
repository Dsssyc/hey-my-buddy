import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

/** Retain message arguments and resolve existing feedback in the current language. */
export function useCopyState() {
  useTranslation("common");
  const [copy, setCopy] = useState<string | (() => string)>("");
  const set = useCallback((next: string | (() => string)) => setCopy(() => next), []);
  return [typeof copy === "function" ? copy() : copy, set] as const;
}
