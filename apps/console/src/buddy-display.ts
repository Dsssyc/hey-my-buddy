import type { ModelServiceGroup } from "./console-data";
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
 * Case-insensitive search text for one model service entry (docs/design/buddy-settings.md):
 * the model's display name, its recorded label/model and effort, the provider,
 * and both the adapter id and its harness display name, so "claude code" finds
 * the `claude` harness exactly like "claude" does.
 */
export function modelServiceSearchText(profile: Profile): string {
  return [
    profileName(profile), profile.label, profile.model, profile.effort,
    profile.provider, profile.adapter, harnessName(profile.adapter), effortText(profile.effort),
  ].join(" ").toLowerCase();
}

export type HarnessGroup = {
  adapter: string;
  name: string;
  modelServices: ModelServiceGroup[];
  /** No profile of this harness is available in the directory right now. */
  unavailable: boolean;
  /** Recorded reasons, deduplicated; empty when none were recorded. */
  reasons: string[];
};

/** Groups model services by harness, keeping the model service order of `modelServices`. */
export function harnessGroups(modelServices: ModelServiceGroup[], all: Profile[]): HarnessGroup[] {
  const groups = new Map<string, HarnessGroup>();
  for (const modelService of modelServices) {
    let group = groups.get(modelService.adapter);
    if (!group) {
      const members = all.filter((profile) => profile.adapter === modelService.adapter);
      const unavailable = !members.some((profile) => profile.available);
      const reasons = unavailable
        ? [...new Set(members.map((profile) => profile.unavailableReason?.trim() ?? "").filter(Boolean))]
        : [];
      group = { adapter: modelService.adapter, name: harnessName(modelService.adapter), modelServices: [], unavailable, reasons };
      groups.set(modelService.adapter, group);
    }
    group.modelServices.push(modelService);
  }
  return [...groups.values()];
}
