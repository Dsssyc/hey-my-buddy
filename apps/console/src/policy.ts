import type { Draft, Profile } from "./types";
import {
  decisionProfileChanged,
  preferenceChanges,
  profileSettings,
} from "./draft";
import { concurrencyLimit, familyKey } from "./console-data";
import { profileTitle } from "./profile-display";

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

/** One actionable message about a user-owned setting. */
export type PolicyIssue = {
  profileId: string;
  label: string;
  message: string;
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
 * *transitions into* pin or a changed decision selector needs a currently legal
 * model; changing the reason of an existing pin, a disable and every other edit
 * stay independent of current availability. A family's concurrency limit is
 * equally independent of availability — it only has to be an integer in 1–32.
 */
export function blockingIssues(baseline: Draft, draft: Draft): PolicyIssue[] {
  const profiles = new Map(draft.profiles.map((p) => [p.profileId, p]));
  const baselinePreferenceModes = new Map(
    baseline.preferences.map((p) => [p.profileId, p.mode]),
  );
  const issues: PolicyIssue[] = [];
  for (const entry of draft.modelConcurrency) {
    if (concurrencyLimit(entry.limit) !== null) continue;
    const member = draft.profiles.find((profile) => familyKey(profile) === familyKey(entry));
    const subject = member ? profileTitle(member) : `${entry.adapter}/${entry.provider}/${entry.model}`;
    issues.push({
      profileId: member?.profileId ?? `${entry.adapter}/${entry.provider}/${entry.model}`,
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
  for (const change of preferenceChanges(baseline, draft)) {
    if (change.mode !== "pin") continue;
    // The board requires current legality only when the mode transitions into
    // pin; a reason-only edit of an existing pin stays a legal patch.
    if (baselinePreferenceModes.get(change.profileId) === "pin") continue;
    const profile = profiles.get(change.profileId);
    const refusal = pinRefusal(profile);
    if (refusal) {
      issues.push({
        profileId: change.profileId,
        label: labelOf(profile, change.profileId),
        message: `${labelOf(profile, change.profileId)} ${refusal}，不能设为固定选择。请改用其他可用且已启用的配置，或保留原设置。`,
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
          message: `路由配置 ${target} 已不在当前目录中，不能新设为路由模型。请改用其他配置。`,
        });
      } else if (!profile.available) {
        issues.push({
          profileId: target,
          label: labelOf(profile, target),
          message: `${labelOf(profile, target)} ${availabilityReason(profile)}，不能新设为路由模型。请改用其他配置。`,
        });
      } else if (!profile.enabled) {
        issues.push({
          profileId: target,
          label: labelOf(profile, target),
          message: `${labelOf(profile, target)} 处于停用状态，不能新设为路由模型。请先启用它，或选择其他可用配置。`,
        });
      } else if (!hasDecisionCapability(profile)) {
        issues.push({
          profileId: target,
          label: labelOf(profile, target),
          message: `${labelOf(profile, target)} 没有经过验证的路由能力，不能新设为路由模型。请选择经过验证支持只读结构化回合的配置。`,
        });
      }
    }
  }
  return issues;
}

/**
 * The fixed decision configuration is program-checked before every routing run.
 * A stale one is reported as needing attention, never as a reason to refuse an
 * unrelated user patch.
 */
export function decisionAttention(source: Draft): PolicyIssue | null {
  const decisionId = source.configuration.decisionProfileId;
  if (!decisionId) return null;
  const profile = source.profiles.find((p) => p.profileId === decisionId);
  if (!profile) {
    return {
      profileId: decisionId,
      label: decisionId,
      message: `当前路由配置 ${decisionId} 已不在目录中；需要处理，但不影响保存其他修改。`,
    };
  }
  if (!profile.available) {
    return {
      profileId: decisionId,
      label: labelOf(profile, decisionId),
      message: `当前路由配置 ${labelOf(profile, decisionId)} ${availabilityReason(profile)}；需要处理，但不影响保存其他修改。`,
    };
  }
  if (!profile.enabled) {
    return {
      profileId: decisionId,
      label: labelOf(profile, decisionId),
      message: `当前路由配置 ${labelOf(profile, decisionId)} 已停用，路由暂时无法使用它；不影响保存其他修改。`,
    };
  }
  if (!hasDecisionCapability(profile)) {
    return {
      profileId: decisionId,
      label: labelOf(profile, decisionId),
      message: `当前路由配置 ${labelOf(profile, decisionId)} 没有经过验证的路由能力；需要处理，但不影响保存其他修改。`,
    };
  }
  return null;
}

/**
 * Settings that stay stale after this draft is published. They are shown as
 * needing attention and never block an unrelated patch: a saved disable, a
 * preference change or an opinion still commits while these remain.
 */
export function attentionIssues(draft: Draft): PolicyIssue[] {
  const profiles = new Map(draft.profiles.map((p) => [p.profileId, p]));
  const issues: PolicyIssue[] = [];
  const decision = decisionAttention(draft);
  if (decision) issues.push(decision);
  for (const preference of draft.preferences) {
    if (preference.mode !== "pin") continue;
    const profile = profiles.get(preference.profileId);
    const refusal = pinRefusal(profile);
    if (refusal) {
      issues.push({
        profileId: preference.profileId,
        label: labelOf(profile, preference.profileId),
        message: `固定选择指向 ${labelOf(profile, preference.profileId)}，但它 ${refusal}；需要处理，但不影响保存其他修改。`,
      });
    }
  }
  return issues;
}
