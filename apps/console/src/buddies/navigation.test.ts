import { describe, expect, it } from "vitest";
import { buddyHash, buddyLocation } from "./navigation";

describe("addresses of the Buddy page", () => {
  it("writes one directory with encoded harness, model and buddy selections", () => {
    expect(buddyHash({ view: "models" })).toBe("#buddies/catalog");
    expect(buddyHash({ view: "models", targetKind: "harness", target: "codex" })).toBe("#buddies/catalog/harness/codex");
    expect(buddyHash({ view: "models", targetKind: "model", target: "codex/openai/gpt" })).toBe("#buddies/catalog/model/codex%2Fopenai%2Fgpt");
    expect(buddyHash({ view: "models", target: "codex:openai:gpt:high" })).toBe("#buddies/catalog/buddy/codex%3Aopenai%3Agpt%3Ahigh");
  });
  it("reads directory and roles selections", () => {
    for (const location of [{ view: "roles" as const, target: "current" },
      ...(["harness", "model", "buddy"] as const).map(targetKind => ({ view: "models" as const, targetKind, target: "a/b c:d" }))]) {
      expect(buddyLocation(buddyHash(location))).toEqual(location);
    }
    expect(buddyLocation("#buddies")).toEqual({ view: "models" });
    expect(buddyLocation("#buddies/catalog")).toEqual({ view: "models" });
  });
  it("rejects the removed table addresses and unknown routes without redirects", () => {
    for (const hash of ["#buddies/models", "#buddies/models/id", "#buddies/harness", "#buddies/harness/codex", "#buddies/router/current"]) expect(buddyLocation(hash)).toBeNull();
  });
  it("rejects malformed selections and other pages", () => {
    for (const hash of ["#tasks", "", "#buddies/catalog/buddy/%E0%A4%A", "#buddies/catalog/buddy", "#buddies/catalog/unknown/id", "#buddies/catalog/model/id/extra"]) expect(buddyLocation(hash)).toBeNull();
  });
});
