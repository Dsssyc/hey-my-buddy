import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import {
  changeCount,
  configurationChanged,
  draftDiffers,
  effectivePreferences,
  familyAnnotationChanges,
  familyAnnotationText,
  familyPreferenceChanges,
  historyView,
  makeDraft,
  modelConcurrencyChanges,
  preferenceChanges,
  profileSettings,
  publication,
  rebaseDraft,
  retainedAdditions,
  setConcurrencyLimit,
  setFamilyAnnotation,
  setFamilyPreference,
  setPreferenceOverride,
  withHistory,
} from "./draft";
import { blockingIssues } from "./policy";
import type { HistoryEntries } from "./draft";
import type { Draft, FamilyAnnotation, Profile, Snapshot, WriterGrant } from "./types";

const flash = {
  profileId: "flash-off",
  label: "Flash",
  adapter: "dsh",
  provider: "deepseek-official",
  model: "deepseek-flash",
  effort: "off",
  available: true,
  enabled: true,
  capabilities: ["execution:dsh", "decision"],
  contextWindow: 1000000,
  description: "",
  source: "catalog",
} satisfies Profile;
const flashHigh = { ...flash, profileId: "flash-high", effort: "high" } satisfies Profile;
const flashFamily = { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
/** A configuration that is still enabled but no longer listed by the catalog. */
const retired = {
  ...flash,
  profileId: "retired",
  model: "retired-model",
  available: false,
  unavailableReason: "provider paused",
} satisfies Profile;
const retiredFamily = { adapter: "dsh", provider: "deepseek-official", model: "retired-model" };
const note = (family: typeof flashFamily, text: string, revision = 1): FamilyAnnotation =>
  ({ ...family, text, revision, updatedAt: null });
const grant = (tableRevision = 4): WriterGrant => ({
  writerId: "w",
  generation: 2,
  writerToken: "private",
  phase: "writing",
  state: "active",
  expiresAt: "",
  tableRevision,
});
const snapshot = {
  tableRevision: 3,
  profiles: [flash],
  cards: [
    {
      profileId: "flash-off",
      revision: 2,
      summary: "自动评价",
      strengths: ["稳定"],
      limitations: [],
      risks: [],
      evidenceIds: ["ev-1"],
      updatedAt: null,
    },
  ],
  preferences: [],
  familyPreferences: [],
  preferenceOverrides: [],
  familyAnnotations: [],
  configuration: {
    revision: 2,
    routerProfileId: "flash-off", defaultRoutingMode: "review" as const, routingBudget: "standard",
  },
  sampleCounts: { "flash-off": 7 },
  modelConcurrency: [
    { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 },
  ],
} as unknown as Snapshot;

describe("editing a published snapshot", () => {
  it("keeps the server snapshot and publication version unchanged while editing", () => {
    const draft = makeDraft(snapshot);
    draft.profiles[0].enabled = false;
    expect(snapshot.profiles[0].enabled).toBe(true);
    expect(publication(makeDraft(snapshot), draft, grant(), "c").expectedRevision).toBe(3);
  });

  it("publishes only the dirty user patches, never a table, a card or an effective preference", () => {
    const baseline = makeDraft(snapshot);
    let draft = setFamilyAnnotation(baseline, flash, "我的使用备注");
    draft = setFamilyPreference(draft, flash, "prefer", "速度快");
    draft = setPreferenceOverride(draft, "flash-off", "exclude", "太贵");
    draft = {
      ...draft,
      profiles: draft.profiles.map((p) => ({ ...p, enabled: false })),
    };
    const patch = publication(baseline, draft, grant(), "c");
    expect(patch).toEqual({
      commandId: "c",
      writerId: "w",
      generation: 2,
      writerToken: "private",
      expectedRevision: 3,
      profileSettings: [{ profileId: "flash-off", enabled: false }],
      familyPreferenceChanges: [{ ...flashFamily, mode: "prefer", reason: "速度快" }],
      preferenceChanges: [{ profileId: "flash-off", mode: "exclude", reason: "太贵" }],
      familyAnnotationChanges: [{ ...flashFamily, text: "我的使用备注" }],
    });
    const wire = JSON.stringify(patch);
    // Program-owned profile facts, automatic cards and the removed per-profile
    // opinion field are not human inputs of the schema 13 contract.
    expect(wire).not.toContain("available");
    expect(wire).not.toContain("summary");
    expect(wire).not.toContain("cards");
    expect(wire).not.toContain("profiles\"");
    expect(wire).not.toContain("annotationChanges\"");
    expect(wire).not.toContain("source");
    expect(patch).not.toHaveProperty("annotationChanges");
    expect(patch).not.toHaveProperty("preferences");
  });

  it("omits every field the draft did not actually change", () => {
    const baseline = makeDraft(snapshot);
    const patch = publication(baseline, makeDraft(snapshot), grant(), "c");
    expect(Object.keys(patch).sort()).toEqual([
      "commandId",
      "expectedRevision",
      "generation",
      "writerId",
      "writerToken",
    ]);
  });

  it("sends a clear for an emptied note, a null family mode and a null override", () => {
    const baseline = {
      ...makeDraft(snapshot),
      familyAnnotations: [note(flashFamily, "旧备注")],
      familyPreferences: [{ ...flashFamily, mode: "prefer" as const, reason: "更快" }],
      preferenceOverrides: [{ profileId: "flash-off", mode: "exclude" as const, reason: "贵" }],
    };
    let draft = setFamilyAnnotation(baseline, flash, "");
    draft = setFamilyPreference(draft, flash, "");
    draft = setPreferenceOverride(draft, "flash-off", "");
    const patch = publication(baseline, draft, grant(), "c");
    expect(patch.familyAnnotationChanges).toEqual([{ ...flashFamily, text: "" }]);
    expect(patch.familyPreferenceChanges).toEqual([{ ...flashFamily, mode: null, reason: "" }]);
    expect(patch.preferenceChanges).toEqual([{ profileId: "flash-off", mode: null, reason: "" }]);
    expect(familyAnnotationChanges(baseline, draft)).toEqual([{ ...flashFamily, text: "" }]);
    expect(familyPreferenceChanges(baseline, draft)).toEqual([{ ...flashFamily, mode: null, reason: "" }]);
    expect(preferenceChanges(baseline, draft)).toEqual([{ profileId: "flash-off", mode: null, reason: "" }]);
  });

  it("publishes an explicit none override distinctly from following the family", () => {
    const baseline = {
      ...makeDraft({ ...snapshot, profiles: [flash, flashHigh] } as Snapshot),
      familyPreferences: [{ ...flashFamily, mode: "prefer" as const, reason: "" }],
    };
    const none = setPreferenceOverride(baseline, "flash-high", "none", "最高档太贵");
    expect(preferenceChanges(baseline, none)).toEqual([
      { profileId: "flash-high", mode: "none", reason: "最高档太贵" },
    ]);
    expect(publication(baseline, none, grant(), "c")).not.toHaveProperty("familyPreferenceChanges");
    // Back to "跟随家族" deletes the override: nothing left to publish.
    expect(preferenceChanges(baseline, setPreferenceOverride(none, "flash-high", ""))).toEqual([]);
  });

  it("keeps a stale pin and a stale Router out of every patch", () => {
    const baseline = makeDraft({
      ...snapshot,
      profiles: [flash, retired],
      preferenceOverrides: [{ profileId: "retired", mode: "pin", reason: "以前固定" }],
      configuration: { revision: 3, routerProfileId: "retired" , defaultRoutingMode: "review" as const, routingBudget: "standard"},
    } as Snapshot);
    const draft = { ...baseline };
    expect(publication(baseline, draft, grant(), "c")).not.toHaveProperty("preferenceChanges");
    expect(publication(baseline, draft, grant(), "c")).not.toHaveProperty("configuration");
    // An unrelated note still publishes while the old settings stay as they are.
    const withNote = setFamilyAnnotation(draft, flash, "只改备注");
    const patch = publication(baseline, withNote, grant(), "c");
    expect(patch.familyAnnotationChanges).toEqual([{ ...flashFamily, text: "只改备注" }]);
    expect(patch).not.toHaveProperty("preferenceChanges");
    expect(patch).not.toHaveProperty("profileSettings");
  });

  it("allows several pins: a new pin never clears another family's or effort's pin", () => {
    const draft = {
      ...makeDraft(snapshot),
      preferenceOverrides: [{ profileId: "a", mode: "pin" as const, reason: "" }],
    };
    const updated = setFamilyPreference(draft, flash, "pin");
    expect(updated.preferenceOverrides).toEqual(draft.preferenceOverrides);
    expect(familyPreferenceChanges(draft, updated)).toEqual([{ ...flashFamily, mode: "pin", reason: "" }]);
    expect(preferenceChanges(draft, updated)).toEqual([]);
  });

  it("detects human edits independently of array order and program facts", () => {
    const baseline = makeDraft(snapshot);
    const reordered: typeof baseline = {
      ...baseline,
      profiles: [
        { ...flash, available: false, label: "目录已改名", contextWindow: null },
      ],
    };
    // A program-side rename or availability change is not a user change.
    expect(draftDiffers(baseline, reordered)).toBe(false);
    expect(changeCount(baseline, reordered)).toBe(0);
    const preferred = setFamilyPreference(baseline, flash, "prefer", "更快");
    expect(draftDiffers(baseline, preferred)).toBe(true);
    expect(changeCount(baseline, preferred)).toBe(1);
    const noted = setFamilyAnnotation(preferred, flash, "备注");
    expect(changeCount(baseline, noted)).toBe(2);
    // An emptied note that never existed is not content.
    expect(draftDiffers(baseline, setFamilyAnnotation(baseline, flash, ""))).toBe(false);
    const disabled = {
      ...baseline,
      profiles: [{ ...baseline.profiles[0], enabled: false }],
    };
    expect(changeCount(baseline, disabled)).toBe(1);
    expect(configurationChanged(baseline, disabled)).toBe(false);
    const reconfigured: typeof disabled = {
      ...disabled,
      configuration: { ...disabled.configuration!, routerProfileId: "other"},
    };
    expect(configurationChanged(baseline, reconfigured)).toBe(true);
    expect(changeCount(baseline, reconfigured)).toBe(2);
    // An invalid limit still counts, so the save bar never shows "0 项".
    expect(changeCount(baseline, setConcurrencyLimit(baseline, flash, NaN))).toBe(1);
    // A reordered copy of the same overrides is not a change.
    const overridden = {
      ...baseline,
      preferenceOverrides: [
        { profileId: "a", mode: "prefer" as const, reason: "1" },
        { profileId: "b", mode: "none" as const, reason: "2" },
      ],
    };
    expect(draftDiffers(overridden, { ...overridden, preferenceOverrides: [...overridden.preferenceOverrides].reverse() })).toBe(false);
  });

  it("rebases the draft onto refreshed directory facts without losing user patches", () => {
    const baseline = makeDraft(snapshot);
    let draft = setFamilyAnnotation(baseline, flash, "保留的备注");
    draft = setFamilyPreference(draft, flash, "prefer", "更快");
    draft = setPreferenceOverride(draft, "flash-off", "none");
    draft = {
      ...draft,
      profiles: draft.profiles.map((p) => ({ ...p, enabled: false })),
    };
    const discovered = {
      ...flash,
      profileId: "flash-max",
      effort: "max",
      enabled: false,
    } satisfies Profile;
    const refreshed = {
      ...snapshot,
      tableRevision: 4,
      // The program renamed the existing profile and added a new configuration.
      profiles: [{ ...flash, label: "目录新名称", available: false }, discovered],
    } as unknown as Snapshot;
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.baseline.tableRevision).toBe(4);
    expect(adopted.draft.tableRevision).toBe(4);
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.label).toBe("目录新名称");
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-max")).toBeTruthy();
    // Program availability is adopted; the user's enablement and note are replayed.
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.available).toBe(false);
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.enabled).toBe(false);
    expect(familyAnnotationText(adopted.draft, flash)).toBe("保留的备注");
    const patch = publication(adopted.baseline, adopted.draft, grant(4), "c");
    expect(patch.expectedRevision).toBe(4);
    expect(patch.familyAnnotationChanges).toEqual([{ ...flashFamily, text: "保留的备注" }]);
    expect(patch.familyPreferenceChanges).toEqual([{ ...flashFamily, mode: "prefer", reason: "更快" }]);
    expect(patch.preferenceChanges).toEqual([{ profileId: "flash-off", mode: "none", reason: "" }]);
    expect(patch.profileSettings).toEqual([{ profileId: "flash-off", enabled: false }]);
    expect(profileSettings(baseline, draft)).toEqual([
      { profileId: "flash-off", enabled: false },
    ]);
  });
});

