import { hasDecisionCapability, hasFastRoutingCapability, routerAttention } from "../policy";
import type { Configuration, Draft, Buddy, RouterHealthEntry, RouterHealthIdentity, RoutingHealth, RoutingMode } from "../types";

/** The four parts of a buddy's identity: `harness / provider / model / effort`. */
export function buddyQuad(identity: RouterHealthIdentity): string {
  return `${identity.adapter} / ${identity.provider} / ${identity.model} / ${identity.effort}`;
}

/** What the recorded window says is wrong with routing; the user has something to do about either. */
export type HealthWarning = { kind: "unavailable"; code: string | null } | { kind: "failing"; count: number };

/**
 * The warning the recorded routing window gives, or none. Absent data is
 * unknown, never success; abstentions and cancellations are not
 * failures.
 */
export function healthSummary(health: RoutingHealth | undefined): { warning: HealthWarning | null } {
  if (!health) return { warning: null };
  if (health.available === false) return { warning: { kind: "unavailable", code: health.reasonCode ?? null } };
  if (health.sampleCount > 0 && health.consecutiveFailures > 0) return { warning: { kind: "failing", count: health.consecutiveFailures } };
  return { warning: null };
}

/** The buddy that holds the Router role: the one the service names, the head of the list only while the snapshot carries no such fact. */
export function currentRouterId(list: string[], health: RoutingHealth | undefined): string | null {
  return health && "currentRouterBuddyId" in health ? health.currentRouterBuddyId ?? null : list[0] ?? null;
}

/** Where one buddy of the list stands: it holds the role, can take it, is skipped until a time, or cannot serve. */
export type RouterStatus =
  | { kind: "current" | "standby" }
  | { kind: "retry"; at: string | null }
  | { kind: "unavailable"; code: string | null };

export type RouterLine = {
  buddyId: string;
  /** Absent once the catalogue no longer has the buddy. */
  buddy: Buddy | undefined;
  /** Null where nothing is recorded: a buddy added to the draft, or a snapshot without health facts. */
  status: RouterStatus | null;
};

/**
 * The lines of the Router list in the draft's order. `recorded` is the
 * published list: a buddy that is only in the draft has no health facts, so it
 * is never the current one.
 */
export function routerLines(list: string[], recorded: string[], buddies: Buddy[], health: RoutingHealth | undefined): RouterLine[] {
  const current = currentRouterId(list, health);
  return list.map(buddyId => {
    const entry = health?.routers?.find(candidate => candidate.buddyId === buddyId);
    return { buddyId, buddy: buddies.find(buddy => buddy.buddyId === buddyId), status: statusOf(buddyId, current, recorded, entry) };
  });
}

function statusOf(buddyId: string, current: string | null, recorded: string[], entry: RouterHealthEntry | undefined): RouterStatus | null {
  if (buddyId === current && recorded.includes(buddyId)) return { kind: "current" };
  if (!entry) return null;
  if (entry.inSkipWindow) return { kind: "retry", at: entry.retryAt ?? entry.skipUntil ?? null };
  if (!entry.eligible) return { kind: "unavailable", code: entry.code ?? null };
  return { kind: "standby" };
}

/** Why a buddy cannot be the Router in a mode; the words are the page's own, under `roles.refusal`. */
export type RouterRefusal = "fast" | "review" | "unavailable" | "disabled";

/** The capability comes first: it is the one reason the user cannot fix here. */
export function routerRefusalOf(buddy: Buddy, mode: RoutingMode): RouterRefusal | null {
  if (mode === "fast" && !hasFastRoutingCapability(buddy)) return "fast";
  if (mode === "review" && !hasDecisionCapability(buddy)) return "review";
  if (!buddy.available) return "unavailable";
  if (!buddy.enabled) return "disabled";
  return null;
}

/** One buddy that the list does not have yet, with the reason it cannot be added in the current mode. */
export type RouterCandidate = { buddy: Buddy; refusal: RouterRefusal | null };

