import { ApiError } from "./api";
import type { MacroTaskStopResult } from "./macro-task-types";

/**
 * Strict parse of an `macro_task_stop` reply: the returned macroTaskId must
 * match the request; runIds, acceptedRunIds and concludedRunIds are each
 * duplicate-free and disjoint; results name each run
 * of the cancellation scope exactly once and nothing else. A malformed reply
 * is an unknown outcome (thrown as INVALID_RESPONSE), never treated as a
 * confirmed stop.
 */
export function errorOfStopReply(expectedMacroTaskId: string, value: unknown): MacroTaskStopResult {
  const reply = value as Partial<MacroTaskStopResult> | null;
  const identifier = (entry: unknown) => typeof entry === "string" && entry.length > 0;
  const identifiers = (entries: unknown): entries is string[] =>
    Array.isArray(entries) && entries.every(identifier);
  const unique = (entries: string[]) => new Set(entries).size === entries.length;
  const valid = !!reply && typeof reply === "object"
    && reply.macroTaskId === expectedMacroTaskId
    && identifiers(reply.runIds) && unique(reply.runIds)
    && identifiers(reply.acceptedRunIds) && unique(reply.acceptedRunIds)
    && identifiers(reply.concludedRunIds) && unique(reply.concludedRunIds)
    // Cancelled and retained scopes never overlap.
    && reply.runIds!.every(runId => !reply.acceptedRunIds!.includes(runId) && !reply.concludedRunIds!.includes(runId))
    && reply.acceptedRunIds!.every(runId => !reply.concludedRunIds!.includes(runId))
    && Array.isArray(reply.results) && (() => {
      if (!reply.results!.every(result =>
        !!result && typeof result === "object" && identifier((result as { runId?: unknown }).runId))) return false;
      const resultIds = reply.results!.map(result => (result as { runId: string }).runId);
      if (!unique(resultIds)) return false;
      // The per-root results cover the cancellation scope exactly, as sets.
      const scope = new Set(reply.runIds!);
      return resultIds.length === scope.size && resultIds.every(runId => scope.has(runId));
    })();
  if (!valid) {
    throw new ApiError("INVALID_RESPONSE", "停止目标的响应不完整，提交结果尚未确认。");
  }
  return {
    macroTaskId: reply.macroTaskId!,
    runIds: [...reply.runIds!],
    acceptedRunIds: [...reply.acceptedRunIds!],
    concludedRunIds: [...reply.concludedRunIds!],
    results: reply.results!.map(result => ({ runId: result.runId, result: typeof result.result === "string" ? result.result : "" })),
  };
}
