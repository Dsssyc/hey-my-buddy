import type { RoutingMode } from "./types";
import { effortText } from "./buddy-name";
import type { ExcludedBuddy, RoutingBasis } from "./workflow-types";

/** Use only the recorded mode; missing historic facts stay unrecorded. */
export function recordedRoutingMode(mode: RoutingMode | null | undefined): string {
  return mode === "fast" ? "快速" : mode === "review" ? "审阅" : "未记录";
}

/** How one recorded route was selected; null keeps an unknown or unrecorded source blank. */
export function selectionSourceText(source: string | null | undefined): string | null {
  if (source === "single-candidate") return "程序直选（唯一合法候选，未调用 Router）";
  if (source === "no-candidate") return "无合法候选";
  if (source === "model-selection") return "模型选择";
  return null;
}

function excludedBuddyText(entry: ExcludedBuddy): string {
  const buddy = [entry.harness, entry.provider, entry.model, effortText(entry.effort)].filter(Boolean).join(" / ");
  return entry.reason ? `${buddy}（${entry.reason}）` : buddy;
}

/**
 * The frozen routing basis in one line: the candidate count at submission and
 * the user exclusions that narrowed it. Built only from the record's own frozen
 * facts, never from the current buddy.
 */
export function routingBasisSummary(basis: RoutingBasis | null | undefined): string | null {
  const excluded = basis?.excludedBuddies ?? [];
  if (basis?.candidateCount == null && excluded.length === 0) return null;
  const parts: string[] = [];
  if (basis?.candidateCount != null) parts.push(`提交时冻结候选 ${basis.candidateCount} 个`);
  const count = basis?.excludedCount ?? excluded.length;
  if (count > 0) {
    const listed = excluded.slice(0, count).map(excludedBuddyText).join("；");
    const suffix = count > excluded.length ? ` 等 ${count} 个` : "";
    parts.push(`用户排除 ${count} 个：${listed}${suffix}`);
  }
  return parts.join("；") || null;
}
