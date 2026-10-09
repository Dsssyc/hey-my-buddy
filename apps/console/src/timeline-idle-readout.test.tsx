import { Profiler } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline, type ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { idleReadout } from "./TimelineReadout";
import { createTimelineLayout } from "./objective-timeline-layout";
import { scaleTimeline } from "./objective-timeline-scale";
import { objectiveTimelineFixture } from "./objective-fixtures";
import { clockTime } from "./objective-display";

const base = Date.parse("2026-09-26T00:00:00Z");
const at = (minute: number) => new Date(base + minute * 60_000).toISOString();
function data() {
  const timeline = objectiveTimelineFixture();
  timeline.rows = timeline.rows.filter(row => row.parentRunId === null).slice(0, 2);
  const template = timeline.spans.find(span => span.kind === "execution")!;
  timeline.spans = timeline.rows.map((row, index) => ({ ...template, spanId: `readout-${index}`, runId: row.runId,
    startAt: at(index * 190), endAt: at(index * 190 + 10), state: "finished", uncertain: false,
    shutdownConfirmed: true, clockSkew: false }));
  timeline.events = [];
  timeline.observedAt = at(200);
  timeline.scopeComplete = true;
  timeline.filtered = false;
  timeline.truncated = { rows: false, spans: false, events: false };
  return timeline;
}
function props(timeline = data()): ObjectiveTimelineProps {
  return { summary: timeline.objective, timeline, loading: false, error: "", stale: false,
    newRunIds: new Set(), hidden: false, selection: null, openedKey: null, openedRunId: null,
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn() };
}
const rect = (left = 0, width = 1000, top = 0, height = 600) => ({ left, width, top, height,
  right: left + width, bottom: top + height, x: left, y: top, toJSON() {} } as DOMRect);
