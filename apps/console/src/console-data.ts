import type { ModelService, Preference, Buddy, Task } from "./types";
import { catalogPending, effortText, buddyName } from "./buddy-name";
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
 * the accepted range is the integers 1–32.
 */
export const MODEL_SERVICE_CONCURRENCY_DEFAULT = 2;
export const MODEL_SERVICE_CONCURRENCY_MIN = 1;
export const MODEL_SERVICE_CONCURRENCY_MAX = 32;

/** The model service's entry in a snapshot/page or draft list, or null when absent. */
export function concurrencyEntryFor<E extends ModelService>(
  entries: E[] | undefined,
  modelService: ModelService,
): E | null {
  return entries?.find((entry) => modelServiceKey(entry) === modelServiceKey(modelService)) ?? null;
}

/** Only an integer within 1–32 is a limit setting; anything else is not one. */
export function concurrencyLimit(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value)
    && value >= MODEL_SERVICE_CONCURRENCY_MIN && value <= MODEL_SERVICE_CONCURRENCY_MAX
    ? value
    : null;
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
  // Display order only; default/off modes do not imply a capability score.
  const levels = ["default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"];
  for (const group of groups.values()) group.buddies.sort((a, b) => {
    const rank = (value: string) => levels.includes(value) ? levels.indexOf(value) : levels.length;
    return rank(a.effort) - rank(b.effort) || a.effort.localeCompare(b.effort);
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
  return task.lineage?.project || { id: "unknown", label: "未记录项目", path: null };
}
/** Recorded source attribution survives takeover; current ownership is separate. */
export function taskHost(task: Task) {
  if (task.lineage?.sourceHostId) return task.lineage.sourceHostId;
  const lead = task.lineage?.leadOwnership?.leadRunId ?? task.lineage?.rootLeadRunId;
  return lead ? actorText({ role: "lead", id: lead }) : i18n.t("shell:actor.unrecordedDelegator");
}
export function taskExecutor(task: Task) {
  if (task.spec?.adapter === "command") return null;
  const buddy = task.lineage?.buddy;
  return buddy ? `${buddy.harness} · ${buddy.model} / ${effortText(buddy.effort)}` : "执行 buddy 待确定";
}
