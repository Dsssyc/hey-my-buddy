import type { TimelineEvent } from "../macro-task-types";

export type EventAction = { event: TimelineEvent; relatedEvents?: TimelineEvent[] };

/** Presentation only: keep each source fact intact, with one mark per Host action. */
export function eventActions(events: readonly TimelineEvent[]): EventAction[] {
  const ordered = [...events].sort((a, b) => a.seq - b.seq);
  const continuations = new Set<number>();
  const related = new Map<number, TimelineEvent[]>();
  const integrations = new Map<string, TimelineEvent[]>();
  const integrationKey = (event: TimelineEvent) => JSON.stringify([event.runId, event.integrationId]);
  for (const [index, event] of ordered.entries()) {
    const next = ordered[index + 1];
    // workflow_continue writes these in one transaction. Its service regression
    // test pins adjacency; no elapsed-time threshold stands in for that fact.
    if (event.kind === "reject" && next?.kind === "continue" && next.runId === event.runId && next.seq === event.seq + 1) {
      related.set(event.seq, [next]);
      continuations.add(next.seq);
    }
    if (event.kind === "integrate" && event.integrationId) {
      const key = integrationKey(event);
      integrations.set(key, [...(integrations.get(key) ?? []), event]);
    }
  }
  return ordered.filter(event => !["dispatch", "integrate"].includes(event.kind) && !continuations.has(event.seq))
    .map(event => ({ event, relatedEvents: event.kind === "accept" && event.integrationId ? integrations.get(integrationKey(event)) : related.get(event.seq) }));
}
