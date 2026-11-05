import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { eventName } from "./macro-task-display";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { actorText, parseActor, stopActorText, workflowOwnerText } from "./actor";

afterEach(async () => { await i18n.changeLanguage("zh"); });

describe("recorded actor roles", () => {
  it("shows Host by role and preserves real Lead identities", () => {
    expect(parseActor({ role: "host" })).toEqual({ role: "host" });
    expect(actorText({ role: "host" })).toBe("Host");
    expect(actorText({ role: "host", id: "historical-name" })).toBe("Host");
    expect(parseActor({ role: "host", id: "historical-name" })).toEqual({ role: "host" });
    expect(actorText({ role: "lead", id: "literal-name" })).toBe("Lead · literal-name");
    expect(parseActor("lead:literal-name")).toBeNull();
    expect(parseActor({ role: "worker", id: "retired-role" })).toBeNull();
    expect(parseActor({ role: "host", id: { token: "invalid" } })).toBeNull();
  });

  it("uses the explicit Lead relationship and otherwise shows Host", () => {
    expect(workflowOwnerText({})).toBe("Host");
    expect(workflowOwnerText({ lineage: {
      kind: "micro-task", parentRunId: null, rootRunId: "run", project: null, buddy: null, rootLeadRunId: "lead-run",
    } })).toBe("Lead · lead-run");
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
