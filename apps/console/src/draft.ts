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
  /**
   * Table revision the rows were read at. Retained rows are only ever merged
   * into a source at that exact revision: a cached page must not seed, or be
   * relabelled as, a newer published table.
   */
  tableRevision: number;
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
 * always win; history only fills identities that would otherwise be invisible,
 * and only when the page was read at the source's own revision.
 */
export function withHistory<T extends Pick<Draft, "profiles" | "preferences" | "annotations"> & { tableRevision: number }>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  if (!entries || entries.tableRevision !== source.tableRevision) return source;
  return {
    ...source,
    profiles: addMissing(source.profiles, entries.profiles),
    preferences: addMissing(source.preferences, entries.preferences),
    annotations: addMissing(source.annotations, entries.annotations),
  };
}

/** Full console view: human draft (or snapshot) plus retained history rows. */
export function historyView<T extends Pick<Draft, "profiles" | "preferences" | "annotations">
  & { tableRevision: number; cards: Card[]; sampleCounts?: Record<string, number> }>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  // A page from another revision is stale cache, not evidence about this table.
  if (!entries || entries.tableRevision !== source.tableRevision) return source;
  const base = withHistory(source, entries);
  // Snapshot cards and counts are authoritative for the identities they cover.
  const counts = { ...(entries.sampleCounts ?? {}), ...(source.sampleCounts ?? {}) };
  return { ...base, cards: addMissing(source.cards, entries.cards ?? []), sampleCounts: counts };
}

/**
 * Retained rows that may still be added to an existing draft. A retained row
 * whose identity the draft still holds adds nothing, and a row the draft no
 * longer holds while the baseline does is a deliberate removal (for example
 * "无额外偏好" or a cleared opinion) that retained history must never resurrect.
 * Identities neither the draft nor the baseline ever knew are still filled in.
 */
