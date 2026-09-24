import { createContext, useContext, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";

type Values = Record<string, unknown>;
const Drafts = createContext<{ values: Values; setValues: Dispatch<SetStateAction<Values>> } | null>(null);
/** Unsaved inputs stay in memory, scoped to the exact record; no credentials are stored. */
export function RecordDrafts({ children }: { children: ReactNode }) {
  const [values, setValues] = useState<Values>({});
  return <Drafts.Provider value={{ values, setValues }}>{children}</Drafts.Provider>;
}
export function useRecordDraft<T>(record: string, field: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const storage = useContext(Drafts);
  const [local, setLocal] = useState(initial);
  if (!storage) return [local, setLocal];
  const key = JSON.stringify([record, field]);
  const value = Object.hasOwn(storage.values, key) ? storage.values[key] as T : initial;
  const set: Dispatch<SetStateAction<T>> = update => storage.setValues(all => {
    const before = Object.hasOwn(all, key) ? all[key] as T : initial;
    return { ...all, [key]: typeof update === "function" ? (update as (v: T) => T)(before) : update };
  });
  return [value, set];
}
