import type { Buddy } from "./types";

/** Fields needed to render a buddy name; callers may pass a whole record. */
export type NamedBuddy = Pick<Buddy, "label" | "model" | "effort">;

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

/**
 * Catalog proposals (src/hey_my_buddy/blackboard/catalog/catalog.py) already carry " · <effort>" (U+00B7)
 * inside the label, so drop that exact tail when the effort is rendered next to
 * the name. Custom labels, names that merely contain "off", and labels whose
 * tail does not match stay intact.
 */
export function buddyName(buddy: NamedBuddy): string {
  const label = (buddy.label || "").trim();
  const effort = (buddy.effort || "").trim();
  if (label && effort) {
    const suffix = ` · ${effort}`;
    if (label.endsWith(suffix)) {
      const stripped = label.slice(0, -suffix.length).trim();
      if (stripped) return stripped;
    }
  }
  return label || buddy.model;
}

/** One consistent "name · effort" form for selects, lists and summaries. */
export function buddyTitle(buddy: NamedBuddy): string {
  const name = buddyName(buddy);
  const effort = effortText(buddy.effort);
  return effort ? `${name} · ${effort}` : name;
}

/** buddyTitle for a buddy that may be absent from the current snapshot. */
export function buddyTitleOr(
  buddy: NamedBuddy | undefined,
  fallback: string,
): string {
  return buddy ? buddyTitle(buddy) : fallback;
}
