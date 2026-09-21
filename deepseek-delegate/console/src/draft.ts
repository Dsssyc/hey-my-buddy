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
    sampleCount: 0,
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
      autoMaintain: draft.configuration.autoMaintain,
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
