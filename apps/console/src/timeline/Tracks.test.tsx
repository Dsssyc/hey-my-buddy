import { cleanup, render } from "@testing-library/react";
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
function track(spans: TimelineSpan[], extra: { state?: string; events?: TimelineEvent[]; width?: number } = {}) {
  return render(<Track row={{ ...base.rows[0]!, state: extra.state ?? "accepted", acceptedAt: null, reviewRejectedAt: null }} spans={spans} events={extra.events ?? []} scale={extra.width ? createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [], instants: [], canFold: false, nowAtEnd: true }, extra.width, extra.width / 40) : scale} nowMs={Date.parse(at(40))} ongoing buddies={[]} onOpen={vi.fn()} onHover={vi.fn()} onFocusSpan={vi.fn()} onEvent={vi.fn()} selected={false}/>);
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
    expect(first.left).toBe(44); expect(first.right).toBe(79); expect(next.left).toBeGreaterThanOrEqual(first.right + 4);
  });
});

describe("timeline drawings follow recorded facts", () => {
  it("uses pale settled execution, solid live/failure and a distinct unconfirmed end", () => {
    track([execution()]);
    expect(slot("execution-bar")?.getAttribute("class")).toContain("fill-past-wash");
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
    expect(document.querySelector('[data-key="span:e"]')?.getAttribute("aria-label")).toContain("40 分钟");
  });
  it("draws silence only for the reminder and turns a passed expected mark orange", () => {
    track([execution({ endAt: null, state: "executing", shutdownConfirmed: false, resultStatus: undefined, reminders: { overdue: true, possiblyStuck: true, lastNativeEventAt: at(20), toolInProgress: false } })]);
    expect(slot("silent-tail")?.getAttribute("x")).toBe("400");
    expect(slot("expected-mark")?.getAttribute("class")).toBe("stroke-hand-mark");
    expect(document.querySelectorAll('[data-slot=track-label]').length).toBeGreaterThan(0);
    cleanup(); track([execution({ reminders: null })]);
    expect(slot("silent-tail")).toBeNull(); expect(slot("expected-mark")).toBeNull();
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
  it("draws hollow inquiries, acceptance checks and dots for every other Host event, excluding dispatch", () => {
    const events = ["dispatch", "accept", "decide", "continue", "integrate", "reject", "conclude", "cancel", "takeover"].map((kind, seq) => ({ ...base.events[0]!, seq, kind, at: at(16 + seq * 2), summary: "Explanation" }));
    track([execution({ inquiries: [{ inquiryId: "q", createdAt: at(10), author: "host-1", state: "answered" }] })], { events });
    expect(slot("inquiry-mark")?.getAttribute("class")).toContain("fill-card");
    expect(document.querySelectorAll('[data-slot=accept-mark]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-slot=event-mark]')).toHaveLength(7);
    expect(spanReadout(execution({ inquiries: [{ inquiryId: "q", createdAt: at(10), author: "host-1", state: "answered" }] }), Date.parse(at(40)))).toContain("已答复");
  });
  it("draws a centered duration and fracture on the axis", () => {
    const folded = createTimelineScale({ domain: { startMs: Date.parse(at(0)), endMs: Date.parse(at(40)) }, busy: [{ startMs: Date.parse(at(0)), endMs: Date.parse(at(2)) }, { startMs: Date.parse(at(38)), endMs: Date.parse(at(40)) }], instants: [Date.parse(at(2)), Date.parse(at(38))], canFold: true, nowAtEnd: true }, 800);
    render(<Axis scale={folded} ongoing nowMs={Date.parse(at(40))}/>);
    expect(slot("axis-break-mark")).toBeTruthy(); expect(slot("gap-label")?.textContent).toBe("36 分");
  });
});
