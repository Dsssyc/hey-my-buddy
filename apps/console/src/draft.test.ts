import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import {
  annotationChanges,
  annotationText,
  changedProfileIds,
  configurationChanged,
  draftDiffers,
  historyView,
  makeDraft,
  modelConcurrencyChanges,
  preferenceChanges,
  profileSettings,
  publication,
  rebaseDraft,
  retainedAdditions,
  setAnnotation,
  setConcurrencyLimit,
  setPreference,
  withHistory,
} from "./draft";
import type { HistoryEntries } from "./draft";
import type { Draft, Profile, Snapshot, WriterGrant } from "./types";

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
/** A configuration that is still enabled but no longer listed by the catalog. */
const retired = {
  ...flash,
  profileId: "retired",
  model: "retired-model",
  available: false,
  unavailableReason: "provider paused",
} satisfies Profile;
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
  annotations: [],
  configuration: {
    revision: 2,
    decisionProfileId: "flash-off",
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

  it("publishes only the dirty user patches, never a table or a card", () => {
    const baseline = makeDraft(snapshot);
    let draft = setAnnotation(baseline, "flash-off", "我的使用意见");
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
      annotationChanges: [{ profileId: "flash-off", text: "我的使用意见" }],
    });
    const wire = JSON.stringify(patch);
    // Program-owned profile facts and automatic cards are not human inputs.
    expect(wire).not.toContain("available");
    expect(wire).not.toContain("deepseek-flash");
    expect(wire).not.toContain("summary");
    expect(wire).not.toContain("cards");
    expect(wire).not.toContain("profiles\"");
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

  it("sends a clear for an emptied opinion and a mode null for a removed preference", () => {
    const baseline = {
      ...makeDraft(snapshot),
      annotations: [{ profileId: "flash-off", text: "旧意见", revision: 1, updatedAt: null }],
      preferences: [{ profileId: "flash-off", mode: "prefer" as const, reason: "更快" }],
    };
    let draft = setAnnotation(baseline, "flash-off", "");
    draft = setPreference(draft, "flash-off", "");
    const patch = publication(baseline, draft, grant(), "c");
    expect(patch.annotationChanges).toEqual([{ profileId: "flash-off", text: "" }]);
    expect(patch.preferenceChanges).toEqual([
      { profileId: "flash-off", mode: null, reason: "" },
    ]);
    expect(annotationChanges(baseline, draft)).toEqual([
      { profileId: "flash-off", text: "" },
    ]);
    expect(preferenceChanges(baseline, draft)).toEqual([
      { profileId: "flash-off", mode: null, reason: "" },
    ]);
  });

  it("keeps a stale pin and a stale decision setting out of every patch", () => {
    const baseline = makeDraft({
      ...snapshot,
      profiles: [flash, retired],
      preferences: [{ profileId: "retired", mode: "pin", reason: "以前固定" }],
      configuration: { revision: 3, decisionProfileId: "retired" },
    } as Snapshot);
    const draft = { ...baseline };
    expect(publication(baseline, draft, grant(), "c")).not.toHaveProperty("preferenceChanges");
    expect(publication(baseline, draft, grant(), "c")).not.toHaveProperty("configuration");
    // An unrelated opinion still publishes while the old settings stay as they are.
    const withOpinion = setAnnotation(draft, "flash-off", "只改意见");
    const patch = publication(baseline, withOpinion, grant(), "c");
    expect(patch.annotationChanges).toEqual([{ profileId: "flash-off", text: "只改意见" }]);
    expect(patch).not.toHaveProperty("preferenceChanges");
    expect(patch).not.toHaveProperty("profileSettings");
  });

  it("replaces a global pin without erasing unrelated soft preferences", () => {
    const draft = {
      ...makeDraft(snapshot),
      preferences: [
        { profileId: "a", mode: "pin" as const, reason: "" },
        { profileId: "b", mode: "prefer" as const, reason: "tests" },
      ],
    };
    const updated = setPreference(draft, "flash-off", "pin");
    expect(updated.preferences.map((p) => p.profileId)).toEqual([
      "b",
      "flash-off",
    ]);
    expect(preferenceChanges(draft, updated)).toEqual([
      { profileId: "a", mode: null, reason: "" },
      { profileId: "flash-off", mode: "pin", reason: "" },
    ]);
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
    expect(changedProfileIds(baseline, reordered)).toEqual([]);
    const prefixed = setPreference(baseline, "flash-off", "prefer", "更快");
    expect(draftDiffers(baseline, prefixed)).toBe(true);
    expect(changedProfileIds(baseline, prefixed)).toEqual(["flash-off"]);
    const annotated = setAnnotation(baseline, "flash-off", "意见");
    expect(changedProfileIds(baseline, annotated)).toEqual(["flash-off"]);
    const disabled = {
      ...baseline,
      profiles: [{ ...baseline.profiles[0], enabled: false }],
    };
    expect(changedProfileIds(baseline, disabled)).toEqual(["flash-off"]);
    expect(configurationChanged(baseline, disabled)).toBe(false);
    const reconfigured: typeof disabled = {
      ...disabled,
      configuration: { ...disabled.configuration, decisionProfileId: "other" },
    };
    expect(configurationChanged(baseline, reconfigured)).toBe(true);
    // A reordered copy of the same preferences is not a change.
    const preferred = {
      ...baseline,
      preferences: [
        { profileId: "a", mode: "prefer" as const, reason: "1" },
        { profileId: "b", mode: "prefer" as const, reason: "2" },
      ],
    };
    expect(changedProfileIds(preferred, { ...preferred, preferences: [...preferred.preferences].reverse() })).toEqual([]);
  });

  it("rebases the draft onto refreshed directory facts without losing user patches", () => {
    const baseline = makeDraft(snapshot);
    let draft = setAnnotation(baseline, "flash-off", "保留的意见");
    draft = setPreference(draft, "flash-off", "prefer", "更快");
    draft = {
      ...draft,
      profiles: draft.profiles.map((p) => ({ ...p, enabled: false })),
    };
    const discovered = {
      ...flash,
      profileId: "flash-max",
      model: "deepseek-flash-max",
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
    expect(adopted.baseline.tableRevision).toBe(4);
    expect(adopted.draft.tableRevision).toBe(4);
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.label).toBe("目录新名称");
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-max")).toBeTruthy();
    // Program availability is adopted; the user's enablement and opinion are replayed.
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.available).toBe(false);
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.enabled).toBe(false);
    expect(annotationText(adopted.draft, "flash-off")).toBe("保留的意见");
    const patch = publication(adopted.baseline, adopted.draft, grant(4), "c");
    expect(patch.expectedRevision).toBe(4);
    expect(patch.annotationChanges).toEqual([{ profileId: "flash-off", text: "保留的意见" }]);
    expect(patch.preferenceChanges).toEqual([
      { profileId: "flash-off", mode: "prefer", reason: "更快" },
    ]);
    expect(patch.profileSettings).toEqual([{ profileId: "flash-off", enabled: false }]);
    expect(profileSettings(baseline, draft)).toEqual([
      { profileId: "flash-off", enabled: false },
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

  it("refuses to overlay a draft onto another writer's newer value", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      annotations: [{ profileId: "flash-off", text: "A", revision: 1, updatedAt: null }],
    };
    const draft = setAnnotation(baseline, "flash-off", "B");
    // Another writer published C (v2); a program discovery then published v3.
    const discovered = {
      ...bounded,
      tableRevision: 3,
      annotations: [{ profileId: "flash-off", text: "C", revision: 2, updatedAt: null }],
    } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toHaveLength(1);
    expect(adopted.conflicts[0]).toMatchObject({
      kind: "changed",
      field: "annotation",
      profileId: "flash-off",
    });
    // The original draft and expectedRevision stay; B is never saved under v3.
    expect(adopted.draft).toEqual(draft);
    expect(adopted.baseline).toEqual(baseline);
    expect(adopted.draft.tableRevision).toBe(1);
    expect(annotationText(adopted.draft, "flash-off")).toBe("B");
    expect(annotationText(adopted.baseline, "flash-off")).toBe("A");
    expect(publication(adopted.baseline, adopted.draft, grant(1), "c").expectedRevision).toBe(1);
  });

  it("merges only when the refreshed value still equals the old baseline", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      annotations: [{ profileId: "flash-off", text: "A", revision: 1, updatedAt: null }],
    };
    const draft = setAnnotation(baseline, "flash-off", "B");
    // The program re-published the row unchanged: A is still what the board holds.
    const discovered = {
      ...bounded,
      tableRevision: 3,
      annotations: [{ profileId: "flash-off", text: "A", revision: 2, updatedAt: null }],
    } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toEqual([]);
    expect(annotationText(adopted.draft, "flash-off")).toBe("B");
    expect(adopted.draft.tableRevision).toBe(3);
    expect(adopted.baseline.tableRevision).toBe(3);
  });

  it("keeps the published row when it already equals the draft intent", () => {
    const baseline = { ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot) };
    const draft = setAnnotation(baseline, "flash-off", "B");
    const discovered = {
      ...bounded,
      tableRevision: 3,
      annotations: [{ profileId: "flash-off", text: "B", revision: 2, updatedAt: null }],
    } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toEqual([]);
    expect(annotationText(adopted.draft, "flash-off")).toBe("B");
    expect(adopted.draft.tableRevision).toBe(3);
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
    expect(adopted.draft.profiles.find((p) => p.profileId === "retired")!.enabled).toBe(false);

    // A cached page from the old revision must not mint a baseline at the new revision.
    const stalePage = retained(1, { profiles: [retiredEnabled] });
    const withStale = rebaseDraft(draft, baseline, discovered, stalePage);
    expect(withStale.conflicts[0].kind).toBe("unread");
    expect(withStale.draft.tableRevision).toBe(1);
    expect(withStale.baseline.tableRevision).toBe(1);

    // A fresh retained page at the discovered revision resolves and applies the edit.
    const freshPage = retained(3, { profiles: [retiredEnabled] });
    const resolved = rebaseDraft(draft, baseline, discovered, freshPage);
    expect(resolved.conflicts).toEqual([]);
    expect(resolved.draft.tableRevision).toBe(3);
    expect(resolved.baseline.tableRevision).toBe(3);
    expect(resolved.draft.profiles.find((p) => p.profileId === "retired")!.enabled).toBe(false);
    expect(resolved.draft.profiles.some((p) => p.profileId === "flash-off")).toBe(true);
  });

  it("flags a fresh retained row that differs from both baseline and draft", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      profiles: [flash, retiredEnabled],
      annotations: [{ profileId: "retired", text: "A", revision: 1, updatedAt: null }],
    };
    const draft = setAnnotation(baseline, "retired", "B");
    const discovered = { ...bounded, tableRevision: 3 } as Snapshot;
    const freshPage = retained(3, {
      profiles: [retiredEnabled],
      annotations: [{ profileId: "retired", text: "C", revision: 2, updatedAt: null }],
    });
    const adopted = rebaseDraft(draft, baseline, discovered, freshPage);
    expect(adopted.conflicts[0]).toMatchObject({
      kind: "changed",
      field: "annotation",
      profileId: "retired",
    });
    expect(annotationText(adopted.draft, "retired")).toBe("B");
    expect(adopted.draft.tableRevision).toBe(1);
  });

  it("merges a changed preference mode without overwriting another writer's reason", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      preferences: [{ profileId: "flash-off", mode: "prefer" as const, reason: "old" }],
    };
    const draft = setPreference(baseline, "flash-off", "pin", "old");
    const discovered = {
      ...bounded,
      tableRevision: 3,
      preferences: [{ profileId: "flash-off", mode: "pin" as const, reason: "他人依据" }],
    } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.draft.preferences).toEqual([
      { profileId: "flash-off", mode: "pin", reason: "他人依据" },
    ]);
  });

  it("treats a preference another writer removed as a conflict, not a resurrection", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      preferences: [{ profileId: "flash-off", mode: "prefer" as const, reason: "old" }],
    };
    const draft = setPreference(baseline, "flash-off", "prefer", "mine");
    const discovered = { ...bounded, tableRevision: 3 } as Snapshot;
    const adopted = rebaseDraft(draft, baseline, discovered);
    expect(adopted.conflicts[0]).toMatchObject({ kind: "changed", field: "preference" });
    expect(adopted.draft.tableRevision).toBe(1);
  });

  it("keeps a changed decision selector when the fresh table still holds the old value", () => {
    const baseline = {
      ...makeDraft({ ...bounded, tableRevision: 1 } as Snapshot),
      profiles: [flash, retiredEnabled],
      configuration: { revision: 1, decisionProfileId: "flash-off" },
    };
    const draft = {
      ...baseline,
      configuration: { ...baseline.configuration, decisionProfileId: "retired" },
    };
    const discovered = { ...bounded, tableRevision: 3 } as Snapshot;
    const clean = rebaseDraft(draft, baseline, discovered);
    expect(clean.conflicts).toEqual([]);
    expect(clean.draft.configuration.decisionProfileId).toBe("retired");
    // Another writer changed the selector first: the old revision is retained.
    const conflicted = rebaseDraft(draft, baseline, {
      ...discovered,
      configuration: { revision: 2, decisionProfileId: "gone:model:off" },
    } as Snapshot);
    expect(conflicted.conflicts[0]).toMatchObject({ kind: "changed", field: "configuration" });
    expect(conflicted.draft.configuration.decisionProfileId).toBe("retired");
    expect(conflicted.draft.tableRevision).toBe(1);
  });

  it("bounds retained rows to the revision they were read at", () => {
    const current = makeDraft({ ...bounded, tableRevision: 3 } as Snapshot);
    const entries = retained(2, {
      profiles: [retiredEnabled],
      annotations: [{ profileId: "retired", text: "旧意见", revision: 1, updatedAt: null }],
    });
    const merged = withHistory(current, entries);
    expect(merged.profiles.map((p) => p.profileId)).toEqual(["flash-off"]);
    expect(merged.annotations).toEqual([]);
    const view = historyView({ ...snapshot, tableRevision: 3 } as Snapshot, entries);
    expect(view.profiles.some((p) => p.profileId === "retired")).toBe(false);
    const matched = withHistory(current, retained(3, { profiles: [retiredEnabled] }));
    expect(matched.profiles.some((p) => p.profileId === "retired")).toBe(true);
  });
});

