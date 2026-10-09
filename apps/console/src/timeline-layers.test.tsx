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
