import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import {
  changeCount,
  configurationChanged,
  draftDiffers,
  familyAnnotationChanges,
  familyAnnotationText,
  makeDraft,
  modelConcurrencyChanges,
  preferenceChanges,
  profileSettings,
  publication,
  rebaseDraft,
  setConcurrencyLimit,
  setFamilyAnnotation,
  setPreference,
} from "./draft";
import { blockingIssues } from "./policy";
import type { HistoryEntries } from "./draft";
import type { Draft, FamilyAnnotation, Profile, Snapshot } from "./types";

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
const note = (family: typeof flashFamily, text: string): FamilyAnnotation =>
  ({ ...family, text, updatedAt: null });
const snapshot = {
  profiles: [flash],

  preferences: [],
      familyAnnotations: [],
  configuration: { routerProfileIds: ["flash-off"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard",
  },

  modelConcurrency: [
    { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 },
  ],
} as unknown as Snapshot;

describe("editing a published snapshot", () => {
  it("keeps the server snapshot unchanged while editing", () => {
    const draft = makeDraft(snapshot);
    draft.profiles[0].enabled = false;
    expect(snapshot.profiles[0].enabled).toBe(true);
  });

  it("publishes only the dirty user patches, never a table, a card or an effective preference", () => {
    const baseline = makeDraft(snapshot);
    let draft = setFamilyAnnotation(baseline, flash, "我的使用备注");
    draft = setPreference(draft, "flash-off", "exclude", "太贵");
    draft = {
      ...draft,
      profiles: draft.profiles.map((p) => ({ ...p, enabled: false })),
    };
    const patch = publication(baseline, draft, "c");
    expect(patch).toEqual({
      commandId: "c",
      profileSettings: [{ profileId: "flash-off", enabled: false }],
      preferenceChanges: [{ profileId: "flash-off", mode: "exclude", reason: "太贵" }],
      familyAnnotationChanges: [{ ...flashFamily, text: "我的使用备注" }],
    });
    const wire = JSON.stringify(patch);
    // Program-owned profile facts are not human publication inputs.
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
    const patch = publication(baseline, makeDraft(snapshot), "c");
    expect(Object.keys(patch).sort()).toEqual([
      "commandId",
    ]);
  });

  it("sends a clear for an emptied note, a cleared preference", () => {
    const baseline = {
      ...makeDraft(snapshot),
      familyAnnotations: [note(flashFamily, "旧备注")],
      preferences: [{ profileId: "flash-off", mode: "exclude" as const, reason: "贵" }],
    };
    let draft = setFamilyAnnotation(baseline, flash, "");
    draft = setPreference(draft, "flash-off", "");
    const patch = publication(baseline, draft, "c");
    expect(patch.familyAnnotationChanges).toEqual([{ ...flashFamily, text: "" }]);
    expect(patch.preferenceChanges).toEqual([{ profileId: "flash-off", mode: null, reason: "" }]);
    expect(familyAnnotationChanges(baseline, draft)).toEqual([{ ...flashFamily, text: "" }]);
    expect(preferenceChanges(baseline, draft)).toEqual([{ profileId: "flash-off", mode: null, reason: "" }]);
  });

  it("keeps 不设 as an absent preference after clearing a buddy", () => {
    const baseline = makeDraft({ ...snapshot, preferences: [{ profileId: flash.profileId, mode: "prefer", reason: "常用" }] } as Snapshot);
    const cleared = setPreference(baseline, flash.profileId, "");
    expect(cleared.preferences).toEqual([]);
    expect(preferenceChanges(baseline, cleared)).toEqual([{ profileId: flash.profileId, mode: null, reason: "" }]);
  });

  it("keeps a stale pin and a stale Router out of every patch", () => {
    const baseline = makeDraft({
      ...snapshot,
      profiles: [flash, retired],
      preferences: [{ profileId: "retired", mode: "pin", reason: "以前固定" }],
      configuration: { routerProfileIds: ["retired"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    } as Snapshot);
    const draft = { ...baseline };
    expect(publication(baseline, draft, "c")).not.toHaveProperty("preferenceChanges");
    expect(publication(baseline, draft, "c")).not.toHaveProperty("configuration");
    // An unrelated note still publishes while the old settings stay as they are.
    const withNote = setFamilyAnnotation(draft, flash, "只改备注");
    const patch = publication(baseline, withNote, "c");
    expect(patch.familyAnnotationChanges).toEqual([{ ...flashFamily, text: "只改备注" }]);
    expect(patch).not.toHaveProperty("preferenceChanges");
    expect(patch).not.toHaveProperty("profileSettings");
  });

  it("allows several buddy pins independently", () => {
    const draft = { ...makeDraft(snapshot), preferences: [{ profileId: "a", mode: "pin" as const, reason: "" }] };
    const updated = setPreference(draft, flash.profileId, "pin");
    expect(updated.preferences).toHaveLength(2);
    expect(preferenceChanges(draft, updated)).toEqual([{ profileId: flash.profileId, mode: "pin", reason: "" }]);
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
    const preferred = setPreference(baseline, flash.profileId, "prefer", "更快");
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
      configuration: { ...disabled.configuration!, routerProfileIds: ["other"], routerRetryIntervalSeconds: 600},
    };
    expect(configurationChanged(baseline, reconfigured)).toBe(true);
    expect(changeCount(baseline, reconfigured)).toBe(2);
    // An invalid limit still counts, so the save bar never shows "0 项".
    expect(changeCount(baseline, setConcurrencyLimit(baseline, flash, NaN))).toBe(1);
    // A reordered copy of the same overrides is not a change.
    const overridden = {
      ...baseline,
      preferences: [
        { profileId: "a", mode: "prefer" as const, reason: "1" },
        { profileId: "b", mode: "exclude" as const, reason: "2" },
      ],
    };
    expect(draftDiffers(overridden, { ...overridden, preferences: [...overridden.preferences].reverse() })).toBe(false);
  });

  it("rebases the draft onto refreshed directory facts without losing user patches", () => {
    const baseline = makeDraft(snapshot);
    let draft = setFamilyAnnotation(baseline, flash, "保留的备注");
    draft = setPreference(draft, "flash-off", "prefer", "更快");
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
      // The program renamed the existing profile and added a new configuration.
      profiles: [{ ...flash, label: "目录新名称", available: false }, discovered],
    } as unknown as Snapshot;
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.label).toBe("目录新名称");
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-max")).toBeTruthy();
    // Program availability is adopted; the user's enablement and note are replayed.
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.available).toBe(false);
    expect(adopted.draft.profiles.find((p) => p.profileId === "flash-off")!.enabled).toBe(false);
    expect(familyAnnotationText(adopted.draft, flash)).toBe("保留的备注");
    const patch = publication(adopted.baseline, adopted.draft, "c");
    expect(patch.familyAnnotationChanges).toEqual([{ ...flashFamily, text: "保留的备注" }]);
    expect(patch.preferenceChanges).toEqual([{ profileId: "flash-off", mode: "prefer", reason: "更快" }]);
    expect(patch.profileSettings).toEqual([{ profileId: "flash-off", enabled: false }]);
    expect(profileSettings(baseline, draft)).toEqual([
      { profileId: "flash-off", enabled: false },
    ]);
  });
});

