import { readFileSync } from "node:fs";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "../components/ui/tooltip";
import { macroTaskTimelineSchema } from "../macro-task-types";
import { buddyColorKey } from "../macro-task-display";
import { isFailedRow } from "./failure";
import { RowName } from "./RowName";
import { Track } from "./Tracks";
import { createTimelineScale } from "./scale";

afterEach(cleanup);

it.each([
  ["failed", "concluded", "concluded", true],
  ["cancelled", "concluded", "concluded", false],
  ["delivered", "concluded", "concluded", false],
  [null, "failed", "failed", true],
  [null, "executing", "failed", true],
  [null, "cancelled", "cancelled", false],
  [null, "accepted", "completed", false],
] as const)("reads conclusion %s and states %s/%s as failure=%s", (conclusionStatus, state, status, failed) => {
  expect(isFailedRow({ conclusionStatus, state, status })).toBe(failed);
});

// The Python check creates a private board, calls the real lifecycle writers,
// and hands their unmodified timeline reply to this parser and both renderers.
const writerFixture = process.env.HMB_TIMELINE_CONCLUSIONS;
if (writerFixture) it("renders stored Host conclusions from the real writer reply", () => {
  const timeline = macroTaskTimelineSchema.parse(JSON.parse(readFileSync(writerFixture, "utf8")));
  expect(new Set(timeline.rows.map(row => row.title))).toEqual(new Set(["failed", "cancelled", "delivered", "pending"]));
  const nowMs = Date.parse(timeline.observedAt);
  const scale = createTimelineScale({ domain: { startMs: nowMs - 60_000, endMs: nowMs + 1000 },
    busy: [], instants: [], canFold: false, nowAtEnd: false }, 800);
  for (const row of timeline.rows) {
    const spans = timeline.spans.filter(span => span.runId === row.runId);
    const events = timeline.events.filter(event => event.runId === row.runId);
    const colors = new Map(spans.filter(span => span.kind === "execution")
      .map(span => [buddyColorKey(span.buddy), "#6d7ec0"]));
    // Case names come from the Python writer inputs, independently of the parsed fact.
    expect(row.conclusionStatus).toBe(row.title === "pending" ? null : row.title);
    const failed = row.title === "failed";
    render(<TooltipProvider>
      <RowName row={row} rows={timeline.rows} tabIndex={0} current={false} fresh={false}
        onClick={vi.fn()} onEnter={vi.fn()} onLeave={vi.fn()} onFocus={vi.fn()} onBlur={vi.fn()}/>
      <Track row={row} spans={spans} events={events} scale={scale} nowMs={nowMs} ongoing={false}
        selected={false} buddyColors={colors} onSelect={vi.fn()} onOpen={vi.fn()}
        onHover={vi.fn()} onFocusSpan={vi.fn()} onMarker={vi.fn()}/>
    </TooltipProvider>);
    const mark = document.querySelector('[data-slot=failed-conclusion-mark]');
    expect(!!mark?.querySelector("circle"), `failure circle for ${row.title}`).toBe(failed);
    expect(!!mark?.querySelector("path"), `failure cross for ${row.title}`).toBe(failed);
    expect(document.querySelector('[data-slot=timeline-row-name]')?.getAttribute("data-state-tone") === "fail",
      `failure colour for ${row.title}`).toBe(failed);
    cleanup();
  }
});
