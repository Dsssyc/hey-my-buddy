import type {
  Draft,
  FamilyAnnotation,
  ModelConcurrencyEntry,
  ModelConcurrencySetting,
  ModelFamily,
  Preference,
  PreferenceMode,
  Profile,
  Configuration,
  Snapshot,
} from "./types";
import { concurrencyLimit, familyKey } from "./console-data";

/** Retained identity rows the current snapshot no longer lists. */
export type HistoryEntries = {
  profiles?: Profile[];
  preferences?: Preference[];
  familyAnnotations?: FamilyAnnotation[];
  modelConcurrency?: ModelConcurrencyEntry[];
};

const familyOf = (row: ModelFamily): ModelFamily =>
  ({ adapter: row.adapter, provider: row.provider, model: row.model });

export function makeDraft(snapshot: Snapshot): Draft {
  return structuredClone({
    profiles: snapshot.profiles,
    preferences: snapshot.preferences,
    familyAnnotations: snapshot.familyAnnotations ?? [],
    configuration: snapshot.configuration,
    // The draft carries the user limit settings only; occupancy is observation
    // and is dropped here so no publication path can ever emit it.
    modelConcurrency: snapshot.modelConcurrency.map(
      ({ adapter, provider, model, limit }) => ({ adapter, provider, model, limit }),
    ),
  });
}

export function emptyFamilyAnnotation(family: ModelFamily): FamilyAnnotation {
  return { ...familyOf(family), text: "", updatedAt: null };
}

/** One note per family; empty text is a deliberate clear, not a delete. */
export function setFamilyAnnotation(
  draft: Draft,
  family: ModelFamily,
  text: string,
): Draft {
  const key = familyKey(family);
  const known = draft.familyAnnotations.find((a) => familyKey(a) === key);
  const next: FamilyAnnotation = { ...(known ?? emptyFamilyAnnotation(family)), text };
  return {
    ...draft,
    familyAnnotations: [
      ...draft.familyAnnotations.filter((a) => familyKey(a) !== key),
      next,
    ],
  };
}

export function familyAnnotationText(
  source: { familyAnnotations: FamilyAnnotation[] },
  family: ModelFamily,
): string {
  const key = familyKey(family);
  return source.familyAnnotations.find((a) => familyKey(a) === key)?.text ?? "";
}

/** An empty mode clears this buddy's preference. */
export function setPreference(draft: Draft, profileId: string, mode: PreferenceMode | "", reason = ""): Draft {
  const preferences = draft.preferences.filter(p => p.profileId !== profileId);
  if (mode) preferences.push({ profileId, mode, reason });
  return { ...draft, preferences };
}

function addMissing<T extends { profileId: string }>(current: T[], extra: T[] | undefined): T[] {
  if (!extra?.length) return current;
  const known = new Set(current.map((entry) => entry.profileId));
  const added = extra.filter((entry) => !known.has(entry.profileId) && (known.add(entry.profileId), true));
  return added.length ? [...current, ...added] : current;
}

function addMissingFamilies<T extends ModelFamily>(
  current: T[],
  extra: T[] | undefined,
): T[] {
  if (!extra?.length) return current;
  const known = new Set(current.map(familyKey));
  const added = extra.filter((entry) => !known.has(familyKey(entry)) && (known.add(familyKey(entry)), true));
  return added.length ? [...current, ...added] : current;
}

type UserRows = Pick<Draft, "profiles" | "preferences" | "familyAnnotations">
  & { modelConcurrency?: ModelConcurrencySetting[] };

/**
 * Adds retained history rows the snapshot no longer lists. Live snapshot rows
 * always win; history only fills identities that would otherwise be invisible,
 * The caller supplies only a page read for the current snapshot content.
 */
export function withHistory<T extends UserRows>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  if (!entries) return source;
  // A draft never carries occupancy, so retained family entries join as settings.
  const retainedSettings = entries.modelConcurrency?.map(
    ({ adapter, provider, model, limit }) => ({ adapter, provider, model, limit }),
  );
  return {
    ...source,
    profiles: addMissing(source.profiles, entries.profiles),
    preferences: addMissing(source.preferences, entries.preferences),
    familyAnnotations: addMissingFamilies(source.familyAnnotations, entries.familyAnnotations),
    modelConcurrency: addMissingFamilies(source.modelConcurrency ?? [], retainedSettings),
  };
}

