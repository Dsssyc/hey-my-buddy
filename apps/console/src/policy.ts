import type { Draft, Profile } from "./types";
import {
  routerFieldChanged,
  effectivePreferences,
  familyPreferenceChanges,
  preferenceChanges,
  profileSettings,
} from "./draft";
import { concurrencyLimit, familyKey } from "./console-data";
import { profileName, profileTitle } from "./profile-display";

/** The two capabilities have distinct authority and cannot stand in for each other. */
export function hasFastRoutingCapability(profile: Profile | undefined): boolean {
  return !!profile && (profile.capabilities ?? []).some(entry => entry.trim().toLowerCase() === "routing:fast");
}

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

export function isFastRouterCandidate(profile: Profile | undefined): boolean {
  return !!profile && profile.enabled && profile.available && hasFastRoutingCapability(profile);
}

export function fastRouterCandidates(profiles: Profile[]): Profile[] {
  return profiles.filter(isFastRouterCandidate)
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
export function routerRefusal(profile: Profile, mode: "fast" | "review" = "review"): string | null {
  if (mode === "fast" && !hasFastRoutingCapability(profile)) return "所属 Harness 尚不支持无工具路由调用";
  if (mode === "review" && !hasDecisionCapability(profile)) return "所属 Harness 尚不具备本地只读路由资格";
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
      message: `${subject} 并发上限须为 1–32 的整数；请修改。`,
    });
  }
  for (const setting of profileSettings(baseline, draft)) {
    if (!setting.enabled) continue;
    const profile = profiles.get(setting.profileId);
    if (!profile?.available) {
      issues.push({
        profileId: setting.profileId,
        label: labelOf(profile, setting.profileId),
        message: `${labelOf(profile, setting.profileId)} ${availabilityReason(profile)}；无法启用，请撤销修改。`,
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
        message: `${subject} 没有可用档位；请先启用档位再固定。`,
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
        message: `${labelOf(profile, change.profileId)} ${refusal}；无法固定，请选择可用档位。`,
      });
    }
  }
  if (draft.configuration && (routerFieldChanged(baseline, draft, "routerProfileId")
    || routerFieldChanged(baseline, draft, "defaultRoutingMode"))) {
    const target = draft.configuration.routerProfileId;
    const mode = draft.configuration.defaultRoutingMode;
    if (target) {
      const profile = profiles.get(target);
      const refusal = profile ? routerRefusal(profile, mode) : "已不在当前目录中";
      if (refusal) issues.push({ profileId: target, label: labelOf(profile, target),
        message: `${labelOf(profile, target)} ${refusal}；无法担任 Router，请换档位或模式。` });
    }
  }
  return issues;
}

/** The Router is checked again by the service before each routing run. */
export function routerAttention(source: Pick<Draft, "configuration" | "profiles">, mode = source.configuration?.defaultRoutingMode): PolicyIssue | null {
  const id = source.configuration?.routerProfileId;
  if (!id || !mode) return null;
  const profile = source.profiles.find(p => p.profileId === id);
  const refusal = profile ? routerRefusal(profile, mode) : "已不在目录中";
  return refusal ? { profileId: id, router: true, label: labelOf(profile, id),
    message: `当前 Router ${labelOf(profile, id)} ${refusal}：请在合格的档位菜单中选择“设为 Router”，或更改模式。` } : null;
}

export function decisionAttention(source: Pick<Draft, "configuration" | "profiles">): PolicyIssue | null {
  return routerAttention(source);
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
  const attention = routerAttention(source);
  if (attention) issues.push(attention);
  const effective = effectivePreferences(source);
  for (const override of source.preferenceOverrides) {
    if (override.mode !== "pin") continue;
    const profile = profiles.get(override.profileId);
    const refusal = pinRefusal(profile);
    if (refusal) {
      issues.push({
        profileId: override.profileId,
        label: labelOf(profile, override.profileId),
        message: `固定选择指向 ${labelOf(profile, override.profileId)}：${refusal}；请启用或改回“跟随家族”。`,
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
      message: `家族 ${subject} 的固定档位不可用；请启用档位或更改家族偏好。`,
    });
  }
  return issues;
}
