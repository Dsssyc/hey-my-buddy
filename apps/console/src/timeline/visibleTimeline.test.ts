import { expect, it } from "vitest";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import { timelineScaleInput } from "./scale";
import { visibleTimeline } from "./visibleTimeline";

it("keeps existing row categories, helper identities and every continuation, with only their time facts", () => {
  const timeline = macroTaskTimelineFixture();
  // A helper awaiting its owner is host work even when an accepted root is hidden.
  timeline.rows.find(row => row.runId === "r3")!.category = "host";
  const shown = visibleTimeline(timeline, true);
  expect(shown.rows.map(row => row.runId)).toEqual(["r3", "r4", "r6"]);
  expect(shown.rows[0]).toBe(timeline.rows[2]);
  expect(shown.spans.filter(span => span.runId === "r3")).toEqual(timeline.spans.filter(span => span.runId === "r3"));
  expect(new Set(shown.spans.map(span => span.runId))).toEqual(new Set(["r3", "r4", "r6"]));
  expect(shown.events.every(event => ["r3", "r4", "r6"].includes(event.runId))).toBe(true);
  expect(timelineScaleInput(shown).domain!.startMs).toBe(Date.parse("2026-09-26T01:38:00Z"));
  expect(visibleTimeline(timeline, false)).toBe(timeline);
});

it("keeps active owned rows when their ended Lead is hidden", () => {
  const timeline = macroTaskTimelineFixture();
  timeline.rows[1]!.delegationAuthority = true;
  timeline.rows[4]!.rootLeadRunId = "r2";
  const shown = visibleTimeline(timeline, true);
  expect(shown.rows.map(row => row.runId)).toEqual(["r4", "r6"]);
  expect(shown.rows[0]!.rootLeadRunId).toBe("r2");
});