/** The buddies of the catalogue outside the list that match the search, those that can serve in `mode` first. */
export function routerCandidates(buddies: Buddy[], list: string[], mode: RoutingMode, query: string): RouterCandidate[] {
  const needle = query.trim().toLowerCase();
  const candidates = buddies
    .filter(buddy => !list.includes(buddy.buddyId) && buddyQuad(buddy).toLowerCase().includes(needle))
    .map(buddy => ({ buddy, refusal: routerRefusalOf(buddy, mode) }));
  return [...candidates.filter(entry => !entry.refusal), ...candidates.filter(entry => entry.refusal)];
}

/** The draft with part of its Router settings replaced. */
export function withConfiguration(draft: Draft, change: Partial<Configuration>): Draft {
  return draft.configuration ? { ...draft, configuration: { ...draft.configuration, ...change } } : draft;
}

function withList(draft: Draft, change: (list: string[]) => string[]): Draft {
  return draft.configuration ? withConfiguration(draft, { routerBuddyIds: change(draft.configuration.routerBuddyIds) }) : draft;
}

/** Adds a buddy at the end of the list; the others keep their order. */
export function addRouter(draft: Draft, buddyId: string): Draft {
  return withList(draft, list => [...list, buddyId]);
}

export function removeRouter(draft: Draft, buddyId: string): Draft {
  return withList(draft, list => list.filter(entry => entry !== buddyId));
}

/** Moves a buddy one place up (-1) or down (1); at the end of the list it stays. */
export function moveRouter(draft: Draft, buddyId: string, by: -1 | 1): Draft {
  return withList(draft, list => {
    const from = list.indexOf(buddyId), to = from + by;
    if (from < 0 || to < 0 || to >= list.length) return list;
    const moved = [...list];
    [moved[from], moved[to]] = [moved[to], moved[from]];
    return moved;
  });
}

/** What the list needs the user to know: nobody can serve now, the first buddy is refused in the mode, or routing is failing. */
export type RouterNote = { kind: "nobody" } | { kind: "head"; buddyId: string; refusal: RouterRefusal | null } | HealthWarning;

/**
 * The one note the list carries, in the order of what matters: no buddy of a
 * non-empty list can serve, then the first buddy being refused in the default
 * mode, then the warning of the recorded window. `refusal` is null once the
 * buddy is no longer in the catalogue.
 */
export function routerNote(source: Pick<Draft, "configuration" | "buddies">, health: RoutingHealth | undefined): RouterNote | null {
  const list = source.configuration?.routerBuddyIds ?? [];
  if (list.length > 0 && currentRouterId(list, health) === null) return { kind: "nobody" };
  const head = routerAttention(source);
  if (head) {
    const buddy = source.buddies.find(candidate => candidate.buddyId === head.buddyId);
    return { kind: "head", buddyId: head.buddyId, refusal: buddy ? routerRefusalOf(buddy, source.configuration!.defaultRoutingMode) : null };
  }
  return healthSummary(health).warning;
}

/** The counts of the window that are written out as names with a number; a count of nothing is left out. */
export const RECORD_COUNTS = ["consecutiveFailures", "abstention", "cancelled", "budgetExhausted", "boundsRejected", "inputChanged"] as const;
export type RecordCount = (typeof RECORD_COUNTS)[number];

export function recordCounts(health: RoutingHealth): { name: RecordCount; n: number }[] {
  const counts: Record<RecordCount, number | undefined> = {
    consecutiveFailures: health.consecutiveFailures,
    abstention: health.abstentionCount,
    cancelled: health.cancelledCount,
    budgetExhausted: health.budgetExhaustedCount,
    boundsRejected: health.boundsRejectedCount,
    inputChanged: health.inputChangedCount,
  };
  return RECORD_COUNTS.flatMap(name => (counts[name] ?? 0) > 0 ? [{ name, n: counts[name]! }] : []);
}
