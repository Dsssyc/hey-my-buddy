import type { Card, Preference, Profile, Snapshot, Task } from "./types";
import { effortText, profileName } from "./profile-display";

export const familyKey = (p: Profile) => JSON.stringify([p.adapter, p.provider, p.model]);
export type ModelFamily = { key: string; name: string; adapter: string; provider: string; profiles: Profile[] };
export function modelFamilies(profiles: Profile[]): ModelFamily[] {
  const groups = new Map<string, ModelFamily>();
  for (const profile of profiles) {
    const key = familyKey(profile);
    if (!groups.has(key)) groups.set(key, { key, name: profileName(profile), adapter: profile.adapter, provider: profile.provider, profiles: [] });
    groups.get(key)!.profiles.push(profile);
  }
  const levels = ["off", "low", "medium", "high", "max"];
  for (const group of groups.values()) group.profiles.sort((a, b) => {
    const rank = (value: string) => levels.includes(value) ? levels.indexOf(value) : levels.length;
    return rank(a.effort) - rank(b.effort) || a.effort.localeCompare(b.effort);
  });
  return [...groups.values()].sort((a, b) => a.adapter.localeCompare(b.adapter)
    || Number(b.profiles.some(p => p.enabled)) - Number(a.profiles.some(p => p.enabled))
    || a.name.localeCompare(b.name) || a.key.localeCompare(b.key));
}
export function preferredVariant(family: ModelFamily, preferences: Preference[]) {
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
