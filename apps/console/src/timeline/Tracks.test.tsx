import { cleanup, render, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import type { TimelineEvent, TimelineSpan } from "../macro-task-types";
import { createTimelineScale } from "./scale";
import { fitLabel } from "./labels";
import { Axis, Track, spanReadout } from "./Tracks";

afterEach(cleanup);
const at = (minutes: number) => new Date(Date.UTC(2026, 9, 17, 8, minutes)).toISOString();
const scale = createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [], instants: [], canFold: false, nowAtEnd: true }, 800);
const base = macroTaskTimelineFixture();
const execution = (extra: Partial<TimelineSpan> = {}): TimelineSpan => ({ ...base.spans.find(span => span.kind === "execution")!, spanId: "e", startAt: at(5), endAt: at(15), resultStatus: "ok", state: "finished", uncertain: false, shutdownConfirmed: true, expectedSeconds: 600, reminders: null, inquiries: [], ...extra });
function track(spans: TimelineSpan[], extra: { state?: string; events?: TimelineEvent[]; width?: number; tabKey?: string; onOpen?: (runId: string, key: string) => void; onSelect?: (runId: string, key?: string) => void } = {}) {
  return render(<Track row={{ ...base.rows[0]!, state: extra.state ?? "accepted", acceptedAt: null, reviewRejectedAt: null }} spans={spans} events={extra.events ?? []} scale={extra.width ? createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [], instants: [], canFold: false, nowAtEnd: true }, extra.width, extra.width / 40) : scale} nowMs={Date.parse(at(40))} ongoing buddies={[]} onOpen={extra.onOpen ?? vi.fn()} onSelect={extra.onSelect ?? vi.fn()} onHover={vi.fn()} onFocusSpan={vi.fn()} onMarker={vi.fn()} modelColors={new Map([[execution().buddy?.model ?? "", "var(--model-1)"]])} selected={false} tabKey={extra.tabKey}/>);
}
const slot = (name: string) => document.querySelector(`[data-slot=${name}]`);

describe("measured timeline labels", () => {
  it("chooses full, short, or no text by measured widths", () => {
    const width = (text: string) => ({ full: 90, short: 35 })[text]!;
    expect(fitLabel(["full", "short"], { left: 0, right: 100 }, [], width)?.text).toBe("full");
    expect(fitLabel(["full", "short"], { left: 0, right: 60 }, [], width)?.text).toBe("short");
    expect(fitLabel(["full", "short"], { left: 0, right: 30 }, [], width)).toBeNull();
  });
  it("keeps labels clear of marks and previously placed text", () => {
    const first = fitLabel(["full", "short"], { left: 0, right: 200 }, [{ left: 0, right: 40 }, { left: 100, right: 120 }], text => text === "full" ? 100 : 35)!;
    const next = fitLabel(["next"], { left: 0, right: 200 }, [first, { left: 0, right: 40 }, { left: 100, right: 120 }], () => 35)!;
    expect(first.left).toBe(52); expect(first.right).toBe(87); expect(next.left).toBeGreaterThanOrEqual(first.right + 12);
  });
});

