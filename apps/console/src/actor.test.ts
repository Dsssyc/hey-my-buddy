import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { eventName } from "./macro-task-display";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { actorText, parseActor, stopActorText, workflowOwnerText } from "./actor";

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


it("uses one stop attribution in both languages and keeps the event actor as its source", async () => {
  const source = macroTaskTimelineFixture().events[0]!;
  for (const [language, consoleWords, hostWords] of [["zh", "控制台停止", "Host 停止"], ["en", "Stopped by Console", "Stopped by Host"]]) {
    await i18n.changeLanguage(language);
    expect(stopActorText("console")).toBe(consoleWords);
    expect(eventName({ ...source, kind: "cancel", actor: { role: "console", id: "console-session" } })).toBe(consoleWords);
    expect(eventName({ ...source, kind: "cancel", actor: { role: "host", id: "host-session" }, stopInitiator: "console" })).toBe(hostWords);
  }
});
