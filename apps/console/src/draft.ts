import type {
  Card,
  Draft,
  Preference,
  Profile,
  Snapshot,
  WriterGrant,
} from "./types";

export function makeDraft(snapshot: Snapshot): Draft {
  return structuredClone({
    tableRevision: snapshot.tableRevision,
    profiles: snapshot.profiles,
    cards: snapshot.cards,
    preferences: snapshot.preferences,
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

export function addProfiles(draft: Draft, profiles: Profile[]): Draft {
  const known = new Set(draft.profiles.map((p) => p.profileId));
  const added = profiles.filter(
    (p) => !known.has(p.profileId) && (known.add(p.profileId), true),
  );
  return { ...draft, profiles: [...draft.profiles, ...added] };
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

export function publication(
  draft: Draft,
  grant: WriterGrant,
  commandId: string,
) {
  return {
    commandId,
    writerId: grant.writerId,
    generation: grant.generation,
    writerToken: grant.writerToken,
    expectedRevision: draft.tableRevision,
    profiles: draft.profiles,
    preferences: draft.preferences,
    cards: draft.cards.map(
      ({ profileId, summary, strengths, limitations, risks, evidenceIds }) => ({
        profileId,
        summary,
        strengths: cleanLines(strengths),
        limitations: cleanLines(limitations),
        risks: cleanLines(risks),
        evidenceIds,
      }),
    ),
    configuration: {
      decisionProfileId: draft.configuration.decisionProfileId,
    },
  };
}

function cleanLines(value: string[]): string[] {
  return value.map((s) => s.trim()).filter(Boolean);
}
// Preserve an in-progress newline while typing; normalize only at publication.
export function splitLines(value: string): string[] {
  return value.split("\n");
}

type DraftContent = Pick<
  Draft,
  "profiles" | "cards" | "preferences" | "configuration"
>;

/**
 * Order-independent content fingerprint. A card is re-appended when it is edited,
 * so a positional comparison would report a change after the user undid it.
 */
function fingerprint(draft: DraftContent): string {
  return JSON.stringify({
    profiles: [...draft.profiles].sort(by("profileId")),
    preferences: [...draft.preferences].sort(by("profileId")),
    cards: [...draft.cards].sort(by("profileId")).map((card) => ({
      ...card,
      strengths: [...card.strengths],
      limitations: [...card.limitations],
      risks: [...card.risks],
      evidenceIds: [...card.evidenceIds],
    })),
    configuration: {
      decisionProfileId: draft.configuration.decisionProfileId,
    },
  });
}

function by(key: "profileId") {
  return (a: { profileId: string }, b: { profileId: string }) =>
    a[key].localeCompare(b[key]);
}

/** True when the draft holds any unsubmitted change against its own baseline. */
export function draftDiffers(baseline: Draft, draft: Draft): boolean {
  return fingerprint(baseline) !== fingerprint(draft);
}

/**
 * One record per profile: its profile row, its card and every preference row.
 * Keeping the three together matters because a card edit must not hide an
 * enablement or preference edit made at the same time.
 */
function profileTuples(draft: DraftContent): Map<string, string> {
  type Tuple = { profile?: Profile; card?: Card; preferences: Preference[] };
  const collected = new Map<string, Tuple>();
  const tuple = (profileId: string) => {
    const existing = collected.get(profileId);
    if (existing) return existing;
    const created: Tuple = { preferences: [] };
    collected.set(profileId, created);
    return created;
  };
  for (const profile of draft.profiles) tuple(profile.profileId).profile = profile;
  for (const card of draft.cards) tuple(card.profileId).card = card;
  for (const preference of draft.preferences) tuple(preference.profileId).preferences.push(preference);
  return new Map(
    [...collected].map(([profileId, value]) => [
      profileId,
      JSON.stringify({
        profile: value.profile ?? null,
        card: value.card ?? null,
        // Preferences are a set per profile; their storage order is not content.
        preferences: [...value.preferences].sort(
          (a, b) => a.mode.localeCompare(b.mode) || a.reason.localeCompare(b.reason),
        ),
      }),
    ]),
  );
}

/** Profiles whose recorded content differs from the baseline. */
export function changedProfileIds(baseline: Draft, draft: Draft): string[] {
  const before = profileTuples(baseline);
  const after = profileTuples(draft);
  const changed = new Set<string>();
  for (const id of new Set([...before.keys(), ...after.keys()])) {
    if (before.get(id) !== after.get(id)) changed.add(id);
  }
  return [...changed].sort();
}

export function configurationChanged(baseline: Draft, draft: Draft): boolean {
  return (
    baseline.configuration.decisionProfileId !==
    draft.configuration.decisionProfileId
  );
}