describe("timeline drawings follow recorded facts", () => {
  it("uses pale settled execution, solid live/failure and a distinct unconfirmed end", () => {
    track([execution()]);
    expect((slot("execution-bar") as SVGElement).style.fill).toBe("var(--model-1)");
    cleanup(); track([execution({ endAt: null, state: "executing", shutdownConfirmed: false, resultStatus: undefined })]);
    expect(slot("execution-bar")?.getAttribute("class")).toContain("fill-live");
    cleanup(); track([execution({ resultStatus: "failed" })]);
    expect(slot("execution-bar")?.getAttribute("class")).toContain("fill-fail");
    cleanup(); const unknown = execution({ uncertain: true, shutdownConfirmed: false }); track([unknown]);
    expect(slot("unconfirmed-end")).toBeTruthy();
    expect(spanReadout(unknown, Date.parse(at(40)))).toContain("停止未确认");
  });
  it("renders full, abbreviated or no bar text using the measured available room", () => {
    const live = execution({ startAt: at(0), endAt: null, state: "executing", shutdownConfirmed: false, expectedSeconds: null, resultStatus: undefined });
    track([live], { width: 200 });
    expect(document.querySelector('[data-slot=track-label]')?.textContent).toBe("进行中 40 分");
    cleanup(); track([live], { width: 70 });
    expect(document.querySelector('[data-slot=track-label]')?.textContent).toBe("40 分");
    cleanup(); track([live], { width: 25 });
    expect(document.querySelector('[data-slot=track-label]')).toBeNull();
    expect(document.querySelector('[data-key="span:e"]')?.getAttribute("aria-label")).toContain("40 分");
  });
  it("draws silence only for the reminder and turns a passed expected mark orange", () => {
    track([execution({ endAt: null, state: "executing", shutdownConfirmed: false, resultStatus: undefined, reminders: { overdue: true, possiblyStuck: true, lastNativeEventAt: at(20), toolInProgress: false } })]);
    expect(slot("silent-tail")?.getAttribute("x")).toBe("400");
    expect(slot("expected-mark")?.getAttribute("class")).toBe("stroke-hand-mark");
    expect(document.querySelectorAll('[data-slot=track-label]').length).toBeGreaterThan(0);
    cleanup(); track([execution({ reminders: null })]);
    expect(slot("silent-tail")).toBeNull(); expect(slot("expected-mark")).toBeNull();
  });
  it("keeps an unreached expectation in the readout and off the execution bar", () => {
    const live = execution({ startAt: at(5), endAt: null, state: "executing", shutdownConfirmed: false, expectedSeconds: 7200 });
    track([live]);
    expect(slot("expected-mark")).toBeNull();
    expect(spanReadout(live, Date.parse(at(40)))).toContain("预期 2 时");
    cleanup(); track([{ ...live, expectedSeconds: 35 * 60 }]);
    expect(slot("expected-mark")).toBeNull();
  });
  it("keeps Router executions hollow and distinguishes failures and cancelled/abstained routes", () => {
    for (const outcome of ["ok", "failed", "cancelled", "abstention"] as const) {
      cleanup(); track([execution({ kind: "routing", expectedSeconds: null, inquiries: null, resultStatus: outcome === "abstention" ? undefined : outcome, disposition: outcome === "abstention" ? "abstention" : undefined, routing: { routingMode: "review" } })]);
      expect(slot("routing-bar")?.getAttribute("class")).toContain("fill-card");
      if (outcome === "failed") expect(slot("routing-bar")?.getAttribute("class")).toContain("stroke-fail");
      if (["cancelled", "abstention"].includes(outcome)) expect(slot("routing-bar")?.getAttribute("stroke-dasharray")).toBe("3 2");
    }
    expect(spanReadout(execution({ kind: "routing", routing: { routingMode: "review" } }), Date.parse(at(40)))).toContain("审阅");
  });
  it("marks open human waits and keeps settled waits and queues thin", () => {
    track([execution()], { state: "delivered" });
    expect(slot("acceptance-wait")?.getAttribute("stroke-dasharray")).toBe("5 4"); expect(slot("waiting-diamond")).toBeTruthy();
    cleanup(); track([execution({ kind: "host", state: "open", endAt: null, expectedSeconds: null, inquiries: null })]);
    expect(slot("host-wait")?.getAttribute("stroke-dasharray")).toBe("5 4"); expect(slot("waiting-diamond")).toBeTruthy();
    cleanup(); track([execution({ kind: "queue", state: "queued", endAt: null, expectedSeconds: null, inquiries: null })]);
    expect(slot("queue-wait")?.hasAttribute("stroke-dasharray")).toBe(false);
    expect(document.querySelector('[data-slot=track-label]')?.textContent).toContain("排队");
  });
  it("draws hollow inquiries, acceptance checks and dots for separate Host events, excluding dispatch and integration", () => {
    const events = ["dispatch", "accept", "decide", "continue", "integrate", "reject", "conclude", "cancel", "takeover"].map((kind, seq) => ({ ...base.events[0]!, seq, kind, at: at(16 + seq * 2), summary: "Explanation" }));
    track([execution({ inquiries: [{ inquiryId: "q", createdAt: at(10), author: "host-1", state: "answered" }] })], { events });
    expect(slot("inquiry-mark")?.getAttribute("class")).toContain("fill-card");
    expect(document.querySelectorAll('[data-slot=accept-mark]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-slot=event-mark]')).toHaveLength(6);
    expect(spanReadout(execution({ inquiries: [{ inquiryId: "q", createdAt: at(10), author: "host-1", state: "answered" }] }), Date.parse(at(40)))).toContain("已答复");
  });
  it("merges coincident events into one selectable mark and omits integration", async () => {
    const events = ["reject", "continue", "integrate", "accept"].map((kind, seq) => ({ ...base.events[0]!, seq, kind, at: at(20), summary: "Explanation" }));
    const onOpen = vi.fn();
    track([], { events, onOpen, tabKey: "cluster:event:0" });
    expect(document.querySelector('[data-key="event:2"]')).toBeNull();
    expect(document.querySelectorAll('[data-slot=track-label]')).toHaveLength(0);
    expect(document.querySelectorAll('[data-slot=event-cluster]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-slot=event-mark]')).toHaveLength(0);
    const cluster = document.querySelector<SVGGElement>('[data-slot=event-cluster]')!;
    expect(cluster.getAttribute("data-key")).toBe("cluster:event:0");
    expect(cluster.getAttribute("tabindex")).toBe("0");
    await userEvent.setup().click(cluster);
    const content = document.querySelector<HTMLElement>('[data-slot=popover-content]')!;
    expect(within(content).getAllByRole("button")).toHaveLength(3);
    expect(content.textContent).toContain(base.rows[0]!.title);
    expect(within(content).getAllByRole("button")[0]?.getAttribute("aria-label")).toContain("Explanation");
    await userEvent.setup().click(within(content).getAllByRole("button")[2]!);
    expect(onOpen).toHaveBeenCalledWith(base.rows[0]!.runId, "event:3");
  });
  it("moves through mixed event and inquiry entries, then restores cluster focus on Escape", async () => {
    const user = userEvent.setup();
    const inquiry = { inquiryId: "q", createdAt: at(20), author: "host-1", state: "answered" as const };
    const event = { ...base.events[0]!, seq: 7, kind: "reject", at: at(20), summary: "Decision" };
    const onOpen = vi.fn();
    track([execution({ inquiries: [inquiry] })], { events: [event], onOpen, tabKey: "cluster:event:7" });
    const cluster = document.querySelector<SVGGElement>('[data-slot=event-cluster]')!;
    expect(document.querySelector('[data-slot=inquiry-mark]')).toBeNull();
    await user.click(cluster);
    const buttons = within(document.querySelector<HTMLElement>('[data-slot=popover-content]')!).getAllByRole("button");
    expect(document.activeElement).toBe(buttons[0]);
    await user.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(buttons[1]);
    expect(buttons[1]!.textContent).toContain("问询");
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(cluster);
    await user.keyboard("{Enter}");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(onOpen).toHaveBeenCalledWith(base.rows[0]!.runId, "inquiry:q");
  });
  it("selects a single inquiry without opening, then opens it with a double click or Enter", async () => {
    const onOpen = vi.fn();
    const onSelect = vi.fn();
    track([execution({ inquiries: [{ inquiryId: "q", createdAt: at(20), author: "host-1", state: "answered" }] })], { onOpen, onSelect, tabKey: "inquiry:q" });
    const inquiry = document.querySelector<SVGGElement>('[data-key="inquiry:q"]')!;
    expect(inquiry.getAttribute("tabindex")).toBe("0");
    await userEvent.setup().click(inquiry);
    expect(onSelect).toHaveBeenCalledWith(base.rows[0]!.runId, "inquiry:q");
    expect(onOpen).not.toHaveBeenCalled();
    await userEvent.setup().dblClick(inquiry);
    expect(onOpen).toHaveBeenCalledWith(base.rows[0]!.runId, "inquiry:q");
    inquiry.focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });
  it("keeps isolated events direct and groups marks within 16 pixels", async () => {
    const onOpen = vi.fn();
    const events = [0, 1, 3].map((minute, seq) => ({ ...base.events[0]!, seq, kind: "reject", at: at(20 + minute), summary: `Event ${seq}` }));
    track([], { events, onOpen, width: 400 });
    expect(document.querySelectorAll('[data-slot=event-cluster]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-slot=event-mark]')).toHaveLength(1);
    await userEvent.setup().dblClick(document.querySelector('[data-key="event:2"]')!);
    expect(onOpen).toHaveBeenCalledWith(base.rows[0]!.runId, "event:2");
  });
  it("hides ticks and breaks from screen readers", () => {
    render(<Axis scale={scale} ongoing nowMs={Date.parse(at(40))}/>);
    expect(slot("timeline-axis")?.getAttribute("aria-hidden")).toBe("true");
  });
  it("draws a centered duration and fracture on the axis", () => {
    const folded = createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [{ startMs: Date.parse(at(0)), endMs: Date.parse(at(2)) }, { startMs: Date.parse(at(38)), endMs: Date.parse(at(40)) }], instants: [Date.parse(at(2)), Date.parse(at(38))], canFold: true, nowAtEnd: true }, 800);
    render(<Axis scale={folded} ongoing nowMs={Date.parse(at(40))}/>);
    expect(slot("axis-break-mark")).toBeTruthy(); expect(slot("gap-label")?.textContent).toBe("36 分");
  });
  it("keeps the shared hour-and-minute spacing in a centered break label", () => {
    const folded = createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(138)) }, busy: [{ startMs: Date.parse(at(0)), endMs: Date.parse(at(2)) }, { startMs: Date.parse(at(136)), endMs: Date.parse(at(138)) }], instants: [Date.parse(at(2)), Date.parse(at(136))], canFold: true, nowAtEnd: true }, 800);
    render(<Axis scale={folded} ongoing nowMs={Date.parse(at(138))}/>);
    expect(slot("gap-label")?.textContent).toBe("2 时 14 分");
  });
});

