import { ApiError } from "./api";
import i18n from "i18next";
import "./i18n";
import type { MacroTaskStopResult } from "./macro-task-types";

/** A malformed acknowledgement leaves the stop outcome unknown. */
export function errorOfStopReply(expectedMacroTaskId: string, value: unknown): MacroTaskStopResult {
  const reply = value as Partial<MacroTaskStopResult> | null;
  const results = reply?.results;
  const preserved = new Set(["delivered", "awaiting-host", "failed", "cancelled", "accepted", "concluded"]);
  const valid = !!reply && typeof reply === "object"
    && reply.macroTaskId === expectedMacroTaskId
    && typeof reply.duplicate === "boolean"
    && Array.isArray(results)
    && results.every(result => result && typeof result === "object"
      && typeof result.runId === "string" && result.runId.length > 0
      && typeof result.stopped === "boolean"
      && (result.stopped ? result.state === "cancelled" : preserved.has(result.state)))
    && new Set(results.map(result => result.runId)).size === results.length;
  if (!valid) {
    throw new ApiError("INVALID_RESPONSE", i18n.t("shell:macroStop.invalidResponse"));
  }
  return {
    macroTaskId: reply.macroTaskId!,
    duplicate: reply.duplicate!,
    results: results!.map(result => ({ ...result })),
  };
}
