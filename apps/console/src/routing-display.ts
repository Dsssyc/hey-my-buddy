import type { RoutingFallback, RoutingMode } from "./types";

/** A missing mode belongs to a pre-0.20 record, which used review routing. */
export function recordedRoutingMode(mode: RoutingMode | null | undefined): string {
  return mode === "fast" ? "快速" : mode === "review" ? "审阅" : "审阅（历史记录）";
}

export function fallbackDescription(fallback: RoutingFallback | null | undefined): string {
  return fallback ? `已从审阅降级为快速：${fallback.reason || fallback.code}${fallback.reason && fallback.code ? `（${fallback.code}）` : ""}` : "未降级";
}
