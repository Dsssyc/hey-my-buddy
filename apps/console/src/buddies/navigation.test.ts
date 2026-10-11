import { describe, expect, it } from "vitest";
import { buddyHash, buddyLocation } from "./navigation";

describe("addresses of the Buddy page", () => {
  it("writes a view and an encoded target", () => {
    expect(buddyHash({ view: "models" })).toBe("#buddies/models");
    expect(buddyHash({ view: "harness", target: "codex" })).toBe("#buddies/harness/codex");
    expect(buddyHash({ view: "models", target: "codex:openai:gpt-6-sol:high" })).toBe("#buddies/models/codex%3Aopenai%3Agpt-6-sol%3Ahigh");
  });

  it("reads back what it wrote", () => {
    for (const location of [{ view: "roles" as const, target: "health" }, { view: "models" as const, target: "a/b c:d" }]) {
      expect(buddyLocation(buddyHash(location))).toEqual(location);
    }
    expect(buddyLocation("#buddies/harness")).toEqual({ view: "harness", target: undefined });
  });

  it("opens the models for the bare address and for a view the page does not have", () => {
    expect(buddyLocation("#buddies")).toEqual({ view: "models" });
    expect(buddyLocation("#buddies/router/current")).toEqual({ view: "models" });
  });

  it("gives nothing for another page and keeps the view when a target cannot be decoded", () => {
    expect(buddyLocation("#tasks")).toBeNull();
    expect(buddyLocation("")).toBeNull();
    expect(buddyLocation("#buddies/models/%E0%A4%A")).toEqual({ view: "models" });
  });
});
