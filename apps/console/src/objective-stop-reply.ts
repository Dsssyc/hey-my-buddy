import { ApiError } from "./api";
import type { ObjectiveStopResult } from "./objective-types";

/**
 * Strict parse of an `objective_stop` reply: the returned objectiveId must
 * match the request; runIds and acceptedRunIds are each duplicate-free and
 * disjoint (an accepted root is never also cancelled); results name each run
 * of the cancellation scope exactly once and nothing else. A malformed reply
 * is an unknown outcome (thrown as INVALID_RESPONSE), never treated as a
 * confirmed stop.
 */
export function errorOfStopReply(expectedObjectiveId: string, value: unknown): ObjectiveStopResult {
  const reply = value as Partial<ObjectiveStopResult> | null;
  const identifier = (entry: unknown) => typeof entry === "string" && entry.length > 0;
  const identifiers = (entries: unknown): entries is string[] =>
    Array.isArray(entries) && entries.every(identifier);
  const unique = (entries: string[]) => new Set(entries).size === entries.length;
  const valid = !!reply && typeof reply === "object"
    && reply.objectiveId === expectedObjectiveId
    && identifiers(reply.runIds) && unique(reply.runIds)
    && identifiers(reply.acceptedRunIds) && unique(reply.acceptedRunIds)
    // Cancelled and retained scopes never overlap.
    && reply.runIds!.every(runId => !reply.acceptedRunIds!.includes(runId))
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
    objectiveId: reply.objectiveId!,
    runIds: [...reply.runIds!],
    acceptedRunIds: [...reply.acceptedRunIds!],
    results: reply.results!.map(result => ({ runId: result.runId, result: typeof result.result === "string" ? result.result : "" })),
  };
}
