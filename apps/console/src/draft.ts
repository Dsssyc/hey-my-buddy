import type {
  Annotation,
  Card,
  Draft,
  Preference,
  Profile,
  Snapshot,
  WriterGrant,
} from "./types";

/** Retained identity rows the current snapshot no longer lists. */
export type HistoryEntries = {
  profiles?: Profile[];
  preferences?: Preference[];
  annotations?: Annotation[];
  cards?: Card[];
  sampleCounts?: Record<string, number>;
};

export function makeDraft(snapshot: Snapshot): Draft {
  return structuredClone({
    tableRevision: snapshot.tableRevision,
    profiles: snapshot.profiles,
    preferences: snapshot.preferences,
    annotations: snapshot.annotations ?? [],
    configuration: snapshot.configuration,
  });
}

export function emptyCard(profileId: string): Card {
  return {
    profileId,
    revision: 0,
    summary: "",
    strengths: [],
    limitations: [],
    risks: [],
    evidenceIds: [],
    updatedAt: null,
  };
}

export function emptyAnnotation(profileId: string): Annotation {
  return { profileId, text: "", revision: 0, updatedAt: null };
}

/** One human opinion per profile; empty text is a deliberate clear, not a delete. */
export function setAnnotation(
  draft: Draft,
  profileId: string,
  text: string,
): Draft {
  const known = draft.annotations.find((a) => a.profileId === profileId);
  const next: Annotation = { ...(known ?? emptyAnnotation(profileId)), text };
  return {
    ...draft,
    annotations: [
      ...draft.annotations.filter((a) => a.profileId !== profileId),
      next,
    ],
  };
}

export function annotationText(
  source: { annotations: Annotation[] },
  profileId: string,
): string {
  return source.annotations.find((a) => a.profileId === profileId)?.text ?? "";
}

function addMissing<T extends { profileId: string }>(current: T[], extra: T[] | undefined): T[] {
  if (!extra?.length) return current;
  const known = new Set(current.map((entry) => entry.profileId));
  const added = extra.filter((entry) => !known.has(entry.profileId) && (known.add(entry.profileId), true));
  return added.length ? [...current, ...added] : current;
}

/**
 * Adds retained history rows the snapshot no longer lists. Live snapshot rows
 * always win; history only fills identities that would otherwise be invisible.
 */
export function withHistory<T extends Pick<Draft, "profiles" | "preferences" | "annotations">>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  if (!entries) return source;
  return {
    ...source,
    profiles: addMissing(source.profiles, entries.profiles),
    preferences: addMissing(source.preferences, entries.preferences),
    annotations: addMissing(source.annotations, entries.annotations),
  };
}

/** Full console view: human draft (or snapshot) plus retained history rows. */
export function historyView<T extends Pick<Draft, "profiles" | "preferences" | "annotations">
  & { cards: Card[]; sampleCounts?: Record<string, number> }>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  const base = withHistory(source, entries);
  if (!entries) return base;
  // Snapshot cards and counts are authoritative for the identities they cover.
  const counts = { ...(entries.sampleCounts ?? {}), ...(source.sampleCounts ?? {}) };
  return { ...base, cards: addMissing(source.cards, entries.cards), sampleCounts: counts };
}

export function setPreference(
  draft: Draft,
  profileId: string,
  mode: Preference["mode"] | "",
  reason = "",
): Draft {
  const preferences = draft.preferences.filter(
    (p) => p.profileId !== profileId && !(mode === "pin" && p.mode === "pin"),
  );
  if (mode) preferences.push({ profileId, mode, reason });
  return { ...draft, preferences };
}

/** The only fields a human may publish; program-owned profile/catalog fields stay out. */
export type ProfileSettingPatch = { profileId: string; enabled: boolean };
export type PreferenceChangePatch = {
  profileId: string;
  mode: Preference["mode"] | null;
  reason: string;
};
export type AnnotationChangePatch = { profileId: string; text: string };

