import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import { addProfiles, makeDraft, publication, setPreference } from "./draft";
import type { Profile, Snapshot } from "./types";

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
const snapshot = {
  tableRevision: 3,
  profiles: [profile],
  cards: [],
  preferences: [],
  configuration: {
    revision: 2,
    decisionProfileId: "flash-off",
    autoMaintain: false,
  },
} as unknown as Snapshot;

describe("editing a published snapshot", () => {
  it("keeps the server snapshot and publication version unchanged while editing", () => {
    const draft = makeDraft(snapshot);
    draft.profiles[0].label = "My preference";
    expect(snapshot.profiles[0].label).toBe("Flash");
    expect(
      publication(
        draft,
        {
          writerId: "w",
          generation: 2,
          writerToken: "private",
          phase: "writing",
          expiresAt: "",
          tableRevision: 4,
        },
        "c",
      ).expectedRevision,
    ).toBe(3);
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
      sampleCount: 0,
      updatedAt: null,
    });
    const patch = publication(
      draft,
      {
        writerId: "w",
        generation: 1,
        writerToken: "private",
        phase: "writing",
        expiresAt: "",
        tableRevision: 3,
      },
      "c",
    );
    expect(patch.cards[0]).not.toHaveProperty("sampleCount");
    expect(patch.cards[0]).not.toHaveProperty("revision");
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
