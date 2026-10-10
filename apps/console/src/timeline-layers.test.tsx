import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import type { ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { objectiveSummary, objectiveTimelineFixture } from "./objective-fixtures";
import type { ObjectiveTimeline as ObjectiveTimelineData, TimelineRow, TimelineSpan } from "./objective-types";

// These are DOM/cascade regressions, not pixel or hit-testing evidence. In
// particular, child z-index cannot escape a transformed/filtered span parent.
// Host retains the real Chrome before evidence and verifies the repaired hits.
let injected: HTMLStyleElement;
beforeEach(() => {
  injected = document.createElement("style");
  injected.textContent = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
  document.head.append(injected);
  // jsdom has no pointer layout or focus-visible heuristic. Apply the actual
  // state rules via synthetic classes to exercise their cascade on the DOM.
  const stateRules = [...injected.sheet!.cssRules].filter(
    (rule): rule is CSSStyleRule => "selectorText" in rule);
  for (const rule of stateRules) {
    if (!/:hover|:focus-visible/.test(rule.selectorText)) continue;
    injected.sheet!.insertRule(`${rule.selectorText.replaceAll(":hover", ".probe-hover").replaceAll(":focus-visible", ".probe-focus")} { ${rule.style.cssText} }`, injected.sheet!.cssRules.length);
  }
});
afterEach(() => { injected.remove(); cleanup(); });

function noSpanContext(element: HTMLElement) {
  const css = getComputedStyle(element);
  expect(css.position, element.className).toBe("absolute");
  for (const property of ["transform", "filter", "perspective", "backdrop-filter", "translate", "rotate", "scale"]) {
    expect(["", "none"], `${element.className}: ${property}`).toContain(css.getPropertyValue(property));
  }
  expect(["", "auto"], `${element.className}: z-index`).toContain(css.zIndex);
  expect(["", "1"]).toContain(css.opacity);
  expect(["", "auto"]).toContain(css.isolation);
  expect(["", "normal"]).toContain(css.mixBlendMode);
  expect(["", "auto"]).toContain(css.willChange);
  expect(["", "none"]).toContain(css.contain);
  if (element.classList.contains("sp")) expect(css.overflow).toBe("visible");
}
function rules(selector: string) {
  return [...injected.sheet!.cssRules].filter((rule): rule is CSSStyleRule => "selectorText" in rule)
    .filter(rule => rule.selectorText.split(",").map(s => s.trim()).includes(selector));
}
function layer(element: HTMLElement) {
  const value = getComputedStyle(element).zIndex;
  const token = /var\((--tl-layer-[a-z]+),\s*(\d+)\)/.exec(value);
  // jsdom does not substitute var(). Resolve the actual track token, not just
  // the fallback string: the rendered hierarchy is the subject of this check.
  return token ? Number(getComputedStyle(element.closest(".tl-track")!).getPropertyValue(token[1]!) || token[2]) : Number(value);
}

const FIXTURE_OBSERVED_AT = "2026-09-26T08:12:00Z";

/** Row 1: a short failed execution immediately followed by the open Host wait. */
function row(overrides: Partial<TimelineRow>): TimelineRow {
  return {
    runId: "r1", parentRunId: null, rootRunId: "r1",
    title: "已结束的短执行紧接着等待 Host", titleSource: "title", taskSummary: null, summary: null,
    createdAt: "2026-09-26T01:12:00Z", state: "running", status: "running", category: "active",
    shutdownConfirmed: false, depth: 0, kind: "goal", configuration: null,
    acceptedAt: null, acceptanceVerdict: null,
    ...overrides,
  };
}

function span(overrides: Partial<TimelineSpan>): TimelineSpan {
  return {
    spanId: "s", runId: "r1", kind: "execution", startAt: null, endAt: null, state: "finished",
    attemptId: null, turnId: null, turnIndex: null, requestId: null, configuration: null,
    shutdownConfirmed: null, uncertain: false, clockSkew: false,
    ...overrides,
  };
}

function timeline(): ObjectiveTimelineData {
  const spans: TimelineSpan[] = [
    span({ spanId: "s-exec", runId: "r1", kind: "execution", state: "finished", startAt: "2026-09-26T01:14:00Z",
      endAt: "2026-09-26T01:15:00Z", attemptId: "att-1", turnIndex: 1, resultStatus: "failed", error: "测试命令超时",
      shutdownConfirmed: true, configuration: { adapter: "codex", provider: "openai", model: "gpt-5-codex", effort: "max" } }),
    span({ spanId: "s-wait", runId: "r1", kind: "host", state: "open", startAt: "2026-09-26T01:15:00Z",
      endAt: null, requestId: "req-wait", summary: "等待 Host 决定" }),
  ];
  return {
    objective: objectiveSummary(), observedAt: FIXTURE_OBSERVED_AT, cursor: 1, rows: [row({})], spans, events: [],
    totals: { rows: 1, spans: 2, events: 0, allRows: 1 },
    truncated: { rows: false, spans: false, events: false }, scopeComplete: true, filtered: false,
  };
}

function props(data: ObjectiveTimelineData, overrides: Record<string, unknown> = {}): ObjectiveTimelineProps {
  return {
    summary: data.objective, timeline: data, loading: false, error: "", stale: false,
    newRunIds: new Set<string>(), hidden: false, openedKey: null, openedRunId: null, selection: null,
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
    ...overrides,
  } as ObjectiveTimelineProps;
}

const bar = (key: string) => document.querySelector<HTMLElement>(`.tl-scroll [data-key="${key}"]`);

function unknownTailTimeline(): ObjectiveTimelineData {
  const data = timeline();
  return {
    ...data, observedAt: "2026-09-26T01:30:00Z",
    spans: [span({ spanId: "s-unknown", startAt: "2026-09-26T01:14:00Z", endAt: "2026-09-26T01:18:00Z",
      uncertain: true, shutdownConfirmed: false, turnIndex: 1, configuration: data.spans[0]!.configuration })],
    totals: { ...data.totals, spans: 1 },
  };
}

describe("U2: span descendants share the track stacking context", () => {
  it.each(["default", "selected", "run-member", "hover", "keyboard focus"])("keeps adjacent failed execution / Host wait parents transparent in %s state", state => {
    const data = timeline();
    const overrides = state === "selected" ? { selection: { type: "item", key: "span:s-wait" } }
      : state === "run-member" ? { selection: { type: "run", runId: "r1" } } : {};
    const onSelectItem = vi.fn();
    const onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...props(data, { ...overrides, onSelectItem, onOpenItem })} />);
    const failed = bar("span:s-exec")!;
    const wait = bar("span:s-wait")!;
    expect(wait.previousElementSibling).toBe(failed);
    if (state === "hover") wait.classList.add("probe-hover");
    if (state === "keyboard focus") { wait.focus(); wait.classList.add("probe-focus"); }
    noSpanContext(failed);
    noSpanContext(wait);
    const mark = failed.querySelector<HTMLElement>(".end-mark.bad")!;
    const text = wait.querySelector<HTMLElement>(".sp-text")!;
    expect(mark.parentElement).toBe(failed);
    expect(text.parentElement).toBe(wait);
    expect(layer(mark)).toBeGreaterThan(0);
    expect(layer(text)).toBeGreaterThan(layer(mark));
    // The label paints above marks, but its line-height box must not steal a
    // neighbouring end-mark hit. The owning button still handles label clicks.
    expect(getComputedStyle(text).pointerEvents).toBe("none");
    expect(getComputedStyle(mark).pointerEvents).not.toBe("none");
    // DOM bubbling proves the marker selects its owning execution. It does not
    // prove that a browser coordinate will reach this marker; Host checks that.
    fireEvent.click(mark);
    expect(onSelectItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:s-exec" }));
    fireEvent.keyDown(failed, { key: "Enter" });
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:s-exec" }));
    fireEvent.click(text);
    expect(onSelectItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:s-wait" }));
    expect(failed.style.width).not.toBe("");
    expect(wait.style.width).not.toBe("");
  });

  it("keeps queue, routing failure, running pulse and unknown-tail parents out of stacking contexts", () => {
    const data = objectiveTimelineFixture();
    const route = data.spans.find(s => s.spanId === "s-r2-r")!;
    route.resultStatus = "failed";
    render(<ObjectiveTimeline {...props(data)} />);
    fireEvent.click(document.querySelector<HTMLButtonElement>(".markers-toggle")!);
    zoomGeometry("max"); // A wide route retains its cross.
    const spans = [...document.querySelectorAll<HTMLElement>(".tl-scroll .sp, .tl-scroll .mk, .tl-scroll .flag")];
    for (const span of spans) {
      span.classList.add("selected", "run-member", "probe-hover", "probe-focus", "open");
      noSpanContext(span);
    }
    const cross = document.querySelector<HTMLElement>(".tl-scroll .routing-cross")!;
    expect(cross).not.toBeNull();
    expect(layer(cross)).toBeGreaterThan(0);
    const pulse = document.querySelector<HTMLElement>(".tl-scroll .pulse")!;
    expect(pulse).not.toBeNull();
    expect(layer(pulse)).toBeGreaterThan(0);
    expect(getComputedStyle(pulse).pointerEvents).toBe("none");
    const solid = document.querySelector<HTMLElement>(".tl-scroll .solid")!;
    expect(solid.textContent).toBe("");
    expect(["", "auto"]).toContain(getComputedStyle(solid).zIndex);
    const solidText = solid.parentElement!.querySelector<HTMLElement>(".sp-solid-text")!;
    expect(solid.contains(solidText)).toBe(false);
    expect(layer(solidText)).toBeGreaterThan(layer(cross));
  });

  it("draws interaction decoration below glyphs and text without raising a span background or intercepting clicks", () => {
    render(<ObjectiveTimeline {...props(timeline())} />);
    const decorations = rules(".sp::after");
    expect(decorations).toHaveLength(1);
    const css = decorations[0]!.style;
    expect(css.position).toBe("absolute");
    expect(["0", "0px"]).toContain(css.getPropertyValue("inset"));
    expect(css.pointerEvents).toBe("none");
    const token = /var\((--tl-layer-[a-z]+),\s*\d+\)/.exec(css.zIndex)!;
    expect(token).not.toBeNull();
    const track = getComputedStyle(bar("span:s-exec")!.closest(".tl-track")!);
    expect(Number(track.getPropertyValue(token[1]!))).toBeLessThan(layer(bar("span:s-exec")!.querySelector<HTMLElement>(".end-mark")!));
    expect(Number(track.getPropertyValue(token[1]!))).toBeLessThan(layer(bar("span:s-wait")!.querySelector<HTMLElement>(".sp-text")!));
    expect(rules(".sp.selected::after").some(rule => rule.style.boxShadow !== "")).toBe(true);
    expect(rules(".sp.run-member::after").some(rule => rule.style.boxShadow !== "")).toBe(true);
    expect(rules(".sp:hover::after").some(rule => rule.style.outline !== "")).toBe(true);
    expect(rules(".sp:focus-visible::after").some(rule => rule.style.outline !== "")).toBe(true);
  });
});

describe("U2: unknown-tail text yields to overlapping spans on the same row", () => {
  const at = (time: string) => `2026-09-26T${time}Z`;
  const following = { spanId: "s-next", kind: "host" as const, state: "open", startAt: at("01:18:00"), endAt: null };

  it.each<{ name: string; next: Partial<TimelineSpan>; overlaps: boolean }>([
    { name: "open Host wait at the recorded end", next: {}, overlaps: true },
    { name: "closed Host wait inside the tail", next: { state: "finished", startAt: at("01:20:00"), endAt: at("01:22:00") }, overlaps: true },
    { name: "later execution", next: { kind: "execution", state: "finished", startAt: at("01:20:00"), endAt: at("01:22:00"), shutdownConfirmed: true }, overlaps: true },
    { name: "later queue", next: { kind: "queue", state: "claimed", startAt: at("01:20:00"), endAt: at("01:22:00") }, overlaps: true },
    { name: "later routing", next: { kind: "routing", state: "finished", startAt: at("01:20:00"), endAt: at("01:22:00") }, overlaps: true },
    { name: "span crossing the recorded end", next: { startAt: at("01:16:00"), endAt: at("01:20:00") }, overlaps: true },
    { name: "one millisecond past the tail start", next: { startAt: at("01:16:00"), endAt: at("01:18:00.001") }, overlaps: true },
    { name: "one millisecond before the tail end", next: { startAt: at("01:29:59.999"), endAt: at("01:32:00") }, overlaps: true },
    { name: "earlier disjoint span", next: { startAt: at("01:15:00"), endAt: at("01:16:00") }, overlaps: false },
    { name: "later disjoint span", next: { startAt: at("01:31:00"), endAt: at("01:32:00") }, overlaps: false },
    { name: "end touching the tail start", next: { startAt: at("01:16:00"), endAt: at("01:18:00") }, overlaps: false },
    { name: "start touching the tail end", next: { startAt: at("01:30:00"), endAt: at("01:32:00") }, overlaps: false },
    { name: "zero-duration span inside the tail", next: { startAt: at("01:20:00"), endAt: at("01:20:00") }, overlaps: false },
    { name: "reversed span", next: { startAt: at("01:22:00"), endAt: at("01:20:00") }, overlaps: false },
    { name: "overlap on another row", next: { runId: "r2" }, overlaps: false },
    { name: "missing start", next: { startAt: null }, overlaps: false },
    { name: "invalid start", next: { startAt: "invalid" }, overlaps: false },
    { name: "terminal execution with missing end", next: { kind: "execution", state: "finished", shutdownConfirmed: true }, overlaps: false },
    { name: "invalid end", next: { state: "finished", endAt: "invalid" }, overlaps: false },
    { name: "open execution with an observed end", next: { kind: "execution", state: "running" }, overlaps: true },
  ])("handles $name while preserving the unknown evidence", ({ next, overlaps }) => {
    const data = unknownTailTimeline();
    const view = render(<ObjectiveTimeline {...props(data)} />);
    const unknown = bar("span:s-unknown")!;
    expect(unknown.querySelector(".sp-tail")!.textContent).toBe("结束未确认");
    const title = unknown.title;
    const aria = unknown.getAttribute("aria-label");
    const solidText = unknown.querySelector(".sp-solid-text")!.textContent;
    expect(solidText).toContain("第1轮");
    const childClasses = [...unknown.children].map(child => child.className);
    const other = span({ ...following, ...next });
    // Source order is deliberately not chronology: use the row's facts,
    // rather than treating only later DOM siblings as possible overlaps.
    view.rerender(<ObjectiveTimeline {...props({ ...data,
      rows: other.runId === "r1" ? data.rows : [...data.rows, row({ runId: "r2", rootRunId: "r2" })],
      spans: [other, ...data.spans],
      totals: { ...data.totals, rows: other.runId === "r1" ? 1 : 2, spans: 2 },
    })} />);
    const updated = bar("span:s-unknown")!;
    expect(updated.querySelector(".sp-tail")!.textContent).toBe(overlaps ? "" : "结束未确认");
    expect(updated.title).toBe(title);
    expect(updated.getAttribute("aria-label")).toBe(aria);
    expect(aria).toContain("结束未确认");
    expect(updated.classList.contains("unknown")).toBe(true);
    // jsdom cannot resolve var() inside this border shorthand. The unchanged
    // unknown class must still match the original dotted pseudo-border rule.
    expect(rules(".sp.unknown::before").some(rule => rule.style.border.includes("dotted"))).toBe(true);
    const mark = updated.querySelector(".end-mark.warn")!;
    expect(mark.textContent).toBe("?");
    expect(mark.getAttribute("aria-hidden")).toBe("true");
    expect(updated.querySelector(".sp-solid-text")!.textContent).toBe(solidText);
    expect([...updated.children].map(child => child.className)).toEqual(childClasses);
  });

  // This 100-minute fit canvas has 772 real pixels plus 16px leading room:
  // tail text needs 84px (10px mark clearance, 24px insets, five 10px glyphs).
  it.each([
    { name: "below", tailMs: Math.floor(84 * 60000 / 7.72), text: "" },
    { name: "at", tailMs: Math.ceil(84 * 60000 / 7.72), text: "结束未确认" },
    { name: "above", tailMs: Math.ceil(84 * 60000 / 7.72) + 1, text: "结束未确认" },
  ])("uses the pixel tail text threshold with mark clearance and insets $name the boundary", ({ tailMs, text }) => {
    const data = unknownTailTimeline();
    data.observedAt = at("02:40:00");
    data.spans[0]!.startAt = at("01:00:00");
    data.spans[0]!.endAt = new Date(Date.parse(data.observedAt) - tailMs).toISOString();
    render(<ObjectiveTimeline {...props(data)} />);
    expect(bar("span:s-unknown")!.querySelector(".sp-tail")!.textContent).toBe(text);
  });

  it.each([
    { end: "01:18:00", text: "" },
    { end: "01:18:30", text: "" },
    { end: "01:19:00", text: "" },
    { end: "01:19:30", text: "等待" },
    { end: "01:21:00", text: "等待 Host · 3 分" },
  ])("fits the Host short-bar label after both reading insets through $end", ({ end, text }) => {
    const data = unknownTailTimeline();
    data.spans.push(span({ ...following, startAt: at("01:18:00"), endAt: at(end), state: "finished" }));
    render(<ObjectiveTimeline {...props(data)} />);
    expect(bar("span:s-next")!.querySelector(".sp-text")!.textContent).toBe(text);
    expect(bar("span:s-unknown")!.querySelector(".sp-tail")!.textContent).toBe(end === "01:18:00" ? "结束未确认" : "");
  });

  it("keeps a short solid execution label empty when the tail text is suppressed", () => {
    const data = unknownTailTimeline();
    data.spans[0]!.endAt = at("01:15:00");
    data.spans.push(span(following));
    render(<ObjectiveTimeline {...props(data)} />);
    const unknown = bar("span:s-unknown")!;
    expect(unknown.querySelector(".sp-solid-text")!.textContent).toBe("");
    expect(unknown.querySelector(".sp-tail")!.textContent).toBe("");
  });

  it.each([
    { end: "01:18:30", text: "" },
    { end: "01:19:00", text: "" },
    { end: "01:19:30", text: "2" },
  ])("fits the following execution short-bar label after both reading insets through $end", ({ end, text }) => {
    const data = unknownTailTimeline();
    data.spans.push(span({ ...following, kind: "execution", state: "finished", endAt: at(end),
      turnIndex: 2, shutdownConfirmed: true }));
    render(<ObjectiveTimeline {...props(data)} />);
    expect(bar("span:s-next")!.querySelector(".sp-text")!.textContent).toBe(text);
    expect(bar("span:s-unknown")!.querySelector(".sp-tail")!.textContent).toBe("");
  });

  it.each(["start", "end"])("preserves the existing missing execution %s behavior", missing => {
    const data = unknownTailTimeline();
    if (missing === "start") data.spans[0]!.startAt = null;
    else data.spans[0]!.endAt = null;
    data.spans.push(span(following));
    render(<ObjectiveTimeline {...props(data)} />);
    if (missing === "start") {
      expect(bar("span:s-unknown")).toBeNull();
      expect(document.querySelector(".tl-row .trunc-chip")!.textContent).toContain("1 段时间缺失");
    } else {
      const unknown = bar("span:s-unknown")!;
      expect(unknown.querySelector(".sp-tail")).toBeNull();
      expect(unknown.getAttribute("aria-label")).toContain("结束未确认");
    }
    expect(bar("span:s-next")).not.toBeNull();
  });

  it.each(["missing", "invalid", "before recorded end"])("does not invent an unknown tail with %s observation time", reason => {
    const data = unknownTailTimeline();
    data.observedAt = reason === "missing" ? "" : reason === "invalid" ? "invalid" : at("01:17:00");
    data.spans.push(span(following));
    render(<ObjectiveTimeline {...props(data)} />);
    const unknown = bar("span:s-unknown")!;
    expect(unknown.querySelector(".sp-tail")!.textContent).toBe("");
    expect(unknown.querySelector(".end-mark.warn")!.textContent).toBe("?");
    expect(unknown.getAttribute("aria-label")).toContain("结束未确认");
  });
});

// A continuous four-hour record keeps a long canvas without relying on idle
// folding or mocked DOM measurements. At max, one second is exactly one px.
const GEOMETRY_START = Date.parse("2026-09-26T01:12:00Z");
const geometryAt = (ms: number) => new Date(GEOMETRY_START + ms).toISOString();
function geometryTimeline(subject: Partial<TimelineSpan>, observedMs = 240 * 60000): ObjectiveTimelineData {
  const data = timeline();
  const spans = [
    span({ spanId: "geometry", startAt: geometryAt(0), endAt: geometryAt(3000), turnIndex: 1,
      shutdownConfirmed: true, ...subject }),
    span({ spanId: "domain", runId: "r2", kind: "host", state: "finished",
      startAt: geometryAt(0), endAt: geometryAt(240 * 60000) }),
  ];
  return { ...data, observedAt: geometryAt(observedMs), rows: [row({}), row({ runId: "r2", rootRunId: "r2" })],
    spans, totals: { ...data.totals, spans: spans.length, rows: 2, allRows: 2 } };
}
function zoomGeometry(level: "fit" | "+2" | "max") {
  const button = document.querySelector<HTMLButtonElement>('[aria-label="放大"]')!;
  if (level === "+2") { fireEvent.click(button); fireEvent.click(button); }
  if (level === "max") {
    for (let i = 0; i < 20 && !button.disabled; i++) fireEvent.click(button);
    expect(button.disabled).toBe(true);
  }
}
function geometryTrackPx() {
  const grid = document.querySelector<HTMLElement>(".tl-grid")!;
  // The grid contains a sticky label column plus a 12px end reserve. The
  // remaining declared track width is the percentage containing block.
  return Number(/\+ ([\d.]+)px/.exec(grid.style.width)![1]) - 12;
}
function spanPx(element: HTMLElement) {
  const track = geometryTrackPx();
  return { left: Number.parseFloat(element.style.left) / 100 * track,
    width: Number.parseFloat(element.style.width) / 100 * track };
}
function markCentre(element: HTMLElement) {
  const css = getComputedStyle(element);
  expect(css.right).toBe("auto");
  // Computed style serializes fractional px with limited decimal precision.
  return Number.parseFloat(css.left) + Number.parseFloat(css.width) / 2;
}

describe("U6.1: real duration geometry on a long canvas", () => {
  it.each(["fit", "+2", "max"] as const)("keeps zero, millisecond and three-second spans at their true width at %s", level => {
    const data = geometryTimeline({});
    const kinds = ["execution", "queue", "routing", "host"] as const;
    data.spans = [...data.spans.slice(1), ...kinds.flatMap(kind => [0, 1, 3000].map(ms =>
      span({ spanId: `${kind}-${ms}`, kind, startAt: geometryAt(120000), endAt: geometryAt(120000 + ms),
        shutdownConfirmed: true, resultStatus: kind === "routing" ? "failed" : undefined })))];
    data.totals.spans = data.spans.length;
    render(<ObjectiveTimeline {...props(data)} />);
    zoomGeometry(level);
    const expectedTrack = level === "max" ? 14416 : level === "+2" ? 1753 : 788;
    expect(geometryTrackPx()).toBeCloseTo(expectedTrack, 10);
    const ppm = (expectedTrack - 16) / 240;
    for (const kind of kinds) for (const ms of [0, 1, 3000]) {
      const element = bar(`span:${kind}-${ms}`)!;
      const geometry = spanPx(element);
      expect(geometry.left).toBeCloseTo(16 + 2 * ppm, 8);
      expect(geometry.width).toBeCloseTo(ms / 60000 * ppm, 8);
      expect(geometry.left + geometry.width).toBeCloseTo(16 + (120000 + ms) / 60000 * ppm, 8);
      expect(getComputedStyle(element).minWidth).toBe(kind === "routing" ? "14px" : "4px");
      expect(element.title).not.toBe("");
      expect(element.getAttribute("aria-label")).toBe(element.title);
      if (kind === "execution" || kind === "host") expect(element.querySelector(".sp-text")!.textContent).toBe("");
    }
  });
});

describe("U6.2: end instants are independent of minimum shapes", () => {
  it.each((["failed", "cancelled", "unknown"] as const).flatMap(outcome =>
    (["fit", "+2", "max"] as const).map(level => ({ outcome, level }))))("anchors $outcome ends including zero-duration records at $level", ({ outcome, level }) => {
    for (const ms of [0, 1, 3000]) {
      const unknown = outcome === "unknown";
      const data = geometryTimeline({ startAt: geometryAt(120000), endAt: geometryAt(120000 + ms),
        resultStatus: unknown ? undefined : outcome, uncertain: unknown, shutdownConfirmed: !unknown },
        unknown ? 120000 + ms + 2000 : undefined);
      const view = render(<ObjectiveTimeline {...props(data)} />);
      zoomGeometry(level);
      const pxPerMs = (geometryTrackPx() - 16) / (240 * 60000);
      const element = bar("span:geometry")!;
      const geometry = spanPx(element);
      const mark = element.querySelector<HTMLElement>(".end-mark")!;
      expect(mark.textContent).toBe(unknown ? "?" : outcome === "failed" ? "✕" : "⊘");
      expect(mark.getAttribute("aria-hidden")).toBe("true");
      expect(geometry.left + markCentre(mark)).toBeCloseTo(16 + (120000 + ms) * pxPerMs, 4);
      expect(geometry.width).toBeCloseTo((ms + (unknown ? 2000 : 0)) * pxPerMs, 8);
      expect(element.title).not.toBe("");
      expect(element.getAttribute("aria-label")).toBe(element.title);
      if (unknown) {
        const solid = element.querySelector<HTMLElement>(".solid")!;
        const text = element.querySelector<HTMLElement>(".sp-solid-text")!;
        const tail = element.querySelector<HTMLElement>(".sp-tail")!;
        expect(Number.parseFloat(solid.style.width)).toBeCloseTo(ms * pxPerMs, 8);
        expect(text.style.width).toBe(solid.style.width);
        expect(text.textContent).toBe("");
        expect(Number.parseFloat(tail.style.left)).toBeCloseTo(ms * pxPerMs + 10, 8);
        expect(Number.parseFloat(tail.style.width)).toBe(0);
        expect(tail.textContent).toBe("");
        expect(element.classList.contains("unknown")).toBe(true);
      }
      view.unmount();
    }
  });

  it("keeps a wholly zero-duration unknown record at one instant", () => {
    render(<ObjectiveTimeline {...props(geometryTimeline({ endAt: geometryAt(0), uncertain: true, shutdownConfirmed: false }, 0))} />);
    zoomGeometry("max");
    const element = bar("span:geometry")!;
    expect(spanPx(element).width).toBe(0);
    expect(Number.parseFloat(element.querySelector<HTMLElement>(".solid")!.style.width)).toBe(0);
    expect(markCentre(element.querySelector<HTMLElement>(".end-mark")!)).toBe(0);
    expect(element.querySelector(".sp-solid-text")!.textContent).toBe("");
    expect(element.querySelector(".sp-tail")!.textContent).toBe("");
    expect(element.getAttribute("aria-label")).toBe(element.title);
  });

  it.each([0, 1, 3000])("keeps a %i ms unknown tail and its observation endpoint without a percentage floor", ms => {
    const data = geometryTimeline({ endAt: geometryAt(2000), uncertain: true, shutdownConfirmed: false }, 2000 + ms);
    render(<ObjectiveTimeline {...props(data)} />);
    zoomGeometry("max");
    const element = bar("span:geometry")!;
    const geometry = spanPx(element);
    expect(geometry.width).toBeCloseTo(2 + ms / 1000, 8);
    const solid = element.querySelector<HTMLElement>(".solid")!;
    expect(Number.parseFloat(solid.style.width)).toBeCloseTo(2, 8);
    expect(markCentre(element.querySelector<HTMLElement>(".end-mark")!)).toBeCloseTo(2, 8);
    expect(geometry.width - Number.parseFloat(solid.style.width)).toBeCloseTo(ms / 1000, 8);
    expect(element.querySelector(".sp-tail")!.textContent).toBe("");
  });
});

describe("U6.3: label thresholds use the width remaining after insets", () => {
  it.each([
    { kind: "execution" as const, threshold: 54, before: "", at: "1" },
    { kind: "execution" as const, threshold: 96, before: "1", at: "第1轮" },
    { kind: "execution" as const, threshold: 174, before: "第1轮", at: "第1轮 · 配置未记录" },
    { kind: "host" as const, threshold: 58, before: "", at: "等待" },
    { kind: "host" as const, threshold: 88, before: "等待", at: "等待 Host" },
    { kind: "host" as const, threshold: 134, before: "等待 Host", at: "等待 Host · 2 分" },
  ])("fits $kind text below, at and above $threshold px", ({ kind, threshold, before, at }) => {
    for (const delta of [-0.001, 0, 0.001]) {
      const data = geometryTimeline({ kind, endAt: geometryAt(Math.round((threshold + delta) * 1000)) });
      const view = render(<ObjectiveTimeline {...props(data)} />);
      zoomGeometry("max");
      const element = bar("span:geometry")!;
      const text = element.querySelector<HTMLElement>(".sp-text")!;
      expect(text.textContent).toBe(delta < 0 ? before : at);
      expect(Number.parseFloat(text.style.width)).toBeCloseTo(threshold + delta, 8);
      expect(element.getAttribute("aria-label")).toBe(element.title);
      view.unmount();
    }
  });

  it.each(["solid", "tail"] as const)("fits unknown %s text after insets and mark clearance on a long max canvas", part => {
    for (const delta of [-0.001, 0, 0.001]) {
      const solidMs = part === "solid" ? Math.round((104 + delta) * 1000) : 120000;
      const tailMs = part === "tail" ? Math.round((84 + delta) * 1000) : 120000;
      const data = geometryTimeline({ endAt: geometryAt(solidMs), uncertain: true, shutdownConfirmed: false }, solidMs + tailMs);
      const view = render(<ObjectiveTimeline {...props(data)} />);
      zoomGeometry("max");
      const element = bar("span:geometry")!;
      const text = element.querySelector(part === "solid" ? ".sp-solid-text" : ".sp-tail")!;
      expect(text.textContent).toBe(delta < 0 ? "" : part === "solid" ? "第1轮 · 配置未记录" : "结束未确认");
      expect(markCentre(element.querySelector<HTMLElement>(".end-mark")!)).toBeCloseTo(solidMs / 1000, 8);
      expect(element.getAttribute("aria-label")).toBe(element.title);
      view.unmount();
    }
  });

  it.each(["failed", "cancelled"] as const)("reserves circle room at each %s execution label threshold", outcome => {
    for (const [threshold, before, at] of [[62, "", "1"], [104, "1", "第1轮"],
      [182, "第1轮", `第1轮 · 配置未记录 · ${outcome === "failed" ? "执行失败" : "已取消"}`]] as const) {
      for (const delta of [-0.001, 0, 0.001]) {
        const data = geometryTimeline({ resultStatus: outcome, endAt: geometryAt(Math.round((threshold + delta) * 1000)) });
        const view = render(<ObjectiveTimeline {...props(data)} />);
        zoomGeometry("max");
        const element = bar("span:geometry")!;
        const text = element.querySelector<HTMLElement>(".sp-text")!;
        expect(text.textContent).toBe(delta < 0 ? before : at);
        expect(getComputedStyle(text).paddingLeft).toBe("16px");
        expect(getComputedStyle(text).paddingRight).toBe("16px");
        const mark = element.querySelector<HTMLElement>(".end-mark")!;
        expect(Number.parseFloat(text.style.width) - 16).toBeLessThan(markCentre(mark) - 7.5);
        expect(element.getAttribute("aria-label")).toBe(element.title);
        view.unmount();
      }
    }
  });
});

describe("U6.6: routing decoration preserves adjacent circle pointer targets", () => {
  it.each(["default", "selected", "run-member", "hover", "keyboard focus"])("keeps a wide routing cross pointer-transparent beside a failed execution circle in %s state", state => {
    // Host's real hit regression: the 99px routing decoration covers the
    // centre/right of the T0+4s circle, with routing later in the DOM.
    const data = geometryTimeline({ startAt: geometryAt(3000), endAt: geometryAt(4000), resultStatus: "failed" });
    data.spans.push(span({ spanId: "route-pointer", kind: "routing", startAt: geometryAt(1000),
      endAt: geometryAt(100000), resultStatus: "failed" }));
    data.totals.spans = data.spans.length;
    const callbacks = props(data, state === "selected" ? { selection: { type: "item", key: "span:route-pointer" } }
      : state === "run-member" ? { selection: { type: "run", runId: "r1" } } : {});
    render(<ObjectiveTimeline {...callbacks} />);
    zoomGeometry("max");
    const execution = bar("span:geometry")!;
    const route = bar("span:route-pointer")!;
    const cross = route.querySelector<HTMLElement>(".routing-cross")!;
    const circle = execution.querySelector<HTMLElement>(".end-mark.bad")!;
    expect(route.previousElementSibling).toBe(execution);
    if (state === "hover") route.classList.add("probe-hover");
    if (state === "keyboard focus") { fireEvent.focus(route); route.classList.add("probe-focus"); }
    noSpanContext(route);
    noSpanContext(execution);
    expect(spanPx(route).left).toBeCloseTo(17, 8);
    expect(spanPx(route).width).toBeCloseTo(99, 8);
    const centre = spanPx(execution).left + markCentre(circle);
    expect(centre).toBeCloseTo(20, 8);
    expect(centre - 7.5).toBeLessThan(spanPx(route).left);
    expect(centre).toBeGreaterThan(spanPx(route).left);
    expect(centre + 7.5).toBeLessThan(spanPx(route).left + spanPx(route).width);
    expect(layer(cross)).toBe(layer(circle));
    expect(getComputedStyle(cross).pointerEvents).toBe("none");
    // Cross strokes inherit the protection and must not opt back into hits.
    for (const pseudo of [".routing-cross::before", ".routing-cross::after"]) {
      for (const rule of rules(pseudo)) expect(["", "none"]).toContain(rule.style.pointerEvents);
    }
    expect(getComputedStyle(circle).pointerEvents).toBe("auto");
    expect(getComputedStyle(route).pointerEvents).toBe("auto");
    expect(execution.getAttribute("aria-label")).toBe(execution.title);
    expect(route.getAttribute("aria-label")).toBe(route.title);
    // jsdom does not hit-test coordinates. Verify CSS protection above and
    // the callbacks on the actual remaining pointer targets independently.
    fireEvent.click(circle);
    expect(callbacks.onSelectItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:geometry" }));
    expect(callbacks.onSelectItem).toHaveBeenCalledTimes(1);
    expect(callbacks.onOpenItem).not.toHaveBeenCalled();
    fireEvent.doubleClick(circle);
    expect(callbacks.onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:geometry" }));
    fireEvent.click(route);
    expect(callbacks.onSelectItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:route-pointer" }));
    expect(callbacks.onSelectItem).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(route, { key: "Enter" });
    expect(callbacks.onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "span:route-pointer" }));
    expect(callbacks.onOpenItem).toHaveBeenCalledTimes(2);
  });
});

describe("U6.5: opening glyphs retain room beside sticky labels", () => {
  it.each(["fit", "+2", "max"] as const)("keeps opening terminal circles, flags and event clusters wholly inside the track at %s", level => {
    const data = geometryTimeline({});
    data.spans = [...data.spans.slice(1), ...(["failed", "cancelled", "unknown"] as const).flatMap(outcome =>
      [0, 1, 3000].map(ms => span({ spanId: `${outcome}-${ms}`, startAt: geometryAt(0), endAt: geometryAt(ms),
        resultStatus: outcome === "unknown" ? undefined : outcome, uncertain: outcome === "unknown",
        shutdownConfirmed: outcome !== "unknown", turnIndex: 1 })))];
    data.rows = [row({ acceptedAt: geometryAt(0), acceptanceVerdict: "accepted" }),
      row({ runId: "r2", rootRunId: "r2", acceptedAt: geometryAt(0), acceptanceVerdict: "rejected" })];
    const event = objectiveTimelineFixture().events[0]!;
    data.events = [{ ...event, seq: 1, at: geometryAt(0) }, { ...event, seq: 2, at: geometryAt(1) }];
    render(<ObjectiveTimeline {...props(data, { selection: { type: "run", runId: "r1" } })} />);
    fireEvent.click(document.querySelector<HTMLButtonElement>(".markers-toggle")!);
    zoomGeometry(level);
    const ppm = (geometryTrackPx() - 16) / 240;
    for (const outcome of ["failed", "cancelled", "unknown"]) for (const ms of [0, 1, 3000]) {
      const element = bar(`span:${outcome}-${ms}`)!;
      const mark = element.querySelector<HTMLElement>(".end-mark")!;
      const centre = spanPx(element).left + markCentre(mark);
      expect(spanPx(element).left).toBeCloseTo(16, 8);
      expect(centre).toBeCloseTo(16 + ms / 60000 * ppm, 4);
      expect(centre - Number.parseFloat(getComputedStyle(mark).width) / 2).toBeGreaterThanOrEqual(8.5 - 1e-8);
      expect(element.querySelector(".sp-text")!.textContent).toBe("");
      expect(element.title).toBe(element.getAttribute("aria-label"));
    }
    for (const flag of document.querySelectorAll<HTMLElement>(".tl-scroll .flag, .tl-scroll .mk")) {
      const css = getComputedStyle(flag);
      const left = Number(flag.dataset.x) / 100 * geometryTrackPx();
      expect(left).toBeCloseTo(16, 7);
      if (flag.classList.contains("mk")) {
        expect(flag.style.left).toContain(`clamp(9px, ${flag.dataset.x}%,`);
        expect(flag.style.left).toContain("100% - 9px");
      }
      else expect(Number.parseFloat(flag.style.left)).toBe(Number(flag.dataset.x));
      expect(left + Number.parseFloat(css.marginLeft)).toBeCloseTo(7, 7);
      expect(flag.title).not.toBe("");
    }
    expect(document.querySelectorAll(".flag.accept")).toHaveLength(1);
    expect(document.querySelectorAll(".flag.reject")).toHaveLength(1);
    expect(document.querySelectorAll(".mk.cluster")).toHaveLength(1);
    // The 01:12 origin precedes the next clock-aligned tick (01:30 at fit, 01:15 when zoomed).
    expect(Number.parseFloat(document.querySelector<HTMLElement>(".tick")!.style.left) / 100 * geometryTrackPx()).toBeCloseTo(16 + (level === "fit" ? 18 : 3) * ppm, 7);
    if (level === "fit") expect(geometryTrackPx() + 12).toBe(800);
  });

  it.each(["fit", "+2", "max"] as const)("keeps a short failed route and 3s terminal before a 56s wait distinct at %s", level => {
    const data = geometryTimeline({ resultStatus: "failed" });
    data.spans.push(span({ spanId: "route-edge", kind: "routing", startAt: geometryAt(0), endAt: geometryAt(1), resultStatus: "failed" }),
      span({ spanId: "wait-edge", kind: "host", startAt: geometryAt(3000), endAt: geometryAt(59000) }));
    render(<ObjectiveTimeline {...props(data, { selection: { type: "item", key: "span:geometry" } })} />);
    zoomGeometry(level);
    const failed = bar("span:geometry")!;
    const route = bar("span:route-edge")!;
    const wait = bar("span:wait-edge")!;
    expect(route.querySelector(".routing-cross")).toBeNull();
    expect(route.classList.contains("failed")).toBe(true);
    expect(getComputedStyle(route).minWidth).toBe("14px");
    expect(failed.querySelector(".end-mark.bad")).not.toBeNull();
    expect(failed.querySelector(".sp-text")!.textContent).toBe("");
    expect(wait.querySelector(".sp-text")!.textContent).toBe("");
    expect(wait.title).toContain("等待 Host");
    expect(wait.getAttribute("aria-label")).toBe(wait.title);
    expect(spanPx(failed).left + markCentre(failed.querySelector<HTMLElement>(".end-mark")!)).toBeCloseTo(spanPx(wait).left, 4);
    // Actual text starts 16px inside the wait, beyond the circle's 7.5px radius.
    expect(spanPx(wait).left + Number.parseFloat(getComputedStyle(wait.querySelector(".sp-text")!).paddingLeft)
      - (spanPx(failed).left + markCentre(failed.querySelector<HTMLElement>(".end-mark")!) + 7.5)).toBeCloseTo(8.5, 4);
  });

  it.each([17.999, 18, 18.001, 60])("uses %s real routing pixels for the central-cross boundary", width => {
    const data = geometryTimeline({ kind: "routing", resultStatus: "failed", endAt: geometryAt(Math.round(width * 1000)) });
    render(<ObjectiveTimeline {...props(data)} />);
    zoomGeometry("max");
    const route = bar("span:geometry")!;
    expect(spanPx(route).width).toBeCloseTo(width, 8);
    expect(!!route.querySelector(".routing-cross")).toBe(width >= 18);
    expect(route.classList.contains("failed")).toBe(true);
    expect(getComputedStyle(route).minWidth).toBe("14px");
    expect(rules(".sp.routing.failed::before")[0]!.style.borderColor).toBe("var(--danger)");
    expect(route.title).toContain("路由失败");
    expect(route.getAttribute("aria-label")).toBe(route.title);
  });

  it.each(["selected", "run-member", "probe-hover", "probe-focus"])("keeps %s decorations below every glyph and text in the shared context", state => {
    const data = objectiveTimelineFixture();
    data.spans.find(span => span.spanId === "s-r2-r")!.resultStatus = "failed";
    render(<ObjectiveTimeline {...props(data)} />);
    fireEvent.click(document.querySelector<HTMLButtonElement>(".markers-toggle")!);
    zoomGeometry("max");
    // Include a wide failed route in the same rendered/cascaded context.
    const route = bar("span:s-r2-r")!;
    expect(route.querySelector(".routing-cross")).not.toBeNull();
    for (const item of document.querySelectorAll<HTMLElement>(".tl-scroll .sp, .tl-scroll .mk, .tl-scroll .flag")) {
      item.classList.add(state);
      noSpanContext(item);
      const decoration = rules(item.classList.contains("sp") ? ".sp::after" : item.classList.contains("mk") ? ".mk::after" : ".flag::after")[0]!.style;
      const token = /var\((--tl-layer-[a-z]+),\s*(\d+)\)/.exec(decoration.zIndex)!;
      const interaction = Number(getComputedStyle(item.closest(".tl-track")!).getPropertyValue(token[1]!));
      expect(interaction).toBe(1);
      expect(decoration.pointerEvents).toBe("none");
      for (const glyph of item.querySelectorAll<HTMLElement>(".end-mark, .pulse, .routing-cross, .sp-text, .tl-mark-text")) expect(layer(glyph)).toBeGreaterThan(interaction);
      const track = getComputedStyle(item.closest(".tl-track")!);
      expect(Number(track.getPropertyValue("--tl-layer-detail"))).toBe(2);
      expect(Number(track.getPropertyValue("--tl-layer-text"))).toBe(3);
    }
    expect(rules(".routing-cross")[0]!.style.zIndex).toBe("var(--tl-layer-detail, 2)");
    expect(rules(".flag::before")[0]!.style.zIndex).toBe("var(--tl-layer-detail, 2)");
    expect(Number(getComputedStyle(document.querySelector(".tl-label")!).zIndex)).toBeGreaterThan(3);
  });
});
