import { isDecisionCandidate, isFastRouterCandidate } from "../policy";
import { healthSummary } from "./roles";
import type { ConsoleView, Snapshot } from "../types";

/** What on the Buddy page waits for the user: Router configuration and failures. */
export type BuddyAttention = {
  roles: "upgrade" | "unset" | "unusable" | "failing" | null;
};

export function buddyAttention(data: ConsoleView, snapshot: Snapshot): BuddyAttention {
  const configuration = snapshot.configuration === null ? null : data.configuration;
  if (!configuration) return { roles: "upgrade" };
  // The health projection names the current Router; the head of the list stands in only when no health facts exist.
  const health = snapshot.routingHealth;
  const id = health && "currentRouterBuddyId" in health ? health.currentRouterBuddyId ?? null
    : configuration.routerBuddyIds[0] ?? null;
  if (!id && !configuration.routerBuddyIds.length) return { roles: "unset" };
  const buddy = data.buddies.find(candidate => candidate.buddyId === id);
  const usable = health?.available !== false
    && (configuration.defaultRoutingMode === "fast" ? isFastRouterCandidate(buddy) : isDecisionCandidate(buddy));
  return { roles: !usable ? "unusable" : healthSummary(health).warning ? "failing" : null };
}
