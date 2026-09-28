import type { ModelFamilyGroup } from "./console-data";
import type { OverrideMode, PreferenceMode, Profile } from "./types";

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
 * Each preference has its own colour class *and* a glyph and border style, so
 * the tag never relies on colour alone (the timeline's accessibility rule).
 */
export const PREFERENCE_LABEL: Record<PreferenceMode, string> = {
  prefer: "优先",
  pin: "固定",
  exclude: "排除",
};
export const PREFERENCE_ICON: Record<PreferenceMode, string> = {
  prefer: "▲",
  pin: "◆",
  exclude: "⊘",
};
export const OVERRIDE_LABEL: Record<OverrideMode, string> = {
  ...PREFERENCE_LABEL,
  none: "无偏好",
};

/** Help text for the preference modes, shared by the family field and the tag menu. */
export const PREFERENCE_HELP = "优先：同等条件下先考虑。固定：只要有任何生效的固定，候选就只限于生效为固定的档位。排除：不作为候选。偏好影响后续选择，不改变运行中的任务。";

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

/** Why a harness is folded away: its recorded reasons, or a plain fallback. */
export function harnessUnavailableText(group: HarnessGroup): string {
  return group.reasons.length ? group.reasons.join("；") : "目录中当前没有可用的配置";
}
