import i18n from "i18next";
import type { ConsoleApi } from "../api";
import { ApiError } from "../api";

const liveT = i18n.getFixedT(null, "buddies");

type ClearOutcome = "cleared" | "none";
type Reply = { harness: string; provider: string; cleared: boolean; duplicate: boolean };

/** Retain the request until a known reply so an unknown result can be replayed. */
export async function clearCooldown(
  api: ConsoleApi, csrfToken: string, harness: string, provider: string,
): Promise<ClearOutcome> {
  const intentKey = `hey-my-buddy-quota-redetect:${harness}:${provider}`;
  let requestId: string;
  try {
    const retained = sessionStorage.getItem(intentKey);
    requestId = retained && /^[A-Za-z0-9_-]{1,128}$/.test(retained) ? retained : crypto.randomUUID();
    sessionStorage.setItem(intentKey, requestId);
  } catch {
    throw new ApiError("REQUEST_STORAGE_UNAVAILABLE", () => liveT("harness.cooling.storageUnavailable"));
  }
  const reply = await api.command<Reply>("quota_redetect", { harness, provider, requestId }, csrfToken);
  if (!reply || reply.harness !== harness || reply.provider !== provider || typeof reply.cleared !== "boolean" || typeof reply.duplicate !== "boolean") {
    throw new ApiError("INVALID_RESPONSE", () => liveT("harness.cooling.unknownResult"));
  }
  sessionStorage.removeItem(intentKey);
  return reply.cleared ? "cleared" : "none";
}