describe("replaying a draft on refreshed content", () => {
  it("keeps the local note when another writer changed the same field", () => {
    const baseline = { ...makeDraft(snapshot), familyAnnotations: [note(flashFamily, "A")] };
    const draft = setFamilyAnnotation(baseline, flash, "B");
    const refreshed = { ...snapshot, familyAnnotations: [note(flashFamily, "C")] };
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(familyAnnotationText(adopted.baseline, flash)).toBe("C");
    expect(familyAnnotationText(adopted.draft, flash)).toBe("B");
    expect(publication(adopted.baseline, adopted.draft, "save").familyAnnotationChanges).toEqual([
      { ...flashFamily, text: "B" },
    ]);
  });

  it("waits for a current retained page before replaying an omitted identity", () => {
    const baseline = { ...makeDraft(snapshot), profiles: [flash, { ...retired, enabled: true }] };
    const draft = { ...baseline, profiles: baseline.profiles.map(row => row.profileId === "retired"
      ? { ...row, enabled: false } : row) };
    const refreshed = { ...snapshot, profiles: [flash] };
    const unread = rebaseDraft(draft, baseline, refreshed);
    expect(unread.conflicts).toMatchObject([{ kind: "unread", field: "enabled", profileId: "retired" }]);
    const ready = rebaseDraft(draft, baseline, refreshed, { profiles: [{ ...retired, enabled: true }] });
    expect(ready.conflicts).toEqual([]);
    expect(ready.draft.profiles.find(row => row.profileId === "retired")?.enabled).toBe(false);
  });

  it("replays a buddy preference over a concurrent change", () => {
    const baseline = { ...makeDraft(snapshot), preferences: [{ profileId: flash.profileId, mode: "prefer" as const, reason: "A" }] };
    const draft = setPreference(baseline, flash.profileId, "exclude", "B");
    const refreshed = { ...snapshot, preferences: [{ profileId: flash.profileId, mode: "pin" as const, reason: "C" }] };
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(publication(adopted.baseline, adopted.draft, "save").preferenceChanges).toEqual([
      { profileId: flash.profileId, mode: "exclude", reason: "B" },
    ]);
  });
});

