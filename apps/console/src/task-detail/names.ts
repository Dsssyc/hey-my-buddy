import type { TimelineBuddy } from "../macro-task-types";
import type { Buddy } from "./types";

type Named = Buddy | TimelineBuddy;

/** Model and effort: the part of a buddy's name that a line can hold. */
export function modelEffort(buddy: { model: string | null; effort: string | null } | null | undefined): string | null {
  if (!buddy || (!buddy.model && !buddy.effort)) return null;
  return [buddy.model, buddy.effort].filter(Boolean).join(" / ");
}

/** A buddy's four parts, `harness / provider / model / effort`, as far as they are recorded. */
export function quad(buddy: Named | null | undefined): string | null {
  const parts = buddy ? [buddy.harness, buddy.provider, buddy.model, buddy.effort].filter(Boolean) : [];
  return parts.length ? parts.join(" / ") : null;
}
