import { parseTimelineInstant } from "../macro-task-timeline-layout";
import { acceptanceWaitMs, spanOutcome } from "../macro-task-display";
import type { SpanOutcome } from "../macro-task-display";
import type { MacroTaskTimeline, TimelineBuddy, TimelineEvent, TimelineSpan } from "../macro-task-types";

/**
 * The steps of one micro task: its row of the macro task's timeline stood on end.
 * Each step is one thing that happened (a delegation, a route, a turn, a change
 * of buddy, a Host decision, a cancel, a takeover, the acceptance); the lines
 * between steps are the waits. Built from the spans and events the page has
 * already read, so what the rail says is what the timeline says.
 */
type WaitKind = "queue" | "host" | "lead" | "acceptance";

export type Wait = {
  kind: WaitKind;
  /** Null when the wait has no recorded start or end to measure. */
  ms: number | null;
  /** Still waiting: nobody has taken it up. */
  open: boolean;
  /** The timeline span the line is drawn from; the acceptance wait has none. */
  spanId: string | null;
  /** The Host request a Host wait is for. */
  requestId: string | null;
};

type StepKind = "delegation" | "routing" | "turn" | "handoff" | "decision" | "continue" | "cancel" | "takeover" | "acceptance"
  /** A run with no collaboration record has one step: a command's execution, or a Router's calculation. */
  | "record" | "calculation";

export type Step = {
  key: string;
  kind: StepKind;
  /** Where the step starts, in epoch milliseconds, when it was recorded. */
  at: number | null;
  /** Milliseconds the step itself took; a running turn runs to now. */
  ms: number | null;
  span: TimelineSpan | null;
  /** The events the step stands for: one, or every acceptance event. */
  events: TimelineEvent[];
  /** The step has not happened yet: an open Host decision, or the acceptance of a delivered micro task. */
  pending: boolean;
  /** Turns only: the turn's number. */
  turnIndex: number | null;
  outcome: SpanOutcome | null;
  /** Handoffs only: the buddies before and after. */
  from: TimelineBuddy | null;
  to: TimelineBuddy | null;
  /** The lines that lead to this step, oldest first. */
  waits: Wait[];
};

export type Steps = {
  steps: Step[];
  /** Waits after the last step. */
  tail: Wait[];
  /** False when the timeline read has no row for the micro task: the rail then has the delegation alone. */
  complete: boolean;
};

/** What the acceptance step lists, and of those what settles it: an integration alone leaves the micro task waiting. */
const ACCEPTANCE_EVENTS = ["integrate", "accept", "reject", "conclude"];
const SETTLING_EVENTS = ["accept", "reject", "conclude"];
const AFTER_DELIVERY = ["delivered", "accepted", "concluded"];
/** Order of steps that start at the same instant. */
const RANK: Record<StepKind, number> = {
  delegation: 0, decision: 1, continue: 1, cancel: 1, takeover: 1, routing: 2, handoff: 3, turn: 4, acceptance: 5, record: 4, calculation: 4,
};

/** A change of buddy needs both sides recorded: a missing buddy is not a different one. */
const changed = (left: TimelineBuddy | null, right: TimelineBuddy | null) =>
  !!left && !!right && (left.harness !== right.harness || left.provider !== right.provider
    || left.model !== right.model || left.effort !== right.effort);

const ms = parseTimelineInstant;

function blank(kind: StepKind, key: string, at: number | null): Step {
  return { key, kind, at, ms: null, span: null, events: [], pending: false, turnIndex: null, outcome: null, from: null, to: null, waits: [] };
}

/** The one step of a run that has no collaboration record. */
export function recordSteps(kind: "record" | "calculation"): Steps {
  return { steps: [blank(kind, kind, null)], tail: [], complete: true };
}