describe("effective preferences", () => {
  it("mirrors the view: an override wins, none hides the family default, others follow the family", () => {
    const flashMax = { ...flash, profileId: "flash-max", effort: "max" };
    const rows = effectivePreferences({
      profiles: [flash, flashHigh, flashMax, retired],
      familyPreferences: [{ ...flashFamily, mode: "prefer", reason: "家族默认" }],
      preferenceOverrides: [
        { profileId: "flash-high", mode: "exclude", reason: "太贵" },
        { profileId: "flash-max", mode: "none", reason: "" },
        { profileId: "retired", mode: "pin", reason: "旧" },
      ],
    });
    expect(rows).toEqual([
      { profileId: "flash-off", mode: "prefer", reason: "家族默认", source: "family" },
      { profileId: "flash-high", mode: "exclude", reason: "太贵", source: "override" },
      { profileId: "retired", mode: "pin", reason: "旧", source: "override" },
    ]);
  });
});

describe("field-level three-way rebase", () => {
  /** A bounded snapshot that omits every unavailable identity. */
  const bounded = { ...snapshot, profiles: [flash] } as unknown as Snapshot;
  const retiredEnabled = { ...retired, enabled: true } satisfies Profile;
  const retained = (revision: number, entries: Partial<HistoryEntries> = {}): HistoryEntries => ({
    tableRevision: revision,
    ...entries,
  });

  it("refuses to overlay a draft onto another writer's newer note", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      familyAnnotations: [note(flashFamily, "A")],
    };
    const draft = setFamilyAnnotation(baseline, flash, "B");
    // Another writer published C (v2); a program discovery then published v3.
    const discovered = {
      ...bounded,
      tableRevision: 3,
      familyAnnotations: [note(flashFamily, "C", 2)],
    } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toHaveLength(1);
    expect(adopted.conflicts[0]).toMatchObject({
      kind: "changed",
      field: "familyAnnotation",
      profileId: "dsh/deepseek-official/deepseek-flash",
    });
    // The original draft and expectedRevision stay; B is never saved under v3.
    expect(adopted.draft).toEqual(draft);
    expect(adopted.baseline).toEqual(baseline);
    expect(familyAnnotationText(adopted.draft, flash)).toBe("B");
    expect(familyAnnotationText(adopted.baseline, flash)).toBe("A");
    expect(publication(adopted.baseline, adopted.draft, grant(1), "c").expectedRevision).toBe(1);
  });

  it("merges a note only when the refreshed value still equals the old baseline or the intent", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      familyAnnotations: [note(flashFamily, "A")],
    };
    const draft = setFamilyAnnotation(baseline, flash, "B");
    const unchanged = rebaseDraft(draft, baseline, { ...bounded, tableRevision: 3, familyAnnotations: [note(flashFamily, "A", 2)] } as Snapshot);
    expect(unchanged.conflicts).toEqual([]);
    expect(familyAnnotationText(unchanged.draft, flash)).toBe("B");
    expect(unchanged.draft.tableRevision).toBe(3);
    const already = rebaseDraft(draft, baseline, { ...bounded, tableRevision: 3, familyAnnotations: [note(flashFamily, "B", 2)] } as Snapshot);
    expect(already.conflicts).toEqual([]);
    expect(familyAnnotationText(already.draft, flash)).toBe("B");
  });

  it("keeps an enabled-intent edit unresolved while its profile has no fresh row", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      profiles: [flash, retiredEnabled],
    };
    const draft = {
      ...baseline,
      profiles: baseline.profiles.map((p) =>
        p.profileId === "retired" ? { ...p, enabled: false } : p,
      ),
    };
    const discovered = { ...bounded, tableRevision: 3 } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toHaveLength(1);
    expect(adopted.conflicts[0]).toMatchObject({
      kind: "unread",
      field: "enabled",
      profileId: "retired",
    });
    expect(adopted.draft).toEqual(draft);
    expect(adopted.draft.tableRevision).toBe(1);

    // A cached page from the old revision must not mint a baseline at the new revision.
    const withStale = rebaseDraft(draft, baseline, discovered, retained(1, { profiles: [retiredEnabled] }));
    expect(withStale.conflicts[0].kind).toBe("unread");
    expect(withStale.draft.tableRevision).toBe(1);
    expect(withStale.baseline.tableRevision).toBe(1);

    // A fresh retained page at the discovered revision resolves and applies the edit.
    const resolved = rebaseDraft(draft, baseline, discovered, retained(3, { profiles: [retiredEnabled] }));
    expect(resolved.conflicts).toEqual([]);
    expect(resolved.draft.tableRevision).toBe(3);
    expect(resolved.draft.profiles.find((p) => p.profileId === "retired")!.enabled).toBe(false);
    expect(resolved.draft.profiles.some((p) => p.profileId === "flash-off")).toBe(true);
  });

  it("keeps a family-level edit unread until a profile of that family is readable", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      profiles: [flash, retiredEnabled],
      familyAnnotations: [note(retiredFamily, "A")],
    };
    const draft = setFamilyAnnotation(setFamilyPreference(baseline, retired, "exclude"), retired, "B");
    const discovered = { ...bounded, tableRevision: 3 } as Snapshot;
    const unread = rebaseDraft(draft, baseline, discovered);
    expect(unread.conflicts.map((c) => [c.kind, c.field])).toEqual([
      ["unread", "familyPreference"],
      ["unread", "familyAnnotation"],
    ]);
    // The fresh page carries another writer's note: a real conflict now.
    const changed = rebaseDraft(draft, baseline, discovered, retained(3, {
      profiles: [retiredEnabled],
      familyAnnotations: [note(retiredFamily, "C", 2)],
    }));
    expect(changed.conflicts).toEqual([expect.objectContaining({ kind: "changed", field: "familyAnnotation" })]);
    expect(familyAnnotationText(changed.draft, retired)).toBe("B");
    expect(changed.draft.tableRevision).toBe(1);
  });

  it("merges a changed family mode without overwriting another writer's reason", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      familyPreferences: [{ ...flashFamily, mode: "prefer" as const, reason: "old" }],
    };
    const draft = setFamilyPreference(baseline, flash, "pin", "old");
    const discovered = {
      ...bounded,
      tableRevision: 3,
      familyPreferences: [{ ...flashFamily, mode: "pin" as const, reason: "他人理由" }],
    } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.draft.familyPreferences).toEqual([{ ...flashFamily, mode: "pin", reason: "他人理由" }]);
  });

  it("treats an override another writer removed as a conflict, not a resurrection", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      preferenceOverrides: [{ profileId: "flash-off", mode: "prefer" as const, reason: "old" }],
    };
    const draft = setPreferenceOverride(baseline, "flash-off", "prefer", "mine");
    const adopted = rebaseDraft(draft, baseline, { ...bounded, tableRevision: 3 } as Snapshot);
    expect(adopted.conflicts[0]).toMatchObject({ kind: "changed", field: "preference" });
    expect(adopted.draft.tableRevision).toBe(1);
  });

  it("keeps a changed Router when the fresh table still holds the old value", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      profiles: [flash, retiredEnabled],
      configuration: { revision: 1, routerProfileId: "flash-off", defaultRoutingMode: "review" as const, routingBudget: "standard" as const },
    };
    const draft = {
      ...baseline,
      configuration: { ...baseline.configuration!, routerProfileId: "retired"},
    };
    const discovered = { ...bounded, tableRevision: 3 } as Snapshot;
    const clean = rebaseDraft(draft, baseline, discovered);
    expect(clean.conflicts).toEqual([]);
    expect(clean.draft.configuration!.routerProfileId).toBe("retired");
    // Another writer changed the Router first: the old revision is retained.
    const conflicted = rebaseDraft(draft, baseline, {
      ...discovered,
      configuration: { revision: 2, routerProfileId: "gone:model:off" , defaultRoutingMode: "review" as const, routingBudget: "standard"},
    } as Snapshot);
    expect(conflicted.conflicts[0]).toMatchObject({ kind: "changed", field: "configuration" });
    expect(conflicted.draft.configuration!.routerProfileId).toBe("retired");
    expect(conflicted.draft.tableRevision).toBe(1);
  });

  it("bounds retained rows to the revision they were read at", () => {
    const current = makeDraft({ ...bounded, tableRevision: 3 } as Snapshot);
    const entries = retained(2, {
      profiles: [retiredEnabled],
      familyAnnotations: [note(retiredFamily, "旧备注")],
    });
    const merged = withHistory(current, entries);
    expect(merged.profiles.map((p) => p.profileId)).toEqual(["flash-off"]);
    expect(merged.familyAnnotations).toEqual([]);
    const view = historyView({ ...snapshot, tableRevision: 3 } as Snapshot, entries);
    expect(view.profiles.some((p) => p.profileId === "retired")).toBe(false);
    const matched = withHistory(current, retained(3, { profiles: [retiredEnabled], familyAnnotations: [note(retiredFamily, "旧备注")] }));
    expect(matched.profiles.some((p) => p.profileId === "retired")).toBe(true);
    expect(familyAnnotationText(matched, retired)).toBe("旧备注");
  });
});

