/** The native login facts reported by one harness. */
export type HarnessAccount = {
  adapter: string;
  status: "unknown" | "logged-out" | "ready" | "unsupported";
  accountType: string | null; checkedAt: string | null;
  reasonCode: string | null; guidance: string | null;
  plan?: string | null;
};

/** Only named, nonsecret login facts reach rendering. */
export function parseHarnessAccount(value: unknown, adapter: string): HarnessAccount | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (row.adapter !== adapter
    || !["unknown", "logged-out", "ready", "unsupported"].includes(String(row.status))) return null;
  for (const key of ["accountType", "checkedAt", "reasonCode", "guidance"] as const) {
    if (!(row[key] === null || typeof row[key] === "string")) return null;
  }
  if (row.plan !== undefined && row.plan !== null && typeof row.plan !== "string") return null;
  return {
    adapter, status: row.status as HarnessAccount["status"],
    accountType: row.accountType as string | null, checkedAt: row.checkedAt as string | null,
    reasonCode: row.reasonCode as string | null, guidance: row.guidance as string | null,
    ...(row.plan !== undefined ? { plan: row.plan as string | null } : {}),
  };
}