export type UserPolicyPublication = {
  commandId: string;
  writerId: string;
  generation: number;
  writerToken: string;
  expectedRevision: number;
  profileSettings?: ProfileSettingPatch[];
  preferenceChanges?: PreferenceChangePatch[];
  annotationChanges?: AnnotationChangePatch[];
  configuration?: { decisionProfileId: string | null };
};

type UserEditable = Pick<
  Draft,
  "profiles" | "preferences" | "annotations" | "configuration"
>;

const byProfileId = (a: { profileId: string }, b: { profileId: string }) =>
  a.profileId.localeCompare(b.profileId);

/** Enabled-intent changes, one patch per profile that recorded one. */
export function profileSettings(
  baseline: Draft,
  draft: Draft,
): ProfileSettingPatch[] {
  const before = new Map(baseline.profiles.map((p) => [p.profileId, p.enabled]));
  return draft.profiles
    .filter(
      (p) => before.get(p.profileId) !== undefined && before.get(p.profileId) !== p.enabled,
    )
    .map((p) => ({ profileId: p.profileId, enabled: p.enabled }))
    .sort(byProfileId);
}

/**
 * Preference patches. A removed preference is `mode: null`; a changed reason
 * rides along with its mode, and a trailing empty reason is a deliberate clear.
 */
export function preferenceChanges(
  baseline: Draft,
  draft: Draft,
): PreferenceChangePatch[] {
  const before = new Map(baseline.preferences.map((p) => [p.profileId, p]));
  const after = new Map(draft.preferences.map((p) => [p.profileId, p]));
  const changed: PreferenceChangePatch[] = [];
  for (const profileId of new Set([...before.keys(), ...after.keys()])) {
    const previous = before.get(profileId);
    const next = after.get(profileId);
    if (previous?.mode === next?.mode && (previous?.reason ?? "") === (next?.reason ?? "")) {
      continue;
    }
    changed.push(
      next
        ? { profileId, mode: next.mode, reason: next.reason }
        : { profileId, mode: null, reason: "" },
    );
  }
  return changed.sort(byProfileId);
}

/** Annotation patches; `text: ""` clears the recorded opinion. */
export function annotationChanges(
  baseline: Draft,
  draft: Draft,
): AnnotationChangePatch[] {
  const before = new Map(baseline.annotations.map((a) => [a.profileId, a.text]));
  const after = new Map(draft.annotations.map((a) => [a.profileId, a.text]));
  const changed: AnnotationChangePatch[] = [];
  for (const profileId of new Set([...before.keys(), ...after.keys()])) {
    const previous = before.get(profileId) ?? "";
    const next = after.get(profileId) ?? "";
    if (previous !== next) changed.push({ profileId, text: next });
  }
  return changed.sort(byProfileId);
}

export function configurationChanged(baseline: Draft, draft: Draft): boolean {
  return (
    baseline.configuration.decisionProfileId !==
    draft.configuration.decisionProfileId
  );
}

/**
 * `user_policy_publish` payload: identity, expected revision and only the dirty
 * user patches. An unchanged field is omitted so the board keeps its value; no
 * provider/model/effort/available/catalog field and no card is ever included.
 */
export function publication(
  baseline: Draft,
  draft: Draft,
  grant: WriterGrant,
  commandId: string,
): UserPolicyPublication {
  const settings = profileSettings(baseline, draft);
  const preferences = preferenceChanges(baseline, draft);
  const annotations = annotationChanges(baseline, draft);
  return {
    commandId,
    writerId: grant.writerId,
    generation: grant.generation,
    writerToken: grant.writerToken,
    expectedRevision: draft.tableRevision,
    ...(settings.length ? { profileSettings: settings } : {}),
    ...(preferences.length ? { preferenceChanges: preferences } : {}),
    ...(annotations.length ? { annotationChanges: annotations } : {}),
    ...(configurationChanged(baseline, draft)
      ? { configuration: { decisionProfileId: draft.configuration.decisionProfileId } }
      : {}),
  };
}

/**
 * Order-independent fingerprint of exactly the human-editable content. Program
 * fields (availability, catalog facts) are not user changes and never make a
 * draft look dirty after a directory refresh.
 */