/** Full console view: human draft (or snapshot) plus retained history rows. */
export function historyView<T extends UserRows>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  if (!entries) return source;
  const base = withHistory(source, entries);
  // Retained family entries keep their recorded occupancy for the read-only view.
  return { ...base,
    modelConcurrency: addMissingFamilies(source.modelConcurrency ?? [], entries.modelConcurrency) };
}

/**
 * Sets one family's concurrent-task limit, identified by the exact
 * adapter/provider/model tuple of any of its effort variants. The setting
 * exists independently of availability and is keyed once per family.
 */
export function setConcurrencyLimit(
  draft: Draft,
  family: ModelFamily,
  limit: number,
): Draft {
  const key = familyKey(family);
  const exists = draft.modelConcurrency.some((entry) => familyKey(entry) === key);
  const modelConcurrency = exists
    ? draft.modelConcurrency.map((entry) => (familyKey(entry) === key ? { ...entry, limit } : entry))
    : [...draft.modelConcurrency, { adapter: family.adapter, provider: family.provider, model: family.model, limit }];
  return { ...draft, modelConcurrency };
}

const byFamily = (a: ModelFamily, b: ModelFamily) => familyKey(a).localeCompare(familyKey(b));

/** Limit patches, one per family whose user setting changed; never occupancy. */
export function modelConcurrencyChanges(
  baseline: Draft,
  draft: Draft,
): ModelConcurrencySetting[] {
  const before = new Map(baseline.modelConcurrency.map((entry) => [familyKey(entry), entry]));
  const changed: ModelConcurrencySetting[] = [];
  for (const entry of draft.modelConcurrency) {
    const limit = concurrencyLimit(entry.limit);
    const old = before.get(familyKey(entry));
    if (limit === null || (old && old.limit === limit)) continue;
    changed.push({ adapter: entry.adapter, provider: entry.provider, model: entry.model, limit });
  }
  return changed.sort(byFamily);
}

/** The only fields a human may publish; program-owned profile/catalog fields stay out. */
export type ProfileSettingPatch = { profileId: string; enabled: boolean };
/** One buddy's preference; `mode: null` clears it. */
export type PreferenceChangePatch = { profileId: string; mode: PreferenceMode | null; reason: string };
/** Family note patch; `text: ""` clears the note. */
export type FamilyAnnotationChangePatch = ModelFamily & { text: string };

export type UserPolicyPublication = {
  commandId: string;
  profileSettings?: ProfileSettingPatch[];
  preferenceChanges?: PreferenceChangePatch[];
  familyAnnotationChanges?: FamilyAnnotationChangePatch[];
  configuration?: Partial<Pick<Configuration,
    "routerProfileIds" | "routerRetryIntervalSeconds" | "defaultRoutingMode" | "routingBudget">>;
  /** Per-family limit patches; the board refuses any `active` occupancy here. */
  modelConcurrency?: ModelConcurrencySetting[];
};

type UserEditable = Pick<
  Draft,
  "profiles" | "preferences" | "familyAnnotations" | "configuration" | "modelConcurrency"
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

/** Changes to each buddy's preference. */
export function preferenceChanges(baseline: Draft, draft: Draft): PreferenceChangePatch[] {
  const before = new Map(baseline.preferences.map(p => [p.profileId, p]));
  const after = new Map(draft.preferences.map(p => [p.profileId, p]));
  const changed: PreferenceChangePatch[] = [];
  for (const profileId of new Set([...before.keys(), ...after.keys()])) {
    const previous = before.get(profileId);
    const next = after.get(profileId);
    if (previous?.mode === next?.mode && (previous?.reason ?? "") === (next?.reason ?? "")) continue;
    changed.push(next ? { profileId, mode: next.mode, reason: next.reason } : { profileId, mode: null, reason: "" });
  }
  return changed.sort(byProfileId);
}

