import type { Profile } from "./types";

/** Fields needed to render a profile name; callers may pass a whole record. */
export type NamedProfile = Pick<Profile, "label" | "model" | "effort">;

/**
 * User-facing wording for a thinking effort. Every native value — including
 * `off` — is shown as recorded, exactly like `low`, `high` and `max`; only
 * surrounding whitespace is dropped.
 */
export function effortText(effort: string | null | undefined): string {
  return (effort ?? "").trim();
}

/**
 * Catalog proposals (src/buddy/catalog.py) already carry " · <effort>" (U+00B7)
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
