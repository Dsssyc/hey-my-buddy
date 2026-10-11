import { hasDecisionCapability, hasFastRoutingCapability, routerAttention } from "../policy";
import type { Configuration, Draft, Profile, RouterHealthEntry, RouterHealthIdentity, RoutingHealth, RoutingMode } from "../types";

/** The four parts of a buddy's identity: `harness / provider / model / effort`. */
export function buddyQuad(identity: RouterHealthIdentity): string {
  return `${identity.adapter} / ${identity.provider} / ${identity.model} / ${identity.effort}`;
}

/** What the recorded window says is wrong with routing; the user has something to do about either. */
export type HealthWarning = { kind: "unavailable"; code: string | null } | { kind: "failing"; count: number };

/**
 * The warning the recorded routing window gives, or none. Absent data is
 * unknown, never success; abstentions, cancellations and stale results are not
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
  return health && "currentRouterProfileId" in health ? health.currentRouterProfileId ?? null : list[0] ?? null;
}

/** Where one buddy of the list stands: it holds the role, can take it, is skipped until a time, or cannot serve. */
export type RouterStatus =
  | { kind: "current" | "standby" }
  | { kind: "retry"; at: string | null }
  | { kind: "unavailable"; code: string | null };

export type RouterLine = {
  profileId: string;
  /** Absent once the catalogue no longer has the buddy. */
  profile: Profile | undefined;
  /** Null where nothing is recorded: a buddy added to the draft, or a snapshot without health facts. */
  status: RouterStatus | null;
};

/**
 * The lines of the Router list in the draft's order. `recorded` is the
 * published list: a buddy that is only in the draft has no health facts, so it
 * is never the current one.
 */
export function routerLines(list: string[], recorded: string[], profiles: Profile[], health: RoutingHealth | undefined): RouterLine[] {
  const current = currentRouterId(list, health);
  return list.map(profileId => {
    const entry = health?.routers?.find(candidate => candidate.profileId === profileId);
    return { profileId, profile: profiles.find(profile => profile.profileId === profileId), status: statusOf(profileId, current, recorded, entry) };
  });
}

function statusOf(profileId: string, current: string | null, recorded: string[], entry: RouterHealthEntry | undefined): RouterStatus | null {
  if (profileId === current && recorded.includes(profileId)) return { kind: "current" };
  if (!entry) return null;
  if (entry.inSkipWindow) return { kind: "retry", at: entry.retryAt ?? entry.skipUntil ?? null };
  if (!entry.eligible) return { kind: "unavailable", code: entry.code ?? null };
  return { kind: "standby" };
}

/** Why a buddy cannot be the Router in a mode; the words are the page's own, under `roles.refusal`. */
export type RouterRefusal = "fast" | "review" | "unavailable" | "disabled";

/** The capability comes first: it is the one reason the user cannot fix here. */
export function routerRefusalOf(profile: Profile, mode: RoutingMode): RouterRefusal | null {
  if (mode === "fast" && !hasFastRoutingCapability(profile)) return "fast";
  if (mode === "review" && !hasDecisionCapability(profile)) return "review";
  if (!profile.available) return "unavailable";
  if (!profile.enabled) return "disabled";
  return null;
}

/** One buddy that the list does not have yet, with the reason it cannot be added in the current mode. */
export type RouterCandidate = { profile: Profile; refusal: RouterRefusal | null };

/** The buddies of the catalogue outside the list that match the search, those that can serve in `mode` first. */
export function routerCandidates(profiles: Profile[], list: string[], mode: RoutingMode, query: string): RouterCandidate[] {
  const needle = query.trim().toLowerCase();
  const candidates = profiles
    .filter(profile => !list.includes(profile.profileId) && buddyQuad(profile).toLowerCase().includes(needle))
    .map(profile => ({ profile, refusal: routerRefusalOf(profile, mode) }));
  return [...candidates.filter(entry => !entry.refusal), ...candidates.filter(entry => entry.refusal)];
}

/** The draft with part of its Router settings replaced. */
export function withConfiguration(draft: Draft, change: Partial<Configuration>): Draft {
  return draft.configuration ? { ...draft, configuration: { ...draft.configuration, ...change } } : draft;
}

function withList(draft: Draft, change: (list: string[]) => string[]): Draft {
  return draft.configuration ? withConfiguration(draft, { routerProfileIds: change(draft.configuration.routerProfileIds) }) : draft;
}

/** Adds a buddy at the end of the list; the others keep their order. */
export function addRouter(draft: Draft, profileId: string): Draft {
  return withList(draft, list => [...list, profileId]);
}

export function removeRouter(draft: Draft, profileId: string): Draft {
  return withList(draft, list => list.filter(entry => entry !== profileId));
}

/** Moves a buddy one place up (-1) or down (1); at the end of the list it stays. */
export function moveRouter(draft: Draft, profileId: string, by: -1 | 1): Draft {
  return withList(draft, list => {
    const from = list.indexOf(profileId), to = from + by;
    if (from < 0 || to < 0 || to >= list.length) return list;
    const moved = [...list];
    [moved[from], moved[to]] = [moved[to], moved[from]];
    return moved;
  });
}

/** What the list needs the user to know: nobody can serve now, the first buddy is refused in the mode, or routing is failing. */
export type RouterNote = { kind: "nobody" } | { kind: "head"; profileId: string; refusal: RouterRefusal | null } | HealthWarning;

/**
 * The one note the list carries, in the order of what matters: no buddy of a
 * non-empty list can serve, then the first buddy being refused in the default
 * mode, then the warning of the recorded window. `refusal` is null once the
 * buddy is no longer in the catalogue.
 */
export function routerNote(source: Pick<Draft, "configuration" | "profiles">, health: RoutingHealth | undefined): RouterNote | null {
  const list = source.configuration?.routerProfileIds ?? [];
  if (list.length > 0 && currentRouterId(list, health) === null) return { kind: "nobody" };
  const head = routerAttention(source);
  if (head) {
    const profile = source.profiles.find(candidate => candidate.profileId === head.profileId);
    return { kind: "head", profileId: head.profileId, refusal: profile ? routerRefusalOf(profile, source.configuration!.defaultRoutingMode) : null };
  }
  return healthSummary(health).warning;
}

/** The counts of the window that are written out as names with a number; a count of nothing is left out. */
export const RECORD_COUNTS = ["consecutiveFailures", "abstention", "cancelled", "stale", "budgetExhausted", "boundsRejected", "inputChanged"] as const;
export type RecordCount = (typeof RECORD_COUNTS)[number];

export function recordCounts(health: RoutingHealth): { name: RecordCount; n: number }[] {
  const counts: Record<RecordCount, number | undefined> = {
    consecutiveFailures: health.consecutiveFailures,
    abstention: health.abstentionCount,
    cancelled: health.cancelledCount,
    stale: health.staleCount,
    budgetExhausted: health.budgetExhaustedCount,
    boundsRejected: health.boundsRejectedCount,
    inputChanged: health.inputChangedCount,
  };
  return RECORD_COUNTS.flatMap(name => (counts[name] ?? 0) > 0 ? [{ name, n: counts[name]! }] : []);
}
