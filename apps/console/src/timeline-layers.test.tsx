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

  it("draws interaction decoration above text without raising a span background or intercepting clicks", () => {
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
    expect(Number(track.getPropertyValue(token[1]!))).toBeGreaterThan(layer(bar("span:s-wait")!.querySelector<HTMLElement>(".sp-text")!));
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

  // This 100-minute fit canvas is 788px: tail text needs 76px total
  // (10px mark clearance, 16px insets and five 10px glyphs).
  it.each([
    { name: "below", end: "02:30:21.396", text: "" },
    { name: "at", end: "02:30:21.319", text: "结束未确认" },
    { name: "above", end: "02:30:21.243", text: "结束未确认" },
  ])("uses the pixel tail text threshold with mark clearance and insets $name the boundary", ({ end, text }) => {
    const data = unknownTailTimeline();
    data.observedAt = at("02:40:00");
    data.spans[0]!.startAt = at("01:00:00");
    data.spans[0]!.endAt = at(end);
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
    { end: "01:19:00", text: "2" },
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
    const expectedTrack = level === "max" ? 14400 : level === "+2" ? 1773 : 788;
    expect(geometryTrackPx()).toBe(expectedTrack);
    const ppm = expectedTrack / 240;
    for (const kind of kinds) for (const ms of [0, 1, 3000]) {
      const element = bar(`span:${kind}-${ms}`)!;
      const geometry = spanPx(element);
      expect(geometry.left).toBeCloseTo(2 * ppm, 8);
      expect(geometry.width).toBeCloseTo(ms / 60000 * ppm, 8);
      expect(geometry.left + geometry.width).toBeCloseTo((120000 + ms) / 60000 * ppm, 8);
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
      const pxPerMs = geometryTrackPx() / (240 * 60000);
      const element = bar("span:geometry")!;
      const geometry = spanPx(element);
      const mark = element.querySelector<HTMLElement>(".end-mark")!;
      expect(mark.textContent).toBe(unknown ? "?" : outcome === "failed" ? "✕" : "⊘");
      expect(mark.getAttribute("aria-hidden")).toBe("true");
      expect(geometry.left + markCentre(mark)).toBeCloseTo((120000 + ms) * pxPerMs, 4);
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
    { kind: "execution" as const, threshold: 46, before: "", at: "1" },
    { kind: "execution" as const, threshold: 88, before: "1", at: "第1轮" },
    { kind: "execution" as const, threshold: 166, before: "第1轮", at: "第1轮 · 配置未记录" },
    { kind: "host" as const, threshold: 50, before: "", at: "等待" },
    { kind: "host" as const, threshold: 80, before: "等待", at: "等待 Host" },
    { kind: "host" as const, threshold: 126, before: "等待 Host", at: "等待 Host · 2 分" },
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
      const solidMs = part === "solid" ? Math.round((88 + delta) * 1000) : 120000;
      const tailMs = part === "tail" ? Math.round((76 + delta) * 1000) : 120000;
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
});
