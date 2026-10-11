import type { ModelFamilyGroup } from "./console-data";
import { effortText, profileName } from "./profile-display";
import type { Profile } from "./types";

/** Product names for the known harness ids; an unknown id is shown as recorded. */
const HARNESS_NAMES: Record<string, string> = {
  claude: "Claude Code",
  codex: "Codex",
  dsh: "DSH",
  zcode: "ZCode",
};

export function harnessName(adapter: string): string {
  return HARNESS_NAMES[adapter.trim().toLowerCase()] ?? adapter;
}

/**
 * Case-insensitive search text for one family entry (docs/design/buddy-settings.md):
 * the model's display name, its recorded label/model and effort, the provider,
 * and both the adapter id and its harness display name, so "claude code" finds
 * the `claude` harness exactly like "claude" does.
 */
export function familySearchText(profile: Profile): string {
  return [
    profileName(profile), profile.label, profile.model, profile.effort,
    profile.provider, profile.adapter, harnessName(profile.adapter), effortText(profile.effort),
  ].join(" ").toLowerCase();
}

export type HarnessGroup = {
  adapter: string;
  name: string;
  families: ModelFamilyGroup[];
  /** No profile of this harness is available in the directory right now. */
  unavailable: boolean;
  /** Recorded reasons, deduplicated; empty when none were recorded. */
  reasons: string[];
};

/** Groups families by harness, keeping the family order of `modelFamilies`. */
export function harnessGroups(families: ModelFamilyGroup[], all: Profile[]): HarnessGroup[] {
  const groups = new Map<string, HarnessGroup>();
  for (const family of families) {
    let group = groups.get(family.adapter);
    if (!group) {
      const members = all.filter((profile) => profile.adapter === family.adapter);
      const unavailable = !members.some((profile) => profile.available);
      const reasons = unavailable
        ? [...new Set(members.map((profile) => profile.unavailableReason?.trim() ?? "").filter(Boolean))]
        : [];
      group = { adapter: family.adapter, name: harnessName(family.adapter), families: [], unavailable, reasons };
      groups.set(family.adapter, group);
    }
    group.families.push(family);
  }
  return [...groups.values()];
}
