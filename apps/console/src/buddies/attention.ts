import { snapshotHarnesses } from "../api";
import { quotaView } from "../host-workflow";
import { isDecisionCandidate, isFastRouterCandidate } from "../policy";
import { healthSummary } from "../RoutingStatusBar";
import type { ConsoleView, Snapshot } from "../types";

/** What on the Buddy page waits for the user: the Router role, and the harnesses whose quota nears its limit. */
export type BuddyAttention = {
  roles: "upgrade" | "unset" | "unusable" | "failing" | null;
  quota: string[];
};

export function buddyAttention(data: ConsoleView, snapshot: Snapshot): BuddyAttention {
  const configuration = snapshot.configuration === null ? null : data.configuration;
  const quota = snapshotHarnesses(snapshot).filter(row => quotaView(row.quota)?.alert).map(row => row.adapter);
  if (!configuration) return { roles: "upgrade", quota };
  // The health projection names the current Router; the head of the list stands in only when no health facts exist.
  const health = snapshot.routingHealth;
  const id = health && "currentRouterProfileId" in health ? health.currentRouterProfileId ?? null
    : configuration.routerProfileIds[0] ?? null;
  if (!id && !configuration.routerProfileIds.length) return { roles: "unset", quota };
  const profile = data.profiles.find(candidate => candidate.profileId === id);
  const usable = health?.available !== false
    && (configuration.defaultRoutingMode === "fast" ? isFastRouterCandidate(profile) : isDecisionCandidate(profile));
  return { roles: !usable ? "unusable" : healthSummary(health).warning ? "failing" : null, quota };
}
