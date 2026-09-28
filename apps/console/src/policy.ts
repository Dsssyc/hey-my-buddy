import type { Draft, Profile } from "./types";
import {
  decisionProfileChanged,
  effectivePreferences,
  familyPreferenceChanges,
  preferenceChanges,
  profileSettings,
} from "./draft";
import { concurrencyLimit, familyKey } from "./console-data";
import { profileName, profileTitle } from "./profile-display";

/**
 * Host populates each profile's `capabilities`; the only recorded decision token
 * is `decision`. Coding ability, adapter names and invented aliases never imply
 * a decision capability.
 */
export function hasDecisionCapability(profile: Profile | undefined): boolean {
  if (!profile) return false;
  return (profile.capabilities ?? []).some(
    (entry) => entry.trim().toLowerCase() === "decision",
  );
}

/** Enabled, currently available and actually able to compute a selection. */
export function isDecisionCandidate(profile: Profile | undefined): boolean {
  return !!profile && profile.enabled && profile.available && hasDecisionCapability(profile);
}

export function decisionCandidates(profiles: Profile[]): Profile[] {
  // Deterministic, locale-independent order: harness, then the stable identity.
  return profiles
    .filter(isDecisionCandidate)
    .sort((a, b) => a.adapter.localeCompare(b.adapter) || a.profileId.localeCompare(b.profileId));
}

/**
 * The published directory capability for a harness that can actually execute a
 * delegated helper in a workspace. `catalog.proposed_profiles` records exactly
 * `execution:<adapter>` for every executable adapter, so a newly registered
 * adapter (such as Codex) is admitted without a console-side harness list, and
 * a decision-only or mismatched row is never offered as an executor.
 */
export function hasExecutionCapability(profile: Profile | undefined): boolean {
  if (!profile) return false;
  const token = `execution:${profile.adapter.trim().toLowerCase()}`;
  return (profile.capabilities ?? []).some(
    (entry) => entry.trim().toLowerCase() === token,
  );
}

/** Enabled, currently available and able to execute a delegated helper task. */
export function isExecutionCandidate(profile: Profile | undefined): boolean {
  return !!profile && profile.enabled && profile.available && hasExecutionCapability(profile);
}

export function executionCandidates(profiles: Profile[]): Profile[] {
  return profiles
    .filter(isExecutionCandidate)
    .sort((a, b) => a.adapter.localeCompare(b.adapter) || a.profileId.localeCompare(b.profileId));
}

/**
 * Why this effort cannot be made the Router from its tag menu; null when it
 * can. The capability list is checked first because it is the one reason the
 * user cannot fix here.
 */
export function routerRefusal(profile: Profile): string | null {
  if (!hasDecisionCapability(profile)) return "能力列表不含 decision：该档位没有经过验证的只读路由能力";
  if (!profile.available) return "该档位当前不可用";
  if (!profile.enabled) return "该档位未启用：先打开它的开关";
  return null;
}

/** One actionable message about a user-owned setting. */
export type PolicyIssue = {
  profileId: string;
  label: string;
  message: string;
  /** Family key for a family-level issue; profile-level issues leave it unset. */
  family?: string;
  /** The Router itself needs attention; the page's routing status shows it. */
  router?: boolean;
};

function labelOf(profile: Profile | undefined, profileId: string): string {
  return profile ? profileTitle(profile) : profileId;
}

function availabilityReason(profile: Profile | undefined): string {
  if (!profile) return "已不在当前目录中";
  return profile.unavailableReason?.trim()
    ? `当前不可用（${profile.unavailableReason.trim()}）`
    : "当前不在目录中";
}

/**
 * Why a pin is not legal right now, matching the board: a new pin requires an
 * available *and* enabled configuration. Null means it is legal.
 */
function pinRefusal(profile: Profile | undefined): string | null {
  if (!profile || !profile.available) return availabilityReason(profile);
  if (!profile.enabled) return "处于停用状态";
  return null;
}

