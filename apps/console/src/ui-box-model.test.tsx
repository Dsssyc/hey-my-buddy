import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import type { ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { ObjectiveList } from "./ObjectiveList";
import { listFixture, objectiveSummary, objectiveTimelineFixture } from "./objective-fixtures";
import { Status } from "./ui";
import type { ObjectiveTimeline as ObjectiveTimelineData, TimelineRow, TimelineSpan } from "./objective-types";

// Check the rendered cascade and declared decoration geometry. jsdom cannot
// measure widths, paint, native zoom or elementsFromPoint. Host checks those.
// Existing non-defective controls keep their typography and the global reset.
let injected: HTMLStyleElement;
beforeEach(() => {
  injected = document.createElement("style");
  injected.textContent = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
  document.head.append(injected);
});
afterEach(() => { injected.remove(); cleanup(); });
function rule(selector: string) {
  const matches = [...injected.sheet!.cssRules].filter((rule): rule is CSSStyleRule => "selectorText" in rule)
    .filter(rule => rule.selectorText.split(",").map(s => s.trim()).includes(selector));
  expect(matches.length, selector).toBeGreaterThan(0);
  return matches.at(-1)!.style;
}
const OBSERVED_AT = "2026-09-26T08:12:00Z";

function timeline(kinds: Array<Partial<TimelineSpan>>): ObjectiveTimelineData {
  const base: TimelineRow = {
    runId: "r1", parentRunId: null, rootRunId: "r1", title: "短记录与盒模型", titleSource: "title",
    taskSummary: null, summary: null, createdAt: "2026-09-26T01:12:00Z", state: "running", status: "running",
    category: "active", shutdownConfirmed: false, depth: 0, kind: "goal", configuration: null,
    acceptedAt: null, acceptanceVerdict: null,
  };
  const spans = kinds.map((overrides, index) => ({
    spanId: `s-${index}`, runId: "r1", kind: "execution" as const, startAt: null, endAt: null, state: "finished",
    attemptId: null, turnId: null, turnIndex: 1, requestId: null, configuration: null,
    shutdownConfirmed: null, uncertain: false, clockSkew: false, ...overrides,
  })) as TimelineSpan[];
  return {
    objective: objectiveSummary(), observedAt: OBSERVED_AT, cursor: 1, rows: [base], spans, events: [],
    totals: { rows: 1, spans: spans.length, events: 0, allRows: 1 },
    truncated: { rows: false, spans: false, events: false }, scopeComplete: true, filtered: false,
  };
}

function props(data: ObjectiveTimelineData): ObjectiveTimelineProps {
  return {
    summary: data.objective, timeline: data, loading: false, error: "", stale: false,
    newRunIds: new Set<string>(), hidden: false, openedKey: null, openedRunId: null, selection: null,
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
  } as ObjectiveTimelineProps;
}

describe("U5: duration geometry and borders share one boundary", () => {
  it("keeps every rendered variant and state free of width-taking padding/borders", () => {
    render(<ObjectiveTimeline {...props(objectiveTimelineFixture())} />);
    const spans = document.querySelectorAll<HTMLElement>(".tl-scroll .sp");
    expect(spans.length).toBeGreaterThan(0);
    for (const span of spans) {
      for (const state of ["", "selected", "run-member", "failed", "running", "cancelled"]) {
        if (state) span.classList.add(state);
        const css = getComputedStyle(span);
        expect(css.boxSizing, span.className).toBe("border-box");
        expect(css.paddingLeft, span.className).toBe("0px");
        expect(css.paddingRight, span.className).toBe("0px");
        expect(css.borderLeftWidth, span.className).toBe("0px");
        expect(css.borderRightWidth, span.className).toBe("0px");
        expect(css.minWidth, span.className).toBe(span.matches(".routing.failed") ? "14px" : "4px");
        if (state) span.classList.remove(state);
      }
    }
  });

  it("contains all state borders in the shared pseudo-element box instead of enlarging the button", () => {
    const decoration = rule(".sp::before");
    expect(decoration.position).toBe("absolute");
    expect(["0", "0px"]).toContain(decoration.getPropertyValue("inset"));
    expect(decoration.boxSizing).toBe("border-box");
    expect(decoration.pointerEvents).toBe("none");
    expect(rule(".sp.wait::before").border).toBe("1.5px dashed var(--warning)");
    expect(rule(".sp.wait.open::before").borderRightWidth).toBe("0px");
    expect(rule(".sp.routing.running::before").outline).toBe("3px double var(--line-strong)");
    expect(rule(".sp.routing.running::before").outlineOffset).toBe("-3px");
    expect(rule(".sp.queue::before").border).toBe("1px solid var(--line-strong)");
    expect(rule(".sp.unknown::before").border).toBe("1.5px dotted var(--c)");
  });

  it("preserves the short span's percentage position/width without an inline box-model override", () => {
    const data = timeline([
      { spanId: "short", kind: "execution", state: "finished", startAt: "2026-09-26T01:14:00Z", endAt: "2026-09-26T01:14:00.001Z", resultStatus: "failed", shutdownConfirmed: true },
      { spanId: "wait", kind: "host", state: "open", startAt: "2026-09-26T01:14:00.001Z", endAt: null },
    ]);
    const { container } = render(<ObjectiveTimeline {...props(data)} />);
    const failed = container.querySelector<HTMLElement>('.tl-scroll [data-key="span:short"]')!;
    const wait = container.querySelector<HTMLElement>('.tl-scroll [data-key="span:wait"]')!;
    expect(failed.style.width).toBe("0.3%");
    expect(failed.style.left).toMatch(/%$/);
    expect(failed.style.padding).toBe("");
    expect(wait.previousElementSibling).toBe(failed);
    expect(failed.querySelector(".end-mark.bad")).not.toBeNull();
    expect(getComputedStyle(failed.querySelector(".sp-text")!).overflow).toBe("hidden");
    expect(getComputedStyle(failed).getPropertyValue("--tl-item-height")).toBe("22px");
    expect(rule(".sp.queue").getPropertyValue("--tl-item-height")).toBe("8px");
  });

  it("keeps end and event decoration sizes fixed and clips reading padding inside the label", () => {
    expect(rule(".end-mark").width).toBe("15px");
    expect(rule(".end-mark").height).toBe("15px");
    expect(rule(".end-mark").lineHeight).toBe("12px");
    expect(rule(".mk").width).toBe("18px");
    expect(rule(".mk").marginLeft).toBe("-9px");
    expect(rule(".sp-solid-text").padding).toBe("0px 6px");
    expect(rule(".sp-text").overflow).toBe("hidden");
    expect(rule(".sp-text").textOverflow).toBe("ellipsis");
    expect(rule(".sp.unknown .solid").padding).toBe("");
  });
});

describe("U5: audited controls retain the shared global box model and normal sizes", () => {
  it("lets the rendered list use its full width without reserving an idle scrollbar gutter", () => {
    const rows = listFixture();
    const { getByLabelText } = render(<ObjectiveList
      rows={rows} total={rows.length} loading={false} error="" nextCursor={null} reorder={null}
      filter="all" query="" projectId="" hostId="" choices={{ projects: [], hosts: [] }}
      selected={null} rail={false}
      onFilterChange={vi.fn()} onQueryChange={vi.fn()} onProjectChange={vi.fn()} onHostChange={vi.fn()}
      onSelect={vi.fn()} onRetry={vi.fn()} onMore={vi.fn()} onApplyReorder={vi.fn()}
    />);
    const scroll = getByLabelText("工作目标条目");
    const css = getComputedStyle(scroll);
    expect(css.getPropertyValue("scrollbar-gutter")).toBe("auto");
    expect(css.overflowY).toBe("auto");
    expect(css.overflowX).toBe("hidden");
    expect(["0", "0px"]).toContain(css.minHeight);
    expect(css.getPropertyValue("overscroll-behavior")).toBe("contain");
    expect(css.getPropertyValue("scrollbar-width")).not.toBe("none");
    expect(css.getPropertyValue("touch-action")).not.toBe("none");
    expect(scroll.tabIndex).toBe(0);
    scroll.focus();
    expect(document.activeElement).toBe(scroll);
    const entries = scroll.querySelectorAll(".group-heading, .task-row");
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) {
      expect(getComputedStyle(entry).width).toBe("100%");
      expect(getComputedStyle(entry).boxSizing).toBe("border-box");
    }
  });

  it("uses the existing global border-box reset for badges, segments, filters, titles and floating layers", () => {
    const { container } = render(<>
      <Status status="waiting-host" />
      <div className="segmented"><button>档位</button></div>
      <input type="checkbox" />
      <div className="inspector-card-head">标题</div>
      <div className="marker-popover">详情</div>
    </>);
    for (const element of container.querySelectorAll<HTMLElement>(".badge, .segmented button, input, .inspector-card-head, .marker-popover")) {
      expect(getComputedStyle(element).boxSizing, element.className).toBe("border-box");
    }
    const badge = getComputedStyle(container.querySelector(".badge")!);
    expect(badge.padding).toBe("2px 6px");
    expect(badge.lineHeight).not.toBe("1");
    expect(getComputedStyle(container.querySelector(".segmented button")!).padding).toBe("5px 9px");
    expect(getComputedStyle(container.querySelector("input")!).width).toBe("16px");
    expect(rule("*").boxSizing).toBe("border-box");
  });
});
