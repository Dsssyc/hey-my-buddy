import type { RoutingFallback, RoutingMode } from "./types";
import { effortText } from "./profile-display";
import type { ExcludedProfile, RoutingBasis } from "./workflow-types";

/** A missing mode belongs to a pre-0.20 record, which used review routing. */
export function recordedRoutingMode(mode: RoutingMode | null | undefined): string {
  return mode === "fast" ? "快速" : mode === "review" ? "审阅" : "审阅（历史记录）";
}

export function fallbackDescription(fallback: RoutingFallback | null | undefined): string {
  return fallback ? `已从审阅降级为快速：${fallback.reason || fallback.code}${fallback.reason && fallback.code ? `（${fallback.code}）` : ""}` : "未降级";
}

/** How one recorded route was selected; null keeps an unknown or unrecorded source blank. */
export function selectionSourceText(source: string | null | undefined): string | null {
  if (source === "single-candidate") return "程序直选（唯一合法候选，未调用 Router）";
  if (source === "model-selection") return "模型选择";
  return null;
}

function excludedProfileText(entry: ExcludedProfile): string {
  const configuration = [entry.adapter, entry.provider, entry.model, effortText(entry.effort)].filter(Boolean).join(" / ");
  return entry.reason ? `${configuration}（${entry.reason}）` : configuration;
}

/**
 * The frozen routing basis in one line: the candidate count at submission and
 * the user exclusions that narrowed it. Built only from the record's own frozen
 * facts, never from current Buddy configuration.
 */
export function routingBasisSummary(basis: RoutingBasis | null | undefined): string | null {
  const excluded = basis?.excludedProfiles ?? [];
  if (basis?.candidateCount == null && excluded.length === 0) return null;
  const parts: string[] = [];
  if (basis?.candidateCount != null) parts.push(`提交时冻结候选 ${basis.candidateCount} 个`);
  const count = basis?.excludedCount ?? excluded.length;
  if (count > 0) {
    const listed = excluded.slice(0, count).map(excludedProfileText).join("；");
    const suffix = count > excluded.length ? ` 等 ${count} 个` : "";
    parts.push(`用户排除 ${count} 个：${listed}${suffix}`);
  }
  return parts.join("；") || null;
}