/** Family note patches; `text: ""` clears the recorded note. */
export function familyAnnotationChanges(
  baseline: Draft,
  draft: Draft,
): FamilyAnnotationChangePatch[] {
  const before = new Map(baseline.familyAnnotations.map((a) => [familyKey(a), a]));
  const after = new Map(draft.familyAnnotations.map((a) => [familyKey(a), a]));
  const changed: FamilyAnnotationChangePatch[] = [];
  for (const key of new Set([...before.keys(), ...after.keys()])) {
    const previous = before.get(key)?.text ?? "";
    const next = after.get(key)?.text ?? "";
    if (previous !== next) changed.push({ ...familyOf((after.get(key) ?? before.get(key))!), text: next });
  }
  return changed.sort(byFamily);
}

/** The ordered Router list is equal when it holds the same ids in the same order. */
export function routerListEqual(a: string[] | undefined, b: string[] | undefined): boolean {
  return JSON.stringify(a ?? []) === JSON.stringify(b ?? []);
}

export const ROUTER_FIELDS = ["routerProfileIds", "defaultRoutingMode", "routerRetryIntervalSeconds"] as const;

export function routerFieldChanged(baseline: Draft, draft: Draft, field: typeof ROUTER_FIELDS[number]): boolean {
  if (!baseline.configuration || !draft.configuration) return false;
  const before = baseline.configuration[field];
  const after = draft.configuration[field];
  return field === "routerProfileIds" ? !routerListEqual(before as string[], after as string[]) : before !== after;
}

export function routingBudgetChanged(baseline: Draft, draft: Draft): boolean {
  return !!baseline.configuration && !!draft.configuration && baseline.configuration.routingBudget !== draft.configuration.routingBudget;
}

export function configurationChanged(baseline: Draft, draft: Draft): boolean {
  return ROUTER_FIELDS.some(field => routerFieldChanged(baseline, draft, field)) || routingBudgetChanged(baseline, draft);
}

/**
 * `user_policy_publish` payload: identity and only the dirty
 * user patches. An unchanged field is omitted so the board keeps its value; no
 * provider/model/effort/available/catalog field, no card, no effective
 * preference and no occupancy is ever included.
 */
export function publication(
  baseline: Draft,
  draft: Draft,
  commandId: string,
): UserPolicyPublication {
  const settings = profileSettings(baseline, draft);
  const preferences = preferenceChanges(baseline, draft);
  const notes = familyAnnotationChanges(baseline, draft);
  const concurrency = modelConcurrencyChanges(baseline, draft);
  return {
    commandId,
    ...(settings.length ? { profileSettings: settings } : {}),
    ...(preferences.length ? { preferenceChanges: preferences } : {}),
    ...(notes.length ? { familyAnnotationChanges: notes } : {}),
    ...(concurrency.length ? { modelConcurrency: concurrency } : {}),
    ...(configurationChanged(baseline, draft)
      ? { configuration: {
          ...(routerFieldChanged(baseline, draft, "routerProfileIds")
            ? { routerProfileIds: draft.configuration!.routerProfileIds ?? [] } : {}),
          ...(routerFieldChanged(baseline, draft, "routerRetryIntervalSeconds")
            ? { routerRetryIntervalSeconds: draft.configuration!.routerRetryIntervalSeconds } : {}),
          ...(routerFieldChanged(baseline, draft, "defaultRoutingMode")
            ? { defaultRoutingMode: draft.configuration!.defaultRoutingMode } : {}),
          ...(routingBudgetChanged(baseline, draft) ? { routingBudget: draft.configuration!.routingBudget ?? "standard" } : {}),
        } }
      : {}),
  };
}

/**
 * Order-independent fingerprint of exactly the human-editable content. Program
 * fields (availability, catalog facts, recorded occupancy) are not user changes
 * and never make a draft look dirty after a directory refresh. An empty note is
 * the same content as no note.
 */
