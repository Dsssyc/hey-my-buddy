import type { BillingFact, HarnessQuota } from "./types";

/** Short labels shared by the harness row and one model family's details. */
export function billingLabel(fact: BillingFact | null | undefined): string {
  if (fact?.kind === "subscription") return "订阅";
  if (fact?.kind === "metered") return "按量";
  return "计费未知";
}

const EXHAUSTED_CODES = new Set([
  "QUOTA", "insufficient_quota", "quota_exceeded", "usage_limit_reached",
  "usage_limit_exceeded", "usagelimitexceeded", "sessionbudgetexceeded",
  "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached",
  "workspace_owner_credits_depleted", "workspace_member_credits_depleted",
]);

export function quotaShortLabel(quota: HarnessQuota | null | undefined, provider?: string): string {
  if (!quota || quota.stale || (provider && quota.provider !== provider)) return "额度未知";
  const reached = quota.reachedType?.replace(/[_-]/g, "").toLowerCase();
  if (quota.balanceZero || (reached && [...EXHAUSTED_CODES].some(code => code.replace(/[_-]/g, "").toLowerCase() === reached))) {
    return "额度耗尽";
  }
  if (quota.windows.some(window => !window.stale && window.usedPercent !== null && window.usedPercent >= 90)) {
    return "额度提醒";
  }
  return "额度已记录";
}
