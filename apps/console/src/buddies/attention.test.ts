import { describe, expect, it } from "vitest";
import type { ConsoleView, Buddy, RoutingHealth, Snapshot } from "../types";
import { buddyAttention } from "./attention";

const reviewer = "claude:anthropic:claude-sonnet-5:medium";
const worker = "codex:openai:gpt-6-sol:medium";

function buddy(buddyId: string, capabilities: string[], enabled = true): Buddy {
  const [harness, provider, model, effort] = buddyId.split(":");
  return { effortRank: null, buddyId, label: buddyId, harness, provider, model, effort, available: true, enabled, capabilities,
    contextWindow: null, description: "", source: "catalog:fixture" };
}

const failing: RoutingHealth = {
  available: true, currentRouterBuddyId: reviewer, windowSize: 20, sampleCount: 3, failureCount: 2, consecutiveFailures: 2,
  abstentionCount: 0, cancelledCount: 0, lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
};

function attention(options: { routers?: string[]; mode?: "fast" | "review"; health?: RoutingHealth; configuration?: null } = {}) {
  const configuration = options.configuration === null ? null
    : { routerBuddyIds: options.routers ?? [reviewer], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: options.mode ?? "review", routingBudget: "standard" };
  const buddies = [buddy(reviewer, ["execution:claude", "routing:fast", "decision"]), buddy(worker, ["execution:codex"])];
  const harnesses = [
    { harness: "claude", status: "ready", revision: 1, manualPath: null, checkedAt: "2026-09-29T09:30:00.000Z", candidates: [] },
    { harness: "dsh", status: "ready", revision: 1, manualPath: null, checkedAt: "2026-09-29T09:30:00.000Z", candidates: [],
      providers: [{ provider: "deepseek-official", coolingUntil: "2099-01-01T00:00:00Z", consecutiveRejections: 1 }] },
  ];
  const snapshot = { configuration, routingHealth: options.health, harnesses } as unknown as Snapshot;
  return buddyAttention({ buddies, configuration } as unknown as ConsoleView, snapshot);
}

describe("what waits on the Buddy page", () => {
  it("is nothing while a Router that can work holds the role while a provider cools down", () => {
    expect(attention()).toEqual({ roles: null });
  });

  it("names a missing Router, one that cannot work in the default mode, and settings the service cannot read", () => {
    expect(attention({ routers: [] }).roles).toBe("unset");
    // A buddy without the review capability cannot route in review mode, and one without the fast capability not in fast mode.
    expect(attention({ routers: [worker] }).roles).toBe("unusable");
    expect(attention({ routers: [worker], mode: "fast" }).roles).toBe("unusable");
    expect(attention({ routers: [reviewer], mode: "fast" }).roles).toBeNull();
    expect(attention({ configuration: null }).roles).toBe("upgrade");
  });

  it("follows the service's own word on the current Router, not the head of the list", () => {
    expect(attention({ health: { ...failing, consecutiveFailures: 0, failureCount: 0, currentRouterBuddyId: worker } }).roles).toBe("unusable");
    expect(attention({ health: { ...failing, available: false, currentRouterBuddyId: null } }).roles).toBe("unusable");
  });

  it("names routing that keeps failing while the Router itself can work", () => {
    expect(attention({ health: failing }).roles).toBe("failing");
  });

});