describe("retained additions never resurrect a removal", () => {
  const revision = 3;
  const current = makeDraft({ ...snapshot, tableRevision: revision } as Snapshot);
  const pinned = {
    ...current,
    preferenceOverrides: [{ profileId: "flash-off", mode: "pin" as const, reason: "旧理由" }],
    familyPreferences: [{ ...flashFamily, mode: "exclude" as const, reason: "" }],
  };

  it("keeps a removed override and family default removed when the published rows come back", () => {
    const draft = { ...pinned, preferenceOverrides: [], familyPreferences: [] };
    const page: HistoryEntries = {
      tableRevision: revision,
      preferenceOverrides: [{ profileId: "flash-off", mode: "pin", reason: "旧理由" }],
      familyPreferences: [{ ...flashFamily, mode: "exclude", reason: "" }],
    };
    // A plain merge would erase the removal, so the adoption path filters first.
    expect(withHistory(draft, page).preferenceOverrides).toHaveLength(1);
    const adopted = withHistory(draft, retainedAdditions(draft, pinned, page));
    expect(adopted.preferenceOverrides).toHaveLength(0);
    expect(adopted.familyPreferences).toHaveLength(0);
    // The changes stay publishable as real removals.
    expect(preferenceChanges(pinned, adopted)).toEqual([{ profileId: "flash-off", mode: null, reason: "" }]);
    expect(familyPreferenceChanges(pinned, adopted)).toEqual([{ ...flashFamily, mode: null, reason: "" }]);
  });

  it("still fills identities neither the draft nor the baseline knows", () => {
    const draft = { ...current, preferenceOverrides: [] };
    const page: HistoryEntries = {
      tableRevision: revision,
      preferenceOverrides: [{ profileId: "new-profile", mode: "prefer", reason: "初评" }],
      familyAnnotations: [note(retiredFamily, "历史备注")],
    };
    const additions = retainedAdditions(draft, current, page)!;
    expect(withHistory(draft, additions).preferenceOverrides).toEqual([
      { profileId: "new-profile", mode: "prefer", reason: "初评" },
    ]);
    expect(familyAnnotationText(withHistory(draft, additions), retired)).toBe("历史备注");
  });

  it("ignores a page from another revision entirely", () => {
    const draft = { ...pinned, preferenceOverrides: [] };
    const page: HistoryEntries = {
      tableRevision: revision - 1,
      preferenceOverrides: [{ profileId: "flash-off", mode: "pin", reason: "旧理由" }],
    };
    expect(retainedAdditions(draft, pinned, page)).toBeNull();
  });
});

