import { describe, expect, it } from "vitest";
import { buildSteps, defaultStep, stepForItem } from "./steps";
import { OPUS, at, event, row, span, timeline } from "./task-detail.fixtures";

const NOW = Date.parse(at("11:30"));
const kinds = (result: ReturnType<typeof buildSteps>) => result.steps.map(step => step.kind);

describe("the steps of a micro task", () => {
  it("stands the row of the timeline on end: the delegation, the route, each turn, each decision, the acceptance", () => {
    const result = buildSteps(timeline(), "m1", NOW);
    // Delegate, route, turn 1, decide, continue, handoff (the buddy changes), turn 2, acceptance (waited for).
    expect(kinds(result)).toEqual(["delegation", "routing", "turn", "decision", "continue", "handoff", "turn", "acceptance"]);
    expect(result.steps.filter(step => step.kind === "turn").map(step => step.turnIndex)).toEqual([1, 2]);
    expect(result.complete).toBe(true);
    expect(result.steps.at(-1)!.pending).toBe(true);
  });

  it("puts a handoff only between two turns whose buddies differ, never for an unrecorded one", () => {
    const same = timeline();
    same.spans.find(item => item.spanId === "e2")!.buddy = { ...same.spans.find(item => item.spanId === "e1")!.buddy! };
    expect(kinds(buildSteps(same, "m1", NOW))).not.toContain("handoff");
    const unknown = timeline();
    unknown.spans.find(item => item.spanId === "e2")!.buddy = null;
    expect(kinds(buildSteps(unknown, "m1", NOW))).not.toContain("handoff");
    const handoff = buildSteps(timeline(), "m1", NOW).steps.find(step => step.kind === "handoff")!;
    expect(handoff.from).toMatchObject({ model: "gpt-6.1-sol" });
    expect(handoff.to).toMatchObject({ model: OPUS.model });
  });

  it("draws the waits as lines in front of the step that ends them, in the kinds the board records", () => {
    const result = buildSteps(timeline(), "m1", NOW);
    const waits = result.steps.map(step => step.waits.map(wait => [wait.kind, wait.ms, wait.open]));
    // 1 minute queued before the route, 10 minutes for the Host before its decision, 1 minute queued before the handoff.
    expect(waits[1]).toEqual([["queue", 60000, false]]);
    expect(waits[3]).toEqual([["host", 600000, false]]);
    expect(waits[5]).toEqual([["queue", 60000, false]]);
    // The delivered micro task is awaited for acceptance, from the end of its last turn to now.
    expect(waits[7]).toEqual([["acceptance", NOW - Date.parse(at("11:00")), true]]);
  });

  it("calls a wait for a Lead's decision a wait for the Lead", () => {
    const owned = timeline();
    owned.rows = [row({ rootLeadRunId: "lead-1" })];
    const result = buildSteps(owned, "m1", NOW);
    expect(result.steps[3]!.waits.map(wait => wait.kind)).toEqual(["lead"]);
  });

  it("ends a Host wait that is still open in a decision nobody has made, and runs the open line to now", () => {
    const open = timeline();
    open.spans = open.spans.filter(item => !["q2", "e2"].includes(item.spanId));
    open.events = open.events.filter(item => item.kind === "dispatch");
    const host = open.spans.find(item => item.spanId === "h1")!;
    Object.assign(host, { endAt: null, state: "open" });
    open.rows = [row({ state: "awaiting-host", category: "host" })];
    const result = buildSteps(open, "m1", NOW);
    const last = result.steps.at(-1)!;
    expect(last).toMatchObject({ kind: "decision", pending: true });
    expect(last.waits).toEqual([{ kind: "host", ms: NOW - Date.parse(at("10:30")), open: true, spanId: "h1", requestId: "req-1" }]);
    expect(defaultStep(result)).toBe(result.steps.length - 1);
  });

  it("gives cancel and takeover their own steps, and a takeover is not a handoff", () => {
    const changed = timeline();
    changed.events.push(event(4, "cancel", at("10:50"), { stopInitiator: "host" }), event(5, "takeover", at("10:55")));
    const result = buildSteps(changed, "m1", NOW);
    expect(kinds(result)).toContain("cancel");
    expect(kinds(result)).toContain("takeover");
    expect(result.steps.filter(step => step.kind === "handoff")).toHaveLength(1);
  });

  it("collects the integration, the acceptance, the review and the Host's conclusion into the last step", () => {
    const settled = timeline();
    settled.events.push(event(4, "integrate", at("11:03")), event(5, "accept", at("11:05")), event(6, "reject", at("11:06")), event(7, "conclude", at("11:07")));
    settled.rows = [row({ state: "accepted", acceptedAt: at("11:05"), acceptanceVerdict: "accepted" })];
    const result = buildSteps(settled, "m1", NOW);
    const acceptance = result.steps.at(-1)!;
    expect(acceptance.kind).toBe("acceptance");
    expect(acceptance.events.map(item => item.kind)).toEqual(["integrate", "accept", "reject", "conclude"]);
    expect(acceptance.pending).toBe(false);
    // A micro task that is still running has no acceptance to wait for.
    const running = timeline();
    running.rows = [row({ state: "executing", status: "running" })];
    expect(kinds(buildSteps(running, "m1", NOW))).not.toContain("acceptance");
  });

  it("puts a decision before the route it set off when both happen at the same instant", () => {
    const same = timeline();
    same.spans.push(span("r2", "routing", at("10:40"), at("10:41"), { attemptId: "att-r2", decisionId: "decision-2" }));
    const order = kinds(buildSteps(same, "m1", NOW));
    expect(order.indexOf("decision")).toBeLessThan(order.lastIndexOf("routing"));
  });

  it("is the delegation alone, with a note, when the timeline read has no row for the micro task", () => {
    const truncated = timeline();
    truncated.rows = [];
    expect(buildSteps(truncated, "m1", NOW)).toMatchObject({ complete: false, tail: [], steps: [{ kind: "delegation", at: null }] });
    expect(buildSteps(null, "m1", NOW).complete).toBe(false);
  });

});

