import type { Profile } from "./types";
import { formatDate } from "./ui";

/** Fields needed to render a profile name; callers may pass a whole record. */
export type NamedProfile = Pick<Profile, "label" | "model" | "effort">;

/**
 * The board's recorded pending fact for one profile (ADR-027 §6): the model is
 * missing from a confirmed catalog reading and is awaiting confirmation. Only
 * the field itself is read — a board or fixture without it carries no pending
 * fact, and the console never computes the confirmation deadline itself.
 */
export function catalogPending(
  profile: Pick<Profile, "catalogStatus">,
): boolean {
  return profile.catalogStatus === "pending";
}

/**
 * One visible pending mark: `待确认` plus the recorded first-absence time when
 * the board supplied one. An unrecorded time stays honest as plain `待确认`
 * rather than an invented date. Callers gate on `catalogPending` (or the
 * family's aggregated time) and pass the recorded `pendingSince`.
 */
export function pendingMark(profile: Pick<Profile, "pendingSince">): string {
  const since = formatDate(profile.pendingSince);
  return since === "未记录" ? "待确认" : `待确认 · 自 ${since}`;
}

/**
 * One profile's catalog-state row text. An explicit board `catalogStatus` is
 * the state and wins as recorded; recorded availability only supplies the
 * default for fixtures from before ADR-027, because `available` also folds in
 * harness health and can never override an explicit catalog state.
 */
export function catalogStateText(
  profile: Pick<Profile, "available" | "catalogStatus" | "pendingSince">,
): string {
  if (profile.catalogStatus === "pending") return pendingMark(profile);
  if (profile.catalogStatus === "available") return "可用";
  if (profile.catalogStatus === "unavailable") return "不可用";
  return profile.available ? "可用" : "不可用";
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
export function profileName(profile: NamedProfile): string {
  const label = (profile.label || "").trim();
  const effort = (profile.effort || "").trim();
  if (label && effort) {
    const suffix = ` · ${effort}`;
    if (label.endsWith(suffix)) {
      const stripped = label.slice(0, -suffix.length).trim();
      if (stripped) return stripped;
    }
  }
  return label || profile.model;
}

/** One consistent "name · effort" form for selects, lists and summaries. */
export function profileTitle(profile: NamedProfile): string {
  const name = profileName(profile);
  const effort = effortText(profile.effort);
  return effort ? `${name} · ${effort}` : name;
}

/** profileTitle for a profile that may be absent from the current snapshot. */
export function profileTitleOr(
  profile: NamedProfile | undefined,
  fallback: string,
): string {
  return profile ? profileTitle(profile) : fallback;
}