describe("model family concurrency", () => {
  const flashLimit = (draft: Draft): number | null =>
    draft.modelConcurrency.find((entry) => entry.model === "deepseek-flash")?.limit ?? null;
  const entry = (limit: number) => ({
    adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit, active: 0,
  });
  const basedAt = (revision: number) => makeDraft({ ...snapshot, tableRevision: revision } as unknown as Snapshot);
  const tableAt = (revision: number, limit: number) =>
    ({ ...snapshot, tableRevision: revision, modelConcurrency: [entry(limit)] }) as unknown as Snapshot;

  it("publishes only changed family limits and never the occupancy", () => {
    const baseline = makeDraft(snapshot);
    expect(flashLimit(baseline)).toBe(2);
    expect(modelConcurrencyChanges(baseline, baseline)).toEqual([]);
    const draft = setConcurrencyLimit(baseline, flash, 6);
    expect(modelConcurrencyChanges(baseline, draft)).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const patch = publication(baseline, draft, grant(), "c");
    expect(patch.modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const wire = JSON.stringify(patch);
    // Occupancy is observation and the profile identity never rides along.
    expect(wire).not.toContain("active");
    expect(wire).not.toContain("flash-off");
    // An unrelated edit keeps the concurrency patch out entirely.
    expect(publication(baseline, setFamilyAnnotation(baseline, flash, "只改备注"), grant(), "c"))
      .not.toHaveProperty("modelConcurrency");
  });

  it("never publishes a limit outside the 1–32 integer range", () => {
    const baseline = makeDraft(snapshot);
    for (const limit of [0, 33, 2.5]) {
      const invalid = {
        ...baseline,
        modelConcurrency: [{ ...baseline.modelConcurrency[0], limit }],
      };
      expect(modelConcurrencyChanges(baseline, invalid)).toEqual([]);
    }
  });

  it("treats refreshed occupancy as observation, not a user change", () => {
    const baseline = basedAt(1);
    const draft = basedAt(1);
    const refreshed = {
      ...snapshot,
      tableRevision: 3,
      modelConcurrency: [{ ...entry(2), active: 9 }],
    } as unknown as Snapshot;
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(flashLimit(adopted.draft)).toBe(2);
    expect(draftDiffers(adopted.baseline, adopted.draft)).toBe(false);
    // The draft shape drops occupancy, so it can never be published.
    expect("active" in adopted.draft.modelConcurrency[0]).toBe(false);
  });

  it("retains invalid local limits during a rebase instead of discarding the input", () => {
    const baseline = basedAt(1);
    for (const limit of [33, NaN]) {
      const draft = setConcurrencyLimit(baseline, flash, limit);
      const rebased = rebaseDraft(draft, baseline, tableAt(3, 2));
      expect(rebased.conflicts.some(conflict => conflict.field === "modelConcurrency")).toBe(true);
      expect(Object.is(flashLimit(rebased.draft), limit)).toBe(true);
    }
  });

  it("rebases a changed limit three ways against the refreshed table", () => {
    const baseline = basedAt(1);
    const draft = setConcurrencyLimit(baseline, flash, 6);
    const clean = rebaseDraft(draft, baseline, tableAt(3, 2));
    expect(clean.conflicts).toEqual([]);
    expect(flashLimit(clean.draft)).toBe(6);
    expect(flashLimit(clean.baseline)).toBe(2);
    expect(clean.draft.tableRevision).toBe(3);
    const applied = rebaseDraft(draft, baseline, tableAt(3, 6));
    expect(applied.conflicts).toEqual([]);
    expect(flashLimit(applied.draft)).toBe(6);
    const conflicted = rebaseDraft(draft, baseline, tableAt(3, 4));
    expect(conflicted.conflicts).toEqual([expect.objectContaining({
      kind: "changed",
      field: "modelConcurrency",
      profileId: "dsh/deepseek-official/deepseek-flash",
    })]);
    expect(conflicted.draft).toEqual(draft);
    expect(conflicted.draft.tableRevision).toBe(1);
  });

  it("keeps an edited family unresolved until a fresh retained page supplies it", () => {
    const baseline = basedAt(1);
    const draft = setConcurrencyLimit(baseline, flash, 6);
    const discovered = {
      ...snapshot, tableRevision: 3, profiles: [], modelConcurrency: [],
    } as unknown as Snapshot;
    const unresolved = rebaseDraft(draft, baseline, discovered);
    expect(unresolved.conflicts[0]).toMatchObject({ kind: "unread", field: "modelConcurrency" });
    expect(unresolved.draft.tableRevision).toBe(1);
    const stale = rebaseDraft(draft, baseline, discovered, { tableRevision: 1, modelConcurrency: [entry(2)] });
    expect(stale.conflicts[0].kind).toBe("unread");
    expect(stale.draft.tableRevision).toBe(1);
    const resolved = rebaseDraft(draft, baseline, discovered, { tableRevision: 3, modelConcurrency: [entry(2)] });
    expect(resolved.conflicts).toEqual([]);
    expect(resolved.draft.tableRevision).toBe(3);
    expect(flashLimit(resolved.draft)).toBe(6);
    expect(publication(resolved.baseline, resolved.draft, grant(3), "c").modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
  });
});

describe("private same-origin API", () => {
  it("uses the private prefix and sends writes only on an explicit command", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, result: { saved: true } }), {
          status: 200,
        }),
      );
    const api = createApi("/private-console/", fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    await api.command("user_policy_publish", { commandId: "c" }, "csrf");
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/private-console/api/command");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["X-Buddy-CSRF"]).toBe("csrf");
  });
  it("preserves a stale-version error instead of pretending the write succeeded", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: false,
            error: { code: "REVISION_CONFLICT", message: "stale" },
          }),
          { status: 409 },
        ),
      );
    await expect(
      createApi("/private", fetcher).command("save", {}, "csrf"),
    ).rejects.toMatchObject({ code: "REVISION_CONFLICT" });
  });
});