function fingerprint(draft: UserEditable): string {
  return JSON.stringify({
    profiles: [...draft.profiles]
      .map(({ profileId, enabled }) => ({ profileId, enabled }))
      .sort(byProfileId),
    preferences: [...draft.preferences].map(({ profileId, mode, reason }) => ({ profileId, mode, reason })).sort(byProfileId),
    familyAnnotations: draft.familyAnnotations
      .filter((a) => a.text)
      .map((a) => ({ key: familyKey(a), text: a.text }))
      .sort((a, b) => a.key.localeCompare(b.key)),
    configuration: draft.configuration ? {
      routerProfileIds: draft.configuration.routerProfileIds ?? [],
      routerRetryIntervalSeconds: draft.configuration.routerRetryIntervalSeconds,
      defaultRoutingMode: draft.configuration.defaultRoutingMode,
      routingBudget: draft.configuration.routingBudget,
    } : null,
    modelConcurrency: [...draft.modelConcurrency]
      .map(({ adapter, provider, model, limit }) => ({ key: familyKey({ adapter, provider, model }), limit }))
      .sort((a, b) => a.key.localeCompare(b.key)),
  });
}

/** True when the draft holds any unsubmitted change against its own baseline. */
export function draftDiffers(baseline: Draft, draft: Draft): boolean {
  return fingerprint(baseline) !== fingerprint(draft);
}

/**
 * Number of unsaved user changes, counted as the patches a save would carry.
 * A family limit that is not a valid setting yet still counts, so the save bar
 * never says "0 项" while the draft differs.
 */
export function changeCount(baseline: Draft, draft: Draft): number {
  const limits = new Map(baseline.modelConcurrency.map((entry) => [familyKey(entry), entry.limit]));
  const concurrency = draft.modelConcurrency.filter(
    (entry) => !Object.is(limits.get(familyKey(entry)), entry.limit),
  ).length;
  return profileSettings(baseline, draft).length
    + preferenceChanges(baseline, draft).length
    + familyAnnotationChanges(baseline, draft).length
    + concurrency
    + ROUTER_FIELDS.filter(field => routerFieldChanged(baseline, draft, field)).length
    + (routingBudgetChanged(baseline, draft) ? 1 : 0);
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
  field: "enabled" | "preference" | "familyAnnotation" | "configuration" | "defaultRoutingMode" | "routingBudget" | "routerRetryIntervalSeconds" | "modelConcurrency";
  /** The profile id, or the `adapter/provider/model` of a family-level field. */
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
  preference: "推理强度偏好",
  familyAnnotation: "家族备注",
  configuration: "Router 列表",
  defaultRoutingMode: "默认路由模式",
  routingBudget: "路由预算",
  routerRetryIntervalSeconds: "重试间隔",
  modelConcurrency: "并发上限",
};

const familyText = (family: ModelFamily) => `${family.adapter}/${family.provider}/${family.model}`;

function conflictSubject(field: RebaseConflict["field"], profileId: string): string {
  if (field === "configuration") return profileId ? `Router 列表（${profileId}）` : "Router 列表";
  if (field === "defaultRoutingMode") return "默认路由模式";
  if (field === "routingBudget") return "路由预算";
  if (field === "routerRetryIntervalSeconds") return "重试间隔";
  if (field === "familyAnnotation" || field === "modelConcurrency") {
    return `模型家族 ${profileId} 的${FIELD_NAMES[field]}`;
  }
  return `配置 ${profileId} 的${FIELD_NAMES[field]}`;
}

/** Replay a local edit on the latest content; policy patches are last-write-wins. */
function merge<T>(old: T, wanted: T, fresh: T, same: (a: T, b: T) => boolean = Object.is): { value: T } {
  if (same(old, wanted)) return { value: fresh };
  return { value: wanted };
}

