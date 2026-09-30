import type { Card, ModelFamily, Preference, Profile, Snapshot, Task } from "./types";
import { effortText, profileName } from "./profile-display";

export const familyKey = (p: ModelFamily) => JSON.stringify([p.adapter, p.provider, p.model]);
/**
 * ADR-011 shared model concurrency: one user-owned concurrent-task limit per
 * exact adapter/provider/model family. All effort variants, routing decisions
 * and execution attempts of the family share the count; the default is 2 and
 * the accepted range is the integers 1–32.
 */
export const MODEL_CONCURRENCY_DEFAULT = 2;
export const MODEL_CONCURRENCY_MIN = 1;
export const MODEL_CONCURRENCY_MAX = 32;

/** The family's entry in a snapshot/page or draft list, or null when absent. */
export function concurrencyEntryFor<E extends ModelFamily>(
  entries: E[] | undefined,
  family: ModelFamily,
): E | null {
  return entries?.find((entry) => familyKey(entry) === familyKey(family)) ?? null;
}

/** Only an integer within 1–32 is a limit setting; anything else is not one. */
export function concurrencyLimit(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value)
    && value >= MODEL_CONCURRENCY_MIN && value <= MODEL_CONCURRENCY_MAX
    ? value
    : null;
}
export type ModelFamilyGroup = { key: string; name: string; adapter: string; provider: string; profiles: Profile[] };
export function modelFamilies(profiles: Profile[]): ModelFamilyGroup[] {
  const groups = new Map<string, ModelFamilyGroup>();
  for (const profile of profiles) {
    const key = familyKey(profile);
    if (!groups.has(key)) groups.set(key, { key, name: profileName(profile), adapter: profile.adapter, provider: profile.provider, profiles: [] });
    groups.get(key)!.profiles.push(profile);
  }
  // Display order only; default/off modes do not imply a capability score.
  const levels = ["default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"];
  for (const group of groups.values()) group.profiles.sort((a, b) => {
    const rank = (value: string) => levels.includes(value) ? levels.indexOf(value) : levels.length;
    return rank(a.effort) - rank(b.effort) || a.effort.localeCompare(b.effort);
  });
  return [...groups.values()].sort((a, b) => a.adapter.localeCompare(b.adapter)
    || Number(b.profiles.some(p => p.enabled)) - Number(a.profiles.some(p => p.enabled))
    || a.name.localeCompare(b.name) || a.key.localeCompare(b.key));
}
export function preferredVariant(family: ModelFamilyGroup, preferences: Preference[]) {
  // An unavailable variant never pre-empts an available one for the initial view.
  const available = family.profiles.filter(p => p.available);
  const pool = available.length ? available : family.profiles;
  return pool.find(p => p.enabled && preferences.some(x => x.profileId === p.profileId && ["pin", "prefer"].includes(x.mode)))
    || pool.find(p => p.enabled) || pool[0];
}
export function taskProject(task: Task) {
  return task.delegation?.project || { id: "unknown", label: "未记录项目", path: null };
}
export function taskHost(task: Task) {
  return task.delegation?.sourceHostId || "未记录委派方";
}
export function taskExecutor(task: Task) {
  const config = task.delegation?.configuration;
  return config ? `${config.adapter} · ${config.model} / ${effortText(config.effort)}` : "执行配置待确定";
}

/**
 * Recorded verification samples for one profile, read straight from the current
 * snapshot contract so a count still shows before any card prose is published.
 * The optional access only keeps a board that has not shipped `sampleCounts` yet
 * from blanking the page; the legacy per-card field is never read.
 */
export function recordedSampleCount(
  snapshot: { sampleCounts?: Record<string, number> },
  profileId: string,
): number {
  const recorded = snapshot.sampleCounts?.[profileId];
  return typeof recorded === "number" && Number.isFinite(recorded) ? recorded : 0;
}

/** True only when the card records a maintenance publication. */
export function isMaintenanceCard(card: Pick<Card, "origin"> | undefined): boolean {
  return !!card && (card.origin ?? "").trim().toLowerCase() === "maintenance";
}

/**
 * Recorded card authorship. An unattributed or absent origin is unknown
 * history, not evidence that a maintenance Harness assessed it.
 */
export function cardOriginText(card: Pick<Card, "origin"> | undefined): string {
  if (isMaintenanceCard(card)) return "由维护 Harness 依据证据发布";
  return "发布者未记录（历史卡片，不能判断为自动评价）";
}
