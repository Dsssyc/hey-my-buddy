import { describe, expect, it } from "vitest";
import { buddyHash, buddyLocation } from "./navigation";

describe("addresses of the Buddy page", () => {
  it("writes separate harness and model selections", () => {
    expect(buddyHash({ view: "harness" })).toBe("#buddies/harness");
    expect(buddyHash({ view: "harness", target: "codex/cli" })).toBe("#buddies/harness/codex%2Fcli");
    expect(buddyHash({ view: "models" })).toBe("#buddies/models");
    expect(buddyHash({ view: "models", targetKind: "model", target: "codex/openai/gpt" })).toBe("#buddies/models/model/codex%2Fopenai%2Fgpt");
    expect(buddyHash({ view: "models", target: "codex:openai:gpt:high" })).toBe("#buddies/models/buddy/codex%3Aopenai%3Agpt%3Ahigh");
  });
  it("reads directory and Router selections", () => {
    for (const location of [...(["routing", "maintenance", "profiles"]).map(target => ({ view: "router" as const, target })),
      { view: "router" as const, target: "profiles", profileRunId: "run/id 1" },
      { view: "evaluation" as const, evaluation: { harness: "codex", modelId: "group/one", effort: "very high" } },
      { view: "evaluation" as const, evaluation: { harness: "codex", modelId: "group/one", effort: "very high" }, profile: true },
      { view: "harness" as const, target: "a/b c:d" },
      ...(["model", "buddy"] as const).map(targetKind => ({ view: "models" as const, targetKind, target: "a/b c:d" }))]) {
      expect(buddyLocation(buddyHash(location))).toEqual(location);
    }
    expect(buddyLocation("#buddies", "harness")).toEqual({ view: "harness" });
    expect(buddyLocation("#buddies")).toEqual({ view: "models" });
    expect(buddyLocation("#buddies/harness")).toEqual({ view: "harness" });
    expect(buddyLocation("#buddies/models/model/key")).toEqual({ view: "models", targetKind: "model", target: "key" });
    expect(buddyLocation("#buddies/evaluation")).toEqual({ view: "evaluation" });
  });
  it("rejects retired and unknown routes without redirects", () => {
    for (const hash of ["#buddies/catalog", "#buddies/catalog/model/id", "#buddies/roles", "#buddies/roles/current", "#buddies/router/current", "#buddies/router/health", "#buddies/router/unknown", "#buddies/router/routing/run", "#buddies/evaluation/codex/model/high/other"]) expect(buddyLocation(hash)).toBeNull();
  });
  it("rejects malformed selections and other pages", () => {
    for (const hash of ["#tasks", "", "#buddies/models/buddy/%E0%A4%A", "#buddies/models/buddy", "#buddies/models/unknown/id", "#buddies/models/model/id/extra", "#buddies/harness/a/b", "#buddies/evaluation/codex/id", "#buddies/evaluation/codex/%E0%A4%A/high", "#buddies/evaluation/codex/id/high/profile/extra"]) expect(buddyLocation(hash)).toBeNull();
  });
});