/**
 * Changes that cannot be published at all. Only a *new* enable, a mode that
 * *transitions into* pin (a family default or an effort override) or a changed
 * Router needs a currently legal model; changing the reason of an existing pin,
 * a disable and every other edit stay independent of current availability. A
 * family's concurrency limit is equally independent of availability — it only
 * has to be an integer in 1–32.
 */
export function blockingIssues(baseline: Draft, draft: Draft): PolicyIssue[] {
  const profiles = new Map(draft.profiles.map((p) => [p.profileId, p]));
  const baselineOverrideModes = new Map(
    baseline.preferenceOverrides.map((p) => [p.profileId, p.mode]),
  );
  const issues: PolicyIssue[] = [];
  for (const entry of draft.modelConcurrency) {
    if (concurrencyLimit(entry.limit) !== null) continue;
    const member = draft.profiles.find((profile) => familyKey(profile) === familyKey(entry));
    const subject = member ? profileName(member) : `${entry.adapter}/${entry.provider}/${entry.model}`;
    issues.push({
      profileId: member?.profileId ?? `${entry.adapter}/${entry.provider}/${entry.model}`,
      family: familyKey(entry),
      label: subject,
      message: `${subject} 的并发上限必须是 1–32 的整数，请改正这项修改后再保存。`,
    });
  }
  for (const setting of profileSettings(baseline, draft)) {
    if (!setting.enabled) continue;
    const profile = profiles.get(setting.profileId);
    if (!profile?.available) {
      issues.push({
        profileId: setting.profileId,
        label: labelOf(profile, setting.profileId),
        message: `${labelOf(profile, setting.profileId)} ${availabilityReason(profile)}，不能新启用。请先撤销这项修改。`,
      });
    }
  }
  const effective = effectivePreferences(draft);
  const baselineFamilyModes = new Map(baseline.familyPreferences.map((p) => [familyKey(p), p.mode]));
  for (const change of familyPreferenceChanges(baseline, draft)) {
    if (change.mode !== "pin") continue;
    const key = familyKey(change);
    if (baselineFamilyModes.get(key) === "pin") continue;
    // A new family pin needs at least one effort that is pinned through it and
    // can actually be selected: available and enabled.
    const members = draft.profiles.filter((profile) => familyKey(profile) === key);
    const legal = members.some((profile) => !pinRefusal(profile)
      && effective.some((p) => p.profileId === profile.profileId && p.mode === "pin"));
    if (!legal) {
      const subject = members[0] ? profileName(members[0]) : `${change.adapter}/${change.provider}/${change.model}`;
      issues.push({
        profileId: members[0]?.profileId ?? key,
        family: key,
        label: subject,
        message: `${subject} 没有可用且已启用的档位，不能把整个家族设为固定。请先启用一个档位，或保留原设置。`,
      });
    }
  }
  for (const change of preferenceChanges(baseline, draft)) {
    if (change.mode !== "pin") continue;
    // The board requires current legality only when the mode transitions into
    // pin; a reason-only edit of an existing pin stays a legal patch.
    if (baselineOverrideModes.get(change.profileId) === "pin") continue;
    const profile = profiles.get(change.profileId);
    const refusal = pinRefusal(profile);
    if (refusal) {
      issues.push({
        profileId: change.profileId,
        label: labelOf(profile, change.profileId),
        message: `${labelOf(profile, change.profileId)} ${refusal}，不能设为固定选择。请改用其他可用且已启用的档位，或保留原设置。`,
      });
    }
  }
  if (decisionProfileChanged(baseline, draft)) {
    const target = draft.configuration.decisionProfileId;
    if (target) {
      const profile = profiles.get(target);
      if (!profile) {
        issues.push({
          profileId: target,
          label: target,
          message: `配置 ${target} 已不在当前目录中，不能新设为 Router。请改用其他档位。`,
        });
      } else if (!profile.available) {
        issues.push({
          profileId: target,
          label: labelOf(profile, target),
          message: `${labelOf(profile, target)} ${availabilityReason(profile)}，不能新设为 Router。请改用其他档位。`,
        });
      } else if (!profile.enabled) {
        issues.push({
          profileId: target,
          label: labelOf(profile, target),
          message: `${labelOf(profile, target)} 处于停用状态，不能新设为 Router。请先启用它，或选择其他档位。`,
        });
      } else if (!hasDecisionCapability(profile)) {
        issues.push({
          profileId: target,
          label: labelOf(profile, target),
          message: `${labelOf(profile, target)} 没有经过验证的路由能力，不能新设为 Router。请选择能力列表含 decision 的档位。`,
        });
      }
    }
  }
  return issues;
}