function geometry() {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(1000);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    if (this.classList.contains("tl-overlay")) {
      const grid = this.closest<HTMLElement>(".tl-grid")!;
      const width = Number(/\+ ([\d.]+)px/.exec(grid.style.width)![1]) - 12;
      const scroll = grid.closest<HTMLElement>(".tl-scroll")!;
      return rect(240 - scroll.scrollLeft, width);
    }
    if (this.classList.contains("timeline-readout")) return rect(0, 220, 0, 18);
    if (this.classList.contains("idle-block")) return rect(240 + Number.parseFloat(this.style.left) / 100 * 748, 32, 0, 28);
    if (this.classList.contains("shared-popover")) return rect(0, 350, 0, 90);
    return rect();
  });
}
function move(x: number, pointerType = "mouse") {
  const event = new MouseEvent("pointermove", { bubbles: true, clientX: x, clientY: 80 });
  Object.defineProperty(event, "pointerType", { value: pointerType });
  act(() => document.querySelector(".tl-grid")!.dispatchEvent(event));
}
const line = () => document.querySelector<HTMLDivElement>(".timeline-readout-line")!;
const readout = () => document.querySelector<HTMLOutputElement>(".timeline-readout")!;

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("U10 idle evidence and U11 native readout", () => {
  it("reads real time from the existing map, reads complete idle ranges, then disappears", () => {
    geometry();
    const timeline = data();
    render(<ObjectiveTimeline {...props(timeline)} />);
    const scale = scaleTimeline(createTimelineLayout(timeline), 748);
    expect(line().hidden).toBe(true);
    move(240 + scale.position(at(5))! / 100 * 748);
    expect(line().hidden).toBe(false);
    expect(readout().textContent).toBe(clockTime(at(5)));
    expect(Number.parseFloat(line().style.left)).toBeCloseTo(scale.position(at(5))!, 7);
    const gap = scale.gaps[0]!;
    move(240 + (gap.fromPercent + gap.toPercent) / 200 * 748);
    expect(readout().textContent).toBe(idleReadout(gap));
    expect(readout().textContent).toContain("2026");
    expect(readout().textContent).toContain("3 时 0 分");
    fireEvent.pointerLeave(document.querySelector(".tl-grid")!);
    expect(line().hidden).toBe(true);
    expect(readout().hidden).toBe(true);
  });

  it("uses the updated scale after zoom and reads the scrolled track rather than viewport coordinates", () => {
    geometry();
    const timeline = data();
    render(<ObjectiveTimeline {...props(timeline)} />);
    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    const scale = scaleTimeline(createTimelineLayout(timeline), 748, (748 - 32) / 20 * 1.5);
    const scroller = document.querySelector<HTMLElement>(".tl-scroll")!;
    const overlay = document.querySelector<HTMLElement>(".tl-overlay")!;
    const xAt = (minute: number) => overlay.getBoundingClientRect().left + scale.position(at(minute))! / 100 * overlay.getBoundingClientRect().width;
    move(xAt(195));
    expect(readout().textContent).toBe(clockTime(at(195)));
    scroller.scrollLeft += 38;
    fireEvent.scroll(scroller);
    expect(readout().hidden).toBe(true);
    move(xAt(195));
    expect(readout().textContent).toBe(clockTime(at(195)));
    expect(Number.parseFloat(line().style.left)).toBeCloseTo(scale.position(at(195))!, 2);
  });

  it("pointer samples update only the two readout nodes and cause no React commits or row mutations", () => {
    geometry();
    const commit = vi.fn();
    render(<Profiler id="timeline" onRender={commit}><ObjectiveTimeline {...props()} /></Profiler>);
    const commits = commit.mock.calls.length;
    const rows = [...document.querySelectorAll(".tl-row")];
    const markup = rows.map(row => row.outerHTML);
    const nodes = [...document.querySelectorAll(".sp, .mk, .flag")];
    for (const x of [250, 300, 400, 620, 900, 950]) move(x);
    expect(commit).toHaveBeenCalledTimes(commits);
    expect([...document.querySelectorAll(".tl-row")]).toEqual(rows);
    expect(rows.map(row => row.outerHTML)).toEqual(markup);
    expect([...document.querySelectorAll(".sp, .mk, .flag")]).toEqual(nodes);
    expect(line().hidden).toBe(false);
    expect(readout().textContent).not.toBe("");
  });

  it("hides on sticky labels, touch, scroll, unavailable time, hidden and list views", () => {
    geometry();
    const view = render(<ObjectiveTimeline {...props()} />);
    move(500);
    expect(line().hidden).toBe(false);
    move(100);
    expect(line().hidden).toBe(true);
    move(500, "touch");
    expect(line().hidden).toBe(true);
    move(500);
    fireEvent.scroll(document.querySelector(".tl-scroll")!);
    expect(line().hidden).toBe(true);
    view.rerender(<ObjectiveTimeline {...props()} hidden />);
    expect(document.querySelector(".timeline-readout-line")).toBeNull();
    view.rerender(<ObjectiveTimeline {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "列表" }));
    expect(document.querySelector(".timeline-readout-line")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "时间轴" }));
    const empty = { ...data(), spans: [], events: [] };
    view.rerender(<ObjectiveTimeline {...props(empty)} />);
    move(500);
    expect(line().hidden).toBe(true);
  });

  it("shows idle start/end/duration on keyboard focus without any expansion and closes with Esc", () => {
    geometry();
    render(<ObjectiveTimeline {...props()} />);
    const block = document.querySelector<HTMLElement>(".idle-block")!;
    expect(block.tabIndex).toBe(0);
    act(() => block.focus());
    expect(screen.queryByRole("dialog", { name: "空闲区间" })).not.toBeNull();
    const dialog = screen.getByRole("dialog", { name: "空闲区间" });
    const scale = scaleTimeline(createTimelineLayout(data()), 748);
    expect(dialog.textContent).toBe(idleReadout(scale.gaps[0]!));
    expect(block.style.width).toBe("32px");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "空闲区间" })).toBeNull();
    expect(document.activeElement).toBe(block);
    expect(screen.queryByRole("button", { name: /展开.*空闲|收起空闲|折叠空闲/ })).toBeNull();
  });

  it("Escape from inside the idle dialog closes it without reopening on returned anchor focus", async () => {
    geometry();
    render(<ObjectiveTimeline {...props()} />);
    const block = document.querySelector<HTMLElement>(".idle-block")!;
    act(() => block.focus());
    expect(screen.queryByRole("dialog", { name: "空闲区间" })).not.toBeNull();
    const dialog = screen.getByRole("dialog", { name: "空闲区间" });
    act(() => dialog.focus());
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(document.activeElement).toBe(block);
    expect(screen.queryByRole("dialog", { name: "空闲区间" })).toBeNull();
    await act(async () => Promise.resolve());
    act(() => screen.getByRole("button", { name: "图例" }).focus());
    act(() => block.focus());
    expect(screen.getByRole("dialog", { name: "空闲区间" })).toBeTruthy();
  });

  it.each(["incomplete", "clock"])("drops the focused idle popup when refresh makes %s evidence unsafe", reason => {
    geometry();
    const timeline = data();
    const view = render(<ObjectiveTimeline {...props(timeline)} />);
    act(() => document.querySelector<HTMLElement>(".idle-block")!.focus());
    expect(screen.getByRole("dialog", { name: "空闲区间" })).toBeTruthy();
    const unsafe = structuredClone(timeline);
    if (reason === "incomplete") unsafe.scopeComplete = false;
    else unsafe.spans[0]!.clockSkew = true;
    view.rerender(<ObjectiveTimeline {...props(unsafe)} />);
    expect(document.querySelector(".idle-block")).toBeNull();
    expect(screen.queryByRole("dialog", { name: "空闲区间" })).toBeNull();
  });

  it.each(["incomplete", "filtered", "truncated-rows", "truncated-spans", "truncated-events", "clock", "missing", "reversed", "uncertain-tail"])("never infers idle from %s evidence", reason => {
    geometry();
    const timeline = data();
    if (reason === "incomplete") timeline.scopeComplete = false;
    if (reason === "filtered") timeline.filtered = true;
    if (reason.startsWith("truncated-")) timeline.truncated[reason.slice(10) as "rows" | "spans" | "events"] = true;
    if (reason === "clock") timeline.spans[0]!.clockSkew = true;
    if (reason === "missing") timeline.spans[0]!.endAt = null;
    if (reason === "reversed") timeline.spans[0]!.endAt = at(-1);
    if (reason === "uncertain-tail") { timeline.spans[0]!.uncertain = true; timeline.spans[0]!.shutdownConfirmed = false; }
    render(<ObjectiveTimeline {...props(timeline)} />);
    expect(document.querySelector(".idle-block")).toBeNull();
    expect(document.querySelector(".fold-band")).toBeNull();
    move(500);
    expect(readout().textContent).not.toContain("空闲");
  });

  it("the now and pointer lines remain weak inside the existing layer below tracks", () => {
    const style = document.createElement("style");
    style.textContent = readFileSync("src/styles.css", "utf8");
    document.head.append(style);
    try {
      geometry();
      render(<ObjectiveTimeline {...props()} />);
      const overlay = document.querySelector<HTMLElement>(".tl-overlay")!;
      const track = document.querySelector<HTMLElement>(".tl-row > .tl-track")!;
      const now = document.querySelector<HTMLElement>(".now-line")!;
      expect(line().parentElement).toBe(overlay);
      expect(now.parentElement).toBe(overlay);
      expect(Number(getComputedStyle(overlay).zIndex)).toBeLessThan(Number(getComputedStyle(track).zIndex));
      expect(getComputedStyle(overlay).pointerEvents).toBe("none");
      for (const node of [now, line()]) expect(Number(getComputedStyle(node).opacity)).toBeLessThanOrEqual(.5);
      expect(getComputedStyle(now).width).toBe("1px");
    } finally { style.remove(); } // normal test fixture lifecycle
  });
});