export function retainedAdditions(
  draft: Draft,
  baseline: Draft,
  entries: HistoryEntries | null | undefined,
): HistoryEntries | null {
  if (!entries || entries.tableRevision !== draft.tableRevision) return null;
  const additions = <T extends { profileId: string }>(
    rows: T[] | undefined,
    current: { profileId: string }[],
    known: { profileId: string }[],
  ): T[] | undefined => {
    if (!rows) return rows;
    const inDraft = new Set(current.map((row) => row.profileId));
    const inBaseline = new Set(known.map((row) => row.profileId));
    return rows.filter((row) => inDraft.has(row.profileId) || !inBaseline.has(row.profileId));
  };
  return {
    ...entries,
    profiles: additions(entries.profiles, draft.profiles, baseline.profiles),
    preferences: additions(entries.preferences, draft.preferences, baseline.preferences),
    annotations: additions(entries.annotations, draft.annotations, baseline.annotations),
  };
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
 * One human-owned field that could not be replayed onto a refreshed table.
 * `changed` means another publication holds a different value; `unread` means
 * the bounded snapshot had no row to compare against yet, so the edit stays
 * unresolved until fresh retained-row data is read.
 */
export type RebaseConflictKind = "changed" | "unread";
export type RebaseConflict = {
  kind: RebaseConflictKind;
  field: "enabled" | "preference" | "annotation" | "configuration";
  profileId: string;
  message: string;
};

export type RebaseOutcome = {
  draft: Draft;
  baseline: Draft;
  conflicts: RebaseConflict[];
};

const FIELD_NAMES: Record<RebaseConflict["field"], string> = {
  enabled: "启用状态",
  preference: "用户偏好",
  annotation: "人工意见",
  configuration: "决策模型配置",
};

function conflictSubject(field: RebaseConflict["field"], profileId: string): string {
  return field === "configuration"
    ? `决策模型配置${profileId ? ` ${profileId}` : "（空）"}`
    : `配置 ${profileId} 的${FIELD_NAMES[field]}`;
}

/**
 * Adopts freshly published program facts (a completed directory discovery or a
 * maintenance publication) without discarding the user's unsubmitted edits.
 *
 * Every human-owned field is checked three ways against the old baseline, the
 * draft and the refreshed table. The draft value is replayed only when the
 * refreshed value still equals the old baseline (nobody else touched it) or
 * already equals the draft intent. Anything else keeps the *original* draft and
 * its `expectedRevision` and reports an actionable conflict, so a stale draft
 * can never silently overwrite another writer's publication. A dirty field
 * whose profile is missing from the bounded snapshot stays unresolved until a
 * retained page read at the refreshed revision supplies the missing row; cached
 * rows from an older revision are never used to mint a new baseline.
 */
export function rebaseDraft(
  draft: Draft,
  baseline: Draft,
  snapshot: Snapshot,
  retained?: HistoryEntries | null,
): RebaseOutcome {
  const nextBaseline = withHistory(makeDraft(snapshot), retained);
  const conflicts: RebaseConflict[] = [];
  const fail = (
    kind: RebaseConflictKind,
    field: RebaseConflict["field"],
    profileId: string,
    detail = "",
  ) => {
    const subject = conflictSubject(field, profileId);
    conflicts.push({
      kind,
      field,
      profileId,
      message: kind === "unread"
        ? `${subject} 尚不能与 V${snapshot.tableRevision} 核对：它不在最新快照中，保留历史也尚未按 V${snapshot.tableRevision} 重新读取。你的修改和原基于版本都保留；读取“显示不可用配置”的历史后会自动核对。`
        : `${subject} 已被其他发布修改（V${snapshot.tableRevision}）${detail}；为避免覆盖，草稿仍基于原版本，请重新加载最新版本核对后再提交。`,
    });
  };
  const freshProfiles = new Map(nextBaseline.profiles.map((p) => [p.profileId, p]));

  // Enabled intent: only a profile that still exists in the refreshed table can
  // be compared; a missing row is unresolved rather than silently dropped.
  const enabledOverrides = new Map<string, boolean>();
  const baselineEnabled = new Map(baseline.profiles.map((p) => [p.profileId, p.enabled]));
  for (const setting of profileSettings(baseline, draft)) {
    const fresh = freshProfiles.get(setting.profileId);
    if (!fresh) {
      fail("unread", "enabled", setting.profileId);
      continue;
    }
    const old = baselineEnabled.get(setting.profileId);
    if (fresh.enabled === old || fresh.enabled === setting.enabled) {
      enabledOverrides.set(setting.profileId, setting.enabled);
    } else {
      fail("changed", "enabled", setting.profileId, `（现为${fresh.enabled ? "已启用" : "已停用"}）`);
    }
  }

  // Preferences: mode and reason are separate fields. A field the human did not
  // touch keeps the refreshed value, so another writer's reason survives a mode
  // change of ours.
  const preferenceMerges = new Map<string, Preference>();
  const preferenceRemovals = new Set<string>();
  const baselinePreferences = new Map(baseline.preferences.map((p) => [p.profileId, p]));
  const draftPreferences = new Map(draft.preferences.map((p) => [p.profileId, p]));
  for (const change of preferenceChanges(baseline, draft)) {
    const profileId = change.profileId;
    if (!freshProfiles.has(profileId)) {
      fail("unread", "preference", profileId);
      continue;
    }
    const oldRow = baselinePreferences.get(profileId);
    const draftRow = draftPreferences.get(profileId);
    const freshRow = nextBaseline.preferences.find((p) => p.profileId === profileId);
    const oldMode = oldRow?.mode;
    const draftMode = draftRow?.mode;
    const freshMode = freshRow?.mode;
    let mode: Preference["mode"] | undefined;
    if (oldMode === draftMode) mode = freshMode;
    else if (freshMode === oldMode || freshMode === draftMode) mode = draftMode;
    else {
      fail("changed", "preference", profileId, "（模式）");
      continue;
    }
    const oldReason = oldRow?.reason ?? "";
    const draftReason = draftRow?.reason ?? "";
    const freshReason = freshRow?.reason ?? "";
    let reason: string;
    if (oldReason === draftReason) reason = freshReason;
    else if (freshReason === oldReason || freshReason === draftReason) reason = draftReason;
    else {
      fail("changed", "preference", profileId, "（依据）");
      continue;
    }
    if (mode === undefined) preferenceRemovals.add(profileId);
    else preferenceMerges.set(profileId, { profileId, mode, reason });
  }

  // Opinions: the text is the field; an already-equal refreshed value wins.
  const mergedAnnotations = new Map<string, string>();
  const baselineAnnotations = new Map(baseline.annotations.map((a) => [a.profileId, a.text]));
  for (const change of annotationChanges(baseline, draft)) {
    const profileId = change.profileId;
    if (!freshProfiles.has(profileId)) {
      fail("unread", "annotation", profileId);
      continue;
    }
    const oldText = baselineAnnotations.get(profileId) ?? "";
    const freshText = nextBaseline.annotations.find((a) => a.profileId === profileId)?.text ?? "";
    if (freshText === oldText || freshText === change.text) {
      mergedAnnotations.set(profileId, change.text);
    } else {
      fail("changed", "annotation", profileId);
    }
  }

  // The fixed decision configuration is a single global field.
  let decisionProfileId = nextBaseline.configuration.decisionProfileId;
  if (configurationChanged(baseline, draft)) {
    const old = baseline.configuration.decisionProfileId;
    const wanted = draft.configuration.decisionProfileId;
    const fresh = nextBaseline.configuration.decisionProfileId;
    if (fresh === old || fresh === wanted) decisionProfileId = wanted;
    else fail("changed", "configuration", wanted ?? "");
  }

  if (conflicts.length) return { draft, baseline, conflicts };

  const preferences = nextBaseline.preferences.filter(
    (p) => !preferenceMerges.has(p.profileId) && !preferenceRemovals.has(p.profileId),
  );
  for (const preference of preferenceMerges.values()) preferences.push(preference);
  const annotations = nextBaseline.annotations.filter(
    (annotation) => !mergedAnnotations.has(annotation.profileId),
  );
  for (const [profileId, text] of mergedAnnotations) {
    const known = nextBaseline.annotations.find((a) => a.profileId === profileId);
    annotations.push({ ...(known ?? emptyAnnotation(profileId)), profileId, text });
  }
  return {
    baseline: nextBaseline,
    draft: {
      ...nextBaseline,
      profiles: nextBaseline.profiles.map((profile) =>
        enabledOverrides.has(profile.profileId)
          ? { ...profile, enabled: enabledOverrides.get(profile.profileId)! }
          : profile,
      ),
      preferences,
      annotations,
      configuration: { ...nextBaseline.configuration, decisionProfileId },
    },
    conflicts,
  };
}