describe("empty opinion handling", () => {
  it("reports no change when an absent opinion stays absent", () => {
    const baseline = makeDraft(snapshot);
    expect(annotationText(baseline, "flash-off")).toBe("");
    expect(annotationChanges(baseline, makeDraft(snapshot))).toEqual([]);
  });
});

describe("retained additions never resurrect a removal", () => {
  const revision = 3;
  const current = makeDraft({ ...snapshot, tableRevision: revision } as Snapshot);
  const pinned = {
    ...current,
    preferences: [{ profileId: "flash-off", mode: "pin" as const, reason: "旧依据" }],
  };

  it("keeps a removed preference removed when the published row comes back", () => {
    const draft = { ...pinned, preferences: [] };
    const page: HistoryEntries = {
      tableRevision: revision,
      preferences: [{ profileId: "flash-off", mode: "pin", reason: "旧依据" }],
    };
    // A plain merge would erase the removal, so the adoption path filters first.
    expect(withHistory(draft, page).preferences).toHaveLength(1);
    expect(withHistory(draft, retainedAdditions(draft, pinned, page)).preferences).toHaveLength(0);
    // The change stays publishable as a real removal.
    expect(preferenceChanges(pinned, withHistory(draft, retainedAdditions(draft, pinned, page)))).toEqual([
      { profileId: "flash-off", mode: null, reason: "" },
    ]);
  });

  it("still fills identities neither the draft nor the baseline knows", () => {
    const draft = { ...current, preferences: [] };
    const page: HistoryEntries = {
      tableRevision: revision,
      preferences: [{ profileId: "new-profile", mode: "prefer", reason: "初评" }],
    };
    const additions = retainedAdditions(draft, current, page)!;
    expect(withHistory(draft, additions).preferences).toEqual([
      { profileId: "new-profile", mode: "prefer", reason: "初评" },
    ]);
  });

  it("ignores a page from another revision entirely", () => {
    const draft = { ...pinned, preferences: [] };
    const page: HistoryEntries = {
      tableRevision: revision - 1,
      preferences: [{ profileId: "flash-off", mode: "pin", reason: "旧依据" }],
    };
    expect(retainedAdditions(draft, pinned, page)).toBeNull();
  });
});

