import { describe, expect, it } from "vitest";
import type { ConsoleView, Profile, RoutingHealth, Snapshot } from "../types";
import { buddyAttention } from "./attention";

const reviewer = "claude:anthropic:claude-sonnet-5:medium";
const worker = "codex:openai:gpt-6-sol:medium";

function profile(profileId: string, capabilities: string[], enabled = true): Profile {
  const [adapter, provider, model, effort] = profileId.split(":");
  return { profileId, label: profileId, adapter, provider, model, effort, available: true, enabled, capabilities,
    contextWindow: null, description: "", source: "catalog:fixture" };
}

const failing: RoutingHealth = {
  available: true, currentRouterProfileId: reviewer, windowSize: 20, sampleCount: 3, failureCount: 2, consecutiveFailures: 2,
  abstentionCount: 0, cancelledCount: 0, lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [],
};

function attention(options: { routers?: string[]; mode?: "fast" | "review"; health?: RoutingHealth; configuration?: null; quota?: number } = {}) {
  const configuration = options.configuration === null ? null
    : { routerProfileIds: options.routers ?? [reviewer], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: options.mode ?? "review", routingBudget: "standard" };
  const profiles = [profile(reviewer, ["execution:claude", "routing:fast", "decision"]), profile(worker, ["execution:codex"])];
  const harnesses = [
    { adapter: "claude", status: "ready", available: true, revision: 1, manualPath: null, checkedAt: "2026-09-29T09:30:00.000Z", candidates: [] },
    { adapter: "dsh", status: "ready", available: true, revision: 1, manualPath: null, checkedAt: "2026-09-29T09:30:00.000Z", candidates: [],
      quota: { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", provider: "deepseek-official", stale: false,
        windows: [{ name: "5h", usedPercent: options.quota ?? 40, resetsAt: null }] } },
  ];
  const snapshot = { configuration, routingHealth: options.health, harnesses } as unknown as Snapshot;
  return buddyAttention({ profiles, configuration } as unknown as ConsoleView, snapshot);
}

describe("what waits on the Buddy page", () => {
  it("is nothing while a Router that can work holds the role and no quota nears its limit", () => {
    expect(attention()).toEqual({ roles: null, quota: [] });
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
    expect(attention({ health: { ...failing, consecutiveFailures: 0, failureCount: 0, currentRouterProfileId: worker } }).roles).toBe("unusable");
    expect(attention({ health: { ...failing, available: false, currentRouterProfileId: null } }).roles).toBe("unusable");
  });

  it("names routing that keeps failing while the Router itself can work", () => {
    expect(attention({ health: failing }).roles).toBe("failing");
  });

  it("lists the harnesses whose recorded quota nears its limit", () => {
    expect(attention({ quota: 93 }).quota).toEqual(["dsh"]);
    expect(attention({ configuration: null, quota: 93 })).toEqual({ roles: "upgrade", quota: ["dsh"] });
  });
});
