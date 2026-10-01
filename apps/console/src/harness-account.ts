import type { ConsoleApi } from "./api";

export type AccountSource = "native" | "worker";
export type AccountCapabilities = {
  workerAccount: boolean; oauth: boolean; apiKey: boolean; logout: boolean; remove: boolean;
};
export type HarnessAccount = {
  adapter: string; source: AccountSource; revision: number; credentialRevision: number;
  status: "unknown" | "logged-out" | "ready" | "unsupported";
  accountType: string | null; checkedAt: string | null;
  capabilities: AccountCapabilities; reasonCode: string | null; guidance: string | null;
  pendingLogin?: AccountLogin;
};
export type AccountLogin = {
  loginId: string; state: "pending" | "completed" | "cancelled" | "failed" | "unconfirmed";
  expiresAt: string; authUrl?: string; verificationUrl?: string; userCode?: string;
  kind?: string;
};

/** Only named, nonsecret facts reach rendering; malformed capabilities confer no authority. */
export function parseHarnessAccount(value: unknown, adapter: string): HarnessAccount | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (row.adapter !== adapter || !["native", "worker"].includes(String(row.source))
    || !["unknown", "logged-out", "ready", "unsupported"].includes(String(row.status))) return null;
  for (const key of ["revision", "credentialRevision"] as const) {
    if (!Number.isSafeInteger(row[key]) || Number(row[key]) < 0) return null;
  }
  for (const key of ["accountType", "checkedAt", "reasonCode", "guidance"] as const) {
    if (!(row[key] === null || typeof row[key] === "string")) return null;
  }
  if (!row.capabilities || typeof row.capabilities !== "object") return null;
  const flags = row.capabilities as Record<string, unknown>;
  for (const key of ["workerAccount", "oauth", "apiKey", "logout", "remove"] as const) {
    if (typeof flags[key] !== "boolean") return null;
  }
  const pending = parseAccountLogin(row.pendingLogin);
  return {
    adapter, source: row.source as AccountSource, status: row.status as HarnessAccount["status"],
    revision: row.revision as number, credentialRevision: row.credentialRevision as number,
    accountType: row.accountType as string | null, checkedAt: row.checkedAt as string | null,
    reasonCode: row.reasonCode as string | null, guidance: row.guidance as string | null,
    capabilities: { workerAccount: flags.workerAccount as boolean, oauth: flags.oauth as boolean,
      apiKey: flags.apiKey as boolean, logout: flags.logout as boolean, remove: flags.remove as boolean },
    ...(pending ? { pendingLogin: { loginId: pending.loginId, state: pending.state,
      expiresAt: pending.expiresAt, kind: pending.kind } } : {}),
  };
}

/** A browser link may arrive only in the explicit private login response. */
export function parseAccountLogin(value: unknown): AccountLogin | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (typeof row.loginId !== "string" || !row.loginId || typeof row.expiresAt !== "string"
    || !["pending", "completed", "cancelled", "failed", "unconfirmed"].includes(String(row.state))) return null;
  const result: AccountLogin = { loginId: row.loginId, state: row.state as AccountLogin["state"], expiresAt: row.expiresAt };
  if (typeof row.kind === "string") result.kind = row.kind;
  for (const key of ["authUrl", "verificationUrl"] as const) {
    if (row[key] === undefined) continue;
    if (typeof row[key] !== "string") return null;
    try {
      const url = new URL(row[key]);
      if (url.protocol !== "https:" || url.username || url.password) return null;
      result[key] = row[key];
    } catch { return null; }
  }
  if (row.userCode !== undefined) {
    if (typeof row.userCode !== "string" || row.userCode.length > 128) return null;
    result.userCode = row.userCode;
  }
  return result;
}

export async function accountCommand(api: ConsoleApi, operation: string, params: Record<string, unknown>, csrfToken: string) {
  return api.command<{ account?: unknown; login?: unknown }>(operation, params, csrfToken);
}
