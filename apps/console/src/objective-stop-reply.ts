import { ApiError } from "./api";
import type { ObjectiveStopResult } from "./objective-types";

/**
 * Strict parse of an `objective_stop` reply: the returned objectiveId must
 * match the request, runIds/acceptedRunIds must be arrays of identifiers and
 * results a per-root list. A malformed reply is an unknown outcome (thrown as
 * INVALID_RESPONSE), never treated as a confirmed stop.
 */
export function errorOfStopReply(expectedObjectiveId: string, value: unknown): ObjectiveStopResult {
  const reply = value as Partial<ObjectiveStopResult> | null;
  const identifier = (entry: unknown) => typeof entry === "string" && entry.length > 0;
  const valid = !!reply && typeof reply === "object"
    && reply.objectiveId === expectedObjectiveId
    && Array.isArray(reply.runIds) && reply.runIds.every(identifier)
    && Array.isArray(reply.acceptedRunIds) && reply.acceptedRunIds.every(identifier)
    && Array.isArray(reply.results) && reply.results.every(result =>
      !!result && typeof result === "object" && identifier((result as { runId?: unknown }).runId));
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