/** The steps of the micro task `runId` in the macro task's timeline. */
export function buildSteps(timeline: MacroTaskTimeline | null, runId: string, nowMs: number): Steps {
  const row = timeline?.rows.find(candidate => candidate.runId === runId) ?? null;
  if (!timeline || !row) return { steps: [{ ...blank("delegation", "delegation", null) }], tail: [], complete: false };
  const spans = timeline.spans.filter(span => span.runId === runId)
    .sort((left, right) => (ms(left.startAt) ?? Number.POSITIVE_INFINITY) - (ms(right.startAt) ?? Number.POSITIVE_INFINITY) || 0);
  const events = timeline.events.filter(event => event.runId === runId).sort((left, right) => left.seq - right.seq);
  const lead = row.rootLeadRunId !== null;

  const dispatch = events.find(event => event.kind === "dispatch");
  const delegation = { ...blank("delegation", "delegation", ms(dispatch?.at) ?? ms(row.createdAt)), events: dispatch ? [dispatch] : [] };
  const steps: Step[] = [delegation];

  const executions = spans.filter(span => span.kind === "execution");
  for (const span of spans) {
    if (span.kind === "routing") {
      const end = ms(span.endAt);
      steps.push({ ...blank("routing", `span:${span.spanId}`, ms(span.startAt)), span, outcome: spanOutcome(span),
        ms: end !== null && ms(span.startAt) !== null ? end - ms(span.startAt)! : null });
    } else if (span.kind === "execution") {
      const outcome = spanOutcome(span);
      const start = ms(span.startAt);
      const end = ms(span.endAt) ?? (outcome === "running" ? nowMs : null);
      steps.push({ ...blank("turn", `span:${span.spanId}`, start), span, outcome, turnIndex: span.turnIndex ?? executions.indexOf(span) + 1,
        ms: start !== null && end !== null ? Math.max(0, end - start) : null });
    }
  }
  for (const event of events) {
    if (event.kind === "decide") steps.push({ ...blank("decision", `event:${event.seq}`, ms(event.at)), events: [event] });
    else if (event.kind === "continue") steps.push({ ...blank("continue", `event:${event.seq}`, ms(event.at)), events: [event] });
    else if (event.kind === "cancel") steps.push({ ...blank("cancel", `event:${event.seq}`, ms(event.at)), events: [event] });
    else if (event.kind === "takeover") steps.push({ ...blank("takeover", `event:${event.seq}`, ms(event.at)), events: [event] });
  }
  // A Host request that is still open ends in a decision nobody has made.
  const open = spans.find(span => span.kind === "host" && span.state === "open" && span.endAt === null);
  if (open) steps.push({ ...blank("decision", `span:${open.spanId}`, null), span: open, pending: true });

  const when = (step: Step) => step.at ?? Number.POSITIVE_INFINITY;
  steps.sort((left, right) => (when(left) === when(right) ? 0 : when(left) < when(right) ? -1 : 1) || RANK[left.kind] - RANK[right.kind]);
  // The pending decision has no instant: it closes the sequence before the acceptance.
  // A handoff sits just before the turn the new buddy takes.
  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index]!;
    if (step.kind !== "turn") continue;
    const previous = steps.slice(0, index).reverse().find(candidate => candidate.kind === "turn");
    if (previous && changed(previous.span!.buddy, step.span!.buddy)) {
      steps.splice(index, 0, { ...blank("handoff", `handoff:${step.span!.spanId}`, step.at), from: previous.span!.buddy, to: step.span!.buddy });
      index += 1;
    }
  }

  const listed = events.filter(event => ACCEPTANCE_EVENTS.includes(event.kind));
  const settled = listed.some(event => SETTLING_EVENTS.includes(event.kind));
  const awaiting = row.state === "delivered" && !row.acceptedAt && !row.reviewRejectedAt && !settled;
  if (listed.length > 0 || awaiting || AFTER_DELIVERY.includes(row.state)) {
    steps.push({ ...blank("acceptance", "acceptance", ms(listed[0]?.at)), events: listed, pending: awaiting });
  }

  // The lines between the steps: a wait leads to the step that ends it. A Host wait ends in the decision made on its own
  // request, or in the continuation that came at its end; any other wait in the first step that starts once it has ended.
  const lines: { wait: Wait; start: number; end: number | null }[] = [];
  for (const span of spans) {
    const start = ms(span.startAt);
    if (start === null || (span.kind !== "queue" && span.kind !== "host")) continue;
    const open = spanOutcome(span) === "open";
    const end = open ? null : ms(span.endAt);
    lines.push({ start, end, wait: { kind: span.kind === "queue" ? "queue" : lead ? "lead" : "host",
      ms: open ? Math.max(0, nowMs - start) : end === null ? null : end - start, open, spanId: span.spanId,
      requestId: span.kind === "host" ? span.requestId : null } });
  }
  const tail: Wait[] = [];
  const taken = new Set<Step>();
  for (const { wait, start, end } of lines.sort((left, right) => left.start - right.start)) {
    const byRequest = wait.requestId === null ? undefined
      : steps.find(step => step.kind === "decision" && step.events[0]?.requestId === wait.requestId);
    const byContinue = end === null || wait.requestId === null ? undefined
      : steps.find(step => step.kind === "continue" && !taken.has(step) && step.at === end);
    const target = wait.open
      ? (wait.kind === "queue" ? undefined : steps.find(step => step.pending && step.kind === "decision"))
      : byRequest ?? byContinue ?? steps.find(step => step.at !== null && (end !== null ? step.at >= end : step.at > start));
    if (target && target === byContinue) taken.add(target);
    (target ? target.waits : tail).push(wait);
  }
  // From delivery to the acceptance: still waiting, or the wait that ended in a verdict.
  const finish = steps[steps.length - 1]!;
  if (finish.kind === "acceptance") {
    const executionEnd = Math.max(0, ...executions.map(span => ms(span.endAt) ?? 0));
    const settledWait = acceptanceWaitMs(row, spans);
    if (finish.pending) finish.waits.push({ kind: "acceptance", ms: executionEnd > 0 ? Math.max(0, nowMs - executionEnd) : null, open: true, spanId: null, requestId: null });
    else if (settledWait !== null) finish.waits.push({ kind: "acceptance", ms: settledWait, open: false, spanId: null, requestId: null });
  }
  return { steps, tail, complete: true };
}

