import { cleanup, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { at, event, row, timeline } from "./task-detail.fixtures";
import { open, pane, ready, strip, stripSlice } from "./task-detail.testing";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

/** jsdom ships no matchMedia: without reduced motion the zoom waits for frames that never come. */
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

const scroll = () => document.querySelector<HTMLElement>('[data-slot=detail-strip-scroll]')!;
const trackSvg = () => document.querySelector<SVGElement>('[data-slot=detail-strip] [data-slot=timeline-track]')!;

describe("the step strip along the top of a micro task's detail", () => {
  it("centres the step the detail opened with once; switching a step never moves the scroll again", async () => {
    const f = open({ origin: "span:e2" });
    await ready();
    const centred = scroll().scrollLeft;
    expect(centred).toBeGreaterThan(0);
    fireEvent.click(stripSlice("span:r1")!);
    expect(scroll().scrollLeft).toBe(centred);
    expect(pane().textContent).toContain("路由");
  });

  it("fits the whole micro task however far it is zoomed in, and takes the same zoom keys as the macro task", async () => {
    const f = open({ origin: "span:r1" });
    await ready();
    const fitted = Number(trackSvg().getAttribute("width"));
    const buttonOf = (label: string) => strip().querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!;
    await f.user.click(buttonOf("放大（+）"));
    expect(Number(trackSvg().getAttribute("width"))).toBeGreaterThan(fitted);
    await f.user.click(buttonOf("适应窗口（0）"));
    expect(Number(trackSvg().getAttribute("width"))).toBe(fitted);
    fireEvent.keyDown(strip(), { key: "+" });
    expect(Number(trackSvg().getAttribute("width"))).toBeGreaterThan(fitted);
    fireEvent.keyDown(strip(), { key: "0" });
    expect(Number(trackSvg().getAttribute("width"))).toBe(fitted);
    fireEvent.keyDown(strip(), { key: "-" });
    expect(Number(trackSvg().getAttribute("width"))).toBe(fitted);
  });

  it("keeps one selected slice, switches the step below with one click, and offers the delegation as its own slice", async () => {
    const f = open({ timeline: timeline(), step: 2 });
    await ready();
    expect(stripSlice("span:r1")!.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(stripSlice("span:h1")!);
    await Promise.resolve();
    expect(pane().textContent).toContain("需要读取样例目录");
    // The step a wait ends in carries the selection; its mark sits in the cluster of that instant.
    expect(stripSlice("cluster:event:2")!.getAttribute("aria-pressed")).toBe("true");
    expect(stripSlice("span:h1")!.getAttribute("aria-pressed")).toBe("false");
    expect(stripSlice("span:r1")!.getAttribute("aria-pressed")).toBe("false");
    const delegation = strip().querySelector('[data-key="delegation"]')!;
    expect(delegation.getAttribute("aria-pressed")).toBe("false");
    // Keyboard-focusable overlay marks keep a clear focus outline on their hit rect.
    expect(delegation.getAttribute("class")).toContain("focus-visible:[&_.hit]:stroke-live");
    fireEvent.click(delegation as unknown as Element);
    expect(pane().textContent).toContain("实现微任务详情");
    expect(strip().querySelector('[data-key="delegation"]')!.getAttribute("aria-pressed")).toBe("true");
  });

  it("offers a delivered micro task's open acceptance as its own slice at the end of the record", async () => {
    open({ timeline: timeline(), origin: "span:e2" });
    await ready();
    const acceptance = strip().querySelector('[data-key="acceptance-wait"]')!;
    expect(acceptance.getAttribute("aria-pressed")).toBe("false");
    expect(acceptance.getAttribute("aria-label")).toContain("等待验收");
    fireEvent.click(acceptance);
    expect(acceptance.getAttribute("aria-pressed")).toBe("true");
    expect(pane().textContent).toContain("等待验收");
    expect(strip().querySelectorAll('[data-slot="selected-span"]')).toHaveLength(1);
  });

  it("decides the opening position once even when the first step is the open acceptance with no instant of its own", async () => {
    const f = open({ origin: "settle:m1" });
    await ready();
    expect(strip().querySelector('[data-key="acceptance-wait"]')!.getAttribute("aria-pressed")).toBe("true");
    // The wait's recorded start anchors the one centre-on-open; the reader's own scrolling then stands.
    const centred = scroll().scrollLeft;
    expect(centred).toBeGreaterThan(0);
    scroll().scrollLeft = 0;
    fireEvent.click(stripSlice("span:e1")!);
    expect(scroll().scrollLeft).toBe(0);
    expect(pane().textContent).toContain("回合 1");
    // A later read that extends the open record does not move the reader either.
    const live = timeline();
    Object.assign(live.spans.find(span => span.spanId === "e2")!, { endAt: null, state: "executing", shutdownConfirmed: false });
    live.observedAt = "2026-09-26T12:30:00Z";
    f.reread({ timeline: live, origin: "span:e1" });
    await ready();
    expect(scroll().scrollLeft).toBe(0);
  });

  it("selects a settled acceptance on its settling mark, reachable by mouse and alone", async () => {
    const settled = timeline();
    settled.events.push(event(4, "integrate", at("11:03")), event(5, "accept", at("11:05")));
    settled.rows = [row({ state: "accepted", acceptedAt: at("11:05"), acceptanceVerdict: "accepted" })];
    open({ timeline: settled, origin: "settle:m1" });
    await ready();
    expect(pane().textContent).toContain("已验收");
    const accept = stripSlice("event:5")!;
    expect(accept.getAttribute("aria-pressed")).toBe("true");
    expect(strip().querySelectorAll('[aria-pressed=true]')).toHaveLength(1);
    // Clicking the mark keeps the acceptance selected; the delegation stays a slice of its own.
    fireEvent.click(accept);
    expect(pane().textContent).toContain("验收");
    expect(stripSlice("event:5")!.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(strip().querySelector('[data-key="delegation"]')!);
    expect(stripSlice("event:5")!.getAttribute("aria-pressed")).toBe("false");
    expect(strip().querySelectorAll('[aria-pressed=true]')).toHaveLength(1);
  });

  it("gives a change of buddy its own slice on the boundary it happened on, selectable and alone", async () => {
    const f = open({ timeline: timeline(), step: 7 });
    await ready();
    const handoff = strip().querySelector('[data-key^="handoff:"]')!;
    expect(handoff.getAttribute("aria-label")).toContain("换手");
    expect(handoff.getAttribute("aria-label")).toContain("gpt-6.1-sol");
    expect(handoff.getAttribute("aria-label")).toContain("claude-opus-5-5");
    fireEvent.click(handoff);
    await Promise.resolve();
    expect(pane().textContent).toContain("换手");
    expect(pane().textContent).toContain("gpt-6.1-sol");
    expect(pane().textContent).toContain("claude-opus-5-5");
    expect(strip().querySelector('[data-key^="handoff:"]')!.getAttribute("aria-pressed")).toBe("true");
    expect(strip().querySelectorAll('[aria-pressed=true]')).toHaveLength(1);
    // The turn the new buddy takes stays its own slice beside the boundary.
    fireEvent.click(stripSlice("span:e2")!);
    expect(pane().textContent).toContain("第二轮");
    expect(strip().querySelectorAll('[aria-pressed=true]')).toHaveLength(1);
  });
});
