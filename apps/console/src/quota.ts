/**
 * Recorded harness quota (ADR-018 §23): the newest observation of one harness,
 * validated at the boundary. An unknown window stays unknown and never reads as
 * 0% or as an available account.
 */
import type { HarnessQuota, QuotaWindow } from "./types";

/** The recorded near-limit threshold: a window at or above this warns. */
export const QUOTA_NEAR_LIMIT_PERCENT = 90;

function nonempty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function countOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * The newest recorded quota observation of one harness. Windows and the whole
 * observation are validated: a malformed value reads as no observation rather
 * than as an available account, and an unknown window never reads as 0%.
 */
export function parseQuota(value: unknown): HarnessQuota | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const observedAt = nonempty(row.observedAt);
  const source = nonempty(row.source);
  if (!observedAt || !source) return null;
  if (!Array.isArray(row.windows)) return null;
  const windows: QuotaWindow[] = [];
  for (const item of row.windows) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const entry = item as Record<string, unknown>;
    const name = nonempty(entry.name);
    if (!name) return null;
    let usedPercent: number | null = null;
    if (entry.usedPercent !== null && entry.usedPercent !== undefined) {
      usedPercent = countOrNull(entry.usedPercent);
      if (usedPercent === null || usedPercent > 100) return null;
    }
    let resetsAt: string | null = null;
    if (entry.resetsAt !== null && entry.resetsAt !== undefined) {
      resetsAt = nonempty(entry.resetsAt);
      if (resetsAt === null) return null;
    }
    windows.push({ name, usedPercent, resetsAt, ...(entry.stale === true ? { stale: true } : {}) });
  }
  const provider = nonempty(row.provider);
  return {
    observedAt,
    source,
    ...(provider ? { provider } : {}),
    ...(nonempty(row.reachedType) ? { reachedType: nonempty(row.reachedType)! } : {}),
    ...(typeof row.ordinaryUsageAllowed === "boolean" ? { ordinaryUsageAllowed: row.ordinaryUsageAllowed } : {}),
    ...(row.balanceZero === true ? { balanceZero: true } : {}),
    stale: row.stale === true,
    windows,
  };
}

export type QuotaWindowView = QuotaWindow & {
  /** 0–100 text, or 未知; unknown stays unknown instead of reading as 0. */
  usedText: string;
  /** True at or above the near-limit threshold; an unknown window is never near. */
  nearLimit: boolean;
  atLimit: boolean;
};

export type QuotaView = {
  stale: boolean;
  limitReported: boolean;
  /** True when some recorded window is at or above the near-limit threshold. */
  alert: boolean;
  source: string;
  provider?: string;
  observedAt: string;
  windows: QuotaWindowView[];
  /** Always says this is an observation, never a live account reading. */
  note: string;
};

/** Recorded quota windows with their display facts; `null` when nothing was recorded. */
export function quotaView(quota: HarnessQuota | null | undefined): QuotaView | null {
  if (!quota) return null;
  const windows = quota.windows.map(window => {
    const nearLimit = !quota.stale && !window.stale && window.usedPercent !== null && window.usedPercent >= QUOTA_NEAR_LIMIT_PERCENT;
    return {
      ...window,
      usedText: window.usedPercent === null ? "未知" : `${formatPercent(window.usedPercent)}%`,
      nearLimit,
      atLimit: !quota.stale && !window.stale && window.usedPercent !== null && window.usedPercent >= 100,
    };
  });
  const limitReported = !quota.stale && Boolean(quota.reachedType || quota.ordinaryUsageAllowed === false);
  const alert = limitReported || windows.some(window => window.nearLimit);
  const note = quota.stale
    ? "额度观测已过期"
    : "最近额度观测 · 非实时";
  return {
    stale: quota.stale === true,
    limitReported,
    alert,
    source: quota.source,
    ...(quota.provider ? { provider: quota.provider } : {}),
    observedAt: quota.observedAt,
    windows,
    note: limitReported ? `${note} 原生记录报告额度限制，当前重置状态可能未知。` : note,
  };
}

function formatPercent(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 10) / 10);
}
