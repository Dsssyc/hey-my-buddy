import type { Draft, Buddy } from "./types";
import {
  routerFieldChanged,
  preferenceChanges,
  buddySettings,
} from "./draft";
import { concurrencyLimit, modelServiceKey, retryIntervalSeconds } from "./console-data";
import { buddyName, buddyTitle } from "./buddy-name";

/** The two capabilities have distinct authority and cannot stand in for each other. */
export function hasFastRoutingCapability(buddy: Buddy | undefined): boolean {
  return !!buddy && (buddy.capabilities ?? []).some(entry => entry.trim().toLowerCase() === "routing:fast");
}

export function hasDecisionCapability(buddy: Buddy | undefined): boolean {
  if (!buddy) return false;
  return (buddy.capabilities ?? []).some(
    (entry) => entry.trim().toLowerCase() === "decision",
  );
}

/** Enabled, currently available and actually able to compute a selection. */
export function isDecisionCandidate(buddy: Buddy | undefined): boolean {
  return !!buddy && buddy.enabled && buddy.available && hasDecisionCapability(buddy);
}

export function decisionCandidates(buddies: Buddy[]): Buddy[] {
  // Deterministic, locale-independent order: harness, then the stable identity.
  return buddies
    .filter(isDecisionCandidate)
    .sort((a, b) => a.harness.localeCompare(b.harness) || a.buddyId.localeCompare(b.buddyId));
}

export function isFastRouterCandidate(buddy: Buddy | undefined): boolean {
  return !!buddy && buddy.enabled && buddy.available && hasFastRoutingCapability(buddy);
}

export function fastRouterCandidates(buddies: Buddy[]): Buddy[] {
  return buddies.filter(isFastRouterCandidate)
    .sort((a, b) => a.harness.localeCompare(b.harness) || a.buddyId.localeCompare(b.buddyId));
}

/**
 * The published directory capability for a harness that can actually execute a
 * delegated helper in a workspace. `catalog.proposed_buddies` records exactly
 * `execution:<harness>` for every executable harness, so a newly registered
 * harness (such as Codex) is admitted without a console-side harness list, and
 * a decision-only or mismatched row is never offered as an executor.
 */
export function hasExecutionCapability(buddy: Buddy | undefined): boolean {
  if (!buddy) return false;
  const token = `execution:${buddy.harness.trim().toLowerCase()}`;
  return (buddy.capabilities ?? []).some(
    (entry) => entry.trim().toLowerCase() === token,
  );
}

/** Enabled, currently available and able to execute a delegated helper task. */
export function isExecutionCandidate(buddy: Buddy | undefined): boolean {
  return !!buddy && buddy.enabled && buddy.available && hasExecutionCapability(buddy);
}

export function executionCandidates(buddies: Buddy[]): Buddy[] {
  return buddies
    .filter(isExecutionCandidate)
    .sort((a, b) => a.harness.localeCompare(b.harness) || a.buddyId.localeCompare(b.buddyId));
}

/**
 * Why this effort cannot be made the Router from its tag menu; null when it
 * can. The capability list is checked first because it is the one reason the
 * user cannot fix here.
 */
export function routerRefusal(buddy: Buddy, mode: "fast" | "review" = "review"): string | null {
  if (mode === "fast" && !hasFastRoutingCapability(buddy)) return "所属 Harness 尚不支持无工具路由调用";
  if (mode === "review" && !hasDecisionCapability(buddy)) return "所属 Harness 尚不具备本地只读路由资格";
  if (!buddy.available) return "该推理强度当前不可用";
  if (!buddy.enabled) return "该推理强度未启用：先打开它的开关";
  return null;
}

/** One actionable message about a user-owned setting. */
export type PolicyIssue = {
  buddyId: string;
  label: string;
  message: string;
  /** Model service key for a model service issue; buddy-level issues leave it unset. */
  modelService?: string;
  /** The Router itself needs attention; the page's routing status shows it. */
  router?: boolean;
};

function labelOf(buddy: Buddy | undefined, buddyId: string): string {
  return buddy ? buddyTitle(buddy) : buddyId;
}

function availabilityReason(buddy: Buddy | undefined): string {
  if (!buddy) return "已不在当前目录中";
  return buddy.unavailableReason?.trim()
    ? `当前不可用（${buddy.unavailableReason.trim()}）`
    : "当前不在目录中";
}

/**
 * Why a pin is not legal right now, matching the board: a new pin requires an
 * available *and* enabled buddy. Null means it is legal.
 */
function pinRefusal(buddy: Buddy | undefined): string | null {
  if (!buddy || !buddy.available) return availabilityReason(buddy);
  if (!buddy.enabled) return "处于停用状态";
  return null;
}

/**
 * Changes that cannot be published at all. Only a *new* enable, a mode that
 * *transitions into* pin or a changed
 * Router needs a currently legal model; changing the reason of an existing pin,
 * a disable and every other edit stay independent of current availability. A
 * model service's concurrency limit is equally independent of availability — it only
 * has to be an integer in 1–32.
 */