/**
 * The step a timeline span or marker belongs to. A queue or a Host wait is the
 * line before a step: it belongs to the step that ends the wait.
 */
export function stepForItem(result: Steps, key: string, timeline: MacroTaskTimeline | null): number | null {
  const direct = result.steps.findIndex(step => step.key === key);
  if (direct >= 0) return direct;
  if (key.startsWith("settle:")) return result.steps.findIndex(step => step.kind === "acceptance");
  if (key.startsWith("event:")) {
    const event = timeline?.events.find(candidate => `event:${candidate.seq}` === key);
    if (event?.kind === "dispatch") return 0;
    if (event && ACCEPTANCE_EVENTS.includes(event.kind)) return result.steps.findIndex(step => step.kind === "acceptance");
    return null;
  }
  if (key.startsWith("span:")) {
    const spanId = key.slice("span:".length);
    const line = result.steps.findIndex(step => step.waits.some(wait => wait.spanId === spanId));
    if (line >= 0) return line;
    return result.tail.some(wait => wait.spanId === spanId) ? result.steps.length - 1 : null;
  }
  return null;
}

/** The step a detail opens on when nothing names one: what waits for a decision, what runs, the last turn, the route, the delegation. */
export function defaultStep(result: Steps): number {
  const steps = result.steps;
  const choose = (test: (step: Step) => boolean, last = false) => {
    const matches = steps.map((step, index) => ({ step, index })).filter(({ step }) => test(step));
    return matches.length ? (last ? matches[matches.length - 1]! : matches[0]!).index : -1;
  };
  const found = [
    choose(step => step.kind === "decision" && step.pending),
    choose(step => step.kind === "turn" && step.outcome === "running"),
    choose(step => step.kind === "turn", true),
    choose(step => step.kind === "routing", true),
  ].find(index => index >= 0);
  return found ?? 0;
}
