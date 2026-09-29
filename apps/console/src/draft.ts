import type {
  Card,
  Draft,
  FamilyAnnotation,
  FamilyPreference,
  ModelConcurrencyEntry,
  ModelConcurrencySetting,
  ModelFamily,
  OverrideMode,
  Preference,
  PreferenceMode,
  PreferenceOverride,
  Profile,
  Configuration,
  Snapshot,
  WriterGrant,
} from "./types";
import { concurrencyLimit, familyKey } from "./console-data";

/** Retained identity rows the current snapshot no longer lists. */
export type HistoryEntries = {
  /**
   * Table revision the rows were read at. Retained rows are only ever merged
   * into a source at that exact revision: a cached page must not seed, or be
   * relabelled as, a newer published table.
   */
  tableRevision: number;
  profiles?: Profile[];
  /** Effective preferences of the retained rows (read-only). */
  preferences?: Preference[];
  preferenceOverrides?: PreferenceOverride[];
  familyPreferences?: FamilyPreference[];
  familyAnnotations?: FamilyAnnotation[];
  cards?: Card[];
  sampleCounts?: Record<string, number>;
  modelConcurrency?: ModelConcurrencyEntry[];
};

const familyOf = (row: ModelFamily): ModelFamily =>
  ({ adapter: row.adapter, provider: row.provider, model: row.model });