describe("model family concurrency", () => {
  const flashLimit = (draft: Draft): number | null =>
    draft.modelConcurrency.find((entry) => entry.model === "deepseek-flash")?.limit ?? null;
  const entry = (revision: number, limit: number) => ({
    adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit, active: 0,
  });
  const basedAt = (revision: number) => makeDraft({ ...snapshot, tableRevision: revision } as unknown as Snapshot);
  const tableAt = (revision: number, limit: number) =>
    ({ ...snapshot, tableRevision: revision, modelConcurrency: [entry(revision, limit)] }) as unknown as Snapshot;

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
    expect(publication(baseline, setAnnotation(baseline, "flash-off", "只改意见"), grant(), "c"))
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
      modelConcurrency: [{ ...entry(3, 2), active: 9 }],
    } as unknown as Snapshot;
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(flashLimit(adopted.draft)).toBe(2);
    expect(draftDiffers(adopted.baseline, adopted.draft)).toBe(false);
    // The draft shape drops occupancy, so it can never be published.
    expect("active" in adopted.draft.modelConcurrency[0]).toBe(false);
  });

  it("rebases a changed limit three ways against the refreshed table", () => {
    const baseline = basedAt(1);
    const draft = setConcurrencyLimit(baseline, flash, 6);
    // Nobody else touched it: the draft limit applies on top of the fresh table.
    const clean = rebaseDraft(draft, baseline, tableAt(3, 2));
    expect(clean.conflicts).toEqual([]);
    expect(flashLimit(clean.draft)).toBe(6);
    expect(flashLimit(clean.baseline)).toBe(2);
    expect(clean.draft.tableRevision).toBe(3);
    // The fresh table already holds the draft's value.
    const applied = rebaseDraft(draft, baseline, tableAt(3, 6));
    expect(applied.conflicts).toEqual([]);
    expect(flashLimit(applied.draft)).toBe(6);
    // Another writer published a different limit first: keep our revision.
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
    // The bounded fresh snapshot no longer lists the family at all.
    const discovered = {
      ...snapshot, tableRevision: 3, profiles: [], modelConcurrency: [],
    } as unknown as Snapshot;
    const unresolved = rebaseDraft(draft, baseline, discovered);
    expect(unresolved.conflicts[0]).toMatchObject({ kind: "unread", field: "modelConcurrency" });
    expect(unresolved.draft.tableRevision).toBe(1);
    // A page cached at the old revision never mints the new baseline.
    const stale = rebaseDraft(draft, baseline, discovered, {
      tableRevision: 1,
      modelConcurrency: [entry(1, 2)],
    });
    expect(stale.conflicts[0].kind).toBe("unread");
    expect(stale.draft.tableRevision).toBe(1);
    // The fresh retained page resolves it and the edit publishes at V3.
    const resolved = rebaseDraft(draft, baseline, discovered, {
      tableRevision: 3,
      modelConcurrency: [entry(3, 2)],
    });
    expect(resolved.conflicts).toEqual([]);
    expect(resolved.draft.tableRevision).toBe(3);
    expect(flashLimit(resolved.draft)).toBe(6);
    const patch = publication(resolved.baseline, resolved.draft, grant(3), "c");
    expect(patch.modelConcurrency).toEqual([
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