/**
 * Adopts freshly published program facts without discarding the user's unsubmitted edits.
 *
 * Human-owned edits are replayed on the refreshed table. Concurrent changes
 * to the same field use the local edit when it is published. A dirty field
 * whose profile (or, for a family field, every profile of the family) is
 * missing from the bounded snapshot stays unresolved until a fresh retained
 * history page supplies the row.
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
        ? `${subject} 尚待读取最新版本；草稿已保留，读取后将自动核对。`
        : `${subject} 已被其他人更改${detail}；草稿已保留，请重新加载核对。`,
    });
  };
  const freshProfiles = new Map(nextBaseline.profiles.map((p) => [p.profileId, p]));
  const freshFamilies = new Set(nextBaseline.profiles.map(familyKey));

  // Enabled intent: only a profile that still exists in the refreshed table can
  // be compared; a missing row is unresolved rather than silently dropped.
  const enabledOverrides = new Map<string, boolean>();
  for (const setting of profileSettings(baseline, draft)) {
    const fresh = freshProfiles.get(setting.profileId);
    if (!fresh) {
      fail("unread", "enabled", setting.profileId);
      continue;
    }
    enabledOverrides.set(setting.profileId, setting.enabled);
  }

  // Buddy preferences: mode and reason are merged per profile.
  const preferenceMerges = new Map<string, Preference>();
  const preferenceRemovals = new Set<string>();
  for (const change of preferenceChanges(baseline, draft)) {
    const profileId = change.profileId;
    if (!freshProfiles.has(profileId)) {
      fail("unread", "preference", profileId);
      continue;
    }
    const oldRow = baseline.preferences.find(p => p.profileId === profileId);
    const draftRow = draft.preferences.find(p => p.profileId === profileId);
    const freshRow = nextBaseline.preferences.find(p => p.profileId === profileId);
    const mode = merge(oldRow?.mode, draftRow?.mode, freshRow?.mode);
    const reason = merge(oldRow?.reason ?? "", draftRow?.reason ?? "", freshRow?.reason ?? "");
    if (mode.value === undefined) preferenceRemovals.add(profileId);
    else preferenceMerges.set(profileId, { profileId, mode: mode.value, reason: reason.value });
  }

  // Family notes: replay the local text over the refreshed value.
  const mergedNotes = new Map<string, FamilyAnnotationChangePatch>();
  for (const change of familyAnnotationChanges(baseline, draft)) {
    const key = familyKey(change);
    if (!freshFamilies.has(key)) {
      fail("unread", "familyAnnotation", familyText(change));
      continue;
    }
    mergedNotes.set(key, change);
  }

  let mergedRouting = nextBaseline.configuration;
  if (baseline.configuration && draft.configuration && nextBaseline.configuration) {
    mergedRouting = { ...nextBaseline.configuration };
    for (const field of [...ROUTER_FIELDS, "routingBudget"] as const) {
      const changed = field === "routerProfileIds"
        ? !routerListEqual(baseline.configuration.routerProfileIds, draft.configuration.routerProfileIds)
        : baseline.configuration[field] !== draft.configuration[field];
      if (!changed) continue;
      const wanted = draft.configuration[field];
      Object.assign(mergedRouting, { [field]: wanted });
    }
  } else if (configurationChanged(baseline, draft)) {
    fail("changed", "configuration", "", "（设置需升级）");
  }

  // Family concurrency limits: the key is the exact adapter/provider/model
  // tuple, so effort variants and occupancy never take part in the comparison.
  const mergedConcurrency = new Map<string, ModelConcurrencySetting>();
  for (const entry of draft.modelConcurrency) {
    if (concurrencyLimit(entry.limit) === null) {
      fail("changed", "modelConcurrency", familyText(entry), "（请先修正草稿中的无效值）");
    }
  }
  for (const change of modelConcurrencyChanges(baseline, draft)) {
    const key = familyKey(change);
    const freshEntry = nextBaseline.modelConcurrency.find((entry) => familyKey(entry) === key);
    if (!freshEntry) {
      fail("unread", "modelConcurrency", familyText(change));
      continue;
    }
    mergedConcurrency.set(key, change);
  }

  if (conflicts.length) return { draft, baseline, conflicts };

  const preferences = nextBaseline.preferences.filter(p => !preferenceMerges.has(p.profileId) && !preferenceRemovals.has(p.profileId));
  for (const preference of preferenceMerges.values()) preferences.push(preference);
  const familyAnnotations = nextBaseline.familyAnnotations.filter(
    (annotation) => !mergedNotes.has(familyKey(annotation)),
  );
  for (const [key, change] of mergedNotes) {
    const known = nextBaseline.familyAnnotations.find((a) => familyKey(a) === key);
    familyAnnotations.push({ ...(known ?? emptyFamilyAnnotation(change)), text: change.text });
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
      familyAnnotations,
      configuration: mergedRouting,
      modelConcurrency: nextBaseline.modelConcurrency.map((entry) =>
        mergedConcurrency.get(familyKey(entry)) ?? entry),
    },
    conflicts,
  };
}