export function makeDraft(snapshot: Snapshot): Draft {
  return structuredClone({
    tableRevision: snapshot.tableRevision,
    profiles: snapshot.profiles,
    familyPreferences: snapshot.familyPreferences ?? [],
    preferenceOverrides: snapshot.preferenceOverrides ?? [],
    familyAnnotations: snapshot.familyAnnotations ?? [],
    configuration: snapshot.configuration,
    // The draft carries the user limit settings only; occupancy is observation
    // and is dropped here so no publication path can ever emit it.
    modelConcurrency: snapshot.modelConcurrency.map(
      ({ adapter, provider, model, limit }) => ({ adapter, provider, model, limit }),
    ),
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

export function emptyFamilyAnnotation(family: ModelFamily): FamilyAnnotation {
  return { ...familyOf(family), text: "", revision: 0, updatedAt: null };
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

/** Family default preference; an empty mode clears the default. */
export function setFamilyPreference(
  draft: Draft,
  family: ModelFamily,
  mode: PreferenceMode | "",
  reason = "",
): Draft {
  const key = familyKey(family);
  const familyPreferences = draft.familyPreferences.filter((p) => familyKey(p) !== key);
  if (mode) familyPreferences.push({ ...familyOf(family), mode, reason });
  return { ...draft, familyPreferences };
}

/**
 * One effort's override. An empty mode deletes the override so the effort
 * follows its family default again; `none` is an explicit "no preference".
 */
export function setPreferenceOverride(
  draft: Draft,
  profileId: string,
  mode: OverrideMode | "",
  reason = "",
): Draft {
  const preferenceOverrides = draft.preferenceOverrides.filter((p) => p.profileId !== profileId);
  if (mode) preferenceOverrides.push({ profileId, mode, reason });
  return { ...draft, preferenceOverrides };
}

/**
 * Effective preferences, mirroring the schema 13 `effective_preferences` view:
 * an override wins (a `none` override yields no row), otherwise the family
 * default applies. Only the listed profiles get a row.
 */
export function effectivePreferences(source: {
  profiles: Profile[];
  familyPreferences: FamilyPreference[];
  preferenceOverrides: PreferenceOverride[];
}): Preference[] {
  const overrides = new Map(source.preferenceOverrides.map((p) => [p.profileId, p]));
  const families = new Map(source.familyPreferences.map((p) => [familyKey(p), p]));
  const rows: Preference[] = [];
  for (const profile of source.profiles) {
    const override = overrides.get(profile.profileId);
    if (override) {
      if (override.mode !== "none") {
        rows.push({ profileId: profile.profileId, mode: override.mode, reason: override.reason, source: "override" });
      }
      continue;
    }
    const family = families.get(familyKey(profile));
    if (family) rows.push({ profileId: profile.profileId, mode: family.mode, reason: family.reason, source: "family" });
  }
  return rows;
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

type UserRows = Pick<Draft, "profiles" | "familyPreferences" | "preferenceOverrides" | "familyAnnotations">
  & { tableRevision: number; modelConcurrency?: ModelConcurrencySetting[] };

/**
 * Adds retained history rows the snapshot no longer lists. Live snapshot rows
 * always win; history only fills identities that would otherwise be invisible,
 * and only when the page was read at the source's own revision.
 */
export function withHistory<T extends UserRows>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  if (!entries || entries.tableRevision !== source.tableRevision) return source;
  // A draft never carries occupancy, so retained family entries join as settings.
  const retainedSettings = entries.modelConcurrency?.map(
    ({ adapter, provider, model, limit }) => ({ adapter, provider, model, limit }),
  );
  return {
    ...source,
    profiles: addMissing(source.profiles, entries.profiles),
    preferenceOverrides: addMissing(source.preferenceOverrides, entries.preferenceOverrides),
    familyPreferences: addMissingFamilies(source.familyPreferences, entries.familyPreferences),
    familyAnnotations: addMissingFamilies(source.familyAnnotations, entries.familyAnnotations),
    modelConcurrency: addMissingFamilies(source.modelConcurrency ?? [], retainedSettings),
  };
}

/** Full console view: human draft (or snapshot) plus retained history rows. */
export function historyView<T extends UserRows
  & { cards: Card[]; preferences: Preference[]; sampleCounts?: Record<string, number> }>(
  source: T,
  entries: HistoryEntries | null | undefined,
): T {
  // A page from another revision is stale cache, not evidence about this table.
  if (!entries || entries.tableRevision !== source.tableRevision) return source;
  const base = withHistory(source, entries);
  // Snapshot cards and counts are authoritative for the identities they cover.
  const counts = { ...(entries.sampleCounts ?? {}), ...(source.sampleCounts ?? {}) };
  // Retained family entries keep their recorded occupancy for the read-only view.
  return { ...base, cards: addMissing(source.cards, entries.cards ?? []), sampleCounts: counts,
    preferences: addMissing(source.preferences, entries.preferences),
    modelConcurrency: addMissingFamilies(source.modelConcurrency ?? [], entries.modelConcurrency) };
}

/**
 * Retained rows that may still be added to an existing draft. A retained row
 * whose identity the draft still holds adds nothing, and a row the draft no
 * longer holds while the baseline does is a deliberate removal (for example a
 * cleared family preference or an override set back to "跟随家族") that
 * retained history must never resurrect. Identities neither the draft nor the
 * baseline ever knew are still filled in.
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
  const familyAdditions = <T extends ModelFamily>(
    rows: T[] | undefined,
    current: ModelFamily[],
    known: ModelFamily[],
  ): T[] | undefined => {
    if (!rows) return rows;
    const inDraft = new Set(current.map(familyKey));
    const inBaseline = new Set(known.map(familyKey));
    return rows.filter((row) => inDraft.has(familyKey(row)) || !inBaseline.has(familyKey(row)));
  };
  return {
    ...entries,
    profiles: additions(entries.profiles, draft.profiles, baseline.profiles),
    preferenceOverrides: additions(entries.preferenceOverrides, draft.preferenceOverrides, baseline.preferenceOverrides),
    familyPreferences: familyAdditions(entries.familyPreferences, draft.familyPreferences, baseline.familyPreferences),
    familyAnnotations: familyAdditions(entries.familyAnnotations, draft.familyAnnotations, baseline.familyAnnotations),
    modelConcurrency: familyAdditions(entries.modelConcurrency, draft.modelConcurrency, baseline.modelConcurrency),
  };
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
/** Family default patch; `mode: null` clears the default. */
export type FamilyPreferenceChangePatch = ModelFamily & {
  mode: PreferenceMode | null;
  reason: string;
};
/** Effort override patch; `mode: null` deletes the override (follow the family). */
export type PreferenceChangePatch = {
  profileId: string;
  mode: OverrideMode | null;
  reason: string;
};
/** Family note patch; `text: ""` clears the note. */
export type FamilyAnnotationChangePatch = ModelFamily & { text: string };

export type UserPolicyPublication = {
  commandId: string;
  writerId: string;
  generation: number;
  writerToken: string;
  expectedRevision: number;
  profileSettings?: ProfileSettingPatch[];
  familyPreferenceChanges?: FamilyPreferenceChangePatch[];
  preferenceChanges?: PreferenceChangePatch[];
  familyAnnotationChanges?: FamilyAnnotationChangePatch[];
  configuration?: Partial<Pick<Configuration, "fastRouterProfileId" | "reviewRouterProfileId" | "defaultRoutingMode" | "routingBudget">>;
  /** Per-family limit patches; the board refuses any `active` occupancy here. */
  modelConcurrency?: ModelConcurrencySetting[];
};

type UserEditable = Pick<
  Draft,
  "profiles" | "familyPreferences" | "preferenceOverrides" | "familyAnnotations" | "configuration" | "modelConcurrency"
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
 * Family default patches. A removed default is `mode: null`; a changed reason
 * rides along with its mode, and a trailing empty reason is a deliberate clear.
 */
export function familyPreferenceChanges(
  baseline: Draft,
  draft: Draft,
): FamilyPreferenceChangePatch[] {
  const before = new Map(baseline.familyPreferences.map((p) => [familyKey(p), p]));
  const after = new Map(draft.familyPreferences.map((p) => [familyKey(p), p]));
  const changed: FamilyPreferenceChangePatch[] = [];
  for (const key of new Set([...before.keys(), ...after.keys()])) {
    const previous = before.get(key);
    const next = after.get(key);
    if (previous?.mode === next?.mode && (previous?.reason ?? "") === (next?.reason ?? "")) continue;
    const family = familyOf((next ?? previous)!);
    changed.push(next
      ? { ...family, mode: next.mode, reason: next.reason }
      : { ...family, mode: null, reason: "" });
  }
  return changed.sort(byFamily);
}

/**
 * Effort override patches. A deleted override is `mode: null` (the effort
 * follows its family again); `none` is an explicit no-preference override.
 */
export function preferenceChanges(
  baseline: Draft,
  draft: Draft,
): PreferenceChangePatch[] {
  const before = new Map(baseline.preferenceOverrides.map((p) => [p.profileId, p]));
  const after = new Map(draft.preferenceOverrides.map((p) => [p.profileId, p]));
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

export const ROUTER_FIELDS = ["fastRouterProfileId", "reviewRouterProfileId", "defaultRoutingMode"] as const;

export function routerFieldChanged(baseline: Draft, draft: Draft, field: typeof ROUTER_FIELDS[number]): boolean {
  return baseline.configuration[field] !== draft.configuration[field];
}

export function routingBudgetChanged(baseline: Draft, draft: Draft): boolean {
  return (baseline.configuration.routingBudget ?? "standard") !== (draft.configuration.routingBudget ?? "standard");
}

export function configurationChanged(baseline: Draft, draft: Draft): boolean {
  return ROUTER_FIELDS.some(field => routerFieldChanged(baseline, draft, field)) || routingBudgetChanged(baseline, draft);
}

/**
 * `user_policy_publish` payload: identity, expected revision and only the dirty
 * user patches. An unchanged field is omitted so the board keeps its value; no
 * provider/model/effort/available/catalog field, no card, no effective
 * preference and no occupancy is ever included.
 */
export function publication(
  baseline: Draft,
  draft: Draft,
  grant: WriterGrant,
  commandId: string,
): UserPolicyPublication {
  const settings = profileSettings(baseline, draft);
  const families = familyPreferenceChanges(baseline, draft);
  const overrides = preferenceChanges(baseline, draft);
  const notes = familyAnnotationChanges(baseline, draft);
  const concurrency = modelConcurrencyChanges(baseline, draft);
  return {
    commandId,
    writerId: grant.writerId,
    generation: grant.generation,
    writerToken: grant.writerToken,
    expectedRevision: draft.tableRevision,
    ...(settings.length ? { profileSettings: settings } : {}),
    ...(families.length ? { familyPreferenceChanges: families } : {}),
    ...(overrides.length ? { preferenceChanges: overrides } : {}),
    ...(notes.length ? { familyAnnotationChanges: notes } : {}),
    ...(concurrency.length ? { modelConcurrency: concurrency } : {}),
    ...(configurationChanged(baseline, draft)
      ? { configuration: {
          ...Object.fromEntries(ROUTER_FIELDS.filter(field => routerFieldChanged(baseline, draft, field))
            .map(field => [field, draft.configuration[field]])),
          ...(routingBudgetChanged(baseline, draft) ? { routingBudget: draft.configuration.routingBudget ?? "standard" } : {}),
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
    familyPreferences: [...draft.familyPreferences]
      .map((p) => ({ key: familyKey(p), mode: p.mode, reason: p.reason }))
      .sort((a, b) => a.key.localeCompare(b.key)),
    preferenceOverrides: [...draft.preferenceOverrides]
      .map(({ profileId, mode, reason }) => ({ profileId, mode, reason }))
      .sort(byProfileId),
    familyAnnotations: draft.familyAnnotations
      .filter((a) => a.text)
      .map((a) => ({ key: familyKey(a), text: a.text }))
      .sort((a, b) => a.key.localeCompare(b.key)),
    configuration: {
      fastRouterProfileId: draft.configuration.fastRouterProfileId,
      reviewRouterProfileId: draft.configuration.reviewRouterProfileId,
      defaultRoutingMode: draft.configuration.defaultRoutingMode,
      routingBudget: draft.configuration.routingBudget ?? "standard",
    },
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
    + familyPreferenceChanges(baseline, draft).length
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
  field: "enabled" | "familyPreference" | "preference" | "familyAnnotation" | "configuration" | "defaultRoutingMode" | "routingBudget" | "modelConcurrency";
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
  familyPreference: "家族偏好",
  preference: "档位偏好",
  familyAnnotation: "家族备注",
  configuration: "Router",
  defaultRoutingMode: "默认路由模式",
  routingBudget: "路由预算",
  modelConcurrency: "并发上限",
};

const familyText = (family: ModelFamily) => `${family.adapter}/${family.provider}/${family.model}`;

function conflictSubject(field: RebaseConflict["field"], profileId: string): string {
  if (field === "configuration") return `Router${profileId ? ` ${profileId}` : "（空）"}`;
  if (field === "defaultRoutingMode") return "默认路由模式";
  if (field === "routingBudget") return "路由预算";
  if (field === "familyPreference" || field === "familyAnnotation" || field === "modelConcurrency") {
    return `模型家族 ${profileId} 的${FIELD_NAMES[field]}`;
  }
  return `配置 ${profileId} 的${FIELD_NAMES[field]}`;
}

/** Three-way merge of one field: null means a real competing value. */
function merge<T>(old: T, wanted: T, fresh: T, same: (a: T, b: T) => boolean = Object.is): { value: T } | null {
  if (same(old, wanted)) return { value: fresh };
  if (same(fresh, old) || same(fresh, wanted)) return { value: wanted };
  return null;
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
 * whose profile (or, for a family field, every profile of the family) is
 * missing from the bounded snapshot stays unresolved until a retained page read
 * at the refreshed revision supplies the missing row; cached rows from an older
 * revision are never used to mint a new baseline.
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
        : `${subject} 已在 V${snapshot.tableRevision} 更改${detail}；草稿已保留，请重新加载核对。`,
    });
  };
  const freshProfiles = new Map(nextBaseline.profiles.map((p) => [p.profileId, p]));
  const freshFamilies = new Set(nextBaseline.profiles.map(familyKey));

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

  // Family defaults: mode and reason are separate fields, so another writer's
  // reason survives a mode change of ours.
  const familyMerges = new Map<string, FamilyPreference>();
  const familyRemovals = new Set<string>();
  for (const change of familyPreferenceChanges(baseline, draft)) {
    const key = familyKey(change);
    if (!freshFamilies.has(key)) {
      fail("unread", "familyPreference", familyText(change));
      continue;
    }
    const oldRow = baseline.familyPreferences.find((p) => familyKey(p) === key);
    const draftRow = draft.familyPreferences.find((p) => familyKey(p) === key);
    const freshRow = nextBaseline.familyPreferences.find((p) => familyKey(p) === key);
    const mode = merge(oldRow?.mode, draftRow?.mode, freshRow?.mode);
    if (!mode) {
      fail("changed", "familyPreference", familyText(change), "（模式）");
      continue;
    }
    const reason = merge(oldRow?.reason ?? "", draftRow?.reason ?? "", freshRow?.reason ?? "");
    if (!reason) {
      fail("changed", "familyPreference", familyText(change), "（理由）");
      continue;
    }
    if (mode.value === undefined) familyRemovals.add(key);
    else familyMerges.set(key, { ...familyOf(change), mode: mode.value, reason: reason.value });
  }

  // Effort overrides, merged the same way per profile.
  const overrideMerges = new Map<string, PreferenceOverride>();
  const overrideRemovals = new Set<string>();
  for (const change of preferenceChanges(baseline, draft)) {
    const profileId = change.profileId;
    if (!freshProfiles.has(profileId)) {
      fail("unread", "preference", profileId);
      continue;
    }
    const oldRow = baseline.preferenceOverrides.find((p) => p.profileId === profileId);
    const draftRow = draft.preferenceOverrides.find((p) => p.profileId === profileId);
    const freshRow = nextBaseline.preferenceOverrides.find((p) => p.profileId === profileId);
    const mode = merge(oldRow?.mode, draftRow?.mode, freshRow?.mode);
    if (!mode) {
      fail("changed", "preference", profileId, "（模式）");
      continue;
    }
    const reason = merge(oldRow?.reason ?? "", draftRow?.reason ?? "", freshRow?.reason ?? "");
    if (!reason) {
      fail("changed", "preference", profileId, "（理由）");
      continue;
    }
    if (mode.value === undefined) overrideRemovals.add(profileId);
    else overrideMerges.set(profileId, { profileId, mode: mode.value, reason: reason.value });
  }

  // Family notes: the text is the field; an already-equal refreshed value wins.
  const mergedNotes = new Map<string, FamilyAnnotationChangePatch>();
  for (const change of familyAnnotationChanges(baseline, draft)) {
    const key = familyKey(change);
    if (!freshFamilies.has(key)) {
      fail("unread", "familyAnnotation", familyText(change));
      continue;
    }
    const oldText = baseline.familyAnnotations.find((a) => familyKey(a) === key)?.text ?? "";
    const freshText = nextBaseline.familyAnnotations.find((a) => familyKey(a) === key)?.text ?? "";
    if (freshText === oldText || freshText === change.text) mergedNotes.set(key, change);
    else fail("changed", "familyAnnotation", familyText(change));
  }

  const mergedRouting = { ...nextBaseline.configuration };
  for (const field of ROUTER_FIELDS) {
    if (!routerFieldChanged(baseline, draft, field)) continue;
    const old = baseline.configuration[field];
    const wanted = draft.configuration[field];
    const fresh = nextBaseline.configuration[field];
    if (fresh === old || fresh === wanted) {
      // Each field is independently fenced by the configuration revision.
      Object.assign(mergedRouting, { [field]: wanted });
    } else fail("changed", field === "defaultRoutingMode" ? "defaultRoutingMode" : "configuration", String(wanted ?? ""));
  }

  let routingBudget = nextBaseline.configuration.routingBudget ?? "standard";
  if (routingBudgetChanged(baseline, draft)) {
    const old = baseline.configuration.routingBudget ?? "standard";
    const wanted = draft.configuration.routingBudget ?? "standard";
    if (routingBudget === old || routingBudget === wanted) routingBudget = wanted;
    else fail("changed", "routingBudget", "", `（现为 ${routingBudget}）`);
  }

  // Family concurrency limits: the key is the exact adapter/provider/model
  // tuple, so effort variants and occupancy never take part in the comparison.
  const mergedConcurrency = new Map<string, ModelConcurrencySetting>();
  const baselineLimits = new Map(
    baseline.modelConcurrency.map((entry) => [familyKey(entry), entry.limit]),
  );
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
    const old = baselineLimits.get(key);
    if (freshEntry.limit === old || freshEntry.limit === change.limit) {
      mergedConcurrency.set(key, change);
    } else {
      fail("changed", "modelConcurrency", familyText(change), `（现为 ${freshEntry.limit}）`);
    }
  }

  if (conflicts.length) return { draft, baseline, conflicts };

  const familyPreferences = nextBaseline.familyPreferences.filter(
    (p) => !familyMerges.has(familyKey(p)) && !familyRemovals.has(familyKey(p)),
  );
  for (const preference of familyMerges.values()) familyPreferences.push(preference);
  const preferenceOverrides = nextBaseline.preferenceOverrides.filter(
    (p) => !overrideMerges.has(p.profileId) && !overrideRemovals.has(p.profileId),
  );
  for (const override of overrideMerges.values()) preferenceOverrides.push(override);
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
      familyPreferences,
      preferenceOverrides,
      familyAnnotations,
      configuration: { ...mergedRouting, routingBudget },
      modelConcurrency: nextBaseline.modelConcurrency.map((entry) =>
        mergedConcurrency.get(familyKey(entry)) ?? entry),
    },
    conflicts,
  };
}