describe("which step opens", () => {
  const result = buildSteps(timeline(), "m1", NOW);
  const open = (key: string) => stepForItem(result, key, timeline());

  it("is the step a span or a marker of the timeline belongs to", () => {
    expect(open("span:r1")).toBe(1);
    expect(open("span:e1")).toBe(2);
    expect(open("span:e2")).toBe(6);
    expect(open("event:1")).toBe(0);
    expect(open("event:2")).toBe(3);
    expect(open("event:3")).toBe(4);
    expect(open("settle:m1")).toBe(7);
    expect(open("span:unknown")).toBeNull();
  });

  it("is the step that ends a wait, for the line of a queue or of a Host wait", () => {
    expect(open("span:q1")).toBe(1);
    expect(open("span:h1")).toBe(3);
    expect(open("span:q2")).toBe(5);
  });

  it("is, with nothing named, the decision waited for, else the turn running, the last turn, the route, the delegation", () => {
    expect(defaultStep(result)).toBe(6);
    const running = timeline();
    Object.assign(running.spans.find(item => item.spanId === "e2")!, { endAt: null, state: "executing", shutdownConfirmed: false });
    expect(defaultStep(buildSteps(running, "m1", NOW))).toBe(6);
    const routed = timeline();
    routed.spans = routed.spans.filter(item => item.kind === "routing");
    routed.events = routed.events.filter(item => item.kind === "dispatch");
    routed.rows = [row({ state: "executing", status: "running" })];
    expect(defaultStep(buildSteps(routed, "m1", NOW))).toBe(1);
    expect(defaultStep(buildSteps(null, "m1", NOW))).toBe(0);
  });
});

describe("what the steps do not make up", () => {
  it("keeps a stop that is not confirmed as it is on the timeline, neither ended nor failed", () => {
    const unsure = timeline();
    Object.assign(unsure.spans.find(item => item.spanId === "e2")!, { uncertain: true, shutdownConfirmed: false });
    const turns = buildSteps(unsure, "m1", NOW).steps.filter(step => step.kind === "turn");
    expect(turns.map(step => step.outcome)).toEqual(["finished", "unknown"]);
    const route = timeline();
    Object.assign(route.spans.find(item => item.spanId === "r1")!, { uncertain: true });
    expect(buildSteps(route, "m1", NOW).steps.find(step => step.kind === "routing")!.outcome).toBe("unknown");
  });

  it("does not take the registering of an integration for the acceptance: the Host is waited for until it accepts, sends back or concludes", () => {
    const integrated = timeline();
    integrated.events.push(event(4, "integrate", at("11:03")));
    const waiting = buildSteps(integrated, "m1", NOW).steps.at(-1)!;
    expect(waiting).toMatchObject({ kind: "acceptance", pending: true });
    expect(waiting.events.map(item => item.kind)).toEqual(["integrate"]);
    expect(waiting.waits.map(wait => [wait.kind, wait.open])).toEqual([["acceptance", true]]);
    for (const verdict of ["accept", "reject", "conclude"]) {
      const settled = timeline();
      settled.events.push(event(4, "integrate", at("11:03")), event(5, verdict, at("11:05")));
      settled.rows = [row({ state: "accepted", acceptedAt: at("11:05") })];
      const step = buildSteps(settled, "m1", NOW).steps.at(-1)!;
      expect(step, verdict).toMatchObject({ kind: "acceptance", pending: false });
      expect(step.events.map(item => item.kind), verdict).toEqual(["integrate", verdict]);
    }
  });

  it("gives a Host wait the request it is for, and the step that ends it, whether a decision or a continuation names that request or not", () => {
    const result = buildSteps(timeline(), "m1", NOW);
    expect(result.steps[3]!.waits[0]).toMatchObject({ kind: "host", requestId: "req-1" });
    expect(result.steps.flatMap(step => step.waits).filter(wait => wait.kind !== "host").every(wait => wait.requestId === null)).toBe(true);
    // Two requests answered at the same instant, by continuations that name no request: each wait ends in its own.
    const twins = timeline();
    twins.spans = [twins.spans[0]!, twins.spans[1]!, twins.spans[2]!,
      span("hA", "host", at("10:30"), at("10:40"), { state: "approved", requestId: "req-A" }),
      span("hB", "host", at("10:31"), at("10:40"), { state: "approved", requestId: "req-B" }),
      twins.spans[5]!];
    twins.events = [event(1, "dispatch", at("10:00")), event(2, "continue", at("10:40"), { continuationId: "c-A" }), event(3, "continue", at("10:40"), { continuationId: "c-B" })];
    const continued = buildSteps(twins, "m1", NOW).steps.filter(step => step.kind === "continue");
    expect(continued.map(step => step.waits.map(wait => wait.requestId))).toEqual([["req-A"], ["req-B"]]);
  });

  it("keeps spans that start at the same instant, or have no start, in the order the timeline gave them", () => {
    const tied = timeline();
    tied.spans.push(span("q9", "queue", at("10:00"), at("10:01")), span("q8", "queue", null, null));
    expect(() => buildSteps(tied, "m1", NOW)).not.toThrow();
    expect(buildSteps(tied, "m1", NOW).steps.map(step => step.kind)).toEqual(kinds(buildSteps(timeline(), "m1", NOW)));
  });
});
