import type { RoutingMode } from "./types";

/** Use only the recorded mode; missing historic facts stay unrecorded. */
export function recordedRoutingMode(mode: RoutingMode | null | undefined): string {
  return mode === "fast" ? "快速" : mode === "review" ? "审阅" : "未记录";
}
