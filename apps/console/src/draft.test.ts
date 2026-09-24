import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import {
  addProfiles,
  changedProfileIds,
  configurationChanged,
  draftDiffers,
  makeDraft,
  publication,
  setPreference,
} from "./draft";
import type { Profile, Snapshot, WriterGrant } from "./types";

const profile = {
  profileId: "flash-off",
  label: "Flash",
  adapter: "dsh",
  provider: "deepseek-official",
  model: "deepseek-flash",
  effort: "off",
  available: true,
  enabled: true,
  capabilities: ["text"],
  contextWindow: 1000000,
  description: "",
  source: "catalog",
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
  profiles: [profile],
  cards: [],
  preferences: [],
  configuration: {
    revision: 2,
    decisionProfileId: "flash-off",
  },
  sampleCounts: { "flash-off": 7 },
} as unknown as Snapshot;

describe("editing a published snapshot", () => {
  it("keeps the server snapshot and publication version unchanged while editing", () => {
    const draft = makeDraft(snapshot);
    draft.profiles[0].label = "My preference";
    expect(snapshot.profiles[0].label).toBe("Flash");
    expect(publication(draft, grant(), "c").expectedRevision).toBe(3);
  });

  it("does not multiply discovered profiles or fabricate samples", () => {
    const draft = addProfiles(makeDraft(snapshot), [
      profile,
      { ...profile, profileId: "flash-max", effort: "max" },
    ]);
    expect(draft.profiles).toHaveLength(2);
    expect(draft.cards).toHaveLength(0);
    draft.cards.push({
      profileId: "flash-max",
      revision: 0,
      summary: "A user-authored observation",
      strengths: [],
      limitations: [],
      risks: [],
      evidenceIds: [],
      updatedAt: null,
    });
    const patch = publication(draft, grant(3), "c");
    expect(patch.cards[0]).not.toHaveProperty("sampleCount");
    expect(patch.cards[0]).not.toHaveProperty("revision");
    expect(patch.configuration).toEqual({ decisionProfileId: "flash-off" });
    expect(patch.configuration).not.toHaveProperty("autoMaintain");
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
  });

  it("detects content changes independently of array order and card rewrites", () => {
    const baseline = makeDraft(snapshot);
    const reordered: typeof baseline = {
      ...baseline,
      cards: [
        { profileId: "b", revision: 1, summary: "", strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null },
        { profileId: "a", revision: 0, summary: "A", strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null },
      ],
    };
    const sameOrder: typeof baseline = { ...reordered, cards: [...reordered.cards].reverse() };
    expect(draftDiffers(baseline, reordered)).toBe(true);
    expect(draftDiffers(reordered, sameOrder)).toBe(false);
    const edited = { ...baseline, cards: [{ profileId: "flash-off", revision: 0, summary: "changed", strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null }] };
    expect(draftDiffers(baseline, edited)).toBe(true);
  });

  it("separates per-profile content changes from the decision configuration", () => {
    const baseline = makeDraft(snapshot);
    const edited: typeof baseline = {
      ...baseline,
      profiles: [{ ...baseline.profiles[0], enabled: false }],
      cards: [{ profileId: "flash-off", revision: 0, summary: "edited", strengths: ["x"], limitations: [], risks: [], evidenceIds: [], updatedAt: null }],
    };
    expect(changedProfileIds(baseline, edited)).toEqual(["flash-off"]);
    expect(configurationChanged(baseline, edited)).toBe(false);
    const reconfigured: typeof baseline = {
      ...edited,
      configuration: { ...edited.configuration, decisionProfileId: "other" },
    };
    expect(configurationChanged(baseline, reconfigured)).toBe(true);
  });

  it("reports enablement and preference edits even when the profile already has a card", () => {
    const existingCard = {
      profileId: "flash-off",
      revision: 4,
      summary: "已发布的卡片",
      strengths: ["稳定"],
      limitations: [],
      risks: [],
      evidenceIds: [],
      updatedAt: null,
    };
    const baseline = { ...makeDraft(snapshot), cards: [existingCard] };
    const enabled = {
      ...baseline,
      profiles: [{ ...baseline.profiles[0], enabled: false }],
    };
    expect(changedProfileIds(baseline, enabled)).toEqual(["flash-off"]);

    const preferred = {
      ...baseline,
      preferences: [{ profileId: "flash-off", mode: "prefer" as const, reason: "更快" }],
    };
    expect(changedProfileIds(baseline, preferred)).toEqual(["flash-off"]);

    // All three kinds at once still resolve to the one profile they describe.
    const combined = {
      ...enabled,
      preferences: preferred.preferences,
      cards: [{ ...existingCard, summary: "重写的卡片" }],
    };
    expect(changedProfileIds(baseline, combined)).toEqual(["flash-off"]);
    // A reordered copy of the same preferences is not a change.
    const reordered = {
      ...preferred,
      preferences: [...preferred.preferences],
    };
    expect(changedProfileIds(preferred, reordered)).toEqual([]);
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
    await api.command("evaluation_write_abort", { commandId: "c" }, "csrf");
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
