import i18n from "i18next";
import type { ConsoleApi } from "../api";
import { ApiError } from "../api";

const liveT = i18n.getFixedT(null, "buddies");

/**
 * The minimal quota recovery entry (ADR-019 second stage). One persisted
 * exhaustion record without a recorded reset would exclude its buddy
 * from automatic routing forever, because an excluded buddy is never
 * routed again and so never produces a newer observation. The one-shot retry
 * window fixes that: one hour after the blocking observation (or after a
 * consumed retry, or immediately after an explicit request) one normal routing
 * decision may admit the buddy again.
 *
 * Allowing the retry is honest by construction: it only opens that window. It
 * queries no balance, calls no model and never claims the quota recovered —
 * including for harnesses that do have an active account query, which this
 * entry never runs. Only a newer usable native observation clears the
 * exhaustion itself.
 */
type RetryOutcome = "duplicate" | "opened" | "already" | "none";

type Reply = { duplicate?: boolean; quota?: { opened?: (string | null)[]; alreadyOpen?: (string | null)[] } };

/**
 * Opens the retry window of one provider of one harness. The request number is
 * kept in the session until a reply is understood, so that a lost reply is
 * replayed as the same request and never opens a second window.
 */
export async function openQuotaRetry(
  api: ConsoleApi, csrfToken: string, harness: string, provider: string,
): Promise<RetryOutcome> {
  const intentKey = `hey-my-buddy-quota-redetect:${harness}:${provider}`;
  let requestId: string;
  try {
    const retained = sessionStorage.getItem(intentKey);
    requestId = retained && /^[A-Za-z0-9_-]{1,128}$/.test(retained) ? retained : crypto.randomUUID();
    sessionStorage.setItem(intentKey, requestId);
  } catch {
    throw new ApiError("REQUEST_STORAGE_UNAVAILABLE", () => liveT("harness.quota.storageUnavailable"));
  }
  const reply = await api.command<Reply>("quota_redetect", { harness, provider, requestId }, csrfToken);
  if (!reply?.quota || !Array.isArray(reply.quota.opened) || !Array.isArray(reply.quota.alreadyOpen)) {
    throw new ApiError("INVALID_RESPONSE", () => liveT("harness.quota.unknownResult"));
  }
  sessionStorage.removeItem(intentKey);
  if (reply.duplicate) return "duplicate";
  if (reply.quota.opened.length) return "opened";
  return reply.quota.alreadyOpen.length ? "already" : "none";
}