it("draws minute readouts and adds seconds only above thirty pixels per minute", () => {
  const fine = createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [], instants: [], canFold: false, nowAtEnd: true }, 1600, 40);
  const view = render(<Axis scale={scale} ongoing nowMs={Date.parse(at(40))} pointerX={40}/>);
  expect(slot("pointer-clock")?.textContent).toMatch(/^\d{2}:\d{2}$/);
  view.rerender(<Axis scale={fine} ongoing nowMs={Date.parse(at(40))} pointerX={40}/>);
  expect(slot("pointer-clock")?.textContent).toMatch(/^\d{2}:\d{2}:\d{2}$/);
});

it("draws acceptance and failed conclusions as circles with separate glyphs", () => {
  track([], { state: "failed", events: [{ ...base.events[0]!, kind: "accept", at: at(5) }, { ...base.events[0]!, seq: 99, kind: "conclude", at: at(35) }] });
  for (const name of ["accept-mark", "failed-conclusion-mark"]) {
    expect(slot(name)?.querySelector("circle")).toBeTruthy();
    expect(slot(name)?.querySelector("path")).toBeTruthy();
  }
});

it("leaves twelve pixels after an inquiry label, or omits a short waiting duration", () => {
  const inquiry = fitLabel(["Inquiry"], { left: 0, right: 100 }, [], () => 40)!;
  const tight = fitLabel(["36 min"], { left: 42, right: 80 }, [inquiry], () => 30);
  expect(tight).toBeNull();
  const room = fitLabel(["36 min"], { left: 42, right: 100 }, [inquiry], () => 30)!;
  expect(room.left - inquiry.right).toBeGreaterThanOrEqual(12);
});

it("does not put a clock inside a compressed interval", () => {
  const folded = createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [{ startMs: Date.parse(at(0)), endMs: Date.parse(at(2)) }, { startMs: Date.parse(at(38)), endMs: Date.parse(at(40)) }], instants: [], canFold: true, nowAtEnd: true }, 800);
  const gap = folded.breaks[0]!;
  render(<Axis scale={folded} ongoing nowMs={Date.parse(at(40))} pointerX={(gap.startX + gap.endX) / 2}/>);
  expect(slot("pointer-clock")).toBeNull();
});