/** The action every stale-Router warning offers. */
const ROUTER_ACTION = "在另一个具备 decision 能力的档位菜单中选择“设为 Router”";

/**
 * The Router is program-checked before every routing run. A stale one is
 * reported as needing attention, with the action that resolves it, never as a
 * reason to refuse an unrelated user patch.
 */
export function decisionAttention(source: Pick<Draft, "configuration" | "profiles">): PolicyIssue | null {
  const decisionId = source.configuration.decisionProfileId;
  if (!decisionId) return null;
  const profile = source.profiles.find((p) => p.profileId === decisionId);
  if (!profile) {
    return {
      profileId: decisionId,
      router: true,
      label: decisionId,
      message: `当前 Router ${decisionId} 已不在目录中：请${ROUTER_ACTION}。`,
    };
  }
  if (!profile.available) {
    return {
      profileId: decisionId,
      router: true,
      label: labelOf(profile, decisionId),
      message: `当前 Router ${labelOf(profile, decisionId)} ${availabilityReason(profile)}：请${ROUTER_ACTION}。`,
    };
  }
  if (!profile.enabled) {
    return {
      profileId: decisionId,
      router: true,
      label: labelOf(profile, decisionId),
      message: `当前 Router ${labelOf(profile, decisionId)} 已停用：请重新启用该档位，或${ROUTER_ACTION}。`,
    };
  }
  if (!hasDecisionCapability(profile)) {
    return {
      profileId: decisionId,
      router: true,
      label: labelOf(profile, decisionId),
      message: `当前 Router ${labelOf(profile, decisionId)} 未验证路由能力：请${ROUTER_ACTION}。`,
    };
  }
  return null;
}

/**
 * Settings that stay stale after this draft is published. They are shown as
 * needing attention and never block an unrelated patch: a saved disable, a
 * preference change or a note still commits while these remain. An override
 * pin is checked per effort; a family pin only needs attention when none of
 * the efforts it pins can be selected.
 */
export function attentionIssues(
  source: Pick<Draft, "configuration" | "profiles" | "familyPreferences" | "preferenceOverrides">,
): PolicyIssue[] {
  const profiles = new Map(source.profiles.map((p) => [p.profileId, p]));
  const issues: PolicyIssue[] = [];
  const decision = decisionAttention(source);
  if (decision) issues.push(decision);
  const effective = effectivePreferences(source);
  for (const override of source.preferenceOverrides) {
    if (override.mode !== "pin") continue;
    const profile = profiles.get(override.profileId);
    const refusal = pinRefusal(profile);
    if (refusal) {
      issues.push({
        profileId: override.profileId,
        label: labelOf(profile, override.profileId),
        message: `固定选择指向 ${labelOf(profile, override.profileId)}，但它${refusal}：请启用该档位，或把它的偏好改回“跟随家族”。`,
      });
    }
  }
  for (const family of source.familyPreferences) {
    if (family.mode !== "pin") continue;
    const key = familyKey(family);
    const pinned = source.profiles.filter((profile) => familyKey(profile) === key
      && effective.some((p) => p.profileId === profile.profileId && p.mode === "pin" && p.source === "family"));
    if (!pinned.length || pinned.some((profile) => !pinRefusal(profile))) continue;
    const subject = profileName(pinned[0]);
    issues.push({
      profileId: pinned[0].profileId,
      family: key,
      label: subject,
      message: `家族 ${subject} 设为固定，但它固定的档位都不可用或未启用：请启用一个档位，或更改家族偏好。`,
    });
  }
  return issues;
}
