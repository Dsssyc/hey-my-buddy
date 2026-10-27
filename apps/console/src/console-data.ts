import type { ModelService, ModelServiceConcurrencyEntry, Preference, Buddy, Task } from "./types";
import { catalogPending, buddyName } from "./buddy-name";
import i18n from "i18next";
import { actorText } from "./actor";

export const modelServiceKey = (p: ModelService) => JSON.stringify([p.harness, p.provider, p.model]);

/**
 * The model service's aggregated pending fact (ADR-027 §6): pending is a model-identity
 * state, so its efforts share one mark. Pending when any effort is pending; the
 * earliest recorded first-absence time rides along, and a pending fact without
 * one stays pending with a null time rather than dropping the state.
 */
export function modelServicePending(buddies: Buddy[]): { pending: boolean; since: string | null } {
  const pendingBuddies = buddies.filter(catalogPending);
  if (!pendingBuddies.length) return { pending: false, since: null };
  const times = pendingBuddies.map(p => p.pendingSince)
    .filter((t): t is string => typeof t === "string" && t !== "")
    .sort();
  return { pending: true, since: times.length ? times[0] : null };
}
/**
 * ADR-011 shared model service concurrency: one user-owned concurrent-task
 * limit per exact harness/provider/model tuple. All effort variants, routing
 * decisions and execution attempts of the model service share the count; the default is 2 and
 * the accepted range is positive JavaScript safe integers.
 */
export const MODEL_SERVICE_CONCURRENCY_DEFAULT = 2;
export const MODEL_SERVICE_CONCURRENCY_MIN = 1;
export const MODEL_SERVICE_CONCURRENCY_MAX = Number.MAX_SAFE_INTEGER;

/** The model service's entry in a snapshot/page or draft list, or null when absent. */
export function concurrencyEntryFor<E extends ModelService>(
  entries: E[] | undefined,
  modelService: ModelService,
): E | null {
  return entries?.find((entry) => modelServiceKey(entry) === modelServiceKey(modelService)) ?? null;
}

/** Only a positive safe integer is a limit setting; anything else is not one. */
export function concurrencyLimit(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value)
    && value >= MODEL_SERVICE_CONCURRENCY_MIN && value <= MODEL_SERVICE_CONCURRENCY_MAX
    ? value
    : null;
}

/** The read boundary checks a limit and its observed occupancy before display. */
export function isModelServiceConcurrencyEntry(value: unknown): value is ModelServiceConcurrencyEntry {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return ["harness", "provider", "model"].every(key => typeof entry[key] === "string" && entry[key] !== "")
    && concurrencyLimit(entry.limit) !== null
    && typeof entry.active === "number" && Number.isSafeInteger(entry.active) && entry.active >= 0;
}
/**
 * The Router retry interval in seconds: a plain number input, so there are no
 * product tiers beyond the 10-minute default. Only a strict integer within
 * 1–2147483647 is a setting (the bound keeps the value representable in time
 * math and storage); anything else is not one.
 */
export const ROUTER_RETRY_INTERVAL_MIN = 1;
export const ROUTER_RETRY_INTERVAL_MAX = 2147483647;

export function retryIntervalSeconds(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value)
    && value >= ROUTER_RETRY_INTERVAL_MIN && value <= ROUTER_RETRY_INTERVAL_MAX
    ? value
    : null;
}
export type ModelServiceGroup = { key: string; name: string; harness: string; provider: string; buddies: Buddy[] };
export function modelServices(buddies: Buddy[]): ModelServiceGroup[] {
  const groups = new Map<string, ModelServiceGroup>();
  for (const buddy of buddies) {
    const key = modelServiceKey(buddy);
    if (!groups.has(key)) groups.set(key, { key, name: buddyName(buddy), harness: buddy.harness, provider: buddy.provider, buddies: [] });
    groups.get(key)!.buddies.push(buddy);
  }
  // Display order only: each harness's current catalog carries its own order.
  for (const group of groups.values()) group.buddies.sort((a, b) => {
    const rank = (value: number | null) => value ?? Number.MAX_SAFE_INTEGER;
    return rank(a.effortRank) - rank(b.effortRank) || a.effort.localeCompare(b.effort);
  });
  return [...groups.values()].sort((a, b) => a.harness.localeCompare(b.harness)
    || Number(b.buddies.some(p => p.enabled)) - Number(a.buddies.some(p => p.enabled))
    || a.name.localeCompare(b.name) || a.key.localeCompare(b.key));
}
export function preferredVariant(modelService: ModelServiceGroup, preferences: Preference[]) {
  // An unavailable variant never pre-empts an available one for the initial view.
  const available = modelService.buddies.filter(p => p.available);
  const pool = available.length ? available : modelService.buddies;
  return pool.find(p => p.enabled && preferences.some(x => x.buddyId === p.buddyId && ["pin", "prefer"].includes(x.mode)))
    || pool.find(p => p.enabled) || pool[0];
}
export function taskProject(task: Task) {
  return task.lineage?.project ?? null;
}
/** Recorded source attribution survives takeover; current ownership is separate. */
export function taskHost(task: Task) {
  if (task.lineage?.sourceHostId) return task.lineage.sourceHostId;
  const lead = task.lineage?.leadOwnership?.leadRunId ?? task.lineage?.rootLeadRunId;
  return lead ? actorText({ role: "lead", id: lead }) : i18n.t("shell:actor.unrecordedDelegator");
}
