import type { Buddy } from "./types";

/**
 * The board's recorded pending fact for one buddy (ADR-027 §6): the model is
 * missing from a confirmed catalog reading and is awaiting confirmation. Only
 * the field itself is read — a board or fixture without it carries no pending
 * fact, and the console never computes the confirmation deadline itself.
 */
export function catalogPending(
  buddy: Pick<Buddy, "catalogStatus">,
): boolean {
  return buddy.catalogStatus === "pending";
}

/**
 * User-facing wording for a thinking effort. Every native value — including
 * `off` — is shown as recorded, exactly like `low`, `high` and `max`; only
 * surrounding whitespace is dropped.
 */
export function effortText(effort: string | null | undefined): string {
  return (effort ?? "").trim();
}