export function blockingIssues(baseline: Draft, draft: Draft): PolicyIssue[] {
  const buddies = new Map(draft.buddies.map((p) => [p.buddyId, p]));
  const baselinePreferenceModes = new Map(
    baseline.preferences.map((p) => [p.buddyId, p.mode]),
  );
  const issues: PolicyIssue[] = [];
  for (const entry of draft.modelServiceConcurrency) {
    if (concurrencyLimit(entry.limit) !== null) continue;
    const member = draft.buddies.find((buddy) => modelServiceKey(buddy) === modelServiceKey(entry));
    const subject = member ? buddyName(member) : `${entry.harness}/${entry.provider}/${entry.model}`;
    issues.push({
      buddyId: member?.buddyId ?? `${entry.harness}/${entry.provider}/${entry.model}`,
      modelService: modelServiceKey(entry),
      label: subject,
      message: `${subject} 并发上限须为 1–32 的整数；请修改。`,
    });
  }
  for (const setting of buddySettings(baseline, draft)) {
    if (!setting.enabled) continue;
    const buddy = buddies.get(setting.buddyId);
    if (!buddy?.available) {
      issues.push({
        buddyId: setting.buddyId,
        label: labelOf(buddy, setting.buddyId),
        message: `${labelOf(buddy, setting.buddyId)} ${availabilityReason(buddy)}；无法启用，请撤销修改。`,
      });
    }
  }
  for (const change of preferenceChanges(baseline, draft)) {
    if (change.mode !== "pin") continue;
    // The board requires current legality only when the mode transitions into
    // pin; a reason-only edit of an existing pin stays a legal patch.
    if (baselinePreferenceModes.get(change.buddyId) === "pin") continue;
    const buddy = buddies.get(change.buddyId);
    const refusal = pinRefusal(buddy);
    if (refusal) {
      issues.push({
        buddyId: change.buddyId,
        label: labelOf(buddy, change.buddyId),
        message: `${labelOf(buddy, change.buddyId)} ${refusal}；无法固定，请选择可用推理强度。`,
      });
    }
  }
  // The interval is checked like the concurrency field: an invalid draft value
  // blocks saving so a save can never publish an earlier valid prefix.
  if (draft.configuration && retryIntervalSeconds(draft.configuration.routerRetryIntervalSeconds) === null) {
    issues.push({
      buddyId: "", label: "重试间隔",
      message: "重试间隔须为 1–2147483647 的整数秒；请修改。",
    });
  }
  if (draft.configuration && routerFieldChanged(baseline, draft, "routerBuddyIds")) {
    // Settings name published complete identities. Runtime eligibility and
    // temporary availability belong to the shared current Router resolver.
    for (const target of draft.configuration.routerBuddyIds) {
      const buddy = buddies.get(target);
      const refusal = !buddy ? "已不在当前目录中"
        : [buddy.harness, buddy.provider, buddy.model, buddy.effort].some(value => !value?.trim())
          ? "buddy 身份不完整" : null;
      if (refusal) issues.push({ buddyId: target, label: labelOf(buddy, target),
        message: `${labelOf(buddy, target)} ${refusal}；无法保存 Router 列表。` });
    }
  }
  return issues;
}

/** The Router is checked again by the service before each routing run. */
export function routerAttention(source: Pick<Draft, "configuration" | "buddies">, mode = source.configuration?.defaultRoutingMode): PolicyIssue | null {
  const id = source.configuration?.routerBuddyIds[0] ?? null;
  if (!id || !mode) return null;
  const buddy = source.buddies.find(p => p.buddyId === id);
  const refusal = buddy ? routerRefusal(buddy, mode) : "已不在目录中";
  return refusal ? { buddyId: id, router: true, label: labelOf(buddy, id),
    message: `当前 Router ${labelOf(buddy, id)} ${refusal}：请在合格的推理强度菜单中选择“设为 Router”，或更改模式。` } : null;
}

export function decisionAttention(source: Pick<Draft, "configuration" | "buddies">): PolicyIssue | null {
  return routerAttention(source);
}

/**
 * Settings that stay stale after this draft is published. They are shown as
 * needing attention and never block an unrelated patch: a saved disable, a
 * preference change or a note still commits while these remain. A pin is checked per buddy.
 */
export function attentionIssues(
  source: Pick<Draft, "configuration" | "buddies" | "preferences">,
): PolicyIssue[] {
  const buddies = new Map(source.buddies.map((p) => [p.buddyId, p]));
  const issues: PolicyIssue[] = [];
  const attention = routerAttention(source);
  if (attention) issues.push(attention);
  for (const preference of source.preferences) {
    if (preference.mode !== "pin") continue;
    const buddy = buddies.get(preference.buddyId);
    const refusal = pinRefusal(buddy);
    if (refusal) {
      issues.push({
        buddyId: preference.buddyId,
        label: labelOf(buddy, preference.buddyId),
        message: `固定选择指向 ${labelOf(buddy, preference.buddyId)}：${refusal}；请启用或改为“不设”。`,
      });
    }
  }
  return issues;
}