describe("independent Router and budget fields", () => {
  it("publishes only the changed Router, default mode and review budget", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!,
      routerProfileId: "review", defaultRoutingMode: "fast" as const,
      routingBudget: "brief" as const } };
    expect(publication(baseline, draft, grant(), "dual").configuration).toEqual({
      routerProfileId: "review", defaultRoutingMode: "fast", routingBudget: "brief",
    });
    expect(changeCount(baseline, draft) - changeCount(baseline, baseline)).toBe(3);
  });
  it("keeps an unchanged standard budget and publishes budget alone with a stale Router", () => {
    const baseline = makeDraft({ ...snapshot, profiles: [], configuration: { revision: 2, routerProfileId: "retired" , defaultRoutingMode: "review" as const, routingBudget: "standard"} } as Snapshot);
    const equivalent = { ...baseline, configuration: { ...baseline.configuration!, routingBudget: "standard" as const } };
    expect(draftDiffers(baseline, equivalent)).toBe(false);
    const draft = { ...equivalent, configuration: { ...equivalent.configuration!, routingBudget: "brief" as const } };
    expect(draftDiffers(baseline, draft)).toBe(true);
    expect(configurationChanged(baseline, draft)).toBe(true);
    expect(publication(baseline, draft, grant(), "budget").configuration).toEqual({ routingBudget: "brief" });
    expect(blockingIssues(baseline, draft)).toEqual([]);
  });

  it("rebases Router and budget independently and preserves the original draft on budget conflict", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routingBudget: "brief" as const } };
    const fresh = { ...snapshot, tableRevision: 4, configuration: { revision: 3, routerProfileId: "new-model", defaultRoutingMode: "review" as const, routingBudget: "standard" as const } };
    const merged = rebaseDraft(draft, baseline, fresh);
    expect(merged.conflicts).toEqual([]);
    expect(merged.draft.configuration).toMatchObject({ routerProfileId: "new-model", defaultRoutingMode: "review" as const, routingBudget: "brief" });
    expect(publication(merged.baseline, merged.draft, grant(), "budget")).toMatchObject({ expectedRevision: 4, configuration: { routingBudget: "brief" } });
    const conflicted = rebaseDraft(draft, baseline, { ...fresh, configuration: { ...fresh.configuration!, routingBudget: "deep" } });
    expect(conflicted.conflicts[0].field).toBe("routingBudget");
    expect(conflicted.draft).toBe(draft);
    expect(conflicted.baseline).toBe(baseline);
    expect(conflicted.draft.tableRevision).toBe(3);
    const modelDraft = { ...baseline, configuration: { ...baseline.configuration!, routerProfileId: "another-model"} };
    const modelMerge = rebaseDraft(modelDraft, baseline, { ...snapshot, tableRevision: 4, configuration: { ...snapshot.configuration!, routingBudget: "deep" } });
    expect(modelMerge.conflicts).toEqual([]);
    expect(modelMerge.draft.configuration!.routingBudget).toBe("deep");
    expect(publication(modelMerge.baseline, modelMerge.draft, grant(), "model").configuration).toEqual({ routerProfileId: "another-model" });
  });
});