describe("retained history with local removals", () => {
  it("keeps a local preference removal when the published row returns", () => {
    const baseline = { ...makeDraft(snapshot), preferences: [{ profileId: flash.profileId, mode: "pin" as const, reason: "old" }] };
    const draft = { ...baseline, preferences: [] };
    const refreshed = { ...snapshot, preferences: baseline.preferences };
    const adopted = rebaseDraft(draft, baseline, refreshed);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.draft.preferences).toEqual([]);
    expect(publication(adopted.baseline, adopted.draft, "save").preferenceChanges).toEqual([
      { profileId: flash.profileId, mode: null, reason: "" },
    ]);
  });

  it("still fills identities neither the draft nor the baseline knows", () => {
    const baseline = makeDraft(snapshot);
    const page: HistoryEntries = { profiles: [retired], familyAnnotations: [note(retiredFamily, "history")] };
    const adopted = rebaseDraft(baseline, baseline, snapshot, page);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.draft.profiles.some(row => row.profileId === retired.profileId)).toBe(true);
    expect(familyAnnotationText(adopted.draft, retired)).toBe("history");
  });
});

describe("model family concurrency", () => {
  const flashLimit = (draft: Draft): number | null =>
    draft.modelConcurrency.find((entry) => entry.model === "deepseek-flash")?.limit ?? null;
  const entry = (limit: number) => ({
    adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit, active: 0,
  });
  const basedAt = () => makeDraft(snapshot);
  const tableAt = (limit: number) =>
    ({ ...snapshot, modelConcurrency: [entry(limit)] }) as Snapshot;

  it("publishes only changed family limits and never the occupancy", () => {
    const baseline = makeDraft(snapshot);
    expect(flashLimit(baseline)).toBe(2);
    expect(modelConcurrencyChanges(baseline, baseline)).toEqual([]);
    const draft = setConcurrencyLimit(baseline, flash, 6);
    expect(modelConcurrencyChanges(baseline, draft)).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const patch = publication(baseline, draft, "c");
    expect(patch.modelConcurrency).toEqual([
      { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 6 },
    ]);
    const wire = JSON.stringify(patch);
    // Occupancy is observation and the profile identity never rides along.
    expect(wire).not.toContain("active");
    expect(wire).not.toContain("flash-off");
    // An unrelated edit keeps the concurrency patch out entirely.
    expect(publication(baseline, setFamilyAnnotation(baseline, flash, "只改备注"), "c"))
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
    const baseline = basedAt();
    const draft = basedAt();
    const refreshed = {
      ...snapshot,
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
    const baseline = basedAt();
    for (const limit of [33, NaN]) {
      const draft = setConcurrencyLimit(baseline, flash, limit);
      const rebased = rebaseDraft(draft, baseline, tableAt(2));
      expect(rebased.conflicts.some(conflict => conflict.field === "modelConcurrency")).toBe(true);
      expect(Object.is(flashLimit(rebased.draft), limit)).toBe(true);
    }
  });

  it("replays a changed limit over the latest recorded limit", () => {
    const baseline = basedAt();
    const draft = setConcurrencyLimit(baseline, flash, 6);
    const adopted = rebaseDraft(draft, baseline, tableAt(4));
    expect(adopted.conflicts).toEqual([]);
    expect(flashLimit(adopted.baseline)).toBe(4);
    expect(flashLimit(adopted.draft)).toBe(6);
    expect(publication(adopted.baseline, adopted.draft, "save").modelConcurrency).toEqual([
      { ...flashFamily, limit: 6 },
    ]);
  });

  it("waits for a retained family row omitted by a bounded snapshot", () => {
    const baseline = basedAt();
    const draft = setConcurrencyLimit(baseline, flash, 6);
    const refreshed = { ...snapshot, profiles: [], modelConcurrency: [] };
    expect(rebaseDraft(draft, baseline, refreshed).conflicts).toMatchObject([
      { kind: "unread", field: "modelConcurrency" },
    ]);
    const ready = rebaseDraft(draft, baseline, refreshed, { profiles: [flash], modelConcurrency: [entry(2)] });
    expect(ready.conflicts).toEqual([]);
    expect(flashLimit(ready.draft)).toBe(6);
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
  it("publishes an emptied list as the explicit clear patch", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routerProfileIds: [] } };
    expect(configurationChanged(baseline, draft)).toBe(true);
    expect(changeCount(baseline, draft) - changeCount(baseline, baseline)).toBe(1);
    expect(publication(baseline, draft, "clear").configuration).toEqual({ routerProfileIds: [] });
  });
  it("counts a changed retry interval as its own settings field", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routerRetryIntervalSeconds: 300 } };
    expect(configurationChanged(baseline, draft)).toBe(true);
    expect(publication(baseline, draft, "interval").configuration).toEqual({ routerRetryIntervalSeconds: 300 });
  });
  it("publishes only the changed Router, default mode and review budget", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!,
      routerProfileIds: ["review"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "fast" as const,
      routingBudget: "brief" as const } };
    expect(publication(baseline, draft, "dual").configuration).toEqual({
      routerProfileIds: ["review"], defaultRoutingMode: "fast", routingBudget: "brief",
    });
    expect(changeCount(baseline, draft) - changeCount(baseline, baseline)).toBe(3);
  });
  it("keeps an unchanged standard budget and publishes budget alone with a stale Router", () => {
    const baseline = makeDraft({ ...snapshot, profiles: [], configuration: { routerProfileIds: ["retired"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"} } as Snapshot);
    const equivalent = { ...baseline, configuration: { ...baseline.configuration!, routingBudget: "standard" as const } };
    expect(draftDiffers(baseline, equivalent)).toBe(false);
    const draft = { ...equivalent, configuration: { ...equivalent.configuration!, routingBudget: "brief" as const } };
    expect(draftDiffers(baseline, draft)).toBe(true);
    expect(configurationChanged(baseline, draft)).toBe(true);
    expect(publication(baseline, draft, "budget").configuration).toEqual({ routingBudget: "brief" });
    expect(blockingIssues(baseline, draft)).toEqual([]);
  });

  it("replays Router and budget fields independently over fresh settings", () => {
    const baseline = makeDraft(snapshot);
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routingBudget: "brief" as const } };
    const fresh = { ...snapshot, configuration: { ...snapshot.configuration!, routerProfileIds: ["new-model"], routingBudget: "deep" as const } };
    const adopted = rebaseDraft(draft, baseline, fresh);
    expect(adopted.conflicts).toEqual([]);
    expect(adopted.draft.configuration).toMatchObject({ routerProfileIds: ["new-model"], routingBudget: "brief" });
    expect(publication(adopted.baseline, adopted.draft, "save").configuration).toEqual({ routingBudget: "brief" });
  });
});

