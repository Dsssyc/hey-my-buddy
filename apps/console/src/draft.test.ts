import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import {
  annotationChanges,
  annotationText,
  changedProfileIds,
  configurationChanged,
  draftDiffers,
  makeDraft,
  preferenceChanges,
  profileSettings,
  publication,
  rebaseDraft,
  setAnnotation,
  setPreference,
} from "./draft";
import type { Profile, Snapshot, WriterGrant } from "./types";

const flash = {
  profileId: "flash-off",
  label: "Flash",
  adapter: "dsh",
  provider: "deepseek-official",
  model: "deepseek-flash",
  effort: "off",
  available: true,
  enabled: true,
  capabilities: ["execution:dsh", "decision:dsh"],
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

describe("empty opinion handling", () => {
  it("reports no change when an absent opinion stays absent", () => {
    const baseline = makeDraft(snapshot);
    expect(annotationText(baseline, "flash-off")).toBe("");
    expect(annotationChanges(baseline, makeDraft(snapshot))).toEqual([]);
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
