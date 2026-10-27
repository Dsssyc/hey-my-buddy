/** Program-owned explanation. Authored notes are separate content. */
export type Reason = { code: string; params: Record<string, unknown> };

/** Only the public envelope is checked here; the service owns parameter validation. */
export function isReason(value: unknown): value is Reason {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return typeof row.code === "string" && !!row.params && typeof row.params === "object" && !Array.isArray(row.params);
}