describe("unavailable Router settings in the editor", () => {
  const unavailable = { ...snapshot, configuration: null,
    configurationError: { code: "router-settings-upgrade-required", message: "Router 设置需升级",} } as Snapshot;
  it("publishes unrelated changes while leaving upgrade-only settings null", () => {
    const baseline = makeDraft(unavailable);
    const draft = setFamilyAnnotation(baseline, flash, "仍可保存备注");
    expect(blockingIssues(baseline, draft)).toEqual([]);
    expect(publication(baseline, draft, "note")).not.toHaveProperty("configuration");
    const merged = rebaseDraft(draft, baseline, { ...unavailable,});
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
    const draft = { ...baseline, configuration: { ...baseline.configuration!, routerProfileIds: ["chosen"], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "fast" as const, routingBudget: "deep" as const } };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true, result: {} })));
    const patch = publication(baseline, draft, "single-router");
    await createApi("", fetcher as typeof fetch).command("user_policy_publish", patch, "csrf");
    const body = JSON.parse(fetcher.mock.calls[0][1]!.body as string);
    expect(body.params.configuration).toEqual({ routerProfileIds: ["chosen"], defaultRoutingMode: "fast", routingBudget: "deep" });
    expect(body.params.configuration).not.toHaveProperty("fastRouterProfileId");
    expect(body.params.configuration).not.toHaveProperty("reviewRouterProfileId");
  });
});
