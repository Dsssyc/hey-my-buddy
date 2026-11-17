import type { TimelineRow } from "../macro-task-types";

/** A recorded Host conclusion preserves failure after both live states become concluded. */
export function isFailedRow(row: Pick<TimelineRow, "state" | "status" | "conclusionStatus">): boolean {
  return row.conclusionStatus === "failed" || row.state === "failed" || row.status === "failed";
}
