import { isReason } from "./reason";
import type { Reason } from "./reason";

/** The native login facts reported by one harness. */
export type HarnessAccount = {
  harness: string;
  status: "unknown" | "logged-out" | "ready" | "unsupported";
  accountType: string | null; checkedAt: string | null;
  reason: Reason | null;
  plan?: string | null;
};

/** Only named, nonsecret login facts reach rendering. */
export function parseHarnessAccount(value: unknown, harness: string): HarnessAccount | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (row.harness !== harness
    || !["unknown", "logged-out", "ready", "unsupported"].includes(String(row.status))) return null;
  for (const key of ["accountType", "checkedAt"] as const) {
    if (!(row[key] === null || typeof row[key] === "string")) return null;
  }
  if (row.reason !== null && !isReason(row.reason)) return null;
  if (row.plan !== undefined && row.plan !== null && typeof row.plan !== "string") return null;
  return {
    harness, status: row.status as HarnessAccount["status"],
    accountType: row.accountType as string | null, checkedAt: row.checkedAt as string | null,
    reason: row.reason as Reason | null,
    ...(row.plan !== undefined ? { plan: row.plan as string | null } : {}),
  };
}
