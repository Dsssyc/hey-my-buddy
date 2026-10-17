import { cleanup, fireEvent, render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { event, row, span, timeline } from "../task-detail/task-detail.fixtures";
import type { TimelineScale } from "./scale";
import { ChronologicalList } from "./ChronologicalList";

afterEach(cleanup);

const at = (m: number) => new Date(Date.UTC(2026, 9, 17, 10, m)).toISOString();
const ms = (m: number) => Date.parse(at(m));
const gap = { startMs: ms(10), endMs: ms(20), startX: 30, endX: 84 };
const scale: TimelineScale = { widthPx: 600, pixelsPerMinute: 10, breaks: [gap], ticks: [], toX: () => 0, toTime: () => 0 };
const items = () => Array.from(document.querySelectorAll<HTMLButtonElement>('[data-slot="chronological-item"]'));

describe("ChronologicalList", () => {
  it("orders spans, all events and inquiries, with only the scale's breaks between them", () => {
    const waiting = span("wait", "host", at(0), at(30), { runId: "r1" });
    const executing = span("execution", "execution", at(25), at(40), { runId: "r2", turnIndex: 2,
      inquiries: [{ inquiryId: "ask", createdAt: at(27), author: "Host", state: "delivered" }] });
    const data = timeline({ rows: [row({ runId: "r1", title: "First" }), row({ runId: "r2", title: "Second" })],
      spans: [executing, waiting], events: [event(3, "integrate", at(28), { runId: "r2" }), event(1, "dispatch", at(5), { runId: "r1" }), event(2, "accept", at(22), { runId: "r1" })] });
    const onOpen = vi.fn();
    render(<ChronologicalList timeline={data} scale={scale} nowMs={ms(45)} onOpen={onOpen}/>);
    expect(items().map(item => item.dataset.key)).toEqual([
      "span:wait", "event:1", "event:2", "span:execution", "inquiry:ask", "event:3",
    ]);
    const breakNode = document.querySelector('[data-slot="chronological-break"]')!;
    expect(document.querySelectorAll('[data-slot="chronological-break"]')).toHaveLength(scale.breaks.length);
    expect(breakNode.previousElementSibling?.getAttribute("data-key")).toBe("event:1");
    expect(breakNode.nextElementSibling?.getAttribute("data-key")).toBe("event:2");
    expect(breakNode.getAttribute("data-start")).toBe(String(gap.startMs));
    expect(items()[0]!.textContent).toContain("30"); // The wait stays whole across the break.
    fireEvent.click(items()[4]!);
    expect(onOpen).toHaveBeenLastCalledWith("r2", "span:execution");
    fireEvent.click(items()[5]!);
    expect(onOpen).toHaveBeenLastCalledWith("r2", "event:3");
    fireEvent.click(items()[0]!);
    expect(onOpen).toHaveBeenLastCalledWith("r1", "span:wait");
  });

  it("moves focus with vertical arrows without passing them to the surrounding task view", async () => {
    const data = timeline({ rows: [row({ runId: "r1" })], spans: [span("a", "queue", at(0), at(1), { runId: "r1" })],
      events: [event(1, "accept", at(2), { runId: "r1" })] });
    const onOpen = vi.fn();
    const parentKeyDown = vi.fn();
    render(<div onKeyDown={parentKeyDown}><ChronologicalList timeline={data} scale={{ ...scale, breaks: [] }} nowMs={ms(3)} onOpen={onOpen}/></div>);
    const [first, second] = items();
    first!.focus();
    fireEvent.keyDown(first!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second!, { key: "ArrowUp" });
    expect(document.activeElement).toBe(first);
    expect(parentKeyDown).not.toHaveBeenCalled();
    await userEvent.keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledWith("r1", "span:a");
  });

  it("lists the confirmed acceptance wait and opens its acceptance step, without inventing an unknown stop", () => {
    const execution = span("delivery", "execution", at(0), at(10), { runId: "r1", shutdownConfirmed: true });
    const data = timeline({ rows: [row({ runId: "r1", state: "delivered", acceptedAt: null })], spans: [execution], events: [] });
    const onOpen = vi.fn();
    const view = render(<ChronologicalList timeline={data} scale={{ ...scale, breaks: [] }} nowMs={ms(30)} onOpen={onOpen}/>);
    const waiting = items().find(item => item.dataset.key === "settle:r1")!;
    expect(waiting.textContent).toContain("等待验收");
    expect(waiting.textContent).toContain("20 分钟");
    fireEvent.click(waiting);
    expect(onOpen).toHaveBeenCalledWith("r1", "settle:r1");
    view.rerender(<ChronologicalList timeline={{ ...data, spans: [{ ...execution, shutdownConfirmed: false, uncertain: true }] }} scale={scale} nowMs={ms(30)} onOpen={onOpen}/>);
    expect(items().some(item => item.dataset.key === "settle:r1")).toBe(false);
  });

  it("starts a new heading for each local calendar day", () => {
    const firstDay = new Date(2026, 9, 17, 23, 59).toISOString();
    const secondDay = new Date(2026, 9, 18, 0, 1).toISOString();
    const data = timeline({ rows: [row({ runId: "r1" })], spans: [span("a", "queue", firstDay, firstDay, { runId: "r1" }),
      span("b", "queue", secondDay, secondDay, { runId: "r1" })], events: [] });
    render(<ChronologicalList timeline={data} scale={{ ...scale, breaks: [] }} nowMs={Date.parse(secondDay)} onOpen={vi.fn()}/>);
    expect(document.querySelectorAll('[data-slot="chronological-list"] h3')).toHaveLength(2);
    expect(items().map(item => item.dataset.key)).toEqual(["span:a", "span:b"]);
  });
});
