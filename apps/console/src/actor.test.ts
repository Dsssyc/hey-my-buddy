import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { actorText, parseActor, workflowOwnerText } from "./actor";

afterEach(async () => { await i18n.changeLanguage("zh"); });

describe("recorded actor roles", () => {
  it("keeps a Host's Lead-like name separate from its role and refuses the retired wire form", () => {
    const host = { role: "host" as const, id: "lead:literal-name" };
    expect(parseActor(host)).toEqual(host);
    expect(actorText(host)).toBe("Host · lead:literal-name");
    expect(actorText({ role: "lead", id: "literal-name" })).toBe("Lead · literal-name");
    expect(parseActor("lead:literal-name")).toBeNull();
    expect(parseActor({ role: "worker", id: "retired-role" })).toBeNull();
    expect(parseActor({ role: "host", id: { token: "invalid" } })).toBeNull();
  });

  it("uses the explicit Lead relationship for an absent Host and records absence honestly", () => {
    expect(workflowOwnerText({}, null)).toBe("未记录");
    expect(workflowOwnerText({ lineage: {
      currentHostId: null, rootLeadRunId: "lead-run",
    } } as Parameters<typeof workflowOwnerText>[0], null)).toBe("Lead · lead-run");
    expect(workflowOwnerText({}, "lead:literal-host")).toBe("lead:literal-host");
  });

  it("writes role labels in the browser's language", async () => {
    expect(actorText({ role: "console", id: "session" })).toBe("控制台 · session");
    await i18n.changeLanguage("en");
    expect(actorText({ role: "console", id: "session" })).toBe("Console · session");
    expect(actorText(null)).toBe("Not recorded");
  });
});