describe("unavailable Router settings in the editor", () => {
  const unavailable = { ...snapshot, configuration: null,
    configurationError: { code: "router-settings-upgrade-required", message: "Router 设置需升级", revision: 4 } } as Snapshot;
  it("publishes unrelated changes while leaving upgrade-only settings null", () => {
    const baseline = makeDraft(unavailable);
    const draft = setFamilyAnnotation(baseline, flash, "仍可保存备注");
    expect(blockingIssues(baseline, draft)).toEqual([]);
    expect(publication(baseline, draft, grant(), "note")).not.toHaveProperty("configuration");
    const merged = rebaseDraft(draft, baseline, { ...unavailable, tableRevision: 4 });
    expect(merged.conflicts).toEqual([]);
    expect(merged.draft.configuration).toBeNull();
    expect(familyAnnotationText(merged.draft, flash)).toBe("仍可保存备注");
  });
  it("keeps a dirty Router intent as a conflict when refreshed settings need upgrade", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routingBudget: "brief" as const } };
    const merged = rebaseDraft(draft, baseline, unavailable);
    expect(merged.conflicts).toMatchObject([{ field: "configuration", kind: "changed" }]);
    expect(merged.draft).toBe(draft);
  });
  it("sends only the single Router, mode and budget through the form publication request", async () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routerProfileId: "chosen",
      defaultRoutingMode: "fast" as const, routingBudget: "deep" as const } };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true, result: { tableRevision: 4 } })));
    const patch = publication(baseline, draft, grant(), "single-router");
    await createApi("", fetcher as typeof fetch).command("user_policy_publish", patch, "csrf");
    const body = JSON.parse(fetcher.mock.calls[0][1]!.body as string);
    expect(body.params.configuration).toEqual({ routerProfileId: "chosen", defaultRoutingMode: "fast", routingBudget: "deep" });
    expect(body.params.configuration).not.toHaveProperty("fastRouterProfileId");
    expect(body.params.configuration).not.toHaveProperty("reviewRouterProfileId");
  });
});
