import type { Profile, Preference, Task } from "./types";
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
  return family.profiles.find(p => p.enabled && preferences.some(x => x.profileId === p.profileId && ["pin", "prefer"].includes(x.mode)))
    || family.profiles.find(p => p.enabled) || family.profiles[0];
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