function fingerprint(draft: UserEditable): string {
  return JSON.stringify({
    profiles: [...draft.profiles]
      .map(({ profileId, enabled }) => ({ profileId, enabled }))
      .sort(byProfileId),
    preferences: [...draft.preferences].sort(byProfileId),
    annotations: [...draft.annotations]
      .map(({ profileId, text }) => ({ profileId, text }))
      .sort(byProfileId),
    configuration: {
      decisionProfileId: draft.configuration.decisionProfileId,
    },
  });
}

/** True when the draft holds any unsubmitted change against its own baseline. */
export function draftDiffers(baseline: Draft, draft: Draft): boolean {
  return fingerprint(baseline) !== fingerprint(draft);
}

/**
 * One tuple per profile over the fields a human owns: its enabled intent, its
 * preference rows and its opinion. A card is program-owned and is not a human
 * change, so it can no longer hide or fabricate a profile edit.
 */
function profileTuples(draft: UserEditable): Map<string, string> {
  type Tuple = { enabled?: boolean; preferences: Preference[]; annotation?: string };
  const collected = new Map<string, Tuple>();
  const tuple = (profileId: string) => {
    const existing = collected.get(profileId);
    if (existing) return existing;
    const created: Tuple = { preferences: [] };
    collected.set(profileId, created);
    return created;
  };
  for (const profile of draft.profiles) tuple(profile.profileId).enabled = profile.enabled;
  for (const preference of draft.preferences) tuple(preference.profileId).preferences.push(preference);
  for (const annotation of draft.annotations) {
    if (annotation.text) tuple(annotation.profileId).annotation = annotation.text;
  }
  return new Map(
    [...collected].map(([profileId, value]) => [
      profileId,
      JSON.stringify({
        enabled: value.enabled ?? null,
        // Preferences are a set per profile; their storage order is not content.
        preferences: [...value.preferences].sort(
          (a, b) => a.mode.localeCompare(b.mode) || a.reason.localeCompare(b.reason),
        ),
        annotation: value.annotation ?? "",
      }),
    ]),
  );
}

/** Profiles whose recorded human content differs from the baseline. */
export function changedProfileIds(baseline: Draft, draft: Draft): string[] {
  const before = profileTuples(baseline);
  const after = profileTuples(draft);
  const changed = new Set<string>();
  for (const id of new Set([...before.keys(), ...after.keys()])) {
    if (before.get(id) !== after.get(id)) changed.add(id);
  }
  return [...changed].sort();
}

/**
 * Adopts freshly published program facts (a completed directory discovery or a
 * maintenance publication) without discarding the user's unsubmitted edits:
 * the new snapshot becomes the baseline and the dirty user patches are replayed
 * on top of it. Provider/model/effort/available/catalog facts come only from the
 * snapshot, so no local copy of them is published.
 */
export function rebaseDraft(
  draft: Draft,
  baseline: Draft,
  snapshot: Snapshot,
): { draft: Draft; baseline: Draft } {
  const nextBaseline = makeDraft(snapshot);
  const settings = new Map(
    profileSettings(baseline, draft).map((entry) => [entry.profileId, entry.enabled]),
  );
  let next: Draft = {
    ...nextBaseline,
    profiles: nextBaseline.profiles.map((profile) =>
      settings.has(profile.profileId)
        ? { ...profile, enabled: settings.get(profile.profileId)! }
        : profile,
    ),
  };
  for (const change of preferenceChanges(baseline, draft)) {
    next = change.mode
      ? setPreference(next, change.profileId, change.mode, change.reason)
      : { ...next, preferences: next.preferences.filter((p) => p.profileId !== change.profileId) };
  }
  for (const change of annotationChanges(baseline, draft)) {
    next = setAnnotation(next, change.profileId, change.text);
  }
  if (configurationChanged(baseline, draft)) {
    next = {
      ...next,
      configuration: {
        ...next.configuration,
        decisionProfileId: draft.configuration.decisionProfileId,
      },
    };
  }
  return { draft: next, baseline: nextBaseline };
}
