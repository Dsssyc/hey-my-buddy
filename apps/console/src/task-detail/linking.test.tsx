import { cleanup, fireEvent, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { at, event, inquiry, row, timeline } from "./task-detail.fixtures";
import { open, pane, ready, strip, stripSlice } from "./task-detail.testing";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => vi.stubGlobal("matchMedia", () => ({ matches: true })));

function withInquiry() {
  const found = timeline();
  found.spans.find(span => span.spanId === "e1")!.inquiries = [{ inquiryId: "inq-1", createdAt: at("10:20"), state: "delivered" }];
  return found;
}
const item = (key: string) => pane().querySelector<HTMLElement>(`[data-item-key="${key}"]`)!;

describe("one focused item shared by the strip and the overview", () => {
  it.each(["click", "Enter", " "])("selects an inquiry and its containing step with %s", async action => {
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const f = open({ step: 7, timeline: withInquiry(), replies: { inquiries: { items: [inquiry()], more: false } } });
    await ready();
    const marker = stripSlice("inquiry:inq-1")!;
    expect(marker.getAttribute("tabindex")).toBe("0");
    if (action === "click") fireEvent.click(marker);
    else fireEvent.keyDown(marker, { key: action });
    expect(f.onStep).toHaveBeenLastCalledWith(3, "inquiry:inq-1");
    expect(item("inquiry:inq-1").getAttribute("data-highlighted")).toBe("true");
    expect(marker.getAttribute("aria-pressed")).toBe("true");
    expect(scroll).toHaveBeenCalledWith({ block: "center", inline: "nearest", behavior: "instant" });
    scroll.mockRestore();
  });

  it("lights the marker on overview hover or focus without changing the focused item", async () => {
    const f = open({ step: 3, timeline: withInquiry(), replies: { inquiries: { items: [inquiry()], more: false } } });
    await ready();
    const frame = stripSlice("inquiry:inq-1")!.querySelector('[data-slot=fragment-focus]')!;
    await f.user.hover(item("inquiry:inq-1"));
    expect(getComputedStyle(frame).opacity).toBe("1");
    await f.user.unhover(item("inquiry:inq-1"));
    item("inquiry:inq-1").focus();
    expect(getComputedStyle(frame).opacity).toBe("1");
    expect(f.onStep).not.toHaveBeenCalled();
    expect(stripSlice("span:e1")!.getAttribute("aria-pressed")).toBe("true");
  });

  it("focuses the item when its time is activated, leaving other inquiries unselected", async () => {
    const f = open({ step: 3, timeline: withInquiry(), replies: { inquiries: { items: [inquiry()], more: false } } });
    await ready();
    await f.user.click(within(item("inquiry:inq-1")).getByRole("button", { name: /^在步骤条中选中问询/ }));
    expect(f.onStep).toHaveBeenLastCalledWith(3, "inquiry:inq-1");
    expect(item("inquiry:inq-1").getAttribute("data-highlighted")).toBe("true");
    expect(strip().querySelectorAll('[aria-pressed=true]')).toHaveLength(1);
  });

  it("reveals the same focused inquiry again after the reader has scrolled away", async () => {
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const f = open({ step: 3, origin: "inquiry:inq-1", timeline: withInquiry(), replies: { inquiries: { items: [inquiry()], more: false } } });
    await ready();
    scroll.mockClear();
    await f.user.click(within(item("inquiry:inq-1")).getByRole("button"));
    expect(scroll).toHaveBeenCalledWith({ block: "center", inline: "nearest", behavior: "instant" });
    expect(f.onStep).toHaveBeenLastCalledWith(3, "inquiry:inq-1");
    scroll.mockRestore();
  });

  it("gives an open Host wait one linked overview frame", async () => {
    const found = timeline();
    found.spans = found.spans.filter(span => ["q1", "r1", "e1", "h1"].includes(span.spanId));
    Object.assign(found.spans.find(span => span.spanId === "h1")!, { state: "open", endAt: null });
    found.events = found.events.filter(event => event.kind === "dispatch");
    found.rows = [row({ state: "awaiting-host" })];
    open({ timeline: found, origin: "span:h1" });
    await ready();
    expect(pane().querySelectorAll('[data-item-key="span:h1"]')).toHaveLength(1);
    expect(item("span:h1").getAttribute("data-highlighted")).toBe("true");
  });

  it.each([["span:q1", 2], ["span:q2", 6], ["span:h1", 4], ["settle:m1", 8]] as const)("links the wait %s in both directions", async (key, step) => {
    const f = open({ step });
    await ready();
    const marker = stripSlice(key)!;
    fireEvent.keyDown(marker, { key: " " });
    expect(f.onStep).toHaveBeenLastCalledWith(step, key);
    expect(item(key).getAttribute("data-highlighted")).toBe("true");
    expect(strip().querySelectorAll('[aria-pressed=true]')).toHaveLength(1);
    await f.user.click(within(item(key)).getByRole("button"));
    expect(f.onStep).toHaveBeenLastCalledWith(step, key);
  });

  it("links a settling acceptance event and keeps arrow navigation between steps", async () => {
    const settled = timeline();
    settled.events.push(event(5, "accept", at("11:05")));
    settled.rows = [row({ state: "accepted", acceptedAt: at("11:05") })];
    const f = open({ step: 3, timeline: settled });
    await ready();
    fireEvent.click(stripSlice("settle:m1")!);
    expect(f.onStep).toHaveBeenLastCalledWith(8, "settle:m1");
    expect(item("settle:m1").getAttribute("data-highlighted")).toBe("true");
    expect(stripSlice("event:5")!.getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(stripSlice("event:5")!, { key: "Enter" });
    expect(f.onStep).toHaveBeenLastCalledWith(8, "event:5");
    expect(item("event:5").getAttribute("data-highlighted")).toBe("true");
    fireEvent.keyDown(stripSlice("event:5")!, { key: "ArrowLeft" });
    expect(f.onStep).toHaveBeenLastCalledWith(7);
  });

  it("retains an exact overview target for every earlier acceptance event", async () => {
    const settled = timeline();
    settled.events.push(event(4, "reject", at("10:35")), event(5, "reject", at("11:02")), event(6, "accept", at("11:05")));
    settled.rows = [row({ state: "accepted", acceptedAt: at("11:05"), reviewRejectedAt: at("11:02") })];
    const f = open({ step: 8, timeline: settled });
    await ready();
    expect(within(pane()).getAllByRole("group", { name: "打回" })).toHaveLength(1);
    for (const seq of [4, 5, 6]) {
      const key = `event:${seq}`;
      expect(pane().querySelectorAll(`[data-item-key="${key}"]`)).toHaveLength(1);
      await f.user.click(within(item(key)).getByRole("button"));
      expect(f.onStep).toHaveBeenLastCalledWith(8, key);
      expect(item(key).getAttribute("data-highlighted")).toBe("true");
    }
  });
});
