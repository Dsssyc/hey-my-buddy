import { describe, expect, it } from "vitest";
import { buddyHash, buddyLocation } from "./navigation";

describe("addresses of the Buddy page", () => {
  it("writes one directory with encoded harness, model and buddy selections", () => {
    expect(buddyHash({ view: "models" })).toBe("#buddies/catalog");
    expect(buddyHash({ view: "models", targetKind: "harness", target: "codex" })).toBe("#buddies/catalog/harness/codex");
    expect(buddyHash({ view: "models", targetKind: "model", target: "codex/openai/gpt" })).toBe("#buddies/catalog/model/codex%2Fopenai%2Fgpt");
    expect(buddyHash({ view: "models", target: "codex:openai:gpt:high" })).toBe("#buddies/catalog/buddy/codex%3Aopenai%3Agpt%3Ahigh");
  });
  it("reads directory and Router selections", () => {
    for (const location of [...(["routing", "maintenance", "profiles"]).map(target => ({ view: "router" as const, target })),
      { view: "router" as const, target: "profiles", profileRunId: "run/id 1" },
      { view: "evaluation" as const, evaluation: { harness: "codex", modelId: "group/one", effort: "very high" } },
      { view: "evaluation" as const, evaluation: { harness: "codex", modelId: "group/one", effort: "very high" }, profile: true },
      ...(["harness", "model", "buddy"] as const).map(targetKind => ({ view: "models" as const, targetKind, target: "a/b c:d" }))]) {
      expect(buddyLocation(buddyHash(location))).toEqual(location);
    }
    expect(buddyLocation("#buddies")).toEqual({ view: "models" });
    expect(buddyLocation("#buddies/catalog")).toEqual({ view: "models" });
    expect(buddyLocation("#buddies/evaluation")).toEqual({ view: "evaluation" });
  });
  it("rejects the removed table addresses and unknown routes without redirects", () => {
    for (const hash of ["#buddies/models", "#buddies/models/id", "#buddies/harness", "#buddies/harness/codex", "#buddies/roles", "#buddies/roles/current", "#buddies/router/current", "#buddies/router/health", "#buddies/router/unknown", "#buddies/router/routing/run", "#buddies/evaluation/codex/model/high/other"]) expect(buddyLocation(hash)).toBeNull();
  });
  it("rejects malformed selections and other pages", () => {
    for (const hash of ["#tasks", "", "#buddies/catalog/buddy/%E0%A4%A", "#buddies/catalog/buddy", "#buddies/catalog/unknown/id", "#buddies/catalog/model/id/extra", "#buddies/evaluation/codex/id", "#buddies/evaluation/codex/%E0%A4%A/high", "#buddies/evaluation/codex/id/high/profile/extra"]) expect(buddyLocation(hash)).toBeNull();
  });
});
