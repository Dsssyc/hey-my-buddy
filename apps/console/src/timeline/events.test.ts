import { describe, expect, it } from "vitest";
import type { TimelineEvent } from "../macro-task-types";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import { eventActions } from "./events";

const seed = macroTaskTimelineFixture().events[0]!;
const event = (seq: number, kind: string, extra: Partial<TimelineEvent> = {}): TimelineEvent => ({ ...seed, integrationId: null, seq, kind, ...extra });

describe("one mark per Host action", () => {
  it("pairs adjacent review rejection and continuation without a time heuristic", () => {
    const rejected = event(10, "reject"), continued = event(11, "continue", { at: "2026-10-17T08:00:00.001Z" });
    expect(eventActions([continued, rejected])).toEqual([{ event: rejected, relatedEvents: [continued] }]);
    expect(eventActions([rejected, { ...continued, seq: 12 }])).toHaveLength(2);
    expect(eventActions([rejected, { ...continued, runId: "another-task" }])).toHaveLength(2);
    expect(eventActions([continued, { ...rejected, seq: 12 }])).toHaveLength(2);
  });
  it("binds integration facts only to the acceptance with the same integration and task", () => {
    const integration = event(20, "integrate", { integrationId: "integrated" });
    const acceptance = event(30, "accept", { integrationId: "integrated" });
    expect(eventActions([integration, acceptance])).toEqual([{ event: acceptance, relatedEvents: [integration] }]);
    for (const other of [{ integrationId: "other" }, { integrationId: null }, { runId: "another-task" }]) {
      expect(eventActions([integration, { ...acceptance, ...other }])[0]!.relatedEvents).toBeUndefined();
    }
  });
});
