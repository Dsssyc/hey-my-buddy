/** A buddy as far as it is recorded: any part may be missing. */
type Named = { harness?: string | null; provider?: string | null; model?: string | null; effort?: string | null };

/** Model and effort: the part of a buddy's name that a line can hold. */
export function modelEffort(buddy: Named | null | undefined): string | null {
  if (!buddy || (!buddy.model && !buddy.effort)) return null;
  return [buddy.model, buddy.effort].filter(Boolean).join(" / ");
}

/** A buddy's four parts, `harness / provider / model / effort`, as far as they are recorded. */
export function quad(buddy: Named | null | undefined): string | null {
  const parts = buddy ? [buddy.harness, buddy.provider, buddy.model, buddy.effort].filter(Boolean) : [];
  return parts.length ? parts.join(" / ") : null;
}
